# Ship Observer Screen

Live AIS vessel display for a 64x64 HUB75 LED matrix on a Raspberry Pi.

Streams from [AISStream.io](https://aisstream.io), filters to a configurable
bounding box, shows the three most interesting vessels on the panel, and serves
a debug page backed by rolling SQLite logs.

- **Pi setup:** [`docs/raspberry-pi-setup.md`](docs/raspberry-pi-setup.md)
- **Design:** [`docs/superpowers/specs/2026-08-26-ship-observer-design.md`](docs/superpowers/specs/2026-08-26-ship-observer-design.md)

## Development (no Pi required)

```bash
pyenv virtualenv 3.11.13 ship_observer
pip install -e ".[dev]"
pytest
```

Everything except `ship_observer/drivers/rgbmatrix.py` runs on any host; the
null driver stands in for the panel, and the debug page's pixel mirror shows
exactly what the LEDs would show.

```bash
cp .env.example .env    # set AIS_STREAM_API_KEY and BBOX
DISPLAY_DRIVER=null DB_PATH=./ships.db python -m ship_observer
open http://localhost:8080/
```
