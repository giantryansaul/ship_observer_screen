from __future__ import annotations

import logging
import threading

from ..config import Settings

log = logging.getLogger(__name__)


class RgbMatrixDriver:
    """HUB75 panel via hzeller/rpi-rgb-led-matrix.

    SwapOnVSync() blocks until the next vsync, so it runs on its own thread
    behind a single-slot buffer. The async render loop never waits on it, and
    frames that arrive faster than the panel can show them are simply dropped.
    """

    def __init__(self, settings: Settings) -> None:
        # Imported lazily: this module must never be imported on a non-Pi host.
        from rgbmatrix import RGBMatrix, RGBMatrixOptions

        options = RGBMatrixOptions()
        options.rows = settings.matrix_rows
        options.cols = settings.matrix_cols
        options.chain_length = settings.matrix_chain
        options.parallel = settings.matrix_parallel
        options.brightness = settings.matrix_brightness
        options.gpio_slowdown = settings.matrix_gpio_slowdown
        options.hardware_mapping = settings.matrix_hardware_mapping
        options.drop_privileges = False

        self._matrix = RGBMatrix(options=options)
        self._canvas = self._matrix.CreateFrameCanvas()
        self.width = settings.panel_width
        self.height = settings.panel_height

        self._pending: bytes | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hub75",
                                        daemon=True)
        self._thread.start()

    def show(self, frame: bytes) -> None:
        """Non-blocking. Replaces any frame not yet drawn."""
        with self._lock:
            self._pending = frame
        self._wake.set()

    def _run(self) -> None:
        from PIL import Image  # provided by the rgbmatrix build

        while not self._stop.is_set():
            self._wake.wait(timeout=0.5)
            self._wake.clear()
            with self._lock:
                frame, self._pending = self._pending, None
            if frame is None:
                continue
            try:
                image = Image.frombytes("RGB", (self.width, self.height), frame)
                self._canvas.SetImage(image)
                self._canvas = self._matrix.SwapOnVSync(self._canvas)
            except Exception:
                log.exception("failed to present a frame")

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=2.0)
        try:
            self._matrix.Clear()
        except Exception:
            log.debug("matrix clear failed during shutdown", exc_info=True)
