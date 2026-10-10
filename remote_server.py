"""prose-forge remote MCP server (streamable HTTP, stateless).

Two endpoints from one tool set:

- /mcp: open, stores nothing. The style profile is JSON the author keeps and
  passes in; Claude records picks in their ledger.
- /drive/mcp: "Sign in with Google" (see prose_forge.signin). The author's
  voice card, profile and ledger live in a prose-forge folder in THEIR Drive;
  the picker saves picks there itself and the profile rebuilds as picks accrue.
  Still no server-side storage: the Google grant rides inside sealed tokens.
  Enabled only when GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are set.

Every check is deterministic: no LLM calls. Claude drafts; this server measures.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402
from mcp.server.apps import Apps  # noqa: E402
from mcp.server.auth.middleware.auth_context import get_access_token  # noqa: E402
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions  # noqa: E402
from mcp.server.mcpserver import MCPServer as FastMCP  # noqa: E402
from starlette.responses import PlainTextResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402

from prose_forge import banlist, continuity, signin, stats, vault, voice  # noqa: E402
from prose_forge.lint import lint_text  # noqa: E402

SEED = banlist.load_rules(ROOT / "seed_banlist.txt")
MAX_CHARS = 400_000  # ~70k words; bounds CPU per request
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://prose-forge-tau.vercel.app").rstrip("/")
REBUILD_EVERY = 10   # new ledger entries before the profile retrains itself
RECENT_PICKS = 5     # ledger entries load_voice hands Claude before drafting
HTTP_TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests swap in a fake Google

INSTRUCTIONS = (
    "Style and continuity checks for fiction drafted in Claude. Workflow: "
    "build_style_profile once from the author's own prose (plus, ideally, "
    "Claude's own attempts at a few of their scenes as controls). Before drafting, "
    "load a voice card and a recent sample of the author's own prose: that matters "
    "more than any rule. After drafting, call check_draft; fix the flagged spans, "
    "then work the voice drift as habits (never by sprinkling words to hit numbers), "
    "starting at the hotspot; check again. Call check_continuity against prior "
    "chapters before handing a chapter back. To learn the author's taste, show 2-4 "
    "versions of a passage with compare_passages; their pick comes back as a chat "
    "message starting [prose-forge pick]. To have the author mark up a draft scene "
    "by scene, use triage_scenes; their notes come back starting [prose-forge triage]."
)
SIGNED_IN_NOTE = (
    " This connection is signed in: the author's voice card, style profile and voice "
    "ledger live in a prose-forge folder in their own Google Drive. Call load_voice "
    "before drafting. check_draft uses the saved profile automatically. Picks the "
    "picker reports as saved are already in the ledger; do not record them again."
)


# -- pure helpers shared by both endpoints --

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


def _label(key: str) -> str:
    return f'the word "{key[2:]}"' if key.startswith("w:") else voice.STRUCT[key]


def _profile(samples: list[str], controls: list[str]) -> dict[str, Any]:
    """{profile, drafting_guidance, imitation_tells?} or {error}."""
    joined = "\n\n".join(samples)
    words = stats.word_count(joined)
    if words < 3 * voice.WINDOW:
        return {"error": f"only {words} words; need {3 * voice.WINDOW}+ (10,000+ recommended)"}
    base = stats.compute_baseline([joined])
    try:
        prof = voice.build_profile(samples, controls=[c for c in controls if c.strip()] or None)
    except ValueError as exc:  # e.g. samples with no paragraph breaks
        return {"error": str(exc)}
    out: dict[str, Any] = {"profile": {"version": 2, "baseline": base, "voice": prof},
                           "drafting_guidance": _guidance(base)}
    if "weights" in prof:
        top = sorted(prof["weights"].items(), key=lambda kv: -abs(kv[1]))[:10]
        out["imitation_tells"] = [
            f"{_label(k)}: Claude's imitation uses {'more' if w > 0 else 'less'} than the author"
            for k, w in top
        ]
    else:
        out["note"] = "No controls given: voice score unavailable, drift is approximate."
    return out


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


def _check(text: str, prof: dict[str, Any], extra_banlist: str) -> dict[str, Any]:
    _guard(text=text)
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


# -- Drive access for the signed-in endpoint --

@contextlib.asynccontextmanager
async def _vault():
    tok = get_access_token()
    if tok is None or not (tok.claims or {}).get("google_refresh"):
        raise PermissionError("Not signed in to prose-forge.")
    async with httpx.AsyncClient(transport=HTTP_TRANSPORT, timeout=20) as http:
        access = await signin.google_access_token(tok.claims["google_refresh"], http)
        yield vault.Vault(access, http)


async def _maybe_rebuild(v: vault.Vault, entries: list[dict[str, Any]]) -> str | None:
    """Retrain the profile once REBUILD_EVERY new picks have accrued: rejected
    Claude versions become imitation examples, author-edited text becomes samples."""
    raw = await v.read("style-profile")
    built_at = (json.loads(raw).get("ledger_entries", 0) if raw else 0)
    if len(entries) - built_at < REBUILD_EVERY:
        return None
    samples, controls = await v.read("samples"), await v.read("controls")
    if not samples:
        return ("Ledger has new picks, but the original writing samples aren't saved in "
                "Drive yet; run build_style_profile once to enable automatic retraining.")
    extra_samples = [e["chosen_text"] for e in entries if e.get("edited") and e["chosen_text"]]
    extra_controls = [r for e in entries if e.get("claude_written", True) for r in e["rejected"]]
    built = _profile([samples, *extra_samples], [controls or "", *extra_controls])
    if "error" in built:
        return f"Retraining skipped: {built['error']}"
    await v.write("style-profile", json.dumps(
        {**built["profile"], "ledger_entries": len(entries)}, indent=1))
    return f"Profile retrained from {len(entries)} picks."


# -- server factory --

def build_server(signed_in: bool, auth: dict[str, Any] | None = None) -> FastMCP:
    apps = Apps()

    def ui(name: str, title: str, description: str) -> str:
        html = (ROOT / f"{name}_app.html").read_text(encoding="utf-8")
        uri = f"ui://prose-forge/{name}-{hashlib.sha256(html.encode()).hexdigest()[:12]}.html"
        apps.add_html_resource(uri, html, title=title, description=description, prefers_border=True)
        return uri

    compare_ui = ui("compare", "Pick a version", "Side-by-side prose picker")
    triage_ui = ui("triage", "Triage a chapter", "Scene-by-scene keep/fix/cut notes")

    # Content-hashed URIs: hosts may cache UI resources by URI, so every change
    # gets a new address. The legacy flat key mirrors registerAppTool.
    @apps.tool(resource_uri=compare_ui, meta={"ui/resourceUri": compare_ui})
    def compare_passages(
        passages: list[str], labels: list[str] | None = None, context: str = ""
    ) -> dict[str, Any]:
        """Show the author 2-4 versions of the same passage side by side, inline,
        to pick one or edit one. Use it to resolve a voice hotspot (the original
        plus 1-2 rewrites) or any 'which sounds like me?' question. Shuffle the
        order and leave labels empty so the pick is blind; labels are short notes
        shown per version, for when the author asks to know which is which.
        context: one line on what is being compared. The choice arrives as a user
        message starting "[prose-forge pick]". Wait for it; don't pick for them."""
        if not 2 <= len(passages) <= 4:
            raise ValueError("pass 2 to 4 passages")
        _guard(**{f"passage_{i}": p for i, p in enumerate(passages)})
        listing = "\n\n".join(f"{chr(65 + i)}: {p}" for i, p in enumerate(passages))
        return {
            "passages": passages, "labels": labels or [], "context": context,
            "if_no_picker": "Show these to the author as A/B/... and ask which reads "
            "most like them, or to edit one:\n\n" + listing,
        }

    @apps.tool(resource_uri=triage_ui, meta={"ui/resourceUri": triage_ui})
    def triage_scenes(
        scenes: list[str], titles: list[str] | None = None, context: str = ""
    ) -> dict[str, Any]:
        """Show the author a chapter split into scenes, inline, to mark each one
        Keep / Fix story / Fix voice / Cut, add notes, and quote passages. Use it
        when the author wants to review or mark up a draft before revising. Split
        at the chapter's own scene breaks (e.g. a line of underscores or a lone
        dash) and pass every scene's full text in order, unchanged, as plain prose
        (drop the chapter heading and markdown markup); titles: optional 2-5 word
        labels per scene. Their notes arrive as one user message starting
        "[prose-forge triage]" (a later one marked "Updated" replaces it). Wait for
        it, then revise in this order: story fixes, then cuts, then voice passes
        only on scenes being kept; leave Keep scenes untouched."""
        if not 1 <= len(scenes) <= 40:
            raise ValueError("pass 1 to 40 scenes")
        _guard(**{f"scene_{i}": t for i, t in enumerate(scenes)})
        listing = "\n".join(
            f"Scene {i + 1}{': ' + titles[i] if titles and i < len(titles) and titles[i] else ''}"
            f" — starts: {t.strip()[:80]}" for i, t in enumerate(scenes)
        )
        return {
            "scenes": scenes, "titles": titles or [], "context": context,
            "if_no_view": "List the scenes for the author and ask them to mark each "
            "Keep / Fix story / Fix voice / Cut, with notes:\n" + listing,
        }

    kwargs: dict[str, Any] = {}
    if auth:
        kwargs = {"auth_server_provider": auth["provider"], "auth": auth["settings"]}
    mcp = FastMCP("prose-forge", extensions=[apps],
                  instructions=INSTRUCTIONS + (SIGNED_IN_NOTE if signed_in else ""), **kwargs)

    @mcp.tool()
    def check_continuity(draft: str, canon: str) -> dict[str, Any]:
        """Compare proper names in a new draft against canon (prior chapters,
        series bible). near_misses are probable misspellings of established names;
        new_names are first appearances to confirm are intentional."""
        _guard(draft=draft, canon=canon)
        return continuity.check(draft, canon)

    if not signed_in:
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
            out = _profile([samples], [controls])
            if "profile" in out:
                out["note"] = (out.get("note", "") + " Save `profile` (e.g. as "
                               "style-profile.json in Drive) and reuse it.").strip()
            return out

        @mcp.tool()
        def check_draft(text: str, profile: str = "", extra_banlist: str = "") -> dict[str, Any]:
            """Lint a draft for AI tells and drift from the author's style profile.

            profile: the JSON from build_style_profile (string). extra_banlist: the
            author's own banned phrases, one per line (`re:` prefix for regex).
            Returns flagged spans with surrounding sentence, stat warnings, and a
            `clean` verdict. Fix only what is flagged; do not rewrite clean prose."""
            return _check(text, json.loads(profile) if profile.strip() else {}, extra_banlist)

        return mcp

    @mcp.tool()
    async def build_style_profile(samples: str, controls: str = "") -> dict[str, Any]:
        """Fingerprint the author's own prose and save it to their Drive.
        samples: 10,000+ words of THEIR writing (not AI drafts). controls
        (strongly recommended): Claude's own attempts at 2-3 of those same scenes,
        written from a plot summary without seeing the original. Saves the
        profile, samples and controls in the author's prose-forge Drive folder so
        check_draft uses it automatically and it can retrain from their picks."""
        _guard(samples=samples, controls=controls)
        out = _profile([samples], [controls])
        if "profile" in out:
            async with _vault() as v:
                entries = vault.parse_ledger(await v.read("ledger"))
                await v.write("samples", samples)
                await v.write("controls", controls)
                await v.write("style-profile", json.dumps(
                    {**out["profile"], "ledger_entries": len(entries)}, indent=1))
            out.pop("profile")
            out["note"] = (out.get("note", "") + " Saved to the author's prose-forge "
                           "folder in Google Drive.").strip()
        return out

    @mcp.tool()
    async def check_draft(text: str, extra_banlist: str = "") -> dict[str, Any]:
        """Lint a draft for AI tells and drift from the author's saved style
        profile. extra_banlist: the author's own banned phrases, one per line
        (`re:` prefix for regex). Returns flagged spans with surrounding sentence,
        stat warnings, and a `clean` verdict. Fix only what is flagged."""
        async with _vault() as v:
            raw = await v.read("style-profile")
        result = _check(text, json.loads(raw) if raw else {}, extra_banlist)
        if not raw:
            result["note"] = "No saved style profile yet: run build_style_profile first."
        return result

    @mcp.tool()
    async def load_voice() -> dict[str, Any]:
        """Load the author's voice before drafting or revising: their voice card,
        whether a style profile is saved, and their most recent picks (chosen text
        and why), which outrank the voice card. Retrains the profile when enough
        new picks have accrued."""
        async with _vault() as v:
            card = await v.read("voice-card")
            has_profile = await v.read("style-profile") is not None
            entries = vault.parse_ledger(await v.read("ledger"))
            retrained = await _maybe_rebuild(v, entries) if has_profile else None
        recent = [{"when_context": e["heading"], "why": e["why"],
                   "chose": e["chosen_text"] or "(none of the versions)"}
                  for e in entries[-RECENT_PICKS:]]
        out: dict[str, Any] = {"voice_card": card, "has_profile": has_profile,
                               "picks_total": len(entries), "recent_picks": recent}
        if retrained:
            out["retrained"] = retrained
        if card is None:
            out["note"] = ("No voice card saved yet. If the author has one elsewhere, "
                           "save it with save_voice_file; otherwise write one (see the skill).")
        return out

    @mcp.tool()
    async def save_voice_file(kind: Literal["voice-card", "style-profile"],
                              content: str) -> dict[str, Any]:
        """Save the author's voice card (markdown) or an existing style profile
        (the JSON from build_style_profile) to their prose-forge Drive folder,
        replacing the previous one. Use it to import files they already have."""
        _guard(content=content)
        if kind == "style-profile":
            prof = json.loads(content)
            if "voice" not in prof or "baseline" not in prof:
                raise ValueError("not a prose-forge style profile (needs 'voice' and 'baseline')")
            content = json.dumps(prof, indent=1)
        async with _vault() as v:
            await v.write(kind, content)
        return {"saved": vault.NAMES[kind]}

    @mcp.tool(meta={"ui": {"visibility": ["model", "app"]}})
    async def record_pick(
        pick_id: str, passages: list[str], chosen: int | None, chosen_text: str | None = None,
        why: str = "", context: str = "", claude_written: bool = True,
    ) -> dict[str, Any]:
        """Record a picker choice in the author's voice ledger in Drive. The
        picker calls this itself; call it only if a [prose-forge pick] message
        says it wasn't saved. chosen: index into passages, or null for "none of
        these". chosen_text: the final text if the author edited it.
        claude_written: false if the passages are the author's own prose.
        Re-sending the same pick_id replaces that entry (a changed pick)."""
        if not 1 <= len(passages) <= 4 or (chosen is not None and not 0 <= chosen < len(passages)):
            raise ValueError("1-4 passages and a valid chosen index (or null)")
        _guard(why=why, **{f"passage_{i}": p for i, p in enumerate(passages)},
               chosen_text=chosen_text or "")
        final = None if chosen is None else (chosen_text or passages[chosen])
        entry = {"id": pick_id[:64], "passages": passages, "chosen": chosen,
                 "chosen_text": final, "edited": chosen is not None and final != passages[chosen],
                 "why": why, "context": context[:200], "claude_written": claude_written}
        async with _vault() as v:
            await v.write("ledger", vault.upsert_entry(await v.read("ledger"), entry))
        return {"saved": True}

    return mcp


def _get405(path: str) -> Route:
    # Stateless: no server-initiated stream, so GET gets a prompt 405 (allowed by
    # the spec) instead of an idle SSE stream that stalled connector setup checks.
    return Route(path, lambda _req: PlainTextResponse("POST only", 405, headers={"Allow": "POST"}),
                 methods=["GET"])


def build_app():
    opts = {"stateless_http": True, "json_response": True, "host": "0.0.0.0"}
    open_app = build_server(False).streamable_http_app(**opts)
    open_app.router.routes.insert(0, _get405("/mcp"))
    if not signin.configured():
        return open_app

    provider = signin.GoogleProvider(
        PUBLIC_URL, PUBLIC_URL + "/drive/mcp",
        client_id=os.environ["GOOGLE_CLIENT_ID"], client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        allowed_emails=set(os.environ.get("ALLOWED_EMAILS", "").split(",")),
        transport=HTTP_TRANSPORT,
    )
    settings = AuthSettings(
        issuer_url=PUBLIC_URL, resource_server_url=PUBLIC_URL + "/drive/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )
    drive_mcp = build_server(True, {"provider": provider, "settings": settings})
    drive_mcp.custom_route(signin.CALLBACK_PATH, methods=["GET"])(provider.google_callback)
    drive_app = drive_mcp.streamable_http_app(streamable_http_path="/drive/mcp", **opts)
    # One app: the drive app's auth middleware only reads bearer tokens, so the
    # open /mcp route can live in it unchanged; both session managers run.
    drive_app.router.routes[:0] = [
        _get405("/drive/mcp"),
        *[r for r in open_app.router.routes if getattr(r, "path", None) == "/mcp"],
    ]

    open_life, drive_life = open_app.router.lifespan_context, drive_app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with open_life(open_app), drive_life(drive_app):
            yield

    drive_app.router.lifespan_context = lifespan
    return drive_app


app = build_app()
