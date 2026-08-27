from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Bitmap:
    """A small sprite. `None` pixels are transparent."""

    width: int
    height: int
    pixels: tuple[RGB | None, ...]

    @classmethod
    def from_art(cls, art: Sequence[str], palette: dict[str, RGB]) -> "Bitmap":
        """Build from string art. '.' is always transparent."""
        if not art:
            raise ValueError("art must have at least one row")
        width = len(art[0])
        if any(len(row) != width for row in art):
            raise ValueError("every art row must be the same width")
        pixels: list[RGB | None] = []
        for row in art:
            for char in row:
                if char == ".":
                    pixels.append(None)
                elif char in palette:
                    pixels.append(palette[char])
                else:
                    raise ValueError(f"art character {char!r} is not in the palette")
        return cls(width=width, height=len(art), pixels=tuple(pixels))


class Canvas:
    """A mutable RGB framebuffer. Writes outside the bounds are dropped."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._buf = bytearray(width * height * 3)

    def clear(self) -> None:
        self._buf[:] = bytes(len(self._buf))

    def set_pixel(self, x: int, y: int, rgb: RGB) -> None:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        offset = (y * self.width + x) * 3
        self._buf[offset:offset + 3] = bytes(rgb)

    def get_pixel(self, x: int, y: int) -> RGB:
        offset = (y * self.width + x) * 3
        return tuple(self._buf[offset:offset + 3])  # type: ignore[return-value]

    def hline(self, y: int, x0: int, x1: int, rgb: RGB) -> None:
        """Inclusive on both ends."""
        for x in range(max(0, min(x0, x1)), min(self.width - 1, max(x0, x1)) + 1):
            self.set_pixel(x, y, rgb)

    def blit(self, bitmap: Bitmap, x: int, y: int) -> None:
        for row in range(bitmap.height):
            for col in range(bitmap.width):
                pixel = bitmap.pixels[row * bitmap.width + col]
                if pixel is not None:
                    self.set_pixel(x + col, y + row, pixel)

    def dim(self, factor: float) -> None:
        """Scale every channel. Used to signal a stale AIS feed."""
        self._buf[:] = bytes(int(value * factor) for value in self._buf)

    def to_bytes(self) -> bytes:
        return bytes(self._buf)
