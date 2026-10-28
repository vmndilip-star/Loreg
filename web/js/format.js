// Display formatting only. The server sends integer cents; nothing here adds, subtracts or compares money.

const WHOLE = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const CENTS = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function money(cents) {
  if (!Number.isFinite(cents)) return '—';
  return `${cents < 0 ? '-' : ''}$${WHOLE.format(Math.abs(Math.round(cents / 100)))}`;
}

export function money2(cents) {
  if (!Number.isFinite(cents)) return '—';
  return `${cents < 0 ? '-' : ''}$${CENTS.format(Math.abs(cents) / 100)}`;
}

export function count(n) {
  return Number.isFinite(n) ? WHOLE.format(n) : '—';
}

// "2026-10-26" or "2026-10-26T08:00:00Z" -> "Oct 26, 2026". Anything else is shown as given, never guessed.
export function date(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
  if (!m) return iso || '';
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 12));
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
}

// An excerpt is a stretch of the article's own words, cut to a limit. It never starts inside a word (the server sees to that), but it can
// start or stop partway through a sentence; "…" says so. The words themselves are shown exactly as stored.
export function excerptText(s) {
  const t = (s || '').trim();
  if (!t) return '';
  return `${/^[a-z]/.test(t) ? '… ' : ''}${t}${/[.!?"”’)\]]$/.test(t) ? '' : ' …'}`;
}

// "1 item", "2 items". English only; this is a display helper like the others here.
export function plural(n, word) {
  return `${count(n)} ${word}${n === 1 ? '' : 's'}`;
}
