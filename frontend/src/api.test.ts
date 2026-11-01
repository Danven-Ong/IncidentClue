import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, isInspection, route, serializeRequest } from './api';
import { id, inspection, otherId } from './testFixture';
afterEach(() => vi.unstubAllGlobals());
describe('HTTP boundary', () => {
  it('accepts partial saved state but rejects malformed outer envelopes', () => {
    expect(isInspection(inspection({ state: {} }))).toBe(true);
    for (const patch of [{ charged: { model: -1, tool: 0 } }, { executing: 'false' }, { state: [] },
      { attempts: [{}] }, { updated_at: 'invalid' }, { status: 'finished' }])
      expect(isInspection({ ...inspection(), ...patch })).toBe(false);
  });
  it('uses decimal UTF-8 bytes including serialized structure', () => {
    expect(new TextEncoder().encode(serializeRequest('a'.repeat(99_998))).length).toBe(100_000);
    expect(() => serializeRequest('a'.repeat(99_999))).toThrow('100,000');
    expect(() => serializeRequest('界'.repeat(33_333))).toThrow('100,000');
  });
  it('rejects malformed routes locally and normalises UUID casing', () => {
    expect(route('#/')).toEqual({ id: null, error: null });
    expect(route('#/investigations/no')).toHaveProperty('error', expect.any(String));
    expect(route('#/investigations/' + id)).toEqual({ id, error: null });
  });
  it('rejects an inspection for a different UUID', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(inspection({ investigation_id: otherId }))));
    await expect(api.inspect(id)).rejects.toThrow('different investigation');
  });
  it('preserves controlled execution cause and UUID without retrying', async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({ error: { code: 'recovery_required', cause: 'uncertain_attempt', message: 'Resume refused.' }, investigation_id: id }, { status: 409 }));
    vi.stubGlobal('fetch', fetch);
    await expect(api.mutate(id, 'resume', '')).rejects.toMatchObject({ code: 'recovery_required', causeCode: 'uncertain_attempt', investigationId: id });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it('does not expose raw HTML errors or network exception details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('raw proxy diagnostics', { status: 502 })));
    await expect(api.inspect(id)).rejects.toMatchObject({ code: 'invalid_response' });
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('sensitive exception')));
    await expect(api.inspect(id)).rejects.toEqual(new ApiError('connection_lost', 'Could not receive a response from the local API.'));
  });
});
