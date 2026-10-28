// One option card, with Accept, Decline and Undo. Used by Watch and by the chat.
import { api } from './api.js';
import { append, clear, el } from './dom.js';
import { count, money } from './format.js';

export function planCard(r, ctx, refreshMoney) {
  const state = el('div', { class: 'd-flex gap-2 flex-wrap align-items-center mt-auto' });
  const draw = (status) => {
    clear(state);
    const act = (path) => async (e) => {
      const button = e.currentTarget;                  // currentTarget is gone after the first await
      button.disabled = true;
      let done;
      try { done = await api(`/actions/${r.id}/${path}`, { method: 'POST', signal: ctx.signal }); } catch (err) { button.disabled = false; ctx.fail(err); return; }
      try { await refreshMoney(); } catch (err) { ctx.fail(err); }   // the card and the money change together; if the re-read fails, say so
      draw(done.status);
    };
    if (status === 'proposed') {
      append(state, [el('button', { type: 'button', class: 'btn-pill btn-go', 'data-testid': 'accept', onclick: act('accept') }, 'Accept'),
        el('button', { type: 'button', class: 'btn-pill btn-soft', 'data-testid': 'decline', onclick: act('decline') }, 'Decline')]);
    } else {
      append(state, [el('span', { class: `chip ${status === 'accepted' ? 'chip-green' : ''}`, 'data-testid': 'decision' }, status === 'accepted' ? 'Accepted' : 'Declined'),
        el('button', { type: 'button', class: 'btn-pill btn-soft', 'data-testid': 'undo', onclick: act('undo') }, 'Undo')]);
    }
    card.dataset.status = status;
  };
  const card = el('section', { class: 'tile glass h-100 d-flex flex-column gap-2', 'data-testid': 'plan-card', 'data-sku': r.sku, 'aria-label': `Option for ${r.name}` },
    el('div', { class: 'fw-bold' }, r.name),
    el('div', { class: 'soft small' }, `SKU ${r.sku} · ${count(r.shelf)} on shelf · ${money(r.tied_cents)} tied up`),
    el('div', { class: 'num num-lg' }, optionTitle(r)),
    el('ul', { class: 'mb-1 ps-3' }, r.reasons.map((x) => el('li', {}, x))),
    el('div', { class: 'd-flex flex-wrap gap-1' }, r.citations.map((c) => el('span', { class: 'chip' }, `${c.label}: ${c.value}`))),
    state);
  draw(r.status);
  return card;
}

function optionTitle(r) {
  return r.option_label;                // the server already says "Discount 5%" for a discount
}
