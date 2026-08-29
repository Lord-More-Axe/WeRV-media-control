"""The push loop: one poll of the OS feeding every connected phone."""

from __future__ import annotations

import json
import queue
import threading
import time

import pytest

from werv.demo import DemoBackend
from werv.hub import MediaHub


@pytest.fixture
def hub():
    instance = MediaHub(DemoBackend())
    instance.start()
    time.sleep(0.3)
    yield instance
    instance.close()


def _drain(channel, timeout=1.5):
    """The newest payload, discarding any that piled up behind it."""
    latest = json.loads(channel.get(timeout=timeout))
    while True:
        try:
            latest = json.loads(channel.get(timeout=0.4))
        except queue.Empty:
            return latest


def test_a_new_subscriber_gets_the_state_immediately(hub):
    """No blank frame while waiting for the next change."""
    state = _drain(hub.subscribe())
    assert state["status"] == "playing"
    assert state["track"]["title"]
    assert state["track"]["art"].startswith("/art/")
    assert state["palette"]["accent"].startswith("#")


def test_commands_are_pushed_not_polled(hub):
    channel = hub.subscribe()
    _drain(channel)
    before = time.monotonic()
    hub.command("next")
    payload = _drain(channel)
    assert payload["track"]["title"]
    # The point of the command hook: the phone hears about it right away.
    assert time.monotonic() - before < 1.0


def test_every_subscriber_sees_the_same_change(hub):
    first, second = hub.subscribe(), hub.subscribe()
    _drain(first); _drain(second)
    hub.command("pause")
    assert _drain(first)["status"] == "paused"
    assert _drain(second)["status"] == "paused"


def test_the_timeline_is_aged_so_the_client_needs_no_clock(hub):
    channel = hub.subscribe()
    _drain(channel)
    time.sleep(0.6)
    snapshot = hub.snapshot()
    assert snapshot["timeline"]["age"] > 0
    assert snapshot["timeline"]["rate"] == 1.0


def test_a_stalled_subscriber_cannot_block_the_others(hub):
    """Payloads are whole snapshots, so dropping backlog costs nothing."""
    stalled = hub.subscribe()
    live = hub.subscribe()
    for _ in range(40):
        hub.command("next")
    assert stalled.qsize() <= 8
    assert _drain(live)["status"] in ("playing", "paused")


def test_unsubscribing_releases_the_slot(hub):
    channel = hub.subscribe()
    assert hub.client_count == 1
    hub.unsubscribe(channel)
    assert hub.client_count == 0


def test_a_backend_that_throws_does_not_kill_the_watcher(hub):
    calls = []

    def boom():
        calls.append(1)
        raise OSError("the media service went away")

    hub.backend.poll = boom
    hub.wake()
    time.sleep(0.6)
    hub.wake()
    time.sleep(0.6)
    # It kept polling rather than dying on the first failure.
    assert len(calls) >= 2


def test_closing_ends_the_stream(hub):
    channel = hub.subscribe()
    _drain(channel)
    hub.close()
    deadline = time.time() + 2
    while time.time() < deadline:
        item = channel.get(timeout=2)
        if item is None:
            return
    pytest.fail("subscribers were never sent an end-of-stream marker")


def test_subscribing_after_close_ends_immediately():
    """Otherwise the stream hangs forever on a queue nothing will ever feed."""
    instance = MediaHub(DemoBackend())
    instance.start()
    time.sleep(0.2)
    instance.close()
    assert instance.subscribe().get(timeout=1) is None


def test_the_shutdown_sentinel_outranks_a_full_backlog(hub):
    channel = hub.subscribe()
    for _ in range(20):
        hub.command("next")          # fill the backlog past its cap
    hub.close()
    deadline = time.time() + 2
    while time.time() < deadline:
        if channel.get(timeout=2) is None:
            return
    pytest.fail("a stalled subscriber never saw end-of-stream")


def test_commands_do_not_pile_up_follow_up_timers(hub):
    import threading

    before = threading.active_count()
    for _ in range(30):
        hub.command("next")
    time.sleep(0.6)
    # One outstanding follow-up at a time, not one thread per tap.
    assert threading.active_count() <= before + 2


def test_a_new_subscriber_never_receives_state_out_of_order(hub):
    """The first snapshot must not arrive behind a broadcast that happened after it.

    snapshot() reads state under the lock and then renders outside it, so a subscriber
    made visible before its snapshot was queued could be handed a newer payload first
    and settle on the older one.
    """
    stop = threading.Event()

    def churn():
        while not stop.is_set():
            hub.command("next")
            time.sleep(0.01)

    noise = threading.Thread(target=churn, daemon=True)
    noise.start()
    try:
        for _ in range(25):
            channel = hub.subscribe()
            revisions = []
            deadline = time.time() + 0.5
            while time.time() < deadline:
                try:
                    payload = channel.get(timeout=0.1)
                except queue.Empty:
                    break
                if payload is None:
                    break
                revisions.append(json.loads(payload)["rev"])
            hub.unsubscribe(channel)
            assert revisions == sorted(revisions), f"out of order: {revisions}"
    finally:
        stop.set()
        noise.join(timeout=1)


def test_a_broadcast_during_registration_is_not_lost(hub):
    """Registering and seeding a subscriber have to be one step.

    Seed first and any broadcast landing in between is dropped; register first and it is
    delivered ahead of the older snapshot. Either way the phone shows the wrong track
    until the next change or the ten-second resync. This forces the interleaving instead
    of hoping to hit it: the first snapshot is held open while a broadcast is issued.
    """
    real_snapshot = hub.snapshot
    inside = threading.Event()
    release = threading.Event()

    def held_snapshot():
        payload = real_snapshot()
        if not inside.is_set():
            inside.set()
            release.wait(2.0)          # the registration window, held wide open
        return payload

    hub.snapshot = held_snapshot
    box = {}
    joiner = threading.Thread(target=lambda: box.update(channel=hub.subscribe()), daemon=True)
    joiner.start()
    assert inside.wait(2.0), "subscribe never rendered a snapshot"

    # The track changes and the watcher publishes it while that registration is still
    # in flight. Driving _refresh rather than _broadcast matters: the hub's own cached
    # state has to move too, or "the newest state" is still the one already seeded.
    hub.backend.next()
    broadcaster = threading.Thread(target=hub._refresh, daemon=True)
    broadcaster.start()
    time.sleep(0.2)
    release.set()
    joiner.join(timeout=3)
    broadcaster.join(timeout=3)
    hub.snapshot = real_snapshot

    expected = hub.snapshot()["track"]["id"]
    channel = box["channel"]
    seen = []
    deadline = time.time() + 0.3       # short, so a later poll cannot rescue a miss
    while time.time() < deadline:
        try:
            payload = channel.get(timeout=0.1)
        except queue.Empty:
            continue
        if payload is None:
            break
        seen.append(json.loads(payload))
    hub.unsubscribe(channel)

    assert seen, "the subscriber received nothing at all"
    assert [f["rev"] for f in seen] == sorted(f["rev"] for f in seen)
    assert seen[-1]["track"]["id"] == expected, "the broadcast raced during registration was lost"
