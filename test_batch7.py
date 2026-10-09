"""Pure-Python checks for the selection-driven map builder (core/mapbuilder)."""
from __future__ import annotations

from core import mapbuilder
from core.mapbuilder import build_map, make_item, pack_rows, pack_scatter


def asset(path, w, h, name=None, size=None):
    return {"path": path, "name": name or path.rsplit("/", 1)[-1],
            "w": w, "h": h, "size": size}


def boxes(result):
    return [piece["_box"] for piece in result["pieces"]]


def overlaps(rects) -> bool:
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            if not (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0]
                    or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1]):
                return True
    return False


def main():
    cs = 70
    # a 280 px square is 4 squares; a name-coded 100x100 ft deck is 20 squares
    tiles = [asset(f"pack/floors/deck_{i}.png", 280, 280) for i in range(4)]
    deck = asset("pack/core/E101 [100x100] Bridge.png", 4800, 4800,
                 size=(100, 100))
    strip = asset("pack/floors/corridor.png", 280, 140)

    # -- only the assets handed over are used, and all of them are ----------
    result = build_map({"selection": tiles, "cell_size": cs, "region": (0, 0, 39, 39),
                        "layout": "grid", "seed": 1})
    used = {piece["asset_path"] for piece in result["pieces"]}
    assert used == {item["path"] for item in tiles}, used
    assert result["counts"]["assets"] == 4
    assert result["mode"] == "selection"

    # -- an empty selection is refused, never guessed at --------------------
    for missing in ({}, {"selection": []}, {"selection": [asset("x.png", 0, 0)]}):
        empty = build_map(dict(missing, region=(0, 0, 9, 9)))
        assert empty["pieces"] == [] and empty["warnings"], missing

    # -- placements sit on whole grid squares and never overlap -------------
    for layout in mapbuilder.LAYOUTS:
        for gap in (0, 1, 3):
            run = build_map({"selection": tiles + [strip, deck], "cell_size": cs,
                             "region": (5, 7, 44, 46), "layout": layout,
                             "copies": 3, "gap": gap, "rotate": True, "seed": 11})
            assert run["pieces"], (layout, gap)
            rects = boxes(run)
            assert not overlaps(rects), f"{layout} gap={gap} overlapped"
            for x, y, w, h in rects:
                assert abs(x / cs - round(x / cs)) < 1e-6, (layout, x)
                assert abs(y / cs - round(y / cs)) < 1e-6, (layout, y)
                assert w > 0 and h > 0
            # nothing lands outside the region that was asked for
            for x, y, w, h in rects:
                assert x >= 5 * cs - 1e-6 and y >= 7 * cs - 1e-6
                assert x + w <= 45 * cs + 1e-6, (layout, x, w)
                assert y + h <= 47 * cs + 1e-6, (layout, y, h)
            used_w, used_h = run["used_cells"]
            assert used_w <= 40 and used_h <= 40, run["used_cells"]

    # -- pieces carry what the project needs to rebuild them ---------------
    piece = result["pieces"][0]
    for key in ("asset_path", "name", "x", "y", "w", "h", "scale", "rotation",
                "layer_name"):
        assert key in piece, key
    assert piece["rotation"] in mapbuilder.ROTATIONS
    assert piece["layer_name"] == "Generated"
    assert build_map({"selection": tiles, "cell_size": cs, "region": (0, 0, 9, 9),
                      "layer_name": "Floor"})["pieces"][0]["layer_name"] == "Floor"

    # -- the row packer walks left to right, then wraps --------------------
    order = [make_item(item, cs, 5) for item in tiles]
    placements, skipped, full = pack_rows(order * 2, 0, 0, 16, 16, 0)
    assert skipped == 0 and not full
    assert [placed[1:3] for placed in placements][:4] == \
        [(0, 0), (4, 0), (8, 0), (12, 0)], placements
    assert placements[4][1:3] == (0, 4), "the fifth copy wraps to the next row"
    assert len(placements) == 8

    # -- an asset too tall for the space left is passed over, not fatal ----
    tall = make_item(asset("tall.png", 70, 700), cs, 5)
    small = make_item(asset("small.png", 70, 70), cs, 5)
    placements, skipped, full = pack_rows([tall, small], 0, 0, 4, 4, 0)
    assert skipped == 1 and not full
    assert [item["name"] for item, *_ in placements] == ["small.png"]

    # -- scatter keeps everything apart ------------------------------------
    many = [make_item(asset(f"p{i}.png", 140, 140), cs, 5) for i in range(20)]
    import random
    placements, skipped = pack_scatter(random.Random(3), many * 3, 0, 0, 40, 40, 1)
    assert skipped == 0
    rects = [(x * cs, y * cs, item["cells_w"] * cs, item["cells_h"] * cs)
             for item, x, y, _rot in placements]
    assert not overlaps(rects), "scatter overlapped two pieces"

    # -- copies and the reported counts -----------------------------------
    one = build_map({"selection": tiles[:1], "cell_size": cs,
                     "region": (0, 0, 39, 39), "copies": 5})
    assert one["counts"]["pieces"] == 5 and one["counts"]["assets"] == 1
    assert len({piece["x"] for piece in one["pieces"]}) > 1, "copies are placed apart"

    # -- a full area is reported, and a part-filled one says so -----------
    filled = build_map({"selection": tiles, "cell_size": cs,
                        "region": (0, 0, 19, 19), "layout": "fill"})
    assert filled["used_cells"] == (20, 20), filled["used_cells"]
    assert not any("left empty" in w for w in filled["warnings"])
    sparse = build_map({"selection": tiles[:1], "cell_size": cs,
                        "region": (0, 0, 39, 39)})
    assert any("left empty" in w for w in sparse["warnings"]), sparse["warnings"]

    print("Batch 7 selection-driven map builder checks passed.")


if __name__ == "__main__":
    main()
