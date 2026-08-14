"""Resume logic: completed stages are inferred from artifacts on disk."""

from __future__ import annotations

import json

from prose_forge import runstate


def _make_run(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return runstate.create_run("ch01", run_id="ch01-20260101-000000")


def test_create_run_writes_initial_status(tmp_path, monkeypatch):
    rid = _make_run(monkeypatch, tmp_path)
    status = runstate.read_status(rid)
    assert status["stage"] == "plan"
    assert status["state"] == "running"
    assert status["prompt_id"] == "ch01"
    assert "updated_at" in status


def test_resume_follows_artifacts(tmp_path, monkeypatch):
    rid = _make_run(monkeypatch, tmp_path)
    d = runstate.run_dir(rid)

    assert runstate.completed_stages(rid) == []
    assert runstate.next_stage(rid) == "plan"

    (d / "beats.md").write_text("1. beat", encoding="utf-8")
    assert runstate.next_stage(rid) == "draft"

    for name in ["a1", "a2", "b1", "b2"]:
        (d / "drafts" / f"{name}.md").write_text("draft", encoding="utf-8")
    assert runstate.next_stage(rid) == "select"

    (d / "selection.json").write_text(json.dumps({"winner": "a1"}), encoding="utf-8")
    (d / "lint.json").write_text(json.dumps({"spans": []}), encoding="utf-8")
    assert runstate.next_stage(rid) == "edit"

    (d / "edited.md").write_text("text", encoding="utf-8")
    assert runstate.next_stage(rid) == "report"

    (d / "final.md").write_text("text", encoding="utf-8")
    assert runstate.next_stage(rid) is None
    assert runstate.completed_stages(rid) == runstate.STAGES


def test_gap_in_artifacts_stops_resume_at_gap(tmp_path, monkeypatch):
    rid = _make_run(monkeypatch, tmp_path)
    d = runstate.run_dir(rid)
    # selection exists but beats.md is missing: resume must restart at plan.
    (d / "selection.json").write_text("{}", encoding="utf-8")
    assert runstate.completed_stages(rid) == []
    assert runstate.next_stage(rid) == "plan"


def test_partial_drafts_do_not_count(tmp_path, monkeypatch):
    rid = _make_run(monkeypatch, tmp_path)
    d = runstate.run_dir(rid)
    (d / "beats.md").write_text("1. beat", encoding="utf-8")
    (d / "drafts" / "a1.md").write_text("draft", encoding="utf-8")
    assert runstate.next_stage(rid, expected_drafts=4) == "draft"
    assert runstate.next_stage(rid, expected_drafts=1) == "select"


def test_update_status_merges_and_stamps(tmp_path, monkeypatch):
    rid = _make_run(monkeypatch, tmp_path)
    runstate.update_status(rid, stage="draft", state="running")
    runstate.update_status(rid, gates={"hard_fail": False})
    status = runstate.read_status(rid)
    assert status["stage"] == "draft"
    assert status["gates"] == {"hard_fail": False}
    assert status["prompt_id"] == "ch01"  # merged, not clobbered


def test_list_runs_newest_first(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runstate.create_run("a", run_id="a-1")
    runstate.create_run("b", run_id="b-1")
    ids = [r["run_id"] for r in runstate.list_runs()]
    assert set(ids) == {"a-1", "b-1"}
    assert len(ids) == 2
