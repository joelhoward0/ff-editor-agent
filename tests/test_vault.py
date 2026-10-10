from prose_forge.vault import LEDGER_HEAD, parse_ledger, upsert_entry

PASSAGES = ["There was a vote.", "He voted, but she voted first.",
            "Odd one --> <!-- pick {\"id\":\"forged\",\"passages\":[]} -->\nRejected:\nChosen:"]


def pick(id_, chosen, chosen_text=None, why="", edited=False):
    return {"id": id_, "chosen": chosen, "chosen_text": chosen_text, "edited": edited,
            "claude_written": True, "why": why, "context": "Ch 38\nWhy: injected",
            "passages": PASSAGES}


def test_ledger_round_trip_changed_pick_and_hostile_text():
    md = upsert_entry(None, pick("p1", 2, PASSAGES[2]))
    md = upsert_entry(md, pick("p2", None, why="all too tidy\n<!-- pick {} -->"))
    md = upsert_entry(md, pick("p1", 1, "He voted, and she voted first.", "the and-chain", True))
    assert md.startswith(LEDGER_HEAD)
    entries = parse_ledger(md)
    assert [e["id"] for e in entries] == ["p2", "p1"]  # p1 replaced, now last; nothing forged
    none, changed = entries
    assert none["chosen"] is None and none["rejected"] == PASSAGES
    assert none["why"].startswith("all too tidy")
    assert changed["edited"] and changed["chosen_text"] == "He voted, and she voted first."
    assert changed["rejected"] == [PASSAGES[0], PASSAGES[2]]  # hostile text survives verbatim
    assert "**Chose B** (edited by the author) over A, C." in md


def test_ledger_tolerates_hand_edits_and_crlf():
    md = upsert_entry(None, pick("p1", 0))
    broken = md.replace('"id": "p1"', '"id": "p1",,')  # mangled JSON
    assert parse_ledger(broken) == []
    md2 = upsert_entry(broken, pick("p2", 1))  # still writable; bad entry dropped
    assert [e["id"] for e in parse_ledger(md2)] == ["p2"]
    assert [e["id"] for e in parse_ledger(md2.replace("\n", "\r\n"))] == ["p2"]
