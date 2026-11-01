# Code map

See the main README for [architecture](../README.md#architecture) and
[technology choices](../README.md#technology-choices).

| Location | Responsibility |
| --- | --- |
| [frontend/src/](../frontend/src/) | Browser interface and API requests |
| [api/](../backend/src/incidentclue/api/) | FastAPI routes and HTTP validation |
| [investigation.py](../backend/src/incidentclue/investigation.py) | LangGraph workflow |
| [lifecycle.py](../backend/src/incidentclue/lifecycle.py), [persistence.py](../backend/src/incidentclue/persistence.py) | Saved state, checkpoints and attempt accounting |
| [schemas.py](../backend/src/incidentclue/schemas.py), [diagnosis.py](../backend/src/incidentclue/diagnosis.py) | Data validation and evidence references |
| [llm.py](../backend/src/incidentclue/llm.py), [tools.py](../backend/src/incidentclue/tools.py) | Model calls and snapshot tools |
| [cli.py](../backend/src/incidentclue/cli.py) | Developer CLI |
| [data/synthetic/](../data/synthetic/), [evals/](../evals/) | Example inputs and separate evaluation criteria |
