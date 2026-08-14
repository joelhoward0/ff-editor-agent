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


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
