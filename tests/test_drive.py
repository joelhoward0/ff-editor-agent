"""Signed-in endpoint, end to end: OAuth (register -> Google -> callback ->
token with PKCE), then tools reading and writing a fake Google Drive."""

import base64
import hashlib
import json
import re
import secrets
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import anyio
import httpx
import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import remote_server  # noqa: E402

CORPUS = "\n\n".join(p.read_text() for p in (ROOT / "tests/fixtures/corpus").glob("*.md")) * 3
SLOP = "Marisole couldn't help but smile. A testament to how far she had come.\n\n" * 200
EMAIL = "joel@example.com"


class FakeGoogle:
    """Google OAuth + userinfo + the slice of Drive v3 the vault uses."""

    def __init__(self, email=EMAIL):
        self.email, self.files, self.n = email, {}, 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        url, p = str(req.url), req.url.params
        if url.startswith("https://oauth2.googleapis.com/token"):
            form = parse_qs(req.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                assert form["code"] == ["gcode"] and form["client_secret"] == ["gsecret"]
                return httpx.Response(200, json={
                    "access_token": "gacc", "refresh_token": "grefresh", "expires_in": 3600,
                    "scope": "openid email https://www.googleapis.com/auth/drive.file"})
            assert form["refresh_token"] == ["grefresh"]
            return httpx.Response(200, json={"access_token": "gacc2", "expires_in": 3600})
        if url.startswith("https://openidconnect.googleapis.com/v1/userinfo"):
            return httpx.Response(200, json={"email": self.email, "email_verified": True})
        if req.headers.get("Authorization") not in ("Bearer gacc", "Bearer gacc2"):
            return httpx.Response(401)
        path = req.url.path
        if req.method == "GET" and path == "/drive/v3/files":
            name = re.search(r"name = '([^']*)'", p["q"]).group(1)
            parent = re.search(r"'([^']*)' in parents", p["q"])
            hits = [{"id": i} for i, f in self.files.items() if f["name"] == name
                    and (not parent or parent.group(1) in f["parents"])]
            return httpx.Response(200, json={"files": hits[:1]})
        if req.method == "POST" and path == "/drive/v3/files":
            return httpx.Response(200, json={"id": self._new(json.loads(req.content), "")})
        if req.method == "POST" and path == "/upload/drive/v3/files":
            boundary = req.headers["Content-Type"].split("boundary=")[1]
            parts = req.content.decode().split(f"--{boundary}")
            meta = json.loads(parts[1].split("\r\n\r\n", 1)[1].strip())
            content = parts[2].split("\r\n\r\n", 1)[1].removesuffix("\r\n")
            return httpx.Response(200, json={"id": self._new(meta, content)})
        file_id = path.rsplit("/", 1)[1]
        if req.method == "GET" and p.get("alt") == "media":
            return httpx.Response(200, content=self.files[file_id]["content"].encode())
        if req.method == "PATCH":
            self.files[file_id]["content"] = req.content.decode()
            return httpx.Response(200, json={"id": file_id})
        return httpx.Response(404)

    def _new(self, meta, content):
        self.n += 1
        self.files[f"f{self.n}"] = {"name": meta["name"], "parents": meta.get("parents", []),
                                    "content": content}
        return f"f{self.n}"

    def text(self, name):
        return next(f["content"] for f in self.files.values() if f["name"] == name)


@pytest.fixture
def server(monkeypatch):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    base = f"http://127.0.0.1:{port}"
    google = FakeGoogle()
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "gclient")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "gsecret")
    monkeypatch.setenv("ALLOWED_EMAILS", f" {EMAIL.upper()} ,someone@else.com")
    monkeypatch.setattr(remote_server, "PUBLIC_URL", base)
    monkeypatch.setattr(remote_server, "HTTP_TRANSPORT", httpx.MockTransport(google))
    monkeypatch.setattr(remote_server, "REBUILD_EVERY", 2)
    remote_server.signin._access_cache.clear()
    srv = uvicorn.Server(uvicorn.Config(remote_server.build_app(), port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    yield base, google
    srv.should_exit = True


def sign_in(base: str) -> str:
    """Drive the OAuth flow the way Claude does; return our access token."""
    redirect = "http://localhost/callback"
    with httpx.Client(base_url=base) as c:
        reg = c.post("/register", json={"redirect_uris": [redirect], "client_name": "test",
                                        "token_endpoint_auth_method": "none",
                                        "grant_types": ["authorization_code", "refresh_token"],
                                        "response_types": ["code"]})
        assert reg.status_code == 201, reg.text
        client_id = reg.json()["client_id"]
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        auth = c.get("/authorize", params={
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect,
            "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz",
            "resource": base + "/drive/mcp"})
        assert auth.status_code == 302, auth.text
        google_url = urlparse(auth.headers["location"])
        assert google_url.netloc == "accounts.google.com"
        gq = parse_qs(google_url.query)
        assert gq["scope"] == ["openid email https://www.googleapis.com/auth/drive.file"]
        cb = c.get("/oauth/google/callback", params={"code": "gcode", "state": gq["state"][0]})
        assert cb.status_code == 302, cb.text
        back = parse_qs(urlparse(cb.headers["location"]).query)
        assert back["state"] == ["xyz"]
        tok = c.post("/token", data={
            "grant_type": "authorization_code", "code": back["code"][0], "redirect_uri": redirect,
            "client_id": client_id, "code_verifier": verifier, "resource": base + "/drive/mcp"})
        assert tok.status_code == 200, tok.text
        body = tok.json()
        # wrong verifier is refused (PKCE)
        bad = c.post("/token", data={
            "grant_type": "authorization_code", "code": back["code"][0], "redirect_uri": redirect,
            "client_id": client_id, "code_verifier": "x" * 50})
        assert bad.status_code == 400
        # refresh works and yields a fresh access token
        ref = c.post("/token", data={"grant_type": "refresh_token", "client_id": client_id,
                                     "refresh_token": body["refresh_token"]})
        assert ref.status_code == 200 and ref.json()["access_token"]
        return body["access_token"]


def test_signed_in_endpoint_end_to_end(server):
    base, google = server
    token = sign_in(base)

    with httpx.Client(base_url=base) as c:
        # no token / tampered token -> 401 pointing at our resource metadata
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"}}}
        hdr = {"Accept": "application/json, text/event-stream"}
        r = c.post("/drive/mcp", json=init, headers=hdr)
        assert r.status_code == 401 and "resource_metadata" in r.headers["www-authenticate"]
        r = c.post("/drive/mcp", json=init,
                   headers={**hdr, "Authorization": "Bearer " + token[:-4] + "AAAA"})
        assert r.status_code == 401
        # the open endpoint still works without any sign-in
        assert c.post("/mcp", json=init, headers=hdr).status_code == 200
        assert c.get("/drive/mcp").status_code == 405

    async def run():
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
        async with http, streamable_http_client(base + "/drive/mcp", http_client=http) as (r, w), \
                ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            assert {"load_voice", "save_voice_file", "record_pick", "triage_scenes"} <= names

            async def call(tool, **args):
                res = await s.call_tool(tool, args)
                assert not res.is_error, res.content
                return json.loads(res.content[0].text)

            first = await call("load_voice")
            assert first["voice_card"] is None and first["picks_total"] == 0
            await call("save_voice_file", kind="voice-card", content="# Voice card\nPlain, warm.")
            built = await call("build_style_profile", samples=CORPUS, controls=SLOP)
            assert "profile" not in built and built["imitation_tells"]
            assert json.loads(google.text("style-profile.json"))["ledger_entries"] == 0
            lint = await call("check_draft", text=SLOP)
            # the saved profile is applied (a voice verdict needs one) and not fooled
            assert lint["voice"]["verdict"] != "reads like the author"

            passages = ["There was a vote.", "He voted, but she voted first."]
            await call("record_pick", pick_id="p1", passages=passages, chosen=0)
            await call("record_pick", pick_id="p1", passages=passages, chosen=1,
                       chosen_text="He voted, and she voted first.", why="and-chain")
            ledger = google.text("voice-ledger.md")
            assert ledger.count("<!-- pick ") == 1 and "(edited by the author)" in ledger
            await call("record_pick", pick_id="p2", passages=passages, chosen=None, why="meh")

            voice_now = await call("load_voice")
            assert voice_now["voice_card"].startswith("# Voice card")
            assert voice_now["picks_total"] == 2
            assert voice_now["recent_picks"][0]["why"] == "and-chain"
            assert voice_now["retrained"] == "Profile retrained from 2 picks."
            assert json.loads(google.text("style-profile.json"))["ledger_entries"] == 2

            bad = await s.call_tool("save_voice_file", {"kind": "style-profile", "content": "{}"})
            assert bad.is_error  # not a prose-forge profile

    anyio.run(run)
    folder = [f for f in google.files.values() if f["name"] == "prose-forge"]
    assert len(folder) == 1  # created once, reused


def test_unlisted_google_account_is_refused(server, monkeypatch):
    base, google = server
    google.email = "stranger@example.com"
    with pytest.raises(AssertionError, match="403|isn't allowed"):
        sign_in(base)


def test_open_only_when_google_not_configured(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    app = remote_server.build_app()
    assert not any(getattr(r, "path", "") == "/drive/mcp" for r in app.router.routes)
