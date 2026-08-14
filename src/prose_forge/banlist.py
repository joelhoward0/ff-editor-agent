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

import numpy as np

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


def _tokens(text: str) -> list[str]:
    """Lowercased word tokens with punctuation stripped (apostrophes kept)."""
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


def ngram_counts(text: str, n_min: int = 2, n_max: int = 5) -> dict[str, int]:
    """Counts of every 2–5-gram (space-joined) in the text."""
    toks = _tokens(text)
    counts: dict[str, int] = {}
    for n in range(n_min, n_max + 1):
        for i in range(len(toks) - n + 1):
            gram = " ".join(toks[i : i + n])
            counts[gram] = counts.get(gram, 0) + 1
    return counts


def mine_candidates(
    control_text: str,
    corpus_text: str,
    *,
    min_control_count: int = 3,
    min_ratio: float = 10.0,
) -> list[tuple[str, float]]:
    """Phrases overrepresented in control (naive AI) prose vs the corpus.

    A phrase is a candidate when control-per-1k ÷ corpus-per-1k >= ``min_ratio``
    (a corpus frequency of zero counts as 0.1 per 1k) and it occurs at least
    ``min_control_count`` times in control. Phrases whose corpus frequency
    exceeds the median corpus phrase frequency are exempt — the author's own
    legit phrasing never gets banned. Returns (phrase, ratio) sorted by ratio
    descending; longer phrases win over their sub-phrases at equal counts.
    """
    control_counts = ngram_counts(control_text)
    corpus_counts = ngram_counts(corpus_text)
    control_words = max(len(_tokens(control_text)), 1)
    corpus_words = max(len(_tokens(corpus_text)), 1)

    corpus_freqs = [c * 1000.0 / corpus_words for c in corpus_counts.values()]
    median_corpus_freq = float(np.median(corpus_freqs)) if corpus_freqs else 0.0

    candidates: dict[str, float] = {}
    for gram, count in control_counts.items():
        if count < min_control_count:
            continue
        corpus_freq = corpus_counts.get(gram, 0) * 1000.0 / corpus_words
        if corpus_freq > median_corpus_freq:
            continue  # author's legit phrase — auto-exempt
        control_freq = count * 1000.0 / control_words
        ratio = control_freq / max(corpus_freq, 0.1)
        if ratio >= min_ratio:
            candidates[gram] = ratio

    # prefer the longest phrase when a sub-phrase exists only inside it
    pruned: dict[str, float] = {}
    for gram in sorted(candidates, key=lambda g: -len(g)):
        if any(gram in longer and gram != longer for longer in pruned):
            if control_counts[gram] <= max(
                control_counts[longer] for longer in pruned if gram in longer
            ):
                continue
        pruned[gram] = candidates[gram]
    return sorted(pruned.items(), key=lambda kv: -kv[1])


def _parse_existing(path: Path) -> tuple[list[str], list[str], set[str]]:
    """Split an existing banlist into (kept lines, drop lines, drop patterns)."""
    kept: list[str] = []
    dropped: list[str] = []
    drop_patterns: set[str] = set()
    if not path.exists():
        return kept, dropped, drop_patterns
    for line in path.read_text(encoding="utf-8").splitlines():
        rule = parse_rule_line(line)
        if rule is None:
            continue
        if rule.marker == "keep":
            kept.append(line.rstrip())
        elif rule.marker == "drop":
            dropped.append(line.rstrip())
            drop_patterns.add(rule.raw_line)
    return kept, dropped, drop_patterns


def write_banlist(mined: list[tuple[str, float]], path: Path = BANLIST_PATH) -> dict:
    """Merge mined candidates with the seed list and hand-edits, write the file.

    ``# keep`` lines always survive; ``# drop`` lines survive as markers but
    their patterns are never re-added as active entries. Unmarked lines are
    regenerated. Mined entries carry an informational ``# ratio=`` trailer.
    """
    kept, dropped, drop_patterns = _parse_existing(path)
    seen = {parse_rule_line(line).raw_line for line in kept}  # type: ignore[union-attr]
    seen |= drop_patterns

    lines = [
        "# prose-forge banlist — literal by default, re:<pattern> for regex.",
        "# Hand-edit freely. Mark a line '# keep' or '# drop' to survive rebuilds.",
        *kept,
    ]
    for line in SEED_BANLIST.read_text(encoding="utf-8").splitlines():
        rule = parse_rule_line(line)
        if rule and rule.raw_line not in seen:
            lines.append(rule.raw_line)
            seen.add(rule.raw_line)
    added = 0
    for phrase, ratio in mined:
        if phrase not in seen:
            lines.append(f"{phrase}  # ratio={ratio:.1f}")
            seen.add(phrase)
            added += 1
    lines.extend(dropped)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"kept": len(kept), "dropped": len(dropped), "mined_added": added,
            "total_active": len(load_rules(path))}


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


CONTROL_DIR = Path("data/control")
PROMPTS_DIR = Path("prompts")


def generate_control_set(config, prompts_sample: int = 30) -> list[Path]:
    """Naive bare-prompt drafts from both drafter slots → ``data/control/``.

    Existing control files are kept (delete the directory to regenerate).
    """
    from . import llm
    from .config import MissingAssetError

    prompts = sorted(PROMPTS_DIR.glob("*.md"))[:prompts_sample]
    if not prompts:
        raise MissingAssetError(
            "no chapter prompts in prompts/ — the control set needs them",
            run_first="forge init",
        )
    out: list[Path] = []
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    for prompt in prompts:
        for slot in ("drafter_a", "drafter_b"):
            dest = CONTROL_DIR / f"{prompt.stem}-{slot}.md"
            if not dest.exists():
                result = llm.chat(
                    slot,
                    [{"role": "user", "content": prompt.read_text(encoding="utf-8")}],
                    sampling_key="draft",
                    config=config,
                )
                dest.write_text(result.text, encoding="utf-8")
            out.append(dest)
    return out


def build_banlist(config, prompts_sample: int = 30) -> dict:
    """Full ``forge banlist build``: control set → mining → merged write."""
    from .corpus import load_chunks

    files = generate_control_set(config, prompts_sample)
    control_text = "\n\n".join(f.read_text(encoding="utf-8") for f in files)
    corpus_text = "\n\n".join(c["text"] for c in load_chunks())
    mined = mine_candidates(control_text, corpus_text)
    summary = write_banlist(mined)
    summary["control_files"] = len(files)
    summary["mined_candidates"] = len(mined)
    return summary
