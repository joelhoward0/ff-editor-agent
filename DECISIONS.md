# DECISIONS

Judgment calls made while implementing the spec, in rough order of appearance.

1. **`config.py` added to the module list.** The spec's module list has no home
   for config loading; a small typed pydantic loader module beats stuffing it
   into `__init__.py`.

2. **All of `data/` is gitignored, not just `data/control/` and
   `data/triples/`.** `chunks.jsonl` and `triples/*.json` contain verbatim
   corpus text, and `banlist.txt`/`baseline.json` are derived from it. The
   hard privacy constraint ("no corpus text in git history") wins over the
   narrower ignore list in the spec. `data/.gitkeep` is committed so the
   directory exists on clone.

3. **Two extra prompt templates: `openers.md` and `editor_stern.md`.**
   "Templates are data — code must not contain prompt strings", but the spec's
   seven templates don't cover opening-line generation (`generate3`) or the
   "sterner template" retry for the editor. Both became template files rather
   than strings in code.

4. **Mock variant cycling scheme.** `tests/fixtures/mock_responses/<slot>.md`
   is variant 1; `<slot>_2.md`, `<slot>_3.md`, … are later variants. A
   module-level per-slot counter cycles through them (wrapping), so repeated
   calls are deterministic. `FORGE_MOCK_DIR` can point elsewhere (used by
   tests running in temp workspaces).

5. **`min_p` pass-through.** `min_p` is sent when present in sampling config;
   if OpenRouter returns 400 mentioning an unsupported parameter, the request
   is retried once with `min_p` stripped, per "ignore provider errors for
   unsupported params".

6. **Eval judges reuse one mock slot.** `eval_judges` are model slugs, not
   slots; `chat()` accepts a `model_override` and eval calls use slot key
   `eval_judge`, so mock mode serves them from `eval_judge.md` fixtures.

7. **Scene chunks under 600 words are allowed.** Scene integrity beats the size
   floor: explicit scene breaks are never merged across, so a short scene
   becomes a short chunk. Packing only applies within a scene (600–1500 target,
   split at paragraph boundaries).

8. **Tagger fallback tags.** If the tagger returns unparseable JSON, the chunk
   gets `{pov: "unknown", scene_type: "interiority", tense: "past"}` and a
   warning — ingest never dies on one bad response.

9. **Judge fallback verdict.** If a pairwise judge response is unparseable
   JSON, the comparison records winner `A` with reason
   `"unparseable judge response (fallback)"` — logged, never fatal. Ties are
   broken deterministically downstream anyway (banlist hits, then sent_len_std).

10. **Literal banlist matching is whitespace-flexible.** Literal entries match
    with `\s+` between words (word-boundary anchored, case-insensitive) so a
    phrase split across a line break still matches.

11. **Banlist ordering.** `# keep`-marked and seed entries first (no ratio),
    then mined entries sorted by control/corpus ratio descending. Ratio is
    recorded in a trailing comment mined entries only; hand-edits survive
    rebuilds via keep/drop markers.

12. **`dialogue_ratio`** counts characters strictly between matched
    straight/curly double-quote pairs (quotes excluded) divided by total
    character count of the text.

13. **`forge run --run-id`** (hidden option) lets the MCP server pre-generate
    the run id so `start_run` can return `{run_id}` immediately after
    `Popen`. The chapter prompt is copied to `runs/<id>/prompt.md` so
    `--resume` doesn't need the prompt argument.

14. **Cancellation via pid file.** The pipeline writes `runs/<id>/pid.txt` at
    start; `cancel_run` SIGTERMs that pid and marks the run failed. Background
    asset jobs (`rebuild_baseline`, …) get handles under `runs/_jobs/<job>.json`
    and are reported by `status()` with liveness checked via `os.kill(pid, 0)`.

15. **Opening-line picker.** `generate3` asks the judge slot for exactly 3
    numbered openers; picker strips numbering, scores banlist hits (fewest
    wins), tiebreaks on shortest. If the response yields no usable lines, the
    run proceeds with no prefill and a warning — never a hard failure.

16. **Report "scene-final lines".** Scenes are split on `***`/`---` marker
    lines within the final text; the last non-empty line of each scene (and of
    the chapter) is listed for human review with its line number.

17. **Baseline aggregation.** Sentence/paragraph metrics pool all sentences and
    paragraphs across chunks (not per-chunk means of means); rate metrics are
    computed over the concatenated corpus.

18. **First push establishes `main`.** The new repo was empty; the project
    lives on `main` as its default branch.

19. **Asset freshness lives in `runstate.py`.** `asset_status()` (used by both
    `forge status` and the MCP `status()` tool) reports timestamps + counts for
    chunks/baseline/banlist/triples; it's state inspection, so it sits with
    run state rather than a new module.

20. **`para_len_words_p50/p90`** use numpy's linear-interpolation percentiles
    over paragraph word counts.

21. **Control-set naming.** `banlist build` writes control drafts to
    `data/control/<prompt-stem>-<slot>.md`, one per prompt × drafter slot, and
    skips existing files (idempotent-ish; delete the dir to regenerate).

22. **Judge sees a gold excerpt capped at ~250 words** (start of the
    best-matching corpus chunk) to keep pairwise prompts affordable.

23. **Editor few-shot pairs come from triples when available.** If
    `data/triples/` is empty, the editor prompt says "(no examples available)" —
    edit still works; triples improve it but aren't a hard dependency.

24. **`context_tail_words` truncation is word-based** and cuts at a paragraph
    boundary when possible (never mid-sentence) so the drafter sees clean
    manuscript tail.
