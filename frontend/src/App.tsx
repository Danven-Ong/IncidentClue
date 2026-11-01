import { useEffect, useState } from 'react';
import { isUuid, openInvestigation, route } from './api';
import { Investigation } from './Investigation';
import { NewIncident } from './NewIncident';

export function App() {
  const [location, setLocation] = useState(() => route(window.location.hash));
  const [uuid, setUuid] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    const changed = () => { setLocation(route(window.location.hash)); setError(''); };
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, []);
  return <>
    <a className="skip" href="#main-content" onClick={e => { e.preventDefault(); document.getElementById('main-content')?.focus(); }}>Skip to content</a>
    <header className="app-header"><a className="brand" href="#/" aria-label="IncidentClue new incident">
      <svg viewBox="0 0 32 32" aria-hidden="true" fill="none"><path d="M23.4 8.1a10.4 10.4 0 1 0 0 11M23.8 23.8l6 6" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" /></svg>IncidentClue</a>
      <span className="local-label">Local workspace</span>{location.id && <a className="button" href="#/">New incident</a>}</header>
    <main id="main-content" tabIndex={-1}>
      <form className="open-form" onSubmit={e => { e.preventDefault(); if (!isUuid(uuid.trim())) { setError('Enter a valid investigation UUID.'); return; } setError(''); openInvestigation(uuid.trim()); }}>
        <label htmlFor="open-uuid">Investigation UUID</label><div className="open-fields"><input id="open-uuid" value={uuid} onChange={e => setUuid(e.target.value)} autoComplete="off" spellCheck={false}
          aria-invalid={!!error} aria-describedby={error ? 'open-error' : undefined} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" /><button>Open</button></div>
        {error && <p className="error-text" id="open-error" role="alert">{error}</p>}
      </form>
      {location.error && <div className="notice error" role="alert">{location.error}</div>}
      {location.id ? <Investigation key={location.id} id={location.id} /> : <NewIncident />}
    </main>
  </>;
}
