"""OpenRouter chat client: slots, sampling profiles, prefill, retries, logging, mock mode.

All LLM traffic in prose-forge goes through :func:`chat`. A module-level run
context (set by the pipeline) routes per-call logs to
``runs/<run_id>/llm_log.jsonl``. With ``FORGE_MOCK=1`` no network is touched:
responses come from ``tests/fixtures/mock_responses/<slot>.md`` (numbered
variants cycle deterministically).
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel

from .config import Config, load_config

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_RETRIES = 4
TIMEOUT_S = 120.0


class LLMResult(BaseModel):
    """Outcome of one chat call; ``text`` always includes any prefill."""

    text: str
    slot: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float | None = None
    latency_s: float = 0.0


_run_context: dict[str, Any] = {}
_mock_counters: dict[str, int] = {}


def set_run_context(run_dir: Path | str | None, stage: str = "") -> None:
    """Route subsequent call logs to ``run_dir/llm_log.jsonl`` under ``stage``."""
    _run_context.clear()
    if run_dir is not None:
        _run_context.update({"dir": Path(run_dir), "stage": stage})


def set_stage(stage: str) -> None:
    """Update the stage label used in log entries for the active run."""
    if _run_context:
        _run_context["stage"] = stage


def is_mock() -> bool:
    """True when FORGE_MOCK=1: no network, canned responses."""
    return os.environ.get("FORGE_MOCK") == "1"


def mock_dir() -> Path:
    """Directory of canned responses (override with FORGE_MOCK_DIR)."""
    return Path(os.environ.get("FORGE_MOCK_DIR", "tests/fixtures/mock_responses"))


def reset_mock_counters() -> None:
    """Restart mock variant cycling (used between test scenarios)."""
    _mock_counters.clear()


def _mock_variants(slot: str) -> list[Path]:
    base = mock_dir()
    variants = [base / f"{slot}.md"]
    variants += sorted(
        base.glob(f"{slot}_[0-9]*.md"),
        key=lambda p: int(re.search(r"_(\d+)$", p.stem).group(1)),  # type: ignore[union-attr]
    )
    return [p for p in variants if p.exists()]


def _mock_response(slot: str) -> str:
    variants = _mock_variants(slot)
    if not variants:
        raise FileNotFoundError(
            f"no mock response for slot {slot!r} in {mock_dir()} "
            f"(expected {slot}.md or {slot}_2.md ...)"
        )
    idx = _mock_counters.get(slot, 0)
    _mock_counters[slot] = idx + 1
    return variants[idx % len(variants)].read_text(encoding="utf-8")


def _api_key() -> str:
    load_dotenv()
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Copy .env.example to .env and add your key "
            "(or export it), or run with FORGE_MOCK=1."
        )
    return key


def _log_call(entry: dict[str, Any]) -> None:
    if not _run_context:
        return
    run_dir: Path = _run_context["dir"]
    run_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "stage": _run_context.get("stage", ""),
        **entry,
    }
    with (run_dir / "llm_log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _strip_echoed_prefill(completion: str, prefill: str) -> str:
    if completion.startswith(prefill):
        return completion[len(prefill) :]
    return completion


def _post_with_retries(payload: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
    """POST to OpenRouter, retrying 429/5xx with exponential backoff (2,4,8,16s)."""
    dropped_min_p = False
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = httpx.post(OPENROUTER_URL, json=payload, headers=headers, timeout=TIMEOUT_S)
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(2 ** (attempt + 1))
                continue
            raise
        if resp.status_code == 400 and not dropped_min_p and "min_p" in payload:
            # Some providers reject non-standard sampling params; drop and retry once.
            payload = {k: v for k, v in payload.items() if k != "min_p"}
            dropped_min_p = True
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt < MAX_RETRIES:
                time.sleep(2 ** (attempt + 1))
                continue
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"OpenRouter request failed after {MAX_RETRIES} retries: {last_exc}")


def chat(
    slot: str,
    messages: list[dict[str, str]],
    *,
    prefill: str | None = None,
    sampling_key: str,
    response_prefix_strip: bool = True,
    model_override: str | None = None,
    config: Config | None = None,
) -> LLMResult:
    """Call the model bound to ``slot`` and return the full text (prefill included).

    ``prefill`` is appended as a final assistant message (OpenRouter completes
    partial assistant messages); the returned text is always
    ``prefill + completion`` so callers never reassemble it themselves.
    ``model_override`` bypasses the slot->slug lookup (used for eval judges).
    """
    cfg = config or load_config()
    model = model_override or cfg.models.slug(slot)
    sampling = cfg.sampling.get(sampling_key)
    if sampling is None:
        raise KeyError(f"unknown sampling profile: {sampling_key!r}")

    start = time.monotonic()
    if is_mock():
        completion = _mock_response(slot)
        if prefill and response_prefix_strip:
            completion = _strip_echoed_prefill(completion, prefill)
        text = (prefill or "") + completion
        result = LLMResult(
            text=text,
            slot=slot,
            model=f"mock/{model}",
            prompt_tokens=sum(len(m.get("content", "")) for m in messages) // 4,
            completion_tokens=len(completion) // 4,
            cost=0.0,
            latency_s=0.0,
        )
        _log_call(result.model_dump() | {"mock": True, "text": None})
        return result

    sent_messages = list(messages)
    if prefill:
        sent_messages.append({"role": "assistant", "content": prefill})

    payload: dict[str, Any] = {"model": model, "messages": sent_messages}
    payload.update(sampling.to_payload())
    order = cfg.provider_pinning.get(slot)
    if order:
        payload["provider"] = {"order": order}

    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    resp = _post_with_retries(payload, headers)
    body = resp.json()
    completion = body["choices"][0]["message"].get("content") or ""
    if prefill and response_prefix_strip:
        completion = _strip_echoed_prefill(completion, prefill)
    usage = body.get("usage") or {}
    result = LLMResult(
        text=(prefill or "") + completion,
        slot=slot,
        model=body.get("model", model),
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        cost=usage.get("cost"),
        latency_s=round(time.monotonic() - start, 3),
    )
    _log_call(result.model_dump() | {"mock": False, "text": None})
    return result


TEMPLATES_DIR = Path("prompt_templates")


def render_template(name: str, **values: Any) -> str:
    """Load ``prompt_templates/<name>.md`` and substitute ``{key}`` placeholders.

    Only the provided keys are replaced, so literal braces elsewhere in a
    template (JSON examples, say) pass through untouched. Templates are data:
    code contains no prompt strings beyond trivial glue.
    """
    text = (TEMPLATES_DIR / f"{name}.md").read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_response(text: str) -> dict[str, Any] | None:
    """Best-effort strict-JSON extraction: strips code fences, finds the object."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned)
    match = _JSON_BLOCK.search(cleaned)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def cost_summary(run_dir: Path | str) -> dict[str, Any]:
    """Aggregate llm_log.jsonl for a run: calls, tokens, cost, per-stage breakdown."""
    log = Path(run_dir) / "llm_log.jsonl"
    summary: dict[str, Any] = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                               "cost": 0.0, "by_stage": {}}
    if not log.exists():
        return summary
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        stage = entry.get("stage") or "unknown"
        per = summary["by_stage"].setdefault(
            stage, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
        )
        for bucket in (summary, per):
            bucket["calls"] += 1
            bucket["prompt_tokens"] += entry.get("prompt_tokens") or 0
            bucket["completion_tokens"] += entry.get("completion_tokens") or 0
            bucket["cost"] += entry.get("cost") or 0.0
    return summary
