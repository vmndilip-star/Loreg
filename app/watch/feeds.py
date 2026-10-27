"""Polite feed fetching: robots.txt, conditional GET, a timeout, a size cap and an identifying User-Agent.

The HTTP client is injected, so tests run against a fake transport and never touch the network.
Nothing here fetches anything except the feed URL and that host's robots.txt.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import feedparser
import httpx

USER_AGENT = "LoregBot/0.1 (+https://github.com/vmndilip-star/Loreg; hackathon prototype)"
TIMEOUT_S = 10.0
MAX_BYTES = 2 * 1024 * 1024
ROBOTS_TTL = timedelta(hours=24)
ROBOTS_TRIES = 2
SAMSUNG_FEED = "https://news.samsung.com/global/feed"
_TRACKING = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref")


@dataclass
class FeedResult:
    status: str                       # ok | not_modified | blocked_by_robots | robots_unavailable | network_error | too_large | bad_feed
    entries: list = field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    message: str = ""
    robots_allowed: bool | None = None
    robots_checked: bool = False


def canonical_url(url: str) -> str:
    """Lower-case scheme and host, no fragment, no tracking parameters, no trailing slash: one article, one key."""
    p = urlsplit((url or "").strip())
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith(_TRACKING)]
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urlencode(query), ""))


def content_hash(title: str, text: str) -> str:
    return hashlib.sha256(f"{title}\n{text}".encode("utf-8")).hexdigest()


def _robots_decision(http: httpx.Client, feed_url: str) -> tuple[bool | None, str]:
    """True allowed, False disallowed, None unavailable. Follows RFC 9309: 4xx means no rules; 5xx or no answer means do not fetch."""
    parts = urlsplit(feed_url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    last = ""
    for _ in range(ROBOTS_TRIES):
        try:
            r = http.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S, follow_redirects=True)
        except httpx.HTTPError as e:
            last = f"robots.txt unreachable ({type(e).__name__})"
            continue
        if 400 <= r.status_code < 500:
            return True, "robots.txt not found: no rules"
        if r.status_code >= 500:
            last = f"robots.txt answered {r.status_code}"
            continue
        parser = RobotFileParser()
        parser.parse(r.text[:200_000].splitlines())
        return parser.can_fetch(USER_AGENT, feed_url), "robots.txt read"
    return None, last or "robots.txt unavailable"


def fetch_feed(http: httpx.Client, url: str, *, etag: str | None = None, last_modified: str | None = None,
               robots_allowed: bool | None = None, robots_checked_at: str | None = None, now: datetime | None = None) -> FeedResult:
    now = now or datetime.now(timezone.utc)
    fresh = False
    if robots_allowed is not None and robots_checked_at:
        try:
            fresh = now - datetime.fromisoformat(robots_checked_at.replace("Z", "+00:00")) < ROBOTS_TTL
        except ValueError:
            fresh = False
    checked = False
    if fresh:
        allowed, note = robots_allowed, "robots decision cached"
    else:
        allowed, note = _robots_decision(http, url)
        checked = True
    if allowed is None:
        return FeedResult("robots_unavailable", message=note, robots_allowed=None, robots_checked=checked)
    if allowed is False:
        return FeedResult("blocked_by_robots", message="robots.txt does not allow this feed for our User-Agent", robots_allowed=False, robots_checked=checked)

    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        with http.stream("GET", url, headers=headers, timeout=TIMEOUT_S, follow_redirects=True) as r:
            if r.status_code == 304:
                return FeedResult("not_modified", etag=etag, last_modified=last_modified, message="feed has not changed",
                                  robots_allowed=True, robots_checked=checked)
            if r.status_code != 200:
                return FeedResult("network_error", message=f"feed answered HTTP {r.status_code}", robots_allowed=True, robots_checked=checked)
            declared = r.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > MAX_BYTES:
                return FeedResult("too_large", message=f"feed is larger than {MAX_BYTES // 1024 // 1024} MB", robots_allowed=True, robots_checked=checked)
            body = bytearray()
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    return FeedResult("too_large", message=f"feed is larger than {MAX_BYTES // 1024 // 1024} MB", robots_allowed=True, robots_checked=checked)
            new_etag, new_modified = r.headers.get("etag"), r.headers.get("last-modified")
    except httpx.HTTPError as e:
        return FeedResult("network_error", message=f"could not reach the feed ({type(e).__name__})", robots_allowed=True, robots_checked=checked)
    parsed = feedparser.parse(bytes(body))
    if not parsed.version:  # feedparser could not recognise RSS or Atom (an HTML page, plain text, ...)
        return FeedResult("bad_feed", message="the response is not a readable feed", robots_allowed=True, robots_checked=checked)
    return FeedResult("ok", entries=list(parsed.entries), etag=new_etag, last_modified=new_modified,
                      message=f"{len(parsed.entries)} entries", robots_allowed=True, robots_checked=checked)
