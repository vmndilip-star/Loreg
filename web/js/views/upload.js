// Upload: send a stock list, then show what the data checks found. Real upload is the main path; the demo button only supplies a file.
import { api, ApiError } from '../api.js';
import { append, clear, el, icon } from '../dom.js';
import { count, date } from '../format.js';

const LEVEL_CHIP = { error: 'chip-red', warning: 'chip-amber', info: '' };
const humanCode = (code) => { const t = String(code).toLowerCase().replace(/_/g, ' '); return t.charAt(0).toUpperCase() + t.slice(1); };

export async function mount(root, ctx) {
  const result = el('div', { class: 'col-12' });
  const status = el('div', { class: 'soft', role: 'status' });
  const picker = el('input', { type: 'file', accept: '.csv,text/csv', hidden: true, 'aria-label': 'Stock list CSV file' });
  const choose = el('button', { type: 'button', class: 'btn-pill btn-blue' }, 'Choose a CSV file');
  const demoSlot = el('span');
  const zone = el('section', { class: 'tile glass d-flex flex-column gap-3 align-items-start', 'aria-label': 'Stock list file' },
    el('div', { class: 'd-flex align-items-center gap-3' }, icon('i-upload'), el('div', { class: 'label' }, 'Your stock list (CSV)')),
    el('div', { class: 'soft' }, 'Drop your file here, or choose it. Loreg checks every row before it counts anything, and sets aside rows it cannot trust.'),
    el('div', { class: 'd-flex flex-wrap gap-2 align-items-center' }, choose, demoSlot, picker),
    status);

  append(root, [
    el('h1', { class: 'h3 mb-3' }, 'Upload'),
    el('div', { class: 'row g-3' }, el('div', { class: 'col-12' }, zone), result),
  ]);

  let busy = false;
  async function send(makeRequest, label) {
    if (busy) return;
    busy = true;
    for (const b of [choose, ...demoSlot.querySelectorAll('button')]) b.disabled = true;
    clear(status); append(status, [el('span', { class: 'spinner', 'aria-hidden': 'true' }), ` Checking ${label}…`]);
    ctx.showBanner(null, '');
    try {
      const done = await makeRequest();
      ctx.state.uploadId = done.upload_id;
      clear(status);
      await show(done.upload_id);
      ctx.announce(`Checked ${count(done.rows)} rows. ${count(done.blocked)} blocked.`);
    } catch (e) {
      clear(status);
      ctx.fail(e);
    } finally {
      busy = false;
      for (const b of [choose, ...demoSlot.querySelectorAll('button')]) b.disabled = false;
      picker.value = '';
    }
  }

  const upload = (file) => send(() => {
    const form = new FormData();
    form.append('file', file, file.name);
    return api('/upload', { method: 'POST', form, signal: ctx.signal });
  }, file.name);

  choose.addEventListener('click', () => picker.click());
  picker.addEventListener('change', () => { if (picker.files[0]) upload(picker.files[0]); });
  for (const type of ['dragenter', 'dragover']) zone.addEventListener(type, (e) => { e.preventDefault(); zone.classList.add('tile-blue'); });
  for (const type of ['dragleave', 'drop']) zone.addEventListener(type, (e) => { e.preventDefault(); zone.classList.remove('tile-blue'); });
  zone.addEventListener('drop', (e) => { const f = e.dataTransfer && e.dataTransfer.files[0]; if (f) upload(f); });

  // The demo button exists only when the server says the demo is on (the route answers 404 otherwise).
  api('/demo', { signal: ctx.signal }).then(() => {
    append(demoSlot, [el('button', { type: 'button', class: 'btn-pill btn-soft', onclick: () => send(() => api('/demo/load', { method: 'POST', signal: ctx.signal }), 'the demo stock list') }, 'Use the demo stock list')]);
  }).catch(() => { /* 404: demo off, or no server; nothing to offer */ });

  async function show(uploadId) {
    const data = await api(`/uploads/${uploadId}/issues`, { signal: ctx.signal });
    renderResult(result, data, ctx);
    ctx.setAsOf(data.upload.as_of);
  }

  // Show the latest upload if there is one; "no stock list yet" is the normal first-run state, not an error.
  try {
    const summary = await api('/summary', { signal: ctx.signal });
    if (summary.upload_id) await show(summary.upload_id);
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 404)) throw e;
  }
}

function renderResult(slot, data, ctx) {
  const u = data.upload;
  clear(slot);
  append(slot, [
    el('div', { class: 'row g-3' },
      el('div', { class: 'col-12 col-sm-6' }, el('section', { class: 'tile tile-green h-100 d-flex align-items-center gap-3', 'aria-label': 'Rows OK' },
        el('span', { class: 'num num-lg' }, count(u.ok)), el('span', { class: 'fw-bold' }, 'rows OK'))),
      el('div', { class: 'col-6 col-sm-3' }, el('section', { class: 'tile glass h-100 d-flex flex-column align-items-center justify-content-center text-center px-2', 'aria-label': 'To look at' },
        el('span', { class: 'num num-lg' }, count(u.look)), el('span', { class: 'small fw-bold' }, 'to look at'))),
      el('div', { class: 'col-6 col-sm-3' }, el('section', { class: `tile h-100 d-flex flex-column align-items-center justify-content-center text-center px-2 ${u.blocked ? 'tile-red' : 'glass'}`, 'aria-label': 'Blocked' },
        el('span', { class: 'num num-lg' }, count(u.blocked)), el('span', { class: 'small fw-bold' }, 'blocked'))),
      el('div', { class: 'col-12' }, el('section', { class: 'tile glass', 'aria-label': 'Problems found' },
        el('div', { class: 'label mb-1' }, `${u.file}: ${count(u.rows)} rows, stock as of ${date(u.as_of)}`),
        data.issues.length
          ? el('ul', { class: 'list-unstyled m-0' }, data.issues.map((i) => el('li', { class: 'd-flex gap-2 align-items-start py-1' },
            el('span', { class: `chip ${LEVEL_CHIP[i.level] || ''}` }, i.level), el('span', {}, i.text))))
          : el('div', { class: 'soft' }, 'No problems found.'),
        data.rows.some((r) => r.quarantined) ? quarantineTable(data.rows) : null)),
      el('div', { class: 'col-12' }, el('section', { class: 'tile glass d-flex gap-2 flex-wrap', 'aria-label': 'Next' },
        el('a', { class: 'btn-pill btn-go d-inline-flex align-items-center text-decoration-none', href: '#home' }, 'See what is at risk'),
        el('a', { class: 'btn-pill btn-soft d-inline-flex align-items-center text-decoration-none', href: '#stock' }, 'Open Stock')))),
  ]);
}

function quarantineTable(rows) {
  const set = rows.filter((r) => r.quarantined);
  return el('div', { class: 'mt-3' },
    el('div', { class: 'soft fw-semibold mb-1' }, 'Rows set aside (not counted until fixed)'),
    el('div', { class: 'table-responsive' }, el('table', { class: 'table table-glass' },
      el('thead', {}, el('tr', {}, el('th', { scope: 'col' }, 'Row'), el('th', { scope: 'col' }, 'SKU'), el('th', { scope: 'col' }, 'Problem'))),
      el('tbody', {}, set.map((r) => el('tr', {}, el('td', {}, r.row), el('td', {}, r.sku || '—'), el('td', {}, humanCode(r.code))))))));
}
