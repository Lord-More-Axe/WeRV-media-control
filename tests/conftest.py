"""Shared fixtures.

The Windows tests import a fake ``winrt``/``pycaw``/``comtypes`` from ``tests/fakes``,
which lets the real backend run its real code paths on any platform. That matters more
than usual here: the backend cannot be exercised on the developer's machine or in CI,
so a mock built faithfully from the published type stubs is the only place its method
names and argument shapes get checked at all.
"""

from __future__ import annotations

import datetime
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FAKES = Path(__file__).resolve().parent / "fakes"
sys.path.insert(0, str(ROOT))


@pytest.fixture
def cover_bytes() -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (400, 400), (24, 40, 120))
    ImageDraw.Draw(image).ellipse([70, 70, 330, 330], fill=(220, 96, 40))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=88)
    return buffer.getvalue()


@pytest.fixture
def winrt(monkeypatch):
    """Install the fake WinRT bindings and hand back the control namespace."""
    monkeypatch.syspath_prepend(str(FAKES))
    for name in [m for m in sys.modules if m.split(".")[0] in {"winrt", "pycaw", "comtypes"}]:
        del sys.modules[name]
    import winrt.windows.media.control as control
    yield control
    for name in [m for m in sys.modules if m.split(".")[0] in {"winrt", "pycaw", "comtypes"}]:
        del sys.modules[name]


@pytest.fixture
def smtc(winrt, cover_bytes):
    """A manager with two sessions: a full-featured player and a limited browser tab."""
    now = datetime.datetime.now(datetime.timezone.utc)
    manager = winrt.GlobalSystemMediaTransportControlsSessionManager()
    winrt.GlobalSystemMediaTransportControlsSessionManager.instance = manager

    rich = winrt.Controls(play=True, pause=True, play_pause_toggle=True, next=True,
                          previous=True, stop=True, shuffle=True, repeat=True,
                          playback_position=True)
    plain = winrt.Controls(play=True, pause=True, play_pause_toggle=True)

    player = winrt.GlobalSystemMediaTransportControlsSession(
        "Spotify.exe",
        winrt.PlaybackInfo(winrt.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING,
                           rich, shuffle=True, repeat=2, rate=1.0),
        winrt.TimelineProperties(0, 214, 61, now - datetime.timedelta(seconds=2.5)),
        winrt.MediaProperties("Nightdrive", "Aurora Field", "Parallel Hours",
                              track_number=3, album_track_count=11,
                              thumbnail=winrt.StreamReference(cover_bytes)),
    )
    tab = winrt.GlobalSystemMediaTransportControlsSession(
        "chrome.exe",
        winrt.PlaybackInfo(winrt.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PAUSED, plain),
        # The zero timestamp a player that never reports a timeline leaves behind.
        winrt.TimelineProperties(0, 0, 0, datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)),
        winrt.MediaProperties("A video", "a channel"),
    )
    manager._sessions = [player, tab]
    manager._current = player
    return manager, player, tab


@pytest.fixture
def backend(smtc):
    from werv.windows import WindowsBackend

    instance = WindowsBackend()
    instance.start()
    yield instance
    instance.close()
