"use strict";

const canvas = document.getElementById("panel");
const statusEl = document.getElementById("status");

connectFrames({
  onFrame: (msg) => drawFrame(canvas, msg),
  onState: (state) => renderStatus(statusEl, state),
  onReconnecting: () => {
    statusEl.textContent = "reconnecting…";
    statusEl.className = "status";
  },
});
