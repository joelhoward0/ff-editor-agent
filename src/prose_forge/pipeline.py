"""The chapter pipeline: plan → draft → select → lint → edit → report.

Every stage writes its artifact into ``runs/<run_id>/`` and updates
``status.json``; ``--resume`` re-enters at the first stage whose artifact is
missing. The drafter/planner context is assembled here and, by construction,
never contains banlist content or "avoid X" instructions — style is enforced
afterward by lint + surgical edit.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from . import banlist, editor, judge, llm, retrieve, runstate, stats, triples
from .config import Config, MissingAssetError, load_config
from .corpus import load_baseline, load_chunks
from .lint import lint_text

_POV_LINE = re.compile(r"POV:\s*(?P<pov>[^|\n]+?)\s*\|\s*TYPE:\s*(?P<type>\w+)", re.IGNORECASE)


def _tail_words(text: str, n: int) -> str:
    """Last ~n words, cut at a paragraph boundary when possible."""
    paras = [p for p in re.split(r"\n[ \t]*\n", text) if p.strip()]
    out: list[str] = []
    count = 0
    for para in reversed(paras):
        words = stats.word_count(para)
        if out and count + words > n:
            break
        out.append(para)
        count += words
    if not out and text.strip():
        return " ".join(text.split()[-n:])
    return "\n\n".join(reversed(out))


def manuscript_tail(config: Config) -> str:
    """The context tail fed to the planner and drafters."""
    path = Path(config.paths.manuscript)
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return "(beginning of manuscript)"
    return _tail_words(path.read_text(encoding="utf-8"), config.pipeline.context_tail_words)


def _bible_text(config: Config) -> str:
    root = Path(config.paths.bible)
    if not root.exists():
        return "(no bible)"
    docs = sorted(p for p in root.rglob("*") if p.suffix in (".md", ".txt") and p.is_file())
    if not docs:
        return "(no bible)"
    return "\n\n---\n\n".join(p.read_text(encoding="utf-8") for p in docs)


def parse_beats_header(beats: str) -> tuple[str | None, str | None]:
    """(pov, scene_type) from the beat sheet's declaration line, if present."""
    match = _POV_LINE.search(beats)
    if not match:
        return None, None
    return match.group("pov").strip(), match.group("type").strip().lower()


class RunContext:
    """Everything a stage needs: config, assets, and the run directory."""

    def __init__(self, run_id: str, config: Config | None = None):
        self.config = config or load_config()
        self.run_id = run_id
        self.dir = runstate.run_dir(run_id)
        self.chunks = load_chunks()
        self.baseline = load_baseline()
        self.rules = banlist.active_rules()

    @property
    def expected_drafts(self) -> int:
        return 2 * self.config.pipeline.samples_per_drafter

    def prompt_text(self) -> str:
        path = self.dir / "prompt.md"
        if not path.exists():
            raise MissingAssetError(
                f"runs/{self.run_id}/prompt.md missing — cannot resume without it",
                run_first="forge run prompts/<id>.md",
            )
        return path.read_text(encoding="utf-8")

    def beats(self) -> str:
        return (self.dir / "beats.md").read_text(encoding="utf-8")

    def drafts(self) -> dict[str, str]:
        return {
            p.stem: p.read_text(encoding="utf-8")
            for p in sorted((self.dir / "drafts").glob("*.md"))
        }


def stage_plan(ctx: RunContext) -> None:
    """Planner → ``beats.md`` (numbered beats + POV/TYPE declaration)."""
    llm.set_stage("plan")
    prompt = llm.render_template(
        "planner",
        chapter_prompt=ctx.prompt_text(),
        bible=_bible_text(ctx.config),
        manuscript_tail=manuscript_tail(ctx.config),
    )
    result = llm.chat(
        "planner", [{"role": "user", "content": prompt}],
        sampling_key="judge", mock_key="planner", config=ctx.config,
    )
    (ctx.dir / "beats.md").write_text(result.text.strip() + "\n", encoding="utf-8")


def _opening_prefill(ctx: RunContext, beats: str, tail: str) -> str | None:
    mode = ctx.config.pipeline.opening_line_mode
    if mode == "none":
        return None
    if mode == "human":
        path = ctx.dir / "opening.txt"
        if not path.exists():
            raise MissingAssetError(
                f"opening_line_mode is 'human' but runs/{ctx.run_id}/opening.txt is missing",
                run_first=f"forge run --resume {ctx.run_id} --opening <file>",
            )
        return path.read_text(encoding="utf-8").strip() or None
    openers = judge.generate_openers(beats, tail, ctx.config)
    return judge.pick_opener(openers, ctx.rules)


def stage_draft(ctx: RunContext) -> None:
    """Both drafters × samples_per_drafter → ``drafts/{a1,a2,b1,b2}.md``.

    INVARIANT: the assembled context is built only from the manuscript tail,
    retrieved corpus excerpts, beats, and positive style constraints — no
    banlist content, no negative style instructions.
    """
    llm.set_stage("draft")
    beats = ctx.beats()
    pov, scene_type = parse_beats_header(beats)
    tail = manuscript_tail(ctx.config)
    matches = retrieve.top_matches(
        ctx.chunks, beats,
        k=ctx.config.pipeline.retrieved_excerpts, pov=pov, scene_type=scene_type,
    )
    excerpts = "\n\n---\n\n".join(
        f"[Excerpt {i}]\n{stats.excerpt(c['text'], 300)}" for i, c in enumerate(matches, 1)
    ) or "(none)"
    constraints = "\n".join(f"- {c}" for c in ctx.config.style_constraints) or "(none)"
    prompt = llm.render_template(
        "drafter",
        manuscript_tail=tail,
        excerpts=excerpts,
        beats=beats,
        style_constraints=constraints,
    )
    prefill = _opening_prefill(ctx, beats, tail)
    if prefill:
        (ctx.dir / "opening_used.txt").write_text(prefill + "\n", encoding="utf-8")
    for slot, letter in (("drafter_a", "a"), ("drafter_b", "b")):
        for i in range(1, ctx.config.pipeline.samples_per_drafter + 1):
            result = llm.chat(
                slot, [{"role": "user", "content": prompt}],
                prefill=prefill, sampling_key="draft", config=ctx.config,
            )
            (ctx.dir / "drafts" / f"{letter}{i}.md").write_text(
                result.text.strip() + "\n", encoding="utf-8"
            )


def stage_select(ctx: RunContext) -> None:
    """Round-robin pairwise judging → ``selection.json``."""
    llm.set_stage("select")
    beats = ctx.beats()
    pov, scene_type = parse_beats_header(beats)
    gold_match = retrieve.top_matches(ctx.chunks, beats, k=1, pov=pov, scene_type=scene_type)
    gold = stats.excerpt(gold_match[0]["text"], 250) if gold_match else "(no corpus excerpt)"
    selection = judge.pairwise_select(ctx.drafts(), gold, ctx.config, ctx.rules)
    selection["gold_ref"] = gold_match[0]["id"] if gold_match else None
    (ctx.dir / "selection.json").write_text(
        json.dumps(selection, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _winner_text(ctx: RunContext) -> str:
    selection = json.loads((ctx.dir / "selection.json").read_text(encoding="utf-8"))
    return ctx.drafts()[selection["winner"]]


def _lint_to_file(ctx: RunContext, text: str) -> dict[str, Any]:
    result = lint_text(text, ctx.rules, baseline=ctx.baseline, gates=ctx.config.gates)
    (ctx.dir / "lint.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return result


def stage_lint(ctx: RunContext) -> None:
    """Deterministic lint of the winning draft → ``lint.json``."""
    _lint_to_file(ctx, _winner_text(ctx))


def stage_edit(ctx: RunContext) -> None:
    """Surgical edit loop while the hard gate fails → ``editN.md``, ``edited.md``."""
    llm.set_stage("edit")
    text = _winner_text(ctx)
    lint_result = json.loads((ctx.dir / "lint.json").read_text(encoding="utf-8"))
    _, scene_type = parse_beats_header(ctx.beats())
    edit_log: list[dict[str, Any]] = []
    loops = 0
    while lint_result["hard_fail"] and loops < ctx.config.pipeline.max_edit_loops:
        loops += 1
        examples = triples.example_pairs(scene_type or "interiority")
        text, info = editor.edit_pass(text, lint_result["spans"], examples, ctx.config)
        (ctx.dir / f"edit{loops}.md").write_text(text.strip() + "\n", encoding="utf-8")
        edit_log.append(info)
        lint_result = _lint_to_file(ctx, text)
    if edit_log:
        (ctx.dir / "edit_log.json").write_text(
            json.dumps(edit_log, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    (ctx.dir / "edited.md").write_text(text.strip() + "\n", encoding="utf-8")


def _gates_table(ctx: RunContext, lint_result: dict[str, Any]) -> list[dict[str, Any]]:
    gates_cfg = ctx.config.gates
    text_stats = lint_result["stats"]
    warned = {w["gate"] for w in lint_result["warnings"]}
    hits = text_stats["banlist_hits_per_1k"]
    rows = [
        {
            "gate": "post_edit_banlist_hits_per_1k",
            "value": round(hits, 3),
            "limit": gates_cfg.post_edit_banlist_hits_per_1k,
            "baseline": ctx.baseline.get("banlist_hits_per_1k", 0.0),
            "status": "fail" if lint_result["hard_fail"] else "pass",
        }
    ]
    for gate, key in [
        ("sent_len_std_tolerance", "sent_len_std"),
        ("em_dash_per_1k_max_ratio", "em_dash_per_1k"),
        ("punchline_para_rate_max_ratio", "punchline_para_rate"),
    ]:
        rows.append(
            {
                "gate": gate,
                "value": round(text_stats[key], 3),
                "baseline": round(ctx.baseline.get(key, 0.0), 3),
                "status": "warn" if gate in warned else "pass",
            }
        )
    return rows


def _review_list(text: str) -> list[str]:
    """Human review pointers: scene-final lines, short paragraph enders, by line."""
    lines = text.splitlines()
    items: list[str] = []
    marker = re.compile(r"^[ \t]*(?:\*{3,}|-{3,}|\* \* \*)[ \t]*$")
    last_nonempty = None
    for i, line in enumerate(lines, 1):
        if marker.match(line) and last_nonempty:
            items.append(f"scene-final line {last_nonempty[0]}: {last_nonempty[1]}")
        if line.strip() and not marker.match(line):
            last_nonempty = (i, line.strip())
    if last_nonempty:
        items.append(f"scene-final line {last_nonempty[0]}: {last_nonempty[1]}")
    for i, para in enumerate(stats.split_paragraphs(text), 1):
        sentences = stats.split_sentences(para)
        if sentences and stats.word_count(sentences[-1]) < 8:
            items.append(f'paragraph {i} ends short: "{sentences[-1]}"')
    return items


def stage_report(ctx: RunContext) -> dict[str, Any]:
    """Final text + report → ``final.md``, ``report.md``; returns gate summary."""
    text = (ctx.dir / "edited.md").read_text(encoding="utf-8")
    lint_result = json.loads((ctx.dir / "lint.json").read_text(encoding="utf-8"))
    (ctx.dir / "final.md").write_text(text, encoding="utf-8")

    table = _gates_table(ctx, lint_result)
    costs = llm.cost_summary(ctx.dir)
    lines = [f"# Run report — {ctx.run_id}", "", "## Gates", ""]
    lines.append("| gate | value | baseline | status |")
    lines.append("|---|---|---|---|")
    for row in table:
        lines.append(
            f"| {row['gate']} | {row['value']} | {row.get('baseline', '')} "
            f"| {row['status'].upper()} |"
        )
    lines += ["", "## Remaining flags", ""]
    if lint_result["spans"]:
        for span in lint_result["spans"]:
            lines.append(
                f'- chars {span["start"]}-{span["end"]}: "{span["text"]}" ({span["rule"]})'
            )
    else:
        lines.append("(none)")
    lines += ["", "## Cost", ""]
    lines.append(
        f"- calls: {costs['calls']}, prompt tokens: {costs['prompt_tokens']}, "
        f"completion tokens: {costs['completion_tokens']}, cost: ${costs['cost']:.4f}"
    )
    for stage, per in sorted(costs["by_stage"].items()):
        lines.append(
            f"  - {stage}: {per['calls']} calls, "
            f"{per['prompt_tokens']}+{per['completion_tokens']} tokens, ${per['cost']:.4f}"
        )
    lines += ["", "## Human review", ""]
    for item in _review_list(text):
        lines.append(f"- {item}")
    lines.append("")
    (ctx.dir / "report.md").write_text("\n".join(lines), encoding="utf-8")

    gates = {
        "hard_fail": lint_result["hard_fail"],
        "warnings": [w["gate"] for w in lint_result["warnings"]],
    }
    return gates


_STAGE_FUNCS = {
    "plan": stage_plan,
    "draft": stage_draft,
    "select": stage_select,
    "lint": stage_lint,
    "edit": stage_edit,
}


def run_pipeline(
    prompt_path: str | Path | None = None,
    *,
    run_id: str | None = None,
    resume: bool = False,
    pause_after_beats: bool | None = None,
    opening_file: str | Path | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Drive a run to completion (or to ``awaiting_beats``); returns final status."""
    config = config or load_config()
    if resume:
        if not run_id:
            raise MissingAssetError("--resume requires a run id", run_first="forge runs list")
        if runstate.read_status(run_id) is None:
            raise MissingAssetError(
                f"unknown run: {run_id}", run_first="forge runs list"
            )
    else:
        if prompt_path is None:
            raise MissingAssetError(
                "a chapter prompt is required", run_first="forge run prompts/<id>.md"
            )
        prompt_path = Path(prompt_path)
        if not prompt_path.exists():
            raise MissingAssetError(
                f"prompt not found: {prompt_path}", run_first="forge init"
            )
        run_id = runstate.create_run(prompt_path.stem, run_id=run_id)
        shutil.copy(prompt_path, runstate.run_dir(run_id) / "prompt.md")

    ctx = RunContext(run_id, config=config)
    if opening_file:
        shutil.copy(Path(opening_file), ctx.dir / "opening.txt")
    pause = (
        config.pipeline.pause_after_beats if pause_after_beats is None else pause_after_beats
    )
    runstate.write_pid(run_id)
    llm.set_run_context(ctx.dir)
    try:
        while True:
            stage = runstate.next_stage(run_id, expected_drafts=ctx.expected_drafts)
            if stage is None:
                break
            if stage == "report":
                runstate.update_status(run_id, stage=stage, state="running")
                gates = stage_report(ctx)
                runstate.update_status(run_id, stage="report", state="done", gates=gates)
                break
            runstate.update_status(run_id, stage=stage, state="running")
            _STAGE_FUNCS[stage](ctx)
            if stage == "plan" and pause:
                runstate.update_status(run_id, stage="plan", state="awaiting_beats")
                return runstate.read_status(run_id)
    except Exception as exc:
        runstate.update_status(run_id, state="failed", error=str(exc))
        raise
    finally:
        llm.set_run_context(None)
    return runstate.read_status(run_id)


def accept_run(run_id: str, config: Config | None = None) -> Path:
    """Append the run's ``final.md`` to the manuscript with a scene separator."""
    config = config or load_config()
    final = runstate.run_dir(run_id) / "final.md"
    if not final.exists():
        raise MissingAssetError(
            f"runs/{run_id}/final.md not found — the run hasn't finished",
            run_first=f"forge run --resume {run_id}",
        )
    manuscript = Path(config.paths.manuscript)
    manuscript.parent.mkdir(parents=True, exist_ok=True)
    text = final.read_text(encoding="utf-8").strip()
    if manuscript.exists() and manuscript.read_text(encoding="utf-8").strip():
        existing = manuscript.read_text(encoding="utf-8").rstrip()
        manuscript.write_text(existing + "\n\n***\n\n" + text + "\n", encoding="utf-8")
    else:
        manuscript.write_text(text + "\n", encoding="utf-8")
    runstate.update_status(run_id, state="accepted")
    return manuscript
