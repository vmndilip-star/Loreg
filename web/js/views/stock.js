// Stock: where the money is tied up. Every figure here is read from the API; this file only lays it out.
import { api, ApiError } from '../api.js';
import { append, el } from '../dom.js';
import { count, money } from '../format.js';
import { humanCode, moneyTile, statusChip } from '../parts.js';

const TOP = 10;

export async function mount(root, ctx) {
  let summary;
  try {
    summary = await api('/summary', { signal: ctx.signal });
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      append(root, [el('h1', { class: 'h3 mb-3' }, 'Stock'), el('section', { class: 'tile glass empty' },
        el('div', { class: 'label mb-2' }, 'No stock list yet'),
        el('p', { class: 'soft' }, 'Upload your stock list to see what is dead or slow.'),
        el('a', { class: 'btn-pill btn-go d-inline-flex align-items-center text-decoration-none', href: '#upload' }, 'Upload a stock list'))]);
      return;
    }
    throw e;
  }
  ctx.setAsOf(summary.as_of);
  const [top, look] = await Promise.all([
    api(`/inventory/top?limit=${TOP}`, { signal: ctx.signal }),
    api('/needs-look?limit=20', { signal: ctx.signal }),
  ]);
  const t = summary.totals;

  append(root, [el('h1', { class: 'h3 mb-3' }, 'Stock'), el('div', { class: 'row g-3' },
    el('div', { class: 'col-12 col-lg-5' }, moneyTile('Money at risk', summary.money_at_risk_cents,
      el('div', { class: 'fw-bold' }, `${count(t.dead.n)} dead (${money(t.dead.cap_cents)}) and ${count(t.slow.n)} slow (${money(t.slow.cap_cents)}) of ${count(t.skus)} items.`))),
    el('div', { class: 'col-12 col-lg-7' }, byCategory(summary.by_category)),
    el('div', { class: 'col-12' }, topTable(top.items)),
    look.count ? el('div', { class: 'col-12' }, needsLook(look)) : null)]);
}

function byCategory(rows) {
  return el('section', { class: 'tile glass h-100', 'aria-label': 'By category' },
    el('div', { class: 'label mb-2' }, 'Where it is tied up'),
    el('div', { class: 'd-flex flex-column gap-2' }, rows.map((r) => el('div', {},
      el('div', { class: 'd-flex justify-content-between fw-semibold' }, el('span', {}, `${r.label} (${count(r.n)})`), el('span', {}, money(r.cap_cents))),
      el('div', { class: 'bar', role: 'img', 'aria-label': `${r.label}: ${money(r.cap_cents)}` }, barFill(r.pct))))));
}

function barFill(pct) {
  const fill = el('div', { class: 'bar-fill' });
  fill.style.setProperty('--w', `${Math.max(0, Math.min(100, Number(pct) || 0))}%`);   // the server computed pct
  return fill;
}

function topTable(items) {
  return el('section', { class: 'tile glass', 'aria-label': 'Biggest amounts tied up' },
    el('div', { class: 'label mb-2' }, 'Biggest amounts tied up'),
    el('div', { class: 'table-responsive' }, el('table', { class: 'table table-glass', 'data-testid': 'top-items' },
      el('thead', {}, el('tr', {}, ['Item', 'Category', 'On shelf', 'Status', 'Why', 'Tied up'].map((h) => el('th', { scope: 'col' }, h)))),
      el('tbody', {}, items.map((i) => el('tr', { 'data-sku': i.sku },
        el('td', {}, el('div', { class: 'fw-semibold' }, i.name), el('div', { class: 'soft small' }, i.sku)),
        el('td', {}, i.category_label), el('td', {}, count(i.qty)), el('td', {}, statusChip(i.status)),
        el('td', {}, i.why), el('td', { class: 'fw-bold text-nowrap' }, money(i.cap_cents))))))));
}

function needsLook(look) {
  return el('section', { class: 'tile glass', 'aria-label': 'Needs a look' },
    el('div', { class: 'label mb-1' }, `${count(look.count)} items need a look`),
    el('div', { class: 'soft mb-2' }, 'Loreg could not place these with confidence, so they are left out of the totals until you check them.'),
    el('ul', { class: 'mb-0' }, look.items.map((i) => el('li', {}, [i.sku, i.name, (i.reasons || []).map(humanCode).join(', ')].filter(Boolean).join(' · ')))));
}
