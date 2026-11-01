import { useEffect, useRef, useState } from 'react';
import { api, ApiError, errorText, type Boundary, type Inspection } from './api';

type View = { data: Inspection | null; loading: boolean; stale: boolean; error: string;
  notice: string; requiresInspect: boolean; resumePending: boolean; pausePending: boolean; observed: string };
const initial: View = { data: null, loading: true, stale: true, error: '', notice: '',
  requiresInspect: false, resumePending: false, pausePending: false, observed: '' };
type Actions = { refresh: () => void; mutate: (action: 'resume' | 'pause', boundary: Boundary) => void };

export function useInvestigation(id: string) {
  const [view, setView] = useState<View>(initial);
  const actions = useRef<Actions>({ refresh: () => {}, mutate: () => {} });
  useEffect(() => {
    let live = true, sequence = 0, last: Inspection | null = null, readFailed = false;
    let read: AbortController | null = null, timer: ReturnType<typeof setTimeout> | undefined;
    const pending = { resume: false, pause: false };
    const posts = new Set<AbortController>();
    const update = (patch: Partial<View>) => { if (live) setView(current => ({ ...current, ...patch })); };
    const visible = () => document.visibilityState !== 'hidden' && navigator.onLine;
    function schedule() {
      clearTimeout(timer);
      const terminal = last && ['completed', 'failed', 'recovery_required'].includes(last.status);
      if (live && visible() && !readFailed && (pending.resume || (last && !terminal && (last.executing || (last.pause_requested && last.status === 'running')))))
        timer = setTimeout(() => { void inspect(false); }, 2000);
    }
    async function inspect(manual: boolean) {
      clearTimeout(timer); read?.abort();
      const current = ++sequence;
      if (!visible()) { update({ stale: true, loading: false }); return; }
      const controller = new AbortController(); read = controller;
      update({ loading: true });
      try {
        const value = await api.inspect(id, controller.signal);
        if (!live || current !== sequence) return;
        last = value; readFailed = false;
        update({ data: value, stale: false, error: '', observed: new Date().toISOString(),
          ...(manual ? { requiresInspect: false, notice: '' } : {}) });
      } catch (error) {
        if (!live || current !== sequence || controller.signal.aborted) return;
        readFailed = true; update({ stale: true, error: errorText(error) });
      } finally {
        if (live && current === sequence) { read = null; update({ loading: false }); schedule(); }
      }
    }
    async function mutate(action: 'resume' | 'pause', boundary: Boundary) {
      if (pending[action] || !visible()) return;
      pending[action] = true;
      update({ [action === 'resume' ? 'resumePending' : 'pausePending']: true, error: '', notice: '' });
      const controller = new AbortController(); posts.add(controller);
      schedule();
      try {
        // POST snapshots are validated but never replace an independently newer read.
        await api.mutate(id, action, boundary, controller.signal);
        update({ notice: '' });
      } catch (error) {
        if (!live) return;
        const uncertain = !(error instanceof ApiError) || error.status === 0 || ['invalid_response', 'internal_error'].includes(error.code);
        update({ requiresInspect: true,
          error: errorText(error), notice: uncertain
            ? 'Response outcome unknown. Execution may continue. Inspect state before another resume.'
            : `${errorText(error)} ${error instanceof ApiError ? [error.code, error.causeCode].filter(Boolean).join(' · ') + '. ' : ''}Inspect saved state and charged attempts before another resume.` });
      } finally {
        posts.delete(controller); pending[action] = false;
        if (live) {
          update({ [action === 'resume' ? 'resumePending' : 'pausePending']: false });
          void inspect(false);
        }
      }
    }
    function availability() {
      clearTimeout(timer);
      if (visible()) { readFailed = false; void inspect(false); }
      else { ++sequence; read?.abort(); read = null; update({ stale: true, loading: false }); }
    }
    actions.current = { refresh: () => { readFailed = false; void inspect(true); },
      mutate: (action, boundary) => { void mutate(action, boundary); } };
    void inspect(false);
    document.addEventListener('visibilitychange', availability);
    window.addEventListener('online', availability); window.addEventListener('offline', availability);
    return () => {
      live = false; ++sequence; clearTimeout(timer); read?.abort();
      posts.forEach(controller => controller.abort()); // Stops waiting, not backend execution.
      document.removeEventListener('visibilitychange', availability);
      window.removeEventListener('online', availability); window.removeEventListener('offline', availability);
    };
  }, [id]);
  return { ...view, refresh: () => actions.current.refresh(),
    mutate: (action: 'resume' | 'pause', boundary: Boundary) => actions.current.mutate(action, boundary) };
}
