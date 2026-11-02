"""Optional runbook preparation/retrieval; deliberately separate from investigations."""


class RetrievalError(RuntimeError):
    """Controlled preparation/retrieval failure, never an empty successful lookup."""
