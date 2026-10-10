"""The author's voice files, kept in a "prose-forge" folder in THEIR Google Drive.

Uses the drive.file scope, so the server can see only files it created. Files
are plain text the author can open, edit or delete: voice-card.md,
style-profile.json, voice-ledger.md, samples.md, controls.md.

The ledger is human-readable markdown; each entry starts with an HTML comment
carrying its machine fields, and texts sit in ~~~ fences so they parse back.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import httpx

DRIVE = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER = "prose-forge"
FOLDER_MIME = "application/vnd.google-apps.folder"
NAMES = {
    "voice-card": "voice-card.md",
    "style-profile": "style-profile.json",
    "ledger": "voice-ledger.md",
    "samples": "samples.md",
    "controls": "controls.md",
}
LEDGER_HEAD = (
    "# Voice ledger\n\nEvery pick you made in the prose-forge picker. Claude reads the recent "
    "ones before drafting; versions you rejected teach the checker what imitation looks like, "
    "and text you edited counts as your own writing. Edit or delete entries freely.\n"
)


class Vault:
    def __init__(self, access_token: str, http: httpx.AsyncClient):
        self.http = http
        self.auth = {"Authorization": f"Bearer {access_token}"}
        self._folder: str | None = None
        self._ids: dict[str, str | None] = {}

    async def _req(self, method: str, url: str, **kw: Any) -> httpx.Response:
        headers = {**self.auth, **kw.pop("headers", {})}
        r = await self.http.request(method, url, headers=headers, **kw)
        if r.status_code in (401, 403):
            raise PermissionError("Google Drive refused access; sign in to prose-forge again.")
        r.raise_for_status()
        return r

    async def _find(self, name: str, parent: str | None, mime: str | None = None) -> str | None:
        q = f"name = '{name}' and trashed = false"
        q += f" and '{parent}' in parents" if parent else ""
        q += f" and mimeType = '{mime}'" if mime else ""
        r = await self._req("GET", DRIVE, params={"q": q, "fields": "files(id)", "pageSize": 1})
        files = r.json().get("files", [])
        return files[0]["id"] if files else None

    async def folder(self) -> str:
        if self._folder is None:
            self._folder = await self._find(FOLDER, None, FOLDER_MIME)
        if self._folder is None:
            r = await self._req("POST", DRIVE, json={"name": FOLDER, "mimeType": FOLDER_MIME},
                                params={"fields": "id"})
            self._folder = r.json()["id"]
        return self._folder

    async def _id(self, kind: str) -> str | None:
        if kind not in self._ids:
            self._ids[kind] = await self._find(NAMES[kind], await self.folder())
        return self._ids[kind]

    async def read(self, kind: str) -> str | None:
        file_id = await self._id(kind)
        if file_id is None:
            return None
        r = await self._req("GET", f"{DRIVE}/{file_id}", params={"alt": "media"})
        return r.content.decode("utf-8")

    async def write(self, kind: str, text: str) -> None:
        mime = "application/json" if NAMES[kind].endswith(".json") else "text/markdown"
        file_id = await self._id(kind)
        if file_id:
            await self._req("PATCH", f"{UPLOAD}/{file_id}", params={"uploadType": "media"},
                            content=text.encode("utf-8"), headers={"Content-Type": mime})
            return
        meta = {"name": NAMES[kind], "parents": [await self.folder()], "mimeType": mime}
        boundary = "prose-forge-boundary"
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(meta)}\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n"
        ).encode() + text.encode("utf-8") + f"\r\n--{boundary}--".encode()
        r = await self._req("POST", UPLOAD, params={"uploadType": "multipart", "fields": "id"},
                            content=body,
                            headers={"Content-Type": f"multipart/related; boundary={boundary}"})
        self._ids[kind] = r.json()["id"]


# -- ledger format --

_ENTRY = re.compile(r"<!-- pick (\{.*?\}) -->\n(.*?)(?=<!-- pick |\Z)", re.S)
_FENCE = re.compile(r"~~~\n(.*?)\n~~~", re.S)


def _fence(text: str) -> str:
    return "~~~\n" + text.replace("~~~", "~ ~ ~").strip() + "\n~~~"


def format_entry(e: dict[str, Any]) -> str:
    meta = {k: e[k] for k in ("id", "chosen", "edited", "claude_written")}
    labels = [chr(65 + i) for i in range(len(e["passages"]))]
    when = e.get("when") or datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"<!-- pick {json.dumps(meta)} -->", f"## {when} · {e.get('context') or 'pick'}"]
    if e["chosen"] is None:
        lines.append("**None of these** sounded like the author.")
    else:
        others = [lb for i, lb in enumerate(labels) if i != e["chosen"]]
        edited = " (edited by the author)" if e["edited"] else ""
        lines.append(f"**Chose {labels[e['chosen']]}**{edited} over {', '.join(others) or '-'}.")
    if e.get("why"):
        lines.append(f"Why: {e['why'].strip()}")
    if e["chosen"] is not None:
        lines += ["", "Chosen:", _fence(e["chosen_text"])]
    rejected = [p for i, p in enumerate(e["passages"]) if i != e["chosen"]]
    if rejected:
        lines += ["", "Rejected:"] + [_fence(p) for p in rejected]
    return "\n".join(lines) + "\n\n"


def parse_ledger(md: str | None) -> list[dict[str, Any]]:
    out = []
    for meta_json, body in _ENTRY.findall(md or ""):
        try:
            meta = json.loads(meta_json)
        except ValueError:
            continue
        chosen_part, _, rejected_part = body.partition("\nRejected:\n")
        chosen = _FENCE.findall(chosen_part.partition("\nChosen:\n")[2])
        why = re.search(r"^Why: (.*)$", body, re.M)
        out.append({
            **meta,
            "heading": body.splitlines()[0].lstrip("# ").strip() if body.strip() else "",
            "why": why.group(1) if why else "",
            "chosen_text": chosen[0] if chosen else None,
            "rejected": _FENCE.findall(rejected_part),
        })
    return out


def upsert_entry(md: str | None, entry: dict[str, Any]) -> str:
    """Append an entry, replacing any earlier entry with the same id (a changed pick)."""
    md = md or LEDGER_HEAD
    kept = [m.group(0) for m in _ENTRY.finditer(md)
            if json.loads(m.group(1)).get("id") != entry["id"]]
    head = md[: m.start()] if (m := _ENTRY.search(md)) else md
    return head.rstrip() + "\n\n" + "".join(kept) + format_entry(entry)
