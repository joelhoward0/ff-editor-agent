"""Discrimination eval: can outside judges tell the pipeline's prose from yours?

K corpus paragraphs and K pipeline paragraphs are shuffled (seeded) and shown
blind to each ``eval_judges`` model — families used nowhere else in the
pipeline. Mean accuracy at or below the gate means the pipeline text is
statistically hard to tell apart from the author's. History accumulates in
``data/eval_history.jsonl``.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import banlist, llm, stats
from .config import Config, MissingAssetError, config_hash
from .corpus import load_chunks

EVAL_HISTORY = Path("data/eval_history.jsonl")
MIN_WORDS, MAX_WORDS = 80, 200
_SPARKS = "▁▂▃▄▅▆▇█"


def _pick_paragraphs(texts: list[str], k: int, rng: random.Random) -> list[str]:
    """k paragraphs of 80–200 words; pads with nearest-length ones if scarce."""
    paras = []
    for text in texts:
        paras.extend(stats.split_paragraphs(text))
    paras = list(dict.fromkeys(paras))  # dedupe, keep order
    in_range = [p for p in paras if MIN_WORDS <= stats.word_count(p) <= MAX_WORDS]
    if len(in_range) >= k:
        return rng.sample(in_range, k)

    def distance(p: str) -> int:
        words = stats.word_count(p)
        return max(MIN_WORDS - words, words - MAX_WORDS, 0)

    rest = sorted((p for p in paras if p not in in_range), key=distance)
    padded = in_range + rest[: k - len(in_range)]
    rng.shuffle(padded)
    return padded


def _pipeline_texts() -> tuple[list[str], str]:
    """Recent run finals, falling back to the control set."""
    finals = sorted(
        Path("runs").glob("*/final.md"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if finals:
        return [p.read_text(encoding="utf-8") for p in finals[:20]], "runs"
    control = sorted(Path("data/control").glob("*.md"))
    if control:
        return [p.read_text(encoding="utf-8") for p in control], "control"
    raise MissingAssetError(
        "no pipeline text to evaluate (no runs/*/final.md, no data/control/)",
        run_first="forge run prompts/<id>.md  (or forge banlist build)",
    )


def sample_paragraphs(k: int, seed: int = 7) -> dict[str, Any]:
    """K human and K AI paragraphs, seeded."""
    rng = random.Random(seed)
    human = _pick_paragraphs([c["text"] for c in load_chunks()], k, rng)
    ai_texts, ai_source = _pipeline_texts()
    ai = _pick_paragraphs(ai_texts, k, rng)
    return {"human": human, "ai": ai, "ai_source": ai_source}


def trend_line(history: list[dict[str, Any]], last: int = 10) -> str:
    """Sparkline of the last N mean accuracies."""
    recent = history[-last:]
    if not recent:
        return "(no history)"
    marks = []
    for entry in recent:
        acc = entry.get("mean_accuracy", 0.0)
        marks.append(_SPARKS[min(int(acc * len(_SPARKS)), len(_SPARKS) - 1)])
    values = " ".join(f"{e.get('mean_accuracy', 0):.2f}" for e in recent)
    return f"{''.join(marks)}  ({values})"


def load_history() -> list[dict[str, Any]]:
    """All eval history entries, oldest first."""
    if not EVAL_HISTORY.exists():
        return []
    return [
        json.loads(line)
        for line in EVAL_HISTORY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def last_eval() -> dict[str, Any] | None:
    """Most recent eval entry, or None."""
    history = load_history()
    return history[-1] if history else None


def run_eval(config: Config, k: int = 10, seed: int = 7) -> dict[str, Any]:
    """Run the discrimination test and append to history; returns the summary."""
    if not config.models.eval_judges:
        raise MissingAssetError(
            "config.models.eval_judges is empty", run_first="edit config.yaml"
        )
    sample = sample_paragraphs(k, seed)
    items = [(p, "HUMAN") for p in sample["human"]] + [(p, "AI") for p in sample["ai"]]
    random.Random(seed).shuffle(items)

    accuracies: dict[str, float] = {}
    parse_failures = 0
    for model in config.models.eval_judges:
        correct = 0
        for paragraph, truth in items:
            prompt = llm.render_template("eval_judge", paragraph=paragraph)
            result = llm.chat(
                "eval_judge", [{"role": "user", "content": prompt}],
                sampling_key="judge", model_override=model, config=config,
            )
            parsed = llm.parse_json_response(result.text)
            verdict = parsed.get("verdict") if parsed else None
            if verdict not in ("HUMAN", "AI"):
                parse_failures += 1  # counts as a wrong answer
                continue
            if verdict == truth:
                correct += 1
        accuracies[model] = correct / len(items) if items else 0.0

    mean_accuracy = sum(accuracies.values()) / len(accuracies)
    ai_joined = "\n\n".join(sample["ai"])
    slop = banlist.hits_per_1k(ai_joined, banlist.active_rules())
    entry = {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "k": k,
        "ai_source": sample["ai_source"],
        "accuracies": accuracies,
        "mean_accuracy": round(mean_accuracy, 4),
        "slop": round(slop, 4),
        "parse_failures": parse_failures,
        "config_hash": config_hash(),
    }
    EVAL_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with EVAL_HISTORY.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    history = load_history()
    return {
        **entry,
        "pass": mean_accuracy <= config.gates.discrimination_max,
        "gate": config.gates.discrimination_max,
        "trend": trend_line(history),
    }
