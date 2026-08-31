from __future__ import annotations

import functools

from ..models import ShipCategory
from .canvas import RGB, Bitmap

ICON_W = 8
ICON_H = 8

# '#' is the hull, drawn in the category colour. '*' is an accent, drawn
# brighter. 'R'/'G'/'B' are fixed extra colours (used for the cargo icon's
# mixed container stack). '.' is transparent.
CATEGORY_COLOR: dict[ShipCategory, RGB] = {
    ShipCategory.PASSENGER: (80, 200, 255),
    ShipCategory.CARGO: (255, 170, 60),
    ShipCategory.TANKER: (255, 90, 90),
    ShipCategory.TUG: (200, 140, 255),
    ShipCategory.FISHING: (120, 220, 140),
    ShipCategory.SAILING: (230, 230, 230),
    ShipCategory.PLEASURE: (255, 220, 120),
    ShipCategory.PATROL: (255, 60, 60),
    ShipCategory.MILITARY: (150, 170, 190),
    ShipCategory.OTHER: (140, 140, 140),
    # Bright enough to read next to white name text: at (90, 90, 90) the
    # weekend run's 64 unresolved vessels looked like they had no icon at all.
    ShipCategory.UNKNOWN: (190, 190, 190),
}

ACCENT: RGB = (255, 255, 255)

# Container colours for the cargo icon - bright enough to read on the LED
# matrix next to the orange hull.
BOX_RED: RGB = (230, 70, 70)
BOX_GREEN: RGB = (90, 210, 110)
BOX_BLUE: RGB = (80, 140, 255)

_ART: dict[ShipCategory, list[str]] = {
    ShipCategory.PASSENGER: [   # ferry: boxy superstructure on a wide hull
        "........",
        "..####..",
        "..#..#..",
        ".######.",
        ".#....#.",
        "########",
        ".######.",
        "..####..",
    ],
    ShipCategory.CARGO: [       # tanker-style hull, mixed-colour containers
        "........",
        ".RRBBGG.",
        ".RRBBGG.",
        "########",
        "########",
        ".######.",
        "........",
        "........",
    ],
    ShipCategory.TANKER: [      # long low hull with a midships manifold
        "........",
        "........",
        "......#.",
        "..#...##",
        "########",
        "########",
        ".######.",
        "........",
    ],
    ShipCategory.TUG: [         # masts, windowed pilothouse, portholed hull
        "...#.#..",
        "..#####.",
        "..#*#*#.",
        ".######.",
        "########",
        "####*#*#",
        ".######.",
        "........",
    ],
    ShipCategory.FISHING: [     # a fish, nose left, tail right
        "........",
        "...##...",
        "..####.#",
        ".#*####.",
        "..####.#",
        "...##...",
        "........",
        "........",
    ],
    ShipCategory.SAILING: [     # triangular sail
        "...#....",
        "..##....",
        ".###....",
        "####....",
        "...#....",
        "########",
        ".######.",
        "........",
    ],
    ShipCategory.PLEASURE: [    # water skier: white head/rope/spray, towed left
        "........",
        ".....**.",
        "**#####.",
        ".....##.",
        "..#.##.*",
        "..######",
        "........",
        "........",
    ],
    ShipCategory.PATROL: [      # police badge: shield with a bright emblem
        ".######.",
        ".######.",
        ".##**##.",
        ".##**##.",
        "..####..",
        "...##...",
        "........",
        "........",
    ],
    ShipCategory.MILITARY: [    # anchor: ring, stock, shank, crown
        "...**...",
        "..####..",
        "...##...",
        "...##...",
        "#..##..#",
        "##.##.##",
        ".######.",
        "........",
    ],
    ShipCategory.OTHER: [       # generic hull
        "........",
        "........",
        "........",
        "...##...",
        "..####..",
        "########",
        ".######.",
        "........",
    ],
    ShipCategory.UNKNOWN: [     # generic hull with a question mark above
        "..***...",
        "....*...",
        "...*....",
        "........",
        "...*....",
        "########",
        ".######.",
        "........",
    ],
}


@functools.lru_cache(maxsize=None)
def icon_for(category: ShipCategory) -> Bitmap:
    art = _ART[category]
    palette = {"#": CATEGORY_COLOR[category], "*": ACCENT,
               "R": BOX_RED, "G": BOX_GREEN, "B": BOX_BLUE}
    return Bitmap.from_art(art, palette)
