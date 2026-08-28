"""Mock of Windows.Media.Control matching the verified winsdk/winrt stub signatures.

Signatures copied from the generated .pyi: static request_async, sync get_current_session /
get_sessions, (sender, args) event handlers returning a token, IAsyncOperation with .get().
"""
import datetime, enum, itertools

class GlobalSystemMediaTransportControlsSessionPlaybackStatus(enum.IntEnum):
    CLOSED = 0; OPENED = 1; CHANGING = 2; STOPPED = 3; PLAYING = 4; PAUSED = 5

class _Op:
    """Stands in for IAsyncOperation[T] -- the maintained bindings expose .get()."""
    def __init__(self, value): self._value = value
    def get(self): return self._value
    def __await__(self):
        if False: yield
        return self._value

class _Token:
    _ids = itertools.count(1)
    def __init__(self): self.value = next(self._ids)

class Controls:
    def __init__(self, **kw):
        for name in ("play","pause","play_pause_toggle","stop","next","previous","fast_forward",
                     "rewind","record","shuffle","repeat","playback_position","playback_rate",
                     "channel_up","channel_down"):
            setattr(self, f"is_{name}_enabled", kw.get(name, False))

class PlaybackInfo:
    def __init__(self, status, controls, shuffle=None, repeat=None, rate=None):
        self.playback_status = status; self.controls = controls
        self.is_shuffle_active = shuffle; self.auto_repeat_mode = repeat
        self.playback_rate = rate; self.playback_type = None

class TimelineProperties:
    def __init__(self, start, end, position, updated, min_seek=None, max_seek=None):
        td = datetime.timedelta
        self.start_time = td(seconds=start); self.end_time = td(seconds=end)
        self.position = td(seconds=position)
        self.min_seek_time = td(seconds=min_seek if min_seek is not None else start)
        self.max_seek_time = td(seconds=max_seek if max_seek is not None else end)
        self.last_updated_time = updated

class MediaProperties:
    def __init__(self, title="", artist="", album_title="", album_artist="", subtitle="",
                 track_number=0, album_track_count=0, thumbnail=None):
        self.title=title; self.artist=artist; self.album_title=album_title
        self.album_artist=album_artist; self.subtitle=subtitle
        self.track_number=track_number; self.album_track_count=album_track_count
        self.genres=None; self.playback_type=None; self.thumbnail=thumbnail

class _Stream:
    def __init__(self, data): self._d = data; self.size = len(data); self.content_type="image/jpeg"
    def read_async(self, buffer, count, options):
        assert count <= buffer.capacity, "count must be <= capacity"
        from winrt.windows.storage.streams import Buffer as B
        out = B(capacity=count, data=self._d[:count])
        return _Op(out)                       # deliberately NOT the input buffer

class StreamReference:
    def __init__(self, data): self._d = data
    def open_read_async(self): return _Op(_Stream(self._d))

CALLS = []

class GlobalSystemMediaTransportControlsSession:
    def __init__(self, app_id, info, timeline, props):
        self.source_app_user_model_id = app_id
        self._info = info; self._timeline = timeline; self._props = props
        self.handlers = {}
    def get_playback_info(self): return self._info
    def get_timeline_properties(self): return self._timeline
    def try_get_media_properties_async(self): return _Op(self._props)
    def _add(self, kind, h): self.handlers[kind] = h; return _Token()
    def add_media_properties_changed(self, h, /): return self._add("media", h)
    def add_playback_info_changed(self, h, /): return self._add("playback", h)
    def add_timeline_properties_changed(self, h, /): return self._add("timeline", h)
    def remove_media_properties_changed(self, t, /): self.handlers.pop("media", None)
    def remove_playback_info_changed(self, t, /): self.handlers.pop("playback", None)
    def remove_timeline_properties_changed(self, t, /): self.handlers.pop("timeline", None)
    def _cmd(self, name, *a): CALLS.append((self.source_app_user_model_id, name, a)); return _Op(True)
    def try_play_async(self): return self._cmd("play")
    def try_pause_async(self): return self._cmd("pause")
    def try_stop_async(self): return self._cmd("stop")
    def try_toggle_play_pause_async(self): return self._cmd("toggle")
    def try_skip_next_async(self): return self._cmd("next")
    def try_skip_previous_async(self): return self._cmd("previous")
    def try_change_shuffle_active_async(self, state, /): return self._cmd("shuffle", state)
    def try_change_auto_repeat_mode_async(self, mode, /): return self._cmd("repeat", mode)
    def try_change_playback_position_async(self, ticks, /): return self._cmd("seek", ticks)

class GlobalSystemMediaTransportControlsSessionManager:
    instance = None
    def __init__(self): self._sessions = []; self._current = None; self.handlers = {}
    @staticmethod
    def request_async():
        m = GlobalSystemMediaTransportControlsSessionManager.instance
        return _Op(m)
    def get_current_session(self): return self._current
    def get_sessions(self): return list(self._sessions)
    def add_current_session_changed(self, h, /): self.handlers["current"]=h; return _Token()
    def remove_current_session_changed(self, t, /): self.handlers.pop("current", None)
    def add_sessions_changed(self, h, /): self.handlers["sessions"]=h; return _Token()
    def remove_sessions_changed(self, t, /): self.handlers.pop("sessions", None)
