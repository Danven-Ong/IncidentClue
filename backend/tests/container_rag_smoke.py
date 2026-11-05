"""Explicit container smoke with real embeddings/SQL and a fully mocked provider.

Run begin, restart the isolated DB, complete, restart again, then inspect.
The record directory must be writable. No pytest or source checkout is required.
"""
import argparse
import json
import os
from pathlib import Path
import socket

import httpx2
from openai import OpenAI

from incidentclue import lifecycle
from incidentclue.lifecycle import Lifecycle
from incidentclue.persistence import open_database
from incidentclue.rag.corpus import RetrievalConfig, load_corpus
from incidentclue.rag.embeddings import artifact_contract
from incidentclue.schemas import Incident
from incidentclue.tools import HealthSnapshot


def block_external_network():
    original = socket.socket.connect
    resolve = socket.getaddrinfo

    def check(host):
        if host not in ('postgres', '127.0.0.1', '::1'):
            raise AssertionError('External network attempted during offline smoke')

    def connect(sock, address):
        check(address[0])
        return original(sock, address)

    def getaddrinfo(host, *args, **kwargs):
        check(host)
        return resolve(host, *args, **kwargs)

    socket.socket.connect = connect
    socket.getaddrinfo = getaddrinfo


def client(responses, requests):
    def handler(request):
        requests.append(json.loads(request.content))
        message = responses[len(requests) - 1]
        return httpx2.Response(200, json={
            'id': 'offline-container-response', 'object': 'chat.completion', 'created': 1,
            'model': 'offline-scripted', 'choices': [{'index': 0,
                'message': {'role': 'assistant', **message},
                'finish_reason': 'tool_calls' if message.get('tool_calls') else 'stop'}],
        })

    return OpenAI(api_key='fake-offline-key', base_url='https://offline.invalid/v1',
                  http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('phase', choices=['begin', 'complete', 'inspect'])
    parser.add_argument('--record', type=Path, required=True)
    args = parser.parse_args()
    block_external_network()
    corpus = load_corpus(Path('/opt/incidentclue'), artifact_contract(),
                         RetrievalConfig(max_distance=0.3))
    assert corpus.corpus_id == '3d0820c28e00e9ec5b4e400e7d38e2898cc9ab02c04958e2532d3443f6c7a3c0'
    with open_database(os.environ['INCIDENTCLUE_DATABASE_URL']) as db:
        db.require_initialized()
        service = Lifecycle(db, model_directory='/models/embedding')
        if args.phase == 'begin':
            assert not args.record.exists(), 'Refuse to overwrite smoke record'
            incident = Incident(incident_id='container-rag-smoke', service='search',
                summary='Database-backed requests are slow.', evidence=[{
                    'id': 'O1', 'timestamp': '2026-09-15T10:00:00Z', 'kind': 'log',
                    'content': 'Pool checked-out=20 limit=20; acquisition wait=2400ms; query duration=12ms.'}])
            health = HealthSnapshot(service='search', evidence=[])
            plain = service.start(incident, health, paused=True)
            assert service.inspect(plain)['retrieval'] is None
            identifier = service.start(incident, health, corpus_id=corpus.corpus_id, paused=True)
            assert service.inspect(identifier)['charged'] == {'model': 0, 'tool': 0}
            requests = []
            paused = service.resume(identifier, lambda: client([{'content': None, 'tool_calls': [{
                'id': 'lookup-1', 'type': 'function', 'function': {
                    'name': 'get_runbook_context', 'arguments': '{"service":"search"}'}}]}], requests),
                pause_after='execute')
            assert len(requests) == 1 and paused['charged'] == {'model': 1, 'tool': 1}
            sources = paused['state']['retrieved_sources']
            assert sources and any(s['document_id'] == 'pool' for s in sources)
            assert all(s['path'] == f"data/runbooks/{s['document_id']}.md" and
                       s['distance'] <= 0.3 for s in sources)
            assert paused['state']['evidence'] == incident.model_dump(mode='json')['evidence']
            with args.record.open('x', encoding='utf-8') as stream:
                json.dump({'plain_id': plain, 'paused': paused}, stream, indent=2)
            print(json.dumps({'phase': 'begin', 'sources': len(sources),
                              'charged': paused['charged'], 'provider_calls': 0}))
            return
        record = json.loads(args.record.read_text(encoding='utf-8'))
        paused = record['paused']
        identifier = paused['investigation_id']
        restored = service.inspect(identifier)
        assert restored['compatible']
        assert service.inspect(record['plain_id'])['retrieval'] is None

        def forbidden(*args, **kwargs):
            raise AssertionError('Saved retrieval or completed provider request replayed')

        lifecycle.retrieve = forbidden
        if args.phase == 'complete':
            assert restored == paused, 'Paused state changed across DB/container restart'
            diagnosis = {'status': 'hypothesis',
                'probable_cause': 'Connection acquisition waits while the pool is at its limit.',
                'evidence_ids': ['O1'],
                'source_ids': [paused['state']['retrieved_sources'][0]['id']],
                'support_assessment': {'evidence_links': [{'evidence_id': 'O1',
                    'excerpt': 'Pool checked-out=20 limit=20; acquisition wait=2400ms',
                    'relation': 'Records pool occupancy at the limit and acquisition delay.'}],
                    'blocking_gaps': []},
                'missing_evidence': ['Reason connections remain checked out.'],
                'next_checks': ['Inspect connection hold times.']}
            requests = []
            done = service.resume(identifier, lambda: client([
                {'content': 'Enough observations; finish.'},
                {'content': json.dumps(diagnosis)}], requests))
            assert len(requests) == 2
            assert done['status'] == 'completed' and done['charged'] == {'model': 3, 'tool': 1}
            assert done['state']['retrieved_sources'] == paused['state']['retrieved_sources']
            assert done['state']['diagnosis']['source_ids'] == diagnosis['source_ids']
            record['completed'] = done
            args.record.write_text(json.dumps(record, indent=2), encoding='utf-8')
        else:
            done = record['completed']
            assert restored == done, 'Completed state changed across DB/container restart'
            assert service.resume(identifier, forbidden) == done
        print(json.dumps({'phase': args.phase, 'status': done['status'],
                          'charged': done['charged'], 'provider_calls': 0}))


if __name__ == '__main__':
    main()
