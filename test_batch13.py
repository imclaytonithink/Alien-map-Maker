"""Layouts: tidy rows, random scatter and filling the area.

Each layout is checked for what it promises - rows read left to right and wrap,
scatter spreads copies apart without overlaps, fill keeps going until the area
is covered - and for the controls around them: copies, spacing, rotation and
shuffling.
"""
from __future__ import annotations

from core.mapbuilder import LAYOUTS, build_map


def asset(path, w, h, name=None, size=None):
    return {"path": path, "name": name or path.rsplit("/", 1)[-1],
            "w": w, "h": h, "size": size}


def cells(result):
    """Occupied grid cells of every piece, as (col, row)."""
    out = []
    for piece in result["pieces"]:
        x, y, w, h = piece["_box"]
        cs = 70
        for col in range(int(round(x / cs)), int(round((x + w) / cs + 0.4))):
            for row in range(int(round(y / cs)), int(round((y + h) / cs + 0.4))):
                out.append((col, row))
    return out


def main():
    cs = 70
    tiles = [asset(f"pack/deck_{i}.png", 280, 280) for i in range(3)]   # 4x4 squares
    base = {"selection": tiles, "cell_size": cs, "feet_per_square": 5}

    # -- tidy rows: left to right, wrapping ------------------------------
    rows = build_map(dict(base, layout="grid", region=(0, 0, 11, 11), gap=0,
                          copies=2))
    origins = [(round(p["x"] / cs), round(p["y"] / cs)) for p in rows["pieces"]]
    assert origins == [(0, 0), (4, 0), (8, 0), (0, 4), (4, 4), (8, 4)], origins
    assert rows["counts"]["pieces"] == 6 and rows["counts"]["assets"] == 3

    # copies repeat the whole selection
    twice = build_map(dict(base, layout="grid", region=(0, 0, 11, 11), copies=2))
    assert twice["counts"]["pieces"] == 6 and twice["counts"]["assets"] == 3

    # spacing keeps whole grid squares between placements
    spaced = build_map(dict(base, layout="grid", region=(0, 0, 39, 39), gap=2))
    xs = sorted(round(p["x"] / cs) for p in spaced["pieces"])
    assert xs == [0, 6, 12], xs

    # an unshuffled grid keeps the library's own order
    ordered = build_map(dict(base, layout="grid", region=(0, 0, 39, 39),
                             shuffle=False))
    assert [p["asset_path"] for p in ordered["pieces"]] == \
        [item["path"] for item in tiles]
    shuffled = build_map(dict(base, layout="grid", region=(0, 0, 39, 39),
                              copies=4, shuffle=True, seed=5))
    assert sorted(p["asset_path"] for p in shuffled["pieces"]) == \
        sorted([item["path"] for item in tiles] * 4)

    # -- scatter: spread out, never on top of each other -----------------
    scatter = build_map(dict(base, layout="scatter", region=(0, 0, 39, 39),
                             copies=12, seed=17))
    assert scatter["counts"]["pieces"] == 36
    assert len(set(cells(scatter))) == len(cells(scatter)), "scatter overlapped"
    xs = {round(p["x"] / cs) for p in scatter["pieces"]}
    ys = {round(p["y"] / cs) for p in scatter["pieces"]}
    assert len(xs) > 3 and len(ys) > 3, "scatter should spread across the area"
    # spacing is honoured there too
    tight = build_map(dict(base, layout="scatter", region=(0, 0, 39, 39),
                           copies=12, gap=3, seed=17))
    assert len(set(cells(tight))) == len(cells(tight))

    # -- fill: repeats until the area is covered -------------------------
    filled = build_map(dict(base, layout="fill", region=(0, 0, 19, 19)))
    assert filled["used_cells"] == (20, 20), filled["used_cells"]
    assert filled["counts"]["pieces"] == 25, filled["counts"]["pieces"]
    assert len(set(cells(filled))) == len(cells(filled)), "fill overlapped"
    # copies are ignored while filling - the selection simply repeats
    assert build_map(dict(base, layout="fill", region=(0, 0, 19, 19),
                          copies=9))["counts"]["pieces"] == 25
    # mixed sizes still tile the area completely
    mixed = build_map({"selection": tiles + [asset("pack/wide.png", 560, 280)],
                       "cell_size": cs, "layout": "fill", "region": (0, 0, 23, 23)})
    assert mixed["used_cells"] == (24, 24), mixed["used_cells"]

    # -- rotation --------------------------------------------------------
    strip = asset("pack/strip.png", 560, 140)          # 8x2 squares
    upright = build_map({"selection": [strip], "cell_size": cs,
                         "region": (0, 0, 1, 11), "layout": "scatter", "seed": 2})
    assert upright["counts"]["skipped"] == 1 and not upright["pieces"], \
        "an 8-square strip cannot fit a 2-square-wide area upright"
    turned = build_map({"selection": [strip], "cell_size": cs,
                        "region": (0, 0, 1, 11), "layout": "scatter",
                        "rotate": True, "seed": 2})
    assert turned["counts"]["pieces"] == 1
    assert turned["pieces"][0]["rotation"] in (90, 270), turned["pieces"][0]
    assert all(p["rotation"] == 0 for p in
               build_map(dict(base, layout="scatter", region=(0, 0, 39, 39),
                              copies=4, rotate=False))["pieces"])

    # -- every layout is offered and named -------------------------------
    assert LAYOUTS == ("grid", "scatter", "fill")
    from core.mapbuilder import LAYOUT_ABOUT, LAYOUT_LABELS
    assert set(LAYOUT_LABELS) == set(LAYOUTS) == set(LAYOUT_ABOUT)
    # an unknown layout falls back to tidy rows rather than failing
    assert build_map(dict(base, layout="nonsense",
                          region=(0, 0, 11, 11)))["layout"] == "grid"

    print("Batch 13 layout checks passed.")


if __name__ == "__main__":
    main()
