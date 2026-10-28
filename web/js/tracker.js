// Launch tracker on the Watch screen. Every sentence and date comes from the server; this only lays them out as text.
import { api } from './api.js';
import { append, clear, el } from './dom.js';
import { count, date } from './format.js';

const STATE_LABEL = {
  announced: 'Announced', date_approximate: 'Approximate date', due: 'Due', date_changed: 'Date changed',
  unverified: 'Unverified', launch_confirmed: 'Launch confirmed',
};
const STATE_CHIP = { due: 'chip-amber', unverified: 'chip-amber', date_changed: 'chip-blue', launch_confirmed: 'chip-green' };

function where(s) {
  return `${s.name}${s.domain ? ` (${s.domain})` : ''}. Published ${date(s.published)}. Fetched ${date(s.fetched)}.`;
}

function row(w) {
  const history = w.history.length ? el('details', { class: 'small' },
    el('summary', { class: 'soft' }, `Date history (${count(w.history.length)})`),
    el('ol', { class: 'mb-0 ps-3 d-flex flex-column gap-1' }, w.history.map((h) => el('li', { 'data-testid': 'history-row' },
      el('div', {}, `${h.old_date ? `${h.old_date} → ` : 'First date: '}${h.new_date}`),
      el('div', { class: 'soft text-break' }, `${h.source_url}. Published ${date(h.published)}. Fetched ${date(h.fetched)}.`))))) : null;
  return el('div', { class: 'd-flex flex-column gap-1 pb-2 border-bottom border-secondary-subtle', 'data-testid': 'tracker-row', 'data-state': w.state },
    el('div', { class: 'd-flex flex-wrap align-items-center gap-2' },
      el('span', { class: `chip ${STATE_CHIP[w.state] || ''}`, 'data-testid': 'tracker-state' }, STATE_LABEL[w.state] || w.state),
      w.label ? el('span', { class: 'chip chip-amber', 'data-testid': 'tracker-label' }, w.label) : null,
      el('span', { class: 'fw-semibold' }, `${w.brand} ${w.line}`)),
    el('div', { class: 'small', 'data-testid': 'tracker-text' }, w.text),
    el('div', { class: 'soft small text-break' }, `Source: ${w.source.title}. ${where(w.source)}`),
    w.confirmation ? el('div', { class: 'soft small text-break', 'data-testid': 'tracker-confirmation' }, `Confirmed by: ${w.confirmation.title}. ${where(w.confirmation)}`) : null,
    history);
}

function noteRow(n, ctx, redraw) {
  const mark = n.read ? null : el('button', { type: 'button', class: 'btn-pill btn-soft align-self-start', 'data-testid': 'note-read' }, 'Mark read');
  if (mark) {
    mark.addEventListener('click', async () => {
      mark.disabled = true;
      try { await api(`/notifications/${n.id}/read`, { method: 'POST', signal: ctx.signal }); await redraw(); } catch (e) { mark.disabled = false; ctx.fail(e); }
    });
  }
  return el('div', { class: `d-flex flex-column gap-1 ${n.read ? 'soft' : ''}`, 'data-testid': 'note', 'data-read': n.read ? 'yes' : 'no' },
    el('div', { class: 'small' }, n.read ? '' : el('span', { class: 'chip chip-blue me-2' }, 'New'), n.text),
    el('div', { class: 'soft small' }, `Noted ${date(n.created_at)}`), mark);
}

// The tracker tile. `redraw` re-reads the tracker and the notifications from the server (after a scan or a "mark read").
export function trackerTile(ctx) {
  const list = el('div', { class: 'd-flex flex-column gap-2', 'data-testid': 'tracker' });
  const notes = el('div', { class: 'd-flex flex-column gap-2', 'data-testid': 'notes' });
  const allRead = el('button', { type: 'button', class: 'btn-pill btn-soft', 'data-testid': 'notes-read-all' }, 'Mark all read');
  async function redraw() {
    const [t, n] = await Promise.all([api('/launch/tracker', { signal: ctx.signal }), api('/notifications?limit=20', { signal: ctx.signal })]);
    clear(list); clear(notes);
    append(list, t.watches.length ? t.watches.map(row)
      : [el('div', { class: 'soft' }, 'No launch is being followed yet. Check for news to read the manufacturer newsroom.')]);
    append(notes, n.items.length ? n.items.map((x) => noteRow(x, ctx, redraw)) : [el('div', { class: 'soft small' }, 'No notifications.')]);
    allRead.hidden = !n.unread;
    ctx.setUnread(n.unread);
  }
  allRead.addEventListener('click', async () => {
    allRead.disabled = true;
    try { await api('/notifications/read-all', { method: 'POST', signal: ctx.signal }); await redraw(); } catch (e) { ctx.fail(e); } finally { allRead.disabled = false; }
  });
  const tile = el('section', { class: 'tile glass d-flex flex-column gap-3', 'aria-label': 'Launch tracker' },
    el('div', {}, el('div', { class: 'label mb-1' }, 'Launch tracker'),
      el('div', { class: 'soft small' }, 'Launch dates as your stored news states them. A date that has come does not mean the product launched: only a later source '
        + 'that says it is on sale confirms it. No news does not mean a delay.')),
    list,
    el('div', { class: 'd-flex flex-wrap gap-2 justify-content-between align-items-center' }, el('div', { class: 'label' }, 'Notifications'), allRead),
    notes);
  return { tile, redraw };
}
