"""Local investigation records without credential or raw-input storage."""

import json
from pathlib import Path


def write_run_record(path: Path, record: dict[str, object]) -> None:
    """Write a new record without overwriting a previous run."""
    with path.open("x", encoding="utf-8") as destination:
        json.dump(record, destination, indent=2)
        destination.write("\n")
