"""Template rendering + the drafter/planner cleanliness invariant."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from prose_forge import banlist

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "prompt_templates"

NEGATIVE_PHRASING = re.compile(r"avoid|don'?t\s+use|never\s+use", re.IGNORECASE)

EXPECTED_TEMPLATES = {
    "planner", "drafter", "judge_pairwise", "editor", "editor_stern",
    "tagger", "beats_miner", "eval_judge", "openers",
}


def test_all_templates_present():
    found = {p.stem for p in TEMPLATES.glob("*.md")}
    assert found == EXPECTED_TEMPLATES


def test_render_replaces_only_given_placeholders(workspace):
    from prose_forge import llm

    rendered = llm.render_template("tagger", scene="THE SCENE TEXT")
    assert "THE SCENE TEXT" in rendered
    assert "{scene}" not in rendered
    # literal JSON braces in the template survive rendering
    assert '"scene_type"' in rendered


def test_drafter_placeholders_all_render(workspace):
    from prose_forge import llm

    rendered = llm.render_template(
        "drafter",
        manuscript_tail="TAIL", excerpts="EXCERPTS", beats="BEATS",
        style_constraints="CONSTRAINTS",
    )
    for token in ("TAIL", "EXCERPTS", "BEATS", "CONSTRAINTS"):
        assert token in rendered
    assert not re.search(r"\{[a-z_]+\}", rendered)


@pytest.mark.parametrize("name", ["drafter", "planner"])
def test_drafter_and_planner_templates_carry_no_negative_style_rules(name):
    """The invariant: enforcement is post-hoc, never 'avoid X' in the drafter."""
    text = (TEMPLATES / f"{name}.md").read_text(encoding="utf-8")
    assert not NEGATIVE_PHRASING.search(text), f"{name}.md contains negative phrasing"
    rules = banlist.load_rules(REPO_ROOT / "seed_banlist.txt")
    hits = banlist.find_hits(text, rules)
    assert hits == [], f"{name}.md contains banlist terms: {[h['text'] for h in hits]}"
