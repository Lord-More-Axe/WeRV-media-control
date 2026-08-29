"""Where the pairing key lives between runs.

Minting a new key on every launch would mean every phone in the house is bounced back
to the pairing screen each time the PC reboots -- which makes "scan once" untrue and
turns the QR code into a chore rather than a setup step. The key is therefore kept in
the user's own config directory, readable only by them, and reused until they ask for
a new one.
"""

from __future__ import annotations

import logging
import os
import secrets
import stat
from pathlib import Path

log = logging.getLogger("werv.pairing")

#: Long enough that guessing is hopeless, short enough to read off a screen and type.
KEY_BYTES = 9


def key_path() -> Path:
    """The file the pairing key is kept in, following each platform's convention."""
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(root) / "WeRV" / "pairing-key"
    root = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(root) / "werv" / "pairing-key"


def _read(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    # Anything that is not a key we would have written is treated as absent rather than
    # trusted -- a truncated write or a hand-edited file should not become a weak key.
    if len(value) < 8 or not value.replace("-", "").replace("_", "").isalnum():
        return None
    return value


def _write(path: Path, key: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key, encoding="ascii")
        # Best effort: on Windows this is a no-op in practice, and the file sits in the
        # user's own profile either way.
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return True
    except OSError as error:
        log.info("could not store the pairing key at %s: %s", path, error)
        return False


def load_or_create(*, rotate: bool = False) -> tuple[str, Path | None]:
    """Return the pairing key, and where it is stored -- or None if it could not be.

    A key that cannot be written to disk still works; it just lasts for this run, and
    the caller is expected to say so rather than promise otherwise.
    """
    path = key_path()
    if not rotate:
        existing = _read(path)
        if existing:
            return existing, path

    key = secrets.token_urlsafe(KEY_BYTES)
    return key, (path if _write(path, key) else None)


def forget() -> bool:
    """Discard the stored key, so every paired device has to pair again."""
    try:
        key_path().unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False
