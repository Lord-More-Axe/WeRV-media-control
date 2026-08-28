from . import endpoint_state


class _EndpointVolume:
    def GetMasterVolumeLevelScalar(self): return endpoint_state["level"]
    def SetMasterVolumeLevelScalar(self, level, context): endpoint_state["level"] = float(level)
    def GetMute(self): return 1 if endpoint_state["muted"] else 0
    def SetMute(self, muted, context): endpoint_state["muted"] = bool(muted)


class AudioDevice:
    """The wrapper pycaw >= 20251023 returns -- deliberately WITHOUT .Activate."""
    FriendlyName = "Speakers"
    EndpointVolume = _EndpointVolume()


class AudioUtilities:
    @staticmethod
    def GetSpeakers(): return AudioDevice()
