// The signed-in organization's profile, as text, and signing out. The demo account can also reset its data (demo mode only).
import { api, ApiError } from '../api.js';
import { append, clear, el } from '../dom.js';

const ROWS = [['Organization', 'org_name'], ['Email', 'email'], ['Street', 'street'], ['City', 'city'], ['State', 'state'], ['ZIP code', 'zip'],
  ['Country', 'country']];

// Two clicks, so a reset is never one slip away. The server refuses it for every account but the demo account.
function demoReset(ctx) {
  const note = el('div', { class: 'small', role: 'status', 'data-testid': 'demo-reset-note' });
  const ask = el('button', { type: 'button', class: 'btn-pill btn-soft', 'data-testid': 'demo-reset' }, 'Reset demo data');
  const yes = el('button', { type: 'button', class: 'btn-pill btn-go', 'data-testid': 'demo-reset-confirm', hidden: true }, 'Yes, reset it');
  ask.addEventListener('click', () => {
    yes.hidden = false;
    clear(note);
    append(note, ['This deletes the demo account’s plans, decisions and briefs, marks its notifications unread, and loads the demo stock list again.']);
  });
  yes.addEventListener('click', async () => {
    ask.disabled = true; yes.disabled = true;
    try {
      await api('/demo/reset', { method: 'POST', signal: ctx.signal });
      ctx.navigate('home');
    } catch (e) {
      ask.disabled = false; yes.disabled = false;
      clear(note);
      append(note, [e instanceof ApiError ? e.detail : 'Something went wrong. Nothing was changed.']);
    }
  });
  return el('div', { class: 'd-flex flex-column gap-2' },
    el('div', { class: 'soft small' }, 'This is the demo account. Its data is synthetic and its address is made up.'),
    el('div', { class: 'd-flex flex-wrap gap-2' }, ask, yes), note);
}

export async function mount(root, ctx) {
  const p = await api('/profile', { signal: ctx.signal });
  const out = el('button', { type: 'button', class: 'btn-pill btn-soft align-self-start', 'data-testid': 'signout' }, 'Sign out');
  out.addEventListener('click', async () => {
    out.disabled = true;
    try {
      await ctx.signOut();                                   // the page starts again on #signin, holding nothing of this shop
    } catch (e) { out.disabled = false; ctx.fail(e); }
  });
  append(root, [el('h1', { class: 'h3 mb-3' }, 'Account'),
    el('section', { class: 'tile glass d-flex flex-column gap-3', 'aria-label': 'Organization profile' },
      el('dl', { class: 'mb-0 d-flex flex-column gap-2', 'data-testid': 'profile' }, ROWS.map(([label, key]) => el('div', {},
        el('dt', { class: 'small soft' }, label), el('dd', { class: 'mb-0 text-break', 'data-field': key }, p[key] || '')))),
      ctx.session.demo ? demoReset(ctx) : null,
      out)]);
}
