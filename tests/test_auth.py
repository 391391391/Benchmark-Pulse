"""Tests for sign-in.

The properties that matter are not "does login work" but the ones that are easy
to get wrong and invisible when you do: that a password is never recoverable
from the stored file, that two accounts with the same password hash differently,
and that a wrong password and an unknown address are indistinguishable.
"""

from __future__ import annotations

import json

import pytest

from benchmark_pulse import auth


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Each test gets its own credentials file."""
    monkeypatch.setattr(auth, "USERS_PATH", tmp_path / "users.json")
    yield


def make(email="a.bhuttani@preferredsquare.com", name="A. Bhuttani",
         password="correct horse battery"):
    return auth.create_user(email, name, password)


# -- storage ----------------------------------------------------------------

def test_the_password_never_appears_in_the_stored_file():
    """The single most important property here."""
    make(password="a very memorable phrase")
    stored = auth.USERS_PATH.read_text(encoding="utf-8")
    assert "a very memorable phrase" not in stored
    assert "memorable" not in stored


def test_same_password_produces_different_hashes_for_different_accounts():
    """Without a per-account salt, identical passwords hash identically and one
    cracked password reveals every account that shares it."""
    make("one@example.com", "One", "the same passphrase")
    make("two@example.com", "Two", "the same passphrase")
    users = auth.load_users()
    a, b = users["one@example.com"], users["two@example.com"]
    assert a.salt != b.salt
    assert a.password_hash != b.password_hash


def test_stored_record_has_only_expected_fields():
    make()
    record = json.loads(auth.USERS_PATH.read_text(encoding="utf-8"))[0]
    assert set(record) == {"email", "name", "salt", "password_hash", "role",
                           "created_at", "last_seen"}


# -- authentication ---------------------------------------------------------

def test_correct_password_authenticates():
    user = make()
    assert auth.authenticate(user.email, "correct horse battery").email == user.email


def test_wrong_password_is_rejected():
    user = make()
    with pytest.raises(auth.AuthError):
        auth.authenticate(user.email, "correct horse batteru")


def test_unknown_address_and_wrong_password_give_the_same_message():
    """Otherwise the response reveals which addresses have accounts."""
    make("known@example.com", "Known", "correct horse battery")

    with pytest.raises(auth.AuthError) as wrong:
        auth.authenticate("known@example.com", "not the password")
    with pytest.raises(auth.AuthError) as unknown:
        auth.authenticate("nobody@example.com", "not the password")

    assert str(wrong.value) == str(unknown.value)


def test_email_matching_ignores_case_and_padding():
    make("A.Bhuttani@PreferredSquare.com", "A. Bhuttani", "correct horse battery")
    assert auth.authenticate("  a.bhuttani@preferredsquare.com  ",
                             "correct horse battery")


# -- account rules ----------------------------------------------------------

def test_first_account_becomes_admin_and_later_ones_do_not():
    first = make("first@example.com", "First", "correct horse battery")
    second = make("second@example.com", "Second", "correct horse battery")
    assert first.role == "admin"
    assert second.role == "analyst"


def test_duplicate_address_is_refused():
    make("dup@example.com", "Dup", "correct horse battery")
    with pytest.raises(auth.AuthError, match="already exists"):
        make("dup@example.com", "Dup Again", "another good passphrase")


@pytest.mark.parametrize("bad", ["short", "123456789", ""])
def test_short_passwords_are_refused(bad):
    with pytest.raises(auth.AuthError, match="at least"):
        make(password=bad)


@pytest.mark.parametrize("bad", ["notanemail", "no@domain", "@example.com", ""])
def test_malformed_addresses_are_refused(bad):
    with pytest.raises(auth.AuthError, match="email address"):
        auth.create_user(bad, "Someone", "correct horse battery")


def test_a_name_is_required_because_it_appears_on_sign_offs():
    with pytest.raises(auth.AuthError, match="name is required"):
        auth.create_user("x@example.com", "   ", "correct horse battery")


# -- password change --------------------------------------------------------

def test_changing_a_password_rotates_the_salt():
    """Reusing the salt would leak that the password changed but the hash
    material did not move."""
    user = make()
    before = auth.load_users()[user.email].salt
    auth.change_password(user.email, "correct horse battery", "a brand new phrase")
    after = auth.load_users()[user.email].salt
    assert before != after
    assert auth.authenticate(user.email, "a brand new phrase")


def test_changing_a_password_requires_the_current_one():
    user = make()
    with pytest.raises(auth.AuthError):
        auth.change_password(user.email, "wrong current", "a brand new phrase")


def test_old_password_stops_working_after_a_change():
    user = make()
    auth.change_password(user.email, "correct horse battery", "a brand new phrase")
    with pytest.raises(auth.AuthError):
        auth.authenticate(user.email, "correct horse battery")


# -- display ----------------------------------------------------------------

def test_initials_are_derived_for_the_avatar():
    assert make(name="Aditya Bhuttani").initials == "AB"
    assert make("x@example.com", "A. Bhuttani", "correct horse battery"
                ).initials == "AB"


def test_public_view_excludes_credential_material():
    user = make()
    assert set(user.public()) == {"email", "name", "role"}
