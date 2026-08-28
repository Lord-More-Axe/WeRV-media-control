"""Windows backend: System Media Transport Controls, plus endpoint volume.

The original drove playback by synthesising global media keys with
``win32api.keybd_event``.  That broadcasts to whatever Windows decides is listening, so
the wrong app answers as often as not, there is no way to know whether it worked, and
it cannot express anything richer than play/next/previous.

This talks to the session directly.  Windows already knows every app registered as
playing media, what each one supports, and where it is in the track, and it accepts
commands aimed at one of them in particular.  That is where seeking, shuffle, repeat,
per-app capabilities and the source picker all come from.
"""

from __future__ import annotations

import ctypes
import datetime
import logging
import re
import threading

from .media import (
    Capabilities, MediaBackend, PlayerState, Repeat, SessionRef, Status, Timeline, Track, Volume,
)

log = logging.getLogger("werv.windows")

TICKS_PER_SECOND = 10_000_000

#: Windows.Foundation.AsyncStatus.Completed
_ASYNC_COMPLETED = 1

# Windows.Media.Control.GlobalSystemMediaTransportControlsSessionPlaybackStatus
_STATUS = {
    0: Status.STOPPED,   # Closed
    1: Status.STOPPED,   # Opened -- media loaded, not started
    2: Status.PLAYING,   # Changing -- mid track switch; treat as live so the UI does not flicker
    3: Status.STOPPED,
    4: Status.PLAYING,
    5: Status.PAUSED,
}

# Windows.Media.MediaPlaybackAutoRepeatMode
_REPEAT_IN = {0: Repeat.OFF, 1: Repeat.ONE, 2: Repeat.ALL}
_REPEAT_OUT = {Repeat.OFF: 0, Repeat.ONE: 1, Repeat.ALL: 2}

#: App identifiers are not meant to be read by people.  Names we can improve, we do.
_FRIENDLY = {
    "spotify.exe": "Spotify",
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "brave.exe": "Brave",
    "opera.exe": "Opera",
    "vlc.exe": "VLC",
    "foobar2000.exe": "foobar2000",
    "musicbee.exe": "MusicBee",
    "itunes.exe": "iTunes",
    "aimp.exe": "AIMP",
    "deezer.exe": "Deezer",
    "tidal.exe": "TIDAL",
    "telegram.exe": "Telegram",
    "discord.exe": "Discord",
    "mpc-hc64.exe": "MPC-HC",
    "wmplayer.exe": "Windows Media Player",
    "microsoft.zunemusic_8wekyb3d8bbwe!microsoft.zunemusic": "Media Player",
    "microsoft.zunevideo_8wekyb3d8bbwe!microsoft.zunevideo": "Films & TV",
    "spotifyab.spotifymusic_zpdnekdrzrea0!spotify": "Spotify",
}


# Virtual key codes for the hardware media keys, used only as a last resort.
VK_PLAY_PAUSE = 0xB3
VK_NEXT_TRACK = 0xB0
VK_PREV_TRACK = 0xB1

_ULONG_PTR = ctypes.c_size_t


class _KeyboardInput(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32),
                ("dwExtraInfo", _ULONG_PTR)]


class _MouseInput(ctypes.Structure):
    # Never used, but it is the largest member of the INPUT union, so leaving it out
    # would make the struct too small and SendInput would silently reject it.
    # Windows LONG is 32-bit even on 64-bit builds, so it is spelled out as c_int32
    # rather than c_long -- which would be 64-bit under a 64-bit Unix and get the
    # layout wrong on any machine used to check this.
    _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32), ("mouseData", ctypes.c_uint32),
                ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32),
                ("dwExtraInfo", _ULONG_PTR)]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput), ("ki", _KeyboardInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("union", _InputUnion)]


def press_media_key(virtual_key: int) -> bool:
    """Synthesise a media key press.

    A handful of apps register a media session and then ignore the transport calls
    aimed at it, while still responding to the keyboard.  This is the fallback for
    those, used only after the precise call has already been refused.

    Unlike the original, which called the deprecated keybd_event once and never sent a
    matching key-up, this sends a proper down/up pair through SendInput.
    """
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SendInput.argtypes = (ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int)
        user32.SendInput.restype = ctypes.c_uint32
    except Exception:
        return False

    def event(flags: int) -> _Input:
        item = _Input()
        item.type = 1                       # INPUT_KEYBOARD
        item.union.ki = _KeyboardInput(wVk=virtual_key, wScan=0, dwFlags=flags,
                                       time=0, dwExtraInfo=0)
        return item

    KEYEVENTF_KEYUP = 0x0002
    batch = (_Input * 2)(event(0), event(KEYEVENTF_KEYUP))
    sent = user32.SendInput(2, ctypes.byref(batch), ctypes.sizeof(_Input))
    return sent == 2


def friendly_name(app_id: str) -> str:
    """Turn a raw app user model id into something worth showing on a phone."""
    if not app_id:
        return "Unknown app"
    known = _FRIENDLY.get(app_id.lower())
    if known:
        return known
    name = app_id.split("!")[-1] if "!" in app_id else app_id
    name = re.sub(r"\.exe$", "", name, flags=re.IGNORECASE)
    name = name.split(".")[-1] if "." in name else name
    name = re.sub(r"_[a-z0-9]{13}$", "", name)              # strip a UWP publisher hash
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)        # MusicBee -> Music Bee
    return name.strip() or app_id


def _import_winrt():
    """Import the WinRT bindings, preferring the maintained package.

    ``winsdk`` was the original distribution and is frozen at a pre-release; the
    per-namespace ``winrt-*`` packages superseded it with a byte-identical API. Support
    both so an existing install keeps working.
    """
    try:
        from winrt.windows.media import MediaPlaybackAutoRepeatMode
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Manager,
        )
        from winrt.windows.storage.streams import Buffer, InputStreamOptions
        try:
            # winrt.runtime, not winrt.system: the latter exists but exports only the
            # boxing helpers, so importing from it silently leaves the apartment unset.
            from winrt.runtime import ApartmentType, init_apartment

            def enter_apartment() -> None:
                init_apartment(ApartmentType.MULTI_THREADED)
        except ImportError:
            enter_apartment = None
        return Manager, Buffer, InputStreamOptions, MediaPlaybackAutoRepeatMode, enter_apartment
    except ImportError:
        from winsdk.windows.media import MediaPlaybackAutoRepeatMode
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Manager,
        )
        from winsdk.windows.storage.streams import Buffer, InputStreamOptions
        try:
            # Exposed as METH_O -- the apartment type is a required argument, not a
            # default, so calling this bare raises TypeError and initialises nothing.
            from winsdk._winrt import MTA, init_apartment

            def enter_apartment() -> None:
                init_apartment(MTA)
        except ImportError:
            enter_apartment = None
        return Manager, Buffer, InputStreamOptions, MediaPlaybackAutoRepeatMode, enter_apartment


class _Awaiter:
    """Resolve a WinRT async operation from ordinary synchronous code.

    There are three tiers here, and which one runs depends on the installed bindings.

    ``wait(timeout)`` (PyWinRT >= 3.2) is the one we want: it maps onto cppwinrt's
    ``wait_for``, which attaches a completion handler and waits on a Win32 event, then
    returns ``AsyncStatus.Started`` if the deadline passed rather than raising.  That is
    the only variant with a bounded wait -- ``get()`` is ``wait_get``, which waits
    INFINITE, so a wedged player would hold the thread forever.  ``get()`` is kept only
    as a fallback for 3.0/3.1, where nothing better exists.

    ``winsdk`` has neither, only ``__await__``.  Rather than run an event loop for it we
    drive the same mechanism by hand -- set ``completed``, wait on a ``threading.Event``
    -- because that is all ``__await__`` does anyway: PyWinRT's implementation attaches
    the identical handler and marshals the result onto a loop with
    ``call_soon_threadsafe``.  The loop was never doing any work, and it cannot express
    a timeout.

    All three block, so this must never run on an STA thread: PyWinRT guards the two
    native paths with an explicit apartment check that raises
    "Cannot call blocking method from single-threaded apartment", and the hand-rolled
    path would simply deadlock, since an STA delivers completions through a message
    pump that a Flask process does not have.
    """

    def __call__(self, operation, timeout: float = 4.0):
        # A healthy media session answers in single-digit milliseconds. The timeout only
        # exists so that one app which has stopped responding cannot hold the watcher
        # thread -- and therefore every connected phone -- indefinitely.
        waiter = getattr(operation, "wait", None)
        if callable(waiter):
            if int(waiter(timeout)) != _ASYNC_COMPLETED:
                self._abandon(operation)
                raise TimeoutError("the media session did not answer")
            return operation.get_results()

        if hasattr(operation, "completed"):
            return self._via_handler(operation, timeout)

        # PyWinRT 3.0/3.1: get() waits INFINITE. Nothing here can bound it.
        return operation.get()

    @staticmethod
    def _abandon(operation) -> None:
        """Give up on an operation without waiting for it to notice.

        ``cancel`` is a request, not a guarantee, and an SMTC call that has already
        stopped responding frequently ignores it -- so we ask once and move on rather
        than blocking a second time on the thing that just timed out.
        """
        try:
            operation.cancel()
        except Exception:
            pass

    @staticmethod
    def _via_handler(operation, timeout: float):
        """The ``winsdk`` path: the same event-and-handler dance, minus the event loop."""
        done = threading.Event()
        box: list = []

        def on_complete(op, _status):
            # Runs on an RPC pool thread. WinRT invokes it inline, on this thread, if the
            # operation has already finished by the time we attach -- so there is no gap
            # between assigning the handler and waiting in which a result could be lost.
            try:
                box.append(op.get_results())
            except BaseException as error:  # noqa: BLE001 - re-raised on the caller
                box.append(error)
            finally:
                done.set()

        operation.completed = on_complete
        if not done.wait(timeout):
            _Awaiter._abandon(operation)
            raise TimeoutError("the media session did not answer")

        result = box[0] if box else None
        if isinstance(result, BaseException):
            raise result
        return result

    def close(self) -> None:
        """Nothing to tear down -- there is no loop and no thread of our own."""


def _endpoint_volume():
    """Obtain IAudioEndpointVolume for the default playback device.

    Two traps here, both of which the widely-copied snippet falls into.  pycaw changed
    ``GetSpeakers()`` from returning a raw ``IMMDevice`` to an ``AudioDevice`` wrapper,
    which has no ``Activate``; and the interface must be taken with ``QueryInterface``
    rather than ``ctypes.cast``, because cast does not add a reference and the resulting
    double free shows up as an intermittent access violation.
    """
    import comtypes

    try:
        from pycaw.api.endpointvolume import IAudioEndpointVolume
        from pycaw.utils import AudioUtilities
    except ImportError:  # older layout
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    speakers = AudioUtilities.GetSpeakers()
    if speakers is None:
        raise RuntimeError("no default audio output device")

    wrapper = getattr(speakers, "EndpointVolume", None)
    if wrapper is not None:            # pycaw >= 20251023
        return wrapper
    interface = speakers.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
    return interface.QueryInterface(IAudioEndpointVolume)


class _Volume:
    """System volume through pycaw.

    pycaw is COM, and COM cares which thread you call it from, so every call is funnelled
    through one worker that initialises its apartment exactly once.  That also serialises
    access, which the underlying interface is not documented to tolerate concurrently.
    """

    def __init__(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="werv-volume")
        self._endpoint = None
        self._broken = False
        self._pool.submit(self._open).result(timeout=6.0)

    def _open(self) -> None:
        try:
            import comtypes

            # comtypes initialises COM on whichever thread first imports it, which -- since
            # the import above sits inside this method -- is this worker. The explicit call
            # keeps that true even if something else imported comtypes first.
            comtypes.CoInitialize()
            self._endpoint = _endpoint_volume()
            self._broken = False
        except Exception as error:
            log.info("volume control unavailable: %s", error)
            self._broken = True

    def _run(self, work, default=None, timeout: float = 2.0):
        if self._broken and self._endpoint is None:
            return default
        try:
            return self._pool.submit(work).result(timeout=timeout)
        except Exception:
            # Plugging in headphones makes the old endpoint stale, so take one shot at
            # re-acquiring the current default device before giving up.
            log.debug("volume call failed; re-acquiring endpoint", exc_info=True)
            try:
                self._pool.submit(self._open).result(timeout=timeout)
                if self._endpoint is None:
                    return default
                return self._pool.submit(work).result(timeout=timeout)
            except Exception:
                log.debug("volume unavailable", exc_info=True)
                return default

    def read(self) -> Volume:
        def work():
            return (self._endpoint.GetMasterVolumeLevelScalar(),
                    bool(self._endpoint.GetMute()))

        result = self._run(work)
        if result is None:
            return Volume(available=False)
        level, muted = result
        return Volume(level=float(level), muted=muted, available=True, scope="master")

    def set_level(self, level: float) -> bool:
        def work():
            self._endpoint.SetMasterVolumeLevelScalar(min(max(level, 0.0), 1.0), None)
            if level > 0:
                self._endpoint.SetMute(0, None)     # a nudge off zero implies unmute
            return True

        return bool(self._run(work, default=False))

    def set_muted(self, muted: bool) -> bool:
        def work():
            self._endpoint.SetMute(1 if muted else 0, None)
            return True

        return bool(self._run(work, default=False))

    def close(self) -> None:
        self._pool.shutdown(wait=False)


class WindowsBackend(MediaBackend):
    name = "windows"

    def __init__(self, *, media_key_fallback: bool = True) -> None:
        (self._Manager, self._Buffer, self._StreamOptions,
         self._RepeatMode, self._init_apartment) = _import_winrt()
        self._media_keys = media_key_fallback
        self._await = _Awaiter()
        self._lock = threading.RLock()
        self._manager = None
        self._session = None
        self._tokens: dict[str, object] = {}
        self._preferred: str | None = None      # app id the user pinned, if any
        self._properties = None                 # cached media properties
        self._properties_stale = True
        self._art_revision = 0
        self._volume: _Volume | None = None
        self.on_change = lambda: None            # replaced by the hub

    # -- lifecycle ---------------------------------------------------------------
    def start(self) -> None:
        if self._init_apartment is not None:
            try:
                # Multi-threaded: callbacks arrive on pool threads and need no message
                # loop.  A single-threaded apartment would require one, and a Flask
                # process has none, so events would simply never fire.
                self._init_apartment()
            except Exception:
                log.debug("apartment already initialised", exc_info=True)

        # The manager is long-lived. The original re-requested it on every HTTP hit,
        # which is both slow and impossible to hang events off.
        self._manager = self._await(self._Manager.request_async())
        self._tokens["current"] = self._manager.add_current_session_changed(self._on_sessions_changed)
        self._tokens["sessions"] = self._manager.add_sessions_changed(self._on_sessions_changed)
        self._rebind()

        try:
            self._volume = _Volume()
        except Exception:
            log.info("volume control unavailable", exc_info=True)
            self._volume = None

    def close(self) -> None:
        with self._lock:
            self._unbind()
            if self._manager is not None:
                for key in ("current", "sessions"):
                    token = self._tokens.pop(key, None)
                    if token is None:
                        continue
                    remove = (self._manager.remove_current_session_changed if key == "current"
                              else self._manager.remove_sessions_changed)
                    try:
                        remove(token)
                    except Exception:
                        log.debug("could not detach %s", key, exc_info=True)
        if self._volume is not None:
            self._volume.close()
        self._await.close()

    # -- session binding ---------------------------------------------------------
    def _sessions(self) -> list:
        if self._manager is None:
            return []
        try:
            return list(self._manager.get_sessions() or [])
        except Exception:
            log.debug("could not enumerate sessions", exc_info=True)
            return []

    def _choose(self):
        """The session to control: the pinned one if it still exists, else Windows' own."""
        sessions = self._sessions()
        if self._preferred:
            for session in sessions:
                if session.source_app_user_model_id == self._preferred:
                    return session
        try:
            return self._manager.get_current_session() if self._manager else None
        except Exception:
            return sessions[0] if sessions else None

    def _rebind(self) -> None:
        with self._lock:
            chosen = self._choose()
            current_id = getattr(self._session, "source_app_user_model_id", None)
            chosen_id = getattr(chosen, "source_app_user_model_id", None)
            if self._session is not None and current_id == chosen_id:
                return
            self._unbind()
            self._session = chosen
            self._properties, self._properties_stale = None, True
            if chosen is None:
                return
            try:
                self._tokens["media"] = chosen.add_media_properties_changed(self._on_media_changed)
                self._tokens["playback"] = chosen.add_playback_info_changed(self._on_state_changed)
                self._tokens["timeline"] = chosen.add_timeline_properties_changed(self._on_state_changed)
            except Exception:
                log.debug("could not subscribe to session events", exc_info=True)

    def _unbind(self) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        # The binding holds a strong reference to each handler, so failing to detach
        # keeps this session object alive for the life of the process.
        for key, remover in (("media", "remove_media_properties_changed"),
                             ("playback", "remove_playback_info_changed"),
                             ("timeline", "remove_timeline_properties_changed")):
            token = self._tokens.pop(key, None)
            if token is None:
                continue
            try:
                getattr(session, remover)(token)
            except Exception:
                log.debug("could not detach %s", key, exc_info=True)

    # -- event handlers ----------------------------------------------------------
    # These arrive on WinRT thread-pool threads. They must return immediately: anything
    # slow here stalls the dispatcher for every app on the system.
    def _on_sessions_changed(self, _sender, _args) -> None:
        try:
            self._rebind()
        except Exception:
            log.debug("rebind failed", exc_info=True)
        self.on_change()

    def _on_media_changed(self, _sender, _args) -> None:
        with self._lock:
            self._properties_stale = True
            self._art_revision += 1
        self.on_change()

    def _on_state_changed(self, _sender, _args) -> None:
        self.on_change()

    # -- reads -------------------------------------------------------------------
    def _media_properties(self):
        """Track metadata, re-read only when Windows says it changed."""
        with self._lock:
            session, stale = self._session, self._properties_stale
        if session is None:
            return None
        if not stale and self._properties is not None:
            return self._properties
        try:
            properties = self._await(session.try_get_media_properties_async())
        except Exception:
            log.debug("media properties unavailable", exc_info=True)
            return self._properties
        with self._lock:
            self._properties, self._properties_stale = properties, False
        return properties

    def poll(self) -> PlayerState:
        try:
            return self._poll()
        except Exception:
            log.debug("poll failed", exc_info=True)
            return PlayerState(status=Status.UNAVAILABLE,
                               message="Windows did not answer. It usually recovers on its own.")

    def _poll(self) -> PlayerState:
        if self._manager is None:
            return PlayerState(status=Status.UNAVAILABLE, message="Media service not started.")

        refs = []
        for session in self._sessions():
            try:
                app_id = session.source_app_user_model_id
                info = session.get_playback_info()
                refs.append(SessionRef(
                    id=app_id, name=friendly_name(app_id),
                    playing=bool(info and _STATUS.get(int(info.playback_status)) is Status.PLAYING),
                ))
            except Exception:
                continue

        with self._lock:
            session = self._session
        if session is None:
            self._rebind()
            with self._lock:
                session = self._session
        volume = self._volume.read() if self._volume else Volume(available=False)

        if session is None:
            return PlayerState(status=Status.IDLE, volume=volume, sessions=tuple(refs))

        app_id = session.source_app_user_model_id
        refs = tuple(SessionRef(r.id, r.name, r.playing, r.id == app_id) for r in refs)

        info = session.get_playback_info()
        status = _STATUS.get(int(info.playback_status), Status.STOPPED) if info else Status.IDLE
        controls = getattr(info, "controls", None)
        capabilities = self._capabilities(controls)

        properties = self._media_properties()
        track = Track()
        if properties is not None:
            track = Track(
                title=properties.title or "",
                artist=properties.artist or properties.album_artist or "",
                album=properties.album_title or "",
                track_number=int(properties.track_number or 0),
                track_count=int(properties.album_track_count or 0),
                art_key=f"{app_id}#{self._art_revision}" if properties.thumbnail else None,
            )

        # Some apps register a session and never fill it in; that is 'nothing playing',
        # not an error, and certainly not the 500 the old code produced.
        if track.empty and status is not Status.PLAYING:
            return PlayerState(status=Status.IDLE, volume=volume, sessions=refs, session_id=app_id)

        return PlayerState(
            status=status,
            track=track,
            timeline=self._timeline(session, status, info, capabilities.seek),
            capabilities=capabilities,
            volume=volume,
            shuffle=getattr(info, "is_shuffle_active", None) if info else None,
            repeat=self._repeat(info),
            sessions=refs,
            session_id=app_id,
        )

    @staticmethod
    def _capabilities(controls) -> Capabilities:
        if controls is None:
            return Capabilities(play=True, pause=True, toggle=True, next=True, previous=True)
        get = lambda name: bool(getattr(controls, name, False))  # noqa: E731
        return Capabilities(
            play=get("is_play_enabled"),
            pause=get("is_pause_enabled"),
            toggle=get("is_play_pause_toggle_enabled") or get("is_play_enabled") or get("is_pause_enabled"),
            next=get("is_next_enabled"),
            previous=get("is_previous_enabled"),
            stop=get("is_stop_enabled"),
            seek=get("is_playback_position_enabled"),
            shuffle=get("is_shuffle_enabled"),
            repeat=get("is_repeat_enabled"),
        )

    def _repeat(self, info) -> Repeat | None:
        mode = getattr(info, "auto_repeat_mode", None) if info else None
        return _REPEAT_IN.get(int(mode)) if mode is not None else None

    def _timeline(self, session, status: Status, info, seekable: bool) -> Timeline:
        try:
            properties = session.get_timeline_properties()
        except Exception:
            return Timeline()
        if properties is None:
            return Timeline()

        start = properties.start_time.total_seconds()
        end = properties.end_time.total_seconds()
        position = properties.position.total_seconds()
        duration = max(0.0, end - start)

        # Apps that never report a timeline leave the timestamp at zero, which converts
        # to the year 1601; extrapolating from that would claim four centuries of play.
        updated = properties.last_updated_time
        age = 0.0
        if updated is not None and updated.year >= 1970:
            now = datetime.datetime.now(datetime.timezone.utc)
            age = max(0.0, (now - updated).total_seconds())
            if age > 3600:                          # a stale report is worse than none
                age = 0.0

        rate = 1.0
        if info is not None and getattr(info, "playback_rate", None):
            rate = float(info.playback_rate)

        return Timeline(
            position=max(0.0, position - start),
            duration=duration,
            age=age,
            rate=rate if status is Status.PLAYING else 0.0,
            seekable=seekable and duration > 0,
        )

    def artwork(self, track: Track) -> bytes | None:
        properties = self._media_properties()
        reference = getattr(properties, "thumbnail", None) if properties else None
        if reference is None:
            return None
        try:
            stream = self._await(reference.open_read_async())
            size = int(stream.size)
            if size <= 0:
                return None
            buffer = self._Buffer(size)
            # Read from the buffer the call *returns*: the implementation is free to hand
            # back a different one than the one passed in.
            result = self._await(stream.read_async(buffer, size, self._StreamOptions.READ_AHEAD))
            return bytes(memoryview(result))
        except Exception:
            log.debug("thumbnail read failed", exc_info=True)
            return None

    # -- commands ----------------------------------------------------------------
    def _control(self, method: str, *args) -> bool:
        with self._lock:
            session = self._session
        if session is None:
            return False
        try:
            return bool(self._await(getattr(session, method)(*args)))
        except Exception:
            log.debug("%s failed", method, exc_info=True)
            return False

    def _transport(self, method: str, fallback_key: int | None) -> bool:
        """Ask the session; if it refuses, fall back to the hardware key.

        The session is always tried first, because it goes to the app the user picked
        rather than to whatever Windows decides is listening.
        """
        if self._control(method):
            return True
        if fallback_key is None or not self._media_keys:
            return False
        log.debug("%s refused; falling back to the media key", method)
        return press_media_key(fallback_key)

    def play(self) -> bool: return self._transport("try_play_async", VK_PLAY_PAUSE)
    def pause(self) -> bool: return self._transport("try_pause_async", VK_PLAY_PAUSE)
    def toggle(self) -> bool: return self._transport("try_toggle_play_pause_async", VK_PLAY_PAUSE)
    def stop(self) -> bool: return self._control("try_stop_async")
    def next(self) -> bool: return self._transport("try_skip_next_async", VK_NEXT_TRACK)
    def previous(self) -> bool: return self._transport("try_skip_previous_async", VK_PREV_TRACK)

    def seek(self, seconds: float) -> bool:
        with self._lock:
            session = self._session
        if session is None:
            return False
        try:
            properties = session.get_timeline_properties()
            start = properties.start_time.total_seconds() if properties else 0.0
            target = seconds + start
            if properties is not None:
                lower = properties.min_seek_time.total_seconds()
                upper = properties.max_seek_time.total_seconds()
                if upper > lower:
                    target = min(max(target, lower), upper)
        except Exception:
            target = seconds
        # The position is an Int64 count of 100-nanosecond ticks.
        return self._control("try_change_playback_position_async",
                             int(round(target * TICKS_PER_SECOND)))

    def set_shuffle(self, on: bool) -> bool:
        return self._control("try_change_shuffle_active_async", bool(on))

    def set_repeat(self, mode: Repeat) -> bool:
        return self._control("try_change_auto_repeat_mode_async",
                             self._RepeatMode(_REPEAT_OUT[mode]))

    def set_volume(self, level: float) -> bool:
        return self._volume.set_level(level) if self._volume else False

    def set_muted(self, muted: bool) -> bool:
        return self._volume.set_muted(muted) if self._volume else False

    def select_session(self, session_id: str | None) -> bool:
        with self._lock:
            self._preferred = session_id or None
        self._rebind()
        self.on_change()
        return True
