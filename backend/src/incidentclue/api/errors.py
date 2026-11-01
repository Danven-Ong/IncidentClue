"""HTTP error categories and controlled diagnostic text."""

from fastapi.responses import JSONResponse

from incidentclue.api.schemas import ErrorDetail, ErrorEnvelope
from incidentclue.llm import ConfigurationError
from incidentclue.persistence import ExecutionFailed, PersistenceError


def error_response(status, code, message, *, cause=None, identifier=None):
    envelope = ErrorEnvelope(error=ErrorDetail(code=code, cause=cause, message=message),
                             investigation_id=identifier)
    return JSONResponse(status_code=status, content=envelope.model_dump(mode="json"))


def controlled_error(error, identifier=None):
    if isinstance(error, ConfigurationError):
        return error_response(503, "configuration_unavailable",
                              "Server configuration required for this operation is unavailable.",
                              identifier=identifier)
    if isinstance(error, ExecutionFailed):
        if error.code == "provider_error":
            status, code = 502, "provider_error"
        elif error.code == "validation_error":
            status, code = 502, "execution_validation_error"
        else:
            status, code = 500, "execution_error"
        return error_response(status, code, "Execution failed; inspect saved progress and charged attempts.",
                              cause=error.cause, identifier=identifier)
    if isinstance(error, PersistenceError):
        status = {"storage_unavailable": 503, "investigation_not_found": 404}.get(error.code, 409)
        messages = {
            "storage_unavailable": "Storage is unavailable or not initialized with a compatible schema.",
            "investigation_not_found": "Investigation was not found.",
            "investigation_busy": "Investigation is already executing.",
            "incompatible_runtime": "Saved investigation is incompatible with this runtime.",
            "investigation_failed": "Investigation has failed; inspect it and start a new ID.",
            "recovery_required": "Recovery requires inspection; execution was refused.",
        }
        # Stored causes are data, not trusted diagnostic messages.
        cause = error.cause if error.cause in {
            "uncertain_attempt", "prior_attempt_failed", "lifecycle_error",
            "provider_error", "validation_error", "execution_error", "configuration_error",
        } else None
        return error_response(status, error.code, messages.get(error.code, messages["recovery_required"]),
                              cause=cause, identifier=identifier)
    return error_response(500, "internal_error", "Internal server error.", identifier=identifier)
