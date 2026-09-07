from __future__ import annotations

import functools
from pathlib import Path

from .canvas import RGB, Canvas

FONT_W = 4
FONT_H = 6
FONT_DIR = Path(__file__).parent / "fonts"
FONT_PATH = FONT_DIR / "4x6.bdf"
LARGE_FONT_PATH = FONT_DIR / "6x10.bdf"
LARGE_FONT_W = 6
LARGE_FONT_H = 10
FALLBACK_CHAR = "?"

# Design overrides on top of the stock BDF, for glyphs that are illegible at
# LED scale. Rows are 4-bit masks, MSB-first from the left edge of the cell.
GLYPH_OVERRIDES: dict[str, tuple[int, ...]] = {
    # Misc-Fixed fakes N's diagonal with two lone corner pixels and runs
    # neither vertical full-height. This "gate" form - both verticals with a
    # bar across the top-left - is the classic tiny-font N and stays readable
    # at 4 mm per pixel.
    "N": (0b1100, 0b1010, 0b1010, 0b1010, 0b1010, 0b0000),
}


def text_width(text: str, font: Font | None = None) -> int:
    return len(text) * (font or Font.default()).width


def max_chars(pixels: int, font: Font | None = None) -> int:
    """How many characters fit in a box this wide."""
    return max(0, pixels // (font or Font.default()).width)


class Font:
    """A parsed BDF font.

    Each glyph is a tuple of row bitmasks, MSB-first from the left edge of the
    cell. Only the bits within `width` matter.
    """

    def __init__(self, glyphs: dict[str, tuple[int, ...]],
                 width: int = FONT_W, height: int = FONT_H) -> None:
        self._glyphs = glyphs
        self.width = width
        self.height = height

    @classmethod
    @functools.lru_cache(maxsize=1)
    def default(cls) -> "Font":
        font = cls.from_bdf(FONT_PATH, width=FONT_W, height=FONT_H)
        # The overrides are hand-drawn for a 4px cell, so they belong to this
        # font only - the 6x10 face draws those glyphs legibly as shipped.
        font._glyphs.update(GLYPH_OVERRIDES)
        return font

    @classmethod
    @functools.lru_cache(maxsize=1)
    def large(cls) -> "Font":
        return cls.from_bdf(LARGE_FONT_PATH, width=LARGE_FONT_W,
                            height=LARGE_FONT_H)

    @classmethod
    def from_bdf(cls, path: Path, width: int = FONT_W,
                 height: int = FONT_H) -> "Font":
        """Parse a BDF. The cell size comes from FONTBOUNDINGBOX when the file
        declares one; `width`/`height` are the fallback."""
        glyphs: dict[str, tuple[int, ...]] = {}
        codepoint: int | None = None
        rows: list[int] | None = None

        # FONTBOUNDINGBOX precedes every glyph in a BDF, so `width` is already
        # the cell width by the time the first bitmap row is read.
        for line in path.read_text(encoding="latin-1").splitlines():
            line = line.strip()
            if line.startswith("FONTBOUNDINGBOX "):
                width, height = (int(part) for part in line.split()[1:3])
            elif line.startswith("ENCODING "):
                codepoint = int(line.split()[1])
            elif line == "BITMAP":
                rows = []
            elif line == "ENDCHAR":
                if codepoint is not None and rows is not None and 0 <= codepoint < 0x110000:
                    glyphs[chr(codepoint)] = tuple(rows)
                codepoint, rows = None, None
            elif rows is not None and line:
                # BDF pads each row to a whole number of bytes; the glyph is
                # left-aligned in the high bits.
                value = int(line, 16)
                shift = (len(line) * 4) - width
                rows.append((value >> shift) if shift > 0 else value)

        if not glyphs:
            raise ValueError(f"no glyphs parsed from {path}")
        return cls(glyphs, width=width, height=height)

    def glyph(self, char: str) -> tuple[int, ...]:
        return self._glyphs.get(char) or self._glyphs.get(FALLBACK_CHAR) or ()


def draw_text(canvas: Canvas, text: str, x: int, y: int, rgb: RGB,
              clip_x0: int | None = None, clip_x1: int | None = None,
              font: Font | None = None) -> None:
    """Draw `text` with its top-left cell corner at (x, y).

    Negative x clips rather than wrapping, which is what makes horizontal
    scrolling work. clip_x0/clip_x1 are inclusive column bounds.
    """
    font = font or Font.default()
    low = 0 if clip_x0 is None else clip_x0
    high = canvas.width - 1 if clip_x1 is None else clip_x1

    for index, char in enumerate(text):
        cell_x = x + index * font.width
        if cell_x > high or cell_x + font.width <= low:
            continue
        for row_index, bits in enumerate(font.glyph(char)):
            if row_index >= font.height:
                break
            for col in range(font.width):
                if bits & (1 << (font.width - 1 - col)):
                    px = cell_x + col
                    if low <= px <= high:
                        canvas.set_pixel(px, y + row_index, rgb)
