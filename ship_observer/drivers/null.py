from __future__ import annotations


class NullDriver:
    """Records frames instead of displaying them. Used for development on
    non-Pi hosts, for CI, and as the fallback when rgbmatrix is unavailable.
    """

    def __init__(self, width: int, height: int, keep: int = 1) -> None:
        self.width = width
        self.height = height
        self._keep = keep
        self.frames: list[bytes] = []
        self.closed = False

    def show(self, frame: bytes) -> None:
        expected = self.width * self.height * 3
        if len(frame) != expected:
            raise ValueError(f"frame is {len(frame)} bytes, expected {expected}")
        self.frames.append(frame)
        if len(self.frames) > self._keep:
            del self.frames[:-self._keep]

    def close(self) -> None:
        self.closed = True
