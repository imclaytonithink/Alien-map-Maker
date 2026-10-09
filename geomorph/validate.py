"""Validation of a generated result: connectivity, routes, access, alignment."""
from __future__ import annotations

from collections import deque

from .model import TIER

ROUTE_ALLOWED_DEFAULT = {"staff": ["public", "staff", "restricted", "secure"], "visitor": ["public"],
                         "emergency": ["public", "staff", "restricted", "secure", "containment"]}


def zone_graph(res):
    """Adjacency among zone ids (open connections only)."""
    adj = {zid: set() for zid in res.zones}
    for l in res.links:
        if l.get("state") in ("native", "patched", "vertical"):
            a, b = l.get("a"), l.get("b")
            if a in adj and b in adj and a != b:
                adj[a].add(b)
                adj[b].add(a)
    return adj


def _bfs(adj, start, allowed=None, blocked=()):
    seen = {start}
    dq = deque([start])
    while dq:
        n = dq.popleft()
        for m in adj.get(n, ()):
            if m in seen or m in blocked:
                continue
            if allowed is not None and not allowed(m):
                continue
            seen.add(m)
            dq.append(m)
    return seen


def entrance_zone_id(res):
    lay = res.layout
    if lay is not None and lay.entrance.get("zone"):
        return lay.entrance["zone"]
    for z in res.zones.values():
        if getattr(z, "entrance", False):
            return z.id
    return next(iter(res.zones), None)


def validate(res, arch=None) -> list:
    issues = list(res.issues)
    # 1. no overlaps, everything in bounds
    for g in res.grids:
        cells = {}
        for p in g.placed:
            for c in p.cells():
                if c in cells and cells[c] is not p:
                    issues.append(f"overlap on {g.name} at {c}")
                    break
                cells[c] = p
    adj = zone_graph(res)
    ent = entrance_zone_id(res)
    # 2. connectivity (levels joined by vertical structures)
    if res.kind == "site" and ent in adj:
        reach = _bfs(adj, ent)
        missing = [z for z in res.zones if z not in reach]
        if missing:
            issues.append("not connected to the entrance: " + ", ".join(sorted(missing)))
    # 3. routes & access gradient
    if res.kind == "site" and arch is not None and ent in adj:
        routes = {k: set(v) for k, v in (arch.get("routes") or {}).items()}
        routes.setdefault("emergency", set(ROUTE_ALLOWED_DEFAULT["emergency"]))
        for zid, z in res.zones.items():
            ok = False
            for rname, allowed in routes.items():
                if z.access not in allowed:
                    continue
                reach = _bfs(adj, ent, allowed=lambda m, a=allowed: res.zones[m].access in a)
                if zid in reach or zid == ent:
                    ok = True
                    break
            if not ok:
                issues.append(f"no legal route reaches {z.name} ({zid}, {z.access})")
        # secure zones must not be reachable from public ones without passing a checkpoint
        cps = {zid for zid, z in res.zones.items() if z.checkpoint}
        for zid, z in res.zones.items():
            if z.tier != 0 or z.checkpoint:
                continue
            reach = _bfs(adj, zid, blocked=cps)
            bad = [m for m in reach if res.zones[m].tier >= 3 and m not in cps]
            if bad:
                issues.append(f"{', '.join(sorted(bad))} reachable from public {z.name} without a checkpoint")
                break
    # 4. vertical alignment
    lay = res.layout
    if lay is not None:
        by_cell = {}
        for s in lay.slots:
            if s.role == "vertical" and s.placed is not None:
                by_cell.setdefault((s.x, s.y), []).append(s)
        for cell, ss in by_cell.items():
            sigs = {(s.placed.tile.id, s.placed.o.rot, s.placed.o.mirror, s.placed.x, s.placed.y) for s in ss}
            if len({(sg[0], sg[1], sg[2]) for sg in sigs}) > 1:
                issues.append(f"vertical core at {cell} differs between levels")
        for v in lay.vertical:
            if v["kind"] == "core" and v["source"] == "tile":
                lv = {s.level for s in lay.slots if (s.x, s.y) == (v["x"], v["y"]) and s.role == "vertical"}
                if lv != set(v["levels"]):
                    issues.append("vertical core missing on some levels")
        for vol in lay.volumes:
            for k in range(1, vol["height"]):
                lvl = vol["level"] - k
                if not any(s.level == lvl and (s.x, s.y) == (vol["x"], vol["y"]) and s.reserved == "void"
                           for s in lay.slots):
                    issues.append(f"tall room {vol['name']} has no void on level {lvl + 1}")
    # 5. required zones present (Planned layouts guarantee them)
    if arch is not None and res.meta.get("mode") == "planned" and res.kind == "site":
        have = {}
        for z in res.zones.values():
            have[z.base] = have.get(z.base, 0) + 1
        for spec in arch["zones"]:
            if spec.get("required", True) and have.get(spec["id"], 0) < spec.get("min", 1):
                issues.append(f"required zone '{spec['name']}' missing")
    return issues
