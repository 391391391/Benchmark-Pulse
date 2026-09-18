"""Tests for staying signed in across a refresh.

The properties that matter: a session survives a reload, signing out actually
revokes it rather than just forgetting it, and a token that has been revoked or
has expired cannot be replayed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from benchmark_pulse import auth


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "USERS_PATH", tmp_path / "users.json")
    monkeypatch.setattr(auth, "SESSIONS_PATH", tmp_path / "sessions.json")
    yield


@pytest.fixture
def user():
    return auth.create_user("a.bhuttani@preferredsquare.com", "A. Bhuttani",
                            "correct horse battery")


# -- persistence ------------------------------------------------------------

def test_a_session_survives_and_returns_the_same_user(user):
    """The behaviour being asked for: refresh, stay signed in."""
    token = auth.start_session(user)
    resumed = auth.resume_session(token)
    assert resumed is not None
    assert resumed.email == user.email


def test_resuming_twice_keeps_working(user):
    token = auth.start_session(user)
    assert auth.resume_session(token)
    assert auth.resume_session(token)


def test_each_sign_in_gets_a_distinct_token(user):
    a = auth.start_session(user)
    b = auth.start_session(user)
    assert a != b
    assert auth.resume_session(a) and auth.resume_session(b)


# -- revocation -------------------------------------------------------------

def test_signing_out_revokes_the_token(user):
    """Forgetting the token in one tab is not enough: the copy in the URL must
    stop working."""
    token = auth.start_session(user)
    auth.end_session(token)
    assert auth.resume_session(token) is None


def test_signing_out_one_session_leaves_the_others(user):
    a = auth.start_session(user)
    b = auth.start_session(user)
    auth.end_session(a)
    assert auth.resume_session(a) is None
    assert auth.resume_session(b) is not None


def test_ending_all_sessions_revokes_every_one(user):
    tokens = [auth.start_session(user) for _ in range(3)]
    assert auth.end_all_sessions(user.email) == 3
    assert all(auth.resume_session(t) is None for t in tokens)


def test_changing_a_password_revokes_existing_sessions(user):
    """Otherwise a browser you wanted locked out stays signed in."""
    token = auth.start_session(user)
    auth.change_password(user.email, "correct horse battery", "a brand new phrase")
    assert auth.resume_session(token) is None


def test_deleting_the_account_invalidates_its_sessions(user):
    token = auth.start_session(user)
    auth.USERS_PATH.write_text("[]", encoding="utf-8")
    assert auth.resume_session(token) is None


# -- expiry -----------------------------------------------------------------

def test_an_expired_session_is_refused(user):
    token = auth.start_session(user, hours=-1)
    assert auth.resume_session(token) is None


def test_expiry_rolls_forward_on_use(user):
    """An analyst working through the afternoon should not be interrupted."""
    token = auth.start_session(user, hours=1)
    stored = json.loads(auth.SESSIONS_PATH.read_text(encoding="utf-8"))
    before = next(iter(stored.values()))["expires"]

    auth.resume_session(token, hours=12)

    stored = json.loads(auth.SESSIONS_PATH.read_text(encoding="utf-8"))
    after = next(iter(stored.values()))["expires"]
    assert datetime.fromisoformat(after) > datetime.fromisoformat(before)


def test_expired_records_are_cleared_from_the_store(user):
    auth.start_session(user, hours=-1)
    auth.start_session(user, hours=12)
    auth.resume_session("anything")           # triggers a purge
    stored = json.loads(auth.SESSIONS_PATH.read_text(encoding="utf-8"))
    assert len(stored) == 1


# -- the token itself -------------------------------------------------------

def test_the_token_is_not_stored_in_recoverable_form(user):
    """A leaked sessions file must not yield usable tokens, for the same reason
    a leaked password file must not yield passwords."""
    token = auth.start_session(user)
    stored = auth.SESSIONS_PATH.read_text(encoding="utf-8")
    assert token not in stored


def test_the_token_carries_no_identity(user):
    """It is an opaque random string, so seeing it reveals nothing about who is
    signed in -- which matters when it travels in a visible URL."""
    token = auth.start_session(user)
    assert "preferredsquare" not in token
    assert "bhuttani" not in token.lower()
    assert len(token) >= 32


@pytest.mark.parametrize("bad", [None, "", "not-a-real-token", "../../etc"])
def test_rubbish_tokens_are_refused_without_raising(bad):
    assert auth.resume_session(bad) is None


def test_resuming_with_no_session_file_does_not_raise():
    assert auth.resume_session("anything") is None
