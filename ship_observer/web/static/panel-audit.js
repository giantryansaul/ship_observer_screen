"use strict";

const SCALE = 12;   // larger than the live mirrors - this is for scrutiny, not a kiosk display
const canvas = document.getElementById("panel");
const caption = document.getElementById("audit-caption");
const legendCard = document.getElementById("icon-legend");
const legendGrid = document.getElementById("icon-legend-grid");
const buttons = document.querySelectorAll("[data-view]");
const fontToggle = document.getElementById("font-toggle");
const fontButtons = document.querySelectorAll("[data-font]");
const sizeToggle = document.getElementById("icon-size-toggle");
const sizeButtons = document.querySelectorAll("[data-size]");
const paging = document.getElementById("icon-paging");
const pageLabel = document.getElementById("icon-page-label");
const prevButton = document.getElementById("icon-prev");
const nextButton = document.getElementById("icon-next");

let currentFont = "small";   // only the Characters view is font-switchable
let currentSize = "8";       // only the Icons view is size-switchable
let currentPage = 0;         // the bigger icon sets need more than one frame
let pageCount = 1;           // as last reported by the API for the current size

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

function renderPaging(msg) {
  pageCount = msg.pages || 1;
  paging.hidden = pageCount < 2;   // one frame holds every 8px icon
  pageLabel.textContent = `page ${(msg.page || 0) + 1} / ${pageCount}`;
  prevButton.disabled = currentPage <= 0;
  nextButton.disabled = currentPage >= pageCount - 1;
}

function query(view) {
  if (view === "chars") return `view=chars&font=${currentFont}`;
  if (view === "icons") return `view=icons&size=${currentSize}&page=${currentPage}`;
  return `view=${view}`;
}

async function loadView(view) {
  const response = await fetch(`/api/panel-audit?${query(view)}`);
  const msg = await response.json();
  drawFrame(canvas, msg, SCALE);
  caption.textContent = CAPTIONS[view] || "";
  fontToggle.hidden = view !== "chars";
  sizeToggle.hidden = view !== "icons";

  const isIcons = view === "icons" && msg.categories;
  legendCard.hidden = !isIcons;
  paging.hidden = true;
  if (isIcons) {
    renderLegend(msg.categories);   // the legend follows the page it belongs to
    renderPaging(msg);
  }

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

for (const button of sizeButtons) {
  button.addEventListener("click", () => {
    currentSize = button.dataset.size;
    currentPage = 0;   // a bigger set has fewer pages - never keep a stale one
    for (const other of sizeButtons) {
      other.classList.toggle("active", other === button);
    }
    loadView("icons");
  });
}

prevButton.addEventListener("click", () => {
  currentPage = Math.max(0, currentPage - 1);
  loadView("icons");
});

nextButton.addEventListener("click", () => {
  currentPage = Math.min(pageCount - 1, currentPage + 1);
  loadView("icons");
});

loadView("chars");
