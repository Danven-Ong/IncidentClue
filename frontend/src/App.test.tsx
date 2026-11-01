import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { id, inspection, otherId } from './testFixture';

beforeEach(() => { window.location.hash = '/'; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
async function atInvestigation() {
  window.location.hash = '/investigations/' + id;
  render(<App />);
  await screen.findByText('Created paused. No attempts charged. Resume starts execution.');
}
describe('incident form', () => {
  it('uses direct copy and requires confirmation before replacing entered data', async () => {
    render(<App />); const user = userEvent.setup();
    expect(screen.getByRole('heading', { name: 'New incident' })).toBeTruthy();
    expect(screen.queryByText('What happened?')).toBeNull();
    await user.type(screen.getByLabelText('Incident ID'), 'original');
    await user.click(screen.getByRole('button', { name: 'Load synthetic example' }));
    expect((screen.getByLabelText('Incident ID') as HTMLInputElement).value).toBe('original');
    await user.click(screen.getByRole('button', { name: 'Replace input' }));
    expect((screen.getByLabelText('Incident ID') as HTMLInputElement).value).toBe('checkout-001');
  });
  it('retains input and prevents blind creation retry after losing the response', async () => {
    const fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch')); vi.stubGlobal('fetch', fetch);
    render(<App />); const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Load synthetic example' }));
    await user.click(screen.getByRole('button', { name: 'Create incident' }));
    await screen.findByText(/Creation outcome unknown/);
    expect((screen.getByRole('button', { name: 'Create incident' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText('Incident ID') as HTMLInputElement).value).toBe('checkout-001');
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it('reports JSON syntax errors before any request', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch); render(<App />); const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Load synthetic example' }));
    fireEvent.change(screen.getByLabelText('Health snapshot · JSON'), { target: { value: '{invalid' } });
    await user.click(screen.getByRole('button', { name: 'Create incident' }));
    expect(screen.getByRole('alert').textContent).toContain('Health snapshot: invalid JSON'); expect(fetch).not.toHaveBeenCalled();
  });
});
describe('execution observation', () => {
  it('keeps pause usable during resume and prevents duplicate local resumes', async () => {
    let finish: (r: Response) => void = () => {};
    const fetch = vi.fn((url: string) => url.endsWith('/resume')
      ? new Promise<Response>(resolve => { finish = resolve; }) : Promise.resolve(Response.json(inspection())));
    vi.stubGlobal('fetch', fetch); await atInvestigation();
    const resume = screen.getByRole('button', { name: 'Resume' });
    fireEvent.click(resume); fireEvent.click(resume);
    expect((resume as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Request pause' }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Request pause' }));
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.endsWith('/pause'))).toBe(true));
    expect(fetch.mock.calls.filter(([url]) => url.endsWith('/resume'))).toHaveLength(1);
    await act(async () => finish(Response.json(inspection({ status: 'completed' }))));
  });
  it('requires a successful manual inspection after an unknown resume outcome', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => url.endsWith('/resume')
      ? Promise.reject(new TypeError('lost')) : Promise.resolve(Response.json(inspection()))));
    await atInvestigation(); fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
    await screen.findByText(/Response outcome unknown/);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Resume' }) as HTMLButtonElement).disabled).toBe(true));
    await waitFor(() => expect((screen.getByRole('button', { name: 'Inspect state' }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Inspect state' }));
    await waitFor(() => expect((screen.getByRole('button', { name: 'Resume' }) as HTMLButtonElement).disabled).toBe(false));
  });
  it('retains observed budgets when a later read fails', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(Response.json(inspection({ charged: { model: 1, tool: 1 } })))
      .mockResolvedValue(Response.json({ error: { code: 'storage_unavailable', cause: null, message: 'Storage unavailable.' } }, { status: 503 }));
    vi.stubGlobal('fetch', fetch); window.location.hash = '/investigations/' + id; render(<App />);
    await screen.findByText('1 / 3'); fireEvent.click(screen.getByRole('button', { name: 'Inspect state' }));
    await screen.findByText('Storage unavailable.'); expect(screen.getByText('1 / 3')).toBeTruthy();
    expect(screen.getByText(/stale data/)).toBeTruthy();
  });
  it('does not let a response for a previous UUID overwrite the current page', async () => {
    let finish: (r: Response) => void = () => {};
    vi.stubGlobal('fetch', vi.fn((url: string) => url.endsWith(id)
      ? new Promise<Response>(resolve => { finish = resolve; })
      : Promise.resolve(Response.json(inspection({ investigation_id: otherId, incident_id: 'other', state: {} })))));
    window.location.hash = '/investigations/' + id; render(<App />);
    await act(async () => { window.location.hash = '/investigations/' + otherId; window.dispatchEvent(new HashChangeEvent('hashchange')); });
    await screen.findByRole('heading', { name: 'other' });
    await act(async () => finish(Response.json(inspection())));
    expect(screen.getByRole('heading', { name: 'other' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Checkout unavailable' })).toBeNull();
  });
  it('keeps partial incompatible state inspectable and blocks execution', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(inspection({ compatible: false, state: {} }))));
    window.location.hash = '/investigations/' + id; render(<App />);
    await screen.findByText('Incompatible runtime');
    expect(screen.getByText('No validated diagnosis saved.')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Resume' }) as HTMLButtonElement).disabled).toBe(true);
  });
});
