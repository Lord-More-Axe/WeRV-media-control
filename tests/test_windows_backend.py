"""The Windows backend, run against a faithful mock of the WinRT surface.

Every method name and argument shape asserted here was taken from the real package's
generated type stubs. A typo in a WinRT call would otherwise only surface on a user's
machine, as a control that silently does nothing.
"""

from __future__ import annotations

import datetime

import pytest

from werv.media import Repeat, Status
from werv.windows import TICKS_PER_SECOND, friendly_name


def test_reads_the_current_track(backend):
    state = backend.poll()
    assert state.status is Status.PLAYING
    assert state.track.title == "Nightdrive"
    assert state.track.artist == "Aurora Field"
    assert (state.track.track_number, state.track.track_count) == (3, 11)
    assert state.session_id == "Spotify.exe"


def test_timeline_is_extrapolatable(backend):
    timeline = backend.poll().timeline
    assert timeline.duration == pytest.approx(214.0)
    assert timeline.position == pytest.approx(61.0)
    # The client adds `age` to the reported position, so it must reflect the real delay.
    assert 2.0 < timeline.age < 4.0
    assert timeline.rate == 1.0
    assert timeline.seekable


def test_capabilities_follow_the_app(backend):
    rich = backend.poll().capabilities
    assert rich.seek and rich.shuffle and rich.repeat and rich.next

    backend.select_session("chrome.exe")
    plain = backend.poll().capabilities
    # A browser tab offers play/pause and nothing else; the interface dims the rest.
    assert plain.toggle
    assert not plain.seek and not plain.next and not plain.shuffle


def test_unknown_shuffle_and_repeat_stay_unknown(backend):
    """None means 'this app does not say', which is not the same as off."""
    backend.select_session("chrome.exe")
    state = backend.poll()
    assert state.shuffle is None
    assert state.repeat is None


def test_shuffle_and_repeat_are_read(backend):
    state = backend.poll()
    assert state.shuffle is True
    assert state.repeat is Repeat.ALL


def test_seek_sends_hundred_nanosecond_ticks(backend, winrt):
    winrt.CALLS.clear()
    assert backend.seek(90.0)
    action, args = winrt.CALLS[-1][1], winrt.CALLS[-1][2]
    assert action == "seek"
    assert args == (int(90.0 * TICKS_PER_SECOND),)


def test_seek_is_clamped_to_the_seekable_window(backend, smtc, winrt):
    _, player, _ = smtc
    player._timeline = winrt.TimelineProperties(
        0, 300, 10, datetime.datetime.now(datetime.timezone.utc), min_seek=30, max_seek=200)
    winrt.CALLS.clear()
    backend.seek(999.0)
    assert winrt.CALLS[-1][2] == (200 * TICKS_PER_SECOND,)


def test_transport_targets_the_session_not_the_keyboard(backend, winrt):
    winrt.CALLS.clear()
    backend.toggle(); backend.next(); backend.previous()
    assert [(app, name) for app, name, _ in winrt.CALLS] == [
        ("Spotify.exe", "toggle"), ("Spotify.exe", "next"), ("Spotify.exe", "previous")]


def test_repeat_is_sent_as_the_windows_enum(backend, winrt):
    winrt.CALLS.clear()
    backend.set_repeat(Repeat.ONE)
    assert int(winrt.CALLS[-1][2][0]) == 1          # MediaPlaybackAutoRepeatMode.TRACK


def test_artwork_reads_the_returned_buffer(backend, cover_bytes):
    """WinRT may hand back a different buffer than the one passed in."""
    assert backend.artwork(backend.poll().track) == cover_bytes


def test_events_notify_the_hub(backend, smtc):
    _, player, _ = smtc
    woken = []
    backend.on_change = lambda: woken.append(1)
    player.handlers["playback"](player, None)       # handlers take (sender, args)
    player.handlers["media"](player, None)
    assert len(woken) == 2


def test_switching_source(backend):
    backend.select_session("chrome.exe")
    assert backend.poll().session_id == "chrome.exe"
    backend.select_session(None)
    assert backend.poll().session_id == "Spotify.exe"


def test_sessions_are_listed_with_readable_names(backend):
    sessions = backend.poll().sessions
    assert [s.name for s in sessions] == ["Spotify", "Chrome"]
    assert [s.id for s in sessions if s.current] == ["Spotify.exe"]


def test_nothing_playing_is_idle_not_an_error(backend, smtc):
    manager, _, _ = smtc
    manager._sessions, manager._current = [], None
    backend.select_session(None)
    state = backend.poll()
    assert state.status is Status.IDLE
    assert state.sessions == ()


def test_enumeration_failing_does_not_lose_the_bound_session(backend, smtc):
    """Losing the session *list* is not losing the session we are already driving."""
    manager, _, _ = smtc

    def boom(*_args):
        raise OSError("RPC server unavailable")

    manager.get_sessions = boom
    manager.get_current_session = boom
    state = backend.poll()
    assert state.status is Status.PLAYING
    assert state.sessions == ()          # the list is simply unavailable this time


def test_a_failing_windows_never_raises(backend, smtc):
    """Any COM error must become a state the phone can render, never a 500."""
    _, player, _ = smtc

    def boom(*_args):
        raise OSError("RPC server unavailable")

    player.get_playback_info = boom
    player.get_timeline_properties = boom
    state = backend.poll()
    assert state.status is Status.UNAVAILABLE
    assert state.message


def test_commands_decline_rather_than_raise(backend, smtc):
    _, player, _ = smtc

    def boom(*_args):
        raise OSError("session went away")

    player.try_skip_next_async = boom
    assert backend.next() is False


def test_the_1601_sentinel_is_not_extrapolated(backend):
    """A player that never reported a timeline leaves a zero timestamp."""
    backend.select_session("chrome.exe")
    timeline = backend.poll().timeline
    assert timeline.age == 0.0
    assert not timeline.seekable


@pytest.mark.parametrize("app_id,expected", [
    ("Spotify.exe", "Spotify"),
    ("chrome.exe", "Chrome"),
    ("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic", "Media Player"),
    ("MusicBee.exe", "MusicBee"),
    ("", "Unknown app"),
])
def test_friendly_names(app_id, expected):
    assert friendly_name(app_id) == expected
