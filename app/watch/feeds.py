"""Polite feed fetching: allowlist, robots.txt, conditional GET, a timeout, a size cap and an identifying User-Agent.

The HTTP client is injected, so tests run against a fake transport and never touch the network.
Nothing here fetches anything except an allowlisted feed and that host's robots.txt. Redirects are followed by hand, at most a few,
and every hop is checked against the allowlist before it is requested: a redirect to a look-alike or to http is refused unrequested.
"""

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import feedparser
import httpx

from app.watch.allowlist import check_url, load_news_config

USER_AGENT = "LoregBot/0.1 (+https://github.com/vmndilip-star/Loreg; hackathon prototype)"
TIMEOUT_S = 10.0
MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
ROBOTS_TTL = timedelta(hours=24)      # a successful robots.txt answer is trusted for this long without asking again
ROBOTS_TRIES = 3
ROBOTS_BACKOFF_S = 2.0
_sleep = time.sleep   # tests replace this so they do not wait
SAMSUNG_FEED = "https://news.samsung.com/global/feed"
_TRACKING = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref")
_REDIRECTS = (301, 302, 303, 307, 308)


@dataclass
class FeedResult:
    status: str                       # ok | not_modified | blocked_by_robots | robots_unavailable | network_error | too_large | bad_feed | blocked_host | blocked_redirect
    entries: list = field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    message: str = ""
    robots_allowed: bool | None = None
    robots_checked: bool = False
    robots_checked_on: str | None = None     # the date robots.txt was really read (not today's date, when the answer came from the cache)
    robots_from_cache: bool = False


def canonical_url(url: str) -> str:
    """Lower-case scheme and host, no fragment, no tracking parameters, no trailing slash: one article, one key."""
    p = urlsplit((url or "").strip())
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith(_TRACKING)]
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urlencode(query), ""))


def content_hash(title: str, text: str) -> str:
    return hashlib.sha256(f"{title}\n{text}".encode("utf-8")).hexdigest()


class _Refused(Exception):
    """A redirect went somewhere the allowlist does not permit (or went round in circles)."""

    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _next_hop(current: str, response: httpx.Response, hops: int, allowed, max_redirects: int) -> str:
    location = response.headers.get("location")
    if not location:
        raise _Refused("network_error", "a redirect came without a destination")
    if hops >= max_redirects:
        raise _Refused("blocked_redirect", f"more than {max_redirects} redirects")
    target = urljoin(current, location)
    problem = check_url(target, allowed)
    if problem:
        raise _Refused("blocked_redirect", f"a redirect to {urlsplit(target).hostname or 'an unreadable address'} was refused: {problem}")
    return target


def _robots_decision(http: httpx.Client, feed_url: str, allowed, max_redirects: int = MAX_REDIRECTS) -> tuple[bool | None, str]:
    """True allowed, False disallowed, None unavailable. Follows RFC 9309: 4xx means no rules; 5xx or no answer means do not fetch."""
    parts = urlsplit(feed_url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    last = ""
    for attempt in range(ROBOTS_TRIES):
        if attempt:
            _sleep(ROBOTS_BACKOFF_S * attempt)  # polite: wait a little longer before each retry
        current, hops, r = robots_url, 0, None
        try:
            while True:
                r = http.get(current, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S, follow_redirects=False)
                if r.status_code not in _REDIRECTS:
                    break
                current = _next_hop(current, r, hops, allowed, max_redirects)
                hops += 1
        except httpx.HTTPError as e:
            last = f"robots.txt unreachable ({type(e).__name__})"
            continue
        except _Refused as e:
            return None, f"robots.txt: {e.message}"
        if 400 <= r.status_code < 500:
            return True, "robots.txt not found: no rules"
        if r.status_code >= 500:
            last = f"robots.txt answered {r.status_code}"
            continue
        parser = RobotFileParser()
        parser.parse(r.text[:200_000].splitlines())
        return parser.can_fetch(USER_AGENT, feed_url), "robots.txt read"
    return None, last or "robots.txt unavailable"


def _read_body(r: httpx.Response, max_bytes: int):
    """The body of a 200 answer, or a FeedResult if it is too large."""
    declared = r.headers.get("content-length")
    too_large = FeedResult("too_large", message=f"feed is larger than {max_bytes // 1024 // 1024} MB", robots_allowed=True)
    if declared and declared.isdigit() and int(declared) > max_bytes:
        return too_large
    body = bytearray()
    for chunk in r.iter_bytes():
        body += chunk
        if len(body) > max_bytes:
            return too_large
    return bytes(body)


def fetch_feed(http: httpx.Client, url: str, *, etag: str | None = None, last_modified: str | None = None,
               robots_allowed: bool | None = None, robots_checked_at: str | None = None, now: datetime | None = None,
               allowed=None, max_bytes: int = MAX_BYTES, max_redirects: int = MAX_REDIRECTS) -> FeedResult:
    allowed = allowed if allowed is not None else load_news_config().allowed_hosts
    now = now or datetime.now(timezone.utc)
    problem = check_url(url, allowed)
    if problem:                                    # nothing is requested, not even robots.txt
        return FeedResult("blocked_host", message=f"this address is not fetched: {problem}")
    fresh, cached_on = False, (robots_checked_at or "")[:10] or None
    if robots_allowed is not None and robots_checked_at:
        try:
            fresh = now - datetime.fromisoformat(robots_checked_at.replace("Z", "+00:00")) < ROBOTS_TTL
        except ValueError:
            fresh = False
    checked, stale_note, from_cache, checked_on = False, "", False, now.strftime("%Y-%m-%d")
    if fresh:
        ok, note, from_cache, checked_on = robots_allowed, "robots decision cached", True, cached_on
    else:
        ok, note = _robots_decision(http, url, allowed, max_redirects)
        checked = ok is not None            # an unreachable robots.txt must not overwrite the last good decision
        if ok is None and robots_allowed is True and cached_on:
            # robots.txt could not be read now, but an earlier read said "allowed". RFC 9309 lets a crawler keep using its last copy while the
            # file is unreachable. Fail closed only when there is no cached success at all (below). A cached "disallowed" is never overridden.
            ok, from_cache, checked_on = True, True, cached_on
            stale_note = f"robots.txt was unreachable ({note}); used the copy checked on {cached_on}"
    if ok is None:
        return FeedResult("robots_unavailable", message=note, robots_allowed=None, robots_checked=False)
    if ok is False:
        return FeedResult("blocked_by_robots", message="robots.txt does not allow this feed for our User-Agent", robots_allowed=False, robots_checked=checked,
                          robots_checked_on=checked_on, robots_from_cache=from_cache)
    robots_note = f"robots.txt {'cached, checked on' if from_cache else 'read on'} {checked_on}" if not stale_note else ""

    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    current, hops, body, new_etag, new_modified = url, 0, None, None, None
    try:
        while True:
            with http.stream("GET", current, headers=headers, timeout=TIMEOUT_S, follow_redirects=False) as r:
                if r.status_code in _REDIRECTS:
                    current = _next_hop(current, r, hops, allowed, max_redirects)
                    hops += 1
                    continue
                if r.status_code == 304:
                    return FeedResult("not_modified", etag=etag, last_modified=last_modified, message="feed has not changed",
                                      robots_allowed=True, robots_checked=checked, robots_checked_on=checked_on, robots_from_cache=from_cache)
                if r.status_code != 200:
                    return FeedResult("network_error", message=f"feed answered HTTP {r.status_code}", robots_allowed=True, robots_checked=checked, robots_checked_on=checked_on, robots_from_cache=from_cache)
                got = _read_body(r, max_bytes)
                if isinstance(got, FeedResult):
                    got.robots_checked = checked
                    return got
                body, new_etag, new_modified = got, r.headers.get("etag"), r.headers.get("last-modified")
                break
    except _Refused as e:
        return FeedResult(e.status, message=e.message, robots_allowed=True, robots_checked=checked, robots_checked_on=checked_on, robots_from_cache=from_cache)
    except httpx.HTTPError as e:
        return FeedResult("network_error", message=f"could not reach the feed ({type(e).__name__})", robots_allowed=True, robots_checked=checked, robots_checked_on=checked_on, robots_from_cache=from_cache)
    parsed = feedparser.parse(body)
    if not parsed.version:  # feedparser could not recognise RSS or Atom (an HTML page, plain text, ...)
        return FeedResult("bad_feed", message="the response is not a readable feed", robots_allowed=True, robots_checked=checked, robots_checked_on=checked_on, robots_from_cache=from_cache)
    return FeedResult("ok", entries=list(parsed.entries), etag=new_etag, last_modified=new_modified,
                      message=f"{len(parsed.entries)} entries; {stale_note or robots_note}", robots_allowed=True, robots_checked=checked,
                      robots_checked_on=checked_on, robots_from_cache=from_cache)
