"""Explicit PostgreSQL setup, atomic publication and bounded exact/lexical lookup."""

import re
from importlib.metadata import version

from psycopg.types.json import Jsonb

from . import RetrievalError
from .corpus import Corpus, canonical, identity
from .embeddings import checked_vector

SCHEMA_VERSION = 1
VECTOR_VERSION = "0.8.1"
SETUP_LOCK = 470948371523
INGEST_LOCK = 470948371524

DDL = (
    "CREATE SCHEMA ic_rag",
    "CREATE TABLE ic_rag.schema_version (singleton boolean PRIMARY KEY CHECK (singleton), version integer NOT NULL)",
    "INSERT INTO ic_rag.schema_version VALUES (true, 1)",
    """CREATE TABLE ic_rag.corpora (
        corpus_id text PRIMARY KEY, contract jsonb NOT NULL,
        passage_count integer NOT NULL CHECK (passage_count BETWEEN 1 AND 48))""",
    """CREATE TABLE ic_rag.passages (
        corpus_id text NOT NULL REFERENCES ic_rag.corpora(corpus_id),
        passage_id text NOT NULL, metadata jsonb NOT NULL,
        services text[] NOT NULL, current boolean NOT NULL,
        embedding public.vector(384) NOT NULL,
        terms tsvector NOT NULL,
        PRIMARY KEY (corpus_id, passage_id))""",
)


def _platform(connection):
    row = connection.execute("""SELECT current_setting('server_version_num')::integer AS server,
        (SELECT extversion FROM pg_extension WHERE extname='vector') AS vector,
        (SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON e.extnamespace=n.oid
         WHERE e.extname='vector') AS namespace""").fetchone()
    if not 170000 <= row["server"] < 180000:
        raise RetrievalError("RAG storage requires PostgreSQL 17.")
    if row["vector"] is not None and (row["vector"] != VECTOR_VERSION or row["namespace"] != "public"):
        raise RetrievalError("RAG storage requires vector 0.8.1 in the public schema.")
    return row


def require_schema(connection):
    """Read-only preflight. Never creates extensions, tables or migrations."""
    if _platform(connection)["vector"] is None:
        raise RetrievalError("RAG storage is not prepared; run explicit setup.")
    tables = connection.execute("""SELECT to_regclass('ic_rag.schema_version') AS version,
        to_regclass('ic_rag.corpora') AS corpora,
        to_regclass('ic_rag.passages') AS passages""").fetchone()
    if not all(tables.values()):
        raise RetrievalError("RAG schema is absent or incomplete; explicit setup is required.")
    rows = connection.execute("SELECT version FROM ic_rag.schema_version").fetchall()
    if rows != [{"version": SCHEMA_VERSION}]:
        raise RetrievalError("RAG schema is incompatible; no automatic migration is available.")


def prepare_schema(connection):
    """Only this explicit administrative operation can create the extension/schema."""
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (SETUP_LOCK,))
        platform = _platform(connection)
        exists = connection.execute("SELECT to_regnamespace('ic_rag') AS namespace").fetchone()
        if exists["namespace"] is not None:
            require_schema(connection)
            return
        if platform["vector"] is None:
            available = connection.execute("""SELECT 1 FROM pg_available_extension_versions
                WHERE name='vector' AND version=%s""", (VECTOR_VERSION,)).fetchone()
            if available is None:
                raise RetrievalError("Install the pinned vector 0.8.1 extension before explicit setup.")
            connection.execute("CREATE EXTENSION vector WITH SCHEMA public VERSION '0.8.1'")
        for statement in DDL:
            connection.execute(statement)
        require_schema(connection)


def _adapter(connection):
    try:
        if version("pgvector") != "0.5.1":
            raise RetrievalError("Pinned pgvector adapter version is required.")
        from pgvector.psycopg import register_vector
        register_vector(connection)
    except Exception as error:
        raise RetrievalError("Vector adaptation unavailable; install pinned RAG dependencies.") from error


def _vector(values):
    from pgvector import Vector
    return Vector(checked_vector(values))


def _stored_contract(connection, corpus_id):
    if not isinstance(corpus_id, str) or not re.fullmatch(r"[0-9a-f]{64}", corpus_id):
        raise RetrievalError("A pinned corpus SHA256 is required.")
    row = connection.execute("SELECT contract, passage_count FROM ic_rag.corpora WHERE corpus_id=%s",
                             (corpus_id,)).fetchone()
    if row is None:
        raise RetrievalError("Pinned corpus is unavailable; this is not an empty lookup.")
    if identity(row["contract"]) != corpus_id:
        raise RetrievalError("Stored corpus contract failed its identity check.")
    passages = connection.execute("""SELECT metadata FROM ic_rag.passages WHERE corpus_id=%s
        ORDER BY passage_id COLLATE "C"
        """, (corpus_id,)).fetchall()
    if (len(passages) != row["passage_count"]
            or identity([p["metadata"] for p in passages]) != row["contract"]["passages_sha256"]):
        raise RetrievalError("Stored corpus is incomplete.")
    return row["contract"]


def inspect_corpus(connection, corpus_id):
    require_schema(connection)
    with connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        return {"corpus_id": corpus_id, "contract": _stored_contract(connection, corpus_id)}


def ingest(connection, corpus: Corpus, embedder):
    """One complete immutable version, or no writes. Identical publication is a no-op."""
    require_schema(connection)
    if (identity(corpus.contract) != corpus.corpus_id
            or identity(corpus.passages) != corpus.contract["passages_sha256"]):
        raise RetrievalError("Corpus identity validation failed.")
    if corpus.contract["artifact"] != embedder.artifact:
        raise RetrievalError("Embedding artifact differs from the corpus contract.")
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (INGEST_LOCK,))
        exists = connection.execute("SELECT 1 FROM ic_rag.corpora WHERE corpus_id=%s",
                                    (corpus.corpus_id,)).fetchone()
        if exists:
            _stored_contract(connection, corpus.corpus_id)
            return {"corpus_id": corpus.corpus_id, "published": False,
                    "passages": len(corpus.passages)}
        passages = corpus.passages
        # All token counts and vector validation finish before the first publication write.
        vectors = list(embedder.passage_vectors([p["text"] for p in passages]))
        if len(vectors) != len(passages):
            raise RetrievalError("Embedding count does not match the corpus.")
        vectors = [checked_vector(v) for v in vectors]
        _adapter(connection)
        connection.execute("INSERT INTO ic_rag.corpora VALUES (%s, %s, %s)",
                           (corpus.corpus_id, Jsonb(corpus.contract), len(passages)))
        for passage, vector in zip(passages, vectors, strict=True):
            connection.execute("""INSERT INTO ic_rag.passages
                (corpus_id, passage_id, metadata, services, current, embedding, terms)
                VALUES (%s, %s, %s, %s, %s, %s, to_tsvector('english', %s))""",
                (corpus.corpus_id, passage["passage_id"], Jsonb(passage), passage["services"],
                 passage["status"] == "current", _vector(vector), passage["text"]))
    return {"corpus_id": corpus.corpus_id, "published": True, "passages": len(passages)}


def bound_sources(corpus_id, candidates, config, *, mode):
    """Ranked candidates are filtered in SQL; retain exact excerpts within byte bounds."""
    sources, seen, text_bytes, excluded = [], set(), 0, 0
    for row in candidates:
        passage = row["metadata"]
        passage_id = passage["passage_id"]
        if passage_id in seen:
            raise RetrievalError("Lookup returned conflicting/duplicate passage IDs.")
        seen.add(passage_id)
        source = {key: passage[key] for key in (
            "document_id", "document_version", "document_sha256", "title", "path",
            "section", "line_start", "line_end", "text")}
        source.update(id=f"source:{corpus_id}:{passage_id}", corpus_id=corpus_id,
                      rank=len(sources) + 1,
                      distance=float(row["score"]) if mode == "vector" else None)
        if mode == "lexical":
            source["lexical_score"] = float(row["score"])
        size = len(source["text"].encode("utf-8"))
        if (len(sources) >= config["top_k"] or text_bytes + size > config["text_bytes"]
                or len(canonical([*sources, source]).encode("utf-8")) > config["context_bytes"]):
            excluded += 1
            continue
        text_bytes += size
        sources.append(source)
    return {"retrieved_sources": sources, "bounds_excluded": excluded,
            "context_bytes": len(canonical(sources).encode("utf-8")), "text_bytes": text_bytes}


def lookup(connection, corpus_id, service, query, *, mode="vector", embedder=None):
    """Administrative primitive; investigation query construction/charging comes later."""
    if (mode not in ("vector", "lexical") or not isinstance(service, str)
            or not service or service.strip() != service or len(service) > 80
            or service == "global" or not isinstance(query, str) or not query.strip()
            or len(query.encode("utf-8")) > 2000):
        raise RetrievalError("Lookup requires a concrete service and a bounded nonempty query.")
    require_schema(connection)
    with connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        contract = _stored_contract(connection, corpus_id)
        config = contract["retrieval"]
        counts = connection.execute("""SELECT count(*) AS total,
            count(*) FILTER (WHERE current AND (services @> ARRAY[%s]::text[]
                OR services @> ARRAY['global']::text[])) AS eligible
            FROM ic_rag.passages WHERE corpus_id=%s""", (service, corpus_id)).fetchone()
        eligible = counts["eligible"]
        if mode == "vector":
            if embedder is None or embedder.artifact != contract["artifact"]:
                raise RetrievalError("Lookup requires the exact pinned local embedding artifact.")
            query_vector = checked_vector(embedder.query_vector(query))
            _adapter(connection)
            candidates = connection.execute("""WITH eligible AS MATERIALIZED (
                SELECT passage_id, metadata, embedding FROM ic_rag.passages
                WHERE corpus_id=%s AND current AND (services @> ARRAY[%s]::text[]
                    OR services @> ARRAY['global']::text[]))
                SELECT passage_id, metadata, embedding <=> %s AS score FROM eligible
                WHERE embedding <=> %s <= %s ORDER BY score ASC, passage_id COLLATE "C" ASC""",
                (corpus_id, service, _vector(query_vector), _vector(query_vector), config["max_distance"])).fetchall()
        else:
            candidates = connection.execute("""WITH eligible AS MATERIALIZED (
                SELECT passage_id, metadata, terms FROM ic_rag.passages
                WHERE corpus_id=%s AND current AND (services @> ARRAY[%s]::text[]
                    OR services @> ARRAY['global']::text[])),
                q AS (SELECT plainto_tsquery('english', %s) AS terms)
                SELECT passage_id, metadata, ts_rank_cd(e.terms, q.terms) AS score
                FROM eligible e CROSS JOIN q WHERE e.terms @@ q.terms
                ORDER BY score DESC, passage_id COLLATE "C" ASC""",
                (corpus_id, service, query)).fetchall()
        bounded = bound_sources(corpus_id, candidates, config, mode=mode)
    return {"corpus_id": corpus_id, "mode": mode, "eligible_passages": eligible,
            "scope_excluded": counts["total"] - eligible,
            "matched_passages": len(candidates), "match_excluded": eligible - len(candidates),
            **bounded}
