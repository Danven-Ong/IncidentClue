"""Groq client configuration through the OpenAI-compatible API."""

from openai import OpenAI

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-20b"
REQUEST_TIMEOUT_SECONDS = 20.0


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
