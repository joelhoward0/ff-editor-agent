"""``forge`` — typer CLI for prose-forge.

Exit codes: 0 ok, 1 hard-gate failure, 2 config/asset missing (the error
message names the command to run first).
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(help="prose-forge: draft fiction in your voice, then strip the AI-isms.")
console = Console()

EXIT_OK = 0
EXIT_GATE_FAIL = 1
EXIT_MISSING = 2

_EXAMPLE_PROMPT = """\
# Chapter prompt: example

POV: (character name)
Goal: (what this chapter must accomplish)

Notes:
- (setting, situation, and any beats you already know)
- (what changed at the end of the previous chapter)
"""


@app.command()
def init() -> None:
    """Create the working directories and example files."""
    for d in ["corpus", "prompts", "bible", "manuscript", "runs",
              "data", "data/control", "data/triples"]:
        Path(d).mkdir(parents=True, exist_ok=True)
    example = Path("prompts/example.md")
    if not example.exists():
        example.write_text(_EXAMPLE_PROMPT, encoding="utf-8")
    env_example = Path(".env.example")
    if not env_example.exists():
        env_example.write_text("OPENROUTER_API_KEY=\n", encoding="utf-8")
    console.print("[green]Initialized.[/green] Drop your prose in corpus/, then run "
                  "[bold]forge ingest && forge baseline && forge banlist build[/bold].")


@app.command()
def ingest() -> None:
    """Split corpus files into tagged scene chunks (data/chunks.jsonl)."""
    from .config import MissingAssetError, load_config
    from .corpus import ingest as run_ingest

    try:
        summary = run_ingest(load_config())
    except MissingAssetError as exc:
        console.print(f"[red]{exc}[/red] — run: [bold]{exc.run_first}[/bold]")
        raise typer.Exit(EXIT_MISSING) from exc
    console.print(
        f"Ingested [bold]{summary['files']}[/bold] files → {summary['chunks']} chunks "
        f"({summary['tagged']} tagged, {summary['reused']} reused)."
    )


@app.command()
def baseline() -> None:
    """Compute corpus style baseline (data/baseline.json)."""
    from .config import MissingAssetError
    from .corpus import build_baseline_asset

    try:
        result = build_baseline_asset()
    except MissingAssetError as exc:
        console.print(f"[red]{exc}[/red] — run: [bold]{exc.run_first}[/bold]")
        raise typer.Exit(EXIT_MISSING) from exc
    console.print(
        f"Baseline over {result['chunk_count']} chunks / {result['word_count']} words: "
        f"sent_len {result['sent_len_mean']:.1f}±{result['sent_len_std']:.1f}, "
        f"em-dash/1k {result['em_dash_per_1k']:.2f}, "
        f"dialogue {result['dialogue_ratio']:.2f}, "
        f"self banlist hits/1k {result['banlist_hits_per_1k']:.2f}"
    )


banlist_app = typer.Typer(help="Build and inspect the AI-tell banlist.")
app.add_typer(banlist_app, name="banlist")


@banlist_app.command("build")
def banlist_build(
    prompts_sample: int = typer.Option(30, "--prompts-sample", help="Prompts for control set."),
) -> None:
    """Mine AI tells (control vs corpus) and merge into data/banlist.txt."""
    from .banlist import build_banlist
    from .config import MissingAssetError, load_config

    try:
        summary = build_banlist(load_config(), prompts_sample=prompts_sample)
    except MissingAssetError as exc:
        console.print(f"[red]{exc}[/red] — run: [bold]{exc.run_first}[/bold]")
        raise typer.Exit(EXIT_MISSING) from exc
    console.print(
        f"Banlist rebuilt from {summary['control_files']} control files: "
        f"{summary['mined_candidates']} mined, {summary['mined_added']} added, "
        f"{summary['kept']} kept, {summary['dropped']} dropped, "
        f"[bold]{summary['total_active']} active rules[/bold]."
    )


triples_app = typer.Typer(help="Build beats→AI-draft→gold triples.")
app.add_typer(triples_app, name="triples")


@triples_app.command("build")
def triples_build(
    n: int = typer.Option(40, "--n", help="Number of corpus chunks to sample."),
) -> None:
    """Build data/triples/ from sampled corpus chunks."""
    from .config import MissingAssetError, load_config
    from .triples import build_triples

    try:
        summary = build_triples(load_config(), n=n)
    except MissingAssetError as exc:
        console.print(f"[red]{exc}[/red] — run: [bold]{exc.run_first}[/bold]")
        raise typer.Exit(EXIT_MISSING) from exc
    console.print(
        f"Triples: {summary['built']} built, {summary['skipped']} existing, "
        f"{summary['total']} total."
    )


@app.command()
def run(
    prompt: str = typer.Argument(None, help="Chapter prompt file (prompts/<id>.md)."),
    resume: str = typer.Option(None, "--resume", help="Run id to resume."),
    pause_after_beats: bool = typer.Option(
        False, "--pause-after-beats", help="Stop after the beat sheet for human review."
    ),
    opening: str = typer.Option(None, "--opening", help="File with a human opening line."),
    run_id: str = typer.Option(None, "--run-id", hidden=True),
) -> None:
    """Run the chapter pipeline: plan → draft → select → lint → edit → report."""
    from .config import MissingAssetError
    from .pipeline import run_pipeline

    try:
        status = run_pipeline(
            prompt,
            run_id=run_id or resume,
            resume=resume is not None,
            pause_after_beats=pause_after_beats or None,
            opening_file=opening,
        )
    except MissingAssetError as exc:
        console.print(f"[red]{exc}[/red] — run: [bold]{exc.run_first}[/bold]")
        raise typer.Exit(EXIT_MISSING) from exc
    rid = status["run_id"]
    if status.get("state") == "awaiting_beats":
        console.print(
            f"[yellow]Paused after beats.[/yellow] Edit runs/{rid}/beats.md, then "
            f"[bold]forge run --resume {rid}[/bold]"
        )
        return
    gates = status.get("gates") or {}
    if gates.get("hard_fail"):
        console.print(f"[red]Run {rid} finished with the hard gate FAILING.[/red] "
                      f"See runs/{rid}/report.md")
        raise typer.Exit(EXIT_GATE_FAIL)
    warnings = gates.get("warnings") or []
    warn_note = f" ({len(warnings)} soft warnings)" if warnings else ""
    console.print(f"[green]Run {rid} complete.[/green]{warn_note} "
                  f"Final text: runs/{rid}/final.md — accept with "
                  f"[bold]forge accept {rid}[/bold]")


@app.command()
def accept(run_id: str = typer.Argument(..., help="Run id to accept.")) -> None:
    """Append a finished run's final.md to the manuscript."""
    from .config import MissingAssetError
    from .pipeline import accept_run

    try:
        manuscript = accept_run(run_id)
    except MissingAssetError as exc:
        console.print(f"[red]{exc}[/red] — run: [bold]{exc.run_first}[/bold]")
        raise typer.Exit(EXIT_MISSING) from exc
    console.print(f"[green]Accepted.[/green] Appended to {manuscript}.")


runs_app = typer.Typer(help="Inspect runs.")
app.add_typer(runs_app, name="runs")


@runs_app.command("list")
def runs_list(limit: int = typer.Option(20, "--limit")) -> None:
    """Most recent runs with stage/state."""
    from rich.table import Table

    from .runstate import list_runs

    table = Table("run_id", "stage", "state", "updated_at")
    for status in list_runs(limit):
        table.add_row(
            status.get("run_id", "?"), status.get("stage", "?"),
            status.get("state", "?"), status.get("updated_at", ""),
        )
    console.print(table)


@runs_app.command("show")
def runs_show(run_id: str = typer.Argument(...)) -> None:
    """One run's status, artifacts, and gate results."""
    import json as _json

    from .runstate import read_status, run_dir

    status = read_status(run_id)
    if status is None:
        console.print(f"[red]unknown run: {run_id}[/red]")
        raise typer.Exit(EXIT_MISSING)
    console.print_json(_json.dumps(status))
    d = run_dir(run_id)
    artifacts = sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file())
    console.print("artifacts: " + ", ".join(artifacts))


def _read_text_arg(source: str) -> str:
    import sys

    if source == "-":
        return sys.stdin.read()
    path = Path(source)
    if not path.exists():
        console.print(f"[red]file not found: {source}[/red]")
        raise typer.Exit(EXIT_MISSING)
    return path.read_text(encoding="utf-8")


@app.command()
def lint(source: str = typer.Argument(..., help="File to lint, or - for stdin.")) -> None:
    """Deterministic lint of arbitrary text against the current banlist + gates."""
    import json as _json

    from .banlist import active_rules
    from .config import load_config
    from .corpus import BASELINE_PATH
    from .lint import lint_text

    cfg = load_config()
    text = _read_text_arg(source)
    baseline = None
    if BASELINE_PATH.exists():
        baseline = _json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    result = lint_text(text, active_rules(), baseline=baseline, gates=cfg.gates)
    for span in result["spans"]:
        console.print(f"  [red]{span['start']}-{span['end']}[/red] "
                      f"“{span['text']}” ← [dim]{span['rule']}[/dim]")
    for warning in result["warnings"]:
        console.print(f"  [yellow]warn[/yellow] {warning['message']}")
    verdict = "[red]HARD FAIL[/red]" if result["hard_fail"] else "[green]clean[/green]"
    console.print(f"{verdict} — {len(result['spans'])} banlist hits, "
                  f"{len(result['warnings'])} warnings")
    if result["hard_fail"]:
        raise typer.Exit(EXIT_GATE_FAIL)


@app.command()
def edit(source: str = typer.Argument(..., help="File to edit, or - for stdin.")) -> None:
    """Ad-hoc lint + surgical edit of arbitrary text; edited text goes to stdout."""
    from .banlist import active_rules
    from .config import load_config
    from .editor import edit_pass
    from .lint import lint_text
    from .triples import example_pairs

    cfg = load_config()
    text = _read_text_arg(source)
    rules = active_rules()
    result = lint_text(text, rules, gates=cfg.gates)
    err = Console(stderr=True)
    if not result["spans"]:
        err.print("[green]No flagged spans — text unchanged.[/green]")
        typer.echo(text, nl=False)
        return
    new_text, info = edit_pass(text, result["spans"], example_pairs("interiority"), cfg)
    err.print(
        f"Edited {len(result['spans'])} flagged spans in {info['attempts']} attempt(s); "
        f"{len(info['rejected'])} out-of-span changes healed."
    )
    typer.echo(new_text, nl=False)


eval_app = typer.Typer(help="Discrimination eval: judges guess HUMAN vs AI.")
app.add_typer(eval_app, name="eval")


@eval_app.command("run")
def eval_run(k: int = typer.Option(10, "--k", help="Paragraphs per side.")) -> None:
    """Blind HUMAN/AI test over K corpus + K pipeline paragraphs."""
    from rich.table import Table

    from .config import MissingAssetError, load_config
    from .evalx import run_eval

    cfg = load_config()
    try:
        result = run_eval(cfg, k=k)
    except MissingAssetError as exc:
        console.print(f"[red]{exc}[/red] — run: [bold]{exc.run_first}[/bold]")
        raise typer.Exit(EXIT_MISSING) from exc
    table = Table("judge", "accuracy")
    for model, acc in result["accuracies"].items():
        table.add_row(model, f"{acc:.2f}")
    console.print(table)
    verdict = "[green]PASS[/green]" if result["pass"] else "[red]FAIL[/red]"
    console.print(
        f"{verdict} mean accuracy {result['mean_accuracy']:.2f} "
        f"(gate ≤ {result['gate']}), slop {result['slop']:.2f}/1k, "
        f"ai_source={result['ai_source']}"
    )
    console.print(f"trend: {result['trend']}")
    if not result["pass"]:
        raise typer.Exit(EXIT_GATE_FAIL)


@app.command()
def status() -> None:
    """Config summary, asset freshness, active runs, last eval."""
    from .config import MissingAssetError, load_config
    from .evalx import last_eval
    from .runstate import asset_status, list_runs

    try:
        cfg = load_config()
    except MissingAssetError as exc:
        console.print(f"[red]{exc}[/red] — run: [bold]{exc.run_first}[/bold]")
        raise typer.Exit(EXIT_MISSING) from exc
    console.print("[bold]models[/bold]")
    for slot in ("planner", "drafter_a", "drafter_b", "judge", "editor", "tagger"):
        console.print(f"  {slot}: {cfg.models.slug(slot)}")
    console.print(f"  eval_judges: {', '.join(cfg.models.eval_judges) or '(none)'}")
    console.print("[bold]assets[/bold]")
    for name, info in asset_status().items():
        if info is None:
            console.print(f"  {name}: [yellow]missing[/yellow]")
        else:
            count = f", {info['count']} items" if "count" in info else ""
            console.print(f"  {name}: {info['updated_at']}{count}")
    active = [s for s in list_runs(50) if s.get("state") in ("running", "awaiting_beats")]
    console.print(f"[bold]active runs[/bold]: {len(active)}")
    for status_row in active:
        console.print(
            f"  {status_row['run_id']}: {status_row.get('stage')}/{status_row.get('state')}"
        )
    latest = last_eval()
    if latest:
        console.print(
            f"[bold]last eval[/bold]: mean {latest['mean_accuracy']:.2f} "
            f"at {latest['timestamp']} (slop {latest['slop']:.2f}/1k)"
        )
    else:
        console.print("[bold]last eval[/bold]: (none)")


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
