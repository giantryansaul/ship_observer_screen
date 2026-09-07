import pytest

from ship_observer.models import ShipCategory
from ship_observer.render.icons import (
    ART_BY_SIZE,
    CATEGORY_COLOR,
    ICON_H,
    ICON_SIZES,
    ICON_W,
    icon_for,
)

# '#' hull, '*' accent, 'R'/'G'/'B' fixed container colours, '.' transparent.
LEGAL_GLYPHS = set("#*RGB.")


def test_icon_dimensions_match_the_layout_budget():
    assert (ICON_W, ICON_H) == (8, 8)


@pytest.mark.parametrize("category", list(ShipCategory))
def test_every_category_has_an_icon_of_the_right_size(category):
    icon = icon_for(category)
    assert icon.width == ICON_W and icon.height == ICON_H
    assert len(icon.pixels) == ICON_W * ICON_H


@pytest.mark.parametrize("category", list(ShipCategory))
def test_every_icon_lights_at_least_one_pixel(category):
    assert any(p is not None for p in icon_for(category).pixels)


@pytest.mark.parametrize("category", list(ShipCategory))
def test_every_category_has_a_colour(category):
    r, g, b = CATEGORY_COLOR[category]
    assert all(0 <= c <= 255 for c in (r, g, b))


@pytest.mark.parametrize("size", ICON_SIZES)
def test_icons_are_visually_distinct(size):
    """Two categories rendering identically would make the panel useless."""
    shapes = {}
    for category in ShipCategory:
        key = tuple(p is not None for p in icon_for(category, size).pixels)
        shapes.setdefault(key, []).append(category.value)
    duplicates = {k: v for k, v in shapes.items() if len(v) > 1}
    assert not duplicates, f"identical icon shapes: {list(duplicates.values())}"


def test_icon_for_is_cached():
    assert icon_for(ShipCategory.CARGO) is icon_for(ShipCategory.CARGO)


def test_the_three_icon_sizes_are_the_supported_set():
    assert ICON_SIZES == (8, 16, 32)
    assert sorted(ART_BY_SIZE) == [8, 16, 32]


@pytest.mark.parametrize("size", ICON_SIZES)
@pytest.mark.parametrize("category", list(ShipCategory))
def test_every_category_has_square_art_of_legal_glyphs_at_every_size(
        category, size):
    """The art is hand-typed string grids, where a single dropped character
    silently shifts a whole row - so every row is checked against the icon's
    own width, and every character against the palette."""
    art = ART_BY_SIZE[size][category]
    assert len(art) == size, f"{category.value} {size}px art has {len(art)} rows"
    for index, row in enumerate(art):
        assert len(row) == size, (
            f"{category.value} {size}px art row {index} is {len(row)} wide")
        illegal = set(row) - LEGAL_GLYPHS
        assert not illegal, (
            f"{category.value} {size}px art row {index} has {illegal}")


@pytest.mark.parametrize("size", ICON_SIZES)
def test_container_colours_are_reserved_for_the_cargo_icon(size):
    """R/G/B are fixed container colours, not part of a category's palette -
    using them elsewhere would draw an icon in someone else's colour."""
    for category, art in ART_BY_SIZE[size].items():
        if category is ShipCategory.CARGO:
            continue
        assert not set("".join(art)) & set("RGB"), (
            f"{category.value} {size}px art uses a container colour")


@pytest.mark.parametrize("size", ICON_SIZES)
@pytest.mark.parametrize("category", list(ShipCategory))
def test_icon_for_renders_the_requested_size(category, size):
    icon = icon_for(category, size)
    assert (icon.width, icon.height) == (size, size)
    assert len(icon.pixels) == size * size
    assert any(p is not None for p in icon.pixels)


def test_icon_for_defaults_to_the_8px_icon():
    """Existing call sites pass no size and must keep the layout icon."""
    assert icon_for(ShipCategory.CARGO) == icon_for(ShipCategory.CARGO, 8)


@pytest.mark.parametrize("size", [0, 7, 12, 24, 64])
def test_icon_for_rejects_a_size_with_no_art(size):
    with pytest.raises(ValueError):
        icon_for(ShipCategory.CARGO, size)


@pytest.mark.parametrize("category", list(ShipCategory))
def test_every_icon_is_bright_enough_to_read_on_the_panel(category):
    """A dim icon reads as a missing icon on the LED matrix. The first weekend
    run displayed 64 unresolved vessels whose (90, 90, 90) UNKNOWN icon was
    invisible next to the white name text."""
    r, g, b = CATEGORY_COLOR[category]
    assert max(r, g, b) >= 140, (
        f"{category.value} icon colour ({r}, {g}, {b}) is too dim to see")


def test_cargo_icon_carries_multicolor_container_boxes():
    """The cargo redesign is a tanker-style hull with cargo boxes on top in
    different colors - so its palette must go beyond the single category
    color plus white accent."""
    colors = {p for p in icon_for(ShipCategory.CARGO).pixels if p is not None}
    assert len(colors) >= 4, "expected hull plus at least three box colors"
