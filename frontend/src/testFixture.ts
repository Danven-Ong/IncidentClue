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
