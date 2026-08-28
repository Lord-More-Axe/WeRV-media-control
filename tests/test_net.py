"""Finding an address the phone can actually reach, and encoding it into a QR."""

from __future__ import annotations

import ipaddress

from werv import net


def test_candidates_are_offered_best_first():
    """The original used gethostbyname(gethostname()), which answers 127.0.0.1 on many
    machines and picks the VPN adapter on others. Loopback must never be the one we
    print, unless it is genuinely all we have."""
    found = net.candidates(5000)
    assert found
    assert found[0].primary
    assert sum(1 for a in found if a.primary) == 1
    if len(found) > 1:
        assert found[-1].ip == "127.0.0.1"


def test_every_candidate_is_a_real_address():
    for address in net.candidates(5000):
        ipaddress.ip_address(address.ip)
        assert address.port == 5000


def test_urls_carry_the_pairing_key():
    address = net.candidates(5000)[0]
    assert address.url().endswith(":5000/")
    assert address.url("abc123").endswith("?k=abc123")


def test_ranking_prefers_the_lan_over_loopback_and_link_local():
    order = sorted(["127.0.0.1", "169.254.9.9", "192.168.1.10", "172.17.0.2"], key=net._rank)
    assert order[0] == "192.168.1.10"          # a real home network
    assert order[-1] == "127.0.0.1"
    assert order.index("172.17.0.2") < order.index("169.254.9.9")


def test_qr_is_self_contained_svg():
    """Rendered inline rather than written to qr_code.png and opened in an image viewer."""
    svg = net.qr_svg("http://192.168.1.5:5000/?k=abc")
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "<image" not in svg and "http://www.w3.org/2000/svg" in svg
    assert svg.count("<rect") > 20


def test_qr_encodes_what_it_was_given():
    import qrcode

    link = "http://192.168.1.5:5000/?k=abcdef"
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2)
    code.add_data(link)
    code.make(fit=True)
    size = len(code.get_matrix())
    assert f'viewBox="0 0 {size} {size}"' in net.qr_svg(link)


def test_adjacent_modules_are_merged():
    """One rect per run rather than per module, which roughly halves the markup."""
    svg = net.qr_svg("http://192.168.1.5:5000/")
    assert 'width="1"' in svg          # single modules still exist
    assert any(f'width="{n}"' in svg for n in range(2, 8))
