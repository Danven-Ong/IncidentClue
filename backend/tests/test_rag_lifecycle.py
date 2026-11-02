"""Real database accounting/recovery with fake SDK responses and deterministic vectors."""

import json
import os

import pytest

from incidentclue.lifecycle import Lifecycle
from incidentclue.persistence import RecoveryRequired
from incidentclue.persistence import ExecutionFailed, InvestigationFailed
from incidentclue.rag.storage import ingest, prepare_schema
from incidentclue.rag.corpus import RetrievalConfig, load_corpus
from incidentclue.tools import HealthSnapshot
from test_investigation import completion, fake_client
from test_rag_integration import ROOT, answer, tool
from test_rag_postgres import Embedder

pytestmark = pytest.mark.postgres
pytest_plugins = ["test_rag_integration"]


@pytest.fixture
def database_url():
    url=os.environ.get("INCIDENTCLUE_RAG_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set INCIDENTCLUE_RAG_TEST_DATABASE_URL to an isolated PG17/vector database.")
    return url


@pytest.fixture
def prepared(db, context):
    pytest.importorskip("pgvector")
    prepare_schema(db.connection)
    corpus=load_corpus(ROOT,Embedder.artifact,RetrievalConfig(max_distance=0.4))
    ingest(db.connection,corpus,Embedder())
    return corpus


def start(db,context,prepared):
    incident,_,_=context
    service=Lifecycle(db,model_directory="unused-in-fake-test")
    identifier=service.start(incident,HealthSnapshot(service=incident.service,evidence=[]),
                             corpus_id=prepared.corpus_id,paused=True)
    return service,identifier


def tail(source):
    return [completion({"content":"done"}),completion({"content":json.dumps(answer([source["id"]]))})]


def test_creation_pins_without_embedding_or_model_and_saved_completion_is_offline(db,context,prepared,monkeypatch):
    from incidentclue.rag import context as runtime
    from incidentclue import lifecycle
    _,_,source=context
    monkeypatch.setattr(runtime,"LocalEmbedder",lambda *a:pytest.fail("Creation loaded embeddings"))
    service,identifier=start(db,context,prepared)
    view=service.inspect(identifier)
    assert view["charged"]=={"model":0,"tool":0} and view["retrieval"]["corpus_id"]==prepared.corpus_id
    calls=[]
    monkeypatch.setattr(lifecycle,"retrieve",lambda *a:calls.append(a) or {"status":"ok","retrieved_sources":[source]})
    done=service.resume(identifier,lambda:fake_client([tool(),*tail(source)],[]))
    assert len(calls)==1 and done["charged"]=={"model":3,"tool":1}
    assert done["state"]["retrieved_sources"]==[source]
    monkeypatch.setattr(lifecycle,"retrieve",lambda *a:pytest.fail("Saved result retrieved again"))
    monkeypatch.setattr(lifecycle,"pin_corpus",lambda *a:pytest.fail("Inspection required corpus storage"))
    assert service.inspect(identifier)["state"]==done["state"]
    assert service.resume(identifier,lambda:pytest.fail("Completed created provider"))["state"]==done["state"]


def test_real_sql_lookup_inside_reserved_tool_with_deterministic_embeddings(db,context,prepared,monkeypatch):
    from incidentclue.rag import context as runtime
    class Local(Embedder):
        def __init__(self,*args):
            from types import SimpleNamespace
            self.model=SimpleNamespace(token_count=lambda query:len(query.split()))
        def query_vector(self,text):
            attempts=db.attempts(identifier)
            assert any(a["kind"]=="tool" and a["status"]=="in_flight" for a in attempts)
            return super().query_vector(text)
    monkeypatch.setattr(runtime,"LocalEmbedder",Local)
    service,identifier=start(db,context,prepared)
    paused=service.resume(identifier,lambda:fake_client([tool()],[]),pause_after="execute")
    assert paused["charged"]=={"model":1,"tool":1}
    assert paused["state"]["retrieved_sources"]
    assert paused["state"]["evidence"]==context[0].model_dump(mode="json")["evidence"]
    source=paused["state"]["retrieved_sources"][0]
    done=service.resume(identifier,lambda:fake_client(tail(source),[]))
    assert done["state"]["diagnosis"]["source_ids"]==[source["id"]]


def test_committed_lookup_output_survives_missing_graph_checkpoint_without_replay(db,context,prepared,monkeypatch):
    from incidentclue import lifecycle
    _,_,source=context
    calls=[]
    monkeypatch.setattr(lifecycle,"retrieve",lambda *a:calls.append(a) or {"status":"ok","retrieved_sources":[source]})
    service,identifier=start(db,context,prepared)
    original=db.run_node
    def interrupted(identifier,name,state,node):
        output=original(identifier,name,state,node)
        if name=="execute":
            raise KeyboardInterrupt("Simulated crash after durable output")
        return output
    monkeypatch.setattr(db,"run_node",interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.resume(identifier,lambda:fake_client([tool()],[]))
    assert service.inspect(identifier)["charged"]=={"model":1,"tool":1}
    monkeypatch.setattr(db,"run_node",original)
    done=service.resume(identifier,lambda:fake_client(tail(source),[]))
    assert done["status"]=="completed" and len(calls)==1
    assert done["charged"]=={"model":3,"tool":1}


def test_uncertain_lookup_remains_charged_and_refuses_replay(db,context,prepared,monkeypatch):
    from incidentclue import lifecycle
    calls=[]
    def interrupted(*args):
        calls.append(args)
        raise KeyboardInterrupt("Simulated interrupted read")
    monkeypatch.setattr(lifecycle,"retrieve",interrupted)
    service,identifier=start(db,context,prepared)
    with pytest.raises(KeyboardInterrupt):
        service.resume(identifier,lambda:fake_client([tool()],[]))
    with pytest.raises(RecoveryRequired):
        service.resume(identifier,lambda:pytest.fail("Uncertain request replayed"))
    assert service.inspect(identifier)["charged"]=={"model":1,"tool":1} and len(calls)==1


def test_api_creation_uses_server_corpus_without_changing_create_payload(db,context,prepared,monkeypatch):
    from fastapi.testclient import TestClient
    from contextlib import contextmanager
    from incidentclue.api.app import Settings,create_app
    from incidentclue.rag import context as runtime
    monkeypatch.setattr(runtime,"LocalEmbedder",lambda *a:pytest.fail("Creation loaded embeddings"))
    @contextmanager
    def database(_):
        yield db
    app=create_app(Settings(retrieval_corpus=prepared.corpus_id),database_factory=database)
    with TestClient(app) as client:
        response=client.post('/investigations',json={"incident":context[0].model_dump(mode="json"),
            "health":{"service":"billing","evidence":[]}})
    assert response.status_code==201
    assert response.json()["retrieval"]["corpus_id"]==prepared.corpus_id
    assert response.json()["charged"]=={"model":0,"tool":0}


def test_failed_lookup_is_charged_and_never_replayed(db,context,prepared,monkeypatch):
    from incidentclue import lifecycle
    from incidentclue.rag import RetrievalError
    calls=[]
    def unavailable(*args):
        calls.append(args)
        raise RetrievalError("Missing artifact")
    monkeypatch.setattr(lifecycle,"retrieve",unavailable)
    service,identifier=start(db,context,prepared)
    with pytest.raises(ExecutionFailed):
        service.resume(identifier,lambda:fake_client([tool()],[]))
    view=service.inspect(identifier)
    assert view["status"]=="failed" and view["error_code"]=="retrieval_error"
    assert view["charged"]=={"model":1,"tool":1} and view["state"]["retrieved_sources"]==[]
    with pytest.raises(InvestigationFailed,match="cannot resume"):
        service.resume(identifier,lambda:pytest.fail("Failed lookup replayed"))
    assert len(calls)==1


def test_api_resume_uses_saved_pin_and_returns_sources_without_new_create_toggle(db,context,prepared,monkeypatch):
    from contextlib import contextmanager
    from fastapi.testclient import TestClient
    from incidentclue.api.app import Settings,create_app
    from incidentclue import lifecycle
    source=context[2]
    service,identifier=start(db,context,prepared)
    monkeypatch.setattr(lifecycle,"retrieve",lambda *a:{"status":"ok","retrieved_sources":[source]})
    @contextmanager
    def database(_):
        yield db
    app=create_app(Settings(),database_factory=database,
                   client_factory=lambda:fake_client([tool(),*tail(source)],[]))
    with TestClient(app) as client:
        result=client.post(f'/investigations/{identifier}/resume')
        assert result.status_code==200
        body=result.json()
        assert body["state"]["diagnosis"]["source_ids"]==[source["id"]]
        assert body["retrieval"]==service.inspect(identifier)["retrieval"]
        app.state.client_factory=lambda:pytest.fail("Saved API result created provider")
        again=client.post(f'/investigations/{identifier}/resume')
        assert again.status_code==200 and again.json()["state"]==body["state"]
