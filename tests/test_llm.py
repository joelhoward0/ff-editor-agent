"""Mock-mode behavior of the OpenRouter client: cycling, prefill, logging."""

from __future__ import annotations

import json

from prose_forge import llm


def _messages():
    return [{"role": "user", "content": "Continue the manuscript."}]


def test_mock_cycles_numbered_variants(cfg, mock_env):
    (mock_env / "drafter_a.md").write_text("variant one", encoding="utf-8")
    (mock_env / "drafter_a_2.md").write_text("variant two", encoding="utf-8")

    first = llm.chat("drafter_a", _messages(), sampling_key="draft", config=cfg)
    second = llm.chat("drafter_a", _messages(), sampling_key="draft", config=cfg)
    third = llm.chat("drafter_a", _messages(), sampling_key="draft", config=cfg)

    assert first.text == "variant one"
    assert second.text == "variant two"
    assert third.text == "variant one"  # wraps deterministically


def test_prefill_is_included_in_returned_text(cfg, mock_env):
    (mock_env / "drafter_b.md").write_text(" and the rest followed.", encoding="utf-8")

    result = llm.chat(
        "drafter_b", _messages(), prefill="The door stuck.", sampling_key="draft", config=cfg
    )
    assert result.text == "The door stuck. and the rest followed."


def test_echoed_prefill_is_stripped(cfg, mock_env):
    (mock_env / "drafter_b.md").write_text("The door stuck. More text.", encoding="utf-8")

    result = llm.chat(
        "drafter_b", _messages(), prefill="The door stuck.", sampling_key="draft", config=cfg
    )
    assert result.text == "The door stuck. More text."


def test_calls_logged_to_run_dir_with_stage(cfg, mock_env, tmp_path):
    (mock_env / "judge.md").write_text('{"winner": "A"}', encoding="utf-8")
    run_dir = tmp_path / "run"
    llm.set_run_context(run_dir, stage="select")
    try:
        llm.chat("judge", _messages(), sampling_key="judge", config=cfg)
    finally:
        llm.set_run_context(None)

    entries = [
        json.loads(line)
        for line in (run_dir / "llm_log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(entries) == 1
    assert entries[0]["stage"] == "select"
    assert entries[0]["slot"] == "judge"
    assert entries[0]["mock"] is True

    summary = llm.cost_summary(run_dir)
    assert summary["calls"] == 1
    assert "select" in summary["by_stage"]


def test_model_override_used_for_eval_judges(cfg, mock_env):
    (mock_env / "eval_judge.md").write_text('{"verdict": "HUMAN"}', encoding="utf-8")

    result = llm.chat(
        "eval_judge",
        _messages(),
        sampling_key="judge",
        model_override="test/eval-one",
        config=cfg,
    )
    assert result.model == "mock/test/eval-one"
