"""Surgical editing: LLM rewrite of flagged spans + a deterministic diff-assert.

The editor model returns the complete text; ``diff_assert`` sentence-aligns it
against the original and rejects any change whose source sentences don't
overlap a flagged span. Rejected changes are healed by keeping the original
sentences, so an overreaching editor can never rewrite clean prose.
"""

from __future__ import annotations

import difflib
from typing import Any

from . import llm, stats
from .config import Config


def _segments(text: str) -> list[str]:
    """Contiguous sentence segments whose concatenation equals the text."""
    return [text[s:e] for s, e in stats.sentence_spans(text)]


def _overlaps(seg_start: int, seg_end: int, spans: list[dict]) -> bool:
    return any(span["start"] < seg_end and span["end"] > seg_start for span in spans)


def diff_assert(
    original: str, edited: str, flagged_spans: list[dict]
) -> tuple[bool, str, list[dict]]:
    """Validate an edit against the surgical contract.

    Returns ``(ok, merged, rejected)``: ``ok`` is True when every change's
    source sentences overlap a flagged span (inserted-from-nothing sentences
    are always illegal); ``merged`` is the text with illegal changes replaced
    by the original sentences; ``rejected`` describes each illegal change.
    """
    orig_segments = _segments(original)
    edit_segments = _segments(edited)
    offsets = []
    pos = 0
    for segment in orig_segments:
        offsets.append((pos, pos + len(segment)))
        pos += len(segment)

    matcher = difflib.SequenceMatcher(
        a=[s.strip() for s in orig_segments],
        b=[s.strip() for s in edit_segments],
        autojunk=False,
    )
    merged: list[str] = []
    rejected: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            merged.extend(orig_segments[i1:i2])
            continue
        if tag == "insert":
            rejected.append(
                {
                    "kind": "insert",
                    "edited": "".join(edit_segments[j1:j2]).strip(),
                    "reason": "added sentences with no source",
                }
            )
            continue  # drop the insertion
        # replace / delete: legal only where the source sentences are flagged.
        # Equal-length replace blocks are judged pairwise so one illegal
        # sentence doesn't drag down a neighboring legal rewrite.
        if tag == "replace" and (i2 - i1) == (j2 - j1):
            pairs = [(i, j) for i, j in zip(range(i1, i2), range(j1, j2), strict=True)]
        else:
            pairs = None
        if pairs is not None:
            for i, j in pairs:
                if _overlaps(offsets[i][0], offsets[i][1], flagged_spans):
                    merged.append(edit_segments[j])
                elif orig_segments[i].strip() == edit_segments[j].strip():
                    merged.append(orig_segments[i])
                else:
                    merged.append(orig_segments[i])
                    rejected.append(
                        {
                            "kind": "replace",
                            "original": orig_segments[i].strip(),
                            "edited": edit_segments[j].strip(),
                            "reason": "changed a sentence outside flagged spans",
                        }
                    )
            continue
        legal = all(
            _overlaps(offsets[i][0], offsets[i][1], flagged_spans) for i in range(i1, i2)
        )
        if legal:
            merged.extend(edit_segments[j1:j2])
        else:
            merged.extend(orig_segments[i1:i2])
            rejected.append(
                {
                    "kind": tag,
                    "original": "".join(orig_segments[i1:i2]).strip(),
                    "edited": "".join(edit_segments[j1:j2]).strip(),
                    "reason": "changed sentences outside flagged spans",
                }
            )
    return (not rejected, "".join(merged), rejected)


def _format_flags(spans: list[dict]) -> str:
    if not spans:
        return "(none)"
    return "\n".join(
        f'- chars {s["start"]}-{s["end"]}: "{s["text"]}"  (rule: {s["rule"]})' for s in spans
    )


def _format_examples(pairs: list[dict[str, str]]) -> str:
    if not pairs:
        return "(no examples available)"
    blocks = []
    for i, pair in enumerate(pairs, 1):
        blocks.append(
            f"Example {i} — draft phrasing:\n{pair['before']}\n\n"
            f"Example {i} — the author's phrasing:\n{pair['after']}"
        )
    return "\n\n".join(blocks)


def edit_pass(
    text: str,
    flagged_spans: list[dict],
    examples: list[dict[str, str]],
    config: Config,
) -> tuple[str, dict[str, Any]]:
    """One surgical edit attempt (+ one stern retry if the contract is broken).

    Returns the new text and an info dict with attempt count and any rejected
    (healed) changes.
    """
    info: dict[str, Any] = {"attempts": 0, "rejected": []}
    current_attempt_text = text
    for template in ("editor", "editor_stern"):
        prompt = llm.render_template(
            template,
            draft=text,
            flags=_format_flags(flagged_spans),
            examples=_format_examples(examples),
        )
        result = llm.chat(
            "editor", [{"role": "user", "content": prompt}],
            sampling_key="edit", mock_key="editor", config=config,
        )
        info["attempts"] += 1
        ok, merged, rejected = diff_assert(text, result.text.strip(), flagged_spans)
        current_attempt_text = merged
        if ok:
            info["rejected"] = []
            return merged, info
        info["rejected"] = rejected
    # still failing: keep original sentences for illegal changes (already merged)
    return current_attempt_text, info
