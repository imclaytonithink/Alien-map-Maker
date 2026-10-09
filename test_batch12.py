"""How big a selected asset is on the map.

The map builder never guesses what an asset *is*, but it does have to know how
much floor it covers. These are the real naming patterns from the published
high-resolution packs: a name that codes a size in feet on a high-resolution
image is sized by feet-per-square (exactly as the canvas sizes it when you drag
it over by hand); anything else keeps its own pixel size.
"""
from __future__ import annotations

from core.asset_manager import Asset
from core.mapbuilder import describe_selection, make_item, world_size
from core.project import parse_size_from_name

CELL = 70          # map pixels per grid square
FEET = 5           # feet per grid square


def make(name, folder="Pack/100x100 Core", pixels=None, overlay=False):
    size = parse_size_from_name(name)
    if pixels is None:
        pixels = (max(size) * 72 - 1) if size else 600
    h = pixels if not size or size[0] == size[1] else int(pixels * size[1] / size[0])
    return Asset(path=f"{folder}/{name}", name=name, folder=folder, size=size,
                 is_overlay=overlay, width=pixels, height=h)


# (name, folder, expected footprint in grid squares)
NAMED_CASES = [
    ("E101 [100x100] Tractor Beam Control.png", "Pack/100x100 Core", (20, 20)),
    ("E757 [100x100] Reactor Hall, 37 Consoles.png", "Pack/100x100 Core", (20, 20)),
    ("M101 [200x100] Tractor Beam Bay.png", "Pack/200x100 Mega", (40, 20)),
    ("M006 [200x100] Flight Deck - Vertical.png", "Pack/200x100 Mega", (40, 20)),
    ("A124 [100x200] Port (Concorde).png", "Pack/Aero", (20, 40)),
    ("A139 [50x200] Port (4x Gunnery).png", "Pack/Aero", (10, 40)),
    ("E758 [50x50] 12 Stations, Lounge.png", "Pack/50x50 Core", (10, 10)),
    ("501 [50x50] Fuel Deck (Intake Scoop).png", "Pack/50x50 Core", (10, 10)),
]

# Small art whose name happens to hold a size stays pixel-sized: the image is
# far too small for "[100x100]" to mean feet.
PIXEL_CASES = [
    ("room_100x100.png", "floors", (100, 100), (2, 2)),
    ("wall_25x50.png", "walls", (25, 50), (1, 1)),
    ("corridor_40x120.png", "floors", (40, 120), (1, 2)),
    ("keyboard_40x15.png", "props", (40, 15), (1, 1)),
    ("Mystery.png", "misc", (64, 32), (1, 1)),
]


def main():
    # -- a named high-resolution deck is sized in feet --------------------
    for name, folder, expected in NAMED_CASES:
        item = make_item(make(name, folder), CELL, FEET)
        assert (item["cells_w"], item["cells_h"]) == expected, \
            (name, item["cells_w"], item["cells_h"], expected)

    # -- and it matches what the canvas does with the same asset ----------
    asset = make(NAMED_CASES[0][0], NAMED_CASES[0][1])
    w, h = world_size(asset.width, asset.height, asset.size, CELL, FEET)
    item = make_item(asset, CELL, FEET)
    assert abs(item["w"] * item["scale"] - w) < 1e-6
    assert abs(item["h"] * item["scale"] - h) < 1e-6
    # 100 ft at 5 ft per square = 20 squares of 70 px
    assert abs(w - 20 * CELL) < 1e-6, w
    assert item["scale"] < 1.0, "a 7199 px deck is scaled down to the map"

    # -- ordinary pixel art keeps its own pixel size ----------------------
    for name, folder, pixels, expected in PIXEL_CASES:
        asset = make(name, folder, pixels=max(pixels))
        asset.width, asset.height = pixels
        asset.size = parse_size_from_name(name) if name == "Mystery.png" else None
        item = make_item(asset, CELL, FEET)
        assert abs(item["scale"] - 1.0) < 1e-9, (name, item["scale"])
        assert (item["cells_w"], item["cells_h"]) == expected, \
            (name, item["cells_w"], item["cells_h"])

    # a size in the name only means feet when the image is high resolution
    small = make("room_100x100.png", "floors", pixels=100)
    assert abs(make_item(small, CELL, FEET)["scale"] - 1.0) < 1e-9

    # -- feet-per-square changes the footprint, not the picture -----------
    deck = make(NAMED_CASES[0][0], NAMED_CASES[0][1])
    assert make_item(deck, CELL, 10)["cells_w"] == 10
    assert make_item(deck, CELL, 5)["cells_w"] == 20

    # -- the size control scales every selected asset --------------------
    assert make_item(deck, CELL, FEET, 0.5)["cells_w"] == 10
    assert make_item(deck, CELL, FEET, 2.0)["cells_w"] == 40
    assert make_item(deck, CELL, FEET, 0)["scale"] > 0, "a 0% size is clamped"

    # -- a footprint is never smaller than one square --------------------
    tiny = Asset(path="misc/dot.png", name="dot.png", folder="misc",
                 width=3, height=3)
    assert (make_item(tiny, CELL, FEET)["cells_w"],
            make_item(tiny, CELL, FEET)["cells_h"]) == (1, 1)

    # -- assets with no readable size are simply not placeable -----------
    assert make_item(Asset(path="x.png", name="x.png", folder="."), CELL, FEET) is None
    assert make_item({"path": "", "name": "", "w": 10, "h": 10}, CELL, FEET) is None

    # -- the dialog's summary reports the same numbers -------------------
    rows = describe_selection([make(n, f) for n, f, _e in NAMED_CASES], CELL, FEET)
    assert [row["cells_w"] for row in rows] == [20, 20, 40, 40, 20, 10, 10, 10]
    assert rows[0]["px_w"] == make(NAMED_CASES[0][0], NAMED_CASES[0][1]).width

    print("Batch 12 asset sizing checks passed.")


if __name__ == "__main__":
    main()
