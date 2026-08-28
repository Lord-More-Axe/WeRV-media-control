"""Command line entry point.

Starting WeRV should tell you exactly one thing: where to point your phone.  The old
version wrote ``qr_code.png`` into the working directory and popped it open in the
system image viewer; here the QR is drawn straight into the console, so there is nothing
to clean up and nothing to close.
"""

from __future__ import annotations

import argparse
import logging
import platform
import signal
import sys
import threading

from . import __version__, net
from .hub import MediaHub
from .media import MediaBackend
from .server import create_app

log = logging.getLogger("werv")

BANNER = "\n  \033[1mWeRV\033[0m \033[2m{version}\033[0m  \033[2m--  remote media control\033[0m\n"


def _pick_backend(force_demo: bool) -> tuple[MediaBackend, str | None]:
    """Choose a platform adapter, explaining any fallback rather than crashing."""
    if force_demo:
        from .demo import DemoBackend
        return DemoBackend(), None

    if platform.system() != "Windows":
        from .demo import DemoBackend
        return DemoBackend(), (
            f"{platform.system()} detected -- media control is Windows-only, "
            "so the simulated player is running instead."
        )

    try:
        from .windows import WindowsBackend
        return WindowsBackend(), None
    except Exception as error:  # pragma: no cover - depends on the host
        from .demo import DemoBackend
        return DemoBackend(), f"Windows media control unavailable ({error}); running the simulated player."


def _console_qr(data: str, *, light_terminal: bool = False) -> str:
    """Render a QR small enough to scan off a terminal, using half-block characters.

    Two modules per character cell keeps the code square in a normal terminal.  The
    glyphs are inverted by default because a dark terminal must paint the QR's *dark*
    modules with its own background.
    """
    import qrcode

    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2)
    code.add_data(data)
    code.make(fit=True)
    matrix = code.get_matrix()
    # Two rows per line: the upper half-block and the background colour give one
    # character per two modules, which keeps the code square in a normal terminal.
    lines = []
    for y in range(0, len(matrix), 2):
        top = matrix[y]
        bottom = matrix[y + 1] if y + 1 < len(matrix) else [False] * len(matrix)
        row = []
        for x in range(len(top)):
            up, down = top[x], bottom[x]
            row.append("█" if up and down else "▀" if up else "▄" if down else " ")
        lines.append("  " + "".join(row))
    if light_terminal:
        return "\n".join(lines)
    return "\n".join(line.translate(str.maketrans("█▀▄ ", " ▄▀█")) for line in lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="werv",
        description="Control the media playing on this PC from any device on the same network.",
    )
    parser.add_argument("--port", type=int, default=5000, help="port to listen on (default: 5000)")
    parser.add_argument("--host", default="0.0.0.0", help="interface to bind (default: all)")
    parser.add_argument("--no-auth", action="store_true",
                        help="skip pairing entirely; anyone on the network can control playback")
    parser.add_argument("--demo", action="store_true",
                        help="run the simulated player instead of the real one")
    parser.add_argument("--no-qr", action="store_true", help="do not draw a QR code in the console")
    parser.add_argument("--qr-light", action="store_true",
                        help="invert the console QR code for terminals with a light background")
    parser.add_argument("--verbose", "-v", action="store_true", help="log everything")
    parser.add_argument("--version", action="version", version=f"WeRV {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    if not args.verbose:
        logging.getLogger("werkzeug").setLevel(logging.WARNING)

    backend, note = _pick_backend(args.demo)
    hub = MediaHub(backend)
    hub.start()

    import secrets
    token = None if args.no_auth else secrets.token_urlsafe(9)
    app = create_app(hub, token=token, port=args.port, quiet=not args.verbose)

    addresses = net.candidates(args.port)
    primary = addresses[0] if addresses else None
    link = primary.url(token) if primary else f"http://127.0.0.1:{args.port}/"

    print(BANNER.format(version=__version__))
    if note:
        print(f"  \033[33m!\033[0m {note}\n")
    if not args.no_qr:
        print(_console_qr(link, light_terminal=args.qr_light))
        print()
    print(f"  Scan the code, or open  \033[1;36m{link}\033[0m")
    if len(addresses) > 1:
        print(f"  \033[2mOther addresses: {', '.join(a.url(token) for a in addresses[1:3])}\033[0m")
    if token is None:
        print("  \033[33mPairing disabled -- anyone on this network can control playback.\033[0m")
    print("\n  \033[2mPress Ctrl+C to stop.\033[0m\n")

    stopping = threading.Event()

    def shutdown(*_args) -> None:
        if stopping.is_set():
            return
        stopping.set()
        print("\n  Stopping...")
        hub.close()

    signal.signal(signal.SIGINT, lambda *_: (shutdown(), sys.exit(0)))

    try:
        _serve(app, args.host, args.port)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown()
    return 0


def _serve(app, host: str, port: int) -> None:
    """Serve with waitress when it is available, Flask's own server otherwise.

    Each streaming client holds a worker for as long as it stays connected, so the
    thread pool is sized for a household of devices rather than the default handful.
    """
    try:
        from waitress import serve as waitress_serve
    except ImportError:
        app.run(host=host, port=port, debug=False, threaded=True)
        return
    # Each open event stream occupies a worker for as long as the device stays
    # connected, so the pool has to cover every phone, tablet and desktop tab at once --
    # plus the ordinary requests happening alongside them. At 24 a household with a few
    # devices and a couple of stale connections could use every thread, and then even
    # /health would not answer.
    waitress_serve(app, host=host, port=port, threads=64, channel_timeout=900,
                   connection_limit=200, ident="WeRV",
                   clear_untrusted_proxy_headers=True, _quiet=True)


if __name__ == "__main__":
    raise SystemExit(main())
