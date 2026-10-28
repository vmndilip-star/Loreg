// Home: the alert comes first, then one question. No option, no discount, until the owner asks for help.
import { api, ApiError } from '../api.js';
import { append, clear, el } from '../dom.js';
import { count, date, money } from '../format.js';
import { alertSummary, moneyTile, sourceBlock, sourceNote } from '../parts.js';

export async function mount(root, ctx) {
  let summary;
  try {
    summary = await api('/summary', { signal: ctx.signal });
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return empty(root);
    throw e;
  }
  ctx.setAsOf(summary.as_of);
  const t = summary.totals;
  const groups = (await api('/launch/exposed', { signal: ctx.signal })).groups;

  const alertSlot = el('div', { class: 'col-12', 'data-testid': 'alert' });
  const briefSlot = el('div', { class: 'col-12 col-lg-6', 'data-testid': 'brief' });
  const grid = el('div', { class: 'row g-3' },
    alertSlot,
    el('div', { class: 'col-12 col-lg-6' }, moneyTile('Money at risk', summary.money_at_risk_cents,
      el('div', { class: 'fw-bold' }, `${count(t.at_risk.n)} of ${count(t.skus)} items are dead or slow.`))),
    el('div', { class: 'col-12 col-lg-6' }, el('div', { class: 'row g-3 h-100' },
      el('div', { class: 'col-6' }, el('section', { class: 'tile glass h-100 d-flex flex-column justify-content-center', 'aria-label': 'Dead items' },
        el('div', { class: 'num num-lg' }, `${count(t.dead.n)} dead`), el('div', { class: 'soft fw-semibold' }, `${money(t.dead.cap_cents)} tied up`))),
      el('div', { class: 'col-6' }, el('section', { class: 'tile glass h-100 d-flex flex-column justify-content-center', 'aria-label': 'Slow items' },
        el('div', { class: 'num num-lg' }, `${count(t.slow.n)} slow`), el('div', { class: 'soft fw-semibold' }, `${money(t.slow.cap_cents)} tied up`))))),
    briefSlot,
    summary.needs_look.count ? el('div', { class: 'col-12 col-lg-6' }, el('section', { class: 'tile glass h-100', 'aria-label': 'Needs a look' },
      el('div', { class: 'label' }, `${count(summary.needs_look.count)} items need a look`),
      el('div', { class: 'soft mb-2' }, 'Loreg could not place these with confidence, so they are not in the numbers above.'),
      el('a', { class: 'btn-pill btn-soft d-inline-flex align-items-center text-decoration-none', href: '#stock' }, 'Open Stock'))) : null);
  append(root, [el('h1', { class: 'h3 mb-3' }, 'Home'), grid]);

  renderAlert(alertSlot, groups[0], ctx);
  await renderBrief(briefSlot, ctx);
}

function empty(root) {
  append(root, [el('h1', { class: 'h3 mb-3' }, 'Home'), el('section', { class: 'tile glass empty' },
    el('div', { class: 'label mb-2' }, 'No stock list yet'),
    el('p', { class: 'soft' }, 'Upload your stock list and Loreg will show how much money is tied up in dead and slow stock.'),
    el('a', { class: 'btn-pill btn-go d-inline-flex align-items-center text-decoration-none', href: '#upload' }, 'Upload a stock list'))]);
}

function renderAlert(slot, g, ctx) {
  clear(slot);
  if (!g) {
    append(slot, [el('section', { class: 'tile glass', 'aria-label': 'Launch alert' }, el('div', { class: 'label' }, 'No launch alert'),
      el('div', { class: 'soft' }, 'None of your launch-family items is dead or slow right now.'))]);
    return;
  }
  const body = el('div', { class: 'd-flex flex-column gap-3' });
  const draw = () => {
    clear(body);
    append(body, [el('div', { class: 'num num-lg' }, g.title), el('div', { class: 'fw-semibold' }, alertSummary(g)), sourceBlock(g.source, g.source_note)]);
    if (g.help_requested) {
      append(body, [el('div', { class: 'soft' }, 'You asked for help with this. The plan is waiting for your decision.'),
        el('a', { class: 'btn-pill btn-go d-inline-flex align-items-center text-decoration-none align-self-start', href: '#watch' }, 'Open the plan')]);
    } else if (ctx.state.notNow === g.alert_id) {
      append(body, [el('div', { class: 'soft' }, 'Okay. I will not plan anything until you ask.'),
        el('button', { type: 'button', class: 'btn-pill btn-soft align-self-start', onclick: () => { ctx.state.notNow = null; draw(); } }, 'Ask me again')]);
    } else {
      append(body, [el('div', { class: 'label' }, 'Want me to plan what to do with them?'),
        el('div', { class: 'd-flex gap-2 flex-wrap' },
          el('button', { type: 'button', class: 'btn-pill btn-go', onclick: askForHelp }, 'Yes, help me'),
          el('button', { type: 'button', class: 'btn-pill btn-soft', onclick: () => { ctx.state.notNow = g.alert_id; draw(); } }, 'Not now'))]);
    }
  };
  async function askForHelp(e) {
    const button = e.currentTarget;
    button.disabled = true;
    try {
      await api(`/alerts/${g.alert_id}/help`, { method: 'POST', signal: ctx.signal });
      ctx.state.alertId = g.alert_id;
      ctx.navigate('watch');
    } catch (err) { button.disabled = false; ctx.fail(err); }
  }
  draw();
  append(slot, [el('section', { class: 'tile glass', 'aria-label': 'Launch alert' }, el('div', { class: 'chip chip-amber mb-2' }, 'Alert'), body)]);
}

async function renderBrief(slot, ctx) {
  const card = el('section', { class: 'tile glass h-100 d-flex flex-column gap-2', 'aria-label': 'Loreg Daily' });
  append(slot, [card]);
  const draw = (brief) => {
    clear(card);
    append(card, [el('div', { class: 'label' }, 'Loreg Daily')]);
    if (!brief) {
      append(card, [el('div', { class: 'soft' }, 'No summary has been written yet.')]);
    } else {
      append(card, [el('div', { class: 'fw-bold fs-5', 'data-testid': 'brief-headline' }, brief.headline),
        el('ul', { class: 'mb-1' }, brief.bullets.map((b) => el('li', {}, b))),
        el('div', { class: 'soft small' }, `${sourceNote(brief.source)} Written ${date(brief.generated_at)}.`)]);
    }
    const run = el('button', { type: 'button', class: 'btn-pill btn-soft align-self-start mt-auto' }, brief ? 'Write it again' : 'Write today’s summary');
    run.addEventListener('click', async () => {
      run.disabled = true; clear(run); append(run, [el('span', { class: 'spinner', 'aria-hidden': 'true' }), ' Writing…']);
      try { draw(await api('/daily/run', { method: 'POST', signal: ctx.signal })); ctx.announce('Summary written'); } catch (e) { ctx.fail(e); draw(brief); }
    });
    append(card, [run]);
  };
  try { draw(await api('/daily', { signal: ctx.signal })); } catch (e) {
    if (e instanceof ApiError && e.status === 404) draw(null); else throw e;
  }
}
