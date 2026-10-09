"""prose-forge remote MCP server (streamable HTTP, stateless).

The hosted, zero-cost half of prose-forge: every tool is deterministic, no LLM
calls and no storage. Claude is the drafter and the editor; this server
measures. A user's style profile is plain JSON they keep (Drive, a project
file) and pass back in, so the server never holds anyone's prose.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    from mcp.server.fastmcp import FastMCP

from prose_forge import banlist, continuity, stats, voice  # noqa: E402
from prose_forge.lint import lint_text  # noqa: E402

SEED = banlist.load_rules(ROOT / "seed_banlist.txt")
MAX_CHARS = 400_000  # ~70k words; bounds CPU per request

mcp = FastMCP(
    "prose-forge",
    instructions=(
        "Style and continuity checks for fiction drafted in Claude. Workflow: "
        "build_style_profile once from the author's own prose (plus, ideally, "
        "Claude's own attempts at a few of their scenes as controls) and keep the "
        "JSON. Before drafting, load a voice card and a recent sample of the "
        "author's own prose: that matters more than any rule. After drafting, "
        "call check_draft with the profile; fix the flagged spans, then work the "
        "voice drift as habits (never by sprinkling words to hit numbers), "
        "starting at the hotspot; check again. Call check_continuity against "
        "prior chapters before handing a chapter back."
    ),
)


def _guard(**texts: str) -> None:
    for name, text in texts.items():
        if len(text) > MAX_CHARS:
            raise ValueError(f"{name} is {len(text)} chars; limit is {MAX_CHARS}")


def _rules(extra_banlist: str) -> list[banlist.Rule]:
    extra = [r for line in extra_banlist.splitlines() if (r := banlist.parse_rule_line(line))]
    return SEED + [r for r in extra if r.marker != "drop"]


def _guidance(base: dict[str, float]) -> list[str]:
    """Positive drafting rules derived from the author's own numbers."""
    out = [
        f"Average about {base['sent_len_mean']:.0f} words per sentence, with real spread "
        f"(std ≈ {base['sent_len_std']:.0f}): mix fragments with long sentences.",
        f"Typical paragraph ≈ {base['para_len_words_p50']:.0f} words; "
        f"longest run to ≈ {base['para_len_words_p90']:.0f}.",
        f"Em dashes ≈ {base['em_dash_per_1k']:.1f} per 1,000 words; "
        f"semicolons ≈ {base['semicolon_per_1k']:.1f} per 1,000.",
        f"About {base['dialogue_ratio']:.0%} of the text is inside dialogue.",
        f"About {base['punchline_para_rate']:.0%} of paragraphs end on a sentence "
        "under eight words; let the rest end mid-motion.",
    ]
    if base["adverb_tag_rate"] < 0.5:
        out.append("Dialogue tags are plain: 'said'/'asked' with no -ly adverbs.")
    return out


@mcp.tool()
def build_style_profile(samples: str, controls: str = "") -> dict[str, Any]:
    """Fingerprint the author's own prose. samples: 10,000+ words of THEIR
    writing (not AI drafts). controls (strongly recommended): Claude's own
    attempts at 2-3 of those same scenes, written from a plot summary without
    seeing the original; the profile then learns exactly how Claude's imitation
    of this author differs from the real thing, which is what makes
    check_draft's voice score reliable. Returns a profile JSON to save and pass
    to check_draft, plus drafting_guidance."""
    _guard(samples=samples, controls=controls)
    words = stats.word_count(samples)
    if words < 3 * voice.WINDOW:
        return {"error": f"only {words} words; need {3 * voice.WINDOW}+ (10,000+ recommended)"}
    base = stats.compute_baseline([samples])
    try:
        prof = voice.build_profile([samples], controls=[controls] if controls.strip() else None)
    except ValueError as exc:  # e.g. samples with no paragraph breaks
        return {"error": str(exc)}
    out = {
        "profile": {"version": 2, "baseline": base, "voice": prof},
        "drafting_guidance": _guidance(base),
        "note": "Save `profile` (e.g. as style-profile.json in Drive) and reuse it.",
    }
    if "weights" in prof:
        top = sorted(prof["weights"].items(), key=lambda kv: -abs(kv[1]))[:10]
        out["imitation_tells"] = [
            f"{_label(k)}: Claude's imitation uses {'more' if w > 0 else 'less'} than the author"
            for k, w in top
        ]
    else:
        out["note"] += " No controls given: voice score unavailable, drift is approximate."
    return out


def _label(key: str) -> str:
    return f'the word "{key[2:]}"' if key.startswith("w:") else voice.STRUCT[key]


def _voice_check(text: str, prof: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"drift": voice.drift_report(text, prof)}
    score = voice.claude_score(text, prof)
    if score is None:
        return out
    out["score"] = round(score, 2)
    out["verdict"] = voice.verdict(score, prof)
    wins = voice.windows(text)
    if len(wins) > 1:
        scored = [(voice.claude_score(w, prof), w) for w in wins]
        worst_score, worst = max(scored, key=lambda sw: sw[0])
        out["hotspot"] = {"score": round(worst_score, 2), "starts": worst[:160]}
    return out


@mcp.tool()
def check_draft(text: str, profile: str = "", extra_banlist: str = "") -> dict[str, Any]:
    """Lint a draft for AI tells and drift from the author's style profile.

    profile: the JSON from build_style_profile (string). extra_banlist: the
    author's own banned phrases, one per line (`re:` prefix for regex).
    Returns flagged spans with surrounding sentence, stat warnings, and a
    `clean` verdict. Fix only what is flagged; do not rewrite clean prose."""
    _guard(text=text)
    prof = json.loads(profile) if profile.strip() else {}
    result = lint_text(text, _rules(extra_banlist), prof.get("baseline"))
    sentences = stats.sentence_spans(text)
    for span in result["spans"]:
        s, e = next(((a, b) for a, b in sentences if a <= span["start"] < b), (0, 0))
        span["sentence"] = text[s:e].strip()
    if "voice" in prof and stats.word_count(text) >= voice.WINDOW // 2:
        result["voice"] = _voice_check(text, prof["voice"])
    result["clean"] = (
        not result["spans"] and not result["warnings"]
        and result.get("voice", {}).get("verdict", "reads like the author")
        == "reads like the author"
    )
    result["word_count"] = stats.word_count(text)
    return result


@mcp.tool()
def check_continuity(draft: str, canon: str) -> dict[str, Any]:
    """Compare proper names in a new draft against canon (prior chapters,
    series bible). near_misses are probable misspellings of established names;
    new_names are first appearances to confirm are intentional."""
    _guard(draft=draft, canon=canon)
    return continuity.check(draft, canon)


app = mcp.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
