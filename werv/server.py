"""HTTP surface: one streaming endpoint, one command endpoint, and the pages.

Two things shape the design.  First, clients are *pushed* to over Server-Sent Events
instead of polling, so a phone left open on a nightstand costs one idle socket rather
than three requests a second.  Second, this binds to every interface on a network the
user does not control, so the control endpoints are behind a pairing token and the
usual browser-side attacks against a local service are closed off deliberately.
"""

from __future__ import annotations

import ipaddress
import logging
import queue
import secrets
import sys
from functools import wraps
from pathlib import Path

from flask import (
    Flask, Response, abort, jsonify, make_response, redirect, render_template, request, url_for,
)

from . import __version__, net
from .hub import MediaHub
from .media import Repeat

log = logging.getLogger("werv.server")

COOKIE = "werv_key"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30
KEEPALIVE_SECONDS = 15.0

#: Commands the phone may invoke, and how to read their arguments off the request body.
COMMANDS: dict[str, callable] = {
    "play": lambda _: {},
    "pause": lambda _: {},
    "toggle": lambda _: {},
    "stop": lambda _: {},
    "next": lambda _: {},
    "previous": lambda _: {},
    "seek": lambda body: {"seconds": max(0.0, float(body["seconds"]))},
    "set_shuffle": lambda body: {"on": bool(body["on"])},
    "set_repeat": lambda body: {"mode": Repeat(str(body["mode"]))},
    "set_volume": lambda body: {"level": min(max(float(body["level"]), 0.0), 1.0)},
    "set_muted": lambda body: {"muted": bool(body["muted"])},
    "select_session": lambda body: {"session_id": body.get("session_id") or None},
}


def _host_is_local(host: str) -> bool:
    """Guard against DNS rebinding.

    A page on the public internet can point a name it controls at this machine's LAN
    address; the browser then treats the request as same-origin and our cookie rides
    along.  Requiring the Host header to be an IP literal or a local-network name means
    such a request never reaches a control endpoint.
    """
    name = host.rsplit(":", 1)[0].strip("[]").lower() if host else ""
    if not name:
        return False
    if name in ("localhost",) or name.endswith((".local", ".lan", ".home", ".internal")):
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def _asset_root() -> Path:
    """Where templates/ and static/ live.

    PyInstaller unpacks bundled data into a temporary directory and points
    ``sys._MEIPASS`` at it, so paths relative to this module do not survive freezing.
    """
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parent.parent


def create_app(hub: MediaHub, *, token: str | None, port: int, quiet: bool = True) -> Flask:
    root = _asset_root()
    app = Flask(__name__,
                template_folder=str(root / "templates"),
                static_folder=str(root / "static"))
    app.config.update(SECRET_KEY=secrets.token_bytes(32), JSON_SORT_KEYS=False)
    if quiet:
        logging.getLogger("werkzeug").setLevel(logging.WARNING)

    def authorised() -> bool:
        if token is None:
            return True
        supplied = request.cookies.get(COOKIE) or request.args.get("k") or request.headers.get("X-WeRV-Key")
        return bool(supplied) and secrets.compare_digest(supplied, token)

    def guard(view):
        """Authorise, and reject cross-site calls into the control surface."""
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not _host_is_local(request.host):
                abort(403, "unrecognised host")
            if not authorised():
                abort(401, "pair this device first")
            return view(*args, **kwargs)
        return wrapper

    # -- pages -------------------------------------------------------------------
    @app.get("/")
    def index():
        if not _host_is_local(request.host):
            abort(403, "unrecognised host")
        supplied = request.args.get("k")
        if token is not None and supplied and secrets.compare_digest(supplied, token):
            # Trade the token in the QR link for a cookie, then drop it from the address
            # bar so the key is not left sitting in history or a shared screenshot.
            response = redirect(url_for("index"))
            response.set_cookie(
                COOKIE, token, max_age=COOKIE_MAX_AGE, httponly=True,
                samesite="Lax", path="/",
            )
            return response
        if not authorised():
            return render_template("pair.html", version=__version__), 401
        return render_template("remote.html", version=__version__)

    @app.get("/manifest.webmanifest")
    def manifest():
        return jsonify({
            "name": "WeRV Remote",
            "short_name": "WeRV",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#0b0b0f",
            "theme_color": "#0b0b0f",
            "icons": [
                {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
                {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
            ],
        })

    @app.get("/icon.svg")
    def icon_svg():
        return Response(_ICON_SVG, mimetype="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/icon-<int:size>.png")
    def icon_png(size: int):
        if size not in (180, 192, 512):
            abort(404)
        return Response(_icon_png(size), mimetype="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})

    # -- data --------------------------------------------------------------------
    @app.get("/api/state")
    @guard
    def api_state():
        return jsonify(hub.snapshot())

    @app.get("/api/pair")
    @guard
    def api_pair():
        """Everything the in-app pairing sheet needs to put this player on another device."""
        addresses = net.candidates(port)
        primary = addresses[0] if addresses else None
        link = primary.url(token) if primary else ""
        return jsonify({
            "url": link,
            "qr": net.qr_svg(link) if link else "",
            "alternatives": [a.url(token) for a in addresses[1:]],
        })

    @app.get("/events")
    @guard
    def events():
        channel = hub.subscribe()

        def stream():
            try:
                while True:
                    try:
                        payload = channel.get(timeout=KEEPALIVE_SECONDS)
                    except queue.Empty:
                        # Comment frames keep sleeping phones and any intermediary from
                        # deciding the idle connection is dead.
                        yield ": ping\n\n"
                        continue
                    if payload is None:
                        return
                    yield f"data: {payload}\n\n"
            finally:
                hub.unsubscribe(channel)

        # No `Connection` header: it is hop-by-hop and PEP 3333 forbids a WSGI app from
        # setting it -- waitress rejects the response outright if you do.
        return Response(stream(), mimetype="text/event-stream", headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        })

    @app.post("/api/command")
    @guard
    def api_command():
        if not request.is_json:
            abort(415, "expected application/json")
        body = request.get_json(silent=True) or {}
        action = str(body.get("action", ""))
        builder = COMMANDS.get(action)
        if builder is None:
            abort(400, "unknown action")
        try:
            kwargs = builder(body)
        except (KeyError, TypeError, ValueError):
            abort(400, "bad arguments")
        accepted = hub.command(action, **kwargs)
        return jsonify({"ok": accepted, "action": action})

    @app.get("/art/<key>")
    @guard
    def art(key: str):
        artwork = hub.art.get(key)
        if artwork is None:
            abort(404)
        etag = f'"{key}"'
        if request.headers.get("If-None-Match") == etag:
            return make_response("", 304)
        # Content-addressed, so this URL can never mean anything else: cache it forever
        # and each device downloads a given cover exactly once.
        return Response(artwork.data, mimetype=artwork.content_type, headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": etag,
        })

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "version": __version__,
                        "backend": hub.backend.name, "clients": hub.client_count})

    @app.errorhandler(401)
    def unauthorised(_error):
        if request.path == "/" or "text/html" in request.headers.get("Accept", ""):
            return render_template("pair.html", version=__version__), 401
        return jsonify({"error": "unauthorised"}), 401

    @app.errorhandler(403)
    def forbidden(error):
        return jsonify({"error": getattr(error, "description", "forbidden")}), 403

    @app.after_request
    def harden(response: Response) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    return app


_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
    '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#8b7cf6"/><stop offset="1" stop-color="#e05fa8"/>'
    "</linearGradient></defs>"
    '<rect width="512" height="512" rx="116" fill="#0b0b0f"/>'
    '<g fill="url(#g)">'
    '<rect x="150" y="196" width="34" height="120" rx="17"/>'
    '<rect x="222" y="140" width="34" height="232" rx="17"/>'
    '<rect x="294" y="176" width="34" height="160" rx="17"/>'
    "</g></svg>"
)


def _icon_png(size: int) -> bytes:
    """Home-screen icon, drawn at request time so no binaries live in the repo."""
    import io

    from PIL import Image, ImageDraw

    scale = size / 512
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=116 * scale, fill=(11, 11, 15, 255))
    bars = [(150, 196, 120), (222, 140, 232), (294, 176, 160)]
    for index, (x, y, height) in enumerate(bars):
        blend = index / max(len(bars) - 1, 1)
        colour = (round(139 + (224 - 139) * blend), round(124 + (95 - 124) * blend),
                  round(246 + (168 - 246) * blend), 255)
        draw.rounded_rectangle(
            [x * scale, y * scale, (x + 34) * scale, (y + height) * scale],
            radius=17 * scale, fill=colour,
        )
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()
