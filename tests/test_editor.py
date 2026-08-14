"""Diff-assert: legal surgical edits pass, out-of-span changes are rejected."""

from __future__ import annotations

from prose_forge import banlist, editor, llm

ORIGINAL = "It was cold. It somehow held. She left before the tide turned."


def _spans(text: str = ORIGINAL) -> list[dict]:
    return banlist.find_hits(text, [banlist.parse_rule_line("somehow")])


def test_legal_edit_accepted():
    edited = "It was cold. It barely held. She left before the tide turned."
    ok, merged, rejected = editor.diff_assert(ORIGINAL, edited, _spans())
    assert ok is True
    assert merged == edited
    assert rejected == []


def test_out_of_span_change_rejected_and_healed():
    edited = "It was cold. It barely held. She ran before the tide turned."
    ok, merged, rejected = editor.diff_assert(ORIGINAL, edited, _spans())
    assert ok is False
    # the legal middle-sentence edit survives; the illegal last-sentence edit is healed
    assert merged == "It was cold. It barely held. She left before the tide turned."
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "changed a sentence outside flagged spans"


def test_added_sentence_rejected_and_dropped():
    edited = ORIGINAL + " The gulls approved of this."
    ok, merged, rejected = editor.diff_assert(ORIGINAL, edited, _spans())
    assert ok is False
    assert merged == ORIGINAL
    assert rejected[0]["kind"] == "insert"


def test_deleting_unflagged_sentence_rejected():
    edited = "It was cold. It somehow held."
    ok, merged, rejected = editor.diff_assert(ORIGINAL, edited, _spans())
    assert ok is False
    assert merged == ORIGINAL


def test_deleting_flagged_sentence_is_legal():
    edited = "It was cold. She left before the tide turned."
    ok, merged, _ = editor.diff_assert(ORIGINAL, edited, _spans())
    assert ok is True
    assert merged == edited


def test_identical_text_passes_with_no_spans():
    ok, merged, rejected = editor.diff_assert(ORIGINAL, ORIGINAL, [])
    assert ok is True
    assert merged == ORIGINAL
    assert rejected == []


def test_edit_pass_retries_sternly_then_succeeds(workspace, cfg):
    mock = workspace / "tests/fixtures/mock_responses"
    # first attempt breaks the contract; the stern retry is surgical
    (mock / "editor.md").write_text(
        "It was cold. It barely held. She ran before the tide turned.", encoding="utf-8"
    )
    (mock / "editor_2.md").write_text(
        "It was cold. It barely held. She left before the tide turned.", encoding="utf-8"
    )
    llm.reset_mock_counters()
    new_text, info = editor.edit_pass(ORIGINAL, _spans(), [], cfg)
    assert new_text == "It was cold. It barely held. She left before the tide turned."
    assert info["attempts"] == 2
    assert info["rejected"] == []


def test_edit_pass_heals_when_both_attempts_fail(workspace, cfg):
    mock = workspace / "tests/fixtures/mock_responses"
    bad = "It was cold. It barely held. She ran before the tide turned."
    (mock / "editor.md").write_text(bad, encoding="utf-8")
    (mock / "editor_2.md").write_text(bad, encoding="utf-8")
    llm.reset_mock_counters()
    new_text, info = editor.edit_pass(ORIGINAL, _spans(), [], cfg)
    # legal change kept, illegal change healed back to the original sentence
    assert new_text == "It was cold. It barely held. She left before the tide turned."
    assert info["attempts"] == 2
    assert len(info["rejected"]) == 1
