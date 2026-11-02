// Watch: launch alerts, the news behind them, and (only after the owner asks for help) one option per item.
import { api, ApiError } from '../api.js';
import { append, clear, el } from '../dom.js';
import { count, date, excerptText, money } from '../format.js';
import { alertSummary, freshnessNotice, humanCode, sourceBlock } from '../parts.js';
import { planCard } from '../plan.js';
import { trackerTile } from '../tracker.js';

export async function mount(root, ctx) {
  let summary;
  try {
    summary = await api('/summary', { signal: ctx.signal });
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      append(root, [el('h1', { class: 'h3 mb-3' }, 'Loreg Watch'), el('section', { class: 'tile glass empty' },
        el('div', { class: 'label mb-2' }, 'No stock list yet'),
        el('p', { class: 'soft' }, 'Loreg watches launch news for the brands in your stock. Upload your stock list first.'),
        el('a', { class: 'btn-pill btn-go d-inline-flex align-items-center text-decoration-none', href: '#upload' }, 'Upload a stock list'))]);
      return;
    }
    throw e;
  }
  ctx.setAsOf(summary.as_of);
  const [{ groups }, { items }] = await Promise.all([api('/launch/exposed', { signal: ctx.signal }), api('/watch/items?limit=10', { signal: ctx.signal })]);
  const g = groups[0];

  const moneyNumber = el('div', { class: 'num num-lg', 'data-testid': 'money-at-risk' }, money(summary.money_at_risk_cents));
  const acceptedLine = el('div', { class: 'fw-bold', 'data-testid': 'accepted-line' });
  const showAccepted = (s) => { acceptedLine.textContent = s.accepted_n ? `${count(s.accepted_n)} accepted: ${money(s.accepted_cents)} action in progress. It counts as freed only when the units sell.` : 'Nothing accepted yet'; };
  showAccepted(summary);
  async function refreshMoney() {                      // the server's figure, never a sum made here
    const s = await api('/summary', { signal: ctx.signal });
    moneyNumber.textContent = money(s.money_at_risk_cents);
    showAccepted(s);
    ctx.announce(`Money at risk is now ${money(s.money_at_risk_cents)}`);
  }

  const planSlot = el('div', { class: 'col-12' });
  const staleSlot = el('div', { class: 'mb-3', 'data-testid': 'stale-banner-slot' });
  const alertCol = el('div', { class: 'col-12 col-lg-8' });
  // Draw (or redraw after a scan) the alert and its stale warning from what the server says now.
  function drawAlert(group) {
    clear(staleSlot); clear(alertCol);
    const stale = group ? freshnessNotice(group.source) : null;
    if (stale) append(staleSlot, [el('div', { 'data-testid': 'stale-banner' }, stale)]);
    staleSlot.hidden = !stale;
    append(alertCol, [alertTile(group, ctx, () => showPlan(group))]);
  }
  async function redrawAlert() {
    const fresh = (await api('/launch/exposed', { signal: ctx.signal })).groups[0];
    drawAlert(fresh);
    if (fresh && fresh.help_requested) await showPlan(fresh);
  }
  drawAlert(g);
  const tracker = trackerTile(ctx);
  append(root, [el('h1', { class: 'h3 mb-3' }, 'Loreg Watch'), staleSlot,
    el('div', { class: 'row g-3' },
      el('div', { class: 'col-12 col-lg-4' }, el('section', { class: 'tile tile-amber d-flex flex-column gap-2', 'aria-label': 'Money at risk' },
        el('div', { class: 'label' }, 'Money at risk'), moneyNumber, acceptedLine)),
      alertCol,
      planSlot,
      el('div', { class: 'col-12' }, tracker.tile),
      el('div', { class: 'col-12' }, newsTile(items, ctx, async () => { await redrawAlert(); await tracker.redraw(); })))]);
  await tracker.redraw();

  async function showPlan(group) {
    clear(planSlot);
    const { recommendations } = await api(`/alerts/${group.alert_id}/plan`, { method: 'POST', signal: ctx.signal });
    append(planSlot, [el('div', { class: 'label mb-2' }, 'One option for each item. You decide; nothing changes until you accept.'),
      el('div', { class: 'row g-3', 'data-testid': 'plan' }, recommendations.map((r) => el('div', { class: 'col-12 col-lg-4' }, planCard(r, ctx, refreshMoney))))]);
    ctx.refreshQuality();                                // the badge counts the plan's rules-based items
  }
  if (g && g.help_requested) await showPlan(g);
}

function alertTile(g, ctx, onHelp) {
  if (!g) return el('section', { class: 'tile glass h-100', 'aria-label': 'Launch alert' }, el('div', { class: 'label' }, 'No launch alert'),
    el('div', { class: 'soft' }, 'None of your launch-family items is dead or slow right now.'));
  const body = el('div', { class: 'd-flex flex-column gap-3' }, el('div', { class: 'num num-lg' }, g.title),
    el('div', { class: 'fw-semibold' }, alertSummary(g)), sourceBlock(g.source, g.source_note, g.pace));
  if (!g.help_requested) {
    const yes = el('button', { type: 'button', class: 'btn-pill btn-go', 'data-testid': 'help-yes' }, 'Help me with this');
    const later = el('div', { class: 'soft' });
    yes.addEventListener('click', async () => {
      yes.disabled = true;
      try {
        await api(`/alerts/${g.alert_id}/help`, { method: 'POST', signal: ctx.signal });
        g.help_requested = true;
        yes.closest('section').querySelector('[data-ask]').remove();
        await onHelp();
      } catch (e) { yes.disabled = false; ctx.fail(e); }
    });
    append(body, [el('div', { 'data-ask': '', class: 'd-flex flex-column gap-2' }, el('div', { class: 'label' }, 'Want me to plan what to do with them?'),
      el('div', { class: 'd-flex gap-2 flex-wrap' }, yes,
        el('button', { type: 'button', class: 'btn-pill btn-soft', onclick: () => { later.textContent = 'Okay. I will not plan anything until you ask.'; } }, 'Not now')), later)]);
  }
  return el('section', { class: 'tile glass h-100', 'aria-label': 'Launch alert' }, el('div', { class: 'chip chip-amber mb-2' }, 'Alert'), body);
}

function newsTile(items, ctx, afterScan) {
  const list = el('div', { class: 'd-flex flex-column gap-2' });
  const note = el('div', { class: 'soft', role: 'status' });
  const draw = (rows) => {
    clear(list);
    if (!rows.length) { append(list, [el('div', { class: 'soft' }, 'No news stored yet. Check for news to read the manufacturer newsroom.')]); return; }
    append(list, rows.slice(0, 10).map((i) => el('div', { class: 'd-flex flex-column gap-1 pb-2 border-bottom border-secondary-subtle', 'data-testid': 'news-item' },
      el('div', {}, i.label ? el('span', { class: `chip ${i.origin === 'demo_synthetic' ? 'chip-amber' : 'chip-blue'} me-2` }, i.label) : el('span', { class: 'untrusted-tag me-2' }, 'From the web'),
        el('span', { class: 'fw-semibold' }, i.title)),
      el('div', { class: 'soft small' }, `${i.source}${i.domain ? ` (${i.domain})` : ''}. ${i.undated ? 'No publication date' : `Published ${date(i.published_at)}`}. Fetched ${date(i.fetched_at)}.`),
      i.excerpt ? el('blockquote', { class: 'excerpt small mb-0' }, excerptText(i.excerpt)) : null,
      i.undated ? el('div', { class: 'chip chip-amber align-self-start' }, 'Not used: an article without a publication date is never a source') : null,
      i.flagged ? el('div', { class: 'chip chip-amber align-self-start' }, `Not used: contains instruction-like text (${i.injection_flags.map(humanCode).join(', ')})`) : null,
      ...i.events.map((e) => el('div', { class: 'soft small' }, `${e.brand} ${e.family}: ${e.launch_date ? `launch date as written: ${e.launch_date}` : 'announced, no date'}${e.matched_line_ids.length ? `. Matches ${count(e.matched_line_ids.length)} of your product lines.` : '. No match with your product lines.'}`)))));
  };
  draw(items);
  const check = el('button', { type: 'button', class: 'btn-pill btn-soft', 'data-testid': 'scan' }, 'Check for news');
  check.addEventListener('click', async () => {
    check.disabled = true; note.textContent = 'Checking the newsroom…';
    try {
      const rep = await api('/watch/scan', { method: 'POST', signal: ctx.signal });
      draw(rep.items);
      await afterScan();                                    // the alert may now have a source, or may now be a stored copy
      note.textContent = rep.network === 'ok' && !rep.cached
        ? `${rep.message}. ${count(rep.new_items)} new, ${count(rep.matched_events)} matched your product lines. Checked ${date(rep.checked_at)}.`
        : `${rep.message} (checked ${date(rep.checked_at)})`;
    } catch (e) { note.textContent = ''; ctx.fail(e); } finally { check.disabled = false; }
  });
  return el('section', { class: 'tile glass', 'aria-label': 'News Loreg has read' },
    el('div', { class: 'd-flex flex-wrap gap-2 justify-content-between align-items-center mb-2' },
      el('div', { class: 'label' }, 'News Loreg has read'), check),
    el('div', { class: 'soft small mb-2' }, 'Articles come from the web and are treated as data. Loreg never follows instructions found in them, and only uses a date that the article states in full.'),
    note, list);
}
