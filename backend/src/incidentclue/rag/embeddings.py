"""Pinned CPU embeddings from verified local files; never acquire artifacts here."""

from hashlib import sha256
from importlib.metadata import version
import json
import math
from pathlib import Path

from . import RetrievalError
from .corpus import DIMENSION


def artifact_contract():
    return json.loads(Path(__file__).with_name("artifact.json").read_text(encoding="utf-8"))


def checked_vector(values):
    try:
        vector = [float(value) for value in values]
        if (len(vector) != DIMENSION or not all(math.isfinite(v) for v in vector)
                or not 0 < sum(v * v for v in vector) < float("inf")):
            raise ValueError
        # pgvector stores float32. Refuse overflow/underflow before publication.
        import struct
        vector = list(struct.unpack(f"{DIMENSION}f", struct.pack(f"{DIMENSION}f", *vector)))
        if not all(math.isfinite(v) for v in vector) or sum(v * v for v in vector) == 0:
            raise ValueError
        return vector
    except (TypeError, ValueError, OverflowError) as error:
        raise RetrievalError("Embedding must be a finite, nonzero 384-dimensional vector.") from error


class LocalEmbedder:
    def __init__(self, directory: Path):
        self.artifact = artifact_contract()
        try:
            directory = directory.resolve(strict=True)
            for name, record in self.artifact["files"].items():
                path = directory / name
                if (path.is_symlink() or path.resolve(strict=True).parent != directory
                        or path.stat().st_size != record["bytes"]
                        or sha256(path.read_bytes()).hexdigest() != record["sha256"]):
                    raise ValueError
            for name, pin in self.artifact["packages"].items():
                if version(name) != pin:
                    raise ValueError
            from fastembed import TextEmbedding
            self.model = TextEmbedding(model_name=self.artifact["model"], threads=1,
                providers=["CPUExecutionProvider"], local_files_only=True,
                specific_model_path=str(directory))
        except Exception as error:
            raise RetrievalError("Local embedding setup failed; install pinned RAG dependencies and verify cached artifacts.") from error

    def passage_vectors(self, texts):
        try:
            if any(self.model.token_count(text) > 200 for text in texts):
                raise RetrievalError("Passage exceeds the 200-token embedding limit.")
            vectors = [checked_vector(v) for v in self.model.passage_embed(texts)]
            if len(vectors) != len(texts):
                raise RetrievalError("Embedding count does not match the corpus.")
            return vectors
        except RetrievalError:
            raise
        except Exception as error:
            raise RetrievalError("Local passage embedding failed.") from error

    def query_vector(self, query):
        try:
            # Bound the raw query under the pinned tokenizer before backend prefixing.
            if self.model.token_count(query) > 200:
                raise RetrievalError("Query exceeds the 200-token embedding limit.")
            vectors = list(self.model.query_embed(query))
            if len(vectors) != 1:
                raise RetrievalError("Expected exactly one query embedding.")
            return checked_vector(vectors[0])
        except RetrievalError:
            raise
        except Exception as error:
            raise RetrievalError("Local query embedding failed.") from error
