"""Finding an address the phone can actually reach, and encoding it into a QR."""

from __future__ import annotations

import ipaddress

import pytest

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


def test_adapter_names_deprioritise_virtual_interfaces():
    """A VPN handing out a 192.168.x address is indistinguishable by IP alone.

    Only the adapter name gives it away, which is why ranking takes it into account.
    """
    real = net._rank("192.168.1.10", "Wi-Fi")
    vpn = net._rank("192.168.1.11", "OpenVPN TAP-Windows Adapter V9")
    wsl = net._rank("192.168.1.12", "vEthernet (WSL)")
    assert real < vpn and real < wsl


def test_adapter_ranking_still_puts_loopback_last():
    ranked = sorted([("127.0.0.1", "Loopback Pseudo-Interface 1"),
                     ("192.168.1.10", "Wi-Fi"),
                     ("172.20.0.1", "vEthernet (Default Switch)")],
                    key=lambda pair: net._rank(*pair))
    assert ranked[0][0] == "192.168.1.10"
    assert ranked[-1][0] == "127.0.0.1"


def test_addresses_carry_a_label_for_the_pairing_sheet():
    for address in net.candidates(5000):
        assert address.label()          # never blank: falls back to the IP


def test_enumeration_survives_a_missing_psutil(monkeypatch):
    """psutil arrives with pycaw on Windows, but must not be required."""
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("no psutil")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    assert net._adapter_ips() == {}
    assert net.candidates(5000)          # still finds something to offer


@pytest.mark.parametrize("wildcard", ["0.0.0.0", "::", "", None])
def test_a_wildcard_bind_leaves_discovery_free(wildcard):
    assert len(net.candidates(5000, bind=wildcard)) >= 1


@pytest.mark.parametrize("bound", ["127.0.0.1", "192.168.1.20", "10.0.0.5"])
def test_binding_one_interface_advertises_only_that_address(bound):
    """The QR has to point at something this process is actually listening on.

    Binding 127.0.0.1 used to still advertise a LAN address, and binding one NIC while a
    VPN owned the default route put the VPN's address in the QR code.
    """
    found = net.candidates(5000, bind=bound)
    assert [a.ip for a in found] == [bound]
    assert found[0].primary
    assert found[0].url("abc").startswith(f"http://{bound}:5000/")
