"""prose-forge MCP stdio server.

Thin wrappers over the same library functions the CLI uses — no pipeline logic
lives here. Long work never blocks the server: runs and asset rebuilds launch
as detached subprocesses writing to plain log files, and state is read back
from ``runs/<id>/status.json`` and ``runs/_jobs/*.json``.

Register (project scope) via the repo's ``.mcp.json``, or manually:
    claude mcp add --scope project prose-forge -- uv run python mcp_server.py
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:  # mcp >= 2.0 renamed FastMCP; the decorator surface is unchanged
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from prose_forge import banlist as banlist_mod
from prose_forge import evalx, runstate
from prose_forge import lint as lint_mod
from prose_forge import pipeline as pipeline_mod
from prose_forge.config import MissingAssetError, load_config
from prose_forge.corpus import BASELINE_PATH
from prose_forge.editor import edit_pass
from prose_forge.triples import example_pairs

mcp = FastMCP("prose-forge")

JOBS_DIR = Path("runs/_jobs")
ARTIFACTS = {
    "beats": "beats.md",
    "selection": "selection.json",
    "lint": "lint.json",
    "final": "final.md",
    "report": "report.md",
}
TRUNCATE_AT = 8000


def _cli(*args: str) -> list[str]:
    """The forge CLI as a subprocess command (same interpreter, any cwd)."""
    return [sys.executable, "-m", "prose_forge.cli", *args]


def _spawn(cmd: list[str], log_path: Path) -> int:
    """Launch a detached subprocess with stdout/err appended to a log file."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
    return proc.pid


def _launch_job(name: str, cli_args: list[str]) -> dict[str, Any]:
    """Start a background asset job and persist a checkable handle."""
    job_id = f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = JOBS_DIR / f"{job_id}.log"
    pid = _spawn(_cli(*cli_args), log_path)
    handle = {
        "job_id": job_id,
        "command": " ".join(cli_args),
        "pid": pid,
        "log": str(log_path),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (JOBS_DIR / f"{job_id}.json").write_text(json.dumps(handle, indent=2), encoding="utf-8")
    return handle


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _jobs() -> list[dict[str, Any]]:
    out = []
    if not JOBS_DIR.exists():
        return out
    for path in sorted(JOBS_DIR.glob("*.json")):
        handle = json.loads(path.read_text(encoding="utf-8"))
        handle["running"] = _alive(handle.get("pid", -1))
        out.append(handle)
    return out


def _tail(path: Path, chars: int = 2000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-chars:]


@mcp.tool()
def status() -> dict[str, Any]:
    """Config summary, asset freshness (chunks/baseline/banlist/triples),
    active runs, background jobs, and the last eval result."""
    try:
        cfg = load_config()
        models = {
            slot: cfg.models.slug(slot)
            for slot in ("planner", "drafter_a", "drafter_b", "judge", "editor", "tagger")
        }
        models["eval_judges"] = cfg.models.eval_judges
    except MissingAssetError as exc:
        return {"error": str(exc), "run_first": exc.run_first}
    active = [
        s for s in runstate.list_runs(50)
        if s.get("state") in ("running", "awaiting_beats")
    ]
    return {
        "models": models,
        "assets": runstate.asset_status(),
        "active_runs": active,
        "jobs": _jobs(),
        "last_eval": evalx.last_eval(),
    }


@mcp.tool()
def list_chapter_prompts() -> dict[str, Any]:
    """Chapter prompt files available under prompts/ (id = filename stem)."""
    prompts = []
    for path in sorted(Path("prompts").glob("*.md")):
        first_line = path.read_text(encoding="utf-8").strip().splitlines()
        prompts.append({"id": path.stem, "path": str(path),
                        "title": first_line[0] if first_line else ""})
    return {"prompts": prompts}


@mcp.tool()
def list_runs(limit: int = 20) -> dict[str, Any]:
    """Recent runs, newest first, with stage/state/gates from status.json."""
    return {"runs": runstate.list_runs(limit)}


@mcp.tool()
def start_run(
    prompt_id: str = "", prompt_text: str = "", pause_after_beats: bool = False
) -> dict[str, Any]:
    """Start a chapter run in the background; returns {run_id} immediately.

    Provide either prompt_id (a file stem from list_chapter_prompts) or
    prompt_text (written to prompts/adhoc-<timestamp>.md first). Progress is
    readable via get_run; artifacts via get_artifact.
    """
    if prompt_text:
        Path("prompts").mkdir(exist_ok=True)
        prompt_path = Path("prompts") / f"adhoc-{time.strftime('%Y%m%d-%H%M%S')}.md"
        prompt_path.write_text(prompt_text, encoding="utf-8")
    elif prompt_id:
        prompt_path = Path("prompts") / f"{Path(prompt_id).stem}.md"
        if not prompt_path.exists():
            return {"error": f"no such prompt: {prompt_path}"}
    else:
        return {"error": "provide prompt_id or prompt_text"}
    run_id = runstate.new_run_id(prompt_path.stem)
    run_dir = runstate.run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    args = ["run", str(prompt_path), "--run-id", run_id]
    if pause_after_beats:
        args.append("--pause-after-beats")
    _spawn(_cli(*args), run_dir / "log.txt")
    return {"run_id": run_id}


@mcp.tool()
def get_run(run_id: str) -> dict[str, Any]:
    """One run's stage, state, gates, artifact names, and the tail of its log."""
    status_data = runstate.read_status(run_id)
    if status_data is None:
        return {"error": f"unknown run: {run_id}"}
    d = runstate.run_dir(run_id)
    artifacts = sorted(
        str(p.relative_to(d)) for p in d.rglob("*") if p.is_file() and p.name != "log.txt"
    )
    return {
        "status": status_data,
        "artifacts": artifacts,
        "log_tail": _tail(d / "log.txt"),
    }


@mcp.tool()
def get_artifact(run_id: str, name: str) -> dict[str, Any]:
    """Fetch a run artifact: beats | draft_winner | selection | lint | final | report.

    Text over 8k chars is truncated with a note and the on-disk path."""
    d = runstate.run_dir(run_id)
    if name == "draft_winner":
        selection_path = d / "selection.json"
        if not selection_path.exists():
            return {"error": "selection has not happened yet"}
        winner = json.loads(selection_path.read_text(encoding="utf-8"))["winner"]
        path = d / "drafts" / f"{winner}.md"
    elif name in ARTIFACTS:
        path = d / ARTIFACTS[name]
    else:
        return {"error": f"unknown artifact {name!r}; "
                         f"expected one of: draft_winner, {', '.join(ARTIFACTS)}"}
    if not path.exists():
        return {"error": f"artifact not ready: {path}"}
    text = path.read_text(encoding="utf-8")
    truncated = len(text) > TRUNCATE_AT
    if truncated:
        text = text[:TRUNCATE_AT]
    return {
        "name": name,
        "path": str(path),
        "truncated": truncated,
        "note": f"truncated to {TRUNCATE_AT} chars; full text at {path}" if truncated else "",
        "text": text,
    }


@mcp.tool()
def approve_beats(run_id: str, beats_markdown: str = "") -> dict[str, Any]:
    """Resume an awaiting_beats run; optionally overwrite beats.md first."""
    status_data = runstate.read_status(run_id)
    if status_data is None:
        return {"error": f"unknown run: {run_id}"}
    if status_data.get("state") != "awaiting_beats":
        return {"error": f"run {run_id} is not awaiting beats "
                         f"(state: {status_data.get('state')})"}
    d = runstate.run_dir(run_id)
    if beats_markdown.strip():
        (d / "beats.md").write_text(beats_markdown.strip() + "\n", encoding="utf-8")
    _spawn(_cli("run", "--resume", run_id), d / "log.txt")
    return {"run_id": run_id, "relaunched": True}


@mcp.tool()
def accept_run(run_id: str) -> dict[str, Any]:
    """Append a finished run's final.md to the manuscript."""
    try:
        manuscript = pipeline_mod.accept_run(run_id)
    except MissingAssetError as exc:
        return {"error": str(exc), "run_first": exc.run_first}
    return {"run_id": run_id, "manuscript": str(manuscript)}


@mcp.tool()
def cancel_run(run_id: str) -> dict[str, Any]:
    """SIGTERM a running run's recorded pid and mark the run failed."""
    pid = runstate.read_pid(run_id)
    if pid is None:
        return {"error": f"no pid recorded for run {run_id}"}
    killed = False
    if _alive(pid):
        os.kill(pid, signal.SIGTERM)
        killed = True
    runstate.update_status(run_id, state="failed", error="cancelled via MCP")
    return {"run_id": run_id, "killed": killed}


@mcp.tool()
def lint_text(text: str) -> dict[str, Any]:
    """Deterministic lint of arbitrary text: banlist spans, stats, gates."""
    cfg = load_config()
    baseline = None
    if BASELINE_PATH.exists():
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return lint_mod.lint_text(
        text, banlist_mod.active_rules(), baseline=baseline, gates=cfg.gates
    )


@mcp.tool()
def edit_text(text: str) -> dict[str, Any]:
    """One synchronous lint + surgical edit pass on arbitrary text (may take ~60s)."""
    cfg = load_config()
    result = lint_mod.lint_text(text, banlist_mod.active_rules(), gates=cfg.gates)
    if not result["spans"]:
        return {"text": text, "edited": False, "spans": [], "note": "no flagged spans"}
    new_text, info = edit_pass(text, result["spans"], example_pairs("interiority"), cfg)
    return {
        "text": new_text,
        "edited": True,
        "spans": result["spans"],
        "attempts": info["attempts"],
        "healed": info["rejected"],
    }


@mcp.tool()
def rebuild_baseline() -> dict[str, Any]:
    """Recompute the corpus style baseline in the background; returns a job handle."""
    return _launch_job("baseline", ["baseline"])


@mcp.tool()
def rebuild_banlist() -> dict[str, Any]:
    """Rebuild the banlist (control set + mining + merge) in the background."""
    return _launch_job("banlist", ["banlist", "build"])


@mcp.tool()
def build_triples(n: int = 40) -> dict[str, Any]:
    """Build beats/AI-draft/gold triples in the background; returns a job handle."""
    return _launch_job("triples", ["triples", "build", "--n", str(n)])


@mcp.tool()
def run_eval(k: int = 10) -> dict[str, Any]:
    """Run the discrimination eval in the background; results land in
    data/eval_history.jsonl and show up in status()."""
    return _launch_job("eval", ["eval", "run", "--k", str(k)])


if __name__ == "__main__":
    mcp.run()
