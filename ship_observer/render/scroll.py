from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable

from .font import Font, text_width

HOLD_START = "hold_start"
SCROLLING = "scrolling"
HOLD_END = "hold_end"


@dataclass
class _State:
    text: str
    box_width: int
    font_width: int
    phase: str = HOLD_START
    offset: float = 0.0
    timer: float = 0.0


class Scroller:
    """Per-field horizontal scrolling.

    Fields that fit stay perfectly still, which keeps the panel calm; only
    overflowing text moves. State is keyed by (mmsi, field) and resets whenever
    the text, the box width, or the font it is measured in changes.
    """

    def __init__(self, speed_px_s: float = 12.0, pause_s: float = 1.5) -> None:
        self._speed = speed_px_s
        self._pause = pause_s
        self._states: dict[Hashable, _State] = {}

    def offset_for(self, key: Hashable, text: str, box_width: int,
                   dt: float, font: Font | None = None) -> int:
        font = font or Font.default()
        overflow = text_width(text, font) - box_width
        if overflow <= 0:
            self._states.pop(key, None)
            return 0

        state = self._states.get(key)
        if (state is None or state.text != text or state.box_width != box_width
                or state.font_width != font.width):
            state = _State(text=text, box_width=box_width,
                           font_width=font.width)
            self._states[key] = state

        state.timer += dt
        if state.phase == HOLD_START:
            if state.timer > self._pause:
                state.phase = SCROLLING
                state.timer = 0.0
        elif state.phase == SCROLLING:
            state.offset -= self._speed * dt
            if state.offset <= -overflow:
                state.offset = float(-overflow)
                state.phase = HOLD_END
                state.timer = 0.0
        elif state.phase == HOLD_END:
            if state.timer > self._pause:
                state.phase = HOLD_START
                state.offset = 0.0
                state.timer = 0.0

        return int(state.offset)

    def retain(self, keys: set[Hashable]) -> None:
        """Drop state for fields no longer on screen, so the dict cannot grow
        without bound over days of uptime.
        """
        for key in list(self._states):
            if key not in keys:
                del self._states[key]
