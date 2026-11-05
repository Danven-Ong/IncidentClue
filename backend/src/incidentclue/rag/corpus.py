"""Validate only the explicit corpus allowlist, without importing evaluation code."""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import RetrievalError

DIMENSION = 384


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def identity(value) -> str:
    return sha256(canonical(value).encode("utf-8")).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Document(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    path: str
    title: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=32)
    effective_date: str
    services: list[str] = Field(min_length=1, max_length=16)
    status: Literal["current", "superseded"]
    superseded_by: str | None
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Manifest(Contract):
    schema_version: Literal[1]
    synthetic: Literal[True]
    chunk_policy: Literal["markdown-h2-v1"]
    documents: list[Document] = Field(min_length=1, max_length=12)


class RetrievalConfig(Contract):
    # No semantic cutoff is claimed calibrated. Callers must supply it explicitly.
    max_distance: float = Field(ge=0, le=2, allow_inf_nan=False)
    policy: Literal["exact-cosine-fts-v1"] = "exact-cosine-fts-v1"
    top_k: Literal[3] = 3
    text_bytes: Literal[3000] = 3000
    context_bytes: Literal[4000] = 4000


@dataclass(frozen=True)
class Corpus:
    corpus_id: str
    contract_json: str
    passages_json: str

    @property
    def passages(self):
        return json.loads(self.passages_json)

    @property
    def contract(self):
        return json.loads(self.contract_json)


def load_corpus(root: Path, artifact: dict, config: RetrievalConfig) -> Corpus:
    """Complete validation precedes embeddings or database writes; no recursive scan."""
    try:
        root = root.resolve(strict=True)
        directory = root / "data/runbooks"
        if directory.resolve(strict=True) != directory or directory.is_symlink():
            raise ValueError
        manifest_path = directory / "manifest.json"
        if manifest_path.is_symlink():
            raise ValueError
        with manifest_path.open("rb") as stream:
            raw_manifest = stream.read(64_001)
        if len(raw_manifest) > 64_000:
            raise ValueError
        manifest = Manifest.model_validate_json(raw_manifest)
        documents = {doc.id: doc for doc in manifest.documents}
        if len(documents) != len(manifest.documents):
            raise ValueError
        passages = []
        for doc in manifest.documents:
            date.fromisoformat(doc.effective_date)
            if (len(set(doc.services)) != len(doc.services)
                    or any(not s or s.strip() != s or len(s) > 80
                           for s in doc.services)):
                raise ValueError
            if doc.status == "superseded":
                replacement = documents.get(doc.superseded_by)
                if replacement is None or replacement.id == doc.id or replacement.status != "current":
                    raise ValueError
            elif doc.superseded_by is not None:
                raise ValueError
            if doc.path != f"data/runbooks/{doc.id}.md":
                raise ValueError
            path = root / doc.path
            if path.is_symlink() or path.resolve(strict=True).parent != directory:
                raise ValueError
            with path.open("rb") as stream:
                raw = stream.read(64_001)
            if len(raw) > 64_000 or sha256(raw).hexdigest() != doc.sha256:
                raise ValueError
            lines = raw.decode("utf-8").splitlines(keepends=True)
            starts = [i for i, line in enumerate(lines) if line.startswith("## ")]
            if not starts:
                raise ValueError
            for index, start in enumerate(starts):
                end = starts[index + 1] if index + 1 < len(starts) else len(lines)
                section = lines[start][3:].strip()
                text = "".join(lines[start:end]).rstrip()
                if not section or len(section) > 120 or not 0 < len(text.encode("utf-8")) <= 1000:
                    raise ValueError
                passages.append({"passage_id": f"{doc.id}#{section.lower()}",
                    "document_id": doc.id, "document_version": doc.version,
                    "document_sha256": doc.sha256, "title": doc.title, "path": doc.path,
                    "section": section, "line_start": start + 1, "line_end": end,
                    "text": text, "effective_date": doc.effective_date,
                    "services": doc.services, "status": doc.status})
        if len(passages) > 48 or len({p["passage_id"] for p in passages}) != len(passages):
            raise ValueError
        passages.sort(key=lambda p: p["passage_id"])
        contract = {"schema_version": 1, "manifest": manifest.model_dump(mode="json"),
                    "artifact": artifact, "retrieval": config.model_dump(mode="json"),
                    "passages_sha256": identity(passages)}
        return Corpus(identity(contract), canonical(contract), canonical(passages))
    except (OSError, ValueError, ValidationError) as error:
        raise RetrievalError("Corpus validation failed; check the allowlist, hashes and bounds.") from error
