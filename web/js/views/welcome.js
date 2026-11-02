// Shown after every sign-in and signup: the signed-in organization's name, a short greeting, and Continue to Home.
// The name comes from the session the server sent and is set as text, never as HTML.
import { append, el } from '../dom.js';

export function mount(root, ctx) {
  const name = (ctx.session.profile && ctx.session.profile.org_name) || 'there';
  const go = el('button', { type: 'button', class: 'btn-pill btn-go align-self-start', 'data-testid': 'welcome-continue' }, 'Continue');
  go.addEventListener('click', () => ctx.navigate('home'));
  append(root, [el('section', { class: 'tile glass d-flex flex-column gap-3', 'aria-label': 'Welcome', 'data-testid': 'welcome' },
    el('h1', { class: 'h3 mb-0', 'data-testid': 'welcome-name' }, `Welcome, ${name}.`),
    el('p', { class: 'mb-0' }, 'I\'m ready to provide a smart assistant for your data.'),
    go)]);
}
