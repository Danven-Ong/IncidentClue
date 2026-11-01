import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useInvestigation } from './useInvestigation';
import { id, inspection } from './testFixture';
beforeEach(() => vi.useFakeTimers());
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const flush = async () => { await act(async () => { await Promise.resolve(); }); };

it('stops polling at a paused boundary even with a persisted pause flag', async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json(inspection({ pause_requested: true })));
  vi.stubGlobal('fetch', fetch); renderHook(() => useInvestigation(id)); await flush();
  await act(async () => vi.advanceTimersByTimeAsync(6000)); expect(fetch).toHaveBeenCalledTimes(1);
});

it('does not overlap reads and stops observation after a read failure', async () => {
  let finish: (r: Response) => void = () => {};
  const fetch = vi.fn().mockResolvedValueOnce(Response.json(inspection({ status: 'running', executing: true })))
    .mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }))
    .mockRejectedValue(new TypeError('lost'));
  vi.stubGlobal('fetch', fetch); renderHook(() => useInvestigation(id)); await flush();
  await act(async () => vi.advanceTimersByTimeAsync(2000));
  await act(async () => vi.advanceTimersByTimeAsync(10_000)); expect(fetch).toHaveBeenCalledTimes(2);
  await act(async () => finish(Response.json(inspection({ status: 'running', executing: true }))));
  await act(async () => vi.advanceTimersByTimeAsync(2000)); expect(fetch).toHaveBeenCalledTimes(3);
  await act(async () => vi.advanceTimersByTimeAsync(10_000)); expect(fetch).toHaveBeenCalledTimes(3);
});

it('discards a superseded GET even when a transport ignores abort', async () => {
  let finish: (r: Response) => void = () => {};
  const fetch = vi.fn().mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }))
    .mockResolvedValueOnce(Response.json(inspection({ charged: { model: 2, tool: 1 } })));
  vi.stubGlobal('fetch', fetch); const { result } = renderHook(() => useInvestigation(id));
  await act(async () => result.current.refresh()); await flush();
  await act(async () => finish(Response.json(inspection())));
  expect(result.current.data?.charged.model).toBe(2);
});

it('stops while hidden and refreshes on return without sending POSTs', async () => {
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
  const fetch = vi.fn().mockImplementation(() => Promise.resolve(Response.json(inspection({ status: 'running', executing: true }))));
  vi.stubGlobal('fetch', fetch); renderHook(() => useInvestigation(id)); await flush();
  visibility.mockReturnValue('hidden');
  await act(async () => document.dispatchEvent(new Event('visibilitychange')));
  await act(async () => vi.advanceTimersByTimeAsync(6000)); expect(fetch).toHaveBeenCalledTimes(1);
  visibility.mockReturnValue('visible');
  await act(async () => document.dispatchEvent(new Event('visibilitychange'))); await flush();
  expect(fetch).toHaveBeenCalledTimes(2); expect(fetch.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
  visibility.mockRestore();
});
