// The signed-in organization's profile, as text, and signing out.
import { api } from '../api.js';
import { append, el } from '../dom.js';

const ROWS = [['Organization', 'org_name'], ['Email', 'email'], ['Street', 'street'], ['City', 'city'], ['State', 'state'], ['ZIP code', 'zip'],
  ['Country', 'country']];

export async function mount(root, ctx) {
  const p = await api('/profile', { signal: ctx.signal });
  const out = el('button', { type: 'button', class: 'btn-pill btn-soft align-self-start', 'data-testid': 'signout' }, 'Sign out');
  out.addEventListener('click', async () => {
    out.disabled = true;
    try {
      ctx.setSession(await api('/logout', { method: 'POST' }));
      ctx.navigate('signin');
    } catch (e) { out.disabled = false; ctx.fail(e); }
  });
  append(root, [el('h1', { class: 'h3 mb-3' }, 'Account'),
    el('section', { class: 'tile glass d-flex flex-column gap-3', 'aria-label': 'Organization profile' },
      el('dl', { class: 'mb-0 d-flex flex-column gap-2', 'data-testid': 'profile' }, ROWS.map(([label, key]) => el('div', {},
        el('dt', { class: 'small soft' }, label), el('dd', { class: 'mb-0 text-break', 'data-field': key }, p[key] || '')))),
      out)]);
}
