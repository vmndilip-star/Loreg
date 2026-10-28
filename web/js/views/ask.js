// Ask Loreg: a chat that reads a streamed answer (fetch + stream reader). Cards are drawn only from what the server sends.
import { api, ApiError, askStream } from '../api.js';
import { append, clear, el, icon } from '../dom.js';
import { count, money } from '../format.js';
import { humanCode, sourceBlock, statusChip } from '../parts.js';
import { planCard } from '../plan.js';

const SUGGESTIONS = ['What should I clear before the launch?', 'How much money is tied up?', 'Give me today’s summary.'];
const STEP = {
  router: 'Understood your question', stored_market_event: 'Checked your stored launch news', inventory_facts: 'Read your numbers',
  inventory_agent: 'Explained an item', recommender_agent: 'Picked an option', shop_facts: 'Read your shop numbers', daily_agent: 'Wrote the summary',
};
const HOW = (source) => (source === 'fallback' ? ' (by rules)' : source === 'mock' ? ' (offline test writer)' : source ? ' (model)' : '');

export async function mount(root, ctx) {
  let summary;
  try {
    summary = await api('/summary', { signal: ctx.signal });
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      append(root, [el('h1', { class: 'h3 mb-3' }, 'Ask Loreg'), el('section', { class: 'tile glass empty' },
        el('div', { class: 'label mb-2' }, 'No stock list yet'),
        el('p', { class: 'soft' }, 'Loreg answers from your own numbers. Upload your stock list first.'),
        el('a', { class: 'btn-pill btn-go d-inline-flex align-items-center text-decoration-none', href: '#upload' }, 'Upload a stock list'))]);
      return;
    }
    throw e;
  }
  ctx.setAsOf(summary.as_of);

  const moneyNumber = el('div', { class: 'num num-lg', 'data-testid': 'money-at-risk' }, money(summary.money_at_risk_cents));
  async function refreshMoney() {
    const s = await api('/summary', { signal: ctx.signal });
    moneyNumber.textContent = money(s.money_at_risk_cents);
  }
  const log = el('div', { class: 'd-flex flex-column gap-3', role: 'log', 'aria-label': 'Conversation', 'data-testid': 'chat-log' });
  const input = el('input', { id: 'q', class: 'flex-grow-1', type: 'text', maxlength: '500', placeholder: 'Ask about your stock', autocomplete: 'off', 'aria-label': 'Your question' });
  const sendButton = el('button', { type: 'submit', class: 'send', 'aria-label': 'Send', 'data-testid': 'send' }, icon('i-send'));
  const form = el('form', { class: 'composer glass d-flex align-items-center gap-2 mt-3', 'aria-label': 'Ask a question' }, input, sendButton);
  let busy = false;
  let launchQuestion = SUGGESTIONS[0];

  append(root, [el('h1', { class: 'h3 mb-3' }, 'Ask Loreg'), el('div', { class: 'row g-3' },
    el('div', { class: 'col-12 col-lg-4' }, el('section', { class: 'tile tile-amber d-flex flex-column gap-2', 'aria-label': 'Money at risk' },
      el('div', { class: 'label' }, 'Money at risk'), moneyNumber,
      el('div', { class: 'small fw-semibold' }, 'Loreg shows one option per item. Nothing happens until you accept, and you can decline any of them.'))),
    el('div', { class: 'col-12 col-lg-8' }, log,
      el('div', { class: 'glass d-flex flex-wrap gap-2 mt-3 p-2', 'aria-label': 'Suggested questions', role: 'group' }, SUGGESTIONS.map((q) => el('button', { type: 'button', class: 'btn-pill btn-soft', onclick: () => send(q) }, q))),
      form))]);

  form.addEventListener('submit', (e) => { e.preventDefault(); const q = input.value.trim(); if (q) { input.value = ''; send(q); } });

  async function send(question, shown) {
    if (busy) return;
    busy = true; input.disabled = true; sendButton.disabled = true;
    append(log, [el('div', { class: 'bubble-user', 'data-testid': 'user-bubble' }, shown || question)]);
    const status = el('div', { class: 'soft small', role: 'status' });
    const steps = el('ul', { class: 'steps glass', 'data-testid': 'steps' });
    const reply = el('div', { 'data-testid': 'reply' });
    const cards = el('div', { class: 'd-flex flex-column gap-2 mt-2', 'data-testid': 'cards' });
    const foot = el('div', { class: 'soft small mt-2' });
    append(log, [el('div', { class: 'bubble-bot glass', 'data-testid': 'bot-bubble' }, status, steps, reply, cards, foot)]);
    log.lastElementChild.scrollIntoView({ block: 'end' });
    try {
      for await (const { event, data } of askStream(question, { signal: ctx.signal })) {
        if (event === 'status') status.textContent = data.message;
        else if (event === 'tool') append(steps, [el('li', {}, `${STEP[data.name] || humanCode(data.name)}${data.sku ? ` ${data.sku}` : ''}${HOW(data.source && typeof data.source === 'string' ? data.source : '')}`)]);
        else if (event === 'token') reply.textContent += data.text;
        else if (event === 'cards') { for (const c of data.cards) append(cards, [card(c)]); }
        else if (event === 'done') {
          status.textContent = '';
          moneyNumber.textContent = money(data.money_at_risk_cents);   // the server's figure, sent with the end of the stream
          foot.textContent = `Answered in ${(data.total_ms / 1000).toFixed(1)} s.`;
          if (data.pipeline === 'launch_clear' && !data.help_requested) launchQuestion = question;
          ctx.announce('Answer ready');
        } else if (event === 'error') {
          status.textContent = '';
          append(cards, [el('div', { class: 'banner banner-error' }, data.message)]);
        }
      }
    } catch (e) {
      status.textContent = '';
      if (e.name !== 'AbortError') append(cards, [el('div', { class: 'banner banner-error' }, e instanceof ApiError ? e.detail : 'The answer stopped early. Nothing was changed.')]);
    } finally {
      busy = false; input.disabled = false; sendButton.disabled = false;
      if (!ctx.signal.aborted) input.focus();
    }
  }

  function card(c) {
    if (c.type === 'item') return itemCard(c);
    if (c.type === 'plan') return el('div', { 'data-testid': 'plan' }, planCard(c, ctx, refreshMoney));
    if (c.type === 'summary') return summaryCard(c);
    if (c.type === 'alert') return alertCard(c);
    return el('div', {});
  }

  function alertCard(c) {
    const holder = el('section', { class: 'tile glass', 'data-testid': 'alert-card', 'aria-label': 'Launch alert' });
    const actions = el('div', { class: 'd-flex gap-2 flex-wrap' });
    const yes = el('button', { type: 'button', class: 'btn-pill btn-go', 'data-testid': 'plan-yes' }, 'Yes, plan it');
    const no = el('button', { type: 'button', class: 'btn-pill btn-soft' }, 'Not now');
    const answer = el('div', { class: 'soft', role: 'status' });
    yes.addEventListener('click', async () => {
      if (busy) return;
      yes.disabled = no.disabled = true;
      try {
        await api(`/alerts/${c.alert_id}/help`, { method: 'POST', signal: ctx.signal });
        clear(actions);
        send(launchQuestion, 'Yes, plan it');
      } catch (e) { yes.disabled = no.disabled = false; ctx.fail(e); }
    });
    no.addEventListener('click', () => { answer.textContent = 'Okay. I will not plan anything until you ask.'; clear(actions); });
    append(actions, [yes, no]);
    append(holder, [el('div', { class: 'chip chip-amber mb-2' }, 'Alert'), el('div', { class: 'fw-bold' }, c.title),
      el('div', { class: 'soft mb-2' }, `${count(c.n)} items, ${money(c.cap_cents)} tied up.`), sourceBlock(c.source),
      el('div', { class: 'label mt-3 mb-2' }, c.question), actions, answer]);
    return holder;
  }
}

function itemCard(c) {
  return el('section', { class: 'tile glass', 'data-testid': 'item-card', 'data-sku': c.sku, 'aria-label': c.name },
    el('div', { class: 'd-flex flex-wrap gap-2 align-items-center mb-1' }, el('span', { class: 'fw-bold' }, c.name), statusChip(c.status)),
    el('div', { class: 'soft small mb-1' }, `SKU ${c.sku} · ${count(c.shelf)} on shelf · ${money(c.cap_cents)} tied up · ${c.why}`),
    el('div', {}, c.explanation));
}

function summaryCard(c) {
  return el('section', { class: 'tile glass', 'data-testid': 'summary-card', 'aria-label': 'Summary' },
    el('div', { class: 'fw-bold' }, `Money at risk: ${money(c.money_at_risk_cents)}`),
    el('div', { class: 'soft' }, `${count(c.dead.n)} dead (${money(c.dead.cap_cents)}) and ${count(c.slow.n)} slow (${money(c.slow.cap_cents)}) of ${money(c.stock_at_cost_cents)} stock at cost.`));
}
