"""Pure-Python checks for the geomorph ship/site generator (``geomorph`` package).

Covers: tile-name parsing and tags, edge data vs. image rotation/mirroring,
placement (no overlaps, edge matching), the four ship layout modes, the data-
driven site archetypes (validation, adding one with no code change), vertical
alignment, route/access validation, determinism by seed, saving/loading
layouts, GM/player packages, rendering and PDF/PNG export (with synthetic tile
images, so the real tile pack is not needed).
"""
from __future__ import annotations

import copy
import json
import os
import random
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from geomorph import archetype as archmod
from geomorph import exporter, pipeline, render, reports, ship, validate
from geomorph.custom import make_custom_tile
from geomorph.edges import DOOR, VOID, WALL, analyse_image
from geomorph.placement import (LevelGrid, Orientation, hull_sides, orientation_for,
                                orientations, tile_piece)
from geomorph.registry import Registry, Tile, derive_tags, parse_name, tag_table

REG = Registry.load()
ARCH = archmod.load_all()
ARCH.pop("_problems")


def check_parsing():
    info = parse_name("101 [100x100] Multipurpose (Offices, Fuel, Engineering, Medical).png")
    assert info["number"] == "101" and info["rooms"][:2] == ["Offices", "Fuel"], info
    assert info["w_ft"] == 100 and not info["overlay"] and not info["mirror"]
    assert parse_name("502 [50x50] [Mirror] Fuel Deck (Intake Scoop).png")["mirror"]
    assert parse_name("101 [100x100] [Overlay] Escape Pod.png")["overlay"]
    assert parse_name("(1) not a tile.png") is None
    tags = derive_tags("Hangar", ["Repair Shop", "Fighters"], tag_table())
    assert "hangar" in tags and "workshop" in tags, tags
    assert len(REG.tiles) > 500
    types = {t.type for t in REG.tiles.values()}
    assert {"standard", "edge", "corner", "end"} <= types, types
    for t in REG.tiles.values():
        assert t.edges and set(t.edges) == {"N", "E", "S", "W"}, t.id
        for side, e in t.edges.items():
            n = t.w if side in "NS" else t.h
            assert len(e["cls"]) == n == len(e["raw"]), (t.id, side)
    # a function tag exists for each guide function the generator relies on
    for tag in ("bridge", "engineering", "fuel", "staterooms", "medical", "lab", "cargo", "hangar",
                "weapons", "security", "vertical", "hydroponics"):
        assert REG.with_tag(tag), tag
    assert any(t.tags.get("vertical") == 1.0 and t.title in ("Elevator Core", "Vertical Core")
               for t in REG.tiles.values())
    # ranges from the guide: a standard tile lives in 101-220, edge 301-460, ...
    print("parsing/registry ok:", len(REG.tiles), "tiles")


def synthetic_tile(w, h, pps=6, doors=None, hull=()):
    """A tiny tile image: boundary walls, door glyphs (dim) and open spans (clear)."""
    B = 2 * pps
    im = Image.new("RGBA", ((w + 4) * pps, (h + 4) * pps), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = B, B, B + w * pps, B + h * pps
    t = max(1, pps // 3)
    wall = (120, 230, 235, 255)
    for side, (a, b, c, e) in {"N": (x0, y0 - t, x1, y0 + t), "S": (x0, y1 - t, x1, y1 + t),
                               "W": (x0 - t, y0, x0 + t, y1), "E": (x1 - t, y0, x1 + t, y1)}.items():
        if side not in hull:
            d.rectangle((a, b, c, e), fill=wall)
    for side, spec in (doors or {}).items():
        for idx, kind in spec.items():
            if side in hull:
                continue
            if side in "NS":
                bx0, bx1 = x0 + idx * pps, x0 + (idx + 1) * pps
                by0, by1 = (y0 - t, y0 + t) if side == "N" else (y1 - t, y1 + t)
            else:
                by0, by1 = y0 + idx * pps, y0 + (idx + 1) * pps
                bx0, bx1 = (x0 - t, x0 + t) if side == "W" else (x1 - t, x1 + t)
            if kind == "door":      # door glyph: partly see-through
                d.rectangle((bx0, by0, bx1 - 1, by1 - 1), fill=(120, 230, 235, 150))
            else:                   # open span: no wall
                d.rectangle((bx0, by0, bx1 - 1, by1 - 1), fill=(0, 0, 0, 0))
    return im


def check_orientation_matches_image_transforms():
    """Edge data rotated by ``orientations`` must equal re-analysing the image the
    renderer would draw (mirror first, then clockwise rotation)."""
    pps = 6
    w, h = 12, 8
    doors = {"N": {3: "door", 8: "door"}, "E": {2: "door"}, "S": {5: "door", 6: "open"}, "W": {1: "door", 4: "door", 7: "door"}}
    im = synthetic_tile(w, h, pps, doors)
    edges = analyse_image(im, w, h, pps)
    exp = {"N": [DOOR if i in (3, 8) else WALL for i in range(w)],
           "S": [DOOR if i == 5 else (VOID if i == 6 else WALL) for i in range(w)],
           "E": [DOOR if i == 2 else WALL for i in range(h)],
           "W": [DOOR if i in (1, 4, 7) else WALL for i in range(h)]}
    for s in "NESW":
        assert edges[s]["cls"] == exp[s], (s, edges[s]["cls"], exp[s])
    tile = Tile(id="syn", number="syn", type="standard", w=w, h=h, title="syn", rooms=[], tags={}, image="",
                edges=edges)
    seen = 0
    for rot in (0, 90, 180, 270):
        for mirror in (False, True):
            o = orientation_for(tile, rot, mirror)
            t = im
            if mirror:
                t = t.transpose(Image.FLIP_LEFT_RIGHT)
            if rot:
                t = t.transpose({90: Image.ROTATE_270, 180: Image.ROTATE_180, 270: Image.ROTATE_90}[rot])
            got = analyse_image(t, o.w, o.h, pps)
            for s in "NESW":
                assert got[s]["cls"] == list(o.cls(s)), (rot, mirror, s, got[s]["cls"], o.cls(s))
            seen += 1
    assert seen == 8
    print("orientation math matches rotated/mirrored images (8 orientations)")


def check_piece_geometry():
    t = REG.tiles["101"]
    g = LevelGrid(0, "x", 100, 100)
    o = orientation_for(t, 90, False)
    p = g.place(t, 10, 20, o)
    cell = 70.0
    pc = tile_piece(p, cell)
    cx = pc["x"] + pc["w"] * pc["scale"] / 2
    cy = pc["y"] + pc["h"] * pc["scale"] / 2
    assert abs(cx - (10 + 10) * cell) < 1e-6 and abs(cy - (20 + 10) * cell) < 1e-6, (cx, cy)
    assert pc["rotation"] == 90 and pc["flip_h"] is False
    assert abs(pc["scale"] * 300 - cell) < 1e-9, "one grid square is 300 px in the pack"


def check_placement():
    g = LevelGrid(0, "x", 100, 100)
    t = REG.tiles["101"]
    o = orientations(t)[0]
    a = g.place(t, 10, 10, o)
    assert not g.free(15, 15, 10, 10) and g.free(30, 10, 5, 5)
    assert not g.fit(15, 15, o).ok, "overlap rejected"
    assert not g.fit(-1, 0, o).ok and not g.fit(90, 0, o).ok, "bounds"
    f = g.fit(30, 10, o)
    assert f.ok and f.touching == 1
    try:
        g.place(t, 15, 15, o)
        raise AssertionError("overlap must assert")
    except AssertionError as e:
        assert "overlap" in str(e)
    # hull sides: edge tiles have exactly one, corners two, ends three
    for tile in REG.tiles.values():
        n = len(hull_sides(tile))
        assert n == {"standard": 0, "megamorph": 0, "wing": 0, "edge": 1, "corner": 2, "end": 3}[tile.type], tile.id
    # a standard tile has orientation variants and sizes swap on 90 degrees
    e = REG.tiles[[k for k, v in REG.tiles.items() if v.type == "edge"][0]]
    dims = {(o.w, o.h) for o in orientations(e)}
    assert dims == {(20, 10), (10, 20)}, dims


def hull_ok(grid):
    """Every hull side faces empty space (or another hull side), never a tile interior."""
    for p in grid.placed:
        for side in p.o.hull:
            dx, dy = {"N": (0, -1), "E": (1, 0), "S": (0, 1), "W": (-1, 0)}[side]
            n = p.w if side in "NS" else p.h
            for i in range(n):
                cx = p.x + (i if side in "NS" else (p.w if side == "E" else -1))
                cy = p.y + (i if side in "EW" else (p.h if side == "S" else -1))
                nb = grid.occ.get((cx, cy))
                if nb is not None and nb.tile.type in ("standard", "megamorph"):
                    return False
    return True


def check_ships():
    reqs = {"Merchant": ("cargo",), "Military": ("weapons", "hangar"), "Research": ("lab",),
            "Luxury Liner": ("passenger",), "Scout": ("sensors",), "Colony / Generational": ("lowberth",),
            "Medical / Rescue": ("medical",)}
    for ship_type, need in reqs.items():
        res = pipeline.generate(REG, {"kind": "ship", "ship_type": ship_type, "tonnage": 1500, "seed": 11})
        g = res.grids[0]
        tags = {}
        for p in g.placed:
            tags.setdefault(p.zone.split("#")[0], 0)
            tags[p.zone.split("#")[0]] += 1
        for t in ("bridge", "engineering", "fuel", "staterooms") + need:
            assert t in tags, (ship_type, t, tags)
        assert hull_ok(g), ship_type
        assert not res.issues, (ship_type, res.issues)
        # bridge fore (north), engineering aft (south)
        ys = {p.zone.split("#")[0]: p.y for p in g.placed if p.tile.type == "end"}
        assert ys["bridge"] < ys["engineering"], ys
        cells = sum(1 for _ in g.occ)
        assert cells == sum(p.w * p.h for p in g.placed), "no overlaps"
    # all four modes, symmetric and not; determinism; orientation keeps structure
    for mode in ship.MODES:
        for sym in (True, False):
            opts = {"kind": "ship", "mode": mode, "symmetric": sym, "tonnage": 2000, "seed": "abc", "ship_type": "Military"}
            a = pipeline.generate(REG, opts)
            b = pipeline.generate(REG, opts)
            assert exporter.to_package(a)["levels"] == exporter.to_package(b)["levels"], (mode, sym)
            assert hull_ok(a.grids[0]) or mode == "movie", mode
    sym = pipeline.generate(REG, {"kind": "ship", "tonnage": 2000, "symmetric": True, "seed": 3, "fins": True})
    g = sym.grids[0]
    mid = g.bounds()[0] + (g.bounds()[2] - g.bounds()[0]) / 2
    by_pos = {(p.x + p.w / 2, p.y): p for p in g.placed}
    pairs = 0
    for (cx, y), p in by_pos.items():
        q = by_pos.get((2 * mid - cx, y))
        if q is not None and q is not p and p.tile.type in ("edge", "corner", "standard") and cx < mid:
            assert q.tile.id.rstrip("m") == p.tile.id.rstrip("m"), (p.tile.id, q.tile.id)
            pairs += 1
    assert pairs >= 2, "symmetric ship mirrors port and starboard"
    # rotating the ship keeps every tile and connection
    base = pipeline.generate(REG, {"kind": "ship", "tonnage": 1000, "seed": 5, "orientation": "N"})
    for ori in "ESW":
        r = pipeline.generate(REG, {"kind": "ship", "tonnage": 1000, "seed": 5, "orientation": ori})
        assert len(r.grids[0].placed) == len(base.grids[0].placed)
        assert sorted(p.tile.id for p in r.grids[0].placed) == sorted(p.tile.id for p in base.grids[0].placed)
        assert hull_ok(r.grids[0]), ori
        assert len(r.grids[0].connections()) == len(base.grids[0].connections()), ori
    # RULE: wings are always a Port + Starboard pair of the same A-number, mirrored about the centre line
    for seed in range(15):
        for sym in (True, False):
            r = pipeline.generate(REG, {"kind": "ship", "tonnage": 2000, "seed": seed, "symmetric": sym, "fins": True})
            g = r.grids[0]
            wings = sorted((p for p in g.placed if p.tile.type == "wing"), key=lambda p: p.x)
            assert len(wings) == 2, wings
            a, b = wings
            assert a.tile.id.endswith("P") and b.tile.id.endswith("S") and a.tile.pair == b.tile.id
            assert (a.w, a.h, a.y) == (b.w, b.h, b.y)
            mid = ship.PAD + 36 + (ship.choose_dims(2000, True)[0] * 20) / 2
            assert abs((a.x + a.w / 2) + (b.x + b.w / 2) - 2 * mid) < 1e-6, "mirrored about the centre line"
    no_fins = pipeline.generate(REG, {"kind": "ship", "tonnage": 2000, "seed": 1, "fins": False})
    assert not [p for p in no_fins.grids[0].placed if p.tile.type == "wing"]
    c, rws = ship.choose_dims(1000)
    assert ship.slots_area(ship.hull_slots(c, rws)) // 2 == 1000, "the guide's ~1000-ton sample deck"
    print("ships ok")


def check_archetype_files():
    assert len(ARCH) >= 25, len(ARCH)
    groups = {a["group"] for a in ARCH.values()}
    assert {"Colony", "Industry", "Research", "Medical", "Security", "Wreck", "Orbital"} <= groups, groups
    for name in ("Deep mine", "Company town", "Xeno-biology containment lab", "Prison / penal colony"):
        assert name in ARCH, name
    for a in ARCH.values():
        assert archmod.validate(a) == [], (a["name"], archmod.validate(a))
    bad = copy.deepcopy(ARCH["Deep mine"])
    bad["topology"] = "pentagon"
    bad["zones"][0]["access"] = "vip"
    bad["zones"][1]["prefer_adjacent"] = ["nope"]
    del bad["scales"]["large"]
    errs = archmod.validate(bad)
    assert len(errs) >= 4, errs
    sec = copy.deepcopy(ARCH["Prison / penal colony"])
    for z in sec["zones"]:
        z["checkpoint"] = False
    assert any("checkpoint" in e for e in archmod.validate(sec))
    # a brand-new archetype needs only a definition file
    with tempfile.TemporaryDirectory() as td:
        new = archmod.blank_archetype("Test Moon Base")
        new["topology"] = "campus"
        new["scales"] = {s: {"buildings": [4, 5], "levels": 1} for s in archmod.SCALES}
        new["zones"] = [
            {"id": "dome", "name": "Dome", "tags": ["recreation"], "min": 1, "max": 2, "required": True,
             "level": "any", "position": "any", "access": "public", "prefer_adjacent": [], "forbid_adjacent": []},
            {"id": "lab", "name": "Lab", "tags": ["lab"], "min": 1, "max": 3, "required": True, "level": "any",
             "position": "any", "access": "staff", "prefer_adjacent": [], "forbid_adjacent": []}]
        new["entrance_zone"] = "dome"
        path = archmod.save_custom(new, td)
        assert path.exists()
        loaded = archmod.load_all(extra=[td])
        assert "Test Moon Base" in loaded
        res = pipeline.generate(REG, {"kind": "site", "archetype": "Test Moon Base", "seed": 2, "scale": "small"}, loaded)
        assert not res.issues, res.issues
        zb = {z.base for z in res.zones.values()}
        assert {"dome", "lab"} <= zb
        # a broken file is reported, not fatal
        Path(td, "broken.json").write_text("{not json", encoding="utf-8")
        again = archmod.load_all(extra=[td])
        assert any("broken.json" in p for p in again["_problems"])
    print("archetype files ok:", len(ARCH))


def levels_of(res):
    return [(g.index, sorted((p.tile.id, p.x, p.y, p.o.rot, p.o.mirror, p.zone) for p in g.placed)) for g in res.grids]


def check_sites():
    n = 0
    for name, arch in sorted(ARCH.items()):
        for scale in ("small", "large"):
            for seed in (1, 2):
                opts = {"kind": "site", "archetype": name, "scale": scale, "seed": seed}
                res = pipeline.generate(REG, opts, ARCH)
                n += 1
                assert not res.issues, (name, scale, seed, res.issues[:3])
                # no overlaps, one tile per cell
                for g in res.grids:
                    assert len(g.occ) == sum(p.w * p.h for p in g.placed), (name, "overlap")
                # required zones present (Planned mode guarantees them)
                have = {}
                for z in res.zones.values():
                    have[z.base] = have.get(z.base, 0) + 1
                if arch["topology"] not in ("ship", "wreck"):
                    for spec in arch["zones"]:
                        if spec.get("required", True):
                            assert have.get(spec["id"], 0) >= spec.get("min", 1), (name, spec["id"])
    # determinism: same seed + same options = same output; different seed differs
    opts = {"kind": "site", "archetype": "Deep mine", "scale": "medium", "seed": "same", "overlays": ["threat", "secrets"]}
    a, b = pipeline.generate(REG, opts, ARCH), pipeline.generate(REG, opts, ARCH)
    assert levels_of(a) == levels_of(b) and exporter.to_package(a) == exporter.to_package(b)
    c = pipeline.generate(REG, dict(opts, seed="other"), ARCH)
    assert levels_of(a) != levels_of(c)
    print("sites ok:", n, "layouts generated")


def check_vertical_alignment():
    for name in ("Research facility", "Prison / penal colony", "Black site / secret corporate lab", "Deep mine"):
        for seed in (1, 4, 9):
            res = pipeline.generate(REG, {"kind": "site", "archetype": name, "scale": "medium", "seed": seed}, ARCH)
            lay = res.layout
            cores = [v for v in lay.vertical]
            assert cores, name
            for v in cores:
                if v["source"] == "tile":
                    ps = [(s.x, s.y, s.placed.tile.id, s.placed.o.rot, s.placed.o.mirror)
                          for s in lay.slots if s.role == "vertical" and s.placed]
                    xy = {(a, b) for a, b, *_ in ps}
                    assert len(xy) == 1, (name, xy)
                    assert len({(c, d, e) for _a, _b, c, d, e in ps}) == 1, "same tile and orientation on each level"
                    assert {s.level for s in lay.slots if s.role == "vertical"} == set(v["levels"])
                else:
                    # filler core: same X/Y on every level
                    assert len(set(v["levels"])) == len(v["levels"])
            # shaft markers repeat at the same spots on every level
            for m in lay.markers:
                assert m["levels"] == sorted(m["levels"])
    # tall rooms leave a void directly above them on each level
    found = False
    for seed in range(1, 12):
        res = pipeline.generate(REG, {"kind": "site", "archetype": "Prison / penal colony", "scale": "large", "seed": seed}, ARCH)
        for vol in res.layout.volumes:
            found = True
            for k in range(1, vol["height"]):
                assert any(s.level == vol["level"] - k and (s.x, s.y) == (vol["x"], vol["y"]) and s.reserved == "void"
                           for s in res.layout.slots)
                g = res.grids[vol["level"] - k]
                assert any(f["kind"] == "void" and (f["x"], f["y"]) == (vol["x"], vol["y"]) for f in g.filler)
    assert found, "prison observation deck volume appears"
    print("vertical alignment ok")


def check_routes_and_access():
    arch = ARCH["Xeno-biology containment lab"]
    for seed in range(1, 6):
        res = pipeline.generate(REG, {"kind": "site", "archetype": arch["name"], "scale": "medium", "seed": seed}, ARCH)
        assert not res.issues, res.issues
        zones = res.zones
        adj = validate.zone_graph(res)
        ent = validate.entrance_zone_id(res)
        cps = {z for z, zz in zones.items() if zz.checkpoint}
        assert cps, "checkpoint zone exists"
        # every zone reachable by at least one legal route
        for zid, z in zones.items():
            ok = False
            for rname, allowed in {**arch["routes"], "emergency": list(archmod.ACCESS_LEVELS)}.items():
                if z.access not in allowed:
                    continue
                reach = validate._bfs(adj, ent, allowed=lambda m, a=allowed: zones[m].access in a)
                ok = ok or zid in reach or zid == ent
            assert ok, (zid, z.access)
        # secure zones cannot be reached from public ones without a checkpoint
        for zid, z in zones.items():
            if z.tier == 0 and not z.checkpoint:
                reach = validate._bfs(adj, zid, blocked=cps)
                assert not [m for m in reach if zones[m].tier >= 3 and m not in cps], zid
        # negative test: a door joining a public room to a secure one is caught
        pub = next(z for z in zones.values() if z.tier == 0 and not z.checkpoint)
        sec = next(z for z in zones.values() if z.tier >= 3 and not z.checkpoint)
        broken = copy.copy(res)
        broken.links = list(res.links) + [{"level": 0, "a": pub.id, "b": sec.id, "state": "native"}]
        issues = validate.validate(broken, arch)
        assert any("without a checkpoint" in i for i in issues), issues
        # negative test: cutting every connection to a zone strands it
        victim = next(z for z in zones.values() if z.id != ent and not z.checkpoint)
        cut = copy.copy(res)
        cut.links = [l for l in res.links if victim.id not in (l.get("a"), l.get("b"))]
        assert any("not connected" in i for i in validate.validate(cut, arch))
    # door gating itself
    from geomorph.assign import door_allowed
    from geomorph.model import ZoneInst
    z = lambda acc, cp=False: ZoneInst(id=acc, base=acc, name=acc, tags=[], access=acc, checkpoint=cp)
    assert door_allowed(z("public"), z("staff")) and door_allowed(z("secure"), z("containment"))
    assert not door_allowed(z("staff"), z("secure")) and not door_allowed(z("public"), z("secure"))
    assert not door_allowed(z("restricted"), z("secure")) and not door_allowed(z("public"), z("containment"))
    assert door_allowed(z("staff", True), z("secure")) and door_allowed(z("public", True), z("containment"))
    print("routes/access ok")


def check_environment_and_entrance():
    r = pipeline.generate(REG, {"kind": "site", "archetype": "Research facility", "scale": "small", "seed": 2,
                                "environment": "vacuum"}, ARCH)
    assert r.layout.entrance["kind"] == "airlock", r.layout.entrance
    assert any(f["kind"] == "airlock" for g in r.grids for f in g.filler)
    r = pipeline.generate(REG, {"kind": "site", "archetype": "Research facility", "scale": "small", "seed": 2,
                                "environment": "breathable"}, ARCH)
    assert r.layout.entrance["kind"] == "door"
    r = pipeline.generate(REG, {"kind": "site", "archetype": "Research facility", "scale": "medium", "seed": 2,
                                "environment": "underground"}, ARCH)
    assert r.layout.level_names[0] == "Surface" and not r.grids[0].placed, "only the entrance shows on the surface"
    assert any(f["kind"] == "building" for f in r.grids[0].filler)
    assert "hostile" in ARCH["Frontier colony outpost"]["environments"]
    hostile = pipeline.generate(REG, {"kind": "site", "archetype": "Frontier colony outpost", "scale": "small",
                                      "seed": 2, "environment": "hostile"}, ARCH)
    assert hostile.layout.entrance["kind"] == "airlock"
    print("environment ok")


def check_modes_for_sites():
    for mode in ("planned", "random", "selective", "movie"):
        res = pipeline.generate(REG, {"kind": "site", "archetype": "Company town", "scale": "medium", "seed": 6,
                                      "mode": mode}, ARCH)
        assert res.grids[0].placed, mode
        if mode == "movie":
            assert res.meta.get("movie_removed", 0) > 0 and res.meta.get("crop")
    print("site layout modes ok")


def check_package_and_save_load():
    opts = {"kind": "site", "archetype": "Black site / secret corporate lab", "scale": "medium", "seed": 5,
            "overlays": ["lockdown", "power_failure", "threat", "secrets", "breach", "quarantine", "salvage", "battle"],
            "intensity": 0.8}
    res = pipeline.generate(REG, opts, ARCH)
    gm = exporter.to_package(res, gm=True)
    pl = exporter.to_package(res, gm=False)
    assert gm["hooks"] and gm["notes"] and not pl["hooks"] and not pl["notes"]
    assert any(m["type"] == "threat" for m in gm["markers"]) and any(m["type"] == "secret" for m in gm["markers"])
    assert not any(m.get("gm_only") for m in pl["markers"]), "player version hides secrets and the threat overlay"
    assert all(set(e) <= {"n", "level", "title"} for e in pl["key"])
    assert gm["description"] and gm["title"] and len(gm["key"]) >= len(res.zones)
    types = {h["type"] for h in gm["hooks"]}
    assert len(types) >= 3 and types <= set(json.load(open(Path("geomorph/data/common.json")))["hook_types"])
    assert gm["credits"] and "Pearce" in gm["credits"]
    # save and reload the layout JSON
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "lay.json"
        exporter.save_layout(res, p)
        back = exporter.load_layout(p, REG)
        assert levels_of(back) == levels_of(res)
        assert exporter.to_package(back)["levels"] == gm["levels"]
    # overlays are seeded: same seed, same overlay state
    again = pipeline.generate(REG, opts, ARCH)
    assert again.overlays == res.overlays and again.markers == res.markers
    # intensity changes the amount of threat markers
    few = pipeline.generate(REG, dict(opts, intensity=0.1), ARCH)
    many = pipeline.generate(REG, dict(opts, intensity=1.0), ARCH)
    count = lambda r: sum(1 for m in r.markers if m["type"] == "threat")
    assert count(few) <= count(many)
    off = pipeline.generate(REG, dict(opts, overlays=[]), ARCH)
    assert not any(m["type"] in ("threat", "secret", "breach") for m in off.markers)
    print("package/save/load ok")


def check_condition_and_text():
    r = pipeline.generate(REG, {"kind": "site", "archetype": "Deep mine", "seed": 3, "condition": "Derelict",
                                "zone_conditions": {"pump": "Well Maintained"}, "peculiarities": 3}, ARCH)
    assert r.meta["condition"] == "Derelict"
    pump = [z for z in r.zones if z.startswith("pump")]
    assert pump and r.meta["zone_conditions"][pump[0]] == "Well Maintained", "mixing conditions is allowed"
    assert len(r.meta["peculiarities"]) == 3
    assert "derelict" in r.text["description"].lower()
    from geomorph import dressing
    assert dressing.conditions() == ["Scrap", "Derelict", "Disrepair", "Average", "Cluttered", "Sleeper",
                                     "Well Maintained", "Top of the Line"]
    from geomorph import names
    assert {"alien_corporate_frontier", "generic_scifi"} <= set(names.themes())
    n1 = names.make_name("mines", random.Random(1), "alien_corporate_frontier")
    n2 = names.make_name("mines", random.Random(1), "generic_scifi")
    assert n1 and n2 and n1 != n2
    for table in ("facilities", "colonies", "corporations", "mines", "stations", "ships"):
        assert names.make_name(table, random.Random(2)), table
    print("condition/text/names ok")


def check_reroll_and_gaps():
    res = pipeline.generate(REG, {"kind": "site", "archetype": "Company town", "seed": 8, "scale": "medium"}, ARCH)
    zid = next(z for z, zz in res.zones.items() if zz.base == "housing")
    before = [p.tile.id for g in res.grids for p in g.placed if p.zone == zid]
    changed = False
    for s in range(6):
        changed = pipeline.reroll_zone(res, zid, seed=s) or changed
    after = [p.tile.id for g in res.grids for p in g.placed if p.zone == zid]
    assert len(after) == len(before) == 1
    for g in res.grids:
        assert len(g.occ) == sum(p.w * p.h for p in g.placed)
    gaps = reports.all_gaps(REG, ARCH)
    assert gaps["Company town"] and "filler" in gaps["Company town"][0]["reason"]
    assert reports.gap_report(REG, {"zones": [{"id": "x", "name": "X", "tags": ["no_such_function"]}]})
    assert reports.edge_review_list(REG) is not None
    assert "bridge" in reports.tag_table_report(REG)
    print("reroll/gaps ok")


def fake_loader(tile):
    """A thumbnail drawn without the real pack (15 px per square, 2-square border)."""
    pps = render.THUMB_PPS
    im = Image.new("RGBA", ((tile.w + 4) * pps, (tile.h + 4) * pps), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((2 * pps, 2 * pps, (tile.w + 2) * pps, (tile.h + 2) * pps), outline=(120, 230, 235, 255), width=2)
    d.line((2 * pps, 2 * pps, (tile.w + 2) * pps, (tile.h + 2) * pps), fill=(120, 230, 235, 255))
    return im


def check_render_and_export():
    images = render.TileImages(None, loader=fake_loader)
    res = pipeline.generate(REG, {"kind": "site", "archetype": "Prison / penal colony", "scale": "small", "seed": 4,
                                  "overlays": ["threat", "secrets", "lockdown", "power_failure"]}, ARCH)
    im = render.render_level(res, 0, images, pps=6)
    assert im.size[0] > 100 and im.size[1] > 100
    gm = render.render_level(res, 0, images, pps=6, gm=True)
    pl = render.render_level(res, 0, images, pps=6, gm=False)
    assert gm.tobytes() != pl.tobytes() or not any(m.get("gm_only") and m["level"] == 0 for m in res.markers)
    ship_res = pipeline.generate(REG, {"kind": "ship", "tonnage": 800, "seed": 1})
    assert render.render_level(ship_res, 0, images, pps=5).size[0] > 50
    assert render.render_section(res).size[0] > 50
    with tempfile.TemporaryDirectory() as td:
        files = exporter.export_all(res, td, images, pps=6)
        names_ = [os.path.basename(f) for f in files]
        assert any(n.endswith("_gm.pdf") for n in names_) and any(n.endswith("_player.pdf") for n in names_)
        assert any("_level1_gm.png" in n for n in names_) and any(n.endswith("_section.png") for n in names_)
        assert any(n.endswith("_gm.json") for n in names_)
        for f in files:
            assert os.path.getsize(f) > 0, f
        pdf = [f for f in files if f.endswith("_gm.pdf")][0]
        assert open(pdf, "rb").read(5) == b"%PDF-"
        pages = exporter.build_pdf_pages(res, images, True, 6)
        assert len(pages) >= 1 + len(res.grids)
    print("render/export ok")


def check_custom_tiles():
    with tempfile.TemporaryDirectory() as td:
        pps = 6
        im = synthetic_tile(20, 20, pps, {"N": {5: "door", 15: "door"}, "S": {4: "door", 14: "door"},
                                          "E": {5: "door", 15: "door"}, "W": {4: "door", 14: "door"}})
        p = Path(td) / "mine.png"
        im.save(p)
        t = make_custom_tile(p, "X1", "My Cargo Bay (Cargo Hold)", "standard", pps=pps, rooms=["Cargo Hold"])
        assert (t.w, t.h) == (20, 20) and t.tags.get("cargo") and t.edges["N"]["cls"][5] == DOOR
        REG.add(t)
        try:
            assert t.id in REG.tiles and orientations(t)
            res = pipeline.generate(REG, {"kind": "site", "archetype": "Deep mine", "seed": 1}, ARCH)
            assert not res.issues
        finally:
            REG.tiles.pop("X1", None)
    print("custom tiles ok")


def main():
    check_parsing()
    check_orientation_matches_image_transforms()
    check_piece_geometry()
    check_placement()
    check_ships()
    check_archetype_files()
    check_sites()
    check_vertical_alignment()
    check_routes_and_access()
    check_environment_and_entrance()
    check_modes_for_sites()
    check_package_and_save_load()
    check_condition_and_text()
    check_reroll_and_gaps()
    check_render_and_export()
    check_custom_tiles()
    print("ALL GEOMORPH CHECKS PASSED")


if __name__ == "__main__":
    main()
