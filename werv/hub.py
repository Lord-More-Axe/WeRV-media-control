"""The bridge between a media backend and every connected phone.

One background thread watches the player.  When something actually changes it renders a
state payload once and hands it to every subscriber, rather than each client asking the
operating system the same three questions every second.  A four-phone household went from
twelve OS round-trips and twelve PNG writes per second to one cheap poll.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass

from .art import DEFAULT_PALETTE, ArtCache, Artwork
from .media import MediaBackend, PlayerState, Status

log = logging.getLogger("werv.hub")

# How often to ask the OS, when nothing has told us to look sooner.  Backends that can
# raise change notifications call `wake()` and make these an upper bound, not a cadence.
INTERVAL_ACTIVE = 1.0
INTERVAL_IDLE = 2.5

# Re-send an unchanged state occasionally so a client that has been interpolating its
# scrubber for a while is pulled back onto the truth.
RESYNC_SECONDS = 10.0

# A seek performed on the PC itself moves the position without changing anything else;
# spotting that drift is what keeps the phone's scrubber honest.
DRIFT_TOLERANCE = 1.5

SUBSCRIBER_BACKLOG = 4


@dataclass(eq=False)
class _Subscriber:
    queue: "queue.Queue[str | None]"

    def offer(self, payload: str) -> None:
        """Enqueue, discarding backlog rather than blocking the watcher thread.

        A phone that has gone to sleep must never be able to stall the one thread every
        other client depends on.  Since each payload is a complete snapshot, dropping
        intermediate ones costs nothing.
        """
        try:
            self.queue.put_nowait(payload)
        except queue.Full:
            try:
                while True:
                    self.queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait(payload)
            except queue.Full:
                pass

    def close(self) -> None:
        """Signal end-of-stream so the client's generator can return."""
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass


class MediaHub:
    def __init__(self, backend: MediaBackend) -> None:
        self.backend = backend
        self.art = ArtCache()

        self._state = PlayerState(status=Status.IDLE)
        self._artwork: Artwork | None = None
        self._sampled_at = time.monotonic()
        self._revision = 0
        self._last_push = 0.0

        self._lock = threading.Lock()
        self._subscribers: set[_Subscriber] = set()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------------
    def start(self) -> None:
        self.backend.start()
        setattr(self.backend, "on_change", self.wake)  # event-driven backends push us
        self._thread = threading.Thread(target=self._run, name="werv-watch", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        with self._lock:
            subscribers, self._subscribers = list(self._subscribers), set()
        for subscriber in subscribers:
            subscriber.close()
        try:
            self.backend.close()
        except Exception:
            log.debug("backend close failed", exc_info=True)

    def wake(self) -> None:
        """Ask the watcher to look again immediately."""
        self._wake.set()

    # -- watcher -----------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._refresh()
            except Exception:
                # The watcher thread must outlive any single bad poll; a backend that
                # throws once (app closing mid-read) should not silence every client.
                log.debug("poll failed", exc_info=True)
            interval = INTERVAL_ACTIVE if self._state.playing else INTERVAL_IDLE
            self._wake.wait(interval)
            self._wake.clear()

    def _refresh(self) -> None:
        state = self.backend.poll()
        now = time.monotonic()

        with self._lock:
            previous, previous_at = self._state, self._sampled_at
            self._state, self._sampled_at = state, now

        changed = state.identity() != previous.identity()
        if not changed and state.track.art_key != previous.track.art_key:
            changed = True

        if changed or state.track.id != previous.track.id or self._artwork is None:
            self._artwork = self._load_artwork(state)

        # Did the position jump in a way that free-running interpolation would not have
        # produced?  That means somebody scrubbed at the PC.
        expected = previous.timeline.position + (now - previous_at) * previous.timeline.rate
        drifted = abs(state.timeline.position - expected) > DRIFT_TOLERANCE

        if changed or drifted or (now - self._last_push) >= RESYNC_SECONDS:
            self._broadcast()

    def _load_artwork(self, state: PlayerState) -> Artwork | None:
        if state.track.empty:
            return None
        try:
            raw = self.backend.artwork(state.track)
        except Exception:
            log.debug("artwork read failed", exc_info=True)
            return None
        return self.art.put(raw) if raw else None

    # -- payload -----------------------------------------------------------------
    def snapshot(self) -> dict:
        """Current state, with the timeline aged to this instant."""
        with self._lock:
            state, sampled_at, artwork = self._state, self._sampled_at, self._artwork
            revision = self._revision

        palette = (artwork.palette if artwork else DEFAULT_PALETTE).as_dict()
        art_url = f"/art/{artwork.key}" if artwork else None
        payload = state.as_dict(art_url=art_url, palette=palette)
        # Two delays stack here and both matter. The backend reports how old *its* reading
        # was -- Windows only refreshes the position every few seconds -- and then the
        # state sat here until this request. Replacing rather than adding would silently
        # drop the first one and leave the scrubber running seconds behind the music.
        elapsed = max(0.0, time.monotonic() - sampled_at)
        payload["timeline"]["age"] = round(state.timeline.age + elapsed, 3)
        payload["rev"] = revision
        return payload

    def _broadcast(self) -> None:
        with self._lock:
            self._revision += 1
            subscribers = list(self._subscribers)
        self._last_push = time.monotonic()
        if not subscribers:
            return
        payload = json.dumps(self.snapshot(), separators=(",", ":"))
        for subscriber in subscribers:
            subscriber.offer(payload)

    # -- subscriptions -----------------------------------------------------------
    def subscribe(self) -> "queue.Queue[str | None]":
        subscriber = _Subscriber(queue.Queue(maxsize=SUBSCRIBER_BACKLOG))
        with self._lock:
            self._subscribers.add(subscriber)
        subscriber.offer(json.dumps(self.snapshot(), separators=(",", ":")))
        return subscriber.queue

    def unsubscribe(self, channel: "queue.Queue[str | None]") -> None:
        with self._lock:
            for subscriber in list(self._subscribers):
                if subscriber.queue is channel:
                    self._subscribers.discard(subscriber)

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    # -- commands ----------------------------------------------------------------
    def command(self, action: str, **kwargs) -> bool:
        """Run a transport command, then look again so the phone updates immediately."""
        handler = getattr(self.backend, action, None)
        if handler is None:
            raise KeyError(action)
        try:
            accepted = bool(handler(**kwargs))
        except Exception:
            log.debug("command %s failed", action, exc_info=True)
            accepted = False
        # Players update their reported state a beat after accepting a command; look
        # once now for responsiveness and again shortly after to catch up with them.
        self.wake()
        threading.Timer(0.25, self.wake).start()
        return accepted
