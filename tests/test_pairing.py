"""The pairing key, and the promise that scanning once is enough."""

from __future__ import annotations

import os
import stat

import pytest

from werv import pairing


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_the_same_key_comes_back_on_the_next_run(config_home):
    """Otherwise every phone in the house is bounced to the pairing screen on reboot."""
    first, path = pairing.load_or_create()
    assert path is not None and path.exists()
    second, again = pairing.load_or_create()
    assert second == first
    assert again == path


def test_rotating_issues_a_different_key(config_home):
    first, _ = pairing.load_or_create()
    rotated, _ = pairing.load_or_create(rotate=True)
    assert rotated != first
    assert pairing.load_or_create()[0] == rotated      # and the new one sticks


def test_forgetting_unpairs_everything(config_home):
    first, _ = pairing.load_or_create()
    assert pairing.forget()
    assert pairing.load_or_create()[0] != first
    assert pairing.forget()                             # already gone is not a failure


def test_the_key_is_not_readable_by_other_users(config_home):
    _, path = pairing.load_or_create()
    if os.name == "nt":
        pytest.skip("POSIX permission bits do not apply")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_the_key_is_long_enough_to_be_worth_having(config_home):
    key, _ = pairing.load_or_create()
    assert len(key) >= 12
    assert key.replace("-", "").replace("_", "").isalnum()


@pytest.mark.parametrize("junk", ["", "   ", "short", "has spaces in it", "!!!!!!!!!!"])
def test_a_damaged_key_file_is_replaced_not_trusted(config_home, junk):
    """A truncated write or a hand-edited file must not become a weak key."""
    _, path = pairing.load_or_create()
    path.write_text(junk, encoding="ascii")
    replacement, _ = pairing.load_or_create()
    assert replacement.strip() != junk.strip()
    assert len(replacement) >= 12


def test_an_unwritable_location_still_yields_a_working_key(config_home, monkeypatch):
    """It just does not survive a restart, which the banner says out loud."""
    monkeypatch.setattr(pairing, "_write", lambda path, key: False)
    key, path = pairing.load_or_create()
    assert key and path is None


def test_the_key_lives_somewhere_sensible(config_home):
    path = pairing.key_path()
    assert path.name == "pairing-key"
    assert "werv" in str(path).lower()
