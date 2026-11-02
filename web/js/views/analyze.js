// Analyze: pick a category, see its figures, then choose what to look at: the stock report, the sales history, or what to buy.
// Every figure and every bar length is read from the API; this file only lays them out. The address holds the choice, so Back works
// and a view can be shared: #analyze?category=tv&show=buy (show: report | sales | buy; sales may add &sku=...).
import { api, ApiError } from '../api.js';
import { append, clear, el } from '../dom.js';
import { count, date, money, plural } from '../format.js';
import { statusChip } from '../parts.js';
import { columns, hbars, stackBar, tableView } from '../chart.js';

const SHOW = { report: 'Stock report', sales: 'Sales history', buy: 'What to buy' };
const STATUS_LABEL = { dead: 'Dead', slow: 'Slow', healthy: 'Healthy', new: 'New', insufficient_data: 'Not enough data' };
const WINDOW_LABEL = { '1d': 'Last day', '7d': 'Last 7 days', '30d': 'Last 30 days', '365d': 'Last 365 days' };
const TOP_BRANDS = 8;
const FIRST_ROWS = { buy: 10, dont: 5 };
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const units = (n) => plural(n, 'unit');
const monthName = (ym) => { const [y, m] = ym.split('-').map(Number); return `${MONTHS[m - 1]} ${y}`; };
const span = (from, to) => `${date(from)} to ${date(to)}`;

export async function mount(root, ctx) {
  let summary;
  try {
    summary = await api('/summary', { signal: ctx.signal });
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return empty(root);
    throw e;
  }
  ctx.setAsOf(summary.as_of);
  const { categories } = await api('/categories', { signal: ctx.signal });
  const known = new Set(categories.map((c) => c.key));
  const state = { category: known.has(ctx.params.get('category')) ? ctx.params.get('category') : '', show: Object.hasOwn(SHOW, ctx.params.get('show')) ? ctx.params.get('show') : '',
    sku: (ctx.params.get('sku') || '').slice(0, 64) };

  const pickSlot = el('div', { class: 'col-12' });
  const statsSlot = el('div', { class: 'col-12', 'data-testid': 'cat-stats' });
  const askSlot = el('div', { class: 'col-12' });
  const panelSlot = el('div', { class: 'col-12', 'data-testid': 'analyze-panel', 'aria-live': 'polite' });
  append(root, [el('h1', { class: 'h3 mb-1' }, 'Analyze'),
    el('p', { class: 'soft mb-3' }, 'Pick a category to see its figures. Then choose what to look at.'),
    el('div', { class: 'row g-3' }, pickSlot, statsSlot, askSlot, panelSlot)]);

  const go = (patch) => {
    Object.assign(state, patch);
    const q = new URLSearchParams();
    if (state.category) q.set('category', state.category);
    if (state.show) q.set('show', state.show);
    if (state.show === 'sales' && state.sku) q.set('sku', state.sku);
    history.pushState(null, '', `#analyze${q.toString() ? `?${q}` : ''}`);
    draw(Boolean(patch.show));
  };

  function drawPicker() {
    clear(pickSlot);
    append(pickSlot, [el('section', { class: 'tile glass', 'aria-label': 'Category' },
      el('div', { class: 'label mb-2' }, 'Category'),
      el('div', { class: 'd-flex flex-wrap gap-2', role: 'group', 'aria-label': 'Category' }, categories.map((c) => el('button', {
        type: 'button', class: 'chipbtn', 'aria-pressed': String(state.category === c.key), 'data-testid': 'analyze-category', 'data-category': c.key,
        onclick: () => go({ category: c.key, show: '', sku: '' }) }, `${c.label} (${count(c.items)})`))))]);
  }

  function drawAsk() {
    clear(askSlot);
    if (!state.category) return;
    const label = (categories.find((c) => c.key === state.category) || {}).label || state.category;
    append(askSlot, [el('section', { class: 'tile glass', 'aria-label': 'What to look at' },
      el('div', { class: 'label mb-2' }, `What would you like to see for ${label}?`),
      el('div', { class: 'd-flex flex-wrap gap-2', role: 'group', 'aria-label': 'What to look at' }, Object.entries(SHOW).map(([k, v]) => el('button', {
        type: 'button', class: 'chipbtn', 'aria-pressed': String(state.show === k), 'data-testid': `show-${k}`, onclick: () => go({ show: k, sku: '' }) }, v))))]);
  }

  async function draw(scroll = false) {
    drawPicker();
    drawAsk();
    clear(statsSlot); clear(panelSlot);
    if (!state.category) return;
    try {
      const stats = await api(`/categories/${encodeURIComponent(state.category)}`, { signal: ctx.signal });
      append(statsSlot, [statsTile(stats)]);
      if (state.show === 'report') await report(panelSlot, state, ctx);
      else if (state.show === 'sales') await sales(panelSlot, state, ctx, (sku) => go({ sku }));
      else if (state.show === 'buy') await buy(panelSlot, state, ctx);
      if (state.show) ctx.announce(`${SHOW[state.show]} for ${stats.label} loaded`);
      if (state.show && scroll) panelSlot.scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    } catch (e) {
      if (e.name === 'AbortError') return;
      clear(panelSlot);
      append(panelSlot, [failure(e)]);
    }
  }

  await draw();
}

function statsTile(s) {
  const tile = (label, value, extra) => el('div', { class: 'col-6 col-md-4 col-xl' }, el('div', { class: 'stat' },
    el('div', { class: 'soft small fw-bold' }, label), el('div', { class: 'num num-md' }, value), extra ? el('div', { class: 'soft small' }, extra) : null));
  const statuses = s.statuses.map((x) => ({ key: x.status, label: STATUS_LABEL[x.status] || x.status, pct: x.pct,
    detail: `${plural(x.items, 'item')} · ${money(x.cap_cents)} · ${x.pct}%`,
    tip: `${STATUS_LABEL[x.status] || x.status}: ${plural(x.items, 'item')}, ${units(x.units)}, ${money(x.cap_cents)} tied up (${x.pct}%)` }));
  const brands = s.brands.slice(0, TOP_BRANDS).map((b) => ({ label: b.brand, value: money(b.cap_cents),
    parts: [{ key: 'risk', pct: b.risk_bar_pct }, { key: 'rest', pct: b.bar_pct - b.risk_bar_pct }],
    tip: `${b.brand}: ${money(b.cap_cents)} tied up in ${plural(b.items, 'item')}, ${money(b.at_risk_cap_cents)} of it at risk; ${units(b.sold_90d)} sold in 90 days` }));
  return el('section', { class: 'tile glass d-flex flex-column gap-4', 'aria-label': `${s.label} at a glance` },
    el('div', { class: 'label' }, `${s.label} at a glance`),
    el('div', { class: 'row g-3' },
      tile('Items', count(s.items)),
      tile('On the shelf', units(s.units)),
      tile('Money tied up', money(s.stock_cap_cents)),
      tile('Money at risk', money(s.at_risk_cap_cents), `${plural(s.at_risk_n, 'item')} dead or slow`),
      tile('Sold in 30 days', units(s.sold_30d), `${units(s.sold_90d)} in 90 days`)),
    el('div', {},
      stackBar('Where the money is, by status', statuses, 'status-bar'),
      tableView(['Status', 'Items', 'Units', 'Tied up', 'Share'], s.statuses.map((x) => [STATUS_LABEL[x.status] || x.status, count(x.items), count(x.units),
        money(x.cap_cents), `${x.pct}%`]))),
    el('div', {},
      hbars('Money tied up, by brand', brands, 'brand-bars', [{ key: 'risk', label: 'At risk (dead or slow)' }, { key: 'rest', label: 'The rest' }]),
      s.brands.length > TOP_BRANDS ? el('div', { class: 'soft small mt-1' }, `The ${TOP_BRANDS} brands with the most money tied up. The table lists all ${count(s.brands.length)}.`) : null,
      tableView(['Brand', 'Items', 'Units', 'Sold in 90 days', 'Tied up', 'At risk'], s.brands.map((b) => [b.brand, count(b.items), count(b.units),
        count(b.sold_90d), money(b.cap_cents), money(b.at_risk_cap_cents)]))));
}

// ---- stock report ------------------------------------------------------------------------------------------------------------------
async function report(slot, state, ctx) {
  const data = await api(`/products?${new URLSearchParams({ category: state.category, status: 'all', sort: 'tied_up', page_size: '25' })}`, { signal: ctx.signal });
  const t = data.totals;
  append(slot, [el('section', { class: 'tile glass', 'aria-label': 'Stock report', 'data-testid': 'stock-report' },
    el('div', { class: 'd-flex flex-wrap justify-content-between align-items-center gap-2 mb-2' },
      el('div', { class: 'label' }, `Stock report: ${plural(t.items, 'item')}, ${money(t.at_risk_cap_cents)} at risk`),
      el('a', { class: 'linkbtn', href: `#stock?${new URLSearchParams({ category: state.category, status: 'all' })}` }, 'Open every item in Stock')),
    el('div', { class: 'soft small mb-2' }, data.rows.length < t.items ? `The ${count(data.rows.length)} items with the most money tied up.` : 'Every item, most money tied up first.'),
    el('div', { class: 'table-responsive' }, el('table', { class: 'table table-glass' },
      el('thead', {}, el('tr', {}, ['Item', 'On shelf', 'Days since sold', 'Status', 'Why', 'Tied up'].map((h) => el('th', { scope: 'col' }, h)))),
      el('tbody', {}, data.rows.map((r) => el('tr', { 'data-sku': r.sku },
        el('td', {}, el('div', { class: 'fw-semibold' }, r.name), el('div', { class: 'soft small' }, `${r.sku} · ${r.variant}`)),
        el('td', {}, count(r.qty)), el('td', {}, r.days_since_sold === null ? 'never' : count(r.days_since_sold)), el('td', {}, statusChip(r.status)),
        el('td', {}, r.why), el('td', { class: 'fw-bold text-nowrap' }, money(r.tied_up_cents))))))))]);
}

// ---- sales history -----------------------------------------------------------------------------------------------------------------
async function sales(slot, state, ctx, pickItem) {
  const q = state.sku ? `?${new URLSearchParams({ sku: state.sku })}` : '';
  const [h, items] = await Promise.all([
    api(`/categories/${encodeURIComponent(state.category)}/sales${q}`, { signal: ctx.signal }),
    api(`/products?${new URLSearchParams({ category: state.category, status: 'all', sort: 'name', page_size: '100' })}`, { signal: ctx.signal })]);
  const picker = el('select', { id: 'salesItem', class: 'sortbox', 'data-testid': 'sales-item' },
    el('option', { value: '' }, `All ${h.label}`),
    items.rows.map((r) => el('option', { value: r.sku, selected: r.sku === state.sku }, `${r.name} (${r.variant}) · ${r.sku}`)));
  picker.addEventListener('change', () => pickItem(picker.value));
  const partList = items.totals.items > items.rows.length ? el('div', { class: 'soft small' }, `The item list shows the first ${count(items.rows.length)} by name.`) : null;
  const who = h.item ? `${h.item.name} (${h.item.variant})` : `All ${h.label}`;
  const head = el('div', { class: 'd-flex flex-wrap justify-content-between align-items-center gap-2' },
    el('div', { class: 'label' }, `Sales history: ${who}`),
    el('span', { class: 'd-flex gap-2 align-items-center mw-100' }, el('label', { for: 'salesItem', class: 'small fw-bold' }, 'Item'), picker), partList);
  const tile = el('section', { class: 'tile glass d-flex flex-column gap-4', 'aria-label': 'Sales history', 'data-testid': 'sales-history' }, head);
  append(slot, [tile]);

  if (h.source === 'stock_list') {
    const s = h.stock_list;
    append(tile, [el('div', { class: 'banner banner-info', role: 'note', 'data-testid': 'no-history' }, h.note),
      hbars('Units sold: the last 30 days against the average 30 days before', [
        { label: 'Last 30 days', parts: [{ key: 'one', pct: s.bar_pct_30d }], value: units(s.sold_30d), tip: `Last 30 days: ${units(s.sold_30d)}` },
        { label: 'The 30 days before, on average', parts: [{ key: 'one', pct: s.bar_pct_before }], value: `${s.month_before_avg} units`,
          tip: `The 60 days before the last 30, per 30 days: ${s.month_before_avg} units` }], 'stock-list-bars'),
      el('a', { class: 'btn-pill btn-soft d-inline-flex align-items-center text-decoration-none align-self-start', href: '#upload' }, 'Upload a sales history')]);
    return;
  }

  append(tile, [el('div', { class: 'd-flex flex-wrap gap-2 align-items-center small' },
    h.synthetic ? el('span', { class: 'chip chip-amber', 'data-testid': 'synthetic' }, 'Synthetic sales history') : null,
    el('span', { class: 'soft' }, `${h.file ? `From ${h.file}: ` : ''}sales from ${span(h.first_date, h.as_of)}. ${h.note}`))]);
  if (!h.check_30d.match) {
    append(tile, [el('div', { class: 'banner banner-warn', role: 'note', 'data-testid': 'history-mismatch' },
      `The sales history has ${units(h.check_30d.history)} sold in the last 30 days, but the stock list says ${units(h.check_30d.stock_list)}. One of the two files may be out of date.`)]);
  }
  append(tile, [el('div', { class: 'row g-3', 'data-testid': 'sales-windows' }, h.windows.map((w) => el('div', { class: 'col-6 col-lg-3' },
    el('div', { class: 'stat h-100', 'data-window': w.key },
      el('div', { class: 'soft small fw-bold' }, w.key === '1d' ? `Last day (${date(w.to)})` : WINDOW_LABEL[w.key]),
      w.covered ? [el('div', { class: 'num num-md' }, units(w.units)), el('div', { class: 'soft small' }, `${money(w.revenue_cents)} in sales`),
        w.returned ? el('div', { class: 'soft small' }, `${units(w.returned)} returned`) : null]
        : el('div', { class: 'soft' }, 'The history does not go back this far.')))))]);

  const top = Math.max(...h.monthly.map((m) => m.units));
  const last = h.monthly.length - 1;
  const partial = h.monthly.filter((m) => m.partial).map((m) => `${monthName(m.month)} is counted ${span(m.from, m.to)}.`);
  append(tile, [el('div', {},
    columns('Units sold, by month', h.monthly.map((m, i) => ({ label: MONTHS[Number(m.month.slice(5)) - 1] + (i === 0 || m.month.endsWith('-01') ? ` ’${m.month.slice(2, 4)}` : ''),
      pct: m.bar_pct, value: count(m.units), partial: m.partial, empty: !m.covered, labelled: i === last || (m.units === top && top > 0),
      tip: m.covered ? `${monthName(m.month)}: ${units(m.units)}, ${money(m.revenue_cents)} in sales${m.partial ? ` (${span(m.from, m.to)})` : ''}`
        : `${monthName(m.month)}: before the sales history starts` })), 'monthly-chart', partial.length ? `Lighter columns are part months: ${partial.join(' ')}` : ''),
    tableView(['Month', 'Units sold', 'Returned', 'Sales'], h.monthly.map((m) => [monthName(m.month), m.covered ? count(m.units) : 'no history',
      m.covered ? count(m.returned) : '', m.covered ? money(m.revenue_cents) : ''])))]);

  append(tile, [el('div', {},
    hbars('Units sold, by year', h.yearly.map((y) => ({ label: y.partial ? `${y.year}*` : String(y.year), parts: [{ key: 'one', pct: y.bar_pct }],
      value: `${units(y.units)} · ${money(y.revenue_cents)}`, tip: `${y.year}: ${units(y.units)}, ${money(y.revenue_cents)} in sales (${span(y.from, y.to)})` })), 'yearly-chart'),
    h.yearly.some((y) => y.partial) ? el('div', { class: 'soft small mt-1' }, `* Part years: ${h.yearly.filter((y) => y.partial).map((y) => `${y.year} is ${span(y.from, y.to)}`).join('; ')}.`) : null,
    tableView(['Year', 'From', 'To', 'Units sold', 'Returned', 'Sales'], h.yearly.map((y) => [String(y.year), date(y.from), date(y.to), count(y.units),
      count(y.returned), money(y.revenue_cents)])))]);
}

// ---- what to buy -------------------------------------------------------------------------------------------------------------------
async function buy(slot, state, ctx) {
  const r = await api(`/categories/${encodeURIComponent(state.category)}/restock`, { signal: ctx.signal });
  const name = (i) => [el('div', { class: 'fw-semibold' }, i.name), el('div', { class: 'soft small' }, `${i.sku} · ${i.variant}`)];
  // A long list shows its first rows (the soonest to run out, or the most money tied up) and a button for the rest.
  const table = (testid, headers, rows, first = rows.length) => {
    rows.forEach((r, i) => { if (i >= first) r.hidden = true; });
    const more = rows.length > first ? el('button', { type: 'button', class: 'linkbtn btn btn-link text-white p-0 mt-1', 'data-testid': `${testid}-more`,
      onclick: () => { rows.forEach((r) => { r.hidden = false; }); more.remove(); } }, `Show all ${count(rows.length)} items`) : null;
    return [el('div', { class: 'table-responsive' }, el('table', { class: 'table table-glass', 'data-testid': testid },
      el('thead', {}, el('tr', {}, headers.map((h) => el('th', { scope: 'col' }, h)))), el('tbody', {}, rows))), more];
  };
  append(slot, [el('section', { class: 'tile glass d-flex flex-column gap-4', 'aria-label': 'What to buy', 'data-testid': 'what-to-buy' },
    el('div', {},
      el('div', { class: 'label mb-1' }, `What to buy: ${r.label}`),
      el('div', { class: 'num num-md', 'data-testid': 'buy-total' }, r.buy.length ? `Buy ${units(r.buy_units)} of ${plural(r.buy.length, 'item')}, about ${money(r.buy_cost_cents)}`
        : 'Nothing to buy now'),
      el('div', { class: 'soft small mt-1', 'data-testid': 'buy-note' }, r.note),
      el('div', { class: 'soft small' }, `${plural(r.enough_n, 'item')} already ${r.enough_n === 1 ? 'has' : 'have'} ${r.target_days} days of stock or more.`)),
    r.buy.length ? el('div', {}, el('div', { class: 'label mb-2' }, 'Buy'),
      table('buy-table', ['Item', 'On shelf', `Sold in ${r.pace_days} days`, 'Lasts', 'Buy', 'Cost', 'Why'], r.buy.map((i) => el('tr', { 'data-sku': i.sku },
        el('td', {}, name(i)), el('td', {}, count(i.qty)), el('td', {}, count(i.sold_90d)), el('td', { class: 'text-nowrap' }, `${count(i.cover_days)} days`),
        el('td', { class: 'fw-bold' }, count(i.buy_units)), el('td', { class: 'fw-bold text-nowrap' }, money(i.buy_cost_cents)), el('td', { class: 'small' }, i.why))),
      FIRST_ROWS.buy)) : null,
    r.wait.length ? el('div', {}, el('div', { class: 'label mb-2' }, 'Wait'),
      table('wait-table', ['Item', 'On shelf', 'Why'], r.wait.map((i) => el('tr', { 'data-sku': i.sku, 'data-reason': i.reason },
        el('td', {}, name(i)), el('td', {}, count(i.qty)), el('td', { class: 'small' }, i.why))))) : null,
    r.dont_buy.length ? el('div', {}, el('div', { class: 'label mb-1' }, 'Buy no more'),
      el('div', { class: 'soft small mb-2' }, `${plural(r.dont_buy.length, 'item')} ${r.dont_buy.length === 1 ? 'is' : 'are'} dead or slow, with ${money(r.dont_buy_cap_cents)} tied up. Sell these first.`),
      table('dont-buy-table', ['Item', 'On shelf', 'Status', 'Why', 'Tied up'], r.dont_buy.map((i) => el('tr', { 'data-sku': i.sku },
        el('td', {}, name(i)), el('td', {}, count(i.qty)), el('td', {}, statusChip(i.status)), el('td', { class: 'small' }, i.why),
        el('td', { class: 'fw-bold text-nowrap' }, money(i.tied_up_cents)))), FIRST_ROWS.dont)) : null)]);
}

function empty(root) {
  append(root, [el('h1', { class: 'h3 mb-3' }, 'Analyze'), el('section', { class: 'tile glass empty' },
    el('div', { class: 'label mb-2' }, 'No stock list yet'),
    el('p', { class: 'soft' }, 'Upload your stock list to analyze it by category.'),
    el('a', { class: 'btn-pill btn-go d-inline-flex align-items-center text-decoration-none', href: '#upload' }, 'Upload a stock list'))]);
}

function failure(e) {
  return el('section', { class: 'tile glass', role: 'alert' }, el('div', { class: 'label mb-1' }, 'This could not be loaded'),
    el('div', { class: 'soft mb-2' }, e instanceof ApiError ? e.detail : 'Something went wrong. Nothing was changed.'),
    el('button', { type: 'button', class: 'btn-pill btn-soft', onclick: () => location.reload() }, 'Try again'));
}

