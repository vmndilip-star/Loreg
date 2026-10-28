// Stock: where the money is tied up, and a way to browse it. Every figure here is read from the API; this file only lays it out.
// The address holds the filter, so a view can be shared and the Back button works: #stock?category=tv&status=dead&q=oled&sort=name&page=2
import { api, ApiError } from '../api.js';
import { append, clear, el } from '../dom.js';
import { count, money, plural } from '../format.js';
import { humanCode, moneyTile, statusChip } from '../parts.js';
import { stockLink } from '../search.js';

const STATUSES = ['all', 'dead', 'slow'];
const SORTS = { tied_up: 'Money tied up', name: 'Name', qty: 'On the shelf', days_since_sold: 'Days since last sale' };
const CHIP_LABEL = { all: 'All', dead: 'Dead', slow: 'Slow' };

function readFilter(params) {
  const status = params.get('status');
  const sort = params.get('sort');
  const page = Number.parseInt(params.get('page') || '1', 10);
  return {
    category: params.get('category') || '', status: STATUSES.includes(status) ? status : 'all', q: (params.get('q') || '').slice(0, 100),
    sort: Object.hasOwn(SORTS, sort) ? sort : 'tied_up', page: Number.isFinite(page) && page >= 1 ? page : 1,
  };
}

const href = (f) => stockLink({ category: f.category, status: f.status === 'all' ? '' : f.status, q: f.q, sort: f.sort === 'tied_up' ? '' : f.sort, page: f.page === 1 ? '' : f.page });

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
  const [{ categories }, look] = await Promise.all([api('/categories', { signal: ctx.signal }), api('/needs-look?limit=20', { signal: ctx.signal })]);
  let filter = readFilter(ctx.params);

  const barsSlot = el('div', { class: 'col-12 col-lg-7' });
  const filterSlot = el('div', { class: 'col-12' });
  const tableSlot = el('div', { class: 'col-12', 'data-testid': 'stock-table' });
  append(root, [el('h1', { class: 'h3 mb-3' }, 'Stock'), el('div', { class: 'row g-3' },
    el('div', { class: 'col-12 col-lg-5' }, moneyTile('Money at risk', summary.money_at_risk_cents,
      el('div', { class: 'fw-bold' }, `${count(summary.totals.dead.n)} dead (${money(summary.totals.dead.cap_cents)}) and ${count(summary.totals.slow.n)} slow (${money(summary.totals.slow.cap_cents)}) of ${count(summary.totals.skus)} items.`))),
    barsSlot, filterSlot, tableSlot, look.count ? el('div', { class: 'col-12' }, needsLook(look)) : null)]);

  function change(patch) {                  // update the filter, the address and the page; no reload, and Back still works
    filter = { ...filter, page: 1, ...patch };
    history.pushState(null, '', href(filter));
    drawBars();
    load();
  }

  function drawBars() {
    clear(barsSlot);
    append(barsSlot, [el('section', { class: 'tile glass h-100', 'aria-label': 'By category' },
      el('div', { class: 'd-flex justify-content-between align-items-center mb-2' }, el('div', { class: 'label' }, 'Where it is tied up'),
        filter.category ? el('a', { class: 'linkbtn small', href: href({ ...filter, category: '', page: 1 }), 'data-testid': 'all-categories',
          onclick: (e) => { e.preventDefault(); change({ category: '' }); } }, 'All categories') : null),
      el('div', { class: 'd-flex flex-column gap-1' }, categories.map((c) => {
        const link = el('a', { class: 'bar-link', href: href({ ...filter, category: c.key, page: 1 }), 'data-testid': 'category-link', 'data-category': c.key,
          'aria-current': filter.category === c.key ? 'true' : false, 'aria-label': `${c.label}: ${money(c.at_risk_cap_cents)} at risk in ${count(c.at_risk_n)} items. Show these items.` },
        el('div', { class: 'd-flex justify-content-between fw-semibold' }, el('span', {}, `${c.label} (${count(c.at_risk_n)} at risk)`), el('span', {}, money(c.at_risk_cap_cents))),
        barFill(c.bar_pct));
        link.addEventListener('click', (e) => { e.preventDefault(); change({ category: c.key }); });
        return link;
      })))]);
  }

  async function load() {
    clear(filterSlot); clear(tableSlot);
    append(tableSlot, [skeleton()]);
    const query = new URLSearchParams({ status: filter.status, sort: filter.sort, page: String(filter.page), page_size: '25' });
    if (filter.category) query.set('category', filter.category);
    if (filter.q) query.set('q', filter.q);
    try {
      const data = await api(`/products?${query}`, { signal: ctx.signal });
      clear(tableSlot);
      drawFilter(data);
      drawTable(data);
      ctx.announce(plural(data.totals.items, 'item'));
    } catch (e) {
      if (e.name === 'AbortError') return;
      clear(tableSlot);
      append(tableSlot, [e instanceof ApiError && e.status === 404 ? notFound() : failure(e)]);
    }
  }

  function drawFilter(data) {
    const t = data.totals;
    const label = filter.category ? (categories.find((c) => c.key === filter.category) || {}).label || filter.category : 'All stock';
    clear(filterSlot);
    append(filterSlot, [el('section', { class: 'tile glass d-flex flex-column gap-3', 'aria-label': 'Filter' },
      el('div', { class: 'd-flex flex-wrap gap-2 align-items-center', role: 'group', 'aria-label': 'Status' },
        STATUSES.map((s) => el('button', { type: 'button', class: 'chipbtn', 'aria-pressed': String(filter.status === s), 'data-testid': `chip-${s}`,
          onclick: () => change({ status: s }) }, `${CHIP_LABEL[s]} (${count(t.status_counts[s])})`)),
        el('span', { class: 'ms-auto d-flex gap-2 align-items-center' }, el('label', { for: 'sortBy', class: 'small fw-bold' }, 'Sort by'),
          sortBox())),
      el('div', { class: 'd-flex flex-wrap gap-3 align-items-center', 'data-testid': 'filter-summary' },
        el('span', { class: 'fw-bold' }, `${label}: ${plural(t.items, 'item')}`),
        el('span', { class: 'soft' }, `${count(t.at_risk_n)} dead or slow, ${money(t.at_risk_cap_cents)} at risk here`),
        filter.q ? el('button', { type: 'button', class: 'chipbtn', 'data-testid': 'clear-search', onclick: () => change({ q: '' }) }, `Search: “${filter.q}” ✕`) : null))]);
  }

  function sortBox() {
    const box = el('select', { id: 'sortBy', class: 'sortbox', 'data-testid': 'sort' }, Object.entries(SORTS).map(([k, v]) => el('option', { value: k, selected: filter.sort === k }, v)));
    box.addEventListener('change', () => change({ sort: box.value }));
    return box;
  }

  function drawTable(data) {
    if (!data.rows.length) {
      append(tableSlot, [el('section', { class: 'tile glass empty', 'data-testid': 'empty' }, el('div', { class: 'label' }, 'Nothing matches. Try fewer words.'),
        filter.status !== 'all' || filter.q || filter.category ? el('button', { type: 'button', class: 'btn-pill btn-soft mt-3', onclick: () => change({ status: 'all', q: '', category: '' }) }, 'Clear the filter') : null)]);
      return;
    }
    const showCategory = !filter.category;
    append(tableSlot, [el('section', { class: 'tile glass', 'aria-label': 'Stock' },
      el('div', { class: 'table-responsive' }, el('table', { class: 'table table-glass', 'data-testid': 'products' },
        el('thead', {}, el('tr', {}, [['Item'], showCategory ? ['Category'] : null, ['On shelf'], ['Days since sold'], ['Status'], ['Why'], ['Tied up'], ['Price']].filter(Boolean).map(([h]) => el('th', { scope: 'col' }, h)))),
        el('tbody', {}, data.rows.map((r) => el('tr', { 'data-sku': r.sku },
          el('td', {}, el('div', { class: 'fw-semibold' }, r.name), el('div', { class: 'soft small' }, `${r.sku} · ${r.variant}`)),
          showCategory ? el('td', {}, r.category_label) : null,
          el('td', {}, count(r.qty)), el('td', {}, r.days_since_sold === null ? 'never' : count(r.days_since_sold)), el('td', {}, statusChip(r.status)),
          el('td', {}, r.why), el('td', { class: 'fw-bold text-nowrap' }, money(r.tied_up_cents)), el('td', { class: 'text-nowrap' }, money(r.sell_price_cents))))))),
      pagination(data.pagination))]);
  }

  function pagination({ page, page_size: size, total }) {
    const last = Math.max(1, Math.ceil(total / size));
    if (last === 1) return el('div', { class: 'soft small mt-2', 'aria-live': 'off' }, plural(total, 'item'));
    const shown = [...new Set([1, page - 1, page, page + 1, last])].filter((n) => n >= 1 && n <= last).sort((a, b) => a - b);
    const item = (n, text, { disabled = false, current = false, label } = {}) => {
      const a = el('a', { class: 'page-link', href: href({ ...filter, page: n }), 'aria-label': label || `Page ${n}`, 'aria-current': current ? 'page' : false,
        'aria-disabled': disabled ? 'true' : false, tabindex: disabled ? '-1' : false }, text);
      a.addEventListener('click', (e) => { e.preventDefault(); if (!disabled) { filter = { ...filter, page: n }; history.pushState(null, '', href(filter)); load(); } });
      return el('li', { class: `page-item${current ? ' active' : ''}${disabled ? ' disabled' : ''}` }, a);
    };
    const parts = [item(page - 1, '‹', { disabled: page <= 1, label: 'Previous page' })];
    shown.forEach((n, i) => { if (i && n - shown[i - 1] > 1) parts.push(el('li', { class: 'page-item disabled' }, el('span', { class: 'page-link' }, '…'))); parts.push(item(n, String(n), { current: n === page })); });
    parts.push(item(page + 1, '›', { disabled: page >= last, label: 'Next page' }));
    return el('nav', { 'aria-label': 'Pages', class: 'mt-3 d-flex flex-wrap gap-3 align-items-center' }, el('ul', { class: 'pagination mb-0 flex-wrap' }, parts),
      el('span', { class: 'soft small' }, `Page ${count(page)} of ${count(last)}, ${plural(total, 'item')}`));
  }

  drawBars();
  await load();
}

function barFill(pct) {
  const fill = el('div', { class: 'bar-fill' });
  fill.style.setProperty('--w', `${Math.max(0, Math.min(100, Number(pct) || 0))}%`);   // the server worked the length out
  return el('div', { class: 'bar', role: 'presentation' }, fill);
}

function skeleton() {
  return el('section', { class: 'tile glass', 'aria-label': 'Loading', 'aria-busy': 'true', 'data-testid': 'skeleton' },
    Array.from({ length: 6 }, () => el('div', { class: 'skeleton-row' })));
}

function failure(e) {
  return el('section', { class: 'tile glass', role: 'alert' }, el('div', { class: 'label mb-1' }, 'The list could not be loaded'),
    el('div', { class: 'soft mb-2' }, e instanceof ApiError ? e.detail : 'Something went wrong. Nothing was changed.'),
    el('button', { type: 'button', class: 'btn-pill btn-soft', onclick: () => location.reload() }, 'Try again'));
}

function notFound() {
  return el('section', { class: 'tile glass empty', 'data-testid': 'not-found' }, el('div', { class: 'label mb-2' }, 'That category is not in your stock'),
    el('a', { class: 'btn-pill btn-go d-inline-flex align-items-center text-decoration-none', href: '#stock' }, 'Back to all stock'));
}

function needsLook(look) {
  return el('section', { class: 'tile glass', 'aria-label': 'Needs a look' },
    el('div', { class: 'label mb-1' }, `${count(look.count)} items need a look`),
    el('div', { class: 'soft mb-2' }, 'Loreg could not place these with confidence, so they are left out of the totals until you check them.'),
    el('ul', { class: 'mb-0' }, look.items.map((i) => el('li', {}, [i.sku, i.name, (i.reasons || []).map(humanCode).join(', ')].filter(Boolean).join(' · ')))));
}
