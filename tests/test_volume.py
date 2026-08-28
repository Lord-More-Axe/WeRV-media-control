"""System volume, including the two pycaw traps that only bite at runtime."""

from __future__ import annotations

import pytest


def test_volume_is_read_from_the_endpoint(backend):
    volume = backend.poll().volume
    assert volume.available
    assert volume.scope == "master"
    assert volume.level == pytest.approx(0.5)
    assert volume.muted is False


def test_setting_volume_reaches_the_endpoint(backend):
    from pycaw import endpoint_state

    assert backend.set_volume(0.31)
    assert endpoint_state["level"] == pytest.approx(0.31)
    assert backend.poll().volume.level == pytest.approx(0.31)


def test_volume_is_clamped(backend):
    from pycaw import endpoint_state

    backend.set_volume(5.0)
    assert endpoint_state["level"] == pytest.approx(1.0)
    backend.set_volume(-2.0)
    assert endpoint_state["level"] == pytest.approx(0.0)


def test_raising_volume_lifts_mute(backend):
    from pycaw import endpoint_state

    backend.set_muted(True)
    assert endpoint_state["muted"] is True
    backend.set_volume(0.4)
    assert endpoint_state["muted"] is False


def test_mute_round_trips(backend):
    assert backend.set_muted(True)
    assert backend.poll().volume.muted is True
    assert backend.set_muted(False)
    assert backend.poll().volume.muted is False


def test_endpoint_uses_the_modern_pycaw_contract(winrt):
    """GetSpeakers() returns a wrapper with no .Activate on current pycaw.

    The snippet in every tutorial calls .Activate on the result, which raises
    AttributeError on any pycaw from late 2025 onward. The fake mimics that shape
    exactly, so this test fails loudly if the old snippet ever creeps back in.
    """
    from pycaw.utils import AudioUtilities

    from werv.windows import _endpoint_volume

    assert not hasattr(AudioUtilities.GetSpeakers(), "Activate")
    assert _endpoint_volume() is not None


def test_com_is_initialised_on_the_volume_thread(backend):
    """comtypes only initialises COM on the thread that imports it."""
    import comtypes

    assert comtypes.initialised


def test_a_dead_endpoint_degrades_quietly(backend, monkeypatch):
    from pycaw.utils import _EndpointVolume

    def boom(*_args):
        raise OSError("device disconnected")

    monkeypatch.setattr(_EndpointVolume, "GetMasterVolumeLevelScalar", boom)
    monkeypatch.setattr(_EndpointVolume, "GetMute", boom)
    # Unplugging the speakers must not take the whole remote down with it.
    assert backend.poll().volume.available is False
