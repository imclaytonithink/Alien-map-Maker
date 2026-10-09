"""Top-level pipeline: options in, a finished :class:`Result` (and package) out.

Program -> Zone graph -> Topology -> Placement -> Scoring/repair -> Dressing ->
Text -> Export. Deterministic: same seed + same options = same output.
"""
from __future__ import annotations

import copy
import json
import random

from . import archetype as archmod
from . import connect, dressing, names, ship, site, validate
from . import filler as F
from .model import Link, Result, ZoneInst
from .registry import Registry
from .seeds_compat import coerce_seed

DEFAULTS = {
    "kind": "site", "seed": 1, "theme": names.DEFAULT_THEME, "name": "",
    "archetype": "Research facility", "scale": "medium", "environment": None, "mode": "planned",
    "ship_type": "Merchant", "tonnage": 1000, "symmetric": True, "fins": True, "orientation": "N",
    "condition": None, "mixed_conditions": False, "zone_conditions": {}, "peculiarities": 2,
    "overlays": [], "intensity": 0.5, "parts": {}, "craft": None,
}


def _ship_result(registry, o, rng) -> Result:
    grid, info = ship.generate_ship(registry, rng, int(o["tonnage"]), o["ship_type"], o["mode"],
                                    bool(o["symmetric"]), bool(o["fins"]), o["orientation"], parts=o.get("parts"))
    zones = {}
    counts = {}
    for p in grid.placed:
        tag = p.zone or (max(p.tile.tags, key=p.tile.tags.get) if p.tile.tags else "room")
        counts[tag] = counts.get(tag, 0) + 1
        zid = f"{tag}#{counts[tag]}"
        z = ZoneInst(id=zid, base=tag, name=p.tile.title or tag.title(), tags=sorted(p.tile.tags, key=lambda t: -p.tile.tags[t])[:4],
                     access="staff")
        zones[zid] = z
        p.zone = zid
    res = Result(kind="ship", meta={"ship_type": o["ship_type"], "mode": o["mode"], "tonnage": info["tonnage"],
                                    "environment": "vacuum", "hull": f"{info['C']}x{info['R']}", **{k: info[k] for k in ("bow", "symmetric")}},
                 grids=[grid], zones=zones, layout=None, issues=list(info.get("issues", [])), options=o, registry=registry)
    links, pieces = connect.realize_contacts(grid, zones)
    grid.filler.extend(pieces)
    comps = connect.level_components(grid, links)
    # a corner piece pressed only against other hull pieces needs no door
    comps = [c for c in comps if not (len(c) == 1 and c[0].tile.type in ("corner", "wing"))]
    # patched doors keep it connected; report anything still split
    if len(comps) > 1 and not o.get("_damaged"):
        res.issues.append(f"ship is in {len(comps)} separate parts after repair")
    res.links = [{"level": 0, "a": a.zone, "b": b.zone, "state": st, "a_tile": a.tile.id, "b_tile": b.tile.id}
                 for a, b, st in links]
    res.meta["tonnage_target"] = int(o["tonnage"])
    if info.get("crop"):
        res.meta["crop"] = info["crop"]
    return res


def _apply_damage(res: Result, rng, damage: dict):
    """Wreck / derelict: remove some hull tiles and scatter breaches and rubble."""
    mask = float(damage.get("mask", 0.15))
    breach = float(damage.get("breach", 0.05))
    g = res.grids[0]
    cand = [p for p in list(g.placed) if p.tile.type in ("edge", "corner")]
    removed = 0
    for p in cand:
        if rng.random() < mask:
            g.remove(p)
            g.filler.append(F.piece("rubble", g.index, p.x, p.y, p.w, p.h))
            res.markers.append({"type": "breach", "level": 0, "x": p.x + p.w / 2, "y": p.y + p.h / 2,
                                "label": "Torn hull", "zone": p.zone})
            removed += 1
    for p in list(g.placed):
        if rng.random() < breach:
            res.markers.append({"type": "damage", "level": 0, "x": p.x + p.w / 2, "y": p.y + p.h / 2,
                                "label": "Structural damage", "zone": p.zone})
    if damage.get("tilt"):
        res.meta["tilt"] = rng.choice((-12, -8, 8, 12))
    res.meta["damage_removed"] = removed


def generate(registry: Registry, options=None, archetypes=None) -> Result:
    o = copy.deepcopy(DEFAULTS)
    o.update(options or {})
    rng = random.Random(coerce_seed(o["seed"]))
    theme = o["theme"]
    A = archetypes or archmod.load_all()
    arch = None
    if o["kind"] == "ship":
        res = _ship_result(registry, o, rng)
        table = "ships"
        cond_default = o["condition"] or "Average"
    else:
        arch = A.get(o["archetype"])
        if arch is None:
            raise KeyError(f"unknown archetype '{o['archetype']}'")
        env = o["environment"] or (arch.get("environments") or ["breathable"])[0]
        table = arch.get("name_table", "facilities")
        cond_default = o["condition"] or arch.get("condition_default", "Average")
        if arch["topology"] in ("ship", "wreck"):
            # derelicts and wrecks are ships with a damage mask on top
            so = dict(o, _damaged=True, tonnage=arch["scales"][o["scale"]]["tonnage"],
                      ship_type=o.get("ship_type") if o.get("ship_type") in ship.SHIP_TYPES else "Merchant")
            res = _ship_result(registry, so, rng)
            res.meta.update({"environment": env, "scale": o["scale"], "topology": arch["topology"]})
            _apply_damage(res, rng, arch.get("damage", {}))
        else:
            # scoring and repair: re-roll the layout if validation finds a real fault
            best = None
            for _attempt in range(5):
                cand = site.generate_site(registry, arch, rng, o["scale"], env, o["condition"], o["mode"], o)
                faults = validate.validate(cand, arch)
                if best is None or len(faults) < best[0]:
                    best = (len(faults), cand)
                if not faults:
                    break
            res = best[1]
            res.meta["attempts"] = _attempt + 1
    return finish(res, rng, o, arch, table, cond_default, theme)


def finish(res: Result, rng, o, arch, table, cond_default, theme) -> Result:
    res.meta["seed"] = o["seed"]
    res.meta["theme"] = theme
    res.meta["archetype"] = (arch or {}).get("name", o.get("ship_type", ""))
    res.meta["name"] = o.get("name") or names.make_name(table, rng, theme)
    res.meta["scale"] = res.meta.get("scale") or o.get("scale")
    dressing.apply_condition(res, rng, cond_default, o.get("zone_conditions"), o.get("mixed_conditions"))
    dressing.apply_peculiarities(res, rng, o.get("peculiarities", 2))
    dressing.apply_overlays(res, rng, o.get("overlays"), o.get("intensity", 0.5), arch)
    dressing.build_key(res, rng, arch)
    dressing.build_section(res)
    dressing.build_text(res, rng, arch, theme, res.meta["name"])
    res.issues = validate.validate(res, arch)
    return res


def reroll_zone(res: Result, zone_id: str, seed=None):
    """Re-pick the tile for one zone (keeps everything else). Returns True if changed."""
    from .placement import orientations
    from .tiling import TilePicker
    rng = random.Random(coerce_seed(seed if seed is not None else random.random()))
    reg = res.registry
    target = None
    grid = None
    for g in res.grids:
        for p in g.placed:
            if p.zone == zone_id:
                target, grid = p, g
    if target is None:
        return False
    z = res.zones[zone_id]
    grid.remove(target)
    picker = TilePicker(reg, rng)
    picker.used[target.tile.id] = 3
    ttype = target.tile.type
    allowed = None
    if target.o.hull:
        allowed = lambda t, oo, h=target.o.hull: oo.hull == h
    pick = picker.choose(grid, target.x, target.y, target.w, target.h, z.tags, ttype, allowed_orients=allowed)
    if pick is None:
        grid.place(target.tile, target.x, target.y, target.o, zone=zone_id)
        return False
    tile, o, _ = pick
    newp = grid.place(tile, target.x, target.y, o, zone=zone_id)
    newp.key = target.key
    for k in res.key:
        if k.get("zone") == zone_id:
            k["tile"] = tile.id
    return tile.id != target.tile.id
