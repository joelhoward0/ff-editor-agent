"""Triples: beat sheet → blind AI draft → gold reference, per corpus chunk.

Triples feed the editor's few-shot before/after pairs and the discrimination
eval. The blind draft uses the same continuation framing as the real pipeline,
minus retrieval, so its AI-isms are representative.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from . import llm, stats
from .config import Config
from .corpus import load_chunks

TRIPLES_DIR = Path("data/triples")


def mine_beats(chunk_text: str, config: Config) -> str:
    """Terse numbered beat sheet (5–8 beats, no prose quotes) via the tagger slot."""
    prompt = llm.render_template("beats_miner", scene=chunk_text)
    result = llm.chat(
        "tagger",
        [{"role": "user", "content": prompt}],
        sampling_key="judge",
        mock_key="beats_miner",
        config=config,
    )
    return result.text.strip()


def blind_draft(beats: str, config: Config) -> str:
    """Draft a scene from beats alone — no manuscript tail, no retrieval."""
    prompt = llm.render_template(
        "drafter",
        manuscript_tail="(beginning of manuscript)",
        excerpts="(none)",
        beats=beats,
        style_constraints="\n".join(f"- {c}" for c in config.style_constraints),
    )
    result = llm.chat(
        "drafter_a",
        [{"role": "user", "content": prompt}],
        sampling_key="draft",
        config=config,
    )
    return result.text.strip()


def build_triples(config: Config, n: int = 40, seed: int = 13) -> dict[str, Any]:
    """Sample corpus chunks and write ``data/triples/<id>.json`` for each."""
    chunks = load_chunks()
    rng = random.Random(seed)
    sample = rng.sample(chunks, min(n, len(chunks)))
    TRIPLES_DIR.mkdir(parents=True, exist_ok=True)
    built = skipped = 0
    for chunk in sample:
        dest = TRIPLES_DIR / f"{chunk['id']}.json"
        if dest.exists():
            skipped += 1
            continue
        beats = mine_beats(chunk["text"], config)
        triple = {
            "beats": beats,
            "ai_draft": blind_draft(beats, config),
            "gold_ref": chunk["id"],
            "scene_type": chunk.get("tags", {}).get("scene_type", "interiority"),
            "pov": chunk.get("tags", {}).get("pov", "unknown"),
        }
        dest.write_text(json.dumps(triple, indent=2, ensure_ascii=False), encoding="utf-8")
        built += 1
    return {"built": built, "skipped": skipped, "total": built + skipped}


def load_triples() -> list[dict[str, Any]]:
    """All triples on disk (empty list if none built yet)."""
    if not TRIPLES_DIR.exists():
        return []
    out = []
    for path in sorted(TRIPLES_DIR.glob("*.json")):
        out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


def example_pairs(scene_type: str, k: int = 2) -> list[dict[str, str]]:
    """Up to k before/after pairs (~150 words each) for the editor's few-shot.

    "Before" is a blind AI draft excerpt; "after" is the matching gold corpus
    excerpt. Pairs matching the scene type are preferred.
    """
    triples = load_triples()
    if not triples:
        return []
    by_id = {c["id"]: c for c in load_chunks()}
    matching = [t for t in triples if t.get("scene_type") == scene_type]
    chosen = (matching + [t for t in triples if t not in matching])[:k]
    pairs = []
    for triple in chosen:
        gold = by_id.get(triple["gold_ref"])
        if gold is None:
            continue
        pairs.append(
            {
                "before": stats.excerpt(triple["ai_draft"]),
                "after": stats.excerpt(gold["text"]),
            }
        )
    return pairs
