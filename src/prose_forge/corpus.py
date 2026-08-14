"""Corpus ingestion: scene chunking, tagging, and the baseline asset.

``ingest`` splits every ``corpus/`` file into ~600–1500-word chunks (explicit
scene breaks are never merged across), tags each chunk with the cheap tagger
slot, and writes ``data/chunks.jsonl``. Idempotent: files whose content hash
is unchanged keep their existing chunks and are never re-tagged.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import banlist, llm, stats
from .config import Config, MissingAssetError

CHUNKS_PATH = Path("data/chunks.jsonl")
BASELINE_PATH = Path("data/baseline.json")

MIN_CHUNK_WORDS = 600
MAX_CHUNK_WORDS = 1500

_SCENE_MARKER = re.compile(r"^[ \t]*(?:\*{3,}|-{3,}|\* \* \*)[ \t]*$", re.MULTILINE)
_HARD_BREAK = re.compile(r"(?:\n[ \t]*){3,}")  # two-plus blank lines

SCENE_TYPES = {"action", "dialogue", "interiority", "transition"}
TENSES = {"past", "present"}
FALLBACK_TAGS = {"pov": "unknown", "scene_type": "interiority", "tense": "past"}


def split_scene_blocks(text: str) -> list[str]:
    """Split on ``***``/``---`` marker lines and runs of 2+ blank lines."""
    pieces = _SCENE_MARKER.split(text)
    blocks: list[str] = []
    for piece in pieces:
        blocks.extend(_HARD_BREAK.split(piece))
    return [b.strip() for b in blocks if b.strip()]


def pack_paragraphs(
    scene: str, min_words: int = MIN_CHUNK_WORDS, max_words: int = MAX_CHUNK_WORDS
) -> list[str]:
    """Pack a scene's paragraphs into chunks of roughly min–max words.

    A scene shorter than ``min_words`` stays one (short) chunk; a single
    paragraph longer than ``max_words`` is never split mid-paragraph.
    """
    paras = [p.strip() for p in re.split(r"\n[ \t]*\n", scene) if p.strip()]
    chunks: list[list[str]] = []
    current: list[str] = []
    current_words = 0
    for para in paras:
        pw = stats.word_count(para)
        if current and current_words + pw > max_words and current_words >= min_words:
            chunks.append(current)
            current, current_words = [], 0
        current.append(para)
        current_words += pw
    if current:
        chunks.append(current)
    return ["\n\n".join(chunk) for chunk in chunks]


def chunk_text(text: str) -> list[str]:
    """Scene-aware chunking of a whole corpus file."""
    out: list[str] = []
    for scene in split_scene_blocks(text):
        out.extend(pack_paragraphs(scene))
    return out


def file_hash(path: Path) -> str:
    """Content hash used for ingest idempotency."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def tag_chunk(text: str, config: Config) -> dict[str, str]:
    """Ask the tagger slot for strict-JSON tags; fall back on parse failure."""
    prompt = llm.render_template("tagger", scene=text)
    result = llm.chat("tagger", [{"role": "user", "content": prompt}],
                      sampling_key="judge", config=config)
    parsed = llm.parse_json_response(result.text) or {}
    tags = dict(FALLBACK_TAGS)
    if isinstance(parsed.get("pov"), str) and parsed["pov"].strip():
        tags["pov"] = parsed["pov"].strip()
    if parsed.get("scene_type") in SCENE_TYPES:
        tags["scene_type"] = parsed["scene_type"]
    if parsed.get("tense") in TENSES:
        tags["tense"] = parsed["tense"]
    return tags


def iter_corpus_files(config: Config) -> list[Path]:
    """Sorted ``.md``/``.txt`` files under the corpus directory."""
    root = Path(config.paths.corpus)
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.suffix in (".md", ".txt") and p.is_file())


def load_chunks() -> list[dict[str, Any]]:
    """All chunk records from ``data/chunks.jsonl``; raises if not ingested."""
    if not CHUNKS_PATH.exists():
        raise MissingAssetError(
            "data/chunks.jsonl not found — no corpus has been ingested",
            run_first="forge ingest",
        )
    return [
        json.loads(line)
        for line in CHUNKS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def ingest(config: Config) -> dict[str, Any]:
    """(Re)build ``data/chunks.jsonl`` from the corpus; returns a summary."""
    files = iter_corpus_files(config)
    if not files:
        raise MissingAssetError(
            f"no .md/.txt files in {config.paths.corpus} — add your prose first",
            run_first=f"cp your-scenes.md {config.paths.corpus}",
        )
    existing: dict[str, list[dict[str, Any]]] = {}
    if CHUNKS_PATH.exists():
        for record in load_chunks():
            existing.setdefault(record["source"], []).append(record)

    records: list[dict[str, Any]] = []
    tagged = reused = 0
    for path in files:
        source = str(path)
        digest = file_hash(path)
        prior = existing.get(source, [])
        if prior and all(r.get("file_hash") == digest for r in prior):
            records.extend(prior)
            reused += len(prior)
            continue
        text = path.read_text(encoding="utf-8")
        for i, chunk in enumerate(chunk_text(text)):
            chunk_id = f"{path.stem}-{i:03d}-{hashlib.sha256(chunk.encode()).hexdigest()[:8]}"
            records.append(
                {
                    "id": chunk_id,
                    "source": source,
                    "file_hash": digest,
                    "text": chunk,
                    "tags": tag_chunk(chunk, config),
                    "word_count": stats.word_count(chunk),
                }
            )
            tagged += 1

    CHUNKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CHUNKS_PATH.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"files": len(files), "chunks": len(records), "tagged": tagged, "reused": reused}


def build_baseline_asset() -> dict[str, Any]:
    """Compute corpus baseline stats (incl. banlist self-hits) → ``data/baseline.json``."""
    chunks = load_chunks()
    texts = [c["text"] for c in chunks]
    baseline = stats.compute_baseline(texts)
    rules = banlist.active_rules()
    baseline["banlist_hits_per_1k"] = banlist.hits_per_1k("\n\n".join(texts), rules)
    baseline["chunk_count"] = len(chunks)
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
    return baseline


def load_baseline() -> dict[str, Any]:
    """Parsed ``data/baseline.json``; raises if not built."""
    if not BASELINE_PATH.exists():
        raise MissingAssetError(
            "data/baseline.json not found", run_first="forge baseline"
        )
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
