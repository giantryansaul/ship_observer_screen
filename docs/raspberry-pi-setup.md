# Raspberry Pi Setup — Ship Observer

Provision a Pi from a blank SD card to a working deck display. Follow the
sections in order; each one ends in a state you can verify before moving on.

Budget about 90 minutes, most of it waiting on `pyenv install` and the
`rpi-rgb-led-matrix` build.

## What you need

| Item | Notes |
|---|---|
| Raspberry Pi 4 (2 GB or better) | A Pi 3 works; set `MATRIX_GPIO_SLOWDOWN=2` instead of 4 |
| microSD card, 16 GB+ | |
| 64x64 HUB75 RGB LED panel | 1/32 scan, the common P2/P3 type |
| Adafruit RGB Matrix Bonnet or HAT | Direct GPIO wiring works but is far more error-prone |
| **Separate** 5 V power supply, 4 A minimum, for the panel | See the power warning below |
| USB-C supply for the Pi | The official 5 V 3 A one |

### Power — read this before wiring anything

**A 64x64 panel can draw about 4 A at full white.** The Pi's 5 V rail cannot
supply that. Powering the panel from the Pi's GPIO header or USB will brown out
the Pi and can damage it.

Use a **separate** 5 V supply of at least 4 A wired to the panel's own power
terminals. The Adafruit bonnet has a barrel jack for exactly this. The panel and
the Pi must share a common ground — the bonnet handles this for you; if you are
wiring directly to GPIO, connect the grounds yourself.

## 1. Image the SD card

Use **Raspberry Pi Imager**.

1. Choose **Raspberry Pi OS Lite (64-bit)** — Bookworm. No desktop is needed and
   it leaves more CPU for the panel refresh.
2. Click the gear (Advanced options) and set:
   - Hostname: `ship-observer`
   - **Enable SSH**, with password or public-key authentication
   - Username and password
   - Wi-Fi SSID, password, and country — or skip if you are using Ethernet
   - Locale and timezone
3. Write the card, then boot the Pi with it.

Verify:

```bash
ssh <your-user>@ship-observer.local
```

If `.local` does not resolve, find the Pi's address from your router and use
that instead.

## 2. Wire the panel

With the **Adafruit RGB Matrix Bonnet**:

1. Power the Pi down and unplug it.
2. Seat the bonnet on the 40-pin header.
3. Connect the panel's 16-pin HUB75 ribbon to the bonnet's output. The panel's
   input side is usually marked with an arrow pointing away from it — get this
   backwards and the panel simply stays dark.
4. Connect the panel's power harness to the bonnet's screw terminals, watching
   polarity, then plug the 5 V 4 A supply into the bonnet's barrel jack.
5. Power the Pi separately over USB-C.

### The PWM modification (strongly recommended)

Solder a jumper between **GPIO4 and GPIO18** on the bonnet. This lets the
library use hardware PWM, which removes most visible flicker. With the jumper
in place use `MATRIX_HARDWARE_MAPPING=adafruit-hat-pwm`; without it, use
`adafruit-hat`.

## 3. Run the installer

Clone the repo onto the Pi and run the provisioning script:

```bash
sudo apt-get update && sudo apt-get install -y git
git clone <your-repo-url> ~/ship-observer
cd ~/ship-observer
./deploy/install.sh
```

The script does everything in sections 4 through 8. Read on to understand what
it did and how to verify each part — or to do any step by hand if the script
fails partway.

## 4. Blacklist the onboard sound module

The matrix library drives the panel using the same PWM peripheral that the
onboard sound output uses. If `snd_bcm2835` is loaded, the panel flickers.

```bash
sudo tee /etc/modprobe.d/blacklist-rgb-matrix.conf <<'EOF'
blacklist snd_bcm2835
EOF
sudo update-initramfs -u
```

Verify after rebooting: `lsmod | grep snd_bcm2835` returns nothing.

## 5. Isolate a CPU core

The panel is refreshed by a tight timing loop. Letting Linux schedule other work
on the same core produces horizontal tearing. Reserving core 3 for it fixes that.

Edit `/boot/firmware/cmdline.txt` (on pre-Bookworm images it is
`/boot/cmdline.txt`). It is a **single line** — append to it, never add a new
line:

```
... rootwait isolcpus=3
```

Verify after rebooting: `cat /sys/devices/system/cpu/isolated` prints `3`.

## 6. Install Python 3.11.13 via pyenv

Raspberry Pi OS Bookworm ships Python 3.11, but pyenv pins the exact version so
the Pi and your development machine agree.

```bash
curl -fsSL https://pyenv.run | bash
```

Add to `~/.bashrc`:

```bash
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
eval "$(pyenv virtualenv-init -)"
```

Then, in a fresh shell:

```bash
pyenv install 3.11.13
pyenv virtualenv 3.11.13 ship_observer
```

Verify: `~/.pyenv/versions/ship_observer/bin/python --version` prints
`Python 3.11.13`.

Building CPython on a Pi takes 15–25 minutes. If it fails, a build dependency is
missing — `install.sh` installs the full list.

## 7. Build rpi-rgb-led-matrix

The `rgbmatrix` module is **not on PyPI**. It must be compiled from source
against the exact interpreter that will run the service.

```bash
git clone --depth 1 https://github.com/hzeller/rpi-rgb-led-matrix.git ~/src/rpi-rgb-led-matrix
cd ~/src/rpi-rgb-led-matrix
VENV_PYTHON=~/.pyenv/versions/ship_observer/bin/python
make build-python PYTHON=$VENV_PYTHON
make install-python PYTHON=$VENV_PYTHON
```

Verify:

```bash
~/.pyenv/versions/ship_observer/bin/python -c "import rgbmatrix; print('ok')"
```

## 8. Install the service

```bash
sudo mkdir -p /opt/ship-observer /var/lib/ship-observer
sudo rsync -a --exclude .git --exclude .env ~/ship-observer/ /opt/ship-observer/
sudo ln -sfn ~/.pyenv/versions/ship_observer /opt/ship-observer/venv
sudo /opt/ship-observer/venv/bin/pip install -e '/opt/ship-observer[pi]'
```

`/var/lib/ship-observer` holds the SQLite log and must be writable by whatever
user the service runs as.

### GPIO access: root, or not

The matrix library needs `/dev/mem` and precise timing, which in practice means
**running as root**. The provided unit does exactly that, with
`drop_privileges=False` in the driver.

The alternative is letting the library drop to the `daemon` user after
initialising the hardware — but then `/var/lib/ship-observer` has to be owned by
`daemon`, and the port binding has to happen first. It is more moving parts for
a device on your own LAN. Run as root unless you have a specific reason not to.

### Configure

```bash
sudo cp /opt/ship-observer/.env.example /opt/ship-observer/.env
sudo chmod 600 /opt/ship-observer/.env
sudo nano /opt/ship-observer/.env
```

At minimum, set:

- `AIS_STREAM_API_KEY` — from your aisstream.io account
- `BBOX` — `lon_min,lat_min,lon_max,lat_max`, longitude first. The current deck
  box is `-122.527428,47.859476,-122.323322,47.910359`. Grab new corners from
  [bboxfinder.com](http://bboxfinder.com), which emits them in exactly this
  order.
- `MATRIX_HARDWARE_MAPPING` — `adafruit-hat-pwm` if you did the PWM solder
  jumper, `adafruit-hat` if you did not.
- `MATRIX_GPIO_SLOWDOWN` — `4` on a Pi 4, `2` on a Pi 3.

Then install and enable the unit:

```bash
sudo cp /opt/ship-observer/deploy/ship-observer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ship-observer
sudo reboot
```

The reboot is required: the sound blacklist and `isolcpus` only take effect on
boot.

## 9. Verification

Check each layer in order. If one fails, fix it before moving to the next —
debugging the top of the stack while the bottom is broken wastes hours.

**Layer 1 — the panel itself, independent of this project:**

```bash
cd ~/src/rpi-rgb-led-matrix
sudo ./examples-api-use/demo -D0 --led-rows=64 --led-cols=64 \
  --led-gpio-mapping=adafruit-hat-pwm --led-slowdown-gpio=4
```

Expected: a rotating coloured square. Ctrl-C to stop. If this does not work, no
amount of application debugging will help — go to Troubleshooting.

**Layer 2 — the service starts and stays up:**

```bash
sudo systemctl start ship-observer
sudo systemctl status ship-observer
```

Expected: `active (running)`. If it shows `activating (auto-restart)`, it is
crash-looping — `sudo journalctl -u ship-observer -n 50` will say why.

**Layer 3 — the web interface answers:**

```bash
curl -s http://localhost:8080/healthz
```

Expected: `{"ok": true, "connected": true, "last_message_age_seconds": ...}`.
If `connected` is `false`, the AIS websocket is not up — check the API key.

**Layer 4 — real vessels appear:**

Open `http://ship-observer.local:8080/` in a browser. You should see the panel
mirror, the vessel table, and the event log. The parsed bounding box is shown in
the Configuration panel — **check the corners are what you intended**, since a
transposed paste is otherwise silent.

Puget Sound traffic is not continuous. If the box is empty, that may simply mean
nothing is there right now; the event log will confirm the stream is connected
and receiving.

**Layer 5 — the panel matches the mirror.** Look at the actual LED panel. It
should show what the browser mirror shows.

## 10. Tuning after a week

Let it run for a week, then open the Traffic summary panel on the debug page (or
`curl 'http://localhost:8080/api/traffic-summary?window=7d'`).

Use it to answer:

- **Is recreational traffic noisy?** If sailing and fishing dominate the category
  counts, set `MIN_LENGTH_METERS=50` — that cuts nearly all of it, since ferries,
  cargo, tankers, and warships are all well over 50 m.
- **Is the box right?** Both nearby ferry routes fall outside the current box:
  Edmonds–Kingston runs near 47.80 and Mukilteo–Clinton near 47.95. To catch
  ferries, extend `lat_min` down to about 47.79 or `lat_max` up to about 47.99.
- **Is static data resolving?** A high `unresolved_static_visits` count means
  vessels are passing through faster than their 6-minute `ShipStaticData`
  interval, so they only ever show as generic hulls.

Edit `/opt/ship-observer/.env`, then `sudo systemctl restart ship-observer`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Panel flickers or shimmers | `snd_bcm2835` is still loaded (§4) or `isolcpus` is not applied (§5). Confirm with `lsmod \| grep snd` and `cat /sys/devices/system/cpu/isolated`. Both need a reboot. |
| Faint flicker after the above | Do the GPIO4–GPIO18 solder jumper and switch to `adafruit-hat-pwm`. |
| Panel is completely blank | Check the ribbon is on the panel's **input** side; check the 5 V supply is connected to the panel, not just the Pi; confirm `MATRIX_HARDWARE_MAPPING` matches your board. Test with the `demo` binary first. |
| Colors are wrong (red shows as blue) | Wrong hardware-mapping (`MATRIX_HARDWARE_MAPPING`). Try `adafruit-hat`, `adafruit-hat-pwm`, and `regular` with the `demo` binary until colors are right, then set that value in `.env`. |
| Top half or bottom half is dark | Wrong row-scan for the panel. Add `--led-multiplexing=1` (then 2, 3…) to the `demo` command to find the right value, then set `MATRIX_ROWS` correctly. |
| Service crash-loops immediately | `journalctl -u ship-observer -n 50`. A `Configuration error:` line names the offending variable. A malformed `BBOX` is the usual culprit — it must be four numbers, longitude first, with `min < max` on both axes. |
| `connected: false` in `/healthz` | The API key is wrong, or the subscription was rejected. The event log on the debug page shows the websocket errors. |
| Panel dims by half on its own | That is deliberate: no AIS message has arrived for `STALE_SECONDS` (default 120). It means the feed is down, not the display. |
| `import rgbmatrix` fails | The build was run against the wrong interpreter. Re-run §7 with `PYTHON=` pointing at `~/.pyenv/versions/ship_observer/bin/python`. |
| `ModuleNotFoundError: PIL` | `pip install -e '/opt/ship-observer[pi]'` — the `pi` extra carries Pillow. |
| Web UI loads but the panel is black | Look at the vessel table. Rows struck through are being filtered by `MIN_LENGTH_METERS` or `EXCLUDE_CATEGORIES`, and the Status column says which. |

## Useful commands

```bash
sudo journalctl -u ship-observer -f          # live logs
sudo systemctl restart ship-observer         # after editing .env
curl -s localhost:8080/api/state | python3 -m json.tool
curl -s 'localhost:8080/api/ships?limit=20' | python3 -m json.tool
sqlite3 /var/lib/ship-observer/ships.db 'SELECT name,category,length_m FROM ship_log ORDER BY entered_at DESC LIMIT 20;'
```

To capture a session for later replay, set `RECORD_RAW_PATH=/var/lib/ship-observer/session.jsonl`
in `.env` and restart. Replay it against a scratch database with:

```bash
/opt/ship-observer/venv/bin/python -m ship_observer.replay \
  --file /var/lib/ship-observer/session.jsonl --db /tmp/replay.db
```
