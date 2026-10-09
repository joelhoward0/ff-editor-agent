"""Pure text metrics. Every public function is deterministic and unit-testable.

Sentence segmentation is intentionally simple (regex on terminal punctuation
and paragraph breaks): the same segmentation is used for baseline stats, lint
deltas, and the editor's diff-assert, so consistency matters more than
linguistic perfection.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np

# Any line break ends a paragraph (and so a sentence): Google Docs exports and
# most manuscripts use one newline per paragraph, not blank lines. Hard-wrapped
# prose would over-split; manuscripts essentially never hard-wrap.
_SENT_BOUNDARY = re.compile(r'(?<=[.!?…])["\'”’)\]]*[ \t]+|[ \t]*\n[ \t\n]*')
_WORD = re.compile(r"[\w'’-]+")
_PARA_SPLIT = re.compile(r"[ \t]*\n[ \t\n]*")
_MARKER_ONLY = re.compile(r"^[\s*_\-#~=]*$")
_ADVERB_TAG = re.compile(r"\b(said|asked|replied|whispered|muttered)\s+\w+ly\b", re.IGNORECASE)
_DQUOTE = re.compile(r'"([^"\n]*)"|“([^”\n]*)”')


def word_count(text: str) -> int:
    """Number of word tokens (apostrophes/hyphens kept inside words)."""
    return len(_WORD.findall(text))


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Contiguous ``(start, end)`` segments, one per sentence, covering the text.

    Concatenating ``text[s:e]`` for every span reproduces the input exactly —
    the editor's diff-assert relies on that.
    """
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENT_BOUNDARY.finditer(text):
        end = match.end()
        if text[start:end].strip():
            spans.append((start, end))
            start = end
    if start < len(text):
        if text[start:].strip():
            spans.append((start, len(text)))
        elif spans:
            spans[-1] = (spans[-1][0], len(text))
    return spans


def split_sentences(text: str) -> list[str]:
    """Stripped sentence strings (marker-only segments excluded)."""
    out = []
    for s, e in sentence_spans(text):
        seg = text[s:e].strip()
        if seg and not _MARKER_ONLY.match(seg):
            out.append(seg)
    return out


def split_paragraphs(text: str) -> list[str]:
    """Non-empty paragraphs, scene-break markers (``***``/``---``) excluded."""
    return [
        p.strip()
        for p in _PARA_SPLIT.split(text)
        if p.strip() and not _MARKER_ONLY.match(p.strip())
    ]


def sentence_word_counts(text: str) -> list[int]:
    """Word count of every sentence."""
    return [word_count(s) for s in split_sentences(text)]


def sent_len_mean(text: str) -> float:
    """Mean words per sentence."""
    counts = sentence_word_counts(text)
    return float(np.mean(counts)) if counts else 0.0


def sent_len_std(text: str) -> float:
    """Population standard deviation of words per sentence."""
    counts = sentence_word_counts(text)
    return float(np.std(counts)) if counts else 0.0


def para_len_percentiles(text: str) -> tuple[float, float]:
    """(p50, p90) of paragraph word counts, linear interpolation."""
    counts = [word_count(p) for p in split_paragraphs(text)]
    if not counts:
        return 0.0, 0.0
    return float(np.percentile(counts, 50)), float(np.percentile(counts, 90))


def _per_1k(count: int, words: int) -> float:
    return (count * 1000.0 / words) if words else 0.0


def em_dash_per_1k(text: str) -> float:
    """Em dashes per 1000 words."""
    return _per_1k(text.count("—"), word_count(text))


def semicolon_per_1k(text: str) -> float:
    """Semicolons per 1000 words."""
    return _per_1k(text.count(";"), word_count(text))


def dialogue_ratio(text: str) -> float:
    """Characters inside straight/curly double-quote pairs ÷ total characters."""
    if not text:
        return 0.0
    inside = sum(len(m.group(1) or m.group(2) or "") for m in _DQUOTE.finditer(text))
    return inside / len(text)


def adverb_tag_rate(text: str) -> float:
    """``(said|asked|replied|whispered|muttered) <word>ly`` matches per 1000 words."""
    return _per_1k(len(_ADVERB_TAG.findall(text)), word_count(text))


def punchline_para_rate(text: str) -> float:
    """Fraction of paragraphs whose final sentence is under 8 words."""
    paras = split_paragraphs(text)
    if not paras:
        return 0.0
    short = 0
    for para in paras:
        sentences = split_sentences(para)
        if sentences and word_count(sentences[-1]) < 8:
            short += 1
    return short / len(paras)


def excerpt(text: str, max_words: int = 150) -> str:
    """First ~max_words of a text, cut at a sentence boundary when possible."""
    out: list[str] = []
    count = 0
    for sentence in split_sentences(text):
        words = word_count(sentence)
        if out and count + words > max_words:
            break
        out.append(sentence)
        count += words
    return " ".join(out) if out else text[: max_words * 6]


def compute_stats(text: str) -> dict[str, float]:
    """All baseline metrics (except banlist hits) for one text."""
    p50, p90 = para_len_percentiles(text)
    return {
        "sent_len_mean": sent_len_mean(text),
        "sent_len_std": sent_len_std(text),
        "para_len_words_p50": p50,
        "para_len_words_p90": p90,
        "em_dash_per_1k": em_dash_per_1k(text),
        "semicolon_per_1k": semicolon_per_1k(text),
        "dialogue_ratio": dialogue_ratio(text),
        "adverb_tag_rate": adverb_tag_rate(text),
        "punchline_para_rate": punchline_para_rate(text),
    }


def compute_baseline(texts: list[str]) -> dict[str, Any]:
    """Corpus-wide metrics: sentences/paragraphs pooled across all texts."""
    sent_counts: list[int] = []
    para_counts: list[int] = []
    paras: list[str] = []
    for text in texts:
        sent_counts.extend(sentence_word_counts(text))
        for para in split_paragraphs(text):
            paras.append(para)
            para_counts.append(word_count(para))
    joined = "\n\n".join(texts)
    total_words = word_count(joined)
    short_finals = sum(
        1
        for para in paras
        if (sents := split_sentences(para)) and word_count(sents[-1]) < 8
    )
    return {
        "sent_len_mean": float(np.mean(sent_counts)) if sent_counts else 0.0,
        "sent_len_std": float(np.std(sent_counts)) if sent_counts else 0.0,
        "para_len_words_p50": float(np.percentile(para_counts, 50)) if para_counts else 0.0,
        "para_len_words_p90": float(np.percentile(para_counts, 90)) if para_counts else 0.0,
        "em_dash_per_1k": _per_1k(joined.count("—"), total_words),
        "semicolon_per_1k": _per_1k(joined.count(";"), total_words),
        "dialogue_ratio": dialogue_ratio(joined),
        "adverb_tag_rate": _per_1k(len(_ADVERB_TAG.findall(joined)), total_words),
        "punchline_para_rate": (short_finals / len(paras)) if paras else 0.0,
        "word_count": total_words,
    }
