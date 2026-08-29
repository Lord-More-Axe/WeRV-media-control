"""The shape of a player, independent of the operating system underneath it.

Everything the interface needs is described here as plain dataclasses, and every backend
promises the same small set of verbs.  That split is what lets the Windows implementation
stay a thin adapter over the system media API, and what lets the whole interface be run
and iterated on off-Windows through the simulated backend.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from enum import Enum


class Status(str, Enum):
    """What the player is doing.

    ``UNAVAILABLE`` means we could not talk to the OS at all (missing dependency, denied
    permission).  ``IDLE`` means we can talk to it fine and nothing is playing -- a
    distinction the old code collapsed into a 500 response.
    """

    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"
    IDLE = "idle"
    UNAVAILABLE = "unavailable"


class Repeat(str, Enum):
    OFF = "off"
    ALL = "all"
    ONE = "one"


@dataclass(frozen=True)
class Track:
    title: str = ""
    artist: str = ""
    album: str = ""
    track_number: int = 0
    track_count: int = 0
    art_key: str | None = None

    @property
    def id(self) -> str:
        """Stable identity used by the client to detect an actual track change.

        Derived from the metadata rather than a counter so it survives the server
        restarting or a client reconnecting mid-song.
        """
        seed = f"{self.title}\x1f{self.artist}\x1f{self.album}".lower()
        return hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()[:12]

    @property
    def empty(self) -> bool:
        return not (self.title or self.artist or self.album)


@dataclass(frozen=True)
class Timeline:
    """Playback position, expressed so the client never needs a synchronised clock.

    Rather than sending an absolute timestamp -- which would need the phone's clock to
    agree with the PC's -- we send how many seconds ago the position was sampled.  The
    client adds that to the time elapsed since *it* received the message, and runs the
    scrubber forward locally at ``rate``.  No clock skew, no polling.
    """

    position: float = 0.0   # seconds, at the moment it was sampled
    duration: float = 0.0   # seconds; 0 when unknown (live streams)
    age: float = 0.0        # seconds between sampling and sending
    rate: float = 0.0       # 1.0 while playing, 0.0 otherwise
    seekable: bool = False

    def as_dict(self) -> dict:
        return {
            "position": round(self.position, 3),
            "duration": round(self.duration, 3),
            "age": round(self.age, 3),
            "rate": self.rate,
            "seekable": self.seekable,
        }


@dataclass(frozen=True)
class Capabilities:
    """Which controls the current app actually honours.

    Spotify allows seeking; a YouTube tab usually does not; a podcast app may offer no
    shuffle.  Sending this lets the interface dim what will not work instead of
    presenting buttons that silently do nothing.
    """

    play: bool = False
    pause: bool = False
    toggle: bool = False
    next: bool = False
    previous: bool = False
    stop: bool = False
    seek: bool = False
    shuffle: bool = False
    repeat: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Volume:
    """System or per-app volume. ``available`` is False when we cannot read it."""

    level: float = 0.0        # 0.0 - 1.0
    muted: bool = False
    available: bool = False
    scope: str = "master"     # "master" or "app"

    def as_dict(self) -> dict:
        return {"level": round(self.level, 4), "muted": self.muted,
                "available": self.available, "scope": self.scope}


@dataclass(frozen=True)
class SessionRef:
    """One app currently registered with the system as playing media."""

    id: str
    name: str
    playing: bool = False
    current: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PlayerState:
    status: Status = Status.IDLE
    track: Track = field(default_factory=Track)
    timeline: Timeline = field(default_factory=Timeline)
    capabilities: Capabilities = field(default_factory=Capabilities)
    volume: Volume = field(default_factory=Volume)
    shuffle: bool | None = None
    repeat: Repeat | None = None
    sessions: tuple[SessionRef, ...] = ()
    session_id: str | None = None
    message: str = ""          # human-readable explanation for IDLE / UNAVAILABLE

    @property
    def playing(self) -> bool:
        return self.status is Status.PLAYING

    def as_dict(self, *, art_url: str | None = None, palette: dict | None = None) -> dict:
        return {
            "status": self.status.value,
            "message": self.message,
            "track": {
                "id": self.track.id,
                "title": self.track.title,
                "artist": self.track.artist,
                "album": self.track.album,
                "trackNumber": self.track.track_number,
                "trackCount": self.track.track_count,
                "art": art_url,
            },
            "timeline": self.timeline.as_dict(),
            "can": self.capabilities.as_dict(),
            "volume": self.volume.as_dict(),
            "shuffle": self.shuffle,
            "repeat": self.repeat.value if self.repeat else None,
            "sessions": [s.as_dict() for s in self.sessions],
            "sessionId": self.session_id,
            "palette": palette,
        }

    def identity(self) -> tuple:
        """Everything except the freely-running clock.

        The hub compares this to decide whether a state genuinely changed and is worth
        pushing to clients; the scrubber advances on its own, so a moving position alone
        must not trigger a broadcast.
        """
        return (
            self.status, self.track, self.capabilities, self.shuffle,
            self.repeat, self.sessions, self.session_id, self.message,
            # Rounded: the OS reports volume as a float that can jitter in its last bits,
            # and a broadcast per poll would defeat the point of only pushing on change.
            round(self.volume.level, 3), self.volume.muted, self.volume.available,
            round(self.timeline.duration, 1), self.timeline.seekable,
        )


class MediaBackend:
    """What every platform adapter must provide.

    Commands return ``True`` when the platform accepted them.  They are intentionally
    tolerant: a backend that cannot seek returns ``False`` rather than raising, because a
    remote control that throws a 500 at a phone is worse than one that declines politely.
    """

    name = "none"

    def start(self) -> None: ...

    def close(self) -> None: ...

    def poll(self) -> PlayerState:
        """Read the current state. Must never raise."""
        raise NotImplementedError

    def artwork(self, track: Track) -> bytes | None:
        """Raw cover bytes for ``track``, or None."""
        return None

    # -- transport ---------------------------------------------------------------
    def play(self) -> bool: return False
    def pause(self) -> bool: return False
    def toggle(self) -> bool: return False
    def stop(self) -> bool: return False
    def next(self) -> bool: return False
    def previous(self) -> bool: return False
    def seek(self, seconds: float) -> bool: return False
    def set_shuffle(self, on: bool) -> bool: return False
    def set_repeat(self, mode: Repeat) -> bool: return False

    # -- audio -------------------------------------------------------------------
    def set_volume(self, level: float) -> bool: return False
    def set_muted(self, muted: bool) -> bool: return False

    # -- session selection -------------------------------------------------------
    def select_session(self, session_id: str | None) -> bool: return False
