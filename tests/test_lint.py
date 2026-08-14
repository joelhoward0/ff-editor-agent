"""Deterministic lint: spans, gates, hard fail, soft warnings vs baseline."""

from __future__ import annotations

from prose_forge import banlist, lint
from prose_forge.config import GatesConfig


def _rules(*lines: str) -> list[banlist.Rule]:
    return [banlist.parse_rule_line(line) for line in lines]


CLEAN = (
    "Maren walked the levee before first light and watched the tide push its slow "
    "silver sheet across the mud. Nothing moved. She counted the pilings twice and "
    "then went back inside to wait for the kettle."
)


def test_clean_text_passes():
    result = lint.lint_text(CLEAN, _rules("somehow"), gates=GatesConfig())
    assert result["spans"] == []
    assert result["hard_fail"] is False
    assert result["warnings"] == []


def test_banlist_hit_is_hard_fail_at_zero_gate():
    result = lint.lint_text("It somehow held.", _rules("somehow"), gates=GatesConfig())
    assert result["hard_fail"] is True
    assert result["spans"][0]["text"] == "somehow"
    assert result["stats"]["banlist_hits_per_1k"] > 0


def test_nonzero_gate_tolerates_low_rates():
    gates = GatesConfig(post_edit_banlist_hits_per_1k=500)
    result = lint.lint_text("It somehow held together after all.", _rules("somehow"), gates=gates)
    assert result["hard_fail"] is False


def test_sent_len_std_warning_against_baseline():
    baseline = {"sent_len_std": 10.0, "em_dash_per_1k": 5.0, "punchline_para_rate": 0.2}
    # CLEAN has few sentences; its std is far below 8.0 (=10 - 20%)
    result = lint.lint_text(CLEAN, [], baseline=baseline, gates=GatesConfig())
    gates_hit = [w["gate"] for w in result["warnings"]]
    assert "sent_len_std_tolerance" in gates_hit


def test_em_dash_ratio_warning():
    baseline = {"sent_len_std": 0.0, "em_dash_per_1k": 1.0, "punchline_para_rate": 0.0}
    dashy = "She waited — and waited — and waited — and waited for the ferry to come in."
    result = lint.lint_text(dashy, [], baseline=baseline, gates=GatesConfig())
    assert any(w["gate"] == "em_dash_per_1k_max_ratio" for w in result["warnings"])


def test_no_baseline_means_no_soft_warnings():
    dashy = "She waited — and waited — and waited for the ferry."
    result = lint.lint_text(dashy, [], baseline=None, gates=GatesConfig())
    assert result["warnings"] == []
