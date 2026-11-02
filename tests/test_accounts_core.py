"""Accounts, the pure parts: password hashing, what signup and sign-in accept, the in-memory limits and the auth config. No app, no web."""

import re

import pytest
from pydantic import ValidationError

from app.auth.config import load_auth_config
from app.core import passwords
from app.core.accounts import LIMITS, LoginBody, SignupBody
from app.core.passwords import DUMMY_HASH, hash_password, verify_password
from app.core.throttle import Backoff, Window

GOOD = {"email": "owner@example.com", "password": "a long enough pass", "org_name": "Example Electronics", "street": "100 Example Street",
        "city": "Springfield", "state": "ST", "zip": "00000", "country": "US"}


# ------------------------------------------------------------------------------ passwords
def test_a_hash_carries_its_parameters_and_a_salt_of_its_own():
    a, b = hash_password("same password here"), hash_password("same password here")
    scheme, n, r, p, salt, key = a.split("$")
    assert (scheme, int(n), int(r), int(p)) == ("scrypt", 2 ** 14, 8, 1) and a != b and salt != b.split("$")[4]
    assert verify_password("same password here", a) and verify_password("same password here", b)
    assert not verify_password("same password herE", a) and not verify_password("", a)


def test_the_parameters_are_read_back_from_the_stored_hash():
    salt = b"0123456789abcdef"
    stronger = f"scrypt${2 ** 15}$8$2${passwords._b64(salt)}${passwords._b64(passwords._derive('pw that is long', salt, 2 ** 15, 8, 2, 32))}"
    assert verify_password("pw that is long", stronger)


@pytest.mark.parametrize("stored", [None, "", "plain", "bcrypt$1$2$3$4$5", "scrypt$1024$8$1$c2FsdA$a2V5", "scrypt$x$8$1$a$b",
                                    f"scrypt${2 ** 14}$4$1$c2FsdA$a2V5"])
def test_a_missing_malformed_or_weaker_hash_never_verifies(stored):
    assert verify_password("anything at all", stored) is False


def test_verification_is_constant_time_and_a_dummy_hash_exists_for_unknown_emails():
    source = open(passwords.__file__, encoding="utf-8").read()
    assert "hmac.compare_digest" in source and "==" not in source.split("def verify_password")[1].split("DUMMY_HASH")[0]
    assert DUMMY_HASH.startswith("scrypt$16384$8$1$") and not verify_password("", DUMMY_HASH)


# ------------------------------------------------------------------------------ signup and sign-in input
def test_fields_are_trimmed_except_the_password_and_the_email_is_lower_case():
    body = SignupBody(**{**GOOD, "email": "  Owner@Example.COM ", "org_name": "  Example Electronics  ", "password": "  spaces count  "})
    assert body.email == "owner@example.com" and body.org_name == "Example Electronics" and body.password == "  spaces count  "
    assert "password" not in body.profile() and body.profile()["email"] == "owner@example.com"


@pytest.mark.parametrize("field", ["email", "org_name", "street", "city", "state", "zip", "country"])
def test_every_text_field_is_length_limited(field):
    lo, hi = LIMITS[field]
    too_long = ("a" * (hi - 11) + "@example.com") if field == "email" else ("A" * (hi + 1))
    with pytest.raises(ValidationError):
        SignupBody(**{**GOOD, field: too_long})
    if lo > 1:
        with pytest.raises(ValidationError):
            SignupBody(**{**GOOD, field: "A" * (lo - 1)})
    with pytest.raises(ValidationError):
        SignupBody(**{**GOOD, field: "   "})


@pytest.mark.parametrize("pw, ok", [("a" * 11, False), ("a" * 12, True), ("a" * 128, True), ("a" * 129, False), ("tab\there and more", False)])
def test_the_password_is_12_to_128_characters_and_has_no_control_characters(pw, ok):
    if ok:
        assert SignupBody(**{**GOOD, "password": pw}).password == pw
    else:
        with pytest.raises(ValidationError):
            SignupBody(**{**GOOD, "password": pw})


@pytest.mark.parametrize("field", ["org_name", "street", "city", "state", "country", "email"])
@pytest.mark.parametrize("bad", ["<b>Shop</b>", "Shop\x00", "Shop\nName", "a>b"])
def test_markup_and_control_characters_are_refused(field, bad):
    with pytest.raises(ValidationError):
        SignupBody(**{**GOOD, field: bad})


@pytest.mark.parametrize("email", ["no-at-sign", "a@b", "a@@example.com", "a b@example.com", "@example.com", "a@example."])
def test_an_email_must_look_like_one(email):
    with pytest.raises(ValidationError):
        SignupBody(**{**GOOD, "email": email})


@pytest.mark.parametrize("zip_code, ok", [("00000", True), ("SW1A 1AA", True), ("12345-6789", True), ("12", False), ("1234567890123", False),
                                          ("12#45", False), ("-1234", False)])
def test_the_zip_code(zip_code, ok):
    if ok:
        assert SignupBody(**{**GOOD, "zip": zip_code}).zip == zip_code
    else:
        with pytest.raises(ValidationError):
            SignupBody(**{**GOOD, "zip": zip_code})


@pytest.mark.parametrize("extra", ["tax_id", "ein", "phone", "bank_account", "routing_number", "is_admin", "shop_id"])
def test_fields_that_are_not_asked_for_are_refused(extra):
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SignupBody(**{**GOOD, extra: "123"})
    with pytest.raises(ValidationError):
        LoginBody(email="a@example.com", password="x", **{extra: "1"})


def test_sign_in_input_is_trimmed_and_lower_cased_but_not_checked_for_shape():
    assert LoginBody(email=" Owner@Example.com ", password=" x ").model_dump() == {"email": "owner@example.com", "password": " x "}
    with pytest.raises(ValidationError):
        LoginBody(email="", password="x")


# ------------------------------------------------------------------------------ limits
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_the_signup_window_allows_five_per_address_then_refuses_until_the_window_has_passed():
    clock = Clock()
    w = Window(5, 900, clock)
    assert [w.allow("ip:1") for _ in range(6)] == [True] * 5 + [False]
    assert w.allow("ip:2")                                                     # another address is counted on its own
    clock.t += 899
    assert not w.allow("ip:1")
    clock.t += 1
    assert w.allow("ip:1")


def test_sign_in_failures_back_off_doubling_up_to_the_cap_and_a_success_clears_them():
    clock = Clock()
    b = Backoff(free=5, base_s=30, max_s=900, window_s=900, clock=clock)
    for _ in range(4):
        b.fail("email:a")
    assert b.wait_s("email:a") == 0
    b.fail("email:a")
    assert b.wait_s("email:a") == 30
    clock.t += 10
    assert b.wait_s("email:a") == 20
    b.fail("email:a")
    assert b.wait_s("email:a") == 60
    for _ in range(10):
        b.fail("email:a")
    assert b.wait_s("email:a") == 900
    b.clear("email:a")
    assert b.wait_s("email:a") == 0


def test_old_failures_are_forgotten_after_the_window():
    clock = Clock()
    b = Backoff(free=5, base_s=30, max_s=900, window_s=900, clock=clock)
    for _ in range(6):
        b.fail("ip:1")
    clock.t += 900
    assert b.wait_s("ip:1") == 0


def test_the_auth_config_file():
    cfg = load_auth_config()
    assert (cfg.session_hours, cfg.session_cookie, cfg.csrf_cookie) == (168, "loreg_session", "loreg_csrf")
    assert (cfg.signup_max_per_ip, cfg.signup_window_s, cfg.login_free_failures, cfg.login_base_wait_s, cfg.login_max_wait_s) == (5, 900, 5, 30, 900)
    assert re.fullmatch(r"[a-z_]+", cfg.session_cookie)
