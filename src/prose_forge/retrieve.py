"""TF-IDF retrieval of corpus excerpts, filtered by POV and scene type.

Local scikit-learn only — no embedding APIs. Used to hand the drafter a few
voice-reference excerpts that match the planned scene, and to give the judge a
gold excerpt to compare drafts against.
"""

from __future__ import annotations

from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel


def filter_chunks(
    chunks: list[dict[str, Any]],
    pov: str | None = None,
    scene_type: str | None = None,
) -> list[dict[str, Any]]:
    """Chunks matching both tags, falling back to scene-type only, then all."""
    def match(chunk: dict[str, Any], use_pov: bool, use_type: bool) -> bool:
        tags = chunk.get("tags", {})
        if use_pov and pov and tags.get("pov", "").lower() != pov.lower():
            return False
        if use_type and scene_type and tags.get("scene_type") != scene_type:
            return False
        return True

    for use_pov, use_type in ((True, True), (False, True), (False, False)):
        matched = [c for c in chunks if match(c, use_pov, use_type)]
        if matched:
            return matched
    return list(chunks)


def top_matches(
    chunks: list[dict[str, Any]],
    query: str,
    k: int = 3,
    pov: str | None = None,
    scene_type: str | None = None,
) -> list[dict[str, Any]]:
    """Top-k tag-filtered chunks ranked by TF-IDF cosine similarity to the query."""
    candidates = filter_chunks(chunks, pov=pov, scene_type=scene_type)
    if not candidates:
        return []
    if len(candidates) <= k:
        return candidates
    texts = [c["text"] for c in candidates]
    vectorizer = TfidfVectorizer(stop_words="english")
    matrix = vectorizer.fit_transform(texts + [query])
    scores = linear_kernel(matrix[-1], matrix[:-1]).ravel()
    ranked = sorted(range(len(candidates)), key=lambda i: (-scores[i], i))
    return [candidates[i] for i in ranked[:k]]
