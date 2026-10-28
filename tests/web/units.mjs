// Runs under plain Node (no DOM). Checks formatting and the SSE reader, with a fake fetch that splits frames at awkward places.
import assert from 'node:assert/strict';
import { money, money2, count, date } from '../../web/js/format.js';
import { askStream, parseFrame, api, ApiError } from '../../web/js/api.js';

assert.equal(money(123456789), '$1,234,568');
assert.equal(money(0), '$0');
assert.equal(money(-5000), '-$50');
assert.equal(money2(1999), '$19.99');
assert.equal(money2(5), '$0.05');
assert.equal(money(undefined), '—');
assert.equal(count(3000), '3,000');
assert.equal(date('2026-10-26'), 'Oct 26, 2026');
assert.equal(date('2026-10-26T08:00:00Z'), 'Oct 26, 2026');
assert.equal(date('March 12'), 'March 12');   // never guessed

assert.deepEqual(parseFrame('event: status\ndata: {"text":"hi"}'), { event: 'status', data: { text: 'hi' } });
assert.equal(parseFrame(': comment only'), null);
assert.equal(parseFrame('event: x\ndata: {broken').event, 'error');

function streamOf(chunks) {
  const enc = new TextEncoder();
  return new ReadableStream({ start(c) { for (const ch of chunks) c.enqueue(enc.encode(ch)); c.close(); } });
}
const frames = 'event: status\ndata: {"text":"Reading"}\n\nevent: token\ndata: {"text":"héllo "}\n\nevent: done\ndata: {"ok":true}\n\n';
for (const size of [1, 3, 7, 1000]) {
  const chunks = []; for (let i = 0; i < frames.length; i += size) chunks.push(frames.slice(i, i + size));
  globalThis.fetch = async () => new Response(streamOf(chunks), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
  const got = []; for await (const e of askStream('q')) got.push(e.event + ':' + JSON.stringify(e.data));
  assert.deepEqual(got, ['status:{"text":"Reading"}', 'token:{"text":"héllo "}', 'done:{"ok":true}'], `chunk size ${size}`);
}

// multi-byte characters split across chunks
const bytes = new TextEncoder().encode('event: token\ndata: {"text":"€"}\n\n');
globalThis.fetch = async () => new Response(new ReadableStream({ start(c) { c.enqueue(bytes.slice(0, 27)); c.enqueue(bytes.slice(27)); c.close(); } }));
const euro = []; for await (const e of askStream('q')) euro.push(e.data.text);
assert.deepEqual(euro, ['€']);

// errors: the server's own detail is shown; an unreachable server is status 0
globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'no item with SKU X in your stock' }), { status: 404, headers: { 'Content-Type': 'application/json' } });
await assert.rejects(() => api('/x'), (e) => e instanceof ApiError && e.status === 404 && e.detail === 'no item with SKU X in your stock');
globalThis.fetch = async () => new Response(JSON.stringify({ detail: [{ msg: 'field required' }] }), { status: 422 });
await assert.rejects(() => api('/x'), (e) => e.detail === 'field required');
globalThis.fetch = async () => { throw new TypeError('fetch failed'); };
await assert.rejects(() => api('/x'), (e) => e.status === 0 && /Could not reach Loreg/.test(e.detail));
await assert.rejects(async () => { for await (const _ of askStream('q')) { /* nothing */ } }, (e) => e.status === 0);
console.log('web units ok');
