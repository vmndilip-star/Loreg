# Plan: live news with provenance (Slice 2)

Status: proposed, nothing built yet. Written 2026-10-28.

**Naming.** This message's constraints are about live news, so this plan treats *that* as Slice 2. The earlier "Slice 2" spec (lines, `#stock/<category>/<line>`, breadcrumbs) is unchanged and not started; I would build it after this unless you want it first.

## What the code does today (checked in the code, not remembered)
- **Fetching.** `POST /api/watch/scan` runs only when the owner presses "Check for news". There is no scheduler. One source is configured in code: the Samsung Global Newsroom RSS feed. It is read with `robots.txt`, conditional GET, a 10 s timeout, a 2 MB cap and an identifying User-Agent. Only the feed is fetched; article pages are never fetched. Feed entries are sanitized, injection-flagged, deduplicated and stored as untrusted.
- **The demo alert is not news.** The demo alert ("Samsung TVs may be affected by a newer line") comes from a catalog flag, and it says "no dated source attached". The tests use synthetic fixtures labelled SYNTHETIC. One real scan during development stored 50 real Samsung items in a throwaway database; none named a catalog line, so no real article has ever been attached.
- **Stored fields.** `market_items`: `url`, `canonical_url`, `title`, `published_at`, `fetched_at`, `text`, flags. `market_events`: the launch date exactly as written, `quote`, matched lines. `market_sources`: `last_fetch_at`, `last_status`, `robots_checked_at`. `alerts`: `source_item_id`, `source_name`, `source_url`, `source_title`, `source_published`, `launch_date`.
- **Stale logic.** age = the stock list's as-of date minus `source_published`; more than 30 days (config, our assumption) is stale; an unreadable date is "unknown". The fetched date is not used.

## Gaps against your constraints
1. The alert's source shows `published` but **not the fetched date** (it exists only on `market_items`).
2. `market_items.text` keeps up to **8,000 characters of article text**; you asked for short excerpts only.
3. **No domain allowlist** beyond the single feed in code. Entry links are stored whatever their host, and redirects are followed without a host check.
4. No **cached** label: when the network is down the stored items come back with a message, but nothing says "this copy was fetched on <date>".
5. An article without a publication date can still attach ("publication date unknown"); a dated source should require one.

## Changes
1. **Provenance on every source.** `source` carries `url`, `domain`, `published`, `fetched`, `checked` (last successful check of the feed) and a short `excerpt`. Read from `market_items` and `market_sources` at request time, so nothing is copied twice. An alert that shows a source without all of url, published and fetched is a test failure. No published date means no attach.
2. **Excerpts only.** Keep title, url, dates and an excerpt of at most 280 characters chosen by code (the sentence around the launch cue, a verbatim substring, never model text). The body is held only until the Market agent has read it, then cleared. Existing rows are truncated by a migration (schema v4; note v3 is MAP).
3. **Domain allowlist** in `config/news_sources.yaml`: exact hosts (start: `news.samsung.com`) plus the feed URLs. Refused: any other host, look-alikes such as `news.samsung.com.example`, a redirect that leaves the list, and entries whose link is off-list (dropped, counted, never stored).
4. **Cached labelling.** If the scan could not reach the feed (network, `robots.txt`, error), the response and the page say: "Stored copy. Published <date>, fetched <date>. Could not check for newer news on <date> (<reason>)." If the feed is unchanged (304) it says "Checked <date>, nothing new". Dates are the stored ones, never today's.
5. **Untrusted stays untrusted.** Same sanitizer and flags; excerpts and titles render as text only; a flagged article is shown as "not used" and never attached.
6. **No invented dates or prices.** Launch dates only as written; `fetched` is the server clock at fetch time; no prices are read from news at all.
7. **Watch UI.** The source block shows: domain, link, "Published", "Fetched", "Last checked", the excerpt, and a "stored copy" badge when cached. The stale banner is unchanged.

## Order and stop points
| step | content |
|---|---|
| S1 | schema v4, allowlist config and its tests (off-list host, look-alike, redirect, off-list entry link) |
| S2 | excerpt-only storage, body cleared after reading, migration, size tests |
| S3 | provenance fields and cached labelling in the API, with tests on a fake clock |
| S4 | Watch UI, browser test, README note |
| **stop** | report: fields, examples, tests, anything that did not fit |

## Decisions I need
1. **Order:** this live-news slice first, or lines and breadcrumbs first? (I recommend news first: it is the weakest part of the demo.)
2. **Body retention:** hold the body only until it has been read, then clear it (recommended), or never store it and read each article during the scan only (items beyond the 10-per-scan limit would wait for the next scan's feed, which may not repeat them).
3. **Demo alert:** the live feed has not matched a catalog line. Options: (a) leave the demo alert honestly unsourced and show the real feed items in the news list; (b) add a demo-only fixture article, labelled "SYNTHETIC demo article, not real news" everywhere it appears, enabled only with `LOREG_DEMO=1`. I recommend (a) for the video and (b) only as a clearly labelled rehearsal aid.
4. **More sources:** only `news.samsung.com` for now. Other brands' newsroom feeds need their `robots.txt` and terms read first; I will not guess their URLs.

Not part of this slice: fetching article pages, a scheduler, price scraping, QuickBooks. The hackathon Official Rules are still unread; nothing here is known to conflict with them or with Meta's docs.
