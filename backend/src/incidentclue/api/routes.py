"""Request-scoped synchronous operations; no shared DB sessions or background work."""

from uuid import UUID

from fastapi import APIRouter, Request, Response

from incidentclue.api.errors import controlled_error
from incidentclue.api.schemas import (
    CreateInvestigation, ErrorEnvelope, Health, Inspection, PauseBoundary,
)
from incidentclue.lifecycle import Lifecycle
from incidentclue.llm import ConfigurationError, create_groq_client
from incidentclue.persistence import StorageUnavailable

router = APIRouter(responses={code: {"model": ErrorEnvelope} for code in
                              (404, 409, 413, 422, 500, 502, 503)})


@router.get("/health", response_model=Health)
def health(request: Request):
    try:
        with request.app.state.database_factory(request.app.state.settings.database_url) as db:
            db.require_initialized()
            # Read every required table, including checkpoint migrations, without
            # calling setup(). Metadata-only readiness would miss a partial db-init.
            db.connection.execute("SELECT id FROM ic_investigations LIMIT 0")
            db.connection.execute("SELECT operation FROM ic_attempts LIMIT 0")
            migration = db.connection.execute("SELECT max(v) AS version FROM checkpoint_migrations").fetchone()
            if migration["version"] != len(db.saver.MIGRATIONS) - 1:
                raise StorageUnavailable("Checkpoint schema needs explicit initialization or is incompatible.")
            list(db.saver.list(None, limit=0))
        return Health()
    except Exception as error:
        return controlled_error(error)


def operate(request, action, *, identifier=None):
    try:
        with request.app.state.database_factory(request.app.state.settings.database_url) as db:
            return action(Lifecycle(db, model_directory=request.app.state.settings.model_directory))
    except Exception as error:
        return controlled_error(error, identifier)


@router.post("/investigations", status_code=201, response_model=Inspection)
def create_investigation(payload: CreateInvestigation, request: Request, response: Response):
    model = payload.model if payload.model is not None else request.app.state.settings.model
    if not model.strip() or len(model) > 200:
        return controlled_error(ConfigurationError("Invalid configured model."))

    def create(service):
        identifier = service.start(payload.incident, payload.health, model, logs=payload.logs, paused=True,
                                   corpus_id=request.app.state.settings.retrieval_corpus)
        response.headers["Location"] = f"/investigations/{identifier}"
        return service.inspect(identifier)

    return operate(request, create)


@router.get("/investigations/{identifier}", response_model=Inspection)
def inspect_investigation(identifier: UUID, request: Request):
    return operate(request, lambda service: service.inspect(str(identifier)), identifier=str(identifier))


@router.post("/investigations/{identifier}/pause", response_model=Inspection)
def pause_investigation(identifier: UUID, request: Request):
    return operate(request, lambda service: service.pause(str(identifier)), identifier=str(identifier))


@router.post("/investigations/{identifier}/resume", response_model=Inspection)
def resume_investigation(identifier: UUID, request: Request, pause_after: PauseBoundary | None = None):
    # The callable stays lazy so cached output/completed IDs need no credentials.
    factory = request.app.state.client_factory or (
        lambda: create_groq_client(request.app.state.settings.provider_key)
    )
    return operate(request, lambda service: service.resume(str(identifier), factory, pause_after=pause_after),
                   identifier=str(identifier))
