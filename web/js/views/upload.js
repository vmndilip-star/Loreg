// Upload: send a stock list, then show what the data checks found. Real upload is the main path; the demo button only supplies a file.
// Below it, an optional sales history (dated sales) for the Analyze page.
import { api, ApiError } from '../api.js';
import { append, clear, el, icon } from '../dom.js';
import { count, date, plural } from '../format.js';
import { humanCode } from '../parts.js';

const LEVEL_CHIP = { error: 'chip-red', warning: 'chip-amber', info: '' };

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
    el('div', { class: 'row g-3' }, el('div', { class: 'col-12' }, zone), result, salesSection(ctx)),
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

  const formFor = (file, mapping) => {
    const form = new FormData();
    form.append('file', file, file.name);
    if (mapping) form.append('mapping', JSON.stringify(mapping));
    return form;
  };
  // A file with Loreg's own column names goes straight in. Any other file gets a proposed mapping (code's matches first, the model
  // only for what is left), which the owner checks and confirms before a single row is stored; the server checks it again.
  const upload = (file) => send(async () => {
    try {
      return await api('/upload', { method: 'POST', form: formFor(file), signal: ctx.signal });
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 422 && /missing columns/.test(e.detail))) throw e;
      clear(status); append(status, [el('span', { class: 'spinner', 'aria-hidden': 'true' }), ' Matching your columns…']);
      const proposal = await api('/upload/mapping', { method: 'POST', form: formFor(file), signal: ctx.signal });
      clear(status); append(status, ['Your columns have different names. Check how Loreg matched them, then confirm.']);
      const mapping = await confirmMapping(result, proposal, ctx);
      clear(status); append(status, [el('span', { class: 'spinner', 'aria-hidden': 'true' }), ` Checking ${file.name}…`]);
      return api('/upload', { method: 'POST', form: formFor(file, mapping), signal: ctx.signal });
    }
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

// The sales history: its own file, its own checks. It replaces the stored history and is read against the stock list's date.
function salesSection(ctx) {
  const status = el('div', { class: 'soft', role: 'status' });
  const picker = el('input', { type: 'file', accept: '.csv,text/csv', hidden: true, 'aria-label': 'Sales history CSV file', 'data-testid': 'sales-file' });
  const choose = el('button', { type: 'button', class: 'btn-pill btn-soft' }, 'Choose a sales history CSV');
  const out = el('div', { class: 'w-100', 'data-testid': 'sales-result' });
  choose.addEventListener('click', () => picker.click());
  picker.addEventListener('change', async () => {
    const file = picker.files[0];
    if (!file) return;
    choose.disabled = true;
    clear(out); clear(status); append(status, [el('span', { class: 'spinner', 'aria-hidden': 'true' }), ` Checking ${file.name}…`]);
    ctx.showBanner(null, '');
    try {
      const form = new FormData();
      form.append('file', file, file.name);
      const r = await api('/sales/upload', { method: 'POST', form, signal: ctx.signal });
      clear(status);
      append(out, [salesResult(r)]);
      ctx.announce(`Sales history: ${count(r.kept)} rows kept, ${count(r.blocked)} set aside.`);
    } catch (e) {
      clear(status);
      ctx.fail(e);
    } finally {
      choose.disabled = false;
      picker.value = '';
    }
  });
  return el('div', { class: 'col-12' }, el('section', { class: 'tile glass d-flex flex-column gap-3 align-items-start', 'aria-label': 'Sales history file' },
    el('div', { class: 'label' }, 'Sales history (optional, CSV)'),
    el('div', { class: 'soft' }, 'One row per sale: sku, date, qty, unit_price, and type (sale or return; blank means sale). It replaces the sales history '
      + 'Loreg has, and Analyze shows it by day, week, month and year. Upload the stock list first: a sale dated after its date is set aside.'),
    choose, picker, status, out));
}

function salesResult(r) {
  return el('div', { class: 'd-flex flex-column gap-2' },
    el('div', { class: 'fw-bold' }, `${r.file}: ${count(r.kept)} of ${count(r.rows)} rows kept, ${count(r.blocked)} set aside. Sales from ${date(r.first_date)} to ${date(r.last_date)}.`),
    r.not_in_stock_list ? el('div', { class: 'soft small' }, `${plural(r.not_in_stock_list, 'SKU')} ${r.not_in_stock_list === 1 ? 'is' : 'are'} not in your current stock list: kept, and shown once the item is in a stock list.`) : null,
    r.issues.length ? el('div', { class: 'table-responsive' }, el('table', { class: 'table table-glass' },
      el('thead', {}, el('tr', {}, el('th', { scope: 'col' }, 'Row'), el('th', { scope: 'col' }, 'SKU'), el('th', { scope: 'col' }, 'Problem'))),
      el('tbody', {}, r.issues.map((i) => el('tr', {}, el('td', {}, count(i.row)), el('td', {}, i.sku || '—'), el('td', {}, i.problems.join(' '))))))) : null,
    r.blocked > r.issues.length ? el('div', { class: 'soft small' }, `The first ${count(r.issues.length)} rows set aside are listed.`) : null,
    el('a', { class: 'btn-pill btn-go d-inline-flex align-items-center text-decoration-none align-self-start', href: '#analyze' }, 'See it on Analyze'));
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

const HOW = { exact: ['same name', 'chip-green'], alias: ['matched by name', 'chip-green'], model: ['suggested by the model: please check', 'chip-amber'],
  none: ['not matched', ''] };

// Shows each column with its proposed field and lets the owner change any of them. Resolves with {header: field} on Confirm;
// rejects (quietly, as a cancelled request) on Cancel. Required fields are marked; the server refuses a mapping that leaves one out.
function confirmMapping(slot, p, ctx) {
  return new Promise((resolve, reject) => {
    clear(slot);
    const selects = p.headers.map((h) => {
      const select = el('select', { class: 'form-select form-select-sm', 'aria-label': `Loreg field for column ${h}`, 'data-header': h },
        el('option', { value: '' }, '— not used —'),
        p.fields.map((f) => el('option', { value: f.name, selected: p.mapping[h] === f.name }, `${f.label}${f.required ? ' (required)' : ''}`)));
      return [h, select];
    });
    const confirm = el('button', { type: 'button', class: 'btn-pill btn-go', 'data-testid': 'mapping-confirm' }, 'Confirm and check the file');
    const cancel = el('button', { type: 'button', class: 'btn-pill btn-soft' }, 'Cancel');
    confirm.addEventListener('click', () => {
      const mapping = {};
      for (const [h, s] of selects) if (s.value) mapping[h] = s.value;
      clear(slot);
      resolve(mapping);
    });
    cancel.addEventListener('click', () => { clear(slot); const e = new Error('cancelled'); e.name = 'AbortError'; reject(e); });
    append(slot, [el('section', { class: 'tile glass d-flex flex-column gap-2', 'aria-label': 'Match your columns', 'data-testid': 'mapping' },
      el('div', { class: 'label' }, 'Match your columns'),
      el('div', { class: 'soft' }, 'Nothing is stored until you confirm. The data checks still run on every row afterwards.'),
      p.model && p.model.source === 'fallback' ? el('div', { class: 'banner banner-info' }, 'The model could not suggest a usable match, so only the columns Loreg recognised are filled in.') : null,
      p.problems.length ? el('ul', { class: 'small mb-0' }, p.problems.map((x) => el('li', {}, x))) : null,
      el('div', { class: 'table-responsive' }, el('table', { class: 'table table-glass' },
        el('thead', {}, el('tr', {}, el('th', { scope: 'col' }, 'Your column'), el('th', { scope: 'col' }, 'Example'), el('th', { scope: 'col' }, 'Loreg field'),
          el('th', { scope: 'col' }, 'How'))),
        el('tbody', {}, selects.map(([h, s]) => el('tr', {}, el('td', {}, h), el('td', { class: 'soft small' }, (p.sample[0] || {})[h] || ''), el('td', {}, s),
          el('td', {}, el('span', { class: `chip ${HOW[p.how[h]][1]}` }, HOW[p.how[h]][0]))))))),
      el('div', { class: 'd-flex gap-2 flex-wrap' }, confirm, cancel))]);
  });
}

function quarantineTable(rows) {
  const set = rows.filter((r) => r.quarantined);
  return el('div', { class: 'mt-3' },
    el('div', { class: 'soft fw-semibold mb-1' }, 'Rows set aside (not counted until fixed)'),
    el('div', { class: 'table-responsive' }, el('table', { class: 'table table-glass' },
      el('thead', {}, el('tr', {}, el('th', { scope: 'col' }, 'Row'), el('th', { scope: 'col' }, 'SKU'), el('th', { scope: 'col' }, 'Problem'))),
      el('tbody', {}, set.map((r) => el('tr', {}, el('td', {}, r.row), el('td', {}, r.sku || '—'), el('td', {}, humanCode(r.code))))))));
}
