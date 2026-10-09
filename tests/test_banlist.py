"""Banlist matching (literal + re:), offsets, n-gram mining, keep/drop merge."""

from __future__ import annotations

from pathlib import Path

from prose_forge import banlist


def _rules(*lines: str) -> list[banlist.Rule]:
    out = []
    for line in lines:
        rule = banlist.parse_rule_line(line)
        if rule and rule.marker != "drop":
            out.append(rule)
    return out


class TestMatching:
    def test_literal_hit_with_offsets(self):
        rules = _rules("somehow")
        hits = banlist.find_hits("He somehow knew.", rules)
        assert hits == [{"start": 3, "end": 10, "text": "somehow", "rule": "somehow"}]

    def test_literal_is_case_insensitive(self):
        rules = _rules("somehow")
        assert len(banlist.find_hits("Somehow it worked.", rules)) == 1

    def test_literal_is_word_bounded(self):
        rules = _rules("somehow")
        assert banlist.find_hits("somehowever strange", rules) == []

    def test_literal_matches_across_line_breaks(self):
        rules = _rules("couldn't help but")
        assert len(banlist.find_hits("She couldn't  help\nbut stare.", rules)) == 1

    def test_literal_alternation_group(self):
        rules = _rules("found (himself|herself|themselves)")
        assert len(banlist.find_hits("He found himself alone.", rules)) == 1
        assert len(banlist.find_hits("She found herself alone.", rules)) == 1
        assert banlist.find_hits("They found the door.", rules) == []

    def test_regex_rule_with_offsets(self):
        rules = _rules(r"re:eyes? (gleamed|glinted|sparkled)")
        text = "Her eyes gleamed in the dark."
        hits = banlist.find_hits(text, rules)
        assert hits[0]["start"] == 4
        assert hits[0]["end"] == 16
        assert hits[0]["text"] == "eyes gleamed"
        assert hits[0]["rule"] == r"re:eyes? (gleamed|glinted|sparkled)"

    def test_seed_banlist_parses_and_matches(self):
        rules = banlist.load_rules(Path(__file__).resolve().parent.parent / "seed_banlist.txt")
        assert len(rules) == 21
        text = "It was a testament to habit that a beat passed before she moved."
        assert len(banlist.find_hits(text, rules)) == 2

    def test_hits_per_1k(self):
        rules = _rules("somehow")
        # 1 hit / 4 words -> 250 per 1k
        assert banlist.hits_per_1k("It somehow held together", rules) == 250.0


class TestParsing:
    def test_drop_marker_excluded_from_active_rules(self, tmp_path):
        path = tmp_path / "banlist.txt"
        path.write_text("somehow  # drop\na beat passed\n", encoding="utf-8")
        rules = banlist.load_rules(path)
        assert [r.raw_line for r in rules] == ["a beat passed"]

    def test_keep_marker_and_info_comments_parse(self):
        kept = banlist.parse_rule_line("my pet phrase  # keep")
        info = banlist.parse_rule_line("mined phrase  # ratio=12.3")
        assert kept.marker == "keep"
        assert info.marker is None
        assert info.raw_line == "mined phrase"


class TestMining:
    # "the fog pressed close" x3, each occurrence followed by different words so
    # only the fog grams (not sentence-boundary grams) reach the count floor.
    CONTROL = (
        "The fog pressed close over the water. "
        "Rain fell on the roof and the road all night. "
        "The fog pressed close till morning. "
        "Wind moved in the eaves for hours on end. "
        "The fog pressed close before dawn."
    )

    def test_overrepresented_control_phrase_is_banned(self):
        corpus_text = "Rain fell on the roof and the road. " * 30
        mined = dict(banlist.mine_candidates(self.CONTROL, corpus_text))
        assert any("fog pressed" in phrase for phrase in mined)

    def test_subphrases_pruned_in_favor_of_longest(self):
        corpus_text = "Rain fell on the roof and the road. " * 30
        mined = dict(banlist.mine_candidates(self.CONTROL, corpus_text))
        assert "the fog pressed close" in mined
        assert "the fog" not in mined
        assert "fog pressed" not in mined

    def test_min_control_count_enforced(self):
        control = "The fog pressed close. Rain fell on the roof and the road all night."
        corpus_text = "Rain fell on the roof and the road. " * 30
        assert banlist.mine_candidates(control, corpus_text) == []

    def test_author_phrase_exempt_via_median(self):
        # "the fog pressed close" is corpus-frequent (above the median phrase
        # frequency): never banned, no matter the control ratio.
        corpus_text = (
            "The fog pressed close on the flats. "
            "The fog pressed close again at noon. "
            "The fog pressed close in the last light. "
            + "Rain fell on the roof and the road. " * 30
        )
        mined = dict(banlist.mine_candidates(self.CONTROL, corpus_text))
        assert not any("fog pressed" in phrase for phrase in mined)


class TestMergeWrite:
    def test_keep_and_drop_lines_survive_rebuild(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "seed_banlist.txt").write_text("somehow\na beat passed\n", encoding="utf-8")
        data = tmp_path / "data"
        data.mkdir()
        (data / "banlist.txt").write_text(
            "my pet phrase  # keep\nsomehow  # drop\n", encoding="utf-8"
        )
        banlist.write_banlist([("the fog pressed", 42.0)])
        content = (data / "banlist.txt").read_text(encoding="utf-8")
        lines = [line for line in content.splitlines() if line and not line.startswith("#")]
        assert "my pet phrase  # keep" in lines
        assert "somehow  # drop" in lines  # marker survives …
        assert "somehow" not in [banlist.parse_rule_line(li).raw_line
                                 for li in lines
                                 if banlist.parse_rule_line(li).marker != "drop"]  # … inactive
        assert "a beat passed" in lines  # seed entry present
        assert any(line.startswith("the fog pressed") and "ratio=42.0" in line for line in lines)

    def test_rebuild_is_stable(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "seed_banlist.txt").write_text("somehow\n", encoding="utf-8")
        banlist.write_banlist([("the fog pressed", 12.0)])
        first = (tmp_path / "data/banlist.txt").read_text(encoding="utf-8")
        banlist.write_banlist([("the fog pressed", 12.0)])
        second = (tmp_path / "data/banlist.txt").read_text(encoding="utf-8")
        assert first == second
