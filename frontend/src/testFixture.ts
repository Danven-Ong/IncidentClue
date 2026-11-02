import type { Inspection } from './api';
export const id = '11111111-1111-4111-8111-111111111111';
export const otherId = '22222222-2222-4222-8222-222222222222';
export function inspection(patch: Partial<Inspection> = {}): Inspection {
  return { investigation_id: id, incident_id: 'checkout-001', status: 'paused', executing: false,
    recovery_needed: false, compatible: true, pause_requested: false, model: 'saved-model',
    error_code: null, checkpoint_id: 'checkpoint', next_nodes: ['decide'],
    state: { incident: { service: 'checkout', summary: 'Checkout unavailable' }, evidence: [] },
    attempts: [], charged: { model: 0, tool: 0 }, limits: { model: 3, tool: 2 },
    created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z', ...patch };
}

export function retrievalInspection() {
  const corpus = 'a'.repeat(64);
  const source = { id: `source:${corpus}:dns#checks`, corpus_id: corpus, document_id: 'dns',
    document_version: '1', document_sha256: 'b'.repeat(64), title: 'Dependency name resolution',
    path: 'data/runbooks/dns.md', section: 'Checks', line_start: 8, line_end: 12,
    text: 'Inspect resolver records and correlate them to affected requests. <script>window.bad=true</script>', rank: 1, distance: 0.2 };
  return inspection({ status: 'completed', next_nodes: [], charged: { model: 3, tool: 1 }, state: {
    incident: { service: 'billing', summary: 'Dependency calls fail immediately' }, evidence: [
      { id: 'E1', kind: 'log', timestamp: '2026-11-01T00:00:00Z', content: 'NXDOMAIN returned before any connection attempt.' }],
    retrieval: { corpus_id: corpus }, retrieved_sources: [source], tool_history: [{ name: 'get_runbook_context' }],
    diagnosis: { status: 'insufficient_evidence', probable_cause: null, evidence_ids: ['E1'], source_ids: [source.id],
      missing_evidence: ['Request mapping for the affected workers.'], next_checks: ['Read traces to establish request correlation.'] },
  } });
}
