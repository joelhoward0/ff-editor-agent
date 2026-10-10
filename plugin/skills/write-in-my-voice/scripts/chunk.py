"""Break a manuscript too big for one read or one tool call into chapter files.

Stdlib only, so it runs wherever the skill runs. Input is whatever you have on
disk: a Google Docs `read_doc` result (raw or saved by the host as
{"content": ...}), a Drive `read_file_content` result ({"fileContent": ...}),
or plain text/markdown.

    python chunk.py split SRC OUTDIR [--max-words 6000]
        one file per chapter (split further at scene breaks or paragraphs if a
        chapter is over --max-words), plus OUTDIR/index.json listing each
        file's title, word count and first line.
    python chunk.py names SRC OUT
        the first sentence each proper name appears in: a small stand-in for
        the full text as check_continuity's draft or canon.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^(#{1,6})\s+(.+)$|^((?:chapter|part)\s+[\w.-]+.{0,70})$", re.I)
SCENE_BREAK = re.compile(r"^\s*(?:[—–-]{1,3}|[*_]{3,}|⁂|#)\s*$")
MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!~|>])")
SENTENCE = re.compile(r"[^.!?]+[.!?]+[\"”’')]*\s*|[^.!?]+$")
CAP = re.compile(r"\b[A-Z][a-z’'-]+\b")


def load(src: Path) -> str:
    """Plain text with headings as '# ' lines, whatever the input format."""
    raw = src.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except ValueError:
        return raw
    data = data.get("content", data) if isinstance(data, dict) else data
    if isinstance(data, dict) and "fileContent" in data:
        return MD_ESCAPE.sub(r"\1", data["fileContent"]).replace("**", "")
    if isinstance(data, dict) and ("tabs" in data or "body" in data):
        return _doc_text(data)
    return raw


def _doc_text(doc: dict) -> str:
    tabs, lines = list(doc.get("tabs", [])), []
    bodies = [doc["body"]] if "body" in doc else []
    while tabs:
        tab = tabs.pop(0)
        bodies.append(tab["documentTab"]["body"])
        tabs[:0] = tab.get("childTabs", [])
    for body in bodies:
        for el in body.get("content", []):
            para = el.get("paragraph")
            if not para:
                continue
            text = "".join(e.get("textRun", {}).get("content", "") for e in para["elements"])
            text = text.rstrip("\n")
            style = para.get("paragraphStyle", {}).get("namedStyleType", "")
            level = 1 if style == "TITLE" else int(style[-1]) if style.startswith("HEADING") else 0
            lines.append(f"{'#' * level} {text}" if level and text else text)
    return "\n\n".join(lines)


def words(text: str) -> int:
    return len(text.split())


def split(text: str, max_words: int) -> list[tuple[str, str]]:
    """(title, text) per chunk: chapters at headings, oversized ones cut smaller.

    Splits only at the top heading level that repeats (chapters, not the scene
    or entry headings inside them); "Chapter 12" lines count as level 1."""
    lines = text.splitlines()
    matches = [HEADING.match(ln.strip()) for ln in lines]
    levels = [len(m.group(1)) if m.group(1) else 1 for m in matches if m]
    repeated = [lv for lv in set(levels) if levels.count(lv) > 1]
    split_at = min(repeated or levels or [1])
    sections: list[list[str]] = [["(front matter)"]]
    for line, m in zip(lines, matches, strict=True):
        if m and (len(m.group(1)) if m.group(1) else 1) == split_at:
            sections.append([(m.group(2) or m.group(3)).strip()])
        sections[-1].append(line)
    out = []
    for title, *body in sections:
        chunk = "\n".join(body).strip()
        if not chunk:
            continue
        parts = _pack(chunk, max_words)
        for i, part in enumerate(parts, 1):
            out.append((title if len(parts) == 1 else f"{title} (part {i})", part))
    return out


def _pack(text: str, max_words: int) -> list[str]:
    if words(text) <= max_words:
        return [text]
    # prefer scene breaks, fall back to paragraphs; never split a paragraph
    paras = re.split(r"\n\s*\n", text)
    parts, cur = [], []
    for para in paras:
        over = words("\n\n".join(cur + [para])) > max_words
        if cur and (over or (SCENE_BREAK.match(para) and words("\n\n".join(cur)) > max_words / 2)):
            parts.append("\n\n".join(cur).strip())
            cur = []
        cur.append(para)
    if cur:
        parts.append("\n\n".join(cur).strip())
    return [p for p in parts if p]


def names_extract(text: str) -> str:
    """First sentence for each proper name (capitalized mid-sentence, never seen lowercase)."""
    common = set(re.findall(r"\b[a-z][a-z’'-]+\b", text))
    seen, keep = set(), []
    for para in text.splitlines():
        for sentence in SENTENCE.findall(para.strip().lstrip("#").strip()):
            found = CAP.findall(sentence)
            caps = {w for i, w in enumerate(found) if i or not sentence.startswith(w)}
            new = {w for w in caps if w.lower() not in common} - seen
            if new:
                seen |= new
                keep.append(sentence.strip())
    return "\n".join(keep)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("split")
    s.add_argument("src", type=Path)
    s.add_argument("outdir", type=Path)
    s.add_argument("--max-words", type=int, default=6000)
    n = sub.add_parser("names")
    n.add_argument("src", type=Path)
    n.add_argument("out", type=Path)
    a = ap.parse_args(argv)
    text = load(a.src)
    if a.cmd == "names":
        a.out.write_text(names_extract(text), encoding="utf-8")
        print(f"{a.out}: {words(a.out.read_text(encoding='utf-8'))} words")
        return
    a.outdir.mkdir(parents=True, exist_ok=True)
    index = []
    for i, (title, chunk) in enumerate(split(text, a.max_words), 1):
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "chunk"
        path = a.outdir / f"{i:03d}-{slug}.md"
        path.write_text(chunk + "\n", encoding="utf-8")
        first = next((ln for ln in chunk.splitlines()[1:] if ln.strip()), "")[:100]
        index.append({"file": path.name, "title": title, "words": words(chunk), "starts": first})
    (a.outdir / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"{len(index)} chunks, {sum(c['words'] for c in index)} words -> {a.outdir}")


if __name__ == "__main__":
    sys.exit(main())
