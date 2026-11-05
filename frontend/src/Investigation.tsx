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

function runbookGroups(state: JsonObject) {
  const groups: { key: string; passages: (JsonObject & { id: string; text: string })[] }[] = [];
  const sources = Array.isArray(state.retrieved_sources) ? state.retrieved_sources : [];
  for (const source of sources) {
    if (!isObject(source) || typeof source.id !== 'string' || typeof source.text !== 'string') continue;
    const identity = [source.corpus_id, source.document_id, source.document_version, source.document_sha256, source.path];
    const key = identity.every(value => typeof value === 'string' && value.length > 0) ? JSON.stringify(identity) : source.id;
    let group = groups.find(group => group.key === key);
    if (!group) { group = { key, passages: [] }; groups.push(group); }
    group.passages.push(source as JsonObject & { id: string; text: string });
  }
  return groups;
}

function RunbookCitations({ state }: { state: JsonObject }) {
  const groups = runbookGroups(state);
  const diagnosis = isObject(state.diagnosis) ? state.diagnosis : null;
  const ids = diagnosis && strings(diagnosis.source_ids) ? [...new Set(diagnosis.source_ids)] : [];
  if (!ids.length) return null;
  return <div className="references" aria-label="Runbook citations"><span className="citation-label">Sources</span>{groups.map(group => {
    if (!group.passages.some(passage => ids.includes(passage.id))) return null;
    const source = group.passages[0];
    const label = `${text(source.title)} · v${text(source.document_version)}`;
    return <button key={group.key} className="reference runbook-reference" aria-label={label} title={label} onClick={() => {
      const node = document.getElementById('source-' + encodeURIComponent(group.key)); node?.scrollIntoView({ block: 'center' }); node?.focus();
    }}>{label}</button>;
  })}{ids.filter(id => !groups.some(group => group.passages.some(passage => passage.id === id))).map(id =>
    <span key={id}><code>{id}</code> · passage unavailable</span>)}</div>;
}

export function RunbookSources({ state, showCitations = true }: { state: JsonObject; showCitations?: boolean }) {
  const sources = Array.isArray(state.retrieved_sources) ? state.retrieved_sources : [];
  const groups = runbookGroups(state);
  const diagnosis = isObject(state.diagnosis) ? state.diagnosis : null;
  const citedIds = diagnosis && strings(diagnosis.source_ids) ? [...new Set(diagnosis.source_ids)] : [];
  const savedSource = (id: string) => sources.find(source => isObject(source) && source.id === id && typeof source.text === 'string');
  const unavailableCitedIds = citedIds.filter(id => !savedSource(id));
  const lookedUp = Array.isArray(state.tool_history) && state.tool_history.some(h => isObject(h) && h.name === 'get_runbook_context');
  return <section className="content-section runbook-panel"><h2>Runbook guidance</h2>
    {showCitations && <RunbookCitations state={state} />}
    {!sources.length && <p className="muted">{lookedUp ? 'The lookup returned no matching passages.' : 'No runbook lookup has been saved.'}</p>}
    <div className="evidence-list">{groups.map(group => {
      const source = group.passages[0];
      return <article className="evidence" id={'source-' + encodeURIComponent(group.key)} key={group.key} tabIndex={-1}>
        <div className="source-heading"><div><h3>{text(source.title)}</h3>
          <p className="muted">Version {text(source.document_version)}</p></div></div>
        {group.passages.map(s => <section className="runbook-passage" key={s.id}>
          <div className="passage-heading"><h4>{text(s.section)}</h4></div>
        <div className="source-excerpt">{s.text.split(/\n\s*\n/).map((paragraph, index) =>
          <p className="preserve" key={index}>{paragraph.replace(/^#{1,6}\s+[^\n]+\n?/, '').trim()}</p>)}</div>
        <details><summary>Source details</summary><p>{text(s.path)}</p>
          <p>Lines {String(s.line_start)}–{String(s.line_end)}</p>
          <p>Document SHA256: <code>{text(s.document_sha256)}</code></p><p>Corpus: <code>{text(s.corpus_id)}</code></p></details>
        </section>)}
      </article>;
    })}{sources.map((source, index) => (!isObject(source) || typeof source.id !== 'string' || typeof source.text !== 'string')
      ? <JsonDetails key={index} label="Unrecognised saved source" value={source} /> : null)}</div>
    {!!unavailableCitedIds.length && <p className="muted">Some cited passages are unavailable.</p>}
  </section>;
}
export function DiagnosisSupport({ state }: { state: JsonObject }) {
  const support = isObject(state.diagnosis) ? state.diagnosis.support_assessment : null;
  if (support == null) return <section className="support-assessment"><h3>Evidence support</h3>
    <p className="muted">No support details saved.</p></section>;
  if (!isObject(support) || !strings(support.blocking_gaps) || !Array.isArray(support.evidence_links)
    || !support.evidence_links.every(link => isObject(link) && typeof link.evidence_id === 'string'
      && typeof link.excerpt === 'string' && typeof link.relation === 'string'))
    return <section className="support-assessment"><h3>Evidence support</h3>
      <p className="muted">Saved support format is not recognised.</p><JsonDetails label="Support details" value={support} /></section>;
  const evidence = Array.isArray(state.evidence) ? state.evidence.filter(isObject) : [];
  return <section className="support-assessment"><h3>Evidence support</h3>
    {support.evidence_links.map((link, i) => {
      const observation = evidence.find(e => e.id === link.evidence_id);
      return <div className="support-link" key={i}>
        <p className="support-label">Observation {link.evidence_id}</p>
        <blockquote className="preserve">{link.excerpt}</blockquote>
        <p className="preserve">{link.relation}</p>
        {observation ? <details><summary>Full observation {link.evidence_id}</summary>
          <p className="preserve">{text(observation.content)}</p></details>
          : <p className="muted">The cited observation is unavailable.</p>}
      </div>;
    })}
    {!!support.blocking_gaps.length && <><h3>Unresolved gaps</h3>
      <ul>{support.blocking_gaps.map((gap, i) => <li key={i}>{gap}</li>)}</ul></>}
  </section>;
}

function Diagnosis({ state }: { state: JsonObject }) {
  const d = state.diagnosis;
  if (!isObject(d)) return <p className="muted">No validated diagnosis saved.</p>;
  const known = ['hypothesis', 'insufficient_evidence'].includes(String(d.status))
    && strings(d.evidence_ids) && strings(d.missing_evidence) && strings(d.next_checks)
    && (d.source_ids === undefined || strings(d.source_ids))
    && (d.status === 'hypothesis' ? typeof d.probable_cause === 'string' && !!d.probable_cause.trim()
      && Array.isArray(d.evidence_ids) && d.evidence_ids.length > 0 : d.probable_cause === null);
  if (!known) return <><p className="muted">Saved diagnosis format is not recognised.</p><JsonDetails label="Diagnosis details" value={d} /></>;
  const references = d.evidence_ids as string[];
  const evidence = Array.isArray(state.evidence) ? state.evidence.filter(isObject) : [];
  return <>
    <div className="finding"><span className="uncertainty">{d.status === 'hypothesis' ? 'Hypothesis' : 'Insufficient evidence'}</span>
      {typeof d.probable_cause === 'string' && <p className="cause preserve">{d.probable_cause}</p>}
      {!!references.length && <div className="references">{references.map(id => {
        const exists = evidence.some(e => e.id === id);
        return exists ? <button key={id} className="reference" onClick={() => {
          const node = document.getElementById('evidence-' + encodeURIComponent(id)); node?.scrollIntoView({ block: 'center' }); node?.focus();
        }}>{id}</button> : <span key={id}><code>{id}</code> · evidence unavailable</span>;
      })}</div>}
      <RunbookCitations state={state} />
    </div>
    <div className="result-grid">{!!(d.missing_evidence as string[]).length && <section><h3>Missing evidence</h3>
      <ul>{(d.missing_evidence as string[]).map((s, i) => <li key={i}>{s}</li>)}</ul></section>}
      <section><h3>Next checks</h3>{(d.next_checks as string[]).length
        ? <ul>{(d.next_checks as string[]).map((s, i) => <li key={i}>{s}</li>)}</ul>
        : <p className="muted">None recorded.</p>}</section></div>
    <DiagnosisSupport state={state} />
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
  const hasRunbookReferences = data && isObject(data.state.diagnosis) && strings(data.state.diagnosis.source_ids)
    && data.state.diagnosis.source_ids.length > 0;
  async function copy() {
    try { await navigator.clipboard.writeText(id); setCopied('UUID copied.'); }
    catch { setCopied('Could not copy. Select the UUID below.'); }
  }
  const diagnosis = data && <section className="content-section"><h2>Diagnosis</h2><Diagnosis state={data.state} /></section>;
  const metadata = data && <div className="metadata"><span>Model attempts <strong>{data.charged.model} / {data.limits.model}</strong></span>
    <span>Tool attempts <strong>{data.charged.tool} / {data.limits.tool}</strong></span>
    <span>Model <code>{data.model}</code></span></div>;
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
      {data.status !== 'completed' && metadata}
      {data.status === 'completed' && !data.compatible && <p className="hint">Incompatible runtime</p>}
      {data.status !== 'completed' && <section className="controls"><h2>Execution</h2>
        <div className="execution-flags">{data.executing && <span>Running</span>}
          {!data.compatible && <span>Incompatible runtime</span>}
          {data.pause_requested && <span>Pause requested</span>}{data.recovery_needed && <span>Recovery check required</span>}
          {data.error_code && <code>{data.error_code}</code>}</div>
        <div className="actions"><label className="inline-field">Pause after<select value={boundary} disabled={view.resumePending} onChange={e => setBoundary(e.target.value as Boundary)}>
          <option value="">No boundary</option>{boundaries.map(b => <option key={b} value={b}>After {b}</option>)}</select></label>
          <button className="primary" disabled={!canResume} onClick={() => view.mutate('resume', boundary)}>{view.resumePending ? 'Resuming…' : 'Resume'}</button>
          <button disabled={!canPause} onClick={() => view.mutate('pause', '')}>{view.pausePending ? 'Requesting pause…' : 'Request pause'}</button></div>
        <p className="hint">{data.status === 'paused' && data.charged.model === 0 && data.charged.tool === 0 ? 'Created paused. No attempts charged. Resume starts execution.'
          : 'Pause takes effect at a saved boundary. Closing this page does not cancel execution.'}</p>
        {data.status === 'failed' || data.status === 'recovery_required' ? <p className="warning-text">Resume refused for this saved state. Inspect details; start a new investigation if needed.</p> : null}
        <details><summary>Pause boundaries</summary><p className="hint">Decide selects the next lookup; execute reads recorded evidence; diagnose generates a hypothesis; validate checks the result. Completion takes precedence at the final boundary.</p></details>
      </section>}
      <div className="report-layout"><div className="report-main">
        {diagnosis}
        <section className="content-section"><h2>Evidence</h2><Evidence value={data.state.evidence} /></section>
      </div>
      {(isObject(data.state.retrieval) || (Array.isArray(data.state.retrieved_sources) && data.state.retrieved_sources.length > 0)
        || hasRunbookReferences) && <RunbookSources state={data.state} showCitations={false} />}
      </div>
      {data.status === 'completed' && metadata}
      <section className="content-section"><h2>Saved activity</h2>
        {Array.isArray(data.state.tool_history) && data.state.tool_history.length > 0
          ? data.state.tool_history.map((item, i) => <JsonDetails key={i} label={`Tool record ${i + 1}`} value={item} />)
          : <p className="muted">No saved tool activity.</p>}
        {data.attempts.length > 0 && <div className="table-scroll"><table><caption>Charged attempts</caption><thead><tr><th>Operation</th><th>Kind</th><th>Status</th></tr></thead>
          <tbody>{data.attempts.map((a, i) => <tr key={i}><td><code>{a.operation}</code></td><td>{a.kind}</td><td>{a.status}</td></tr>)}</tbody></table></div>}
      </section>
      <section className="content-section"><JsonDetails label="Saved state details" value={data} /></section>
    </>}
  </div>;
}
