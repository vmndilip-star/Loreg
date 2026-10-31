// Hash router and shared helpers. Each view is a module in ./views/ that exports mount(root, ctx).
import { api, ApiError, setCsrf, whenSignedOut } from './api.js';
import { append, clear, el } from './dom.js';
import { date } from './format.js';
import { initSearch } from './search.js';
import { state } from './state.js';

const VIEWS = { home: 'Home', stock: 'Stock', watch: 'Loreg Watch', ask: 'Ask Loreg', upload: 'Upload', signin: 'Sign in', account: 'Account', welcome: 'Welcome' };
const OPEN_VIEWS = new Set(['signin']);              // every other view needs a session; the server refuses its data without one too
const main = document.getElementById('main');
const banner = document.getElementById('banner');
const live = document.getElementById('live');
let navToken = 0;
let controller = null;

const ctx = {
  api, state,
  signal: null,
  session: { signed_in: false },
  // Keep the session the server sent (its CSRF token goes with every POST) and show or hide the signed-in parts of the page.
  // Signed out, only the sign-in screen is shown: no search box, no navigation.
  setSession(s) {
    ctx.session = s || { signed_in: false };
    setCsrf(ctx.session.csrf_token);
    for (const id of ['account', 'siteSearchForm', 'dock']) document.getElementById(id).hidden = !ctx.session.signed_in;
    if (!ctx.session.signed_in) ctx.setUnread(0);
  },
  // Sign out, then start the page again from nothing (see restartSignedOut).
  async signOut() {
    await api('/logout', { method: 'POST' });
    restartSignedOut();
  },
  // Opening the app (or signing in) records what the launch tracker has newly noticed, each change once, and shows the unread count.
  refreshUnread() {
    api('/notifications/refresh', { method: 'POST' }).then((r) => ctx.setUnread(r.unread)).catch(() => {});
  },
  params: new URLSearchParams(),   // the part of the address after ?, for example #stock?category=tv
  announce(text) { live.textContent = text; },
  // The header's unread count, from the server's number. Hidden at zero.
  setUnread(n) {
    const badge = document.getElementById('unread');
    badge.hidden = !(n > 0);
    badge.textContent = n > 0 ? `${n} new` : '';
    badge.setAttribute('aria-label', `${n} unread launch notification${n === 1 ? '' : 's'}`);
  },
  // The header badge: which backend answers, and how many plan items carry a rules-based option. The offline test writer is never
  // shown as a model. Read-only; a failure only hides the badge.
  refreshQuality() {
    const badge = document.getElementById('backend');
    if (!ctx.session.signed_in) { badge.hidden = true; return; }
    api('/quality').then((q) => {
      const items = q.plan_items.items ? ` · ${q.plan_items.rules_based} of ${q.plan_items.items} items used rules-based fallback` : '';
      badge.textContent = `Backend: ${q.backend_label}${items}`;
      badge.className = `chip small ${q.is_mock ? 'chip-amber' : 'chip-blue'}`;
      badge.hidden = false;
    }).catch(() => { badge.hidden = true; });
  },
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
  const name = location.hash.slice(1).split('?')[0];
  return Object.hasOwn(VIEWS, name) ? name : 'home';
}

function currentParams() {
  return new URLSearchParams(location.hash.includes('?') ? location.hash.slice(location.hash.indexOf('?') + 1) : '');
}

async function show(name) {
  if (!ctx.session.signed_in && !OPEN_VIEWS.has(name)) { ctx.navigate('signin'); return; }
  const token = ++navToken;
  if (controller) controller.abort();
  controller = new AbortController();
  ctx.signal = controller.signal;
  ctx.params = currentParams();
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
    ctx.refreshQuality();
    const h1 = main.querySelector('h1');
    if (h1) { h1.setAttribute('tabindex', '-1'); h1.focus({ preventScroll: true }); }
    ctx.announce(`${VIEWS[name]} loaded`);
  }
}

// After signing out, or when the session is gone, the page is loaded again on #signin. A reload drops everything this page held in
// memory for the last shop: search results, the header's stock date, the alert and upload it was looking at, any view's data.
// Nothing is kept in localStorage, sessionStorage or a service worker, and every page file is served no-cache.
function restartSignedOut() {
  history.replaceState(null, '', `${location.pathname}#signin`);
  location.reload();
}

// A request answered 401. Signed in until now: the session ended (expired, or signed out elsewhere), so start again. Already signed
// out: just show the sign-in screen.
whenSignedOut(() => {
  if (ctx.session.signed_in) restartSignedOut(); else ctx.navigate('signin');
});

async function start() {
  try { ctx.setSession(await api('/session')); } catch (e) { ctx.setSession(null); ctx.fail(e); }
  initSearch(ctx);
  if (ctx.session.signed_in) ctx.refreshUnread();
  window.addEventListener('hashchange', () => show(currentName()));
  show(currentName());
}
start();
