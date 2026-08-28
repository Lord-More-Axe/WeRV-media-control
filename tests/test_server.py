"""HTTP surface, with particular attention to what a hostile network peer can do.

This binds to every interface on a network the user does not control -- the README even
tells people to allow it through the firewall on public networks -- so the interesting
tests here are the ones about who is *refused*.
"""

from __future__ import annotations

import json
import time

import pytest

from werv.demo import DemoBackend
from werv.hub import MediaHub
from werv.server import create_app

TOKEN = "test-token-value"


@pytest.fixture
def app():
    hub = MediaHub(DemoBackend())
    hub.start()
    time.sleep(0.3)
    application = create_app(hub, token=TOKEN, port=5000)
    application.config.update(TESTING=True)
    yield application
    hub.close()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def paired(app):
    session = app.test_client()
    session.get(f"/?k={TOKEN}")          # trade the pairing key for a cookie
    return session


# -- pairing -------------------------------------------------------------------

def test_an_unpaired_device_is_refused(client):
    assert client.get("/api/state").status_code == 401
    assert client.get("/events").status_code == 401
    assert client.post("/api/command", json={"action": "next"}).status_code == 401


def test_the_qr_key_pairs_in_one_hop(client):
    response = client.get(f"/?k={TOKEN}")
    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_the_key_is_dropped_from_the_address_bar(client):
    """So it is not left in history, or in a screenshot of the phone."""
    assert "k=" not in client.get(f"/?k={TOKEN}").headers["Location"]


def test_a_wrong_key_is_refused(client):
    assert client.get("/api/state?k=not-the-key").status_code == 401


def test_a_paired_device_can_read_and_control(paired):
    assert paired.get("/api/state").status_code == 200
    assert paired.post("/api/command", json={"action": "next"}).status_code == 200


def test_no_auth_mode_lets_everyone_in():
    hub = MediaHub(DemoBackend())
    hub.start()
    try:
        client = create_app(hub, token=None, port=5000).test_client()
        assert client.get("/api/state").status_code == 200
    finally:
        hub.close()


# -- hostile callers -----------------------------------------------------------

def test_a_rebound_dns_name_is_refused(paired):
    """A page on the internet can point a name it owns at this machine's LAN address.

    The browser then treats it as same-origin and sends our cookie, so the Host header
    is the only thing left that can tell the difference.
    """
    assert paired.get("/api/state", headers={"Host": "attacker.example.com"}).status_code == 403
    assert paired.post("/api/command", json={"action": "next"},
                       headers={"Host": "attacker.example.com"}).status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1:5000", "192.168.1.40:5000", "10.0.0.7",
                                  "localhost:5000", "mypc.local:5000", "[::1]:5000"])
def test_real_local_addresses_are_allowed(client, host):
    """Authenticate by header, so this test is about the Host check and nothing else.

    (A cookie would not be sent here anyway: browsers scope cookies per origin, so a
    phone paired at 192.168.1.40 only ever sends it back to 192.168.1.40.)
    """
    response = client.get("/api/state", headers={"Host": host, "X-WeRV-Key": TOKEN})
    assert response.status_code == 200


@pytest.mark.parametrize("host", ["attacker.example.com", "werv.evil.co.uk", "example.org:5000"])
def test_internet_names_are_refused_even_with_the_key(client, host):
    response = client.get("/api/state", headers={"Host": host, "X-WeRV-Key": TOKEN})
    assert response.status_code == 403


def test_control_endpoints_reject_a_cross_site_form_post(paired):
    """A form POST is the one cross-origin request a browser sends without a preflight."""
    assert paired.post("/api/command", data={"action": "next"}).status_code == 415
    assert paired.post("/api/command", data="action=next",
                       content_type="text/plain").status_code == 415


def test_controls_are_not_reachable_by_get(paired):
    """The original fired /next and /delete from any <img src> on any website."""
    for path in ("/api/command", "/next", "/pp", "/delete", "/pre"):
        assert paired.get(path).status_code in (404, 405)


def test_unknown_actions_are_rejected(paired):
    assert paired.post("/api/command", json={"action": "shutdown"}).status_code == 400
    assert paired.post("/api/command", json={"action": "poll"}).status_code == 400


def test_malformed_arguments_are_rejected(paired):
    for body in ({"action": "seek"}, {"action": "seek", "seconds": "soon"},
                 {"action": "set_volume", "level": None},
                 {"action": "set_repeat", "mode": "sideways"}):
        assert paired.post("/api/command", json=body).status_code == 400


def test_volume_is_clamped_at_the_edge(paired):
    assert paired.post("/api/command", json={"action": "set_volume", "level": 42}).status_code == 200
    assert json.loads(paired.get("/api/state").data)["volume"]["level"] <= 1.0


# -- art -----------------------------------------------------------------------

def test_art_is_immutable_and_revalidates(paired):
    key = json.loads(paired.get("/api/state").data)["track"]["art"]
    response = paired.get(key)
    assert response.status_code == 200
    assert "immutable" in response.headers["Cache-Control"]
    etag = response.headers["ETag"]
    assert paired.get(key, headers={"If-None-Match": etag}).status_code == 304


def test_unknown_art_is_a_404_not_a_traversal(paired):
    """The original derived a filename from the track title and wrote it to disk."""
    for key in ("../../werv.py", "..%2f..%2fwerv.py", "nonexistent"):
        assert paired.get(f"/art/{key}").status_code == 404


# -- streaming -----------------------------------------------------------------

def test_events_stream_is_sent_unbuffered(paired):
    response = paired.get("/events", buffered=False)
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert "no-cache" in response.headers["Cache-Control"]
    # `Connection` is hop-by-hop; a WSGI app that sets it is rejected outright.
    assert "Connection" not in response.headers
    first = next(response.response)
    assert first.startswith(b"data: ")
    response.close()


def test_state_carries_everything_the_interface_renders(paired):
    state = json.loads(paired.get("/api/state").data)
    assert set(state) >= {"status", "track", "timeline", "can", "volume",
                          "shuffle", "repeat", "sessions", "palette", "rev"}
    assert set(state["timeline"]) == {"position", "duration", "age", "rate", "seekable"}


# -- odds and ends -------------------------------------------------------------

def test_pairing_payload_offers_a_qr_and_fallback_addresses(paired):
    info = json.loads(paired.get("/api/pair").data)
    assert info["url"].startswith("http://") and TOKEN in info["url"]
    assert info["qr"].startswith("<svg")
    assert isinstance(info["alternatives"], list)


def test_health_needs_no_pairing(client):
    body = json.loads(client.get("/health").data)
    assert body["ok"] and body["backend"] == "demo"


def test_home_screen_assets_are_served(client):
    assert client.get("/manifest.webmanifest").status_code == 200
    assert client.get("/icon.svg").status_code == 200
    assert client.get("/icon-180.png").data[:4] == b"\x89PNG"
    assert client.get("/icon-999.png").status_code == 404
