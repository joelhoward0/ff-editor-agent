"""Stateless "Sign in with Google" for the MCP server.

The server is its own OAuth authorization server (the MCP SDK provides the
endpoints) and hands the human part off to Google. Nothing is stored: every
piece of state (registered client, Google round-trip state, authorization
code, access and refresh tokens) is a sealed (encrypted + authenticated,
time-stamped) blob carried by the client. The Google refresh token rides
inside our tokens so tools can reach the user's Drive.

The sealing key is derived from GOOGLE_CLIENT_SECRET, so setup needs only the
Google client id and secret. Rotating that secret invalidates every session.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from typing import Any

import httpx
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
# drive.file: the app sees only files it created; nothing else in the user's Drive.
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
GOOGLE_SCOPES = f"openid email {DRIVE_SCOPE}"
CALLBACK_PATH = "/oauth/google/callback"

STATE_TTL = 600          # Google round trip
CODE_TTL = 120           # authorization code (stateless, so replayable until expiry; PKCE-bound)
ACCESS_TTL = 3600        # our access token
REFRESH_TTL = 30 * 86400  # our refresh token (sliding: each refresh issues a fresh one)

# Only Claude may receive codes: its hosted callback, or Claude Code's loopback
# redirect on any port. Without this, anyone could register their own redirect
# and phish a signed-in user into granting them these tools.
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
LOOPBACK = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?/")


def redirect_allowed(uri: str) -> bool:
    return uri == CLAUDE_CALLBACK or bool(LOOPBACK.match(uri))


def configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


class Sealer:
    """Encrypt-and-authenticate small JSON payloads tagged with a kind."""

    def __init__(self, secret: str):
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                   info=b"prose-forge sealed tokens v1").derive(secret.encode())
        self._f = Fernet(base64.urlsafe_b64encode(key))

    def seal(self, kind: str, data: dict[str, Any]) -> str:
        return self._f.encrypt(json.dumps({"k": kind, **data}).encode()).decode()

    def unseal(self, token: str, kind: str, ttl: int | None) -> dict[str, Any] | None:
        try:
            data = json.loads(self._f.decrypt(token.encode(), ttl=ttl))
        except (InvalidToken, ValueError, TypeError):
            return None
        return data if data.pop("k", None) == kind else None


class GoogleProvider:
    """OAuthAuthorizationServerProvider backed by sealed tokens and Google."""

    def __init__(self, base_url: str, resource_url: str, *, client_id: str, client_secret: str,
                 allowed_emails: set[str], transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.resource_url = resource_url
        self.client_id, self.client_secret = client_id, client_secret
        self.allowed = {e.strip().lower() for e in allowed_emails if e.strip()}
        self.sealer = Sealer(client_secret)
        self.transport = transport  # tests inject a fake Google here

    # -- clients (dynamic registration; the client_id IS the sealed record) --

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.redirect_uris or not all(
                redirect_allowed(str(u)) for u in client_info.redirect_uris):
            raise RegistrationError("invalid_redirect_uri", "only Claude may register")
        record = client_info.model_dump(mode="json", exclude={"client_id"}, exclude_none=True)
        client_info.client_id = self.sealer.seal("client", record)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        record = self.sealer.unseal(client_id, "client", ttl=None)
        # re-check on every load, so no client minted under looser rules survives
        if record is None or not all(redirect_allowed(u) for u in record.get("redirect_uris", [])):
            return None
        return OAuthClientInformationFull.model_validate({**record, "client_id": client_id})

    # -- authorize: bounce through Google, come back to our callback --

    async def authorize(self, client: OAuthClientInformationFull,
                        params: AuthorizationParams) -> str:
        if params.resource and params.resource.rstrip("/") != self.resource_url.rstrip("/"):
            raise AuthorizeError("invalid_target", "unknown resource")
        state = self.sealer.seal("state", {
            "client_id": client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "explicit": params.redirect_uri_provided_explicitly,
            "challenge": params.code_challenge,
            "scopes": params.scopes or [],
            "resource": params.resource,
            "state": params.state,
        })
        return construct_redirect_uri(
            GOOGLE_AUTH, client_id=self.client_id, redirect_uri=self.base_url + CALLBACK_PATH,
            response_type="code", scope=GOOGLE_SCOPES, access_type="offline",
            prompt="consent", include_granted_scopes="true", state=state,
        )

    async def google_callback(self, request: Request) -> Response:
        st = self.sealer.unseal(request.query_params.get("state", ""), "state", ttl=STATE_TTL)
        if st is None:
            return PlainTextResponse("Sign-in expired or invalid. Start again from Claude.", 400)
        if request.query_params.get("error") or not request.query_params.get("code"):
            return RedirectResponse(construct_redirect_uri(
                st["redirect_uri"], error="access_denied", state=st["state"]), 302)
        async with httpx.AsyncClient(transport=self.transport, timeout=15) as http:
            tok = await http.post(GOOGLE_TOKEN, data={
                "code": request.query_params["code"], "client_id": self.client_id,
                "client_secret": self.client_secret, "grant_type": "authorization_code",
                "redirect_uri": self.base_url + CALLBACK_PATH,
            })
            if tok.status_code != 200:
                return PlainTextResponse("Google sign-in failed. Try again.", 502)
            gtok = tok.json()
            granted = set(gtok.get("scope", "").split())
            if DRIVE_SCOPE not in granted or not gtok.get("refresh_token"):
                return PlainTextResponse(
                    "prose-forge needs Drive access (only to files it creates). "
                    "Start again from Claude and allow it.", 400)
            info = await http.get(
                GOOGLE_USERINFO, headers={"Authorization": f"Bearer {gtok['access_token']}"})
        email = (info.json().get("email") or "").lower() if info.status_code == 200 else ""
        if not (email and info.json().get("email_verified")) or email not in self.allowed:
            return PlainTextResponse(
                "This Google account isn't allowed to use prose-forge yet.", 403)
        code = self.sealer.seal("code", {**st, "email": email, "g": gtok["refresh_token"]})
        return RedirectResponse(construct_redirect_uri(st["redirect_uri"], code=code,
                                                       state=st["state"]), 302)

    # -- codes and tokens --

    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> AuthorizationCode | None:
        d = self.sealer.unseal(authorization_code, "code", ttl=CODE_TTL)
        if d is None:  # the SDK checks the code belongs to this client
            return None
        return AuthorizationCode(
            code=authorization_code, scopes=d["scopes"], expires_at=time.time() + CODE_TTL,
            client_id=d["client_id"], code_challenge=d["challenge"], redirect_uri=d["redirect_uri"],
            redirect_uri_provided_explicitly=d["explicit"], resource=d["resource"],
            subject=d["email"],
        )

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        d = self.sealer.unseal(authorization_code.code, "code", ttl=CODE_TTL)
        if d is None:
            raise TokenError("invalid_grant", "authorization code expired")
        return self._issue(client.client_id, d["scopes"], d["email"], d["g"], d["resource"])

    async def load_refresh_token(self, client: OAuthClientInformationFull,
                                 refresh_token: str) -> RefreshToken | None:
        d = self.sealer.unseal(refresh_token, "refresh", ttl=REFRESH_TTL)
        if d is None or d["email"] not in self.allowed:  # SDK checks client and scopes
            return None
        return RefreshToken(token=refresh_token, client_id=d["client_id"], scopes=d["scopes"],
                            subject=d["email"])

    async def exchange_refresh_token(self, client: OAuthClientInformationFull,
                                     refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        d = self.sealer.unseal(refresh_token.token, "refresh", ttl=REFRESH_TTL)
        if d is None:
            raise TokenError("invalid_grant", "refresh token expired")
        # If Google revoked Drive access, fail the refresh so Claude asks to sign in
        # again, rather than issuing tokens every Drive call would then reject.
        async with httpx.AsyncClient(transport=self.transport, timeout=15) as http:
            try:
                await google_access_token(d["g"], http)
            except PermissionError as exc:
                raise TokenError("invalid_grant", "Google access was revoked") from exc
            except (RuntimeError, httpx.HTTPError, ValueError):
                pass  # Google briefly unavailable: don't log the user out for it
        return self._issue(client.client_id, scopes, d["email"], d["g"], d["resource"])

    async def load_access_token(self, token: str) -> AccessToken | None:
        d = self.sealer.unseal(token, "access", ttl=ACCESS_TTL)
        if d is None or d["email"] not in self.allowed:
            return None
        return AccessToken(token=token, client_id=d["client_id"], scopes=d["scopes"],
                           expires_at=d["exp"], resource=d["resource"], subject=d["email"],
                           claims={"google_refresh": d["g"]})

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        return None  # ponytail: stateless, so tokens expire rather than revoke; Google-side
        # revocation (myaccount.google.com/permissions) cuts off Drive immediately.

    def _issue(self, client_id: str, scopes: list[str], email: str, g: str,
               resource: str | None) -> OAuthToken:
        base = {"client_id": client_id, "scopes": scopes, "email": email, "g": g,
                "resource": resource or self.resource_url}
        return OAuthToken(
            access_token=self.sealer.seal("access", {**base, "exp": int(time.time()) + ACCESS_TTL}),
            token_type="Bearer", expires_in=ACCESS_TTL,
            refresh_token=self.sealer.seal("refresh", base),
            scope=" ".join(scopes) or None,
        )


# -- Google access tokens for Drive calls (per-process cache) --

_access_cache: dict[str, tuple[str, float]] = {}


async def google_access_token(refresh_token: str, http: httpx.AsyncClient) -> str:
    hit = _access_cache.get(refresh_token)
    if hit and hit[1] > time.time() + 60:
        return hit[0]
    r = await http.post(GOOGLE_TOKEN, data={
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "grant_type": "refresh_token", "refresh_token": refresh_token,
    })
    if r.status_code == 400 and r.json().get("error") == "invalid_grant":
        raise PermissionError("Google access was revoked or expired; sign in to prose-forge again.")
    if r.status_code != 200:
        raise RuntimeError("Google is unavailable right now; try again shortly.")
    body = r.json()
    expires = time.time() + body.get("expires_in", 3600)
    _access_cache[refresh_token] = (body["access_token"], expires)
    return body["access_token"]
