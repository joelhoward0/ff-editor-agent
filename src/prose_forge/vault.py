"""The author's voice files, kept in a "prose-forge" folder in THEIR Google Drive.

Uses the drive.file scope, so the server can see only files it created. Files
are plain text the author can open, edit or delete: voice-card.md,
style-profile.json, voice-ledger.md, samples.md, controls.md.

Ledger entries keep their data in a hidden comment (JSON) above a readable
rendering; the comment is the source of truth, so the file always parses back
no matter what prose, or hand edits, it contains.
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
    "# Voice ledger\n\nEvery pick you made in the prose-forge picker, newest last. Claude reads "
    "the recent ones before drafting; versions you rejected teach the checker what imitation "
    "looks like, and text you edited counts as your own writing. To remove an entry, delete it "
    "along with the hidden comment line above it. Editing the visible text changes nothing.\n"
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
        if r.status_code == 401:
            raise PermissionError("Google Drive refused access; sign in to prose-forge again.")
        if r.status_code >= 400:  # 403 is usually a rate limit; don't echo request details
            raise RuntimeError(f"Google Drive error {r.status_code}; try again shortly.")
        return r

    async def _find(self, name: str, parent: str | None, mime: str | None = None) -> str | None:
        q = f"name = '{name}' and trashed = false"
        q += f" and '{parent}' in parents" if parent else ""
        q += f" and mimeType = '{mime}'" if mime else ""
        # oldest first, so a duplicate from a racing first write never wins
        r = await self._req("GET", DRIVE, params={"q": q, "fields": "files(id)", "pageSize": 1,
                                                  "orderBy": "createdTime"})
        files = r.json().get("files", [])
        return files[0]["id"] if files else None

    async def _create(self, meta: dict[str, Any]) -> str:
        r = await self._req("POST", DRIVE, json=meta, params={"fields": "id"})
        return r.json()["id"]

    async def folder(self) -> str:
        if self._folder is None:
            self._folder = (await self._find(FOLDER, None, FOLDER_MIME)
                            or await self._create({"name": FOLDER, "mimeType": FOLDER_MIME}))
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
        return r.content.decode("utf-8-sig", "replace").replace("\r\n", "\n")

    async def write(self, kind: str, text: str) -> None:
        mime = "application/json" if NAMES[kind].endswith(".json") else "text/markdown"
        file_id = await self._id(kind)
        if file_id is None:
            file_id = await self._create(
                {"name": NAMES[kind], "parents": [await self.folder()], "mimeType": mime})
            self._ids[kind] = file_id
        await self._req("PATCH", f"{UPLOAD}/{file_id}", params={"uploadType": "media"},
                        content=text.encode("utf-8"), headers={"Content-Type": mime})


# -- ledger format --

_ENTRY = re.compile(r"^<!-- pick (\{.*\}) -->$", re.M)
_FIELDS = ("id", "when", "context", "why", "passages", "chosen", "chosen_text", "edited",
           "claude_written")


def _line(text: str) -> str:
    return " ".join((text or "").split())


def _shown(text: str) -> str:
    """Readable copy of prose; can't be mistaken for an entry marker."""
    return "\n".join("> " + ln for ln in text.strip().replace("<!--", "<!‐‐").splitlines())


def format_entry(e: dict[str, Any]) -> str:
    # > for ">" keeps "-->" out of the JSON, so the comment can't be closed early
    data = json.dumps({k: e.get(k) for k in _FIELDS}, ensure_ascii=False).replace(">", "\\u003e")
    labels = [chr(65 + i) for i in range(len(e["passages"]))]
    lines = [f"<!-- pick {data} -->", f"## {e['when']} · {_line(e.get('context')) or 'pick'}"]
    if e["chosen"] is None:
        lines.append("**None of these** sounded like the author.")
    else:
        others = [lb for i, lb in enumerate(labels) if i != e["chosen"]]
        edited = " (edited by the author)" if e["edited"] else ""
        lines.append(f"**Chose {labels[e['chosen']]}**{edited} over {', '.join(others) or '-'}.")
    if e.get("why"):
        lines.append(f"Why: {_line(e['why'])}")
    if e["chosen"] is not None:
        lines += ["", "Chosen:", _shown(e["chosen_text"] or e["passages"][e["chosen"]])]
    rejected = [p for i, p in enumerate(e["passages"]) if i != e["chosen"]]
    if rejected:
        lines += ["", "Rejected:"] + [_shown(p) + "\n" for p in rejected]
    return "\n".join(lines).rstrip() + "\n\n"


def parse_ledger(md: str | None) -> list[dict[str, Any]]:
    out = []
    for m in _ENTRY.finditer((md or "").replace("\r\n", "\n")):
        try:
            e = json.loads(m.group(1))
        except ValueError:
            continue  # a hand-mangled entry is skipped, never fatal
        ok = (isinstance(e, dict) and e.get("id") and isinstance(e.get("passages"), list)
              and (e.get("chosen") is None
                   or (type(e["chosen"]) is int and 0 <= e["chosen"] < len(e["passages"]))))
        if ok:
            e["rejected"] = [p for i, p in enumerate(e["passages"]) if i != e.get("chosen")]
            out.append(e)
    return out


def upsert_entry(md: str | None, entry: dict[str, Any]) -> str:
    """Rewrite the ledger with this entry last, replacing any earlier entry with
    the same id (a changed pick)."""
    entry = {**entry, "when": entry.get("when") or datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")}
    entries = [e for e in parse_ledger(md) if e["id"] != entry["id"]] + [entry]
    return LEDGER_HEAD + "\n" + "".join(format_entry(e) for e in entries)
