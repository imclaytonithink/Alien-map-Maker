"""Site generator: archetype -> program -> zone graph -> topology -> placement."""
from __future__ import annotations

import random

from . import assign, connect, layouts
from . import filler as F
from .edges import DOOR
from .layouts import T
from .model import Layout, Link, Result, Slot, ZoneInst
from .placement import DIRS, LevelGrid, OPPOSITE, Placed, orientations
from .program import make_program, required_count, zone_from_spec
from .tiling import TilePicker

VERTICAL_ZONE = {"id": "core", "name": "Stairs & lifts core", "tags": ["vertical"], "access": "staff",
                 "required": False}


def _dir_between(a, b):
    dx, dy = (b.x + b.w / 2) - (a.x + a.w / 2), (b.y + b.h / 2) - (a.y + a.h / 2)
    if abs(dx) >= abs(dy):
        return "E" if dx > 0 else "W"
    return "S" if dy > 0 else "N"


def reserve_volumes(arch, lay: Layout, rng):
    """Pick slots for double/triple-height rooms and mark the space above as void."""
    out = {}
    sv = (arch.get("vertical") or {}).get("special_volumes", [])
    zspec = {z["id"]: z for z in arch["zones"]}
    lo, hi = assign.building_levels(lay)
    for v in sv:
        zid, height = v.get("zone"), int(v.get("height", 2))
        spec = zspec.get(zid)
        if spec is None or height < 2 or hi - lo + 1 < height:
            continue
        inst = zone_from_spec(spec, 1, arch.get("entrance_zone", ""), sv)
        inst.height = height
        cands = [s for s in lay.slots if s.zone is None and not s.reserved and s.role not in ("vertical", "headframe", "hub")
                 and assign.constraint_ok(inst, s, lo, hi)]
        # the room rises through the level(s) above it: those slots become void
        good = []
        for s in cands:
            above = [q for q in lay.slots if (q.x, q.y) == (s.x, s.y) and s.level - height < q.level < s.level
                     and q.zone is None and not q.reserved and q.role == ""]
            if len(above) == height - 1:
                good.append((s, above))
        if not good:
            lay.notes.append(f"No room for the {height}x-height '{spec['name']}' volume; it was built single height.")
            continue
        s, above = rng.choice(good)
        s.zone = inst
        for q in above:
            q.reserved = "void"
        lay.volumes.append({"zone": inst.id, "name": spec["name"], "x": s.x, "y": s.y, "level": s.level,
                            "height": height, "open_below": bool(v.get("open_below", True))})
        out[zid] = out.get(zid, 0) + 1
    return out


def special_zones(arch, lay: Layout):
    """Zones for vertical cores / head-frames (they are not drawn from the program)."""
    has_secure = any(z.get("access") in ("secure", "containment") for z in arch["zones"])
    n = 0
    for s in lay.slots:
        if s.role == "vertical" and s.zone is None:
            n += 1
            z = zone_from_spec(VERTICAL_ZONE, n, "", None)
            z.checkpoint = has_secure
            s.zone = z


def run_assign(arch, lay, rng, taken, mode="planned", options=None):
    free = [s for s in lay.slots if s.zone is None and not s.reserved]
    arch2 = dict(arch)
    prog, shortfall = make_program(arch2, rng, len(free))
    # skip instances for zones already satisfied by reserved volumes
    for base, n in taken.items():
        for _ in range(n):
            for i, z in enumerate(prog):
                if z.base == base:
                    prog.pop(i)
                    break
    prog = prog[:len(free)]
    from .program import general_zone
    while len(prog) < len(free):
        prog.append(zone_from_spec(general_zone(len(prog) + 1), len(prog) + 1, "", None))
    g = 3.0 * ((options or {}).get("grouping", 0.6) if options else 0.6)
    if mode == "random":                       # function ignored: no scoring at all
        rep = assign.assign_zones(lay, prog, rng, restarts=1, iters=0)
    elif mode == "selective":                  # light repair only
        rep = assign.assign_zones(lay, prog, rng, restarts=1, iters=40)
    else:
        rep = assign.assign_zones(lay, prog, rng, grouping=g)
    return prog, rep, shortfall


def generate_site(registry, arch: dict, rng: random.Random, scale="medium", env=None, condition=None,
                  mode="planned", options=None) -> Result:
    options = options or {}
    env = env or (arch.get("environments") or ["breathable"])[0]
    topology = arch["topology"]
    preset = arch["scales"][scale]
    ctx = {"shafts": (arch.get("vertical") or {}).get("shafts", []), "dome": arch.get("dome")}
    need = required_count(arch)
    lay = None
    issues = []
    for attempt in range(8):
        lay = layouts.build(topology, rng, preset, env, need, ctx)
        special_zones(arch, lay)
        taken = reserve_volumes(arch, lay, rng)
        free = [s for s in lay.slots if s.zone is None and not s.reserved]
        if len(free) + len(taken) >= need:
            break
        need = need + (need - len(free) - len(taken))
    taken_counts = taken
    prog, rep, shortfall = run_assign(arch, lay, rng, taken_counts, mode, options)
    if rep["unplaced"]:
        issues.append(f"zones with no legal slot: {', '.join(rep['unplaced'])}")
    zones = {s.zone.id: s.zone for s in lay.slots if s.zone}
    # grids per level
    b = _bounds(lay)
    cols = b[2] + layouts.PAD * 2
    rows = b[3] + layouts.PAD * 2
    grids = [LevelGrid(i, lay.level_names[i], cols, rows) for i in range(lay.levels)]
    picker = TilePicker(registry, rng)
    gaps = {}
    _place_slots(registry, arch, lay, grids, picker, rng, env, gaps, mode)
    # filler (ground, roads, shafts, ...) belongs to its level
    for f in lay.filler:
        grids[f["level"]].filler.append(dict(f))
    # volumes: void above tall rooms
    for s in lay.slots:
        if s.reserved == "void":
            grids[s.level].filler.append(F.piece("void", s.level, s.x, s.y, s.w, s.h, label="Open to below"))
    res = Result(kind="site", meta={}, grids=grids, zones=zones, layout=lay, issues=issues, gaps=gaps,
                 options=options, registry=registry)
    res.meta.update({"topology": topology, "scale": scale, "environment": env, "levels": lay.levels,
                     "mode": mode, "score": rep.get("score"), "relaxed": rep.get("relaxed", [])})
    _links_and_paths(res, arch, ctx)
    _entrance(res, arch, env)
    _perimeter(res, arch, env, ctx)
    if mode == "movie":
        movie_crop(res, rng)
    return res


def movie_crop(res: Result, rng, fraction=0.4):
    """Movie set: keep only a connected area where the action takes place."""
    from .validate import zone_graph
    adj = zone_graph(res)
    zones = [z for z in res.zones if adj.get(z)]
    if len(zones) < 4:
        return
    start = rng.choice(zones)
    keep = {start}
    frontier = [start]
    target = max(3, round(len(res.zones) * fraction))
    while frontier and len(keep) < target:
        cur = frontier.pop(0)
        nbrs = [n for n in sorted(adj[cur]) if n not in keep]
        rng.shuffle(nbrs)
        for n in nbrs:
            if len(keep) >= target:
                break
            keep.add(n)
            frontier.append(n)
    removed = set(res.zones) - keep
    for g in res.grids:
        for p in list(g.placed):
            if p.zone in removed:
                g.remove(p)
        g.filler = [f for f in g.filler if not (set(f.get("zones", [])) & removed)]
    res.links = [l for l in res.links if l.get("a") not in removed and l.get("b") not in removed]
    for zid in removed:
        res.zones.pop(zid, None)
    # crop the visible area to what is left
    xs0, ys0, xs1, ys1 = [], [], [], []
    for g in res.grids:
        for p in g.placed:
            xs0.append(p.x); ys0.append(p.y); xs1.append(p.x + p.w); ys1.append(p.y + p.h)
    if xs0:
        res.meta["crop"] = (min(xs0) - 3, min(ys0) - 3, max(xs1) + 3, max(ys1) + 3)
    res.meta["movie_removed"] = len(removed)


def _bounds(lay):
    layouts._bounds(lay)
    return lay.bounds


def _place_slots(reg, arch, lay: Layout, grids, picker: TilePicker, rng, env, gaps, mode):
    ent = assign.entrance_slot(lay)
    order = _bfs_order(lay, ent)
    link_dirs = {}
    for l in lay.links:
        a, b = lay.slots[l.a], lay.slots[l.b]
        if a.level == b.level:
            link_dirs.setdefault(a.idx, set()).add(_dir_between(a, b))
            link_dirs.setdefault(b.idx, set()).add(_dir_between(b, a))
    core_choice = {}
    for idx in order:
        s = lay.slots[idx]
        g = grids[s.level]
        if s.reserved == "void" or s.zone is None:
            continue
        z = s.zone
        if s.kind == "filler" or z.filler_only:
            kind = z.filler or "ground"
            g.filler.append(F.piece(kind, s.level, s.x, s.y, s.w, s.h, label=z.name, zones=[z.id]))
            gaps.setdefault(z.base, "filler only: no suitable tile (drawn procedurally)")
            s.kind = "filler"
            continue
        tags = list(z.tags)
        fixed = None
        allowed = None
        if s.role == "vertical":
            key = (s.x, s.y)
            if key in core_choice:
                tile, o = core_choice[key]
                fixed = tile
                allowed = (lambda t, oo, o=o: oo.rot == o.rot and oo.mirror == o.mirror)
            else:
                tags = ["vertical"]
        pool_ok = picker.pool("standard", s.w, s.h, tags if s.role != "vertical" else ["vertical"],
                              minimum=0.9 if s.role == "vertical" else 0.5)
        if not pool_ok and s.role != "vertical":
            gaps.setdefault(z.base, f"no tile tagged {'/'.join(tags)}; nearest alternative used")
        prefer = link_dirs.get(idx, set())
        if mode == "random":
            tags = []
        if mode == "selective" and s.role != "vertical":
            from .ship import _selective_pick
            pick = _selective_pick(picker, g, {"x": s.x, "y": s.y, "w": s.w, "h": s.h}, tags, "standard", allowed, rng)
        else:
            pick = picker.choose(g, s.x, s.y, s.w, s.h, tags, "standard", prefer_open=prefer,
                                 fixed=fixed, allowed_orients=allowed,
                                 reuse_penalty=0.2 if s.role == "vertical" else 0.45)
        if pick is None:
            lay.notes.append(f"No tile fit slot {s.idx} ({z.name}); drawn as a plain room.")
            g.filler.append(F.piece("building", s.level, s.x, s.y, s.w, s.h, label=z.name, zones=[z.id]))
            s.kind = "filler"
            continue
        tile, o, f = pick
        s.placed = g.place(tile, s.x, s.y, o, zone=z.id)
        if s.role == "vertical":
            core_choice[(s.x, s.y)] = (tile, o)


def _bfs_order(lay: Layout, start):
    pairs = assign.adjacency_pairs(lay)
    adj = {}
    for a, b, w in pairs:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    seen, order = {start}, [start]
    i = 0
    while i < len(order):
        for m in adj.get(order[i], []):
            if m not in seen:
                seen.add(m)
                order.append(m)
        i += 1
    order.extend(s.idx for s in lay.slots if s.idx not in seen)
    return order


def _links_and_paths(res: Result, arch, ctx):
    """Doors, seals, walkways: turn adjacency into drawn, validated connections."""
    lay = res.layout
    zones = res.zones
    all_links = []
    for g in res.grids:
        links, pieces = connect.realize_contacts(g, zones, assign.door_allowed)
        g.filler.extend(pieces)
        for a, b, state in links:
            all_links.append({"level": g.index, "a": a.zone, "b": b.zone, "state": state,
                              "a_tile": a.tile.id, "b_tile": b.tile.id})
    # explicit links: walkways / tunnels get drawn corridors; others are logical
    for l in lay.links:
        sa, sb = lay.slots[l.a], lay.slots[l.b]
        za, zb = sa.zone, sb.zone
        ok = True if za is None or zb is None else assign.door_allowed(za, zb)
        rec = {"level": sa.level, "a": za.id if za else "", "b": zb.id if zb else "", "state": "native",
               "kind": l.kind, "slot_a": l.a, "slot_b": l.b}
        if not ok:
            rec["state"] = "sealed"
            all_links.append(rec)
            l.open = False
            l.sealed = True
            continue
        l.open = True
        if sa.level == sb.level and l.kind in ("walkway", "tunnel"):
            ga = res.grids[sa.level]
            ba = sa.placed if sa.placed is not None else connect.Box(sa.x, sa.y, sa.w, sa.h, sa.level)
            bb = sb.placed if sb.placed is not None else connect.Box(sb.x, sb.y, sb.w, sb.h, sb.level)
            if not hasattr(ba, "level"):
                ba.level = sa.level
            if not hasattr(bb, "level"):
                bb.level = sb.level
            ba.level, bb.level = sa.level, sb.level
            pcs, _sa, _sb = connect.walkway_between(ga, ba, bb, kind=l.kind if l.kind != "tunnel" else "tunnel")
            for pc in pcs:
                pc["zones"] = [za.id if za else "", zb.id if zb else ""]
            ga.filler.extend(pcs)
            rec["state"] = "patched"
            l.patched = True
        elif sa.level != sb.level:
            rec["state"] = "vertical"
        all_links.append(rec)
    # vertical cores: stairs and lifts join the same spot on consecutive levels
    cores = {}
    for sl in lay.slots:
        if sl.role == "vertical" and sl.zone is not None:
            cores.setdefault((sl.x, sl.y), []).append(sl)
    for cell, ss in cores.items():
        ss.sort(key=lambda q: q.level)
        for a, b in zip(ss, ss[1:]):
            all_links.append({"level": a.level, "a": a.zone.id, "b": b.zone.id, "state": "vertical",
                              "kind": "core", "slot_a": a.idx, "slot_b": b.idx})
    res.links = all_links


def _outside_door(g: LevelGrid, p: Placed):
    """A native door square on ``p`` facing empty space: ``(side, coord)`` or None."""
    for side, (dx, dy) in DIRS.items():
        cls = p.side_cls(side)
        n = len(cls)
        for i in range(n):
            if cls[i] != DOOR:
                continue
            cx = p.x + (i if side in "NS" else (p.w if side == "E" else -1))
            cy = p.y + (i if side in "EW" else (p.h if side == "S" else -1))
            if (cx, cy) in g.occ or side in p.o.hull:
                continue
            coord = (p.y + i) if side in ("E", "W") else (p.x + i)
            return side, coord
    return None


def _entrance(res: Result, arch, env):
    lay = res.layout
    envs = _env_table(env)
    needs_airlock = envs.get("airlocks", False)
    ent = assign.entrance_slot(lay)
    s = lay.slots[ent]
    g = res.grids[s.level]
    kind = "airlock" if needs_airlock else "door"
    label = "A/L" if needs_airlock else "Entrance"
    if s.placed is not None:
        found = _outside_door(g, s.placed)
        p = s.placed
        if found is None:
            side = "S"
            coord = p.x + p.w // 2
        else:
            side, coord = found
        bx, by = connect.boundary_point(p, side, coord)
        w = 3 if needs_airlock else 2
        if side in ("E", "W"):
            piece = F.piece(kind, g.index, bx if side == "E" else bx - w, by - 1, w, 2, label=label)
        else:
            piece = F.piece(kind, g.index, bx - 1, by if side == "S" else by - w, 2, w, label=label)
        g.filler.append(piece)
        lay.entrance = {"slot": ent, "level": g.index, "x": bx, "y": by, "kind": kind,
                        "zone": s.zone.id if s.zone else "", "side": side}
    else:
        lay.entrance = {"slot": ent, "level": s.level, "x": s.x + s.w / 2, "y": s.y, "kind": kind,
                        "zone": s.zone.id if s.zone else "", "side": "N"}
    if env == "underground" and res.layout.level_names[0] == "Surface" and not any(
            x["level"] == 0 and x["kind"] == "building" for x in g.filler if x.get("kind") == "building"):
        sg = res.grids[0]
        vc = next((v for v in lay.vertical if v["kind"] == "core"), None)
        if vc and sg is not None and not sg.placed:
            sg.filler.append(F.piece("building", 0, vc["x"] + 4, vc["y"] + 4, 12, 8, label="Entrance structure"))
            lay.entrance.setdefault("surface", True)


def _perimeter(res: Result, arch, env, ctx):
    """Exterior elements, only where they would realistically exist.

    * a dome only for domed towns (which need no wall as well);
    * a fence only in a breathable atmosphere (in vacuum or toxic air it keeps nothing out), a blast wall for
      military/prison style compounds anywhere with an atmosphere (never in vacuum);
    * the single gate sits on the side nearest the entrance (an airlock when the environment needs one) and a road
      runs from it straight to the entrance when nothing is in the way.
    Sealed walkways, airlocks at the entrance and landing pads come from the layout and the archetype's zones.
    """
    box = ctx.get("perimeter_box")
    g = res.grids[0]
    envt = _env_table(env)
    if arch.get("dome") and box:
        x0, y0, x1, y1 = box
        m = 3
        g.filler.append(F.piece("dome", 0, x0 - m - 2, y0 - m - 2, x1 - x0 + 2 * m + 4, y1 - y0 + 2 * m + 4))
        res.layout.notes.append("The settlement sits under a dome.")
        return
    perim = arch.get("perimeter")
    if not box or not perim:
        return
    if perim == "fence" and env != "breathable":
        res.layout.notes.append("No perimeter fence: it would keep nothing out in this environment; buildings are "
                                "linked by sealed walkways.")
        return
    if perim == "wall" and env == "vacuum":
        return
    x0, y0, x1, y1 = box
    m = 3
    kind = "fence" if perim == "fence" else "wall"
    bx0, by0, bx1, by1 = x0 - m, y0 - m, x1 + m, y1 + m
    ent = res.layout.entrance or {}
    ex, ey = ent.get("x", (x0 + x1) / 2), ent.get("y", y1)
    sides = {"N": abs(ey - by0), "S": abs(ey - by1), "W": abs(ex - bx0), "E": abs(ex - bx1)}
    side = min(sides, key=sides.get)
    gw = 4
    t = 0.5
    if side in ("N", "S"):
        gx = min(max(ex - gw / 2, bx0 + 1), bx1 - gw - 1)
        gy = by0 if side == "N" else by1 - t
        g.filler.append(F.piece(kind, 0, bx0, by0, bx1 - bx0, t) if side != "N" else F.piece(kind, 0, bx0, by0, gx - bx0, t))
        if side == "N":
            g.filler.append(F.piece(kind, 0, gx + gw, by0, bx1 - gx - gw, t))
            g.filler.append(F.piece(kind, 0, bx0, by1 - t, bx1 - bx0, t))
        else:
            g.filler.append(F.piece(kind, 0, bx0, by1 - t, gx - bx0, t))
            g.filler.append(F.piece(kind, 0, gx + gw, by1 - t, bx1 - gx - gw, t))
        g.filler.append(F.piece(kind, 0, bx0, by0, t, by1 - by0))
        g.filler.append(F.piece(kind, 0, bx1 - t, by0, t, by1 - by0))
        gate = (gx, gy - (0.5 if side == "N" else 0.5), gw, 1.5)
        axis_x = gx + gw / 2
        road = ((axis_x - 1, by0 + 1, 2, ey - by0 - 1) if side == "N" else (axis_x - 1, ey, 2, by1 - ey - 1))
    else:
        gy = min(max(ey - gw / 2, by0 + 1), by1 - gw - 1)
        gx = bx0 if side == "W" else bx1 - t
        if side == "W":
            g.filler.append(F.piece(kind, 0, bx0, by0, t, gy - by0))
            g.filler.append(F.piece(kind, 0, bx0, gy + gw, t, by1 - gy - gw))
            g.filler.append(F.piece(kind, 0, bx1 - t, by0, t, by1 - by0))
        else:
            g.filler.append(F.piece(kind, 0, bx1 - t, by0, t, gy - by0))
            g.filler.append(F.piece(kind, 0, bx1 - t, gy + gw, t, by1 - gy - gw))
            g.filler.append(F.piece(kind, 0, bx0, by0, t, by1 - by0))
        g.filler.append(F.piece(kind, 0, bx0, by0, bx1 - bx0, t))
        g.filler.append(F.piece(kind, 0, bx0, by1 - t, bx1 - bx0, t))
        gate = (gx - 0.5, gy, 1.5, gw)
        axis_y = gy + gw / 2
        road = ((bx0 + 1, axis_y - 1, ex - bx0 - 1, 2) if side == "W" else (ex, axis_y - 1, bx1 - ex - 1, 2))
    gate_kind = "airlock" if envt.get("airlocks") else "door"
    g.filler.append(F.piece(gate_kind, 0, *gate, label="Gate"))
    rx, ry, rw, rh = road
    clear = rw > 0.5 and rh > 0.5 and not any(
        (cx, cy) in g.occ for cx in range(int(rx), int(rx + rw)) for cy in range(int(ry), int(ry + rh)))
    if clear and env == "breathable":
        g.filler.insert(0, F.piece("road", 0, rx, ry, rw, rh))
    res.layout.notes.append(f"Perimeter {'fence' if kind == 'fence' else 'wall'} with a single gate on the {side} side, "
                            "nearest the entrance.")
    res.layout.gate = {"side": side, "box": [bx0, by0, bx1, by1], "kind": gate_kind, "x": gate[0], "y": gate[1]}


def _env_table(env):
    import json
    from . import DATA_DIR
    with open(DATA_DIR / "common.json", encoding="utf-8") as fh:
        return json.load(fh)["environment"].get(env, {})
