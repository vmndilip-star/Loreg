// Sign in, or create an account. Everything typed is sent as plain text; the server trims, checks and limits every field.
import { api, ApiError } from '../api.js';
import { append, clear, el } from '../dom.js';

function field(label, name, attrs = {}) {
  const id = `f-${name}`;
  return el('div', { class: 'd-flex flex-column gap-1' },
    el('label', { for: id, class: 'small fw-semibold' }, label),
    el('input', { id, name, class: 'search-input', autocomplete: 'off', spellcheck: 'false', ...attrs }));
}

function form(title, fields, submitLabel, testid, onSubmit) {
  const note = el('div', { class: 'small', role: 'status', 'data-testid': `${testid}-note` });
  const submit = el('button', { type: 'submit', class: 'btn-pill btn-go align-self-start', 'data-testid': `${testid}-submit` }, submitLabel);
  const f = el('form', { class: 'd-flex flex-column gap-2', 'data-testid': testid, novalidate: true }, ...fields, submit, note);
  f.addEventListener('submit', async (e) => {
    e.preventDefault();
    submit.disabled = true;
    clear(note);
    try {
      await onSubmit(Object.fromEntries(new FormData(f)));
    } catch (err) {
      append(note, [err instanceof ApiError ? err.detail : 'Something went wrong. Nothing was changed.']);
    } finally {
      submit.disabled = false;
    }
  });
  return el('section', { class: 'tile glass d-flex flex-column gap-3', 'aria-label': title }, el('div', { class: 'label' }, title), f);
}

export async function mount(root, ctx) {
  const done = (s) => { ctx.setSession(s); ctx.refreshUnread(); ctx.navigate('welcome'); };   // every sign-in and signup
  const signIn = form('Sign in', [
    field('Email', 'email', { type: 'email', maxlength: 254, autocomplete: 'username', required: true }),
    field('Password', 'password', { type: 'password', maxlength: 128, autocomplete: 'current-password', required: true }),
  ], 'Sign in', 'signin', async (v) => done(await api('/login', { method: 'POST', json: { email: v.email, password: v.password }, signal: ctx.signal })));

  const signUp = form('Create an account', [
    field('Email', 'email', { type: 'email', maxlength: 254, autocomplete: 'email', required: true }),
    field('Password (12 to 128 characters)', 'password', { type: 'password', minlength: 12, maxlength: 128, autocomplete: 'new-password', required: true }),
    field('Organization name', 'org_name', { maxlength: 100, autocomplete: 'organization', required: true }),
    field('Street', 'street', { maxlength: 200, autocomplete: 'street-address', required: true }),
    field('City', 'city', { maxlength: 100, autocomplete: 'address-level2', required: true }),
    field('State', 'state', { maxlength: 100, autocomplete: 'address-level1', required: true }),
    field('ZIP code', 'zip', { maxlength: 12, autocomplete: 'postal-code', required: true }),
    field('Country', 'country', { maxlength: 100, autocomplete: 'country-name', required: true }),
  ], 'Create account', 'signup', async (v) => done(await api('/signup', { method: 'POST', json: v, signal: ctx.signal })));

  // Demo mode only (the route answers 404 otherwise): a button that fills in the public demo credential. It never signs in by itself.
  api('/demo', { signal: ctx.signal }).then((cred) => {
    const fill = el('button', { type: 'button', class: 'btn-pill btn-soft align-self-start', 'data-testid': 'use-demo' }, 'Use demo account');
    fill.addEventListener('click', () => {
      signIn.querySelector('input[name=email]').value = cred.email;
      signIn.querySelector('input[name=password]').value = cred.password;
      signIn.querySelector('[data-testid=signin-submit]').focus();
    });
    append(signIn, [el('div', { class: 'd-flex flex-wrap gap-2 align-items-center' }, fill,
      el('span', { class: 'soft small' }, 'Fills in the demo sign-in. Its data is synthetic.'))]);
  }).catch(() => { /* 404: demo off, nothing to offer */ });

  append(root, [el('h1', { class: 'h3 mb-3' }, 'Sign in to Loreg'),
    el('div', { class: 'row g-3' }, el('div', { class: 'col-12 col-lg-5' }, signIn), el('div', { class: 'col-12 col-lg-7' }, signUp)),
    el('p', { class: 'small mt-3' }, 'No email is verified and there is no password reset yet. Use an address and a password you can keep.')]);
}
