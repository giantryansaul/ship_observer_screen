import pytest

from ship_observer.models import ShipCategory
from ship_observer.render.icons import CATEGORY_COLOR, ICON_H, ICON_W, icon_for


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


def test_icons_are_visually_distinct():
    """Two categories rendering identically would make the panel useless."""
    shapes = {}
    for category in ShipCategory:
        key = tuple(p is not None for p in icon_for(category).pixels)
        shapes.setdefault(key, []).append(category.value)
    duplicates = {k: v for k, v in shapes.items() if len(v) > 1}
    assert not duplicates, f"identical icon shapes: {list(duplicates.values())}"


def test_icon_for_is_cached():
    assert icon_for(ShipCategory.CARGO) is icon_for(ShipCategory.CARGO)
