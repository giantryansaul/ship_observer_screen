# Ship Observer

An LED-matrix panel that shows the vessels currently passing a fixed stretch of water, from a live AIS feed. This glossary fixes the words used for what a vessel is, how it is identified, and how it is drawn.

## Language

### Vessels and sightings

**Vessel**:
A single hull, identified by its MMSI. Facts about a vessel outlive any one sighting of it.
_Avoid_: Ship (except in display-mode names and user-facing copy), boat, target

**Visit**:
One continuous presence of a vessel inside the watched area, from entry to departure. A vessel that leaves and returns makes a new visit.
_Avoid_: Sighting, track, session

**Static data**:
What a vessel broadcasts about itself rather than about its movement: name, call sign, IMO number, type, dimensions, destination. It arrives rarely and sometimes never.
_Avoid_: Metadata, details

**Vessel store**:
The permanent record of every vessel ever seen, one entry per MMSI, holding each source's answer about it untouched. It outlives the visit log, which forgets after seven days. A vessel whose category comes from it is **remembered**.
_Avoid_: Cache, static cache, registry (that is the in-memory set of current visits)

**Regular**:
A vessel seen on repeated visits, such as a ferry on its route or a harbour tug.

### Identity

**Vessel identity**:
Everything known about what a vessel is, merged from every source: its category, silhouette, flag and operator. It is the single thing the display draws from.
_Avoid_: Profile, enrichment, vessel info

**Category**:
The coarse kind of vessel (cargo, tanker, passenger, tug, …) that decides display priority and filtering.
_Avoid_: Type (that is the raw AIS code), class

**Silhouette**:
The finer kind of vessel that decides which shape is drawn (container ship, bulk carrier, car carrier, cruise ship, …). Every silhouette belongs to one category, and a vessel with no known silhouette is drawn as its category.
_Avoid_: Subtype, icon type

**Unknown**:
The category of a vessel whose kind nobody has told us, either because no static data arrived or because it broadcast "not available". An unknown vessel is a candidate for lookup.
_Avoid_: Unresolved, untyped

**Other**:
The category of a vessel that did state its kind, and that kind is one the panel has no specific category for. Other is an answer; unknown is the absence of one.

**Flag**:
The country of registry, derived from the vessel's MMSI.
_Avoid_: Country, nationality

**Operator**:
The brand a vessel sails for, as a person on shore would recognise it (Maersk, MSC, Washington State Ferries). It is not the legal registered owner, which is often a single-ship shell company.
_Avoid_: Owner, company, line

**Lookup**:
Asking a source other than the vessel's own broadcast what a vessel is.
_Avoid_: Enrichment, resolution

**Override**:
A hand-written statement about a vessel or a family of vessels that beats every other source.

### Drawing

**Panel**:
One physical 64×64 LED matrix module.

**Board**:
The whole display surface, made of one or more panels. The single board is 64×64; the double board is two panels side by side, 128×64.
_Avoid_: Screen, display (ambiguous with display mode), wall

**Display mode**:
How many vessels the board shows at once and how each is laid out: 3-ship, 2-ship or 1-ship.
_Avoid_: View, layout (a layout is one mode at one board width)

**Art strip**:
The wide, flat area of a layout that holds a vessel's side-profile drawing.
_Avoid_: Icon slot, logo area

**Sprite**:
The drawing of one silhouette at one width, with its colours left as named parts to be filled in.
_Avoid_: Icon (the legacy square category drawings), image

**Livery**:
The set of colours an operator's vessels wear (hull, superstructure, funnel, deck, accent), applied to a sprite. A vessel with no known operator wears its category's default livery.
_Avoid_: Logo, theme, skin

**Flag chip**:
The small drawing of a vessel's flag shown beside its art strip.

**Preview size**:
A board size, other than the real board's, that is drawn from the same live vessels so a layout can be judged without the hardware.
_Avoid_: Mock panel, simulator, virtual panel
