"""A simulated player, so the interface can be developed and tested off-Windows.

Nothing about the front end should require a Windows machine to work on.  This backend
fabricates a small playlist with generated cover art and a position that advances in real
time, and honours every command, so the whole application -- routing, streaming, theming,
scrubbing -- can be exercised anywhere.
"""

from __future__ import annotations

import io
import math
import threading
import time

from PIL import Image, ImageDraw, ImageFilter

from .media import (
    Capabilities, MediaBackend, PlayerState, Repeat, SessionRef, Status, Timeline, Track, Volume,
)

_PLAYLIST = [
    (Track("Nightdrive", "Aurora Field", "Parallel Hours", 3, 11), 214.0, (18, 32, 92), (198, 62, 140)),
    (Track("Slow Static", "Marisol Vane", "Room Tone", 5, 9), 186.0, (12, 74, 66), (222, 176, 64)),
    (Track("Every Light in the House Is On (Reprise)", "The Long Weekend", "Anthology", 12, 18), 301.0, (86, 22, 30), (240, 138, 74)),
    (Track("Halcyon", "Petra Lune", "Blue Hour", 1, 7), 247.0, (28, 24, 74), (96, 196, 208)),
]

_CAPS = Capabilities(play=True, pause=True, toggle=True, next=True, previous=True,
                     stop=True, seek=True, shuffle=True, repeat=True)


def _cover(seed: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> bytes:
    """Generate a plausible piece of cover art.

    Soft overlapping radial washes plus a light blur reads much more like a real sleeve
    than a flat gradient, and gives the palette extractor something honest to chew on.
    """
    size = 720
    image = Image.new("RGB", (size, size), top)
    draw = ImageDraw.Draw(image, "RGBA")
    for i in range(7):
        angle = seed * 1.7 + i * 0.9
        cx = size * (0.5 + 0.42 * math.cos(angle))
        cy = size * (0.5 + 0.42 * math.sin(angle * 1.3))
        radius = size * (0.22 + 0.16 * ((i * 37 + seed * 13) % 10) / 10)
        blend = i / 6
        colour = tuple(round(top[c] + (bottom[c] - top[c]) * blend) for c in range(3))
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=(*colour, 150))
    image = image.filter(ImageFilter.GaussianBlur(size / 14))

    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


class DemoBackend(MediaBackend):
    """In-memory player used by ``--demo`` and on non-Windows hosts."""

    name = "demo"

    def __init__(self, *, autoplay: bool = True) -> None:
        self._lock = threading.RLock()
        self._index = 0
        self._playing = autoplay
        self._position = 41.0
        self._since = time.monotonic()
        self._volume = 0.62
        self._muted = False
        self._shuffle = False
        self._repeat = Repeat.OFF
        self._art: dict[str, bytes] = {}

    # -- internals ---------------------------------------------------------------
    def _elapsed(self) -> float:
        if not self._playing:
            return self._position
        return self._position + (time.monotonic() - self._since)

    def _settle(self) -> None:
        self._position = self._elapsed()
        self._since = time.monotonic()

    def _advance(self, step: int) -> None:
        self._index = (self._index + step) % len(_PLAYLIST)
        self._position = 0.0
        self._since = time.monotonic()

    # -- reads -------------------------------------------------------------------
    def poll(self) -> PlayerState:
        with self._lock:
            track, duration, _, _ = _PLAYLIST[self._index]
            position = self._elapsed()
            if position >= duration:  # roll over like a real player would
                self._advance(1)
                track, duration, _, _ = _PLAYLIST[self._index]
                position = 0.0

            key = f"demo:{self._index}"
            return PlayerState(
                status=Status.PLAYING if self._playing else Status.PAUSED,
                track=Track(track.title, track.artist, track.album,
                            track.track_number, track.track_count, art_key=key),
                timeline=Timeline(position=position, duration=duration,
                                  rate=1.0 if self._playing else 0.0, seekable=True),
                capabilities=_CAPS,
                volume=Volume(level=self._volume, muted=self._muted, available=True, scope="master"),
                shuffle=self._shuffle,
                repeat=self._repeat,
                sessions=(SessionRef(id="demo", name="Demo Player", playing=self._playing, current=True),),
                session_id="demo",
            )

    def artwork(self, track: Track) -> bytes | None:
        if not track.art_key:
            return None
        with self._lock:
            if track.art_key not in self._art:
                index = int(track.art_key.split(":")[1])
                _, _, top, bottom = _PLAYLIST[index]
                self._art[track.art_key] = _cover(index, top, bottom)
            return self._art[track.art_key]

    # -- commands ----------------------------------------------------------------
    def play(self) -> bool:
        with self._lock:
            self._settle()
            self._playing = True
        return True

    def pause(self) -> bool:
        with self._lock:
            self._settle()
            self._playing = False
        return True

    def toggle(self) -> bool:
        with self._lock:
            return self.pause() if self._playing else self.play()

    def stop(self) -> bool:
        with self._lock:
            self._playing = False
            self._position = 0.0
            self._since = time.monotonic()
        return True

    def next(self) -> bool:
        with self._lock:
            self._advance(1)
        return True

    def previous(self) -> bool:
        # Match real players: restart the track unless we are near its start.
        with self._lock:
            self._advance(0 if self._elapsed() > 3 else -1)
        return True

    def seek(self, seconds: float) -> bool:
        with self._lock:
            _, duration, _, _ = _PLAYLIST[self._index]
            self._position = min(max(seconds, 0.0), duration)
            self._since = time.monotonic()
        return True

    def set_shuffle(self, on: bool) -> bool:
        with self._lock:
            self._shuffle = bool(on)
        return True

    def set_repeat(self, mode: Repeat) -> bool:
        with self._lock:
            self._repeat = mode
        return True

    def set_volume(self, level: float) -> bool:
        with self._lock:
            self._volume = min(max(level, 0.0), 1.0)
            if self._volume > 0:
                self._muted = False
        return True

    def set_muted(self, muted: bool) -> bool:
        with self._lock:
            self._muted = bool(muted)
        return True

    def select_session(self, session_id: str | None) -> bool:
        return session_id in (None, "demo")
