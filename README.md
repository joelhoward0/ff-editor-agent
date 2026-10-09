# prose-forge

A dead-simple local agentic pipeline that drafts fiction chapters **in your
voice** using models on OpenRouter, then removes AI-isms with deterministic
linting and surgical LLM editing. No agent frameworks — plain Python, plain
files.

Three ways to drive it: a CLI (`forge`), a file-based run directory you can
inspect and diff, and an MCP stdio server so any MCP client (Claude Code,
Claude Desktop, other agents) can control it.

## The pipeline

```
prompts/ch07.md                                   your chapter brief
      │
      ▼
   ┌──────┐   bible/ + manuscript tail
   │ plan │──────────────────────────────►  beats.md   (pausable for review)
   └──────┘
      │
      ▼
   ┌───────┐  manuscript tail + retrieved corpus     drafts/a1.md  a2.md
   │ draft │  excerpts (TF-IDF, POV/scene-matched)   drafts/b1.md  b2.md
   └───────┘  × 2 drafters × 2 samples
      │
      ▼
   ┌────────┐  round-robin pairwise judging
   │ select │  vs a gold corpus excerpt      ──────►  selection.json
   └────────┘  (judge family ≠ drafter families)
      │
      ▼
   ┌──────┐   banlist spans + stat deltas
   │ lint │   vs your baseline — deterministic ───►  lint.json
   └──────┘
      │
      ▼
   ┌──────┐   surgical rewrite of flagged spans
   │ edit │   diff-assert rejects out-of-span  ───►  edit1.md, edit2.md
   └──────┘   changes; loops while hard-failing
      │
      ▼
   ┌────────┐
   │ report │ ────────────────────────────────────►  final.md, report.md
   └────────┘
      │
      ▼
 forge accept  ───►  manuscript/book.md   (next run's context tail)
```

Every stage writes plain files to `runs/<run_id>/` and updates `status.json`,
so everything is inspectable, resumable (`--resume`), and diffable.

## Why post-hoc enforcement

Telling a drafter "avoid *couldn't help but*" plants the phrase in its context
window and measurably distorts the prose around it. prose-forge never puts
banlist content or negative instructions in the drafter's context — a test
enforces this. Instead, drafts are written free, then a deterministic linter
finds the tells (mined from a control set of what these exact models do with
your prompts *without* your corpus), and an editor model from a different
family rewrites only the flagged spans. A sentence-level diff-assert rejects
any edit outside the flags, so the editor can't quietly "improve" your voice.

## Quickstart

```bash
git clone <this repo> && cd prose-forge
uv sync
cp .env.example .env            # add your OPENROUTER_API_KEY
# check model slugs in config.yaml against openrouter.ai/models

# 1. your prose goes in (never committed — see .gitignore)
cp ~/writing/*.md corpus/

# 2. build the assets
uv run forge ingest             # chunk + tag scenes  → data/chunks.jsonl
uv run forge baseline           # your style fingerprint → data/baseline.json
uv run forge banlist build      # mine AI tells       → data/banlist.txt
uv run forge triples build      # editor few-shots    → data/triples/

# 3. write a chapter
uv run forge run prompts/example.md
uv run forge accept <run_id>    # append final.md to manuscript/book.md

# 4. keep score
uv run forge eval run           # can outside judges tell it from you?
```

Everything runs offline with `FORGE_MOCK=1` (canned responses, no key), which
is how the test suite and CI work: `FORGE_MOCK=1 uv run pytest`.

### Useful commands

| command | what |
|---|---|
| `forge status` | models, asset freshness, active runs, last eval |
| `forge run <prompt> --pause-after-beats` | stop after the beat sheet for review |
| `forge run --resume <run_id>` | continue from the last completed stage |
| `forge run <prompt> --opening line.txt` | use your opening line (`opening_line_mode: human`) |
| `forge runs list` / `forge runs show <id>` | inspect runs |
| `forge lint <file\|->` | lint any text against banlist + gates |
| `forge edit <file\|->` | ad-hoc lint + surgical edit (edited text on stdout) |

Exit codes: `0` ok · `1` hard-gate failure · `2` missing config/asset (the
message names the command to run first).

## Use it inside Claude (hosted, no setup)

The deterministic half of prose-forge runs as a public, stateless MCP server:
`https://prose-forge-tau.vercel.app/mcp`. Claude drafts and edits; the server
measures. It stores nothing and calls no LLM, so it costs nothing to run.

| tool | what |
|---|---|
| `build_style_profile` | fingerprint 3k+ words of *your* prose → profile JSON + positive drafting guidance |
| `check_draft` | AI-tell spans (with their sentence) + style drift vs your profile; `clean` verdict |
| `check_continuity` | names in a draft vs canon: probable misspellings and first appearances |

**Claude Code:** `/plugin marketplace add joelhoward0/ff-editor-agent`, then
`/plugin install prose-forge@prose-forge`. You get the MCP server, the
`write-in-my-voice` skill, and a hook: in any project with a
`style-profile.json` at its root, every `.md`/`.txt` Claude writes is checked
and the findings go straight back to Claude to fix (optional `banlist.txt`
beside it adds your own phrases).

**claude.ai / desktop:** add the URL above as a custom connector, and upload
`plugin/skills/write-in-my-voice` as a skill. With Google Drive connected, the
skill builds your profile from your manuscript docs, saves it to Drive, and
reads earlier chapters for continuity.

## Driving it over MCP

The repo ships a project-scoped `.mcp.json`, so Claude Code auto-detects the
server when you open the repo (first use asks you to approve it). Manual
registration:

```bash
claude mcp add --scope project prose-forge -- uv run python mcp_server.py
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "prose-forge": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/prose-forge", "run", "python", "mcp_server.py"],
      "env": {"OPENROUTER_API_KEY": "sk-or-..."}
    }
  }
}
```

Tools: `status`, `list_chapter_prompts`, `list_runs`, `start_run`, `get_run`,
`get_artifact`, `approve_beats`, `accept_run`, `cancel_run`, `lint_text`,
`edit_text`, `rebuild_baseline`, `rebuild_banlist`, `build_triples`,
`run_eval`. Long work runs as detached subprocesses — `start_run` returns a
`run_id` immediately and state is read from `status.json`.

Example agent instructions that work well:

1. *"Start a run for prompts/ch07.md paused after beats. Show me the beat
   sheet; I'll tell you what to change before you resume it."*
2. *"Check status — if the banlist is older than the corpus chunks, rebuild
   it, then run the eval with k=20 and tell me the trend."*
3. *"Here's a paragraph I wrote for chapter 3: lint it, and if anything is
   flagged, run the surgical edit and show me a before/after."*

## Editing the knobs

- **`data/banlist.txt`** — one entry per line; literal by default (word-bound,
  case-insensitive), `re:` prefix for regex. Hand-edit freely: mark a line
  `# keep` to make it survive every rebuild, `# drop` to permanently retire it
  (rebuilds will not re-add it). Unmarked lines are regenerated by
  `forge banlist build`.
- **`prompt_templates/*.md`** — all LLM prompts live here as data; edit
  without touching code. `{placeholders}` are substituted by name; literal
  braces (JSON examples) pass through. Keep the drafter/planner templates free
  of banlist terms and "avoid X" phrasing — `tests/test_templates.py` enforces
  it.
- **`config.yaml`** — model slots (verify slugs at openrouter.ai/models; keep
  adversarial roles in different families), sampling, pipeline knobs, gates.

## Privacy

`corpus/`, `runs/`, `manuscript/`, `.env`, and all of `data/` are gitignored —
your prose and everything derived from it stays local. Test fixtures are
original scenes written for this repo, not anyone's real writing.

## Development

```bash
uv sync
uv run ruff check .
FORGE_MOCK=1 uv run pytest
```

See `PLAN.md` for architecture and `DECISIONS.md` for every judgment call.
