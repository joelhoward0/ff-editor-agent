"""End-to-end pipeline in mock mode: artifacts, resume, accept, context invariant."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from prose_forge import banlist, corpus, llm, pipeline, runstate
from prose_forge.config import load_config

REPO_ROOT = Path(__file__).resolve().parent.parent

PROMPT = """# Chapter: after the storm

POV: Maren
Goal: the morning after the storm, Maren and Tobin take stock and settle back
into their rhythm.
"""


@pytest.fixture
def project(workspace):
    """A workspace with ingested fixture corpus, baseline, and a chapter prompt."""
    shutil.copytree(REPO_ROOT / "tests/fixtures/corpus", workspace / "corpus")
    cfg = load_config()
    corpus.ingest(cfg)
    corpus.build_baseline_asset()
    (workspace / "prompts").mkdir()
    (workspace / "prompts/ch01.md").write_text(PROMPT, encoding="utf-8")
    return workspace


def test_full_run_produces_all_artifacts(project):
    status = pipeline.run_pipeline("prompts/ch01.md")
    rid = status["run_id"]
    d = runstate.run_dir(rid)

    for artifact in ["prompt.md", "beats.md", "selection.json", "lint.json",
                     "edited.md", "final.md", "report.md", "status.json", "llm_log.jsonl"]:
        assert (d / artifact).exists(), artifact
    assert len(list((d / "drafts").glob("*.md"))) == 4

    assert status["state"] == "done"
    assert status["stage"] == "report"
    assert status["gates"]["hard_fail"] is False

    selection = json.loads((d / "selection.json").read_text())
    assert selection["winner"] in {"a1", "a2", "b1", "b2"}
    assert len(selection["matrix"]) == 6

    # generate3 opening: the chosen opener is recorded and starts the final text
    opener = (d / "opening_used.txt").read_text().strip()
    assert (d / "final.md").read_text().startswith(opener)

    report = (d / "report.md").read_text()
    assert "## Gates" in report
    assert "## Human review" in report


def test_drafter_and_planner_context_is_clean(project, monkeypatch):
    """The assembled drafter/planner context carries no banlist content and no
    negative style instructions — enforcement stays post-hoc."""
    captured: list[tuple[str, str]] = []
    real_chat = llm.chat

    def spy(slot, messages, **kwargs):
        captured.append((slot, "\n".join(m["content"] for m in messages)))
        return real_chat(slot, messages, **kwargs)

    monkeypatch.setattr(llm, "chat", spy)
    monkeypatch.setattr(pipeline.llm, "chat", spy)
    pipeline.run_pipeline("prompts/ch01.md")

    rules = banlist.load_rules(REPO_ROOT / "seed_banlist.txt")
    creative = [text for slot, text in captured if slot in ("drafter_a", "drafter_b", "planner")]
    assert creative, "no drafter/planner calls captured"
    for context in creative:
        assert banlist.find_hits(context, rules) == []
        assert "avoid" not in context.lower()
        assert "don't use" not in context.lower()
        assert "never use" not in context.lower()


def test_pause_after_beats_and_resume(project):
    status = pipeline.run_pipeline("prompts/ch01.md", pause_after_beats=True)
    rid = status["run_id"]
    assert status["state"] == "awaiting_beats"
    d = runstate.run_dir(rid)
    assert (d / "beats.md").exists()
    assert list((d / "drafts").glob("*.md")) == []

    resumed = pipeline.run_pipeline(run_id=rid, resume=True)
    assert resumed["state"] == "done"
    assert (d / "final.md").exists()


def test_resume_reenters_at_missing_artifact(project):
    status = pipeline.run_pipeline("prompts/ch01.md")
    rid = status["run_id"]
    d = runstate.run_dir(rid)
    # blow away everything after select; resume must redo lint/edit/report only
    for name in ["lint.json", "edited.md", "final.md", "report.md"]:
        (d / name).unlink()
    before_drafts = sorted(p.name for p in (d / "drafts").glob("*.md"))

    resumed = pipeline.run_pipeline(run_id=rid, resume=True)
    assert resumed["state"] == "done"
    assert sorted(p.name for p in (d / "drafts").glob("*.md")) == before_drafts
    assert (d / "final.md").exists()


def test_accept_grows_manuscript_and_feeds_next_context(project):
    status = pipeline.run_pipeline("prompts/ch01.md")
    rid = status["run_id"]
    cfg = load_config()

    manuscript = pipeline.accept_run(rid)
    first_len = len(manuscript.read_text())
    final_text = (runstate.run_dir(rid) / "final.md").read_text().strip()
    assert final_text in manuscript.read_text()

    # next run's context tail now reflects the manuscript
    tail = pipeline.manuscript_tail(cfg)
    assert tail != "(beginning of manuscript)"
    assert tail.split("\n\n")[-1].strip() in final_text

    # accepting a second run appends with the scene separator
    status2 = pipeline.run_pipeline("prompts/ch01.md")
    pipeline.accept_run(status2["run_id"])
    content = manuscript.read_text()
    assert len(content) > first_len
    assert "\n\n***\n\n" in content


def test_human_opening_mode_requires_file(project, monkeypatch):
    cfg = load_config()
    cfg.pipeline.opening_line_mode = "human"
    from prose_forge.config import MissingAssetError

    with pytest.raises(MissingAssetError):
        pipeline.run_pipeline("prompts/ch01.md", config=cfg)

    status = runstate.list_runs(1)[0]
    assert status["state"] == "failed"


def test_human_opening_mode_uses_file(project, tmp_path):
    cfg = load_config()
    cfg.pipeline.opening_line_mode = "human"
    opening = tmp_path / "opening.txt"
    opening.write_text("The tide had opinions this morning.\n", encoding="utf-8")
    status = pipeline.run_pipeline("prompts/ch01.md", opening_file=opening, config=cfg)
    d = runstate.run_dir(status["run_id"])
    assert (d / "final.md").read_text().startswith("The tide had opinions this morning.")
