"""Voice fingerprint: Burrows-style Delta over word rates + structural features.

A profile is the mean and spread of each feature across ~800-word windows of
the author's own prose. A draft is scored by how many standard deviations each
feature sits from the author's mean; ``delta`` is the mean |z|. Two-sided on
purpose: Claude imitating a style guide overcorrects (avoids words, chops
sentences) as often as it overuses.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

import numpy as np

from . import stats

WINDOW = 800
TOP_WORDS = 150
SHOWN = 6  # strongest tells are shown as advice and kept OUT of the verdict (anti-Goodhart)
# Closed-class words only: they carry style, not subject matter, so a profile
# built from a few chapters isn't thrown by which characters or places appear.
FUNCTION_WORDS = frozenset("""
a about above after again against all almost along already also although always am among an and
another any anyone anything are around as at away back be because been before behind being below
beneath beside besides between beyond both but by can can't cannot could couldn't did didn't do
does doesn't doing don't down during each either else enough even ever every everything few for
from further had hadn't has hasn't have haven't having he he'd he'll he's her here hers herself
him himself his how however i i'd i'll i'm i've if in inside instead into is isn't it it's its
itself just least less like little many may maybe me might mine more most much must my myself
near neither never next no nobody none nor not nothing now of off often on once one only onto or
other others our ours ourselves out outside over own past perhaps quite rather really same she
she'd she'll she's should shouldn't since so some somebody someone something sometimes somehow
still such than that that's the their theirs them themselves then there there's these they
they'd they'll they're they've thing things this those though through throughout till to too
toward towards under until up upon us very was wasn't we we'd we'll we're we've were weren't
what what's whatever when where whether which while who whoever whole whom whose why will with
within without won't would wouldn't yet you you'd you'll you're you've your yours yourself
says said asks asked goes went gets got looks looked turns turned
""".split())
_SPACED_HYPHEN = re.compile(r"(?<=\w) -(?=[ \"”’]|$)|(?<=\w)-$", re.M)
_TOKEN = re.compile(r"[a-z]+(?:['’][a-z]+)?")
STRUCT = {
    "sent_len_mean": "average sentence length",
    "sent_len_std": "sentence-length variety",
    "para_len_mean": "average paragraph length",
    "punchline_para_rate": "share of paragraphs ending on a short line",
    "dash_per_1k": "dashes (any style)",
    "spaced_hyphen_share": "share of dashes typed as spaced hyphens",
    "curly_quote_share": "share of quotes that are curly",
    "exclaim_per_1k": "exclamation marks",
    "colon_per_1k": "colons",
    "dialogue_ratio": "share of text in dialogue",
}


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower().replace("’", "'"))


def windows(text: str, size: int = WINDOW) -> list[str]:
    """Consecutive ~size-word chunks cut on paragraph boundaries."""
    out, cur, n = [], [], 0
    for para in stats.split_paragraphs(text):
        cur.append(para)
        n += stats.word_count(para)
        if n >= size:
            out.append("\n".join(cur))
            cur, n = [], 0
    if n >= size // 2:
        out.append("\n".join(cur))
    return out


def _struct(text: str) -> dict[str, float]:
    paras = stats.split_paragraphs(text)
    words = stats.word_count(text) or 1
    hyphens = len(_SPACED_HYPHEN.findall(text))
    dashes = text.count("—") + text.count("–") + hyphens
    curly = text.count("“") + text.count("”")
    quotes = curly + text.count('"')
    return {
        "sent_len_mean": stats.sent_len_mean(text),
        "sent_len_std": stats.sent_len_std(text),
        "para_len_mean": words / max(len(paras), 1),
        "punchline_para_rate": stats.punchline_para_rate(text),
        "dash_per_1k": dashes * 1000 / words,
        "spaced_hyphen_share": hyphens / dashes if dashes else 0.0,
        "curly_quote_share": curly / quotes if quotes else 0.0,
        "exclaim_per_1k": text.count("!") * 1000 / words,
        "colon_per_1k": text.count(":") * 1000 / words,
        "dialogue_ratio": stats.dialogue_ratio(text),
    }


def features(text: str, vocab: list[str]) -> dict[str, float]:
    toks = _tokens(text)
    counts = Counter(toks)
    total = len(toks) or 1
    out = {f"w:{w}": counts[w] * 1000 / total for w in vocab}
    out.update(_struct(text))
    return out


def _matrix(texts: list[str], vocab: list[str], keys: list[str] | None = None):
    rows = [features(w, vocab) for t in texts for w in windows(t)]
    keys = keys or (list(rows[0]) if rows else [])
    return keys, np.array([[r[k] for k in keys] for r in rows])


def _fit(mat: np.ndarray, cmat: np.ndarray | None, keys: list[str]) -> dict[str, Any]:
    mean, std = mat.mean(axis=0), np.maximum(mat.std(axis=0), 1e-3)
    fit: dict[str, Any] = {
        "mean": dict(zip(keys, mean.round(4).tolist(), strict=True)),
        "std": dict(zip(keys, std.round(4).tolist(), strict=True)),
    }
    if cmat is not None and len(cmat) >= 2:
        pooled = np.sqrt((mat.var(axis=0) + cmat.var(axis=0)) / 2) + 1e-3
        d = (cmat.mean(axis=0) - mean) / pooled
        fit["weights"] = {
            k: round(float(v), 3) for k, v in zip(keys, d, strict=True) if abs(v) >= 0.5
        }
    return fit


def _gate_weights(fit: dict[str, Any], shown: list[str]) -> dict[str, float]:
    return {k: v for k, v in (fit.get("weights") or {}).items() if k not in shown}


def _score_rows(
    rows: np.ndarray, keys: list[str], fit: dict[str, Any], shown: list[str]
) -> list[float]:
    w = _gate_weights(fit, shown)
    if not w:
        return []
    idx = [keys.index(k) for k in w]
    mean = np.array([fit["mean"][k] for k in w])
    std = np.array([fit["std"][k] for k in w])
    wv = np.array(list(w.values()))
    return (((rows[:, idx] - mean) / std) @ wv / np.abs(wv).sum()).tolist()


def build_profile(texts: list[str], controls: list[str] | None = None) -> dict[str, Any]:
    """Mean/std of every feature across windows of the author's prose.

    ``controls`` are Claude's attempts at the author's own scenes. When given,
    the profile also learns ``weights`` (Cohen's d per feature, |d| >= 0.5:
    which features separate this author from Claude-imitating-them, and which
    way) and ``calibration``: 3-fold cross-validated scores of held-out author
    and control windows, so verdict thresholds fit this author rather than a
    fixed constant.
    """
    def top(ts: list[str], n: int) -> list[str]:
        counts = Counter(t for t in _tokens("\n".join(ts)) if t in FUNCTION_WORDS)
        return [w for w, _ in counts.most_common(n)]

    vocab = top(texts, TOP_WORDS)
    if controls:  # words Claude leans on that the author rarely uses must be measurable too
        vocab += [w for w in top(controls, TOP_WORDS // 3) if w not in vocab]
    keys, mat = _matrix(texts, vocab)
    if len(mat) < 3:
        raise ValueError(f"need at least {3 * WINDOW} words of prose, got {len(mat)} windows")
    cmat = _matrix(controls, vocab, keys)[1] if controls else None
    profile: dict[str, Any] = {"vocab": vocab, "windows": len(mat), **_fit(mat, cmat, keys)}
    if "weights" in profile:
        w = profile["weights"]
        profile["shown"] = sorted(w, key=lambda k: -abs(w[k]))[:SHOWN]
    if "weights" in profile and len(cmat) >= 3:
        own, other = [], []
        for a_idx, c_idx in zip(
            np.array_split(np.arange(len(mat)), 3), np.array_split(np.arange(len(cmat)), 3),
            strict=True,
        ):
            fit = _fit(np.delete(mat, a_idx, 0), np.delete(cmat, c_idx, 0), keys)
            own += _score_rows(mat[a_idx], keys, fit, profile["shown"])
            other += _score_rows(cmat[c_idx], keys, fit, profile["shown"])
        if own and other:
            # medians, not tails: verdicts are on whole chapters, which average
            # many windows, so their scores sit near these centres
            profile["calibration"] = {
                "author_p50": round(float(np.median(own)), 3),
                "imitation_p50": round(float(np.median(other)), 3),
            }
    return profile


def verdict(score: float, profile: dict[str, Any]) -> str:
    """Author below 40% of the way from author to imitation centre; imitation past 60%."""
    cal = profile.get("calibration", {"author_p50": 0.0, "imitation_p50": 1.0})
    a, gap = cal["author_p50"], max(cal["imitation_p50"] - cal["author_p50"], 0.1)
    low, high = a + 0.4 * gap, a + 0.6 * gap
    if score <= low:
        return "reads like the author"
    return "reads like an imitation" if score >= high else "drifting toward imitation"


def claude_score(text: str, profile: dict[str, Any]) -> float | None:
    """Weighted z along the learned author-vs-Claude directions.

    ~0 reads like the author; ~1+ leans toward Claude's imitation of them.
    Excludes the ``shown`` features, so editing toward the drift advice can't
    pass the gate on its own. None when the profile was built without controls.
    """
    weights = _gate_weights(profile, profile.get("shown", []))
    if not weights:
        return None
    z = zscores(text, profile)
    total = sum(abs(w) for w in weights.values())
    return float(sum(z[k] * w for k, w in weights.items()) / total)


def zscores(text: str, profile: dict[str, Any]) -> dict[str, float]:
    feats = features(text, profile["vocab"])
    return {k: (feats[k] - profile["mean"][k]) / profile["std"][k] for k in profile["mean"]}


def delta(text: str, profile: dict[str, Any]) -> float:
    """Mean |z| across features; the author's own windows sit near ~0.8."""
    return float(np.mean(np.abs(list(zscores(text, profile).values()))))


def drift_report(text: str, profile: dict[str, Any], top: int = 8) -> list[dict[str, Any]]:
    """The features pulling the draft away from the author, plain words, biggest first.

    With learned weights, only features that separate the author from Claude
    count, ranked by how far they push toward Claude. Without, any |z| >= 2.
    """
    feats = features(text, profile["vocab"])
    z = zscores(text, profile)
    weights = profile.get("weights")
    if weights:
        pull = {k: z[k] * weights[k] for k in profile.get("shown", weights)}
        ranked = [
            k for k in sorted(pull, key=lambda k: -pull[k]) if pull[k] > 0.5 and abs(z[k]) >= 1
        ]
    else:
        ranked = [k for k in sorted(z, key=lambda k: -abs(z[k])) if abs(z[k]) >= 2]
    out = []
    for k in ranked[:top]:
        label = f'the word "{k[2:]}"' if k.startswith("w:") else STRUCT[k]
        unit = " per 1,000 words" if k.startswith("w:") or k.endswith("_1k") else ""
        out.append({
            "feature": label,
            "direction": "more than you" if z[k] > 0 else "less than you",
            "draft": round(feats[k], 2),
            "you": round(profile["mean"][k], 2),
            "unit": unit.strip(),
            "z": round(z[k], 1),
        })
    return out
