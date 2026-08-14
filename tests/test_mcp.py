"""MCP tools: status, lint_text/edit_text, and the paused-run → approve_beats flow.

The FastMCP decorator returns the original functions, so the tools are called
directly; start_run/approve_beats really do spawn detached CLI subprocesses,
exactly as an MCP client would experience them.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import time
from pathlib import Path

import pytest

from prose_forge import corpus, llm
from prose_forge.config import load_config

REPO_ROOT = Path(__file__).resolve().parent.parent

PROMPT = """# Chapter: after the storm

POV: Maren
Goal: morning-after stocktaking.
"""


@pytest.fixture
def server(workspace):
    """Import mcp_server fresh (cwd is the temp workspace)."""
    spec = importlib.util.spec_from_file_location("mcp_server_test", REPO_ROOT / "mcp_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(workspace):
    shutil.copytree(REPO_ROOT / "tests/fixtures/corpus", workspace / "corpus")
    cfg = load_config()
    corpus.ingest(cfg)
    corpus.build_baseline_asset()
    (workspace / "prompts").mkdir()
    (workspace / "prompts/ch01.md").write_text(PROMPT, encoding="utf-8")
    return workspace


def _wait_for(predicate, timeout=90, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_status_tool(server, project):
    result = server.status()
    assert "models" in result
    assert result["models"]["drafter_a"]
    assert set(result["assets"]) == {"chunks", "baseline", "banlist", "triples"}
    assert result["assets"]["chunks"]["count"] >= 3
    assert result["last_eval"] is None


def test_list_chapter_prompts(server, project):
    result = server.list_chapter_prompts()
    assert [p["id"] for p in result["prompts"]] == ["ch01"]


def test_lint_text_tool(server, project):
    result = server.lint_text("A beat passed. It somehow held.")
    assert len(result["spans"]) == 2
    assert result["hard_fail"] is True


def test_edit_text_tool(server, project, workspace):
    (workspace / "tests/fixtures/mock_responses/editor.md").write_text(
        "It was cold. It barely held. She left before the tide turned.", encoding="utf-8"
    )
    llm.reset_mock_counters()
    result = server.edit_text("It was cold. It somehow held. She left before the tide turned.")
    assert result["edited"] is True
    assert result["text"] == "It was cold. It barely held. She left before the tide turned."


def test_edit_text_no_spans_is_noop(server, project):
    result = server.edit_text("The tide went out and the mud held its shape.")
    assert result["edited"] is False
    assert result["text"] == "The tide went out and the mud held its shape."


def test_get_artifact_unknown_name(server, project):
    assert "error" in server.get_artifact("nope", "beats")


def test_paused_run_resumed_via_approve_beats(server, project):
    """Acceptance: start_run(pause_after_beats=True) → awaiting_beats →
    approve_beats(with replacement beats) → run completes."""
    started = server.start_run(prompt_id="ch01", pause_after_beats=True)
    rid = started["run_id"]
    status_path = project / "runs" / rid / "status.json"

    def awaiting():
        if not status_path.exists():
            return False
        return json.loads(status_path.read_text()).get("state") == "awaiting_beats"

    assert _wait_for(awaiting), server.get_run(rid)

    new_beats = (
        "POV: Maren | TYPE: interiority\n"
        "1. Maren inspects the levee after the storm.\n"
        "2. Tobin collects his ladder.\n"
        "3. They settle the question of chowder.\n"
    )
    result = server.approve_beats(rid, beats_markdown=new_beats)
    assert result["relaunched"] is True

    def done():
        return json.loads(status_path.read_text()).get("state") == "done"

    assert _wait_for(done), server.get_run(rid)
    assert (project / "runs" / rid / "beats.md").read_text().startswith("POV: Maren")
    assert "Tobin collects his ladder" in (project / "runs" / rid / "beats.md").read_text()
    assert (project / "runs" / rid / "final.md").exists()

    # artifact fetch over MCP
    final = server.get_artifact(rid, "final")
    assert final["text"]
    winner = server.get_artifact(rid, "draft_winner")
    assert winner["text"]


def test_approve_beats_rejects_non_paused_runs(server, project):
    assert "error" in server.approve_beats("no-such-run")
