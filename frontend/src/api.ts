export type JsonObject = Record<string, unknown>;
export type Counts = { model: number; tool: number };
export type Inspection = {
  investigation_id: string; incident_id: string;
  status: 'ready' | 'running' | 'paused' | 'completed' | 'failed' | 'recovery_required';
  executing: boolean; recovery_needed: boolean; compatible: boolean; pause_requested: boolean;
  model: string; error_code: string | null; checkpoint_id: string | null;
  next_nodes: string[]; state: JsonObject;
  attempts: { operation: string; kind: 'model' | 'tool'; status: 'in_flight' | 'done' | 'failed' }[];
  charged: Counts; limits: Counts; created_at: string; updated_at: string;
};
export const boundaries = ['decide', 'execute', 'diagnose', 'validate'] as const;
export type Boundary = '' | typeof boundaries[number];
export const isObject = (v: unknown): v is JsonObject => typeof v === 'object' && v !== null && !Array.isArray(v);
export const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(x => typeof x === 'string');
export const isUuid = (v: string) => /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(v);
const counts = (v: unknown) => isObject(v) && ['model', 'tool'].every(k => Number.isSafeInteger(v[k]) && Number(v[k]) >= 0);
const date = (v: unknown) => typeof v === 'string' && Number.isFinite(Date.parse(v));
const nullableString = (v: unknown) => v === null || typeof v === 'string';

export function isInspection(v: unknown): v is Inspection {
  return isObject(v) && typeof v.investigation_id === 'string' && isUuid(v.investigation_id)
    && typeof v.incident_id === 'string' && typeof v.model === 'string'
    && ['ready', 'running', 'paused', 'completed', 'failed', 'recovery_required'].includes(String(v.status))
    && ['executing', 'recovery_needed', 'compatible', 'pause_requested'].every(k => typeof v[k] === 'boolean')
    && nullableString(v.error_code) && nullableString(v.checkpoint_id)
    && strings(v.next_nodes) && isObject(v.state) && counts(v.charged) && counts(v.limits)
    && date(v.created_at) && date(v.updated_at) && Array.isArray(v.attempts)
    && v.attempts.every(a => isObject(a) && typeof a.operation === 'string'
      && ['model', 'tool'].includes(String(a.kind)) && ['in_flight', 'done', 'failed'].includes(String(a.status)));
}

export class ApiError extends Error {
  constructor(public code: string, message: string, public status = 0,
    public causeCode: string | null = null, public investigationId: string | null = null) { super(message); }
}

export function serializeRequest(value: unknown) {
  const body = JSON.stringify(value);
  if (new TextEncoder().encode(body).length > 100_000)
    throw new ApiError('request_too_large', 'Request exceeds 100,000 bytes. Reduce the input.');
  return body;
}

async function request(path: string, method: 'GET' | 'POST', signal?: AbortSignal, body?: string) {
  let response: Response;
  try {
    response = await fetch('/api' + path, {
      method, signal, body, cache: 'no-store',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError('connection_lost', 'Could not receive a response from the local API.');
  }
  let value: unknown;
  try { value = await response.json(); }
  catch { throw new ApiError('invalid_response', 'The API returned an unreadable response.', response.status); }
  if (!response.ok) {
    if (!isObject(value) || !isObject(value.error) || typeof value.error.code !== 'string'
      || typeof value.error.message !== 'string' || !nullableString(value.error.cause))
      throw new ApiError('invalid_response', 'The API returned an invalid error response.', response.status);
    throw new ApiError(value.error.code, value.error.message, response.status, value.error.cause as string | null,
      typeof value.investigation_id === 'string' && isUuid(value.investigation_id) ? value.investigation_id : null);
  }
  if (!isInspection(value)) throw new ApiError('invalid_response', 'The API returned an invalid investigation response.', response.status);
  return value;
}

export const api = {
  create: (body: string, signal?: AbortSignal) => request('/investigations', 'POST', signal, body),
  inspect: async (id: string, signal?: AbortSignal) => {
    const value = await request('/investigations/' + id, 'GET', signal);
    if (value.investigation_id.toLowerCase() !== id.toLowerCase())
      throw new ApiError('invalid_response', 'The API returned a different investigation.');
    return value;
  },
  mutate: async (id: string, action: 'resume' | 'pause', boundary: Boundary, signal?: AbortSignal) => {
    const value = await request(`/investigations/${id}/${action}${action === 'resume' && boundary ? '?pause_after=' + boundary : ''}`, 'POST', signal);
    if (value.investigation_id.toLowerCase() !== id.toLowerCase())
      throw new ApiError('invalid_response', 'The API returned a different investigation.');
    return value;
  },
};

export function errorText(error: unknown) {
  return error instanceof ApiError ? error.message : 'The request could not be completed.';
}

export function route(hash: string): { id: string | null; error: string | null } {
  if (!hash || hash === '#' || hash === '#/') return { id: null, error: null };
  const match = /^#\/investigations\/([^/]+)$/.exec(hash);
  return match && isUuid(match[1]) ? { id: match[1].toLowerCase(), error: null }
    : { id: null, error: 'Invalid investigation URL. Open a valid UUID.' };
}
export function openInvestigation(id: string) { window.location.hash = '/investigations/' + id.toLowerCase(); }
