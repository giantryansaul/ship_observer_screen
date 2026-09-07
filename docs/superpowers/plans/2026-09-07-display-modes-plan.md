# Display Modes Plan

Approved design (chat, 2026-09-07). Panel is 64x64. Frames are rendered
server-side in `Service` and mirrored to web pages over `/ws/frames`.
Goal: three selectable display modes — the existing 3-ship layout plus new
"2 ship" and "1 ship detailed" layouts with bigger icons and a larger font —
switchable from a Display dropdown on the kiosk (/) and debug (/debug)
pages, persisted in SQLite, applied to the physical matrix and all mirrors
together.

## Global Constraints

- Panel geometry: 64 wide x 64 tall. Nothing may draw outside the canvas.
- Mode identifiers (API strings and enum values): `three_ship`, `two_ship`,
  `one_ship`. Default mode when nothing is stored: `three_ship`.
- The 3-ship layout (`render/layout.py`) keeps today's pixel output
  unchanged in `three_ship` mode. Existing tests keep passing.
- New font: misc-fixed 6x10 BDF, loaded through the existing `Font` BDF
  parser. The current 4x6 font remains the default everywhere not
  explicitly changed.
- New icons: 16x16 (2-ship) and 32x32 (1-ship) per `ShipCategory`, in the
  existing `_ART` string-grid format, drawn with the existing
  `CATEGORY_COLOR` palette and `#`/`*`/`R`/`G`/`B`/`.` glyph conventions.
- Rotation dwell: 10 seconds per page (constant `DWELL_SECONDS = 10.0`),
  cycling through ALL eligible live vessels in priority order (pages of 1
  or 2 depending on mode). When no live vessels exist, rotate recently
  departed vessels instead, visibly tagged `LAST SEEN`.
- Mode changes broadcast over the existing websocket so every open page
  (and its dropdown) updates without reload.
- TDD; match existing test style (pytest, tests/ and tests/render/).
- Known-failing baseline (pre-existing on main, unrelated, do NOT fix and
  do not count against any task): tests/test_web_api.py::test_traffic_summary_endpoint

## Task 1: Multi-font support + 6x10 font

Generalize `render/font.py` so two fonts coexist.

- Download the misc-fixed 6x10 BDF to `ship_observer/render/fonts/6x10.bdf`
  from https://raw.githubusercontent.com/hzeller/rpi-rgb-led-matrix/master/fonts/6x10.bdf
  (public domain, same family as the existing 4x6.bdf). If network access
  fails, STOP and report BLOCKED — do not hand-write the font.
- `Font` gains `width`/`height` attributes parsed from the BDF
  `FONTBOUNDINGBOX` line (fall back to constructor args if absent).
  `Font.default()` stays the cached 4x6; add `Font.large()` — cached 6x10
  loader. `GLYPH_OVERRIDES` continues to apply to the 4x6 font only.
- `draw_text(...)` and `text_width(...)` gain an optional `font` keyword
  (default `None` -> `Font.default()`), and per-character advance uses
  `font.width`. `max_chars(pixels, font=None)` likewise. All existing call
  sites keep working unchanged.
- `render/scroll.py` `Scroller.offset_for` must scroll correctly for
  either font: check how it measures text; give it an optional `font`
  parameter mirroring `draw_text` if it measures internally.
- `/api/panel-audit?view=chars` gains optional `&font=large` rendering the
  6x10 character set through the real pipeline (`render_font_audit` gains
  a font parameter); default stays 4x6. The /panel page gets a small
  "4x6 / 6x10" toggle shown only when the Characters view is active.
- Tests: font parsing (width/height, glyph advance), text_width with both
  fonts, scroller with the large font, audit endpoint font param.

## Task 2: Display mode state, API, and dropdowns

- New `DisplayMode` str-enum (`three_ship`, `two_ship`, `one_ship`) in
  `ship_observer/models.py`.
- `Storage` gains a generic key/value settings table
  (`app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)`) created in
  the existing schema-setup path, with `async get_setting(key) -> str | None`
  and `async set_setting(key, value)`. Follow the file's existing
  migration/DDL conventions.
- `AppState` gains `display_mode: DisplayMode = DisplayMode.THREE_SHIP`.
  On service startup, load the stored mode (invalid/missing -> default).
- HTTP API: `GET /api/display-mode` -> `{"mode": "three_ship", "modes": [all three]}`;
  `POST /api/display-mode` with `{"mode": "..."}` validates (400 on bad
  mode), stores via `set_setting`, updates `AppState`, and broadcasts
  state over the websocket. `state_json()` gains `"display_mode"`.
- UI: a `<select id="display-mode">` with the three options (labels:
  "3 ship", "2 ship", "1 ship detailed") in the header of `index.html`
  and `debug.html`, styled consistently with existing selects. Shared
  wiring in `common.js`: POST on change; on any state message, set the
  select's value so all pages stay in sync.
- Rendering does not change in this task — mode is plumbing only for now.
- Tests: storage get/set round-trip and overwrite; GET/POST endpoint incl.
  validation and persistence across an AppState reload; state_json field;
  static pages contain the select (see tests/test_web_static.py style).

## Task 3: 16x16 and 32x32 icon sets + audit views

- `render/icons.py`: add `_ART_16` and `_ART_32` dicts covering every
  `ShipCategory`; `icon_for(category, size=8)` dispatches 8/16/32 (invalid
  size -> ValueError). Existing 8x8 art and call sites unchanged.
- First-pass art per the design brief below. The controller (design
  session) will visually iterate afterwards, so favor clean recognizable
  silhouettes over detail. Every grid row must be exactly as wide as the
  icon; validate via a test that checks dimensions and that only legal
  glyph characters appear, for all three sizes and all categories.
- Design brief, 32x32 (16x16 is the same composition simplified; keep
  roughly 2 blank border columns/rows so icons don't touch text):
  - PASSENGER: ferry side profile — wide hull, two stacked white decks
    with rows of accent windows, small forward wheelhouse.
  - CARGO: container ship — low long hull, 3-4 stacks of R/G/B containers
    amidships, small superstructure at the stern.
  - TANKER: long low hull with a raised centerline pipe/manifold run and
    aft superstructure block.
  - TUG: short, tall boat — big rounded pilothouse with accent windows,
    heavy bow, accent fender dots along the gunwale.
  - FISHING: a fish, nose left — oval body, forked tail, accent eye.
  - SAILING: sailboat — slim hull, tall mast, two triangular sails
    (main + jib) with a gap at the mast.
  - PLEASURE: speedboat — sleek raked hull, windshield, accent spray/wake
    at the stern.
  - PATROL: badge/shield shape with an accent star or stripe.
  - MILITARY: anchor — ring, stock, shank, curved crown with flukes.
  - OTHER: simple generic hull with a small deckhouse.
  - UNKNOWN: generic hull below a large accent question mark.
- `/api/panel-audit?view=icons` gains `&size=8|16|32` (default 8) and
  `&page=N` (default 0): render as many icons per 64x64 frame as fit in a
  grid (8 -> existing single page; 16 -> 3x3 grid, 2 pages; 32 -> 2x2
  grid, 3 pages). Response includes `"pages"` and `"page"`; the existing
  `categories` legend lists only the categories on the returned page, in
  grid order. /panel page: when Icons view is active show size buttons
  (8 / 16 / 32) and prev/next paging when `pages > 1`; legend follows.
- Tests: icon_for sizes, art validation, audit paging math, endpoint
  params.

## Task 4: Rotation + 2-ship and 1-ship layouts + dispatch

Depends on Tasks 1-3.

- `selection.py`: add a rotation view for the new modes —
  `select_rotation(live, departed, settings, page_size, page) ->
  RotationView` with fields `vessels: list[Vessel]` (at most page_size),
  `from_history: bool`, `pages: int`, `page: int` (normalized modulo
  pages). Eligible live vessels sorted with the same priority ordering
  `select_slots` uses; when none, fall back to the same recently-departed
  ordering `select_slots` uses for history (from_history=True). Empty box
  -> empty view with pages=0.
- `Service`: rotation clock — advance `page` every `DWELL_SECONDS = 10.0`
  (accumulate the render loop's dt; reset the timer and clamp the page
  when the vessel set shrinks). Mode dispatch: new
  `render/display.py::render_display(mode, canvas, ...)` routes to the
  existing `render_frame` (three_ship, unchanged behavior including
  history divider) or the new layouts. `state_json` slot numbering uses
  the on-screen vessels for the current mode.
- `render/two_ship.py`: two 32px blocks (y=0 and y=32), 1px separator
  line between. Each block: 16x16 icon at left (x=0), name to its right
  in the 6x10 font, scrolling within the remaining 47px; below the
  icon/name band: line2 `CALLSIGN > DESTINATION` (existing
  `format_line2`, 4x6, scrolling full width, same two-color split as the
  3-ship layout) and line3 `<length>  <sog>kn` in 4x6 (omit missing
  parts). Colors follow layout.py's existing constants.
- `render/one_ship.py`: stacked, flight-wall style: 32x32 icon centered
  horizontally at the top; below it the name scrolling full-width in
  6x10; then line2 (4x6, two-color, scrolling); then a details line in
  4x6: length, sog, category word; at the bottom edge a page-indicator
  dot row — one 2x2 dot per page (cap 10), current page in white, others
  dim gray, centered.
- Both new layouts: when `from_history` is true, draw a small `LAST SEEN`
  tag (4x6, `DIVIDER_TEXT_COLOR`) in place of the details line's right
  side or a dedicated band — keep it obvious but unobtrusive. Stale
  dimming (`canvas.dim`) applies exactly as in render_frame; scroller
  retain() must include only on-screen keys.
- `/api/panel-audit?view=ships` renders the sample slots through
  `render_display` with the CURRENT live display mode.
- Tests: select_rotation paging/fallback/normalization; dwell clock;
  layout geometry via rendered-canvas assertions in the existing
  tests/render style (icon placement, no out-of-bounds, dot count,
  LAST SEEN tag when from_history); dispatcher routes; ships audit uses
  current mode.

## Phase 5 (controller-driven, no subagent): visual iteration

The design session renders each mode and the new icon sets to PNGs with
fixture vessels, inspects them, and dispatches small art/layout fix tasks
until the icons read clearly. Not a subagent task.
