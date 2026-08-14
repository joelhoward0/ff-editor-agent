"""Shared fixtures: a minimal validated Config and a mock-mode environment."""

from __future__ import annotations

import pytest

from prose_forge.config import Config

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
def mock_env(tmp_path, monkeypatch):
    """Enable FORGE_MOCK with an empty per-test mock dir; returns that dir."""
    from prose_forge import llm

    mock_dir = tmp_path / "mock_responses"
    mock_dir.mkdir()
    monkeypatch.setenv("FORGE_MOCK", "1")
    monkeypatch.setenv("FORGE_MOCK_DIR", str(mock_dir))
    llm.reset_mock_counters()
    return mock_dir
