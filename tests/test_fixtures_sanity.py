"""Guardrails on the fixture prose itself.

The smoke test's determinism depends on two properties: mock drafter output is
clean against the seed banlist (so a mock run passes the hard gate), and the
mock drafts mine zero ban candidates against the fixture corpus (so a mock
`banlist build` can't poison a following mock run).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prose_forge import banlist

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SEED = Path(__file__).resolve().parent.parent / "seed_banlist.txt"


def _corpus_text() -> str:
    return "\n\n".join(p.read_text() for p in sorted((FIXTURES / "corpus").glob("*.md")))


def _draft_texts() -> dict[str, str]:
    drafts = sorted((FIXTURES / "mock_responses").glob("drafter_*.md"))
    return {p.name: p.read_text() for p in drafts}


def test_three_corpus_scenes_exist_and_are_substantial():
    scenes = sorted((FIXTURES / "corpus").glob("*.md"))
    assert len(scenes) == 3
    for scene in scenes:
        assert len(scene.read_text().split()) > 250


@pytest.mark.parametrize("name", ["corpus"] + list(_draft_texts()))
def test_fixture_prose_is_seed_banlist_clean(name):
    text = _corpus_text() if name == "corpus" else _draft_texts()[name]
    hits = banlist.find_hits(text, banlist.load_rules(SEED))
    assert hits == [], f"{name} contains seed banlist phrases: {[h['text'] for h in hits]}"


def test_mock_drafts_mine_no_candidates_against_fixture_corpus():
    corpus_text = _corpus_text()
    drafts = _draft_texts()
    combos = {
        "first-variants": drafts["drafter_a.md"] + "\n\n" + drafts["drafter_b.md"],
        "all-variants": "\n\n".join(drafts.values()),
    }
    for label, control in combos.items():
        mined = banlist.mine_candidates(control, corpus_text)
        assert mined == [], f"{label} control would poison the banlist: {mined}"
