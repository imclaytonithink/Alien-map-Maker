"""Seeds, oversized assets, warnings and the canvas the build needs.

A build is reproducible: the same seed, selection and settings always produce
the same map, and a different seed produces a different one. Assets too big for
the area are scaled down and reported rather than dropped silently, and the
result says how much canvas it needs so the app can grow the map.
"""
from __future__ import annotations

from core.mapbuilder import build_map, make_item
from core.seeds import clean_seed, coerce_seed, new_seed


def asset(path, w, h, name=None, size=None):
    return {"path": path, "name": name or path.rsplit("/", 1)[-1],
            "w": w, "h": h, "size": size}


def make_scale(entry, cell):
    return make_item(entry, cell, 5)["scale"]


def fingerprint(result):
    return [(piece["asset_path"], round(piece["x"], 4), round(piece["y"], 4),
             piece["rotation"]) for piece in result["pieces"]]


def main():
    cs = 70
    tiles = [asset(f"pack/deck_{i}.png", 280, 280) for i in range(4)]
    base = {"selection": tiles, "cell_size": cs, "feet_per_square": 5,
            "region": (0, 0, 29, 29)}

    # -- the same seed repeats exactly ----------------------------------
    for layout in ("grid", "scatter", "fill"):
        for seed in (1, 42, "hangar-7", new_seed()):
            opts = dict(base, layout=layout, seed=clean_seed(seed), copies=3,
                        rotate=True, shuffle=True)
            first = build_map(opts)
            second = build_map(opts)
            assert fingerprint(first) == fingerprint(second), (layout, seed)
            assert first["seed"] == second["seed"] == clean_seed(seed)
            assert coerce_seed(first["seed"]) == coerce_seed(second["seed"])

    # -- a different seed builds differently ----------------------------
    a = build_map(dict(base, layout="scatter", seed=1, copies=6, rotate=True))
    b = build_map(dict(base, layout="scatter", seed=2, copies=6, rotate=True))
    assert fingerprint(a) != fingerprint(b)
    # word seeds and number seeds are different seeds
    assert coerce_seed("hangar-7") != coerce_seed("7")

    # -- a different area or selection changes the build -----------------
    moved = build_map(dict(base, layout="grid", region=(3, 2, 32, 31)))
    assert fingerprint(moved) != fingerprint(
        build_map(dict(base, layout="grid", region=(0, 0, 29, 29))))
    assert moved["pieces"][0]["_box"][:2] == (3 * 70, 2 * 70), moved["pieces"][0]
    assert fingerprint(build_map(dict(base, layout="scatter", seed=3, copies=2))) \
        != fingerprint(build_map(dict(base, layout="scatter", seed=3, copies=5)))
    assert fingerprint(build_map(dict(base, layout="scatter", seed=3, copies=6,
                                      region=(0, 0, 29, 29)))) \
        != fingerprint(build_map(dict(base, layout="scatter", seed=3, copies=6,
                                      region=(0, 0, 60, 60))))

    # -- an asset too big for the area is reported, never quietly resized -
    huge = asset("pack/mega.png", 7000, 7000)           # 100 x 100 squares
    result = build_map({"selection": [huge], "cell_size": cs,
                        "region": (0, 0, 9, 9)})
    assert not result["pieces"]
    assert result["counts"]["skipped"] == 1
    assert any("bigger than that area" in warning for warning in result["warnings"]), \
        result["warnings"]
    assert any("mega.png" in warning for warning in result["warnings"])
    # the same asset keeps its own scale once the area is big enough
    fits = build_map({"selection": [huge], "cell_size": cs, "region": (0, 0, 119, 119)})
    assert fits["counts"]["pieces"] == 1
    assert not any("bigger than that area" in w for w in fits["warnings"])
    assert abs(fits["pieces"][0]["scale"] - make_scale(huge, cs)) < 1e-12

    # -- rotation can be what makes an asset fit ------------------------
    strip = asset("pack/long.png", 70 * 40, 70)         # 40 x 1 squares
    tight = build_map({"selection": [strip], "cell_size": cs,
                       "region": (0, 0, 4, 4), "rotate": True})
    assert not tight["pieces"] and tight["counts"]["skipped"] == 1
    assert any("allow rotation" in warning or "bigger than that area" in warning
               for warning in tight["warnings"]), tight["warnings"]
    # turned sideways it is 1 x 40, which a tall narrow area holds
    turned = build_map({"selection": [strip], "cell_size": cs,
                        "region": (0, 0, 1, 40), "rotate": True})
    assert turned["counts"]["pieces"] == 1
    assert turned["pieces"][0]["rotation"] in (90, 270), turned["pieces"][0]
    # and lowering the asset size is the other way out
    smaller = build_map({"selection": [huge], "cell_size": cs,
                         "region": (0, 0, 19, 19), "scale": 0.2})
    assert smaller["counts"]["pieces"] == 1, smaller["warnings"]

    # -- the result says how much canvas the build needs ----------------
    small = build_map(dict(base, layout="grid", region=(0, 0, 29, 29)))
    assert small["canvas_cells"] == small["used_cells"]
    assert small["region_cells"] == (30, 30)
    offset = build_map(dict(base, layout="grid", region=(6, 4, 35, 33)))
    assert offset["canvas_cells"] == (6 + offset["used_cells"][0],
                                      4 + offset["used_cells"][1])
    filled = build_map(dict(base, layout="fill", region=(0, 0, 23, 23)))
    assert filled["canvas_cells"] == (24, 24)

    # -- a part-filled area is reported; a full one is not --------------
    assert any("left empty" in warning for warning in small["warnings"])
    assert not any("left empty" in warning for warning in filled["warnings"])

    # -- copies that do not fit are counted and reported ----------------
    crammed = build_map({"selection": [asset("pack/big.png", 70 * 12, 70 * 12)],
                         "cell_size": cs, "region": (0, 0, 29, 29), "copies": 9})
    assert crammed["counts"]["pieces"] == 4, crammed["counts"]
    assert crammed["counts"]["skipped"] == 5
    assert any("did not fit" in warning for warning in crammed["warnings"])

    # -- nothing about the asset is invented: no roles, no categories ---
    for key in ("pools", "roles", "categories", "strategy"):
        assert key not in base
    result = build_map(dict(base, layout="scatter", seed=9))
    assert result["mode"] == "selection" and result["setting"] == "Selection"
    assert "connected" in result and result["counts"]["assets"] == 4

    print("Batch 14 seed and sizing-safety checks passed.")


if __name__ == "__main__":
    main()
