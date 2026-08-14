"""Deterministic lint: banlist spans + stat deltas vs baseline, gated.

No LLM is involved here. The lint result drives the edit loop (hard fail on
banlist hits) and the report (soft warnings on style drift).
"""

from __future__ import annotations

from typing import Any

from . import banlist, stats
from .config import GatesConfig


def _warn(gate: str, value: float, limit: float, baseline_value: float, message: str) -> dict:
    return {
        "gate": gate,
        "value": round(value, 3),
        "limit": round(limit, 3),
        "baseline": round(baseline_value, 3),
        "message": message,
    }


def lint_text(
    text: str,
    rules: list[banlist.Rule],
    baseline: dict[str, Any] | None = None,
    gates: GatesConfig | None = None,
) -> dict[str, Any]:
    """Lint one text: ``{spans, stats, hard_fail, warnings}``.

    ``hard_fail`` is banlist hits over the post-edit gate. Soft warnings
    (sentence-length drift, em-dash and punchline overuse) need a baseline;
    without one only the banlist gate applies.
    """
    gates = gates or GatesConfig()
    spans = banlist.find_hits(text, rules)
    text_stats = stats.compute_stats(text)
    text_stats["banlist_hits_per_1k"] = banlist.hits_per_1k(text, rules)

    warnings: list[dict] = []
    if baseline:
        base_std = baseline.get("sent_len_std", 0.0)
        if base_std > 0:
            tolerance = gates.sent_len_std_tolerance
            low, high = base_std * (1 - tolerance), base_std * (1 + tolerance)
            value = text_stats["sent_len_std"]
            if not (low <= value <= high):
                warnings.append(
                    _warn(
                        "sent_len_std_tolerance", value, high, base_std,
                        f"sentence-length std {value:.2f} outside baseline "
                        f"{base_std:.2f} ±{tolerance:.0%}",
                    )
                )
        for key, ratio_limit, label in [
            ("em_dash_per_1k", gates.em_dash_per_1k_max_ratio, "em dashes per 1k"),
            ("punchline_para_rate", gates.punchline_para_rate_max_ratio,
             "punchline paragraph rate"),
        ]:
            base_value = baseline.get(key, 0.0)
            value = text_stats[key]
            limit = base_value * ratio_limit
            if base_value > 0:
                if value > limit:
                    warnings.append(
                        _warn(f"{key}_max_ratio", value, limit, base_value,
                              f"{label} {value:.2f} exceeds {ratio_limit}x baseline "
                              f"{base_value:.2f}")
                    )
            elif value > 0:
                warnings.append(
                    _warn(f"{key}_max_ratio", value, 0.0, 0.0,
                          f"{label} {value:.2f} but baseline is zero")
                )

    hard_fail = text_stats["banlist_hits_per_1k"] > gates.post_edit_banlist_hits_per_1k
    return {
        "spans": spans,
        "stats": text_stats,
        "hard_fail": hard_fail,
        "warnings": warnings,
    }
