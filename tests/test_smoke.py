"""FORGE_MOCK=1 smoke: ingest → baseline → banlist → triples → run → accept → eval.

The whole asset-and-pipeline sequence in one process against the fixture
corpus, asserting every artifact lands and every gate evaluates.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from prose_forge import banlist, corpus, evalx, pipeline, runstate, triples
from prose_forge.config import load_config

REPO_ROOT = Path(__file__).resolve().parent.parent

PROMPT = """# Chapter: after the storm

POV: Maren
Goal: the morning after the storm, Maren and Tobin take stock.
"""


def test_full_mock_sequence(workspace):
    shutil.copytree(REPO_ROOT / "tests/fixtures/corpus", workspace / "corpus")
    (workspace / "prompts").mkdir()
    (workspace / "prompts/ch01.md").write_text(PROMPT, encoding="utf-8")
    cfg = load_config()

    # ingest + baseline
    ingest_summary = corpus.ingest(cfg)
    assert ingest_summary["chunks"] >= 3
    baseline = corpus.build_baseline_asset()
    assert baseline["word_count"] > 500
    assert (workspace / "data/baseline.json").exists()

    # banlist build: control set from bare prompts through both drafters
    banlist_summary = banlist.build_banlist(cfg, prompts_sample=1)
    assert banlist_summary["control_files"] == 2
    assert (workspace / "data/banlist.txt").exists()
    # fixture sanity guarantees the mock control mines nothing
    assert banlist_summary["mined_added"] == 0
    assert banlist_summary["total_active"] == 16  # the seed list

    # triples
    triples_summary = triples.build_triples(cfg, n=2)
    assert triples_summary["total"] == 2

    # the chapter run
    status = pipeline.run_pipeline("prompts/ch01.md")
    rid = status["run_id"]
    d = runstate.run_dir(rid)
    assert status["state"] == "done"
    assert (d / "final.md").exists()
    assert (d / "report.md").exists()
    gates = status["gates"]
    assert gates["hard_fail"] is False
    assert isinstance(gates["warnings"], list)  # soft gates evaluated

    lint_result = json.loads((d / "lint.json").read_text())
    assert set(lint_result) == {"spans", "stats", "hard_fail", "warnings"}
    assert lint_result["stats"]["banlist_hits_per_1k"] == 0.0

    # accept grows the manuscript
    manuscript = pipeline.accept_run(rid)
    assert (d / "final.md").read_text().strip() in manuscript.read_text()

    # eval over the accepted run's text
    eval_result = evalx.run_eval(cfg, k=3)
    assert eval_result["ai_source"] == "runs"
    assert eval_result["pass"] is True
    assert (workspace / "data/eval_history.jsonl").exists()
