"""Load config/auth.yaml: session length, cookie names and the signup and sign-in limits. And read LOREG_COOKIE_SECURE."""

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "auth.yaml"


@dataclass(frozen=True)
class AuthConfig:
    session_hours: float
    session_cookie: str
    csrf_cookie: str
    signup_max_per_ip: int
    signup_window_s: float
    login_free_failures: int
    login_base_wait_s: float
    login_max_wait_s: float
    login_window_s: float


def load_auth_config(path=None) -> AuthConfig:
    raw = yaml.safe_load(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    cookies, signup, login = raw["cookies"], raw["signup_limit"], raw["login_backoff"]
    cfg = AuthConfig(session_hours=float(raw["session_hours"]), session_cookie=str(cookies["session"]), csrf_cookie=str(cookies["csrf"]),
                     signup_max_per_ip=int(signup["max_per_ip"]), signup_window_s=float(signup["window_s"]),
                     login_free_failures=int(login["free_failures"]), login_base_wait_s=float(login["base_wait_s"]),
                     login_max_wait_s=float(login["max_wait_s"]), login_window_s=float(login["window_s"]))
    if cfg.session_hours <= 0 or cfg.signup_max_per_ip < 1 or cfg.login_free_failures < 1:
        raise ValueError("config/auth.yaml: session_hours, max_per_ip and free_failures must be positive")
    return cfg


def cookie_secure(value: str | None, production: bool) -> bool | None:
    """LOREG_COOKIE_SECURE: "1" marks the cookies Secure always (behind a proxy that ends https, the app itself sees plain http), "0"
    never (a plain-http test), unset or empty: Secure when the app sees https, and always under LOREG_ENV=production. None means
    "when the app sees https". Any other value stops the app at start."""
    if value in (None, ""):
        return True if production else None
    if value not in ("0", "1"):
        raise ValueError("LOREG_COOKIE_SECURE must be 1, 0 or unset")
    return value == "1"
