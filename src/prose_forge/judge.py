"""Draft selection (round-robin pairwise vs a gold excerpt) and opening lines.

The judge never sees which drafter produced which draft, and the judge's model
family differs from the drafters', so self-preference bias has nothing to
anchor on.
"""

from __future__ import annotations

import itertools
import re
from typing import Any

from . import banlist, llm, stats
from .config import Config


def pairwise_select(
    drafts: dict[str, str],
    gold_excerpt: str,
    config: Config,
    rules: list[banlist.Rule],
) -> dict[str, Any]:
    """Round-robin pairwise judging; returns the full matrix and the winner.

    Win count decides; ties break on fewer banlist hits, then higher sentence
    -length std, then name order. An unparseable judge response scores as a
    win for A with a fallback reason (logged in the matrix, never fatal).
    """
    names = sorted(drafts)
    wins = {name: 0 for name in names}
    matrix = []
    for name_a, name_b in itertools.combinations(names, 2):
        prompt = llm.render_template(
            "judge_pairwise",
            gold=gold_excerpt,
            draft_a=drafts[name_a],
            draft_b=drafts[name_b],
        )
        result = llm.chat(
            "judge", [{"role": "user", "content": prompt}],
            sampling_key="judge", config=config,
        )
        parsed = llm.parse_json_response(result.text)
        if parsed and parsed.get("winner") in ("A", "B"):
            verdict = parsed["winner"]
            reason = str(parsed.get("reason", ""))[:300]
        else:
            verdict = "A"
            reason = "unparseable judge response (fallback)"
        winner = name_a if verdict == "A" else name_b
        wins[winner] += 1
        matrix.append({"a": name_a, "b": name_b, "winner": winner, "reason": reason})

    best = max(wins.values())
    tied = [name for name in names if wins[name] == best]
    tiebreak = None
    if len(tied) > 1:
        tiebreak = "banlist_hits"
        tied.sort(
            key=lambda name: (
                len(banlist.find_hits(drafts[name], rules)),
                -stats.sent_len_std(drafts[name]),
                name,
            )
        )
    return {"winner": tied[0], "wins": wins, "matrix": matrix, "tiebreak": tiebreak}


def generate_openers(beats: str, manuscript_tail: str, config: Config) -> list[str]:
    """Ask the judge slot for 3 candidate opening lines."""
    prompt = llm.render_template(
        "openers", beats=beats, manuscript_tail=manuscript_tail
    )
    result = llm.chat(
        "judge", [{"role": "user", "content": prompt}],
        sampling_key="judge", config=config,
    )
    openers = []
    for line in result.text.splitlines():
        match = re.match(r"\s*\d+[.)]\s+(.*\S)", line)
        if match:
            openers.append(match.group(1))
    return openers[:3]


def pick_opener(openers: list[str], rules: list[banlist.Rule]) -> str | None:
    """Fewest banlist hits wins; ties break on shortest. None if no candidates."""
    if not openers:
        return None
    return min(openers, key=lambda o: (len(banlist.find_hits(o, rules)), len(o)))
