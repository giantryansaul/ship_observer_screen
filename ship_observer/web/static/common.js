"use strict";

const FRAME_SCALE = 8;

// Cached per-canvas so repeated frames at the same size reuse one ImageData
// buffer instead of allocating on every draw.
const _frameState = new WeakMap();

function drawFrame(canvas, msg, scale = FRAME_SCALE) {
  const { width, height, rgb } = msg;
  if (canvas.width !== width * scale) {
    canvas.width = width * scale;
    canvas.height = height * scale;
    canvas.getContext("2d").imageSmoothingEnabled = false;
    _frameState.delete(canvas);
  }
  const ctx = canvas.getContext("2d");

  let state = _frameState.get(canvas);
  if (!state || state.width !== width || state.height !== height) {
    state = { width, height, imageData: ctx.createImageData(width, height) };
    _frameState.set(canvas, state);
  }

  const binary = atob(rgb);
  const data = state.imageData.data;
  for (let i = 0, j = 0; i < width * height; i++) {
    data[j++] = binary.charCodeAt(i * 3);
    data[j++] = binary.charCodeAt(i * 3 + 1);
    data[j++] = binary.charCodeAt(i * 3 + 2);
    data[j++] = 255;
  }
  // Draw at 1:1 into an offscreen buffer, then scale up with smoothing off so
  // every LED pixel stays a hard square.
  const off = new OffscreenCanvas(width, height);
  off.getContext("2d").putImageData(state.imageData, 0, 0);
  ctx.drawImage(off, 0, 0, canvas.width, canvas.height);
}

function renderStatus(el, state) {
  const age = state.last_message_age_seconds;
  el.textContent = state.connected
    ? `connected · last message ${age === null ? "never" : age.toFixed(0) + "s ago"}`
    : "disconnected";
  el.className = "status " + (state.stale ? "bad" : "good");
}

// Opens /ws/frames and reconnects on drop. `onFrame`/`onState` receive the
// two message kinds the socket pushes; `onReconnecting` fires while the
// socket is down and about to retry.
function connectFrames({ onFrame, onState, onReconnecting } = {}) {
  const scheme = location.protocol === "https:" ? "wss" : "ws";

  function open() {
    const ws = new WebSocket(`${scheme}://${location.host}/ws/frames`);
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === "frame" && onFrame) {
        onFrame(msg);
      } else if (msg.type === "state" && onState) {
        onState(msg);
      }
    };
    ws.onclose = () => {
      if (onReconnecting) onReconnecting();
      setTimeout(open, 2000);
    };
  }
  open();
}
