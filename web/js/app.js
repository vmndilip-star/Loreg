// Hash router and shared helpers. Each view is a module in ./views/ that exports mount(root, ctx).
import { api, ApiError } from './api.js';
import { append, clear, el } from './dom.js';
import { date } from './format.js';
import { state } from './state.js';

const VIEWS = { home: 'Home', stock: 'Stock', watch: 'Loreg Watch', ask: 'Ask Loreg', upload: 'Upload' };
const main = document.getElementById('main');
const banner = document.getElementById('banner');
const live = document.getElementById('live');
let navToken = 0;
let controller = null;

const ctx = {
  api, state,
  signal: null,
  announce(text) { live.textContent = text; },
  setAsOf(iso) { document.getElementById('asOf').textContent = iso ? `Stock as of ${date(iso)}. Synthetic data.` : ''; },
  showBanner(kind, text) {
    clear(banner);
    if (text) append(banner, [el('div', { class: `banner banner-${kind}` }, text)]);
  },
  // Show an API failure in plain words; "could not reach" gets its own banner so it never looks like empty data.
  fail(e) {
    if (e && e.name === 'AbortError') return;
    ctx.showBanner('error', e instanceof ApiError ? e.detail : 'Something went wrong. Nothing was changed.');
  },
  navigate(name) { location.hash = `#${name}`; },
  async refresh() { await show(currentName()); },
};

function currentName() {
  const name = location.hash.slice(1);
  return Object.hasOwn(VIEWS, name) ? name : 'home';
}

async function show(name) {
  const token = ++navToken;
  if (controller) controller.abort();
  controller = new AbortController();
  ctx.signal = controller.signal;
  ctx.showBanner(null, '');
  document.getElementById('viewName').textContent = VIEWS[name];
  document.title = `${VIEWS[name]} · Loreg`;
  for (const link of document.querySelectorAll('[data-nav]')) {
    if (link.dataset.nav === name) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  }
  clear(main);
  try {
    const view = await import(`./views/${name}.js`);   // name comes from the VIEWS allowlist above
    if (token !== navToken) return;
    await view.mount(main, ctx);
  } catch (e) {
    if (token === navToken) ctx.fail(e);
  }
  if (token === navToken) {
    const h1 = main.querySelector('h1');
    if (h1) { h1.setAttribute('tabindex', '-1'); h1.focus({ preventScroll: true }); }
    ctx.announce(`${VIEWS[name]} loaded`);
  }
}

window.addEventListener('hashchange', () => show(currentName()));
show(currentName());
