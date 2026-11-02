"""What a person may send to create an account or sign in. Pure validation, no database.

Every field is plain text with a length limit; every field except the password is trimmed. Control characters and the characters < and >
are refused in every field that is ever shown. Fields that are not listed (a tax ID, a phone number, bank details or anything else) are
refused, not ignored. The email address is stored in lower case.
"""

import re

from pydantic import BaseModel, ConfigDict, field_validator

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MARKUP = re.compile(r"[<>]")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s.]{2,}$")
_ZIP = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 -]*[A-Za-z0-9]$")

LIMITS = {"email": (3, 254), "password": (12, 128), "org_name": (1, 100), "street": (1, 200), "city": (1, 100), "state": (1, 100),
          "zip": (3, 12), "country": (1, 100)}
LABELS = {"email": "Email", "password": "Password", "org_name": "Organization name", "street": "Street", "city": "City", "state": "State",
          "zip": "ZIP code", "country": "Country"}


def _plain(value: str, field: str) -> str:
    v = value.strip()
    lo, hi = LIMITS[field]
    if _CONTROL.search(v) or _MARKUP.search(v):
        raise ValueError(f"{LABELS[field]} must be plain text: no control characters, < or >")
    if not lo <= len(v) <= hi:
        raise ValueError(f"{LABELS[field]} must be {lo} to {hi} characters")
    return v


def _email(value: str) -> str:
    v = _plain(value, "email").lower()
    if not _EMAIL.match(v):
        raise ValueError("Email must look like name@example.com")
    return v


def _password(value: str) -> str:
    """Not trimmed: a space at either end is part of the password. Only control characters are refused."""
    lo, hi = LIMITS["password"]
    if _CONTROL.search(value):
        raise ValueError("Password must not contain control characters")
    if not lo <= len(value) <= hi:
        raise ValueError(f"Password must be {lo} to {hi} characters")
    return value


class SignupBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str
    org_name: str
    street: str
    city: str
    state: str
    zip: str
    country: str

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        return _email(v)

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return _password(v)

    @field_validator("org_name", "street", "city", "state", "country")
    @classmethod
    def _text(cls, v: str, info) -> str:
        return _plain(v, info.field_name)

    @field_validator("zip")
    @classmethod
    def _zip(cls, v: str) -> str:
        v = _plain(v, "zip")
        if not _ZIP.match(v):
            raise ValueError("ZIP code may only hold letters, digits, spaces and hyphens")
        return v

    def profile(self) -> dict:
        """Everything but the password."""
        return self.model_dump(exclude={"password"})


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not 1 <= len(v) <= LIMITS["email"][1]:
            raise ValueError("Email is required")
        return v

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        if not 1 <= len(v) <= LIMITS["password"][1]:
            raise ValueError("Password is required")
        return v
