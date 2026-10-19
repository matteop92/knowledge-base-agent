"""The private web app's HTTP API and static shell."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Literal

import psycopg
from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.api.answers import AnswerEngine, AnswerJobs, OpenRouterAnswerEngine
from app.api.capture import CaptureJobs, Classifier
from app.api.auth import COOKIE_NAME, AuthError, RateLimiter, Session, SessionStore, SupabaseAuth
from app.api.conversations import ConversationError, ConversationStore
from app.api.scheduler import IndexScheduler, vault_settings_from_env
from app.api.settings import Settings
from app.capture.notes import CaptureError, draft_from_markdown, draft_from_text
from app.storage.spend import SpendCapError, spent_today

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                "connect-src 'self'; manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; "
                                "base-uri 'none'; form-action 'self'; object-src 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
log = logging.getLogger("knowledge.api")


class CodeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class VerifyRequest(CodeRequest):
    code: str = Field(pattern=r"^\d{6}$")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class FeedbackRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class CaptureRequest(BaseModel):
    kind: Literal["text", "markdown"]
    content: str = Field(min_length=1, max_length=2_000_000)
    title: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=2000, pattern=r"^https?://")
    tags: list[str] = Field(default_factory=list, max_length=10)


def create_app(settings: Settings, *, connect: Callable[[], Any] | None = None,
               auth: SupabaseAuth | None = None, engine: AnswerEngine | None = None,
               indexer: IndexScheduler | None = None, classifier: Classifier | None = None) -> FastAPI:
    # A stalled database must fail the request, not hang it: 10 s to connect, 60 s per statement.
    connect = connect or (lambda: psycopg.connect(settings.database_url, autocommit=True, connect_timeout=10,
                                                  options="-c statement_timeout=60000"))
    auth = auth or SupabaseAuth(settings)
    sessions = SessionStore(connect, settings.session_days)
    engine = engine or OpenRouterAnswerEngine(settings, connect)
    if engine.worst_case_usd() > settings.answer.max_usd_per_answer:
        raise RuntimeError("the answer budgets allow more than KNOWLEDGE_MAX_USD_PER_ANSWER; lower them or raise the limit")
    store = ConversationStore(connect, timedelta(seconds=engine.limits.max_seconds))
    jobs = AnswerJobs(store, engine, connect, settings)
    limiter = RateLimiter()
    # Across all addresses, so spreading guesses over many addresses cannot brute-force a six-digit code.
    verify_limiter = RateLimiter(limit=10, window_seconds=900)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.jobs = jobs
    if indexer is None and settings.sync_enabled:
        indexer = IndexScheduler(vault_settings_from_env(), connect, interval_seconds=settings.index_interval_seconds,
                                 max_usd_per_pass=float(settings.index_max_usd_per_pass))
    app.state.indexer = indexer
    captures = CaptureJobs(connect, settings, classifier, indexer)
    app.state.captures = captures

    @app.on_event("startup")
    def interrupt_running_turns() -> None:
        # Only turns past the time limit: during a deploy the old server may still be answering newer ones.
        count = jobs.expire_stale()
        if count:
            log.info("marked %d running turns as interrupted", count)
        count = captures.expire_stale()
        if count:
            log.info("marked %d running captures as interrupted", count)
        if indexer is not None:
            indexer.start()

    @app.on_event("shutdown")
    def finish_running_answers() -> None:
        # Give in-flight answers time to finish and record their spend before the process exits.
        if indexer is not None:
            indexer.stop()
        jobs.wait(engine.limits.max_seconds + 30)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError):
        # Same {"error": ...} shape as every other failure; input values are never echoed back.
        fields = sorted({".".join(str(part) for part in error["loc"][1:]) or "body" for error in exc.errors()})
        return JSONResponse({"error": "invalid request: " + ", ".join(fields)}, status_code=422)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # JSON-only writes: a cross-site form post cannot send this content type without a preflight.
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith(("/api/", "/auth/")):
            if not request.headers.get("content-type", "").startswith("application/json"):
                return _security(JSONResponse({"error": "expected JSON"}, status_code=415))
        return _security(await call_next(request))

    @app.exception_handler(AuthError)
    async def auth_error(_: Request, exc: AuthError):
        response = JSONResponse({"error": exc.message}, status_code=exc.status)
        if exc.status == 401:
            response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.exception_handler(ConversationError)
    async def conversation_error(_: Request, exc: ConversationError):
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    @app.exception_handler(SpendCapError)
    async def cap_error(_: Request, exc: SpendCapError):
        return JSONResponse({"error": "daily spending cap reached", "cap_usd": str(settings.daily_cap_usd),
                             "resets": "midnight UTC"}, status_code=429)

    def set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(COOKIE_NAME, token, max_age=settings.session_days * 86400, path="/",
                            httponly=True, secure=settings.cookie_secure, samesite="lax")

    def session(request: Request, response: Response) -> Session:
        token = request.cookies.get(COOKIE_NAME)
        current = sessions.check(token)
        if current.renewed:
            # Renewal must reach the browser too, or the cookie still expires 30 days after sign-in.
            set_session_cookie(response, token)
        return current

    def client_key(request: Request) -> str:
        # The proxy appends the real address last; earlier entries are whatever the client sent.
        forwarded = [item.strip() for item in request.headers.get("x-forwarded-for", "").split(",") if item.strip()]
        return forwarded[-1] if forwarded else (request.client.host if request.client else "unknown")

    # Public routes ---------------------------------------------------------------------------
    @app.get("/healthz")
    def healthz():
        try:
            with connect() as conn:
                conn.execute("select 1")
            return {"status": "ok"}
        except Exception:  # noqa: BLE001
            return JSONResponse({"status": "fail"}, status_code=503)

    @app.post("/auth/code")
    def send_code(body: CodeRequest, request: Request):
        limiter.check(client_key(request))
        # Only the allowed address gets a code; every caller sees the same reply.
        if body.email.strip().lower() == settings.allowed_email:
            try:
                auth.send_code(settings.allowed_email)
            except AuthError as exc:
                # Same reply either way, so an outage cannot reveal which address is allowed.
                log.warning("sign-in code request failed status=%s", exc.status)
        return {"status": "code sent if the address is allowed"}

    @app.post("/auth/verify")
    def verify_code(body: VerifyRequest, request: Request, response: Response):
        limiter.check(client_key(request))
        verify_limiter.check("verify")
        if body.email.strip().lower() != settings.allowed_email:
            # Same reply as a wrong code, so the reply never reveals which address is allowed.
            raise AuthError(401, "code expired or wrong")
        auth.verify_code(settings.allowed_email, body.code)
        token, _ = sessions.create()
        set_session_cookie(response, token)
        return {"status": "signed in"}

    # Signed-in routes ------------------------------------------------------------------------
    @app.get("/auth/me")
    def me(_: Session = Depends(session)):
        return {"status": "signed in"}

    @app.post("/auth/sign-out")
    def sign_out(response: Response, current: Session = Depends(session)):
        sessions.delete(current.token_sha256)
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"status": "signed out"}

    @app.post("/auth/sign-out-everywhere")
    def sign_out_everywhere(response: Response, _: Session = Depends(session)):
        sessions.delete_all()
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"status": "signed out everywhere"}

    @app.get("/health")
    def health(_: Session = Depends(session)):
        with connect() as conn:
            last_index = conn.execute("""select max(created_at) from knowledge.spend
                where source in ('indexing', 'bulk_load')""").fetchone()[0]
            failed_properties = conn.execute(
                "select count(*) from knowledge.document_properties where status='failed'").fetchone()[0]
            today = spent_today(conn)
        return {"database": "ok", "last_index_at": last_index.isoformat() if last_index else None,
                "properties_failures": failed_properties, "spent_today_usd": str(today),
                "index": indexer.status() if indexer is not None else {"enabled": False},
                "daily_cap_usd": str(settings.daily_cap_usd)}

    @app.get("/api/conversations")
    def conversations(_: Session = Depends(session)):
        return {"conversations": store.list()}

    @app.get("/api/conversations/{conversation_id}")
    def conversation(conversation_id: str, _: Session = Depends(session)):
        jobs.expire_stale()
        return store.get(_uuid(conversation_id))

    @app.post("/api/conversations", status_code=202)
    def new_conversation(body: AskRequest, _: Session = Depends(session)):
        conversation_id, turn_id = jobs.start(None, body.question)
        return {"conversation_id": conversation_id, "turn_id": turn_id}

    @app.post("/api/conversations/{conversation_id}/turns", status_code=202)
    def ask(conversation_id: str, body: AskRequest, _: Session = Depends(session)):
        conversation_id, turn_id = jobs.start(_uuid(conversation_id), body.question)
        return {"conversation_id": conversation_id, "turn_id": turn_id}

    @app.get("/api/turns/{turn_id}")
    def turn(turn_id: str, _: Session = Depends(session)):
        jobs.expire_stale()
        return store.turn(_uuid(turn_id))

    @app.post("/api/turns/{turn_id}/feedback")
    def feedback(turn_id: str, body: FeedbackRequest, _: Session = Depends(session)):
        store.set_feedback(_uuid(turn_id), body.note)
        return {"status": "saved"}

    @app.delete("/api/turns/{turn_id}/feedback")
    def remove_feedback(turn_id: str, _: Session = Depends(session)):
        store.clear_feedback(_uuid(turn_id))
        return {"status": "removed"}

    @app.post("/api/captures", status_code=202)
    def capture(body: CaptureRequest, _: Session = Depends(session)):
        make = draft_from_text if body.kind == "text" else draft_from_markdown
        try:
            draft = make(body.content, title=body.title, url=body.url, tags=body.tags)
        except CaptureError as exc:
            raise ConversationError(422, str(exc)) from None
        return {"capture_id": captures.start(draft)}

    @app.get("/api/captures")
    def recent_captures(_: Session = Depends(session)):
        captures.expire_stale()
        return {"captures": captures.recent(), "enabled": captures.enabled}

    @app.get("/api/captures/{capture_id}")
    def capture_status(capture_id: str, _: Session = Depends(session)):
        captures.expire_stale()
        return captures.job(_uuid(capture_id))

    # Static shell ----------------------------------------------------------------------------
    if WEB_DIR.is_dir():
        @app.get("/")
        def index():
            return FileResponse(WEB_DIR / "index.html", media_type="text/html")

        @app.get("/manifest.webmanifest")
        def manifest():
            return FileResponse(WEB_DIR / "manifest.webmanifest", media_type="application/manifest+json")

        @app.get("/sw.js")
        def service_worker():
            # Served from the root so it may control the whole app; never cached by the browser.
            return FileResponse(WEB_DIR / "sw.js", media_type="text/javascript",
                                headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    return app


def _uuid(value: str) -> str:
    import uuid

    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise ConversationError(404, "not found") from None


def _security(response: Response) -> Response:
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


def build() -> FastAPI:
    """Entry point for uvicorn: `uvicorn app.api.main:build --factory`."""
    from app.env import load_env_file

    load_env_file()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app(Settings.from_env())
