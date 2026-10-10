"""Dressing: condition, peculiarities, overlays, markers, key, section, text."""
from __future__ import annotations

import json
import math
import re

from . import DATA_DIR, names
from . import filler as F
from .model import TIER

_COMMON = None


def common():
    global _COMMON
    if _COMMON is None:
        with open(DATA_DIR / "common.json", encoding="utf-8") as fh:
            _COMMON = json.load(fh)
    return _COMMON


def conditions():
    return list(common()["conditions"])


# ---------------------------------------------------------------------------
def apply_condition(res, rng, global_cond="Average", per_zone=None, mixed=False):
    """Global condition plus optional per-zone overrides (mixing is allowed)."""
    conds = conditions()
    if global_cond not in conds:
        global_cond = "Average"
    zc = {}
    per_zone = per_zone or {}
    gi = conds.index(global_cond)
    for zid, z in res.zones.items():
        base = getattr(z, "base", zid)
        c = per_zone.get(zid) or per_zone.get(base)
        if c is None and mixed and rng.random() < 0.35:
            c = conds[max(0, min(len(conds) - 1, gi + rng.choice((-2, -1, 1, 2))))]
        if c is not None and c in conds:
            zc[zid] = c
    res.meta["condition"] = global_cond
    res.meta["zone_conditions"] = zc


def apply_peculiarities(res, rng, count=2, table=None):
    table = table or common()["peculiarities"]
    count = max(0, min(count, len(table)))
    picks = rng.sample(table, count) if count else []
    res.meta["peculiarities"] = [dict(p) for p in picks]


# ---------------------------------------------------------------------------
def _tile_center(p):
    return p.x + p.w / 2.0, p.y + p.h / 2.0


def _place_tiles(res):
    return [p for g in res.grids for p in g.placed]


def apply_overlays(res, rng, enabled=None, intensity=0.5, arch=None):
    """Seeded state overlays. ``enabled`` is a set/dict of overlay names."""
    enabled = {k for k, v in (enabled.items() if isinstance(enabled, dict) else [(e, True) for e in (enabled or [])]) if v}
    if arch is not None:
        allowed = set(arch.get("overlays") or [])
        enabled &= allowed or enabled
    ov = {"lockdown": [], "power_failure": [], "quarantine": []}
    markers = []
    res.meta["overlays"] = sorted(enabled)
    res.meta["intensity"] = round(float(intensity), 2)
    tiles = _place_tiles(res)
    zone_ids = sorted({p.zone for p in tiles if p.zone})
    ent_zone = (res.layout.entrance or {}).get("zone") if res.layout is not None else ""
    pool = [z for z in zone_ids if z != ent_zone] or zone_ids
    n = len(pool)

    def pick(frac, minimum=1):
        k = min(len(pool), max(minimum, round(n * frac * max(0.0, float(intensity)))))
        return rng.sample(pool, k) if pool else []
    if "lockdown" in enabled:
        ov["lockdown"] = pick(0.25)
    if "power_failure" in enabled:
        ov["power_failure"] = pick(0.35)
    if "quarantine" in enabled:
        ov["quarantine"] = pick(0.2)
    if "breach" in enabled and tiles:
        k = max(1, round(float(intensity) * 3))
        ext = []
        for p in tiles:
            for side in ("N", "E", "S", "W"):
                dx, dy = {"N": (0, -1), "E": (1, 0), "S": (0, 1), "W": (-1, 0)}[side]
                cx = p.x + (p.w if side == "E" else -1 if side == "W" else p.w // 2)
                cy = p.y + (p.h if side == "S" else -1 if side == "N" else p.h // 2)
                g = res.grids[p.level]
                if (cx, cy) not in g.occ:
                    spot = _hull_point(p, side)
                    if spot is not None:
                        ext.append((p.level, spot[0], spot[1], p.zone))
        for lvl, bx, by, z in (rng.sample(ext, min(k, len(ext))) if ext else []):
            markers.append({"type": "breach", "level": lvl, "x": bx, "y": by, "label": "Breach / decompression", "zone": z})
    if "salvage" in enabled and tiles:
        k = max(1, round(len(tiles) * 0.2 * float(intensity)))
        for p in rng.sample(tiles, min(k, len(tiles))):
            cx, cy = _tile_center(p)
            markers.append({"type": "salvage", "level": p.level, "x": cx + 2, "y": cy + 2,
                            "label": "Salvage-stripped", "zone": p.zone})
    if "battle" in enabled and tiles:
        k = max(2, round(len(tiles) * 0.25 * float(intensity)))
        for p in rng.sample(tiles, min(k, len(tiles))):
            cx, cy = _tile_center(p)
            ox, oy = rng.randint(-5, 5), rng.randint(-5, 5)
            markers.append({"type": "damage", "level": p.level, "x": cx + ox, "y": cy + oy,
                            "label": "Battle damage", "zone": p.zone})
            res.grids[p.level].filler.append(F.piece("rubble", p.level, cx + ox - 1, cy + oy - 1, 3, 3))
    th = common()["threat"]
    if "threat" in enabled and tiles:
        low = [p for p in tiles if any(t in ("cargo", "waste", "workshop", "interstitial", "water", "engineering")
                                       for t in (res.zones[p.zone].tags if p.zone in res.zones else []))]
        sealed = [p for p in tiles if p.zone in ov["lockdown"] + ov["quarantine"]]
        cand = low + sealed + tiles
        k = max(1, round((1 + len(tiles) / 4.0) * float(intensity)))
        seen = set()
        for p in cand:
            if len(seen) >= k:
                break
            if id(p) in seen:
                continue
            seen.add(id(p))
            cx, cy = _tile_center(p)
            # vents and service spaces sit near tile corners
            ox = rng.choice((-1, 1)) * rng.randint(5, 8)
            oy = rng.choice((-1, 1)) * rng.randint(5, 8)
            markers.append({"type": "threat", "level": p.level, "x": cx + ox, "y": cy + oy, "gm_only": True,
                            "label": rng.choice(th["labels"]), "zone": p.zone})
    if "secrets" in enabled and tiles:
        k = max(1, round(float(intensity) * 2))
        for p in rng.sample(tiles, min(k, len(tiles))):
            cx, cy = _tile_center(p)
            markers.append({"type": "secret", "level": p.level, "x": cx - 3, "y": cy + 4, "gm_only": True,
                            "label": rng.choice(common()["secrets"]), "zone": p.zone})
    res.overlays = ov
    res.markers.extend(markers)


# ---------------------------------------------------------------------------
STATE_TEXT = {"lockdown": "LOCKDOWN: doors sealed, access only by override.",
              "power_failure": "POWER OUT: dark, no lifts or electronic doors.",
              "quarantine": "QUARANTINE: sealed, contamination protocols active."}


def _room_detail(res, zone_id):
    """What the GM should know about one room: state overlays, contents, threats and secrets."""
    states = [k for k in ("lockdown", "power_failure", "quarantine") if zone_id in (res.overlays or {}).get(k, [])]
    cats = {}
    for it in (getattr(res, "decor", None) or []):
        if it.get("zone") == zone_id and it.get("kind") == "item":
            cats[it.get("cat", "equipment")] = cats.get(it.get("cat", "equipment"), 0) + 1
    contents = [f"{n}x {c}" for c, n in sorted(cats.items(), key=lambda kv: -kv[1])[:6]]
    threats = [m.get("label", "threat") for m in res.markers if m.get("type") == "threat" and m.get("zone") == zone_id]
    secrets = [m.get("label", "secret") for m in res.markers if m.get("type") == "secret" and m.get("zone") == zone_id]
    parts = [STATE_TEXT[k] for k in states]
    dm = (res.meta.get("decor") or {})
    steps = (dm.get("spread") or {}).get(zone_id)
    if steps is not None and steps > 0 and steps <= dm.get("reach", 3):
        parts.append(f"Outbreak: {steps} door{'s' if steps != 1 else ''} from the source ({dm.get('origin_name', '')}); "
                     + ("the worst of the damage, barricades on the doors toward the source." if steps <= dm.get("reach", 3) // 2
                        else "signs of a struggle, the trouble has not stayed here."))
    if contents:
        parts.append("Furnished with: " + ", ".join(contents) + ".")
    if threats:
        parts.append("THREAT: " + "; ".join(threats) + ".")
    if secrets:
        parts.append("SECRET: " + "; ".join(secrets) + ".")
    return {"states": states, "contents": contents, "threats": threats, "secrets": secrets, "gm": " ".join(parts)}


def _key_point(p):
    """Where a tile's key number goes: the middle of its floor (a nose or wing does not fill its square), else
    the middle of the square. The point is snapped to the floor cell nearest that middle so it never floats outside."""
    from . import atmosphere, floors
    rows = atmosphere._floor(p)
    centre = (p.x + p.w / 2.0, p.y + p.h / 2.0)
    if rows is None:
        return centre
    cells = [(x, y) for y, r in enumerate(rows) for x, ch in enumerate(r) if ch != "o"]
    if not cells or len(cells) > 0.9 * len(rows) * len(rows[0]):
        return centre                                # (nearly) full tile: the middle is fine
    sub = floors.SUB
    mx = sum(c[0] for c in cells) / len(cells)
    my = sum(c[1] for c in cells) / len(cells)
    x, y = min(cells, key=lambda c: (c[0] - mx) ** 2 + (c[1] - my) ** 2)
    return (p.x + (x + 0.5) / sub, p.y + (y + 0.5) / sub)


def _hull_point(p, side):
    """Where a breach on ``side`` of placed tile ``p`` goes: on the hull itself, not in the empty corner of a nose or
    wing. Scans in from the middle of that side to the first cell that is not outside (floor maps, half squares).
    Without a floor map the middle of the side is used; a side with no hull near its middle gives None."""
    from . import atmosphere, floors
    mid = {"N": (p.x + p.w / 2, p.y), "S": (p.x + p.w / 2, p.y + p.h), "W": (p.x, p.y + p.h / 2), "E": (p.x + p.w, p.y + p.h / 2)}[side]
    rows = atmosphere._floor(p)
    if rows is None:
        return mid
    H, W = len(rows), len(rows[0])
    sub = floors.SUB
    for off in (0, 1, -1, 2, -2, 3, -3):            # a little either side of the middle if the middle is open
        if side in "NS":
            x = min(max(W // 2 + off, 0), W - 1)
            ys = range(H) if side == "N" else range(H - 1, -1, -1)
            for y in ys:
                if rows[y][x] != "o":
                    return (p.x + (x + 0.5) / sub, p.y + (y if side == "N" else y + 1) / sub)
        else:
            y = min(max(H // 2 + off, 0), H - 1)
            xs = range(W) if side == "W" else range(W - 1, -1, -1)
            for x in xs:
                if rows[y][x] != "o":
                    return (p.x + (x if side == "W" else x + 1) / sub, p.y + (y + 0.5) / sub)
    return None


def build_key(res, rng, arch=None):
    """Numbered key: one entry per room tile/zone, then vertical and utility entries."""
    key = []
    n = 0
    rt = common()["room_text"]
    cond_notes = common()["condition_notes"]
    zc = res.meta.get("zone_conditions", {})
    glob = res.meta.get("condition", "Average")
    for g in res.grids:
        ordered = sorted(g.placed, key=lambda p: (p.y // 10, p.x))
        for p in ordered:
            n += 1
            p.key = n
            z = res.zones.get(p.zone)
            tags = z.tags if z else list(p.tile.tags)
            sentence = rng.choice(rt.get(tags[0] if tags else "_default", rt["_default"]))
            cond = zc.get(p.zone, glob)
            access = f" Access: {z.access}." if z is not None and hasattr(z, "access") and res.kind == "site" else ""
            rooms = ", ".join(p.tile.rooms[:8]) if p.tile.rooms else ""
            text = f"{sentence}{access} {('Contains: ' + rooms + '.') if rooms else ''} Condition: {cond}. {cond_notes.get(cond, '')}".strip()
            title = z.name if z is not None and getattr(z, "name", None) else p.tile.title
            detail = _room_detail(res, p.zone)
            if detail["gm"]:
                text = f"{text} {detail['gm']}"
            kx, ky = _key_point(p)
            key.append({"n": n, "level": g.index, "x": kx, "y": ky, "title": title,
                        "text": text, "zone": p.zone, "tile": p.tile.id,
                        "player": (f"{sentence} Marked on the map: {rooms}." if rooms else sentence),
                        "states": detail["states"], "contents": detail["contents"],
                        "threats": detail["threats"], "secrets": detail["secrets"]})
        for f in g.filler:
            if f["kind"] in ("pad", "pit", "dome", "building") and f.get("label"):
                n += 1
                key.append({"n": n, "level": g.index, "x": f["x"] + f["w"] / 2.0, "y": f["y"] + f["h"] / 2.0,
                            "title": f["label"], "text": "A procedurally drawn area (no geomorph tile fits).",
                            "zone": "", "tile": ""})
    lay = res.layout
    if lay is not None:
        for v in lay.vertical:
            n += 1
            lvl = v["levels"][0]
            key.append({"n": n, "level": lvl, "x": v["x"] + v["w"] / 2.0, "y": v["y"] + v["h"] / 2.0,
                        "title": "Stairs, lifts and shafts" if v["kind"] != "shaft" else "Main shaft",
                        "text": "Stairs and lifts line up in the same position on every level: "
                                + ", ".join(res.grids[i].name for i in v["levels"]) + ".", "zone": "", "tile": ""})
        for v in lay.volumes:                # the railed drop on the level(s) above a double-height room
            for lvl in range(v["level"] - int(v.get("height", 2)) + 1, v["level"]):
                if v.get("paired") and lvl == v.get("upper_level"):
                    continue                  # that level has the room's real upper floor, keyed as its own room
                n += 1
                key.append({"n": n, "level": lvl, "x": v["x"] + 10.0, "y": v["y"] + 10.0,
                            "title": f"Overlook: {v['name']}",
                            "text": f"A railed opening above the {v['name']} on {res.grids[v['level']].name}. Anyone here can see, "
                                    "talk and shoot down into the room. Getting down means the stairs, a rope or a fall.",
                            "player": f"A railed opening looking down into the {v['name']}.", "zone": "", "tile": ""})
        shafts = [m for m in lay.markers if m["type"] == "shaft" and m.get("name") != "Main shaft"]
        if shafts:
            n += 1
            first = shafts[0]
            lvl = first["levels"][0]
            key.append({"n": n, "level": lvl, "x": first["x"] + 1, "y": first["y"] + 1, "title": "Utility shafts",
                        "text": "Shafts for " + ", ".join(sorted({m['name'] for m in shafts}))
                                + " run through the same spots on every level; marked U.", "zone": "", "tile": ""})
        if lay.entrance:
            res.markers.append({"type": "entrance", "level": lay.entrance["level"], "x": lay.entrance["x"],
                                "y": lay.entrance["y"], "label": "Entrance"})
    res.key = key
    return key


# ---------------------------------------------------------------------------
def build_section(res):
    """Data for the side-view building section diagram."""
    lay = res.layout
    levels = []
    verticals = []
    if lay is None:
        # ship: a single deck
        g = res.grids[0]
        if g.placed:
            b = g.bounds()
            levels.append({"name": g.name, "x": 0, "w": b[2] - b[0], "zones": [
                {"x": p.x - b[0], "w": p.w, "void": False} for p in g.placed if p.y == b[1] or True][:0]})
        res.section = {"levels": levels, "verticals": []}
        return res.section
    gx0 = min((p.x for g in res.grids for p in g.placed), default=0)
    gx1 = max((p.x + p.w for g in res.grids for p in g.placed), default=1)
    for g in res.grids:
        zs = []
        voids = [s for s in lay.slots if s.level == g.index and s.reserved == "void"]
        xs = [p.x for p in g.placed] + [s.x for s in voids]
        xe = [p.x + p.w for p in g.placed] + [s.x + s.w for s in voids]
        if not xs:
            levels.append({"name": g.name, "x": gx0 - gx0, "w": 4, "zones": []})
            continue
        for p in sorted(g.placed, key=lambda p: p.x):
            if not any(abs(z["x"] - (p.x - gx0)) < 1 and abs(z["w"] - p.w) < 1 for z in zs):
                zs.append({"x": p.x - gx0, "w": p.w, "void": False})
        for s in voids:
            zs.append({"x": s.x - gx0, "w": s.w, "void": True})
        levels.append({"name": g.name, "x": min(xs) - gx0, "w": max(xe) - min(xs), "zones": zs})
    for v in lay.vertical:
        verticals.append({"x": v["x"] - gx0, "w": max(2, v["w"]), "label": "core" if v["kind"] == "core" else v["kind"]})
    res.section = {"levels": levels, "verticals": verticals,
                   "volumes": [dict(v) for v in lay.volumes]}
    return res.section


# ---------------------------------------------------------------------------
def _zones_by_access(res):
    out = {"public": [], "staff": [], "restricted": [], "secure": [], "any": []}
    for z in res.zones.values():
        acc = getattr(z, "access", "staff")
        out["any"].append(z.name)
        if acc == "containment":
            acc = "secure"
        out.setdefault(acc, []).append(z.name)
    return out


def build_text(res, rng, arch=None, theme=None, name=None):
    c = common()
    zones = _zones_by_access(res)

    def fill(template):
        def repl(m):
            key = m.group(1)
            pool = zones.get(key) or zones["any"] or ["the facility"]
            return rng.choice(pool)
        import re
        return re.sub(r"\{zone:(\w+)\}", repl, template)
    atext = (arch or {}).get("text", {}) if arch else {}
    kind = (arch or {}).get("kind") or res.meta.get("ship_type", "ship").lower()
    env_t = c["environment"].get(res.meta.get("environment", ""), {})
    env_phrase = {"breathable": "on a breathable surface", "hostile": "under a hostile sky",
                  "vacuum": "in hard vacuum", "underground": "buried underground",
                  "orbital": "in orbit"}.get(res.meta.get("environment", ""), "")
    cond = res.meta.get("condition", "Average")
    corp = res.meta.get("corporation") or names.corp_name(rng, theme)
    res.meta["corporation"] = corp
    opener = rng.choice(c["descriptions"]["openers"]).format(
        name=name or res.meta.get("name", "This place"), condition_lower=cond.lower(), kind=kind,
        env_phrase=env_phrase, corp=corp)
    opener = opener.replace("  ", " ").replace(" .", ".")
    opener = re.sub(r"\b([Aa]) (?=[aeiouAEIOU])", lambda m: "An " if m.group(1) == "A" else "an ", opener)
    parts = [opener]
    parts += atext.get("description", [])[:1]
    parts.append(rng.choice(c["descriptions"]["closers"]))
    description = " ".join(p.strip() for p in parts if p)
    notes = list(atext.get("notes", [])) + rng.sample(c["notes"], min(2, len(c["notes"])))
    if env_t.get("note"):
        notes.append(env_t["note"])
    notes.append(c["condition_notes"].get(cond, ""))
    for p in res.meta.get("peculiarities", []):
        notes.append(f"{p['name']} ({p['kind']}): {p['text']}")
    if res.layout is not None:
        notes.extend(res.layout.notes)
    hooks = [{"type": h["type"], "text": fill(h["text"])} for h in atext.get("hooks", [])]
    types = [t for t in c["hook_types"] if t not in {h["type"] for h in hooks}]
    rng.shuffle(types)
    for t in types[: max(0, 4 - len(hooks))]:
        hooks.append({"type": t, "text": fill(rng.choice(c["hooks"][t]))})
    res.text = {"title": name or res.meta.get("name", ""), "description": description,
                "notes": [n for n in notes if n], "hooks": hooks}
    return res.text
