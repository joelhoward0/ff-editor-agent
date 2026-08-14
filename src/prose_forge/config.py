"""Typed loading of ``config.yaml``.

All paths in prose-forge are relative to the current working directory (the
project root), which is what makes temp-workspace tests trivial.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

CONFIG_FILE = "config.yaml"


class SamplingParams(BaseModel):
    """One entry of the ``sampling:`` map, passed through to OpenRouter."""

    temperature: float = 0.7
    max_tokens: int = 2000
    min_p: float | None = None

    def to_payload(self) -> dict[str, Any]:
        """Return the request-body fields for this sampling profile."""
        payload: dict[str, Any] = {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.min_p is not None:
            payload["min_p"] = self.min_p
        return payload


class ModelsConfig(BaseModel):
    """Model slot -> OpenRouter slug mapping."""

    planner: str
    drafter_a: str
    drafter_b: str
    judge: str
    editor: str
    tagger: str
    eval_judges: list[str] = Field(default_factory=list)

    def slug(self, slot: str) -> str:
        """Return the model slug for a named slot (not ``eval_judges``)."""
        value = getattr(self, slot, None)
        if not isinstance(value, str):
            raise KeyError(f"unknown model slot: {slot!r}")
        return value


class PipelineConfig(BaseModel):
    samples_per_drafter: int = 2
    context_tail_words: int = 2500
    retrieved_excerpts: int = 3
    opening_line_mode: str = "generate3"
    max_edit_loops: int = 2
    pause_after_beats: bool = False


class GatesConfig(BaseModel):
    post_edit_banlist_hits_per_1k: float = 0.0
    sent_len_std_tolerance: float = 0.20
    em_dash_per_1k_max_ratio: float = 1.5
    punchline_para_rate_max_ratio: float = 1.15
    discrimination_max: float = 0.65


class PathsConfig(BaseModel):
    corpus: str = "corpus/"
    manuscript: str = "manuscript/book.md"
    bible: str = "bible/"


class Config(BaseModel):
    """Root schema for ``config.yaml``."""

    models: ModelsConfig
    sampling: dict[str, SamplingParams]
    provider_pinning: dict[str, list[str]] = Field(default_factory=dict)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    style_constraints: list[str] = Field(default_factory=list)
    gates: GatesConfig = Field(default_factory=GatesConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)


class MissingAssetError(RuntimeError):
    """A required config file or derived asset is absent.

    Carries the command the user should run first; the CLI maps this to exit
    code 2.
    """

    def __init__(self, message: str, run_first: str = ""):
        super().__init__(message)
        self.run_first = run_first


def load_config(root: Path | str = ".") -> Config:
    """Load and validate ``config.yaml`` from the project root."""
    path = Path(root) / CONFIG_FILE
    if not path.exists():
        raise MissingAssetError(
            f"{CONFIG_FILE} not found in {Path(root).resolve()}", run_first="forge init"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(data)


def config_hash(root: Path | str = ".") -> str:
    """Short content hash of config.yaml, recorded in eval history."""
    path = Path(root) / CONFIG_FILE
    if not path.exists():
        return "no-config"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
