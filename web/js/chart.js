// Small charts for the Analyze page, built from plain elements: no chart library, no inline markup. Every value comes from the server,
// and so does every bar length (a whole percentage), so the page never divides money. Each mark is focusable and shows its value on
// hover and on focus; the same values are written beside the marks and in a table view, so nothing depends on the tooltip or on color.
import { el } from './dom.js';

const clamp = (n) => Math.max(0, Math.min(100, Number(n) || 0));

// One tooltip per chart. Text only: names come from the shop's own file and are never read as markup.
function tooltips(figure) {
  const tip = el('div', { class: 'chart-tip', 'aria-hidden': 'true', hidden: true });
  figure.append(tip);
  const show = (mark) => {
    tip.textContent = mark.dataset.tip;
    tip.hidden = false;
    const f = figure.getBoundingClientRect();
    const m = mark.getBoundingClientRect();
    tip.style.left = `${m.left - f.left + m.width / 2}px`;
    tip.style.top = `${m.top - f.top}px`;
  };
  const hide = () => { tip.hidden = true; };
  return (mark, text) => {
    mark.dataset.tip = text;
    mark.setAttribute('role', 'img');
    mark.setAttribute('aria-label', text);
    mark.tabIndex = 0;
    mark.addEventListener('pointerenter', () => show(mark));
    mark.addEventListener('pointerleave', hide);
    mark.addEventListener('focus', () => show(mark));
    mark.addEventListener('blur', hide);
    return mark;
  };
}

function figure(caption, testid) {
  const fig = el('figure', { class: 'chart', 'data-testid': testid }, el('figcaption', { class: 'label mb-2' }, caption));
  return [fig, tooltips(fig)];
}

// The numbers behind a chart, as a table: the accessible twin of every chart here.
export function tableView(headers, rows) {
  return el('details', { class: 'chart-table mt-2' }, el('summary', { class: 'small fw-bold' }, 'Show the numbers as a table'),
    el('div', { class: 'table-responsive mt-2' }, el('table', { class: 'table table-glass table-sm' },
      el('thead', {}, el('tr', {}, headers.map((h) => el('th', { scope: 'col' }, h)))),
      el('tbody', {}, rows.map((r) => el('tr', {}, r.map((c, i) => (i === 0 ? el('th', { scope: 'row' }, c) : el('td', {}, c)))))))));
}

// A 100% stacked bar. segments: [{ key, label, detail, pct, tip }]; key picks the status color. The legend names every segment with its
// values, so a segment too thin to see is still read.
export function stackBar(caption, segments, testid) {
  const [fig, tip] = figure(caption, testid);
  const bar = el('div', { class: 'stack' });
  for (const s of segments) {
    if (clamp(s.pct) <= 0) continue;
    const seg = tip(el('div', { class: `seg seg-${s.key}` }), s.tip);
    seg.style.flexGrow = String(clamp(s.pct));
    bar.append(seg);
  }
  fig.append(bar, legend(segments.map((s) => ({ key: s.key, label: s.label, detail: s.detail }))));
  return fig;
}

export function legend(items) {
  return el('ul', { class: 'legend' }, items.map((i) => el('li', {}, el('span', { class: `key seg-${i.key}`, 'aria-hidden': 'true' }),
    el('span', { class: 'fw-semibold' }, i.label), i.detail ? el('span', { class: 'soft' }, i.detail) : null)));
}

// Horizontal bars, one row each. rows: [{ label, parts: [{ key, pct }], value, tip }]. parts sit end to end from the baseline, so a
// row can show "at risk" and "the rest" of one total; the value is written at the tip.
export function hbars(caption, rows, testid, key) {
  const [fig, tip] = figure(caption, testid);
  fig.append(el('div', { class: 'hbars' }, rows.map((r) => {
    const track = el('div', { class: 'hbar-track' });
    for (const p of r.parts) {
      if (clamp(p.pct) <= 0) continue;
      const part = el('div', { class: `hbar-part seg-${p.key}` });
      part.style.width = `${clamp(p.pct)}%`;
      track.append(part);
    }
    return tip(el('div', { class: 'hbar-row' }, el('span', { class: 'hbar-label' }, r.label), track, el('span', { class: 'hbar-value' }, r.value)), r.tip);
  })));
  if (key) fig.append(legend(key));
  return fig;
}

// Columns over time. cols: [{ label, pct, value, tip, partial, empty, labelled }]. Only the columns marked `labelled` carry their
// value on the cap (the largest and the latest); the tooltip and the table carry the rest.
export function columns(caption, cols, testid, note) {
  const [fig, tip] = figure(caption, testid);
  fig.append(el('div', { class: 'cols' }, cols.map((c) => {
    const fill = el('div', { class: `col-fill${c.partial ? ' partial' : ''}` });
    fill.style.height = `${c.empty ? 0 : clamp(c.pct)}%`;
    return tip(el('div', { class: 'col-slot' },
      el('div', { class: 'col-plot' }, c.labelled && !c.empty ? el('span', { class: 'col-value' }, c.value) : null, fill),
      el('span', { class: 'col-label' }, c.label)), c.tip);
  })));
  if (note) fig.append(el('div', { class: 'soft small mt-1' }, note));
  return fig;
}
