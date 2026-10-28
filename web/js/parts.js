// Small shared pieces. Text from the API goes in as text nodes only.
import { el } from './dom.js';
import { date, money, count } from './format.js';

const STATUS = {
  dead: ['chip-red', 'Dead'], slow: ['chip-amber', 'Slow'], new: ['chip-blue', 'New'],
  healthy: ['chip-green', 'Healthy'], insufficient_data: ['', 'Not enough data'],
};

export function statusChip(status) {
  const [cls, label] = STATUS[status] || ['', String(status)];
  return el('span', { class: `chip ${cls}` }, label);
}

// An article link from an untrusted feed is only used if it is a plain web address.
export function safeHref(url) {
  try { const u = new URL(url); return u.protocol === 'https:' || u.protocol === 'http:' ? u.href : null; } catch { return null; }
}

// Shown wherever an alert's source appears. The server decides freshness; this only words it.
export function freshnessNotice(source) {
  if (!source || !source.attached || source.freshness === 'fresh') return null;
  const text = source.freshness === 'stale'
    ? `This news is ${count(source.age_days)} days old (more than ${count(source.stale_after_days)}). Do not read it as fresh news.`
    : 'The date of this news could not be read, so it is not shown as fresh.';
  return el('div', { class: 'banner banner-warn', role: 'note' }, text);
}

// Where an alert's news came from, and when. Every field is shown as text; the link is used only for a real http(s) address on a real article.
export function sourceBlock(source, note) {
  if (!source || !source.attached) return el('div', { class: 'soft' }, note || 'No dated source attached.');
  const real = source.origin !== 'demo_synthetic';
  const href = real ? safeHref(source.url) : null;
  const badge = source.label ? el('span', { class: `chip ${source.origin === 'demo_synthetic' ? 'chip-amber' : 'chip-blue'} me-2`, 'data-testid': 'source-label' }, source.label)
    : el('span', { class: 'untrusted-tag me-2' }, 'From the web');
  return el('div', { class: 'd-flex flex-column gap-1', 'data-testid': 'source' },
    el('div', {}, badge, href ? el('a', { href, target: '_blank', rel: 'noopener noreferrer' }, source.title || source.name) : (source.title || source.name)),
    el('div', { class: 'soft small' }, `${source.name}${source.domain ? ` (${source.domain})` : ''}`),
    el('div', { class: 'soft small d-flex flex-wrap gap-3', 'data-testid': 'source-dates' },
      el('span', {}, `Published ${date(source.published)}`), el('span', {}, `Fetched ${date(source.fetched)}`),
      source.checked ? el('span', {}, `Last checked ${date(source.checked)}`) : null),
    source.excerpt ? el('blockquote', { class: 'excerpt small mb-0', 'data-testid': 'excerpt' }, source.excerpt) : null,
    el('div', { class: 'soft small' }, source.launch_date ? `Launch date, as written in the article: ${source.launch_date}.` : 'Announced. The article gives no launch date.'),
    source.cached && source.cache_note ? el('div', { class: 'banner banner-info', role: 'note', 'data-testid': 'cache-note' }, source.cache_note) : null,
    freshnessNotice(source));
}

export function alertSummary(group) {
  return `${count(group.n)} of your ${group.label} are dead or slow, with ${money(group.cap_cents)} tied up.`;
}

export function moneyTile(label, cents, extra, cls = 'tile-amber') {
  return el('section', { class: `tile ${cls} h-100 d-flex flex-column justify-content-between gap-3`, 'aria-label': label },
    el('div', { class: 'label' }, label), el('div', { class: 'num num-hero', 'data-testid': 'money-at-risk' }, money(cents)), extra || null);
}

// How a written text was produced, in plain words.
export function sourceNote(source) {
  if (!source || source === 'fallback') return 'Written by rules from the numbers; the model was not used.';
  if (source === 'mock') return 'Written by the offline test writer, not a real model.';
  return 'Written by the model from numbers that code computed, and checked against them.';
}

// BAD_CATEGORY -> "Bad category". Codes come from the server; this only makes them readable.
export function humanCode(code) {
  const t = String(code).toLowerCase().replace(/_/g, ' ');
  return t.charAt(0).toUpperCase() + t.slice(1);
}
