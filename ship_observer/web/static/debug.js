"use strict";

const canvas = document.getElementById("panel");
const statusEl = document.getElementById("status");

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
      v.display_name, v.mmsi, v.category, v.category_source || "—",
      v.priority,
      v.length_m === null ? "—" : Math.round(v.length_m) + "m",
      v.call_sign || "—", v.destination || "—", status,
    ];
    const DESTINATION_CELL = 8;
    cells.forEach((value, i) => {
      const td = document.createElement("td");
      td.textContent = value;
      // Raw destinations can be a US/GUID code (USCG AIS Encoding Guide
      // v.25, e.g. US^0TEM>016S) that the server already resolved to a
      // plain-English place; show that as a native tooltip rather than a
      // new column, since most destinations don't have one.
      if (i === DESTINATION_CELL && v.destination_resolved) {
        td.title = v.destination_resolved;
      }
      tr.appendChild(td);
    });
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
  const log = document.getElementById("event-log");
  log.innerHTML = "";
  // event.message can embed AIS-broadcast ship-name text (e.g. "entered:
  // <name>") - AIS is an open, unauthenticated protocol, so that string is
  // attacker-controlled. Build nodes with textContent, exactly like
  // renderLive() already does for vessel names, never innerHTML with
  // interpolated event fields.
  for (const e of data.events) {
    const row = document.createElement("div");
    row.className = `event ${e.level}`;
    const ts = document.createElement("span");
    ts.className = "ts";
    ts.textContent = e.ts.slice(11, 19);
    const lvl = document.createElement("span");
    lvl.className = "lvl";
    lvl.textContent = e.level;
    const cat = document.createElement("span");
    cat.className = "cat";
    cat.textContent = e.category;
    const msg = document.createElement("span");
    msg.className = "msg";
    msg.textContent = e.message;
    row.append(ts, lvl, cat, msg);
    log.appendChild(row);
  }
}

// The websocket pushes a state message at WEB_FPS (10/s by default), which
// keeps the status line and the panel mirror smooth - but renderLive() and
// renderConfig() rebuild their contents via innerHTML, and at that rate any
// text selection inside them is destroyed before a copy can complete. Gate
// those two rebuilds to roughly once a second, and skip the rebuild
// entirely when nothing rendered actually changed, so a stable table never
// interrupts a selection at all.
const TABLE_REFRESH_MS = 1000;
let lastTableRender = -Infinity;
let lastTableSnapshot = "";

function tableSnapshot(state) {
  const vessels = [...state.live, ...state.history].map((v) => [
    v.mmsi, v.slot, v.display_name, v.category, v.category_source,
    v.priority, v.length_m,
    v.call_sign, v.destination, v.eligible, v.filtered_reason,
    v.static_resolved, v.depart_reason,
  ]);
  return JSON.stringify({ vessels, config: state.config, capacity: state.capacity });
}

function refreshTableIfDue(state) {
  const now = performance.now();
  if (now - lastTableRender < TABLE_REFRESH_MS) return;
  lastTableRender = now;
  const snapshot = tableSnapshot(state);
  if (snapshot === lastTableSnapshot) return;
  lastTableSnapshot = snapshot;
  renderLive(state);
  renderConfig(state);
}

connectFrames({
  onFrame: (msg) => drawFrame(canvas, msg),
  onState: (state) => {
    renderStatus(statusEl, state);
    refreshTableIfDue(state);
  },
  onReconnecting: () => {
    statusEl.textContent = "reconnecting…";
    statusEl.className = "status";
  },
});

document.getElementById("window").addEventListener("change", refreshTraffic);
document.getElementById("level").addEventListener("change", refreshEvents);
refreshTraffic();
refreshEvents();
setInterval(refreshTraffic, 60000);
setInterval(refreshEvents, 5000);
