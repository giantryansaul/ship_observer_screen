"use strict";

const SCALE = 8;
const canvas = document.getElementById("panel");
const ctx = canvas.getContext("2d");
let imageData = null;

function drawFrame(msg) {
  const { width, height, rgb } = msg;
  if (canvas.width !== width * SCALE) {
    canvas.width = width * SCALE;
    canvas.height = height * SCALE;
    ctx.imageSmoothingEnabled = false;
  }
  const binary = atob(rgb);
  if (!imageData || imageData.width !== width) {
    imageData = ctx.createImageData(width, height);
  }
  for (let i = 0, j = 0; i < width * height; i++) {
    imageData.data[j++] = binary.charCodeAt(i * 3);
    imageData.data[j++] = binary.charCodeAt(i * 3 + 1);
    imageData.data[j++] = binary.charCodeAt(i * 3 + 2);
    imageData.data[j++] = 255;
  }
  // Draw at 1:1 into an offscreen buffer, then scale up with smoothing off so
  // every LED pixel stays a hard square.
  const off = new OffscreenCanvas(width, height);
  off.getContext("2d").putImageData(imageData, 0, 0);
  ctx.drawImage(off, 0, 0, canvas.width, canvas.height);
}

function renderStatus(state) {
  const el = document.getElementById("status");
  const age = state.last_message_age_seconds;
  el.textContent = state.connected
    ? `connected · last message ${age === null ? "never" : age.toFixed(0) + "s ago"}`
    : "disconnected";
  el.className = "status " + (state.stale ? "bad" : "good");
}

function renderLive(state) {
  const body = document.querySelector("#live-table tbody");
  const rows = [...state.live, ...state.history.map((v) => ({ ...v, past: true }))];
  body.innerHTML = "";
  for (const v of rows) {
    const tr = document.createElement("tr");
    if (!v.eligible) tr.className = "filtered";
    if (v.past) tr.classList.add("past");
    const status = v.past
      ? `departed (${v.depart_reason})`
      : v.filtered_reason || (v.static_resolved ? "shown" : "awaiting static");
    const cells = [
      v.slot === null || v.slot === undefined ? "—" : v.slot + 1,
      v.display_name, v.mmsi, v.category, v.priority,
      v.length_m === null ? "—" : Math.round(v.length_m) + "m",
      v.call_sign || "—", v.destination || "—", status,
    ];
    for (const value of cells) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

function renderConfig(state) {
  const c = state.config;
  const bbox = c.bbox;
  document.getElementById("config").innerHTML = `
    <dl>
      <dt>Bounding box</dt>
      <dd>lon ${bbox.lon_min} → ${bbox.lon_max}<br>
          lat ${bbox.lat_min} → ${bbox.lat_max}</dd>
      <dt>Panel</dt><dd>${c.panel.width}×${c.panel.height}, ${c.display_driver}</dd>
      <dt>Slots</dt><dd>${c.max_ships} (capacity ${state.capacity})</dd>
      <dt>History</dt><dd>${c.display_history ? "on" : "off"}</dd>
      <dt>Min length</dt><dd>${c.min_length_meters} m</dd>
      <dt>Excluded</dt><dd>${c.exclude_categories.join(", ") || "none"}</dd>
      <dt>Priority selection</dt><dd>${c.priority_selection ? "on" : "off"}</dd>
      <dt>Retention</dt><dd>ships ${c.ship_log_days}d · events ${c.event_log_hours}h</dd>
    </dl>`;
}

async function refreshTraffic() {
  const window_ = document.getElementById("window").value;
  const data = await (await fetch(`/api/traffic-summary?window=${window_}`)).json();
  const cats = data.by_category
    .map((r) => `<tr><td>${r.category}</td><td>${r.visits}</td>
                     <td>${r.displayed_visits || 0}</td></tr>`)
    .join("");
  const lengths = data.length_histogram
    .map((r) => `<tr><td>${r.bucket_m}–${r.bucket_m + 9} m</td>
                     <td>${r.visits}</td></tr>`)
    .join("");
  const dwell = data.dwell_by_category
    .map((r) => `<tr><td>${r.category}</td><td>${r.median_minutes} min</td>
                     <td>${r.p90_minutes} min</td></tr>`)
    .join("");
  document.getElementById("traffic-summary").innerHTML = `
    <p>${data.total_visits} visits · ${data.unresolved_static_visits} never
       resolved static data</p>
    <div class="grid">
      <table><thead><tr><th>Category</th><th>Visits</th><th>Shown</th></tr></thead>
        <tbody>${cats}</tbody></table>
      <table><thead><tr><th>Length</th><th>Visits</th></tr></thead>
        <tbody>${lengths}</tbody></table>
      <table><thead><tr><th>Category</th><th>Median dwell</th><th>p90</th></tr></thead>
        <tbody>${dwell}</tbody></table>
    </div>`;
}

async function refreshEvents() {
  const level = document.getElementById("level").value;
  const data = await (await fetch(`/api/events?limit=100&level=${level}`)).json();
  document.getElementById("event-log").innerHTML = data.events
    .map((e) => `<div class="event ${e.level}">
        <span class="ts">${e.ts.slice(11, 19)}</span>
        <span class="lvl">${e.level}</span>
        <span class="cat">${e.category}</span>
        <span class="msg">${e.message}</span></div>`)
    .join("");
}

function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${scheme}://${location.host}/ws/frames`);
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "frame") {
      drawFrame(msg);
    } else if (msg.type === "state") {
      renderStatus(msg);
      renderLive(msg);
      renderConfig(msg);
    }
  };
  ws.onclose = () => {
    document.getElementById("status").textContent = "reconnecting…";
    setTimeout(connect, 2000);
  };
}

document.getElementById("window").addEventListener("change", refreshTraffic);
document.getElementById("level").addEventListener("change", refreshEvents);
connect();
refreshTraffic();
refreshEvents();
setInterval(refreshTraffic, 60000);
setInterval(refreshEvents, 5000);
