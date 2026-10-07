"""Role-driven generation: pools, assembly packing, hull parts, furnishing, seeds."""
from __future__ import annotations

import random

from core.asset_manager import Asset
from core.asset_roles import classify_roles
from core.assembly import (build_pools, categories_from_roles, furnish_rooms,
                           generate_assembly, generate_map)
from core.project import parse_size_from_name
from core.seeds import clean_seed, coerce_seed, new_seed

PPS = 300          # source pixels per 5-ft square (matches the real packs)


def asset(name, folder, w_px=None, h_px=None, overlay=False):
    size = parse_size_from_name(name)
    if size and w_px is None:
        # image = core cells + 2 border squares on each side, like the real packs
        w_px = int((size[0] / 5 + 4) * PPS)
        h_px = int((size[1] / 5 + 4) * PPS)
    return Asset(path=f"{folder}/{name}", name=name, folder=folder, size=size,
                 is_overlay=overlay, width=w_px or 400, height=h_px or 400)


def library():
    items = []
    for i in range(6):
        items.append(asset(f"E10{i} [100x100] Deck {i}.png", "Pack/100x100 Core"))
        items.append(asset(f"E10{i} [100x100] Deck {i} [Overlay].png",
                           "Pack/100x100 Core/Overlays", overlay=True))
    for i in range(4):
        items.append(asset(f"M10{i} [200x100] Mega {i}.png", "Pack/200x100 Mega"))
        items.append(asset(f"E7{i}0 [50x50] Station {i}.png", "Pack/50x50 Core"))
    items.append(asset("B004 [200x100] Blank.png", "Pack/200x100 Mega"))
    items.append(asset("B005 [100x100] Blank.png", "Pack/100x100 Core"))
    items.append(asset("A123 [100x50] Nose, Fuel.png", "Pack/Aero"))
    items.append(asset("A124 [100x200] Port (Concorde).png", "Pack/Aero"))
    items.append(asset("A125 [100x200] Starboard (Concorde).png", "Pack/Aero"))
    for i in range(3):
        items.append(asset(f"Locker {i}.png", "Pack/Symbols/Storage", 600, 300))
        items.append(asset(f"Cot {i}.png", "Pack/Symbols/Furniture", 300, 600))
    items.append(asset("Arrow.png", "Pack/Symbols", 200, 200))
    return items


def overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def cell_boxes(pieces, cs, pps):
    boxes = []
    for p in pieces:
        if p["layer_name"] not in ("Geomorphs", "Hull"):
            continue
        w, h = p["w"] * p["scale"], p["h"] * p["scale"]
        cx, cy = p["x"] + w / 2, p["y"] + h / 2
        boxes.append((cx, cy, w, h, p["rotation"]))
    return boxes


def main():
    assets = library()
    roles = classify_roles(assets)
    pools = build_pools(assets, roles, 5)
    counts = pools["counts"]
    assert counts["deck_plan"] >= 10 and counts["room"] == 4 and counts["empty_room"] == 2
    assert counts["ship_part"] == 3 and counts["overlay"] == 6
    assert counts["interior_part"] == 6 and counts["symbol"] == 1
    assert abs(pools["pixels_per_square"] - PPS) < 1, pools["pixels_per_square"]
    deck = next(i for i in pools["modules"]["deck_plan"] if i["feet_w"] == 100)
    assert deck["border_x"] == 2 and deck["border_y"] == 2
    mega = next(i for i in pools["modules"]["deck_plan"] if i["feet_w"] == 200)
    assert mega["cells_w"] == 40 and mega["border_y"] == 2

    cs = 70
    base = {"strategy": "assembly", "cell_size": cs, "feet_per_square": 5,
            "pools": pools, "region": (0, 0, 59, 59), "seed": "123456789012",
            "roles": ["deck_plan", "room", "empty_room"]}

    # mixed packing fills the area with non-overlapping modules in several sizes
    mixed = generate_map(dict(base, packing="mixed", empty_share=0.2))
    boxes = cell_boxes(mixed["pieces"], cs, PPS)
    assert mixed["counts"]["modules"] >= 4 and boxes
    cores = []
    for cx, cy, w, h, rot in boxes:
        # core rectangle = image minus the 2-square border, centered
        cw, ch = w - 4 * cs, h - 4 * cs
        if rot in (90, 270):
            cw, ch = ch, cw
        cores.append((cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2))
    for i, a in enumerate(cores):
        assert a[0] >= -1e-6 and a[1] >= -1e-6, a
        assert a[2] <= 60 * cs + 1e-6 and a[3] <= 60 * cs + 1e-6, a
        for b in cores[i + 1:]:
            assert not overlap(a, b), (a, b)
    sizes = {(round((c[2] - c[0]) / cs), round((c[3] - c[1]) / cs)) for c in cores}
    assert len(sizes) >= 2, sizes            # a genuine mix of footprints
    assert mixed["counts"]["overlays"] >= 0

    # same seed -> same map; different seed -> different map; text seeds work
    again = generate_map(dict(base, packing="mixed", empty_share=0.2))
    assert [(p["asset_path"], p["x"], p["y"], p["rotation"]) for p in again["pieces"]] == \
           [(p["asset_path"], p["x"], p["y"], p["rotation"]) for p in mixed["pieces"]]
    other = generate_map(dict(base, packing="mixed", empty_share=0.2, seed="999999999999"))
    assert [(p["asset_path"], p["x"], p["y"]) for p in other["pieces"]] != \
           [(p["asset_path"], p["x"], p["y"]) for p in mixed["pieces"]]
    text_a = generate_map(dict(base, seed="hangar-seven"))
    text_b = generate_map(dict(base, seed="hangar-seven"))
    assert [p["asset_path"] for p in text_a["pieces"]] == [p["asset_path"] for p in text_b["pieces"]]

    # roles can be switched off: only rooms -> only 50x50 stations
    rooms_only = generate_map(dict(base, roles=["room"], packing="uniform"))
    assert rooms_only["counts"]["modules"] == 36 and all(
        "Station" in p["asset_path"] for p in rooms_only["pieces"]
        if p["layer_name"] == "Geomorphs")
    # empty-room share steers the mix
    none_empty = generate_map(dict(base, empty_share=0.0))
    assert not any("Blank" in p["asset_path"] for p in none_empty["pieces"])
    mostly_empty = generate_map(dict(base, empty_share=1.0, roles=["deck_plan", "empty_room"]))
    blanks = sum("Blank" in p["asset_path"] for p in mostly_empty["pieces"]
                 if p["layer_name"] == "Geomorphs")
    assert blanks >= mostly_empty["counts"]["modules"] // 2, mostly_empty["counts"]

    # uniform packing keeps one footprint
    uniform = generate_map(dict(base, packing="uniform", roles=["deck_plan"]))
    footprints = {(round(p["w"] * p["scale"] / cs), round(p["h"] * p["scale"] / cs))
                  for p in uniform["pieces"] if p["layer_name"] == "Geomorphs"}
    assert len(footprints) == 1, footprints

    # too-small area gives a clear message instead of nothing
    tiny = generate_map(dict(base, region=(0, 0, 4, 4)))
    assert not tiny["pieces"] and tiny["warnings"]

    # hull parts surround the interior and ask for a bigger canvas
    hulled = generate_map(dict(base, hull=True, packing="uniform",
                               roles=["deck_plan"]))
    layers = [p["layer_name"] for p in hulled["pieces"]]
    assert layers.count("Hull") == 3, layers.count("Hull")
    total_w, total_h = hulled["canvas_cells"]
    assert total_w == 60 + 20 + 20 and total_h >= 60 + 10, hulled["canvas_cells"]
    assert hulled["interior_origin"] == (20, 10)

    # furnishing: parts land inside the room, never overlap, and respect margins
    room = {"x": 5 * cs, "y": 5 * cs, "w": 20 * cs, "h": 20 * cs}
    furnished = furnish_rooms({"cell_size": cs, "pools": pools, "targets": [room],
                               "seed": 42, "furnish_density": 0.6, "wall_margin": 2})
    assert furnished["pieces"] and furnished["counts"]["rooms"] == 1
    for p in furnished["pieces"]:
        w, h = p["w"] * p["scale"], p["h"] * p["scale"]
        cx, cy = p["x"] + w / 2, p["y"] + h / 2
        half = max(w, h) / 2
        assert room["x"] + 2 * cs - 1 <= cx - 0 and cx <= room["x"] + room["w"] - 2 * cs + 1
        assert room["y"] + 2 * cs - 1 <= cy and cy <= room["y"] + room["h"] - 2 * cs + 1
    assert furnish_rooms({"cell_size": cs, "pools": pools, "targets": []})["warnings"]
    assert furnish_rooms({"cell_size": cs, "pools": {"parts": []},
                          "targets": [room]})["warnings"]

    # tile-by-tile categories come from roles
    tiles = [asset("room_100x100.png", "floors", 100, 100),
             asset("wall_25x50.png", "walls", 25, 50),
             asset("door_40x40.png", "doors", 40, 40),
             asset("computer_10x50.png", "props", 10, 50)]
    cats = categories_from_roles(tiles, classify_roles(tiles))
    assert [len(cats[c]) for c in ("floor", "wall", "door", "wall_fixture")] == [1, 1, 1, 1], cats

    # seeds
    for _ in range(50):
        seed = new_seed()
        assert len(seed) == 12 and seed.isdigit() and seed[0] != "0"
    assert len({new_seed() for _ in range(50)}) == 50
    assert clean_seed("  ") != "" and len(clean_seed("x" * 99)) == 40
    assert coerce_seed("007") == 7 and coerce_seed("a b") == coerce_seed("a b")
    assert coerce_seed("a b") != coerce_seed("a c")
    print("Batch 13 role-driven generation checks passed.")


if __name__ == "__main__":
    main()
