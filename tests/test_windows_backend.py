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


class TestMediaKeyFallback:
    """Some apps register a session and then ignore commands aimed at it.

    The hardware key is the last resort for those. It cannot be pressed here, but the
    structure handed to SendInput can be checked: Windows rejects the call outright if
    the size is wrong, which would make the fallback silently do nothing.
    """

    def test_input_struct_matches_the_windows_abi(self):
        import ctypes

        from werv.windows import _Input, _InputUnion, _KeyboardInput, _MouseInput

        pointer = ctypes.sizeof(ctypes.c_void_p)
        assert ctypes.sizeof(_KeyboardInput) == (24 if pointer == 8 else 16)
        assert ctypes.sizeof(_MouseInput) == (32 if pointer == 8 else 24)
        assert ctypes.sizeof(_InputUnion) == ctypes.sizeof(_MouseInput)
        assert ctypes.sizeof(_Input) == (40 if pointer == 8 else 28)
        assert _Input.union.offset == pointer

    def test_virtual_key_codes(self):
        from werv.windows import VK_NEXT_TRACK, VK_PLAY_PAUSE, VK_PREV_TRACK

        assert (VK_PLAY_PAUSE, VK_NEXT_TRACK, VK_PREV_TRACK) == (0xB3, 0xB0, 0xB1)

    def test_the_session_is_always_tried_first(self, backend, winrt):
        winrt.CALLS.clear()
        assert backend.next() is True
        assert winrt.CALLS[-1][1] == "next"

    def test_fallback_only_fires_after_the_session_refuses(self, backend, smtc, monkeypatch):
        _, player, _ = smtc
        pressed = []
        monkeypatch.setattr("werv.windows.press_media_key", lambda vk: pressed.append(vk) or True)

        player.try_skip_next_async = lambda: _Refused()
        assert backend.next() is True
        assert pressed == [0xB0]

    def test_fallback_can_be_turned_off(self, smtc, monkeypatch):
        from werv.windows import WindowsBackend

        _, player, _ = smtc
        pressed = []
        monkeypatch.setattr("werv.windows.press_media_key", lambda vk: pressed.append(vk) or True)

        instance = WindowsBackend(media_key_fallback=False)
        instance.start()
        try:
            player.try_skip_next_async = lambda: _Refused()
            assert instance.next() is False
            assert pressed == []
        finally:
            instance.close()


class _Refused:
    """An operation the app declined -- the documented way to say 'I do not do that'."""

    def get(self):
        return False

    def wait(self, _timeout):
        return 1

    def get_results(self):
        return False


def test_a_wedged_player_cannot_wedge_the_server(backend, smtc):
    """.get() blocks forever; the timeout path is what stops one bad app taking us down."""
    _, player, _ = smtc

    class Hung:
        def wait(self, _timeout):
            return 0            # AsyncStatus.Started -- never completed

        def cancel(self):
            pass

    player.try_skip_next_async = lambda: Hung()
    assert backend.next() is False          # declined, not hung, not raised


class TestSurvivingADeadSession:
    """When the bound app quits, its COM proxy does not become None -- it starts raising.

    That is the difference that wedged the backend: a plain getattr default absorbs
    AttributeError but not the OSError a disconnected proxy throws, so the rebind meant
    to recover threw first and the backend stayed bound to a dead object for good.
    """

    def test_quitting_the_bound_app_falls_back_to_what_is_left(self, backend, smtc):
        manager, player, tab = smtc
        assert backend.poll().session_id == "Spotify.exe"

        manager._sessions = [tab]      # Spotify quits: it leaves the list...
        manager._current = tab
        player.dead = True             # ...and the proxy we still hold goes bad.

        state = backend.poll()
        assert state.status is not Status.UNAVAILABLE
        assert state.session_id == "chrome.exe"

    def test_it_does_not_latch_into_unavailable(self, backend, smtc):
        manager, player, tab = smtc
        manager._sessions, manager._current = [], None
        player.dead = True
        assert backend.poll().status is Status.IDLE     # nothing playing, not broken

        manager._sessions, manager._current = [tab], tab
        assert backend.poll().session_id == "chrome.exe"

    def test_a_dead_session_does_not_break_the_source_list(self, backend, smtc):
        manager, player, tab = smtc
        player.dead = True
        names = [s.name for s in backend.poll().sessions]
        assert names == ["Chrome"]


def test_metadata_changing_mid_read_is_not_lost(backend, smtc, winrt):
    """A track change fires several SMTC events at once, one of them mid-read.

    Clearing the stale flag unconditionally after the read discarded the event that
    arrived while it was in flight, leaving the phone on the previous track's title
    until something else happened to change.
    """
    _, player, _ = smtc
    backend.poll()

    original = player.try_get_media_properties_async
    fired = []

    def racing():
        operation = original()
        if not fired:
            fired.append(1)
            # Windows announces the next track while we are still reading this one.
            player._props = winrt.MediaProperties("Second Track", "Someone Else")
            player.handlers["media"](player, None)
        return operation

    player.handlers["media"](player, None)          # mark it stale so a read happens
    player.try_get_media_properties_async = racing
    backend.poll()
    assert fired, "the read under test never ran"
    assert backend.poll().track.title == "Second Track"


def test_an_app_that_declares_nothing_gets_play_pause_only(backend, smtc, winrt):
    """Advertising skip here would fire a global media key at some other app."""
    _, player, _ = smtc
    player._info = winrt.PlaybackInfo(
        winrt.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING, None)
    can = backend.poll().capabilities
    assert can.toggle and can.play and can.pause
    assert not can.next and not can.previous and not can.seek
