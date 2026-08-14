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


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
