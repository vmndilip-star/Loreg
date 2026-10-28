// The search box in the header, on every screen. Words are sent to /api/search; every figure shown comes from the answer.
import { api, ApiError } from './api.js';
import { append, clear, el } from './dom.js';
import { count, money, plural } from './format.js';
import { statusChip } from './parts.js';

const DEBOUNCE_MS = 200;
const MIN_CHARS = 2;

export function stockLink(params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== '') q.set(k, v);
  const text = q.toString();
  return `#stock${text ? `?${text}` : ''}`;
}

export function initSearch(ctx) {
  const input = document.getElementById('siteSearch');
  const list = document.getElementById('searchList');
  const form = document.getElementById('siteSearchForm');
  let timer = null;
  let controller = null;
  let options = [];          // the clickable results, in order
  let active = -1;

  const cancelPending = () => { clearTimeout(timer); if (controller) controller.abort(); };   // a late answer must not reopen the list
  const close = () => { cancelPending(); list.hidden = true; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); active = -1; };
  const open = () => { list.hidden = false; input.setAttribute('aria-expanded', 'true'); };
  const note = (text) => { clear(list); options = []; append(list, [el('div', { class: 'search-note' }, text)]); open(); };

  function mark(n) {
    active = options.length ? (n + options.length) % options.length : -1;
    options.forEach((o, i) => o.node.setAttribute('aria-selected', i === active ? 'true' : 'false'));
    if (active >= 0) { input.setAttribute('aria-activedescendant', options[active].node.id); options[active].node.scrollIntoView({ block: 'nearest' }); } else input.removeAttribute('aria-activedescendant');
  }

  function go(href) { close(); input.blur(); location.hash = href.slice(1); }

  function option(id, href, ...children) {
    const node = el('div', { class: 'search-option', role: 'option', id, 'aria-selected': 'false' }, ...children);
    node.addEventListener('mousedown', (e) => e.preventDefault());       // keep focus in the box until the click lands
    node.addEventListener('click', () => go(href));
    options.push({ node, href });
    return node;
  }

  function render(result) {
    clear(list); options = []; active = -1;
    const parts = [];
    if (result.lines.length) {
      parts.push(el('div', { class: 'search-group', role: 'presentation' }, 'Lines'));
      result.lines.forEach((l, n) => parts.push(option(`opt-line-${n}`, stockLink({ category: l.category, q: l.line }),
        el('span', { class: 'fw-bold' }, `${l.brand} ${l.line}`),
        el('span', { class: 'small soft' }, `${l.category_label} · ${plural(l.items, 'item')} · ${money(l.at_risk_cap_cents)} at risk`))));
    }
    if (result.items.length) {
      parts.push(el('div', { class: 'search-group', role: 'presentation' }, 'Items'));
      result.items.forEach((i, n) => parts.push(option(`opt-item-${n}`, stockLink({ q: i.sku }),
        el('span', { class: 'd-flex gap-2 align-items-center flex-wrap' }, el('span', { class: 'fw-bold' }, i.name), statusChip(i.status)),
        el('span', { class: 'small soft' }, `${i.sku} · ${count(i.qty)} on shelf · ${money(i.tied_up_cents)} tied up`))));
    }
    if (!parts.length) { note('Nothing matches. Try fewer words.'); ctx.announce('Nothing matches'); return; }
    append(list, parts);
    open();
    ctx.announce(`${plural(result.lines.length, 'line')} and ${plural(result.items.length, 'item')} match`);
  }

  async function run() {
    const q = input.value.trim();
    if (controller) controller.abort();
    if (q.length < MIN_CHARS) { close(); return; }
    controller = new AbortController();
    note('Searching…');
    try {
      render(await api(`/search?q=${encodeURIComponent(q)}`, { signal: controller.signal }));
    } catch (e) {
      if (e.name === 'AbortError') return;
      note(e instanceof ApiError && e.status === 422 ? e.detail : 'Search is not available right now.');
    }
  }

  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(run, DEBOUNCE_MS); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); if (list.hidden && input.value.trim().length >= MIN_CHARS) run(); else mark(active + 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); mark(active - 1); }
    else if (e.key === 'Escape') { close(); }
    else if (e.key === 'Enter') {
      e.preventDefault();
      const q = input.value.trim();
      if (active >= 0 && !list.hidden) go(options[active].href);
      else if (q.length >= MIN_CHARS) go(stockLink({ q }));          // Enter on a query opens the full results table
    }
  });
  form.addEventListener('submit', (e) => e.preventDefault());
  form.addEventListener('focusout', (e) => { if (!form.contains(e.relatedTarget)) close(); });
  document.addEventListener('keydown', (e) => {
    const t = e.target;
    const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
    if (e.key === '/' && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); input.focus(); input.select(); }
  });
  window.addEventListener('hashchange', () => close());
}
