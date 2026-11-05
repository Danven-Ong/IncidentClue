# Local embedding model

This directory can stay empty when retrieval is disabled. The main Compose stack
mounts it read-only into the backend. No model files are downloaded automatically.

To enable retrieval, supply the verified files listed in
[artifact.json](../backend/src/incidentclue/rag/artifact.json), or set
`INCIDENTCLUE_RAG_MODEL_DIR` to an existing model directory. See the
[RAG guide](../docs/rag.md#quick-start). Model artifacts are ignored by Git.
