"""Capture one real, current article from the Samsung newsroom feed as the demo's stored copy (data/demo/news_copy.json).

    python scripts/capture_demo_news.py              reads the feed once and writes data/demo/news_items.json: the latest dated items as stored
                                                     copies (title, address, dates, a short excerpt), so the demo's Watch page has real items even
                                                     when robots.txt times out. It also lists items that name one of our launch-family lines.
    python scripts/capture_demo_news.py --pick 2     also writes data/demo/news_copy.json: the stored copy of candidate 2, used as the alert's source

It uses the same polite client as Loreg Watch: the allowlist, robots.txt, conditional GET, a 10 s timeout, a 2 MB cap and our identifying
User-Agent. If robots.txt cannot be read or does not allow the feed, this script stops; it never works around that.

What is written (and nothing else): the article's address, title, published date, the fetched date, a verbatim excerpt of at most 280
characters, a content hash, and the launch date only if the excerpt states it. The article body is never saved. Review the file before you
commit it; the demo labels it "Stored copy" and shows its true dates.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.catalog import load_catalog  # noqa: E402
from app.demo import NEWS_COPY, NEWS_ITEMS  # noqa: E402
from app.watch.allowlist import check_url, load_news_config  # noqa: E402
from app.watch.attach import is_dated  # noqa: E402
from app.watch.excerpt import make_excerpt  # noqa: E402
from app.watch.feeds import content_hash, fetch_feed  # noqa: E402
from app.watch.matching import catalog_lines, match_lines  # noqa: E402
from app.watch.sanitize import sanitize_html, sanitize_title  # noqa: E402
from app.watch.scan import _entry_html, _published  # noqa: E402  (the same reading of a feed entry that Watch uses)


def candidates(entries, news, lines, launch_ids):
    out = []
    for e in entries:
        link = (e.get("link") or "").strip()
        title = sanitize_title(e.get("title") or "")
        text, flags = sanitize_html(_entry_html(e))
        published = _published(e)
        ids = [i for i in match_lines("Samsung", f"{title} {text}", lines) if i in launch_ids]
        if ids and not check_url(link, news.allowed_hosts) and is_dated(published) and not flags:
            out.append({"url": link, "title": title, "published_at": published, "text": text, "line_ids": ids})
    return out


def snapshot(entries, news, now, keep: int = 10) -> list[dict]:
    """The latest `keep` dated, clean, allowlisted items: title, address, dates, a short verbatim excerpt and a hash. No article body."""
    out = []
    for e in entries:
        link = (e.get("link") or "").strip()
        title = sanitize_title(e.get("title") or "")
        text, flags = sanitize_html(_entry_html(e))
        published = _published(e)
        if check_url(link, news.allowed_hosts) or not is_dated(published) or any("instruction" in f or "hidden" in f for f in flags) or not title or not text:
            continue
        out.append({"url": link, "title": title, "published_at": published, "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "excerpt": make_excerpt(text, news.excerpt_chars), "content_hash": content_hash(title, text), "domain": "news.samsung.com"})
    out.sort(key=lambda d: d["published_at"], reverse=True)
    return out[:keep]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pick", type=int, help="write the stored copy for this candidate number")
    args = ap.parse_args()
    news = load_news_config()
    catalog = load_catalog()
    lines = catalog_lines(catalog.variants)
    launch_ids = {v["line_id"] for v in catalog.variants if v["launch"]}
    now = datetime.now(timezone.utc)
    with httpx.Client() as http:
        res = fetch_feed(http, news.sources[0]["url"], allowed=news.allowed_hosts, max_bytes=news.max_feed_bytes, max_redirects=news.max_redirects)
    if res.status != "ok":
        print(f"Could not read the feed ({res.status}): {res.message}. Nothing was written. Try again later.")
        return 2
    items = snapshot(res.entries, news, now)
    NEWS_ITEMS.parent.mkdir(parents=True, exist_ok=True)
    NEWS_ITEMS.write_text(json.dumps({"brand": "Samsung", "source": news.sources[0]["name"], "captured_by": "scripts/capture_demo_news.py",
                                      "robots_checked_on": res.robots_checked_on, "items": items}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {NEWS_ITEMS.name}: {len(items)} real items, newest published {items[0]['published_at'][:10] if items else '-'}, fetched {now:%Y-%m-%d}.")
    found = candidates(res.entries, news, lines, launch_ids)
    print(f"{len(res.entries)} items in the feed; {len(found)} name one of our launch-family lines (and are dated and clean).")
    for n, c in enumerate(found, 1):
        print(f"  [{n}] {c['published_at'][:10]}  {c['title'][:100]}  ->  {', '.join(c['line_ids'])}")
    if not found:
        print("None to capture. The demo will use the synthetic article, labelled 'Demo article (synthetic)'.")
        return 3
    if not args.pick:
        print("Run again with --pick N to write the stored copy.")
        return 0
    c = found[args.pick - 1]
    excerpt = make_excerpt(c["text"], news.excerpt_chars)
    doc = {"brand": "Samsung", "url": c["url"], "title": c["title"], "published_at": c["published_at"], "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "excerpt": excerpt, "content_hash": content_hash(c["title"], c["text"]), "launch_date": None, "domain": "news.samsung.com",
           "captured_by": "scripts/capture_demo_news.py", "matched_line_ids": c["line_ids"]}
    NEWS_COPY.parent.mkdir(parents=True, exist_ok=True)
    NEWS_COPY.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {NEWS_COPY.name}: {len(excerpt)} characters of excerpt, published {doc['published_at'][:10]}, fetched {doc['fetched_at'][:10]}. Review it before committing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
