"""Local FastAPI app factory. Environment is read explicitly at app creation."""

from dataclasses import dataclass, field
import os

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from incidentclue.api.errors import error_response
from incidentclue.api.routes import router
from incidentclue.llm import DEFAULT_MODEL
from incidentclue.persistence import open_database

MAX_BODY_BYTES = 100_000


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default="", repr=False)
    provider_key: str = field(default="", repr=False)
    model: str = DEFAULT_MODEL
    retrieval_corpus: str | None = None
    model_directory: str | None = field(default=None, repr=False)

    @classmethod
    def from_environment(cls):
        return cls(database_url=os.environ.get("INCIDENTCLUE_DATABASE_URL", ""),
                   provider_key=os.environ.get("GROQ_API_KEY", ""),
                   model=os.environ.get("GROQ_MODEL", DEFAULT_MODEL),
                   retrieval_corpus=os.environ.get("INCIDENTCLUE_RAG_CORPUS_ID") or None,
                   model_directory=os.environ.get("INCIDENTCLUE_RAG_MODEL_DIR") or None)


class RequestGuard:
    """Bound streamed bodies and prevent raw exceptions reaching server logs.

    Pure ASGI middleware avoids spawning/cancelling a request task around a
    synchronous executor. It never interprets client disconnect as graph pause.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def observed_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                if len(body) + len(chunk) > MAX_BODY_BYTES:
                    return await error_response(413, "request_too_large", "Request body exceeds 100,000 bytes.")(
                        scope, receive, send)
                body.extend(chunk)
                if not message.get("more_body", False):
                    break
            consumed = False

            async def bounded_receive():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, bounded_receive, observed_send)
        except Exception:
            # Do not re-raise: Uvicorn otherwise logs raw exception diagnostics.
            if not started:
                await error_response(500, "internal_error", "Internal server error.")(scope, receive, send)


def create_app(settings: Settings | None = None, *, database_factory=open_database, client_factory=None):
    app = FastAPI(title="IncidentClue local API", version="0.1.0", debug=False)
    app.state.settings = settings if settings is not None else Settings.from_environment()
    app.state.database_factory = database_factory
    app.state.client_factory = client_factory
    app.add_middleware(RequestGuard)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        # Pydantic details can contain raw input, including submitted secrets.
        return error_response(422, "invalid_request", "Request validation failed; check the API schema and input limits.")

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return error_response(error.status_code, "http_error", "HTTP request could not be served.")

    app.include_router(router)
    return app
