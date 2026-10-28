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
  if (Array.isArray(d)) return d.map((x) => x.msg || String(x)).join('; ');
  return fallback;
}

async function failure(response) {
  let body = null;
  try { body = await response.json(); } catch { /* not JSON */ }
  return new ApiError(response.status, detailOf(body, `${response.status} ${response.statusText}`.trim()));
}

const OFFLINE = 'Could not reach Loreg. Is the server running?';

export async function api(path, { method = 'GET', json, form, signal } = {}) {
  const init = { method, signal, headers: {} };
  if (json !== undefined) { init.body = JSON.stringify(json); init.headers['Content-Type'] = 'application/json'; }
  if (form) init.body = form;
  let response;
  try { response = await fetch(`/api${path}`, init); } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError(0, OFFLINE);
  }
  if (!response.ok) throw await failure(response);
  return response.status === 204 ? null : response.json();
}

// Server-sent events over fetch (EventSource cannot POST). Yields {event, data} until the stream ends.
export async function* askStream(question, { sku, signal } = {}) {
  let response;
  try {
    response = await fetch('/api/ask', {
      method: 'POST', signal,
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(sku ? { question, sku } : { question }),
    });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError(0, OFFLINE);
  }
  if (!response.ok) throw await failure(response);
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
      if (frame) yield frame;
    }
    if (done) break;
  }
  const rest = parseFrame(buffer);
  if (rest) yield rest;
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
