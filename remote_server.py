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

from prose_forge import banlist, continuity, stats  # noqa: E402
from prose_forge.lint import lint_text  # noqa: E402

SEED = banlist.load_rules(ROOT / "seed_banlist.txt")
MAX_CHARS = 400_000  # ~70k words; bounds CPU per request

mcp = FastMCP(
    "prose-forge",
    instructions=(
        "Style and continuity checks for fiction drafted in Claude. Workflow: "
        "build_style_profile once from the author's own prose and keep the JSON; "
        "before drafting, follow its drafting_guidance; after drafting, call "
        "check_draft with the profile and rewrite ONLY the flagged spans and "
        "warnings, then check again; call check_continuity against prior "
        "chapters before handing a chapter back."
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
def build_style_profile(samples: str) -> dict[str, Any]:
    """Fingerprint the author's own prose. Pass 3,000+ words of THEIR writing
    (not AI drafts), scenes separated by blank lines. Returns a profile JSON to
    save and pass to check_draft, plus drafting_guidance to follow while
    writing."""
    _guard(samples=samples)
    words = stats.word_count(samples)
    if words < 500:
        return {"error": f"only {words} words; need at least 500 (3,000+ recommended)"}
    base = stats.compute_baseline([samples])
    return {
        "profile": {"version": 1, "baseline": base},
        "drafting_guidance": _guidance(base),
        "note": "Save `profile` (e.g. as style-profile.json in Drive) and reuse it.",
    }


@mcp.tool()
def check_draft(text: str, profile: str = "", extra_banlist: str = "") -> dict[str, Any]:
    """Lint a draft for AI tells and drift from the author's style profile.

    profile: the JSON from build_style_profile (string). extra_banlist: the
    author's own banned phrases, one per line (`re:` prefix for regex).
    Returns flagged spans with surrounding sentence, stat warnings, and a
    `clean` verdict. Fix only what is flagged; do not rewrite clean prose."""
    _guard(text=text)
    base = json.loads(profile)["baseline"] if profile.strip() else None
    result = lint_text(text, _rules(extra_banlist), base)
    sentences = stats.sentence_spans(text)
    for span in result["spans"]:
        s, e = next(((a, b) for a, b in sentences if a <= span["start"] < b), (0, 0))
        span["sentence"] = text[s:e].strip()
    result["clean"] = not result["spans"] and not result["warnings"]
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
