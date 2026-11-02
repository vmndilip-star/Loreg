"""The domain allowlist for Loreg Watch (config/news_sources.yaml). Pure code, no network.

An address is allowed only if it is https, has no user name or password, uses the default port, and its host name is
exactly one of the listed hosts. Subdomains, look-alikes and IP addresses are all refused.
"""

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "news_sources.yaml"


@dataclass(frozen=True)
class NewsConfig:
    allowed_hosts: frozenset
    sources: tuple
    max_feed_bytes: int = 2 * 1024 * 1024
    max_redirects: int = 3
    timeout_s: float = 10.0
    excerpt_chars: int = 280
    body_keep_days: int = 7
    scan_deadline_s: float = 60.0
    extra: dict = field(default_factory=dict)


def check_url(url: str, allowed_hosts) -> str:
    """"" if this address may be fetched or stored; otherwise a short plain reason."""
    try:
        parts = urlsplit((url or "").strip())
        host, port = parts.hostname, parts.port
    except ValueError:
        return "the address cannot be read"
    if parts.scheme != "https":
        return "only https addresses are allowed"
    if parts.username is not None or parts.password is not None:
        return "an address with a user name is not allowed"
    if port not in (None, 443):
        return "only the default https port is allowed"
    if not host or not host.isascii():
        return "the host name is not allowed"
    host = host.rstrip(".")
    if host not in allowed_hosts:
        return f"{host} is not on the list of allowed news sites"
    return ""


def load_news_config(path=None) -> NewsConfig:
    raw = yaml.safe_load(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    allowed = frozenset(h.strip().lower().rstrip(".") for h in raw["allowed_hosts"])
    lim = raw.get("limits", {})
    cfg = NewsConfig(allowed_hosts=allowed, sources=tuple(raw["sources"]), max_feed_bytes=int(lim.get("max_feed_bytes", 2 * 1024 * 1024)),
                     max_redirects=int(lim.get("max_redirects", 3)), timeout_s=float(lim.get("timeout_s", 10)),
                     excerpt_chars=int(lim.get("excerpt_chars", 280)), body_keep_days=int(lim.get("body_keep_days", 7)),
                     scan_deadline_s=float(lim.get("scan_deadline_s", 60)))
    for s in cfg.sources:
        problem = check_url(s["url"], allowed)
        if problem:
            raise ValueError(f"source {s['name']!r} is not allowed: {problem}")
    return cfg
