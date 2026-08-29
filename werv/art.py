"""Album art: content-addressed caching, downscaling, and palette extraction.

The original implementation wrote a PNG into ``static/images/tracks/`` on every poll --
once per second, per connected client -- naming it after the first three characters of
the track title, and ran a ``/delete`` endpoint every ten seconds that emptied the whole
directory.  That raced with itself, collided on titles sharing a prefix, and let any
title containing a path separator escape the directory.

Here art never touches the disk.  Bytes are hashed, kept in a bounded in-memory cache and
served from ``/art/<hash>`` with an immutable cache header, so each phone fetches a given
cover exactly once.  While we have the bitmap open we also pull a small colour palette out
of it, which is what lets the interface repaint itself around whatever is playing.
"""

from __future__ import annotations

import colorsys
import hashlib
import io
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

from PIL import Image

# Covers arrive at anything from 64px to 3000px square.  640 is more than any phone can
# show and keeps a re-encode under ~80 KB on the LAN.
MAX_EDGE = 640
JPEG_QUALITY = 86

# Bounded so a long listening session cannot grow the process without limit.
CACHE_BYTES = 24 * 1024 * 1024


def _luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG 2.1 relative luminance."""
    channels = []
    for value in rgb:
        c = value / 255
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = _luminance(a), _luminance(b)
    lo, hi = sorted((la, lb))
    return (hi + 0.05) / (lo + 0.05)


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def _rgb(h: float, l: float, s: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hls_to_rgb(h % 1.0, min(max(l, 0.0), 1.0), min(max(s, 0.0), 1.0))
    return (round(r * 255), round(g * 255), round(b * 255))


def _hls(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    return colorsys.rgb_to_hls(*[c / 255 for c in rgb])


def _hue_gap(a: float, b: float) -> float:
    """Distance between two hues on the colour wheel, in turns (0.5 is opposite)."""
    gap = abs(a - b) % 1.0
    return min(gap, 1.0 - gap)


@dataclass(frozen=True)
class Palette:
    """Colours the interface themes itself with.

    ``base`` is deliberately forced very dark rather than tracking the artwork's own
    lightness.  A pale cover would otherwise produce a pale page and there is no safe
    text colour on it; anchoring to a tinted near-black means white text always clears
    WCAG AA and the artwork still tints the whole screen.
    """

    base: str        # page background: near-black, tinted by the cover
    accent: str      # progress fill, active controls
    on_accent: str   # text/icon colour that is legible on `accent`
    glow: list[str] = field(default_factory=list)  # ambient gradient blobs

    def as_dict(self) -> dict:
        return {"base": self.base, "accent": self.accent, "onAccent": self.on_accent, "glow": self.glow}


#: Used before any artwork arrives, and for tracks that have none.
DEFAULT_PALETTE = Palette(
    base="#0b0b0f",
    accent="#8b7cf6",
    on_accent="#0b0b0f",
    glow=["#4c3fa8", "#7b2d6b", "#1e3a6e"],
)


def _swatches(image: Image.Image, count: int = 16) -> list[tuple[int, tuple[int, int, int]]]:
    """Quantised colours from the image, most-used first."""
    small = image.convert("RGB")
    small.thumbnail((72, 72), Image.Resampling.BILINEAR)
    quantised = small.quantize(colors=count, method=Image.Quantize.FASTOCTREE)
    table = quantised.getpalette() or []
    out = []
    for population, index in quantised.getcolors() or []:
        rgb = tuple(table[index * 3 : index * 3 + 3])
        if len(rgb) == 3:
            out.append((population, rgb))
    out.sort(reverse=True, key=lambda item: item[0])
    return out


def extract_palette(image: Image.Image) -> Palette:
    """Derive a usable theme from a cover.

    Picking the *most common* colour alone gives washed-out results: many covers are
    mostly black or mostly cream.  We score on saturation and mid-lightness as well as
    coverage, then normalise the winner into a band that is guaranteed to read well,
    so even a monochrome sleeve yields a lively accent instead of grey-on-grey.
    """
    swatches = _swatches(image)
    if not swatches:
        return DEFAULT_PALETTE

    total = sum(population for population, _ in swatches) or 1
    scored = []
    for population, rgb in swatches:
        hue, light, sat = _hls(rgb)
        coverage = population / total
        # Near-black and blown-out whites carry no hue worth using.
        usable = 1.0 if 0.12 <= light <= 0.88 else 0.15
        scored.append((coverage ** 0.5 * (0.25 + sat) * usable, hue, light, sat, rgb))
    scored.sort(reverse=True, key=lambda item: item[0])

    _, hue, light, sat, _ = scored[0]
    dominant_hue = _hls(swatches[0][1])[0]
    base_rgb = _rgb(dominant_hue, 0.055, min(sat, 0.45) * 0.7)

    if sat < 0.10:
        # Genuinely greyscale artwork.  The hue of a near-grey swatch is numerical noise,
        # so inventing a colour from it looks arbitrary; a bright neutral is honest.
        accent_rgb = (236, 236, 240)
    else:
        accent_rgb = _rgb(hue, min(max(light, 0.52), 0.68), max(sat, 0.55))
        # The accent carries the scrubber fill and active icons against `base`; lift it
        # until it clears WCAG AA so it stays readable on very dark, saturated artwork.
        lift = min(max(light, 0.52), 0.68)
        while contrast_ratio(accent_rgb, base_rgb) < 4.5 and lift < 0.86:
            lift += 0.03
            accent_rgb = _rgb(hue, lift, max(sat, 0.55))

    # Hues for the three ambient fields behind the page.  Chasing maximum separation is
    # faithful to the histogram but looks muddy: the third-most-distinct hue is usually a
    # low-coverage accident of JPEG chroma, and pairing it with the dominant one gives
    # the olive-and-red kind of clash nobody would choose. Staying inside an arc of the
    # anchor keeps the wash reading as one lit room.
    anchor = hue if sat >= 0.10 else dominant_hue
    lightness = (0.34, 0.28, 0.37)
    glow: list[str] = []
    used_hues: list[float] = []
    for _, candidate_hue, _, candidate_sat, _ in scored:
        if _hue_gap(candidate_hue, anchor) > 0.19:            # beyond ~68 degrees
            continue
        if any(_hue_gap(candidate_hue, h) < 0.035 for h in used_hues):
            continue
        used_hues.append(candidate_hue)
        glow.append(_hex(_rgb(candidate_hue, lightness[len(glow)], max(candidate_sat, 0.45))))
        if len(glow) == 3:
            break
    for offset in (-0.06, 0.09, 0.15):                        # fill out a flat cover
        if len(glow) >= 3:
            break
        glow.append(_hex(_rgb(anchor + offset, lightness[len(glow)], max(sat, 0.42))))

    white, black = (255, 255, 255), (0, 0, 0)
    on_accent = white if contrast_ratio(accent_rgb, white) >= contrast_ratio(accent_rgb, black) else black

    return Palette(base=_hex(base_rgb), accent=_hex(accent_rgb), on_accent=_hex(on_accent), glow=glow)


@dataclass(frozen=True)
class Artwork:
    key: str
    data: bytes
    content_type: str
    palette: Palette
    width: int
    height: int


class ArtCache:
    """Bounded, thread-safe, content-addressed store of processed cover art."""

    def __init__(self, max_bytes: int = CACHE_BYTES) -> None:
        self._items: OrderedDict[str, Artwork] = OrderedDict()
        self._lock = threading.Lock()
        self._max_bytes = max_bytes
        self._bytes = 0

    def get(self, key: str) -> Artwork | None:
        with self._lock:
            art = self._items.get(key)
            if art is not None:
                self._items.move_to_end(key)
            return art

    def put(self, raw: bytes) -> Artwork | None:
        """Ingest raw thumbnail bytes; returns the processed artwork, or None if undecodable.

        Keyed by a hash of the *source* bytes, so an unchanged cover is recognised
        without re-decoding it and the URL stays stable across track re-selects.
        """
        key = hashlib.sha256(raw).hexdigest()[:16]
        existing = self.get(key)
        if existing is not None:
            return existing

        try:
            image = Image.open(io.BytesIO(raw))
            image.load()
        except Exception:
            return None

        source_type = Image.MIME.get(image.format or "", "image/jpeg")
        image = image.convert("RGB")
        palette = extract_palette(image)

        if max(image.size) > MAX_EDGE:
            # Covers arrive as large as 3000px square. Re-encoding once here saves every
            # phone on the network from downloading a megabyte it cannot display.
            image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
            data, content_type = buffer.getvalue(), "image/jpeg"
        else:
            data, content_type = raw, source_type

        art = Artwork(key, data, content_type, palette, *image.size)
        with self._lock:
            self._items[key] = art
            self._items.move_to_end(key)
            self._bytes += len(data)
            while self._bytes > self._max_bytes and len(self._items) > 1:
                _, evicted = self._items.popitem(last=False)
                self._bytes -= len(evicted.data)
        return art
