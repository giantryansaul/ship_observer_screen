"use strict";

const SCALE = 12;   // larger than the live mirrors - this is for scrutiny, not a kiosk display
const canvas = document.getElementById("panel");
const caption = document.getElementById("audit-caption");
const legendCard = document.getElementById("icon-legend");
const legendGrid = document.getElementById("icon-legend-grid");
const buttons = document.querySelectorAll("[data-view]");
const fontToggle = document.getElementById("font-toggle");
const fontButtons = document.querySelectorAll("[data-font]");

let currentFont = "small";   // only the Characters view is font-switchable

const CAPTIONS = {
  chars: "Every character the panel can draw: the pangram, then the full "
        + "AIS text set in order.",
  icons: "Every category's icon, in the grid position the legend below mirrors.",
  ships: "1 active vessel (top) and 2 recently departed, below the LAST "
        + "SEEN divider - includes a resolved destination, an unresolved "
        + "one, a vessel with no known length, and the fishing icon in "
        + "context.",
};

function rgb(color) {
  return `rgb(${color[0]}, ${color[1]}, ${color[2]})`;
}

function renderLegend(categories) {
  legendGrid.innerHTML = "";
  for (const c of categories) {
    const cell = document.createElement("div");
    cell.className = "icon-legend-cell";
    const swatch = document.createElement("span");
    swatch.className = "icon-legend-swatch";
    swatch.style.background = rgb(c.color);
    const label = document.createElement("span");
    label.textContent = c.name;
    cell.append(swatch, label);
    legendGrid.appendChild(cell);
  }
}

async function loadView(view) {
  const query = view === "chars" ? `view=chars&font=${currentFont}` : `view=${view}`;
  const response = await fetch(`/api/panel-audit?${query}`);
  const msg = await response.json();
  drawFrame(canvas, msg, SCALE);
  caption.textContent = CAPTIONS[view] || "";
  fontToggle.hidden = view !== "chars";

  const isIcons = view === "icons" && msg.categories;
  legendCard.hidden = !isIcons;
  if (isIcons) renderLegend(msg.categories);

  for (const button of buttons) {
    button.classList.toggle("active", button.dataset.view === view);
  }
}

for (const button of buttons) {
  button.addEventListener("click", () => loadView(button.dataset.view));
}

for (const button of fontButtons) {
  button.addEventListener("click", () => {
    currentFont = button.dataset.font;
    for (const other of fontButtons) {
      other.classList.toggle("active", other === button);
    }
    loadView("chars");
  });
}

loadView("chars");
