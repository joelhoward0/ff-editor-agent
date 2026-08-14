"""Discrimination eval: sampling, accuracy math, history, trend."""

from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import pytest

from prose_forge import corpus, evalx
from prose_forge.config import load_config

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def eval_project(workspace):
    """Workspace with ingested corpus and a control set as pipeline text."""
    shutil.copytree(REPO_ROOT / "tests/fixtures/corpus", workspace / "corpus")
    cfg = load_config()
    corpus.ingest(cfg)
    control = workspace / "data/control"
    control.mkdir(parents=True)
    for i, name in enumerate(["drafter_a.md", "drafter_b.md"]):
        text = (REPO_ROOT / "tests/fixtures/mock_responses" / name).read_text()
        (control / f"example-{i}.md").write_text(text, encoding="utf-8")
    return workspace, cfg


class TestSampling:
    def test_in_range_paragraphs_preferred(self):
        rng = random.Random(1)
        long_para = " ".join(["word"] * 120)
        short_para = "Too short."
        picked = evalx._pick_paragraphs([f"{long_para}\n\n{short_para}"], 1, rng)
        assert picked == [long_para]

    def test_padding_when_scarce(self):
        rng = random.Random(1)
        paras = ["alpha one two", " ".join(["beta"] * 50), " ".join(["gamma"] * 300)]
        picked = evalx._pick_paragraphs(["\n\n".join(paras)], 2, rng)
        assert len(picked) == 2  # padded with nearest-length paragraphs

    def test_sample_paragraphs_uses_control_fallback(self, eval_project):
        sample = evalx.sample_paragraphs(2)
        assert sample["ai_source"] == "control"
        assert len(sample["human"]) == 2
        assert len(sample["ai"]) == 2

    def test_runs_preferred_over_control(self, eval_project):
        workspace, _ = eval_project
        run_dir = workspace / "runs/ch01-x"
        run_dir.mkdir(parents=True)
        (run_dir / "final.md").write_text("Final text of a run. " * 40, encoding="utf-8")
        assert evalx.sample_paragraphs(1)["ai_source"] == "runs"

    def test_missing_pipeline_text_raises(self, workspace):
        shutil.copytree(REPO_ROOT / "tests/fixtures/corpus", workspace / "corpus")
        cfg = load_config()
        corpus.ingest(cfg)
        from prose_forge.config import MissingAssetError

        with pytest.raises(MissingAssetError):
            evalx.sample_paragraphs(1)


class TestRunEval:
    def test_mock_judges_score_half_and_pass(self, eval_project):
        _, cfg = eval_project
        result = evalx.run_eval(cfg, k=3)
        # mock eval judges always answer HUMAN: all human right, all AI wrong
        for acc in result["accuracies"].values():
            assert acc == pytest.approx(0.5)
        assert result["mean_accuracy"] == pytest.approx(0.5)
        assert result["pass"] is True  # 0.5 <= 0.65 gate
        assert result["slop"] == 0.0
        assert len(result["accuracies"]) == 2  # both configured eval judges

    def test_history_appends_and_trend_renders(self, eval_project):
        workspace, cfg = eval_project
        evalx.run_eval(cfg, k=2)
        evalx.run_eval(cfg, k=2)
        history_path = workspace / "data/eval_history.jsonl"
        entries = [json.loads(line) for line in history_path.read_text().splitlines()]
        assert len(entries) == 2
        for entry in entries:
            assert set(entry) >= {"timestamp", "accuracies", "mean_accuracy",
                                  "slop", "config_hash"}
        trend = evalx.trend_line(entries)
        assert "0.50" in trend
