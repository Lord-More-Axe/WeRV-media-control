"""Album art cache and the palette the interface themes itself from."""

from __future__ import annotations

import io
import random

import pytest
from PIL import Image, ImageDraw

from werv.art import MAX_EDGE, ArtCache, contrast_ratio, extract_palette


def _rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def _cover(seed: int, *, style: str = "colour", size: int = 900) -> bytes:
    rng = random.Random(seed)
    base = {"pale": (245, 242, 236), "grey": (28, 28, 30), "dark": (5, 5, 8)}.get(
        style, (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)))
    image = Image.new("RGB", (size, size), base)
    draw = ImageDraw.Draw(image)
    for _ in range(9):
        x, y = rng.randint(-200, size), rng.randint(-200, size)
        w, h = rng.randint(size // 6, size), rng.randint(size // 6, size)
        if style == "pale":
            fill = (rng.randint(214, 255), rng.randint(214, 255), rng.randint(210, 250))
        elif style == "grey":
            level = rng.randint(20, 90)
            fill = (level, level, level)
        elif style == "dark":
            fill = (rng.randint(0, 50), rng.randint(0, 50), rng.randint(0, 70))
        else:
            fill = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
        draw.ellipse([x, y, x + w, y + h], fill=fill)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


@pytest.mark.parametrize("style", ["colour", "pale", "grey", "dark"])
@pytest.mark.parametrize("seed", [1, 7, 23])
def test_text_always_has_contrast(style, seed):
    """The whole point of anchoring the background to a tinted near-black.

    A palette taken naively from the artwork puts white text on a cream sleeve. Whatever
    the cover, white on the page background must clear WCAG AA, and so must the accent
    that carries the scrubber and the active controls.
    """
    palette = extract_palette(Image.open(io.BytesIO(_cover(seed, style=style))))
    base = _rgb(palette.base)
    assert contrast_ratio(base, (255, 255, 255)) >= 4.5
    assert contrast_ratio(base, _rgb(palette.accent)) >= 4.4
    assert contrast_ratio(_rgb(palette.accent), _rgb(palette.on_accent)) >= 4.5


def test_greyscale_art_is_not_given_an_invented_hue():
    """The hue of a near-grey swatch is numerical noise, not a design decision."""
    palette = extract_palette(Image.open(io.BytesIO(_cover(5, style="grey"))))
    red, green, blue = _rgb(palette.accent)
    assert max(red, green, blue) - min(red, green, blue) < 24


def test_ambient_colours_stay_related():
    """Three maximally distinct hues read as a clash; an arc reads as one lit room."""
    import colorsys

    palette = extract_palette(Image.open(io.BytesIO(_cover(11))))
    hues = [colorsys.rgb_to_hls(*[c / 255 for c in _rgb(g)])[0] for g in palette.glow]
    spread = max(min(abs(a - b), 1 - abs(a - b)) for a in hues for b in hues)
    assert spread <= 0.42
    assert len(palette.glow) == 3


def test_large_covers_are_downscaled_for_the_phone():
    cache = ArtCache()
    art = cache.put(_cover(3, size=1800))
    assert max(art.width, art.height) <= MAX_EDGE
    assert art.content_type == "image/jpeg"


def test_small_covers_are_passed_through_untouched():
    raw = _cover(3, size=300)
    art = ArtCache().put(raw)
    assert art.data == raw


def test_identical_bytes_reuse_one_url():
    """A stable, content-addressed URL is what makes immutable caching safe."""
    cache = ArtCache()
    first, second = cache.put(_cover(9)), cache.put(_cover(9))
    assert first.key == second.key
    assert cache.get(first.key) is first


def test_undecodable_data_is_declined_not_raised():
    assert ArtCache().put(b"this is not an image") is None


def test_the_cache_is_bounded():
    """A long listening session must not grow the process without limit."""
    cache = ArtCache(max_bytes=120_000)
    keys = [cache.put(_cover(seed, size=700)).key for seed in range(12)]
    assert cache.get(keys[-1]) is not None
    assert cache.get(keys[0]) is None          # the oldest was evicted
