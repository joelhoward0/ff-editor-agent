from prose_forge import voice

AUTHOR = ("He ran - fast, but the dog was faster and it didn't matter. " * 8 + "\n") * 120
IMITATION = ("He doesn't run. It doesn't matter. The dog is fast.\n" * 6) * 120


def test_dash_styles_counted():
    s = voice._struct('He ran - fast. "Wait -" she said, “no” — then gone.\nDog, gruffly-')
    assert s["spaced_hyphen_share"] == 0.75  # 3 spaced hyphens, 1 em dash
    assert s["curly_quote_share"] == 0.5


def test_controls_learn_direction_and_score():
    prof = voice.build_profile([AUTHOR], controls=[IMITATION])
    assert prof["weights"]["w:doesn't"] > 0 and prof["weights"]["w:but"] < 0
    assert voice.claude_score(IMITATION, prof) > voice.claude_score(AUTHOR, prof)
    drift = {d["feature"] for d in voice.drift_report(IMITATION, prof)}
    assert 'the word "doesn\'t"' in drift
