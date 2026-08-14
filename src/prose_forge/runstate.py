"""Run directories, status.json, resume logic, and asset freshness.

A run lives at ``runs/<run_id>/``. Each pipeline stage writes its artifact
there and updates ``status.json``; resume works purely by checking which
artifacts exist, so a run is recoverable even if status.json is stale.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUNS_DIR = Path("runs")
STAGES = ["plan", "draft", "select", "lint", "edit", "report"]

# stage -> artifact that proves the stage completed
_STAGE_ARTIFACTS: dict[str, str] = {
    "plan": "beats.md",
    "select": "selection.json",
    "lint": "lint.json",
    "edit": "edited.md",
    "report": "final.md",
}


def new_run_id(prompt_id: str) -> str:
    """``<prompt-id>-<yyyymmdd-hhmmss>``."""
    return f"{prompt_id}-{time.strftime('%Y%m%d-%H%M%S')}"


def run_dir(run_id: str) -> Path:
    """Directory for a run's artifacts."""
    return RUNS_DIR / run_id


def create_run(prompt_id: str, run_id: str | None = None) -> str:
    """Create the run directory and initial status; return the run id."""
    rid = run_id or new_run_id(prompt_id)
    d = run_dir(rid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "drafts").mkdir(exist_ok=True)
    update_status(rid, prompt_id=prompt_id, stage="plan", state="running")
    return rid


def update_status(
    run_id: str,
    *,
    prompt_id: str | None = None,
    stage: str | None = None,
    state: str | None = None,
    gates: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Merge fields into status.json and stamp ``updated_at``."""
    d = run_dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    status = read_status(run_id) or {"run_id": run_id}
    for key, value in (
        ("prompt_id", prompt_id),
        ("stage", stage),
        ("state", state),
        ("gates", gates),
        ("error", error),
    ):
        if value is not None:
            status[key] = value
    status["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    (d / "status.json").write_text(
        json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return status


def read_status(run_id: str) -> dict[str, Any] | None:
    """Parsed status.json, or None if absent/corrupt."""
    path = run_dir(run_id) / "status.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Most recent runs (by directory mtime), newest first, with their status."""
    if not RUNS_DIR.exists():
        return []
    dirs = [d for d in RUNS_DIR.iterdir() if d.is_dir() and not d.name.startswith("_")]
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    out = []
    for d in dirs[:limit]:
        status = read_status(d.name) or {"run_id": d.name, "state": "unknown"}
        out.append(status)
    return out


def completed_stages(run_id: str, expected_drafts: int = 4) -> list[str]:
    """Stages whose artifacts exist on disk, in pipeline order."""
    d = run_dir(run_id)
    done = []
    for stage in STAGES:
        if stage == "draft":
            drafts = list((d / "drafts").glob("*.md")) if (d / "drafts").exists() else []
            ok = len(drafts) >= expected_drafts
        else:
            ok = (d / _STAGE_ARTIFACTS[stage]).exists()
        if ok:
            done.append(stage)
        else:
            break  # stages are strictly ordered; stop at the first gap
    return done


def next_stage(run_id: str, expected_drafts: int = 4) -> str | None:
    """First stage whose artifact is missing, or None when the run is complete."""
    done = completed_stages(run_id, expected_drafts=expected_drafts)
    for stage in STAGES:
        if stage not in done:
            return stage
    return None


def write_pid(run_id: str) -> None:
    """Record the running process id so an MCP client can cancel the run."""
    (run_dir(run_id) / "pid.txt").write_text(str(os.getpid()), encoding="utf-8")


def read_pid(run_id: str) -> int | None:
    """Pid recorded by :func:`write_pid`, or None."""
    path = run_dir(run_id) / "pid.txt"
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _file_info(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return {
        "path": str(path),
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(
            timespec="seconds"
        ),
    }


def asset_status() -> dict[str, Any]:
    """Freshness + counts for derived assets (chunks, baseline, banlist, triples)."""
    chunks = _file_info(Path("data/chunks.jsonl"))
    if chunks:
        chunks["count"] = sum(
            1 for line in Path("data/chunks.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    banlist = _file_info(Path("data/banlist.txt"))
    if banlist:
        banlist["count"] = sum(
            1
            for line in Path("data/banlist.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    triples_dir = Path("data/triples")
    triples = None
    if triples_dir.exists():
        files = list(triples_dir.glob("*.json"))
        if files:
            latest = max(f.stat().st_mtime for f in files)
            triples = {
                "path": str(triples_dir),
                "count": len(files),
                "updated_at": datetime.fromtimestamp(latest, UTC).isoformat(timespec="seconds"),
            }
    return {
        "chunks": chunks,
        "baseline": _file_info(Path("data/baseline.json")),
        "banlist": banlist,
        "triples": triples,
    }
