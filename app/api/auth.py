"""Six-digit email-code sign-in through Supabase Auth, then server-owned sessions.

Supabase only proves the email code. The server checks the returned token and the one
allowed user id, then issues its own random session cookie; only a hash of it is stored.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib import error, request

import jwt

from app.api.settings import Settings

COOKIE_NAME = "ka_session"
RENEW_AFTER = timedelta(hours=1)


class AuthError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SupabaseAuth:
    """The two Supabase Auth calls the sign-in screens need."""

    def __init__(self, settings: Settings, opener: Callable[..., Any] = request.urlopen) -> None:
        self.settings = settings
        self.opener = opener
        self._jwks = jwt.PyJWKClient(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json")

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        http_request = request.Request(
            f"{self.settings.supabase_url}/auth/v1{path}", data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"apikey": self.settings.supabase_publishable_key, "Content-Type": "application/json"},
        )
        try:
            with self.opener(http_request, timeout=15) as response:
                return json.loads(response.read() or b"{}")
        except error.HTTPError as exc:
            raise AuthError(401 if exc.code in (400, 401, 403, 422) else 502, "code expired or wrong") from None
        except (error.URLError, TimeoutError, ValueError):
            raise AuthError(502, "sign-in service unavailable") from None

    def send_code(self, email: str) -> None:
        # create_user false: sign-ups stay closed even if the project setting is wrong.
        self._post("/otp", {"email": email, "create_user": False})

    def verify_code(self, email: str, code: str) -> str:
        reply = self._post("/verify", {"type": "email", "email": email, "token": code})
        token = reply.get("access_token")
        if not isinstance(token, str):
            raise AuthError(401, "code expired or wrong")
        return self.user_id(token)

    def user_id(self, token: str) -> str:
        try:
            if self.settings.supabase_jwt_secret:
                # Projects still on the legacy shared secret sign with HS256.
                key, algorithms = self.settings.supabase_jwt_secret, ["HS256"]
            else:
                key, algorithms = self._jwks.get_signing_key_from_jwt(token).key, ["ES256", "RS256"]
            claims = jwt.decode(token, key, algorithms=algorithms, audience="authenticated",
                                issuer=f"{self.settings.supabase_url}/auth/v1",
                                options={"require": ["exp", "sub", "iss", "aud"]})
        except jwt.ExpiredSignatureError:
            raise AuthError(401, "code expired or wrong") from None
        except jwt.PyJWTError:
            raise AuthError(401, "sign-in token is invalid") from None
        if claims["sub"] != self.settings.allowed_user_id:
            raise AuthError(403, "not authorised")
        return claims["sub"]


@dataclass
class Session:
    token_sha256: str
    expires_at: datetime
    renewed: bool = False


class SessionStore:
    def __init__(self, connect: Callable[[], Any], days: int) -> None:
        self.connect = connect
        self.lifetime = timedelta(days=days)

    def create(self) -> tuple[str, datetime]:
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + self.lifetime
        with self.connect() as conn:
            conn.execute("insert into knowledge.sessions (token_sha256, expires_at) values (%s, %s)",
                         (token_hash(token), expires))
        return token, expires

    def check(self, token: str | None) -> Session:
        if not token:
            raise AuthError(401, "sign in required")
        digest = token_hash(token)
        now = datetime.now(timezone.utc)
        with self.connect() as conn:
            row = conn.execute("select expires_at, last_used_at from knowledge.sessions where token_sha256=%s",
                               (digest,)).fetchone()
            if row is None or row[0] <= now:
                raise AuthError(401, "sign in required")
            expires, renewed = row[0], False
            if now - row[1] > RENEW_AFTER:
                expires, renewed = now + self.lifetime, True
                conn.execute("update knowledge.sessions set last_used_at=%s, expires_at=%s where token_sha256=%s",
                             (now, expires, digest))
        return Session(digest, expires, renewed)

    def delete(self, token_sha256: str) -> None:
        with self.connect() as conn:
            conn.execute("delete from knowledge.sessions where token_sha256=%s", (token_sha256,))

    def delete_all(self) -> None:
        with self.connect() as conn:
            conn.execute("delete from knowledge.sessions")


class RateLimiter:
    """A small in-memory limit per client address for the public sign-in routes."""

    def __init__(self, limit: int = 5, window_seconds: float = 60, max_keys: int = 10_000) -> None:
        self.limit, self.window, self.max_keys = limit, window_seconds, max_keys
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._hits) >= self.max_keys:
                # Drop addresses with no recent attempts so the table cannot grow without bound.
                for stale in [name for name, items in self._hits.items() if not items or now - items[-1] > self.window]:
                    del self._hits[stale]
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                raise AuthError(429, "too many attempts; wait a minute")
            hits.append(now)
