// The only module that talks to the server. Everything is same-origin under /api.

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;       // 0 means the server could not be reached
    this.detail = detail;
  }
}

function detailOf(body, fallback) {
  const d = body && body.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((x) => String(x.msg || x).replace(/^Value error, /, '')).join('; ');
  return fallback;
}

// The session's CSRF token, from GET /api/session or a sign-in. Sent with every request that is not a GET, the chat stream included.
let csrfToken = '';
export function setCsrf(token) { csrfToken = token || ''; }

// Called when the server answers 401 (no session, or it expired), so the page can show the sign-in screen.
let signedOut = null;
export function whenSignedOut(fn) { signedOut = fn; }
function check401(response, path) {
  if (response.status === 401 && signedOut && !['/session', '/login'].includes(path)) signedOut();
}

async function failure(response) {
  let body = null;
  try { body = await response.json(); } catch { /* not JSON */ }
  return new ApiError(response.status, detailOf(body, `${response.status} ${response.statusText}`.trim()));
}

const OFFLINE = 'Could not reach Loreg. Is the server running?';

export async function api(path, { method = 'GET', json, form, signal } = {}) {
  const init = { method, signal, headers: {} };
  if (method !== 'GET') init.headers['X-CSRF-Token'] = csrfToken;
  if (json !== undefined) { init.body = JSON.stringify(json); init.headers['Content-Type'] = 'application/json'; }
  if (form) init.body = form;
  let response;
  try { response = await fetch(`/api${path}`, init); } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError(0, OFFLINE);
  }
  check401(response, path);
  if (!response.ok) throw await failure(response);
  return response.status === 204 ? null : response.json();
}

// Server-sent events over fetch (EventSource cannot POST). Yields {event, data} until the stream ends.
// If the stream breaks before its first event (a proxy that buffers or cuts chunked replies), the same question is asked once more
// without streaming (?stream=false) and its events are yielded from one JSON reply. Once an event has been shown it never repeats.
export async function* askStream(question, { sku, signal } = {}) {
  const body = JSON.stringify(sku ? { question, sku } : { question });
  let response;
  try {
    response = await fetch('/api/ask', {
      method: 'POST', signal, body,
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', 'X-CSRF-Token': csrfToken },
    });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError(0, OFFLINE);
  }
  check401(response, '/ask');
  if (!response.ok) throw await failure(response);
  let shown = 0;
  try {
    if (!response.body || !response.body.getReader) throw new TypeError('no stream reader');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done }).replace(/\r\n/g, '\n');
      let cut;
      while ((cut = buffer.indexOf('\n\n')) !== -1) {
        const frame = parseFrame(buffer.slice(0, cut));
        buffer = buffer.slice(cut + 2);
        if (frame) { shown += 1; yield frame; }
      }
      if (done) break;
    }
    const rest = parseFrame(buffer);
    if (rest) { shown += 1; yield rest; }
    if (shown === 0) throw new TypeError('the stream ended with no event');
  } catch (e) {
    if (e.name === 'AbortError' || shown > 0) throw e;
    const whole = await api('/ask?stream=false', { method: 'POST', json: JSON.parse(body), signal });
    for (const frame of (whole && whole.events) || []) yield frame;
  }
}

export function parseFrame(text) {
  let event = 'message';
  const data = [];
  for (const line of text.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
  }
  if (!data.length) return null;
  try { return { event, data: JSON.parse(data.join('\n')) }; } catch { return { event: 'error', data: { message: 'The reply could not be read.' } }; }
}
