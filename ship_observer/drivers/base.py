from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DisplayDriver(Protocol):
    width: int
    height: int

    def show(self, frame: bytes) -> None:
        """Present one frame. `frame` is width*height*3 bytes, row-major RGB."""

    def close(self) -> None:
        """Release hardware. Safe to call more than once."""
