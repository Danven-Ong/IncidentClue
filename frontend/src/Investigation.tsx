import { useState } from 'react';
import { boundaries, isObject, strings, type Boundary, type JsonObject } from './api';
import { useInvestigation } from './useInvestigation';

function text(v: unknown) { return typeof v === 'string' ? v : 'Unavailable'; }
function time(value: string) { return Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString(undefined, { timeZoneName: 'short' }) : value; }
function JsonDetails({ value, label }: { value: unknown; label: string }) {
  return <details><summary>{label}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;
}
function Evidence({ value }: { value: unknown }) {
  if (!Array.isArray(value) || !value.length) return <p className="muted">No saved observations.</p>;
  return <div className="evidence-list">{value.map((item: unknown, index) => {
    if (!isObject(item)) return <JsonDetails key={index} label="Unrecognised evidence" value={item} />;
    return <article className="evidence" id={`evidence-${encodeURIComponent(text(item.id))}`} key={index} tabIndex={-1}>
      <div className="evidence-heading"><span><code>{text(item.id)}</code><span className="kind">{text(item.kind)}</span></span>
        <span className="timestamp">{typeof item.timestamp === 'string' ? time(item.timestamp) : 'Time unavailable'}</span></div>
      <p className="preserve">{text(item.content)}</p>
    </article>;
  })}</div>;
}
function Diagnosis({ state }: { state: JsonObject }) {
  const d = state.diagnosis;
  if (!isObject(d)) return <p className="muted">No validated diagnosis saved.</p>;
  const known = ['hypothesis', 'insufficient_evidence'].includes(String(d.status))
    && strings(d.evidence_ids) && strings(d.missing_evidence) && strings(d.next_checks)
    && (d.status === 'hypothesis' ? typeof d.probable_cause === 'string' && !!d.probable_cause.trim()
      && Array.isArray(d.evidence_ids) && d.evidence_ids.length > 0 : d.probable_cause === null);
  if (!known) return <><p className="muted">Saved diagnosis format is not recognised.</p><JsonDetails label="Diagnosis details" value={d} /></>;
  const references = d.evidence_ids as string[];
  const evidence = Array.isArray(state.evidence) ? state.evidence.filter(isObject) : [];
  return <>
    <div className="finding"><span className="uncertainty">{d.status === 'hypothesis' ? 'Hypothesis · unconfirmed' : 'Insufficient evidence'}</span>
      {typeof d.probable_cause === 'string' && <p className="cause preserve">{d.probable_cause}</p>}
      {!!references.length && <div className="references">{references.map(id => {
        const exists = evidence.some(e => e.id === id);
        return exists ? <button key={id} className="reference" onClick={() => {
          const node = document.getElementById('evidence-' + encodeURIComponent(id)); node?.scrollIntoView({ block: 'center' }); node?.focus();
        }}>{id}</button> : <span key={id}><code>{id}</code> · evidence unavailable</span>;
      })}</div>}
      <p className="hint">References identify observations; they do not confirm causation.</p>
    </div>
    <div className="result-grid"><section><h3>Missing evidence</h3>{(d.missing_evidence as string[]).length
      ? <ul>{(d.missing_evidence as string[]).map((s, i) => <li key={i}>{s}</li>)}</ul> : <p className="muted">None recorded.</p>}</section>
      <section><h3>Suggested next checks</h3>{(d.next_checks as string[]).length
        ? <ul>{(d.next_checks as string[]).map((s, i) => <li key={i}>{s}</li>)}</ul> : <p className="muted">None recorded.</p>}
        <p className="hint">Suggestions only. No remediation is executed.</p></section></div>
  </>;
}

export function Investigation({ id }: { id: string }) {
  const view = useInvestigation(id);
  const [boundary, setBoundary] = useState<Boundary>('');
  const [copied, setCopied] = useState('');
  const data = view.data;
  const terminal = data && ['completed', 'failed', 'recovery_required'].includes(data.status);
  const canResume = data && !view.stale && !view.requiresInspect && !view.resumePending && !view.pausePending && !data.executing
    && (data.status === 'completed' || (data.compatible && !['failed', 'recovery_required'].includes(data.status)));
  const canPause = data && !view.stale && !terminal && !view.pausePending && !data.pause_requested;
  const incident = data && isObject(data.state.incident) ? data.state.incident : null;
  async function copy() {
    try { await navigator.clipboard.writeText(id); setCopied('UUID copied.'); }
    catch { setCopied('Could not copy. Select the UUID below.'); }
  }
  const diagnosis = data && <section className="content-section"><h2>Diagnosis</h2><Diagnosis state={data.state} /></section>;
  return <div className="investigation">
    <div className="section-heading"><h1>{incident ? text(incident.summary) : data?.incident_id || 'Investigation'}</h1>
      {data && <span className="status" role="status">{data.status.replaceAll('_', ' ')}</span>}</div>
    <div className="identity"><code>{id}</code><button className="quiet" onClick={copy}>Copy UUID</button><span role="status">{copied}</span></div>
    {incident && <p className="muted incident-context">{text(incident.service)} · {data?.incident_id}</p>}
    <div className="observe"><span>{view.loading ? 'Inspecting…' : view.observed ? `Observed ${time(view.observed)}` : 'No observation received'}
      {view.stale && data ? ' · stale data' : ''}</span><button onClick={view.refresh} disabled={view.loading}>Inspect state</button></div>
    <span className="sr-only" role="status">{view.announcement}</span>
    {view.error && <div role="alert" className="notice error">{view.error}</div>}
    {view.notice && <div role="status" className="notice">{view.notice}</div>}
    {!data && !view.loading && <p className="muted">No investigation data available. Check the UUID and API, then inspect again.</p>}
    {data && <>
      {data.status === 'completed' && diagnosis}
      <div className="metadata"><span>Model attempts <strong>{data.charged.model} / {data.limits.model}</strong></span>
        <span>Tool attempts <strong>{data.charged.tool} / {data.limits.tool}</strong></span>
        <span>Saved model <code>{data.model}</code></span></div>
      {data.status === 'completed' && <p className="hint">{data.executing ? 'Executor active' : 'No executor observed'} · {data.compatible ? 'Compatible runtime' : 'Incompatible runtime'}{data.pause_requested ? ' · pause requested' : ''}</p>}
      {data.status !== 'completed' && <section className="controls"><h2>Execution</h2>
        <div className="execution-flags"><span>{data.executing ? 'Executor active' : 'No executor observed'}</span>
          <span>{data.compatible ? 'Compatible runtime' : 'Incompatible runtime'}</span>
          {data.pause_requested && <span>Pause requested</span>}{data.recovery_needed && <span>Recovery check required</span>}
          {data.error_code && <code>{data.error_code}</code>}</div>
        <div className="actions"><label className="inline-field">Pause after<select value={boundary} disabled={view.resumePending} onChange={e => setBoundary(e.target.value as Boundary)}>
          <option value="">No boundary</option>{boundaries.map(b => <option key={b} value={b}>After {b}</option>)}</select></label>
          <button className="primary" disabled={!canResume} onClick={() => view.mutate('resume', boundary)}>{view.resumePending ? 'Resuming…' : 'Resume'}</button>
          <button disabled={!canPause} onClick={() => view.mutate('pause', '')}>{view.pausePending ? 'Requesting pause…' : 'Request pause'}</button></div>
        <p className="hint">{data.status === 'paused' && data.charged.model === 0 && data.charged.tool === 0 ? 'Created paused. No attempts charged. Resume starts execution.'
          : 'Pause takes effect at a saved boundary. Closing this page does not cancel execution.'}</p>
        {data.status === 'failed' || data.status === 'recovery_required' ? <p className="warning-text">Resume refused for this saved state. Inspect details; start a new investigation if needed.</p> : null}
        {data.next_nodes.length > 0 && <p className="hint">Next nodes: <code>{data.next_nodes.join(', ')}</code></p>}
        <details><summary>Pause boundaries</summary><p className="hint">Decide selects the next lookup; execute reads recorded evidence; diagnose generates a hypothesis; validate checks the result. Completion takes precedence at the final boundary.</p></details>
      </section>}
      {data.status !== 'completed' && diagnosis}
      <section className="content-section"><h2>Evidence</h2><Evidence value={data.state.evidence} /></section>
      <section className="content-section"><h2>Saved activity</h2>
        {Array.isArray(data.state.tool_history) && data.state.tool_history.length > 0
          ? data.state.tool_history.map((item, i) => <JsonDetails key={i} label={`Tool record ${i + 1}`} value={item} />)
          : <p className="muted">No saved tool activity.</p>}
        {data.attempts.length > 0 && <div className="table-scroll"><table><caption>Charged attempts</caption><thead><tr><th>Operation</th><th>Kind</th><th>Status</th></tr></thead>
          <tbody>{data.attempts.map((a, i) => <tr key={i}><td><code>{a.operation}</code></td><td>{a.kind}</td><td>{a.status}</td></tr>)}</tbody></table></div>}
        <p className="hint">Recorded order. Not a complete event timeline.</p>
      </section>
      <section className="content-section"><JsonDetails label="Saved state details" value={data} /></section>
    </>}
  </div>;
}
