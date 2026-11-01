import { useEffect, useRef, useState } from 'react';
import { ApiError, api, errorText, openInvestigation, serializeRequest } from './api';

const defaults = { incidentId: '', service: '', summary: '', evidence: '[]', health: '', logs: '', model: '' };
const sample = {
  incidentId: 'checkout-001', service: 'checkout', summary: 'Checkout fails to start after deployment.',
  evidence: '[]', health: JSON.stringify({ service: 'checkout', evidence: [{
    id: 'H1', timestamp: '2026-10-28T03:06:00Z', kind: 'health',
    content: 'Synthetic diagnostic health snapshot: checkout is not ready. Its readiness detail reports that required configuration PAYMENT_API_URL is missing.',
  }] }, null, 2), logs: '', model: '',
};

export function NewIncident() {
  const [form, setForm] = useState(defaults);
  const [error, setError] = useState('');
  const [unknown, setUnknown] = useState(false);
  const [pending, setPending] = useState(false);
  const [confirmSample, setConfirmSample] = useState(false);
  const busy = useRef(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    controller.current = new AbortController();
    return () => controller.current?.abort();
  }, []);
  const fields = (key: keyof typeof defaults) => ({
    value: form[key], onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setForm(current => ({ ...current, [key]: e.target.value })),
  });
  async function submit(e: React.SubmitEvent<HTMLFormElement>) {
    e.preventDefault();
    if (busy.current) return;
    setError('');
    let body: string;
    try {
      const parse = (value: string, label: string) => {
        try { return JSON.parse(value); } catch { throw new Error(`${label}: invalid JSON.`); }
      };
      body = serializeRequest({ incident: { incident_id: form.incidentId, service: form.service,
        summary: form.summary, evidence: parse(form.evidence, 'Incident evidence') },
      health: parse(form.health, 'Health snapshot'), logs: form.logs.trim() ? parse(form.logs, 'Logs') : null,
      model: form.model.trim() ? form.model : null });
    } catch (error) { setError(error instanceof Error ? error.message : 'Check the input.'); return; }
    busy.current = true; setPending(true);
    const signal = controller.current?.signal;
    try { const value = await api.create(body, signal); if (!signal?.aborted) openInvestigation(value.investigation_id); }
    catch (error) {
      if (signal?.aborted) return;
      const uncertain = !(error instanceof ApiError) || error.status === 0 || error.code === 'invalid_response' || error.status >= 500;
      setUnknown(uncertain); setError(errorText(error));
    } finally { busy.current = false; setPending(false); }
  }
  function loadSample() {
    const dirty = Object.keys(defaults).some(k => form[k as keyof typeof defaults] !== defaults[k as keyof typeof defaults]);
    if (dirty && !confirmSample) { setConfirmSample(true); return; }
    setForm(sample); setConfirmSample(false);
  }
  return <div className="entry">
    <div className="section-heading"><h1>New incident</h1><button type="button" className="quiet" onClick={loadSample} disabled={pending}>Load synthetic example</button></div>
    {confirmSample && <div className="notice" role="alert">Replace entered input with the synthetic example?
      <div className="actions"><button type="button" onClick={loadSample}>Replace input</button><button type="button" className="quiet" onClick={() => setConfirmSample(false)}>Cancel</button></div></div>}
    <form onSubmit={submit} aria-busy={pending}>
      <fieldset disabled={pending}>
        <div className="field-grid"><label>Incident ID<input required autoComplete="off" {...fields('incidentId')} /></label>
          <label>Service<input required autoComplete="off" {...fields('service')} /></label></div>
        <label>Summary<textarea required rows={3} {...fields('summary')} /></label>
        <label>Incident evidence · JSON<textarea className="code" required rows={3} spellCheck={false} {...fields('evidence')} /></label>
        <label>Health snapshot · JSON<textarea className="code" required rows={6} spellCheck={false}
          placeholder={'{"service":"checkout","evidence":[]}'} aria-describedby="health-help" {...fields('health')} /></label>
        <p id="health-help" className="hint">Recorded data. Empty evidence means no observations.</p>
        <details><summary>Optional logs and model</summary>
          <label>Logs · JSON<textarea className="code" rows={4} spellCheck={false} placeholder="null" {...fields('logs')} /></label>
          <label>Model<input maxLength={200} placeholder="Server default" {...fields('model')} /></label>
        </details>
      </fieldset>
      {error && <div className="notice error" role="alert">{error}</div>}
      {unknown && <div className="notice warning" role="alert">Creation outcome unknown. An investigation may have been saved without a received UUID. Submitting again can create a duplicate.
        <label className="check"><input type="checkbox" checked={false} onChange={() => setUnknown(false)} />Allow another creation attempt</label></div>}
      <div className="actions"><button className="primary" disabled={pending || unknown}>{pending ? 'Creating…' : 'Create incident'}</button></div>
    </form>
  </div>;
}
