"""What the author changed in a draft Claude handed back: learn from their edits.

Stdlib only. Inputs are whatever is on disk (a Drive download or read result,
a Docs read_doc result, or plain text; see chunk.load): the snapshot taken at
handoff and the doc as it stands now.

    python edits.py SNAPSHOT EDITED OUT.json

OUT.json: {"summary": {...}, "spots": [{"kind", "at", "claude", "author"}]}
  kind "edit"   the author rewrote Claude's paragraph(s): author = author-edited
                text, claude = a rejected imitation
  kind "cut"    the author deleted it: claude = rejected, author = ""
  kind "added"  the author wrote new paragraph(s): author = author-written
Trivial changes (a typo, quote style, a word or two) are not spots.
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from chunk import load  # noqa: E402

MIN_WORDS_CHANGED = 3
QUOTES = str.maketrans("“”‘’", "\"\"''")


MARKUP = re.compile(r"^#+\s*|\*+|\\(?=[^\w\s])|\[[a-z]{1,2}\]|﻿")  # md, comment anchors, BOM


def _norm(p: str) -> str:
    return re.sub(r"\s+", " ", MARKUP.sub("", p.translate(QUOTES))).strip().lower()


def _paras(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n") if p.strip()]


def _changed_words(a: str, b: str) -> int:
    aw, bw = _norm(a).split(), _norm(b).split()
    sm = difflib.SequenceMatcher(None, aw, bw, autojunk=False)
    return sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")


def diff(snapshot: str, edited: str) -> dict:
    a, b = _paras(snapshot), _paras(edited)
    sm = difflib.SequenceMatcher(None, [_norm(p) for p in a], [_norm(p) for p in b], autojunk=False)
    spots = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        claude, author = "\n".join(a[i1:i2]), "\n".join(b[j1:j2])
        if op == "replace" and _changed_words(claude, author) < MIN_WORDS_CHANGED:
            continue
        kind = {"replace": "edit", "delete": "cut", "insert": "added"}[op]
        at = " ".join((a[i1 - 1] if i1 else "(start)").split()[:8])
        spots.append({"kind": kind, "at": at, "claude": claude, "author": author})
    n = lambda key, k: sum(len(s[key].split()) for s in spots if s["kind"] == k)  # noqa: E731
    return {
        "summary": {
            "draft_words": len(snapshot.split()), "edited_words": len(edited.split()),
            "spots": len(spots), "edit_words": n("author", "edit"),
            "cut_words": n("claude", "cut"), "added_words": n("author", "added"),
        },
        "scenes": _scenes(snapshot, edited),
        "spots": spots,
    }


def _scenes(snapshot: str, edited: str) -> list[dict]:
    """Scene-level shape: each scene's opening and word count, draft vs edited,
    matched by opening line so cut, added and moved scenes show up."""
    def split(t: str) -> list[str]:
        return [s.strip() for s in SCENE_BREAK.split(t) if s.strip()]
    a, b = split(snapshot), split(edited)
    key = lambda s: _norm(s.split("\n", 1)[0])[:40]  # noqa: E731
    sm = difflib.SequenceMatcher(None, [key(s) for s in a], [key(s) for s in b], autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        for k in range(max(i2 - i1, j2 - j1)):
            x = a[i1 + k] if i1 + k < i2 else ""
            y = b[j1 + k] if j1 + k < j2 else ""
            opens = " ".join((x or y).split("\n", 1)[0].split()[:8])
            out.append({"opens": opens, "draft_words": len(x.split()),
                        "edited_words": len(y.split()), "change": "same" if op == "equal" else op})
    return out


SCENE_BREAK = re.compile(r"(?m)^\s*(?:[—–-]{1,3}|[*_]{3,}|⁂|#)\s*$")


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        sys.exit(__doc__)
    out = diff(load(Path(argv[0])), load(Path(argv[1])))
    Path(argv[2]).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out["summary"]))


if __name__ == "__main__":
    main(sys.argv[1:])
