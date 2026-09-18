"""Sign-in, so the audit trail records a person rather than a typed name.

This exists less to keep people out than to make the sign-off meaningful. A
benchmark decision approved by whatever someone typed into a box is not an
audit trail; one approved by an authenticated account is.

What this is, precisely, so nobody mistakes it for more:

  Passwords are never stored. Each account keeps a random 16-byte salt and an
  scrypt hash, and verification is a constant-time comparison. scrypt is
  deliberately slow and memory-hard, which is what makes a stolen credentials
  file expensive to attack.

  It is a pilot-grade local gate, not enterprise identity. There is no password
  reset, no multi-factor, no session store, and accounts live in one JSON file
  on the machine running the app. For firmwide use the answer is Microsoft
  Entra ID -- the firm already runs Microsoft identity, and this module is the
  only thing that would be replaced.

  A session survives a page refresh and ends when you sign out or when it
  expires. See the session functions below for how, and for the one tradeoff
  that involves.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone

from .paths import DATA_ROOT

USERS_PATH = DATA_ROOT / "users.json"
SESSIONS_PATH = DATA_ROOT / "sessions.json"

#: How long a signed-in session lasts without activity. Rolling: each page load
#: pushes it out again, so an analyst working through an afternoon is never
#: interrupted, while an abandoned tab stops working overnight.
SESSION_HOURS = 12

#: scrypt parameters. n=2**14 costs roughly a tenth of a second per hash here,
#: which is unnoticeable on sign-in and expensive in bulk -- the asymmetry that
#: makes a password hash worth having.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LENGTH = 64

MIN_PASSWORD_LENGTH = 10

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


class AuthError(Exception):
    """Sign-in or account creation failed, with a reason safe to display."""


@dataclass
class User:
    email: str
    name: str
    salt: str                  # hex
    password_hash: str         # hex
    role: str = "analyst"      # analyst | admin
    created_at: str = ""
    last_seen: str = ""

    @property
    def initials(self) -> str:
        parts = [p for p in re.split(r"[\s.]+", self.name) if p]
        return "".join(p[0] for p in parts[:2]).upper() or self.email[:2].upper()

    def public(self) -> dict:
        """Everything except the credential material."""
        return {"email": self.email, "name": self.name, "role": self.role}


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_KEY_LENGTH,
    ).hex()


def load_users() -> dict[str, User]:
    if not USERS_PATH.exists():
        return {}
    try:
        raw = json.loads(USERS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    users: dict[str, User] = {}
    for record in raw:
        try:
            user = User(**record)
        except TypeError:
            continue           # a record from an older shape; skip it
        users[user.email.lower()] = user
    return users


def _save(users: dict[str, User]) -> None:
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    USERS_PATH.write_text(
        json.dumps([asdict(u) for u in users.values()], indent=2),
        encoding="utf-8",
    )
    # Best effort on Windows, which has no chmod worth the name; the file lives
    # under the user's own AppData, which is already per-user.
    try:
        os.chmod(USERS_PATH, 0o600)
    except OSError:
        pass


def any_accounts() -> bool:
    return bool(load_users())


def validate_password(password: str) -> None:
    """Raise if a password is too weak to accept.

    Length carries far more strength than a character-class rule, so that is
    what is enforced. Requiring a symbol mostly produces "Password1!".
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters. "
            "A short phrase is stronger than a short password."
        )
    if password.lower() in {"password12", "benchmarkpulse", "1234567890",
                            "preferredsquare"}:
        raise AuthError("That password is too easy to guess.")


def create_user(email: str, name: str, password: str,
                role: str = "analyst") -> User:
    """Register an account. The first account created becomes an admin."""
    email = email.strip().lower()
    if not _EMAIL.match(email):
        raise AuthError("That does not look like an email address.")
    if not name.strip():
        raise AuthError("A name is required: it is what appears on a sign-off.")
    validate_password(password)

    users = load_users()
    if email in users:
        raise AuthError("An account already exists for that address.")

    salt = os.urandom(16)
    user = User(
        email=email,
        name=name.strip(),
        salt=salt.hex(),
        password_hash=_hash(password, salt),
        role="admin" if not users else role,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    users[email] = user
    _save(users)
    return user


def authenticate(email: str, password: str) -> User:
    """Verify a sign-in.

    The same message is returned whether the address is unknown or the password
    is wrong, so the response cannot be used to discover which addresses have
    accounts. A dummy hash is computed for an unknown address so the two paths
    take the same time.
    """
    users = load_users()
    user = users.get(email.strip().lower())

    if user is None:
        _hash(password, b"0" * 16)      # equalise timing
        raise AuthError("Email or password is incorrect.")

    candidate = _hash(password, bytes.fromhex(user.salt))
    if not hmac.compare_digest(candidate, user.password_hash):
        raise AuthError("Email or password is incorrect.")

    user.last_seen = datetime.now(timezone.utc).isoformat(timespec="seconds")
    users[user.email] = user
    _save(users)
    return user


# -- sessions ---------------------------------------------------------------
#
# A session is an opaque random token held by the browser and a matching record
# on disk. The token carries no information -- not the email, not a signature --
# so it reveals nothing if seen, and it can be revoked instantly because the
# authority is the server-side record rather than the token itself.
#
# Only the token's hash is stored. Someone who reads sessions.json therefore
# learns which accounts have sessions but cannot use them, in the same way the
# password file is useless without the passwords.
#
# The tradeoff: the token travels in the page URL, because that is the only
# channel Streamlit reads synchronously on the first script run. A cookie is
# tidier, but Streamlit's cookie components report back a render late, which
# means a visible flash and a race between "still loading" and "not signed in".
# For a localhost pilot a token in the address bar is the better trade; for
# firmwide deployment this whole module is replaced by Microsoft Entra ID.


def _load_sessions() -> dict[str, dict]:
    if not SESSIONS_PATH.exists():
        return {}
    try:
        return json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_sessions(sessions: dict[str, dict]) -> None:
    SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS_PATH.write_text(json.dumps(sessions, indent=2), encoding="utf-8")
    try:
        os.chmod(SESSIONS_PATH, 0o600)
    except OSError:
        pass


def _token_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _purge(sessions: dict[str, dict]) -> dict[str, dict]:
    now = datetime.now(timezone.utc)
    kept = {}
    for key, record in sessions.items():
        try:
            if datetime.fromisoformat(record["expires"]) > now:
                kept[key] = record
        except (KeyError, ValueError):
            continue
    return kept


def start_session(user: User, hours: int = SESSION_HOURS) -> str:
    """Open a session and return the token the browser should hold."""
    token = secrets.token_urlsafe(32)
    sessions = _purge(_load_sessions())
    sessions[_token_id(token)] = {
        "email": user.email,
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "expires": (datetime.now(timezone.utc)
                    + timedelta(hours=hours)).isoformat(timespec="seconds"),
    }
    _save_sessions(sessions)
    return token


def resume_session(token: str | None, hours: int = SESSION_HOURS) -> User | None:
    """Return the signed-in user for a token, or None.

    Never raises: an unknown, expired or malformed token is simply not a
    session, and the caller shows the sign-in screen.
    """
    if not token:
        return None

    sessions = _purge(_load_sessions())
    record = sessions.get(_token_id(token))
    if record is None:
        _save_sessions(sessions)
        return None

    user = load_users().get(record.get("email", ""))
    if user is None:          # the account was removed after signing in
        sessions.pop(_token_id(token), None)
        _save_sessions(sessions)
        return None

    # Rolling expiry, so continued use keeps the session alive.
    record["expires"] = (datetime.now(timezone.utc)
                         + timedelta(hours=hours)).isoformat(timespec="seconds")
    sessions[_token_id(token)] = record
    _save_sessions(sessions)
    return user


def end_session(token: str | None) -> None:
    """Revoke a session. Signing out must invalidate the token server-side, or
    the copy in the URL would keep working."""
    if not token:
        return
    sessions = _purge(_load_sessions())
    sessions.pop(_token_id(token), None)
    _save_sessions(sessions)


def end_all_sessions(email: str) -> int:
    """Revoke every session for an account. Returns how many were ended."""
    sessions = _purge(_load_sessions())
    target = email.strip().lower()
    doomed = [k for k, v in sessions.items() if v.get("email") == target]
    for key in doomed:
        sessions.pop(key)
    _save_sessions(sessions)
    return len(doomed)


def change_password(email: str, current: str, new: str) -> User:
    user = authenticate(email, current)
    validate_password(new)
    salt = os.urandom(16)
    user.salt = salt.hex()
    user.password_hash = _hash(new, salt)
    users = load_users()
    users[user.email] = user
    _save(users)
    # Changing a password must invalidate sessions opened with the old one --
    # otherwise a browser someone wanted to lock out stays signed in.
    end_all_sessions(user.email)
    return user
