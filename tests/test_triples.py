"""Triples: build from chunks in mock mode, idempotency, editor example pairs."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from prose_forge import corpus, triples

REPO_ROOT = Path(__file__).resolve().parent.parent


def _ingest_fixture_corpus(workspace, cfg):
    dest = workspace / "corpus"
    shutil.copytree(REPO_ROOT / "tests/fixtures/corpus", dest)
    corpus.ingest(cfg)


def test_build_triples_writes_complete_records(workspace, cfg):
    _ingest_fixture_corpus(workspace, cfg)
    summary = triples.build_triples(cfg, n=2)
    assert summary["built"] == 2

    files = sorted((workspace / "data/triples").glob("*.json"))
    assert len(files) == 2
    record = json.loads(files[0].read_text())
    assert set(record) >= {"beats", "ai_draft", "gold_ref", "scene_type", "pov"}
    assert record["beats"].lstrip().startswith("1.")
    assert len(record["ai_draft"].split()) > 50
    chunk_ids = {c["id"] for c in corpus.load_chunks()}
    assert record["gold_ref"] in chunk_ids


def test_build_triples_skips_existing(workspace, cfg):
    _ingest_fixture_corpus(workspace, cfg)
    triples.build_triples(cfg, n=2)
    again = triples.build_triples(cfg, n=2)
    assert again["built"] == 0
    assert again["skipped"] == 2


def test_example_pairs_prefer_scene_type(workspace, cfg):
    _ingest_fixture_corpus(workspace, cfg)
    triples.build_triples(cfg, n=3)
    pairs = triples.example_pairs("interiority", k=2)
    assert 1 <= len(pairs) <= 2
    for pair in pairs:
        assert pair["before"]
        assert pair["after"]
        # excerpts are capped near 150 words
        assert len(pair["before"].split()) < 200
        assert len(pair["after"].split()) < 200


def test_example_pairs_empty_without_triples(workspace):
    assert triples.example_pairs("dialogue") == []
