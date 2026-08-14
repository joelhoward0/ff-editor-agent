# prose-forge — Implementation Plan

A local agentic pipeline that drafts fiction chapters in the author's voice via
OpenRouter, then removes AI-isms with deterministic linting and surgical LLM
editing. Driven by a CLI, a file-based run directory, and an MCP stdio server.

Design philosophy, in priority order:

1. Every stage writes plain files to a run directory — inspectable, resumable,
   diffable.
2. Style enforcement is post-hoc (lint + edit), never "avoid X" instructions in
   the drafter.
3. Different model families for adversarial roles (drafter vs judge vs editor)
   to dodge self-preference bias.
4. No agent frameworks — plain Python.

## Architecture

```
prompts/<id>.md ──► plan ──► draft ×4 ──► select ──► lint ──► edit loop ──► report
                     │          │            │         │          │            │
                  beats.md   drafts/     selection   lint.json  editN.md   final.md
                                .md        .json                            report.md
      every stage: runs/<run_id>/… + status.json  (resume = check which artifacts exist)
```

Supporting assets (all derived, all local):

- `data/chunks.jsonl` — corpus split into tagged scene chunks (`forge ingest`)
- `data/baseline.json` — style statistics of the author's corpus (`forge baseline`)
- `data/banlist.txt` — AI-tell phrase list mined control-vs-corpus (`forge banlist build`)
- `data/triples/` — beats → AI draft → gold reference triples (`forge triples build`)

## Modules (`src/prose_forge/`)

| module        | responsibility |
|---------------|----------------|
| `config.py`   | pydantic schema + loader for `config.yaml` |
| `llm.py`      | OpenRouter chat client: slots, sampling, prefill, retries, per-run JSONL logging, mock mode |
| `runstate.py` | run directories, `status.json`, resume logic, asset freshness |
| `corpus.py`   | scene chunker + tagger → `chunks.jsonl` (idempotent by file hash) |
| `stats.py`    | pure text metrics (sentence/paragraph/punctuation/dialogue stats) |
| `banlist.py`  | rule parsing, matching, n-gram ratio mining, keep/drop merge |
| `lint.py`     | deterministic lint: banlist spans + stat deltas vs baseline + gates |
| `retrieve.py` | TF-IDF retrieval of POV/scene-type-matched corpus excerpts |
| `triples.py`  | beats-mine + blind-draft triples; before/after example pairs for the editor |
| `judge.py`    | pairwise round-robin selection; opening-line generation |
| `editor.py`   | surgical edit calls + diff-assert (sentence alignment, illegal-change rejection) |
| `pipeline.py` | stage orchestration, context assembly, resume, accept |
| `evalx.py`    | discrimination eval, history, trend |
| `cli.py`      | typer CLI (`forge`), exit codes 0/1/2 |

`mcp_server.py` (repo root) — FastMCP stdio server; thin wrappers over the same
library functions; long work via detached `subprocess.Popen`, state read from
`status.json`.

## Milestones

1. **Scaffold** — pyproject (uv), config.yaml, .gitignore, .mcp.json,
   `llm.py` with mock mode, `runstate.py`, initial fixtures.
2. **Corpus assets** — `corpus.py` chunker, `stats.py` metrics, `forge ingest`,
   `forge baseline`, unit tests with hand-computed values.
3. **Banlist + lint** — `banlist.py`, `lint.py`, `seed_banlist.txt`, tests.
4. **Triples + retrieval** — `triples.py`, `retrieve.py`.
5. **Pipeline** — `pipeline.py` end-to-end in mock mode, `judge.py`,
   `editor.py` diff-assert, prompt templates, tests.
6. **Eval + CLI polish** — `evalx.py`, full CLI surface, exit codes.
7. **MCP + docs + CI** — `mcp_server.py`, README, `ci.yml`, smoke test,
   acceptance checklist.

One conventional commit per milestone, tests green at each step.

## Testing strategy

- Unit tests are offline and keyless; all LLM traffic in tests runs with
  `FORGE_MOCK=1` (deterministic canned responses per slot, cycling variants).
- Fixtures are 3 original neutral fiction scenes written for this repo — never
  the author's private prose.
- A smoke test drives the full sequence (ingest → baseline → banlist → run →
  eval) in a temp workspace and asserts artifacts + gate evaluation.
- A template-invariant test proves the drafter/planner context can't contain
  banlist terms or "avoid X" phrasing.

## Privacy

`corpus/`, `runs/`, `manuscript/`, `.env`, and all of `data/` are gitignored
(chunks/control/triples contain corpus-derived text). Nothing in tests, fixtures,
or logs ever embeds corpus text.
