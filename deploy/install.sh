#!/usr/bin/env bash
# Provision a Raspberry Pi to run Ship Observer.
# Follow docs/raspberry-pi-setup.md for the narrated version of these steps.
set -euo pipefail

PYTHON_VERSION="3.11.13"
VENV_NAME="ship_observer"
INSTALL_DIR="/opt/ship-observer"
DATA_DIR="/var/lib/ship-observer"
MATRIX_SRC="${HOME}/src/rpi-rgb-led-matrix"
CMDLINE="/boot/firmware/cmdline.txt"
[[ -f "${CMDLINE}" ]] || CMDLINE="/boot/cmdline.txt"

log() { printf '\n=== %s ===\n' "$1"; }

log "Installing system packages"
sudo apt-get update
sudo apt-get install -y \
  git curl build-essential pkg-config \
  python3-dev cython3 \
  libgraphicsmagick++-dev libwebp-dev \
  libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
  libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev \
  libffi-dev liblzma-dev libjpeg-dev libfreetype6-dev

log "Blacklisting snd_bcm2835"
# The matrix library drives the panel with the same PWM hardware the onboard
# sound uses. Leaving the sound module loaded causes visible flicker.
sudo tee /etc/modprobe.d/blacklist-rgb-matrix.conf >/dev/null <<'EOF'
blacklist snd_bcm2835
EOF
sudo update-initramfs -u

log "Reserving CPU core 3 (isolcpus)"
# Pinning the panel's refresh thread to an isolated core removes the jitter
# that shows up as horizontal tearing.
if ! grep -q "isolcpus=3" "${CMDLINE}"; then
  sudo sed -i '1 s/$/ isolcpus=3/' "${CMDLINE}"
  echo "Added isolcpus=3 to ${CMDLINE} (takes effect after reboot)"
else
  echo "isolcpus=3 already present"
fi

log "Installing pyenv and Python ${PYTHON_VERSION}"
if [[ ! -d "${HOME}/.pyenv" ]]; then
  curl -fsSL https://pyenv.run | bash
fi
export PYENV_ROOT="${HOME}/.pyenv"
export PATH="${PYENV_ROOT}/bin:${PATH}"
eval "$(pyenv init -)"
eval "$(pyenv virtualenv-init -)"
pyenv install -s "${PYTHON_VERSION}"
pyenv virtualenv -f "${PYTHON_VERSION}" "${VENV_NAME}"
VENV_PYTHON="${PYENV_ROOT}/versions/${VENV_NAME}/bin/python"

log "Building rpi-rgb-led-matrix"
# Not on PyPI - it must be built from source against this interpreter.
mkdir -p "$(dirname "${MATRIX_SRC}")"
if [[ ! -d "${MATRIX_SRC}" ]]; then
  git clone --depth 1 https://github.com/hzeller/rpi-rgb-led-matrix.git "${MATRIX_SRC}"
fi
make -C "${MATRIX_SRC}" build-python PYTHON="${VENV_PYTHON}"
make -C "${MATRIX_SRC}" install-python PYTHON="${VENV_PYTHON}"

log "Installing ship-observer"
sudo mkdir -p "${INSTALL_DIR}" "${DATA_DIR}"
sudo chown -R "$(id -u):$(id -g)" "${INSTALL_DIR}"
rsync -a --delete \
  --exclude '.git' --exclude '__pycache__' --exclude '.env' \
  "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/" "${INSTALL_DIR}/"
ln -sfn "${PYENV_ROOT}/versions/${VENV_NAME}" "${INSTALL_DIR}/venv"
"${VENV_PYTHON}" -m pip install --upgrade pip
"${VENV_PYTHON}" -m pip install -e "${INSTALL_DIR}[pi]"

if [[ ! -f "${INSTALL_DIR}/.env" ]]; then
  cp "${INSTALL_DIR}/.env.example" "${INSTALL_DIR}/.env"
  chmod 600 "${INSTALL_DIR}/.env"
  echo "Created ${INSTALL_DIR}/.env - edit it and set AIS_STREAM_API_KEY and BBOX."
fi

log "Installing the systemd unit"
sudo cp "${INSTALL_DIR}/deploy/ship-observer.service" \
  /etc/systemd/system/ship-observer.service
sudo systemctl daemon-reload
sudo systemctl enable ship-observer.service

cat <<EOF

Done. Remaining steps:
  1. Edit ${INSTALL_DIR}/.env and set AIS_STREAM_API_KEY and BBOX.
  2. sudo reboot          (needed for the blacklist and isolcpus)
  3. sudo systemctl start ship-observer
  4. Open http://\$(hostname -I | awk '{print \$1}'):8080/

EOF
