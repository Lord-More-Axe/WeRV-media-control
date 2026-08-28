"""Network discovery and pairing helpers.

The original WeRV resolved its address with ``socket.gethostbyname(socket.gethostname())``,
which routinely answers ``127.0.0.1`` (Linux/WSL hosts, some Windows configurations) or
picks a VPN / Hyper-V / VirtualBox adapter that the phone cannot reach.  We instead ask
the routing table which source address would be used to reach the outside world, then
offer every other plausible candidate as a fallback so the user can pick.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass

# Adapters handed out by hypervisors and VPNs.  A phone on the house Wi-Fi cannot route
# to these, so they sort last rather than being hidden entirely -- occasionally one of
# them really is the LAN.
_VIRTUAL_HINTS = (
    ipaddress.ip_network("172.16.0.0/12"),   # Docker / Hyper-V default switch
    ipaddress.ip_network("192.168.56.0/24"),  # VirtualBox host-only
)


@dataclass(frozen=True)
class Address:
    """One candidate address the remote can be reached on."""

    ip: str
    port: int
    primary: bool = False

    def url(self, token: str | None = None) -> str:
        base = f"http://{self.ip}:{self.port}/"
        return f"{base}?k={token}" if token else base


def _route_source_ip() -> str | None:
    """Source address the OS would use to reach the internet.

    Uses a connected UDP socket: no packet is ever sent, the kernel just consults the
    routing table, so this works fine on a network with no internet access.
    """
    for probe in ("8.8.8.8", "1.1.1.1", "192.168.1.1"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(0.25)
            sock.connect((probe, 9))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("0."):
                return ip
        except OSError:
            continue
        finally:
            sock.close()
    return None


def _host_ips() -> list[str]:
    """Every IPv4 address the host admits to having."""
    found: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found:
                found.append(ip)
    except OSError:
        pass
    return found


def _rank(ip: str) -> tuple[int, str]:
    """Sort key -- lower is better."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return (9, ip)
    if addr.is_loopback:
        return (8, ip)
    if addr.is_link_local:  # 169.254.x -- DHCP failed
        return (7, ip)
    if any(addr in net for net in _VIRTUAL_HINTS):
        return (5, ip)
    if addr.is_private:
        return (1, ip)
    return (3, ip)  # public address: unusual for a home PC but routable


def candidates(port: int) -> list[Address]:
    """All addresses the remote might be reachable on, best first.

    The first entry is the one we print and encode into the QR code; the rest are shown
    on the desktop page so a user behind a VPN can try another.
    """
    preferred = _route_source_ip()
    seen: dict[str, None] = {}
    for ip in ([preferred] if preferred else []) + _host_ips() + ["127.0.0.1"]:
        if ip:
            seen.setdefault(ip, None)

    ips = sorted(seen, key=lambda ip: (0 if ip == preferred else 1, *_rank(ip)))
    return [Address(ip=ip, port=port, primary=(i == 0)) for i, ip in enumerate(ips)]


def qr_svg(data: str, *, quiet_zone: int = 2, fg: str = "#000", bg: str = "#fff") -> str:
    """Render ``data`` as a self-contained SVG QR code.

    Emitting SVG rather than the original's PNG-on-disk means nothing is written to the
    working directory, it stays crisp at any size, and it can be inlined straight into
    the desktop page.  Horizontally adjacent modules are merged into single rects, which
    roughly halves the markup.
    """
    import qrcode

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=quiet_zone)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    size = len(matrix)

    rects: list[str] = []
    for y, row in enumerate(matrix):
        x = 0
        while x < size:
            if not row[x]:
                x += 1
                continue
            run = x
            while run < size and row[run]:
                run += 1
            rects.append(f'<rect x="{x}" y="{y}" width="{run - x}" height="1"/>')
            x = run

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'shape-rendering="crispEdges" role="img" aria-label="Pairing QR code">'
        f'<rect width="{size}" height="{size}" fill="{bg}"/>'
        f'<g fill="{fg}">{"".join(rects)}</g></svg>'
    )
