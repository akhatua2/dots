"""Tests for account identity (`dots/util.py` account helpers and
the derive_account_id KDF).

We point DOTS_HOME at a tmp_path for each test so no test ever writes to
the developer's real ~/.dots/ directory.
"""

from __future__ import annotations

import pytest

from dots.system.util import (
    account_id_short,
    derive_account_id,
    dots_home,
    load_account_alias,
    load_account_email,
    load_account_name,
    load_or_create_account,
    save_account_alias,
    save_account_email,
    save_account_id,
    save_account_name,
)


@pytest.fixture(autouse=True)
def _isolated_dots_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTS_HOME", str(tmp_path))
    yield tmp_path


def test_dots_home_respects_env(tmp_path):
    assert dots_home() == tmp_path


def test_load_or_create_account_creates_then_persists(tmp_path):
    a1 = load_or_create_account()
    assert a1.startswith("acc-")
    # File should now exist and contain the same id.
    assert (tmp_path / "account").read_text().strip() == a1
    # Subsequent call returns the same id.
    a2 = load_or_create_account()
    assert a2 == a1


def test_save_account_id_overrides_existing():
    load_or_create_account()
    save_account_id("acc-explicit-override")
    assert load_or_create_account() == "acc-explicit-override"


def test_alias_name_email_round_trip():
    save_account_alias("arpan")
    save_account_name("Arpan Khatua")
    save_account_email("arpan@stanford.edu")
    assert load_account_alias() == "arpan"
    assert load_account_name() == "Arpan Khatua"
    assert load_account_email() == "arpan@stanford.edu"


def test_unset_fields_return_none():
    assert load_account_alias() is None
    assert load_account_name() is None
    assert load_account_email() is None


def test_account_id_short_strips_prefix():
    assert account_id_short("acc-abcdef1234567890") == "abcdef12"
    assert account_id_short("acc-") == "?"
    assert account_id_short("noprefix") == "noprefix"


def test_derive_account_id_is_deterministic():
    a = derive_account_id("Arpan Khatua", "arpan@stanford.edu", "pw123")
    b = derive_account_id("Arpan Khatua", "arpan@stanford.edu", "pw123")
    assert a == b
    assert a.startswith("acc-")


def test_derive_account_id_normalizes_case_and_whitespace():
    a = derive_account_id("Arpan Khatua", "Arpan@Stanford.edu", "pw123")
    b = derive_account_id("  arpan khatua  ", "arpan@stanford.edu", "pw123")
    assert a == b


def test_derive_account_id_password_is_case_sensitive():
    """Password is intentionally NOT case-normalized — that would weaken
    the entropy of the input."""
    a = derive_account_id("name", "email", "Password")
    b = derive_account_id("name", "email", "password")
    assert a != b


def test_derive_account_id_changes_when_any_input_changes():
    base = derive_account_id("a", "b", "c")
    assert derive_account_id("aa", "b", "c") != base
    assert derive_account_id("a", "bb", "c") != base
    assert derive_account_id("a", "b", "cc") != base
