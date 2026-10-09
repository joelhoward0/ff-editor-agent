"""Deterministic continuity check: proper-name drift between a draft and canon.

Models silently respell names ("Marisol" -> "Marisole") and invent minor
characters; both are cheap to catch without an LLM. A "name" is any
capitalized word seen mid-sentence at least once (sentence-initial words are
ambiguous, so they only count once confirmed elsewhere).
"""

from __future__ import annotations

import difflib
import re

from .stats import split_sentences

_CAP = re.compile(r"\b[A-Z][a-z’'-]+\b")
_POSSESSIVE = re.compile(r"[’']s$")


def names(text: str, confirmed: set[str] | frozenset = frozenset()) -> set[str]:
    """Capitalized words seen mid-sentence, plus sentence-initial ones in ``confirmed``."""
    found: set[str] = set()
    for sentence in split_sentences(text):
        first_letter = re.search(r"[A-Za-z]", sentence)
        for match in _CAP.finditer(sentence):
            word = _POSSESSIVE.sub("", match.group())
            initial = first_letter is not None and match.start() == first_letter.start()
            if not initial or word in confirmed:
                found.add(word)
    return found


def check(draft: str, canon: str, cutoff: float = 0.8) -> dict[str, list]:
    """``{near_misses: [{draft, canon}], new_names: [...]}`` for draft vs canon."""
    confirmed = names(canon) | names(draft)
    canon_names = names(canon, confirmed)
    near, new = [], []
    solid = names(draft, confirmed)
    # sentence-initial words can't be trusted as new names, but can still be typos
    for name in sorted(names(draft, _cap_words(draft)) - canon_names):
        match = difflib.get_close_matches(name, canon_names, n=1, cutoff=cutoff)
        if match:
            near.append({"draft": name, "canon": match[0]})
        elif name in solid:
            new.append(name)
    return {"near_misses": near, "new_names": new}


def _cap_words(text: str) -> set[str]:
    return {_POSSESSIVE.sub("", w) for w in _CAP.findall(text)}
