"""Generator fixes: tile art from every pack, floor maps for wings and transitions, misfit and upper-floor tiles
left out, campus grids without stray buildings, breaches and key numbers on the hull, soft light pools."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image

from geomorph import atmosphere, dressing, exporter, layouts, pipeline, render
from geomorph.registry import Registry
from geomorph.tiling import TilePicker, upper_floor

DATA = Path(__file__).resolve().parent / "geomorph" / "data"
REG = Registry.load()


def test_tile_art_is_found_in_any_pack():
    t = REG.tiles["101"]
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, tempfile.TemporaryDirectory() as c:
        path = Path(b) / t.image
        path.parent.mkdir(parents=True)
        Image.new("RGBA", ((t.w + 4) * 300, (t.h + 4) * 300), (40, 200, 200, 255)).save(path)
        only_a = render.TileImages(a, cache_dir=Path(c) / "1")
        assert only_a.thumb(t) is None, "the tile is not in the first pack"
        both = render.TileImages(a, cache_dir=Path(c) / "2", extra_dirs=[b])
        assert both.thumb(t) is not None, "found in the second pack"
        reg = Registry.load()
        reg.tiles_dir, reg.extra_dirs = Path(a), [b]
        assert reg.path(t) == str(path)


def test_every_wing_and_transition_has_a_floor_map():
    floors = json.loads((DATA / "tile_floor.json").read_text(encoding="utf-8"))
    missing = [t.id for t in REG.tiles.values() if t.id not in floors]
    assert not missing, missing[:10]
    walkable = [t.id for t in REG.tiles.values() if t.type == "wing" and any(ch in "c.r" for ch in floors[t.id])]
    assert len(walkable) >= 60, len(walkable)          # most wings have a corridor (fuel-only wings do not)


def test_misfit_tiles_are_retired_but_old_layouts_open():
    misfits = json.loads((DATA / "tile_misfits.json").read_text(encoding="utf-8"))["tiles"]
    assert "bE758:Bridge, Ends" in misfits and len(misfits) >= 10
    assert not set(misfits) & set(REG.tiles), "never generated"
    assert REG.lookup("bE758:Bridge, Ends").id == "bE758:Bridge, Ends"
    res = pipeline.generate(REG, {"kind": "ship", "seed": "misfit-old"})
    pkg = exporter.to_package(res)
    pkg["levels"][0]["tiles"][0]["tile"] = "bE758:Bridge, Ends"   # a layout saved before the tile was retired
    back = exporter.load_layout(pkg, REG)
    assert back.grids[0].placed[0].tile.id == "bE758:Bridge, Ends"


def test_upper_floors_are_never_ordinary_rooms():
    ups = [t for t in REG.tiles.values() if upper_floor(t)]
    assert {"112", "116", "146", "199-2", "199-3", "226"} <= {t.id for t in ups}
    assert not any(upper_floor(REG.tiles[i]) for i in ("111", "115", "145", "199-1", "225"))
    import random
    picker = TilePicker(REG, random.Random(1))
    for tags in (["office", "bridge"], ["engineering", "power"], ["recreation"], ["hangar"]):
        assert not any(upper_floor(t) for _s, t in picker.pool("standard", 20, 20, tags)), tags
    for seed in range(4):
        res = pipeline.generate(REG, {"kind": "site", "archetype": "Research facility", "seed": f"up-{seed}"})
        tall = {s.fixed_tile.id for s in res.layout.slots if s.fixed_tile is not None}
        for g in res.grids:
            for p in g.placed:
                assert not upper_floor(p.tile) or p.tile.id in tall, (seed, p.tile.id)


def test_campus_grid_has_no_stray_buildings():
    for n in range(2, 25):
        ncol, nrow = layouts._campus_grid(n)
        assert ncol * nrow >= n
        last = n - ncol * (nrow - 1)
        assert nrow == 1 or last != 1, (n, ncol, nrow)
        assert nrow <= ncol <= max(3, 2.5 * nrow), (n, ncol, nrow)
    res = pipeline.generate(REG, {"kind": "site", "archetype": "Frontier colony outpost", "seed": "campus-1"})
    slots = [s for s in res.layout.slots if s.level == 0]
    xs = sorted({s.x for s in slots})
    rows = {}
    for s in slots:
        rows.setdefault(s.y, []).append(s.x)
    last = rows[max(rows)]
    if len(last) < len(xs):                             # a part-filled last row is centred under the others
        left, right = min(last) - xs[0], xs[-1] - max(last)
        assert abs(left - right) <= layouts.T + layouts.GAP, (left, right)


def test_breaches_and_key_numbers_sit_on_the_hull():
    for seed in range(6):
        res = pipeline.generate(REG, {"kind": "ship", "seed": f"hull-{seed}", "overlays": ["breach"], "intensity": 1.0})
        placed = {p.zone: p for g in res.grids for p in g.placed}
        for m in res.markers:
            if m["type"] != "breach":
                continue
            p = placed[m["zone"]]
            rows = atmosphere._floor(p)
            if rows is None:
                continue
            cx = min(int((m["x"] - p.x) * 2), len(rows[0]) - 1)
            cy = min(int((m["y"] - p.y) * 2), len(rows) - 1)
            near = [rows[y][x] for y in range(max(0, cy - 1), min(len(rows), cy + 2))
                    for x in range(max(0, cx - 1), min(len(rows[0]), cx + 2))]
            assert any(ch != "o" for ch in near), (seed, m)
        for e in res.key:
            p = next((q for g in res.grids if g.index == e["level"] for q in g.placed
                      if q.x <= e["x"] <= q.x + q.w and q.y <= e["y"] <= q.y + q.h and q.zone == e.get("zone")), None)
            if p is None:
                continue
            rows = atmosphere._floor(p)
            if rows is None:
                continue
            ch = rows[min(int((e["y"] - p.y) * 2), len(rows) - 1)][min(int((e["x"] - p.x) * 2), len(rows[0]) - 1)]
            assert ch != "o", (seed, e["n"], e["title"])


def test_light_pools_fade_and_wings_get_lamps():
    sprite = atmosphere._light_sprite(200)
    centre, half, edge = sprite.getpixel((100, 100)), sprite.getpixel((150, 100)), sprite.getpixel((195, 100))
    # the old light was still at 74 of 255 just inside its reach, then cut to nothing: a hard rim
    assert centre > 230 and half <= 0.55 * centre and edge <= 50, (centre, half, edge)
    res = pipeline.generate(REG, {"kind": "ship", "seed": "lights-1", "overlays": ["power_failure"], "intensity": 1.0})
    wings = [p for g in res.grids for p in g.placed if p.tile.type == "wing"]
    res.overlays["power_failure"] = sorted({p.zone for p in wings})
    lit = [r for g in res.grids for r in atmosphere.affected(res, g) if r["tile"].tile.type == "wing"]
    assert lit, "dark wings are lit by their emergency lamps"
    with_lamps = [r for r in lit if atmosphere.lamp_specs(r, atmosphere._floor(r["tile"]), None, 10)]
    assert with_lamps, "at least one wing gets a lamp"


ALL_STATES = ["lockdown", "power_failure", "quarantine", "breach", "battle"]


def test_a_room_has_one_state_at_most():
    for kind in ("ship", "site"):
        for seed in range(6):
            opts = {"kind": kind, "seed": f"one-{seed}", "overlays": ALL_STATES, "intensity": 1.0}
            if kind == "site" and seed % 2:
                opts["archetype"] = "Derelict ship"         # torn hull and structural damage before the states
            res = pipeline.generate(REG, opts)
            seen = {}
            for k in ("lockdown", "power_failure", "quarantine"):
                for z in res.overlays.get(k, []):
                    assert z not in seen, (kind, seed, z, seen[z], k)
                    seen[z] = k
            for m in res.markers:
                if m["type"] in ("breach", "damage"):
                    assert m["zone"] not in seen, (kind, seed, m["zone"], seen[m["zone"]], m["type"])
                    seen[m["zone"]] = m["type"]
    res = pipeline.generate(REG, {"kind": "ship", "seed": "one-old", "overlays": ALL_STATES, "intensity": 1.0})
    pkg = exporter.to_package(res)
    z = pkg["overlays"]["lockdown"][0]
    pkg["overlays"]["power_failure"].append(z)         # a layout saved before the rule
    back = exporter.load_layout(pkg, REG)
    assert z in back.overlays["power_failure"] and z not in back.overlays["lockdown"]


def test_breach_and_damage_have_symbols_and_legend_rows():
    from geomorph import states
    for st in ("breach", "damage"):
        assert st in states.ORDER and states.LABEL[st] and states.ABOUT[st]
        im = states.symbol(st, 40)
        assert im.getbbox() is not None
    res = pipeline.generate(REG, {"kind": "ship", "seed": "dmg-1", "overlays": ["breach", "battle"], "intensity": 1.0})
    have = {st for g in res.grids for st, _x, _y in states.placements(res, g)}
    assert {"breach", "damage"} <= have, have
    assert {"breach", "damage"} <= set(states.states_in(res))


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)


if __name__ == "__main__":
    main()
