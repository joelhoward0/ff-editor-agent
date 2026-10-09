from prose_forge.continuity import check, names

CANON = "Marisol held the door. Teague's dog barked at Marisol and the van."
DRAFT = "Later that night, Marisole found the van empty. She called for Teague and for Odell."


def test_names_skip_unconfirmed_sentence_initial_words():
    assert names("Later she met Odell.") == {"Odell"}
    assert names(CANON) == {"Marisol"}  # Teague only appears sentence-initially
    assert names(CANON, confirmed={"Teague"}) == {"Marisol", "Teague"}


def test_flags_respelling_and_new_name():
    result = check(DRAFT, CANON)
    assert result["near_misses"] == [{"draft": "Marisole", "canon": "Marisol"}]
    assert result["new_names"] == ["Odell"]
