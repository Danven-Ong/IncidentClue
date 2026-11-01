"""Groq client configuration through the OpenAI-compatible API."""

from openai import OpenAI

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-20b"
REQUEST_TIMEOUT_SECONDS = 20.0


def selection_request_options(model: str) -> dict:
    """Fit GPT-OSS tool decisions within the existing completion-token cap.

    Groq documents low/medium/high for these exact model IDs. Other models keep
    provider defaults; do not send them a potentially unsupported parameter.
    Final diagnosis generation retains its existing reasoning policy.
    """
    if model in ("openai/gpt-oss-20b", "openai/gpt-oss-120b"):
        return {"reasoning_effort": "low"}
    return {}


class ConfigurationError(ValueError):
    """Required provider configuration is missing or invalid."""


def create_groq_client(api_key: str) -> OpenAI:
    """Create a client with explicit provider, timeout, and retry settings."""
    if not api_key.strip():
        raise ConfigurationError("Set GROQ_API_KEY before making a provider request.")

    return OpenAI(
        api_key=api_key.strip(),
        base_url=GROQ_BASE_URL,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=0,
    )
