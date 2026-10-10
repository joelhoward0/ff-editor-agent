from prose_forge.vault import LEDGER_HEAD, parse_ledger, upsert_entry


def pick(id_, chosen, chosen_text, why="", edited=False):
    return {"id": id_, "chosen": chosen, "chosen_text": chosen_text, "edited": edited,
            "claude_written": True, "why": why, "context": "Ch 38 hotspot",
            "passages": ["There was a vote.", "He voted, but she voted first.", "A ~~~ odd one."]}


def test_ledger_round_trip_and_changed_pick_replaces():
    md = upsert_entry(None, pick("p1", 2, "A ~~~ odd one."))
    md = upsert_entry(md, pick("p2", None, None, why="all too tidy"))
    md = upsert_entry(md, pick("p1", 1, "He voted, and she voted first.", "the and-chain", True))
    assert md.startswith(LEDGER_HEAD.strip())
    entries = parse_ledger(md)
    assert [e["id"] for e in entries] == ["p2", "p1"]  # p1 replaced, now last
    none, changed = entries
    assert none["chosen"] is None and none["chosen_text"] is None and len(none["rejected"]) == 3
    assert none["why"] == "all too tidy"
    assert changed["edited"] and changed["chosen_text"] == "He voted, and she voted first."
    assert changed["rejected"] == ["There was a vote.", "A ~ ~ ~ odd one."]
    assert "**Chose B** (edited by the author) over A, C." in md
