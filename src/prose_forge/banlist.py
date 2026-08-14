"""Banlist rules: parsing, matching, and (in ``build_banlist``) n-gram mining.

File format — one entry per line:
- literal by default: word-boundary, case-insensitive, whitespace-flexible;
  a parenthesized group like ``(himself|herself)`` is treated as alternation
- ``re:`` prefix: raw regex, compiled as written
- trailing ``# keep`` / ``# drop`` markers are hand-edits that ``build`` must
  honor (keep = always retain; drop = never re-add); other ``#`` trailers are
  informational comments
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import stats

SEED_BANLIST = Path("seed_banlist.txt")
BANLIST_PATH = Path("data/banlist.txt")

_ALTERNATION_TOKEN = re.compile(r"^\(([\w'’|-]+)\)$")
_TRAILING_MARKER = re.compile(r"\s+#\s*(.*)$")


@dataclass(frozen=True)
class Rule:
    """One banlist entry, compiled and ready to match."""

    pattern: str
    is_regex: bool
    marker: str | None  # "keep" | "drop" | None
    comment: str | None
    regex: re.Pattern[str]

    @property
    def raw_line(self) -> str:
        """Reconstruct the source line (without any trailing comment)."""
        return ("re:" + self.pattern) if self.is_regex else self.pattern


def _compile_literal(phrase: str) -> re.Pattern[str]:
    parts = []
    for token in phrase.split():
        alt = _ALTERNATION_TOKEN.match(token)
        if alt:
            options = "|".join(re.escape(opt) for opt in alt.group(1).split("|"))
            parts.append(f"(?:{options})")
        else:
            parts.append(re.escape(token))
    return re.compile(r"\b" + r"\s+".join(parts) + r"\b", re.IGNORECASE)


def parse_rule_line(line: str) -> Rule | None:
    """Parse one banlist line; None for blanks and full-line comments."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    marker = None
    comment = None
    trailer = _TRAILING_MARKER.search(stripped)
    if trailer:
        comment = trailer.group(1).strip()
        stripped = stripped[: trailer.start()].rstrip()
        if comment in ("keep", "drop"):
            marker = comment
    if not stripped:
        return None
    if stripped.startswith("re:"):
        pattern = stripped[3:]
        return Rule(pattern, True, marker, comment, re.compile(pattern))
    return Rule(stripped, False, marker, comment, _compile_literal(stripped))


def load_rules(path: Path | str) -> list[Rule]:
    """All active rules from a banlist file (``# drop`` entries excluded)."""
    rules = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        rule = parse_rule_line(line)
        if rule is not None and rule.marker != "drop":
            rules.append(rule)
    return rules


def active_rules() -> list[Rule]:
    """Rules from ``data/banlist.txt`` if built, else ``seed_banlist.txt``."""
    path = BANLIST_PATH if BANLIST_PATH.exists() else SEED_BANLIST
    if not Path(path).exists():
        return []
    return load_rules(path)


def find_hits(text: str, rules: list[Rule]) -> list[dict]:
    """Every rule match with char offsets: ``{start, end, text, rule}``."""
    hits = []
    for rule in rules:
        for match in rule.regex.finditer(text):
            hits.append(
                {
                    "start": match.start(),
                    "end": match.end(),
                    "text": match.group(0),
                    "rule": rule.raw_line,
                }
            )
    hits.sort(key=lambda h: (h["start"], h["end"]))
    return hits


def hits_per_1k(text: str, rules: list[Rule]) -> float:
    """Banlist hits per 1000 words."""
    words = stats.word_count(text)
    if not words:
        return 0.0
    return len(find_hits(text, rules)) * 1000.0 / words
