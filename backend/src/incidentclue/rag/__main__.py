"""Optional local RAG administration, independent of providers and investigation state."""

import argparse
import json
import os
from pathlib import Path
import sys

from . import RetrievalError
from .corpus import RetrievalConfig, load_corpus
from .embeddings import LocalEmbedder, artifact_contract
from .storage import ingest, inspect_corpus, lookup, prepare_schema


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("validate", "ingest"):
        command = commands.add_parser(name)
        command.add_argument("--root", type=Path, required=True, help="Repository/corpus root")
        command.add_argument("--max-distance", type=float, required=True,
                             help="Explicit experimental cosine cutoff; not calibrated")
        if name == "ingest":
            command.add_argument("--model-dir", type=Path, required=True)
    commands.add_parser("setup", help="Explicitly create pinned extension and retrieval tables")
    command = commands.add_parser("inspect", help="Read saved corpus contract without embeddings")
    command.add_argument("--corpus", required=True)
    command = commands.add_parser("lookup", help="Read-only retrieval probe, not an investigation")
    command.add_argument("--corpus", required=True)
    command.add_argument("--service", required=True)
    command.add_argument("--query", required=True)
    command.add_argument("--mode", choices=("vector", "lexical"), default="vector")
    command.add_argument("--model-dir", type=Path)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        corpus = None
        if args.command in ("validate", "ingest"):
            try:
                config = RetrievalConfig(max_distance=args.max_distance)
            except ValueError as error:
                raise RetrievalError("Cosine cutoff must be finite and between 0 and 2.") from error
            corpus = load_corpus(args.root, artifact_contract(), config)
        if args.command == "validate":
            output = {"corpus_id": corpus.corpus_id, "documents": len(corpus.contract["manifest"]["documents"]),
                      "passages": len(corpus.passages), "embedding_tokens_checked": False,
                      "cutoff_calibrated": False}
        else:
            import psycopg
            from psycopg.rows import dict_row
            url = os.environ.get("INCIDENTCLUE_DATABASE_URL", "")
            if not url:
                raise RetrievalError("Configure INCIDENTCLUE_DATABASE_URL for explicit RAG administration.")
            # No dotenv/provider import, secret diagnostics, retries or schema setup on reads.
            with psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=5,
                                 options="-c statement_timeout=10000 -c lock_timeout=5000") as connection:
                if args.command == "setup":
                    prepare_schema(connection)
                    output = {"schema_version": 1, "vector_version": "0.8.1", "ready": True}
                elif args.command == "ingest":
                    output = ingest(connection, corpus, LocalEmbedder(args.model_dir))
                elif args.command == "inspect":
                    output = inspect_corpus(connection, args.corpus)
                else:
                    if args.mode == "vector" and args.model_dir is None:
                        raise RetrievalError("Vector lookup requires --model-dir with verified local artifacts.")
                    embedder = LocalEmbedder(args.model_dir) if args.mode == "vector" else None
                    output = lookup(connection, args.corpus, args.service, args.query,
                                    mode=args.mode, embedder=embedder)
        print(json.dumps(output, ensure_ascii=False, allow_nan=False))
        return 0
    except RetrievalError as error:
        print(f"RAG operation failed: {error}", file=sys.stderr)
        return 1
    except Exception:
        # Database/loader exception strings can include credentials and filesystem details.
        print("RAG operation failed; check storage and local artifact configuration.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
