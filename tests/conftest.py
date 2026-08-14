"""Shared fixtures: a minimal validated Config and mock-mode environments."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from prose_forge.config import Config

REPO_ROOT = Path(__file__).resolve().parent.parent

MINIMAL_CONFIG: dict = {
    "models": {
        "planner": "test/planner-model",
        "drafter_a": "test/drafter-a-model",
        "drafter_b": "test/drafter-b-model",
        "judge": "test/judge-model",
        "editor": "test/editor-model",
        "tagger": "test/tagger-model",
        "eval_judges": ["test/eval-one", "test/eval-two"],
    },
    "sampling": {
        "draft": {"temperature": 1.0, "min_p": 0.05, "max_tokens": 4000},
        "judge": {"temperature": 0.2, "max_tokens": 1200},
        "edit": {"temperature": 0.5, "max_tokens": 6000},
    },
}


@pytest.fixture
def cfg() -> Config:
    """A minimal validated Config with default pipeline/gates/paths."""
    return Config.model_validate(MINIMAL_CONFIG)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A temp project root: templates, seed banlist, config, and the repo's
    mock responses copied in at their normal relative paths; FORGE_MOCK on.

    Tests may overwrite files under tests/fixtures/mock_responses/ freely —
    it's a per-test copy.
    """
    from prose_forge import llm

    shutil.copytree(REPO_ROOT / "prompt_templates", tmp_path / "prompt_templates")
    shutil.copy(REPO_ROOT / "seed_banlist.txt", tmp_path / "seed_banlist.txt")
    shutil.copy(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(
        REPO_ROOT / "tests/fixtures/mock_responses",
        tmp_path / "tests/fixtures/mock_responses",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FORGE_MOCK", "1")
    monkeypatch.delenv("FORGE_MOCK_DIR", raising=False)
    llm.reset_mock_counters()
    return tmp_path


@pytest.fixture
def mock_env(tmp_path, monkeypatch):
    """Enable FORGE_MOCK with an empty per-test mock dir; returns that dir."""
    from prose_forge import llm

    mock_dir = tmp_path / "mock_responses"
    mock_dir.mkdir()
    monkeypatch.setenv("FORGE_MOCK", "1")
    monkeypatch.setenv("FORGE_MOCK_DIR", str(mock_dir))
    llm.reset_mock_counters()
    return mock_dir
