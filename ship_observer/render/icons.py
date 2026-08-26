from __future__ import annotations

import functools

from ..models import ShipCategory
from .canvas import RGB, Bitmap

ICON_W = 8
ICON_H = 8

# '#' is the hull, drawn in the category colour. '*' is an accent, drawn
# brighter. '.' is transparent.
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
    ShipCategory.UNKNOWN: (90, 90, 90),
}

ACCENT: RGB = (255, 255, 255)

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
    ShipCategory.CARGO: [       # box boat: stacked containers
        "........",
        "#.##.##.",
        "########",
        "#.##.##.",
        "########",
        "########",
        ".######.",
        "..####..",
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
    ShipCategory.TUG: [         # short with a tall wheelhouse
        "........",
        "...##...",
        "...##...",
        "..####..",
        ".######.",
        "########",
        ".#####..",
        "........",
    ],
    ShipCategory.FISHING: [     # trawler with net boom
        "..#.....",
        "..#..#..",
        "..#..#..",
        "..####..",
        ".######.",
        "########",
        ".#####..",
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
    ShipCategory.PLEASURE: [    # low powerboat with a windshield
        "........",
        "........",
        "....##..",
        "...####.",
        "..######",
        "########",
        ".#####..",
        "........",
    ],
    ShipCategory.PATROL: [      # light bar
        "..*..*..",
        "..####..",
        "..#..#..",
        ".######.",
        "########",
        "########",
        ".#####..",
        "........",
    ],
    ShipCategory.MILITARY: [    # mast and low profile
        "....#...",
        "....#...",
        "..#.#...",
        "..###...",
        ".#####..",
        "########",
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
        "..###...",
        "....#...",
        "...#....",
        "........",
        "...#....",
        "########",
        ".######.",
        "........",
    ],
}


@functools.lru_cache(maxsize=None)
def icon_for(category: ShipCategory) -> Bitmap:
    art = _ART[category]
    palette = {"#": CATEGORY_COLOR[category], "*": ACCENT}
    return Bitmap.from_art(art, palette)
