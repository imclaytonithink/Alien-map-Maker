"""Reports: tag table, gaps per archetype, edge review sheet."""
from __future__ import annotations

from . import archetype as archmod
from .placement import hull_sides
from .registry import Registry
from .tiling import TilePicker


def tag_table_report(reg: Registry) -> str:
    """Function-tag table for every tile (id, type, size, weighted tags)."""
    lines = ["id\ttype\tsize\ttags\ttitle"]
    for t in sorted(reg.tiles.values(), key=lambda t: (t.type, t.id)):
        tags = ", ".join(f"{k}:{v}" for k, v in sorted(t.tags.items(), key=lambda kv: -kv[1]))
        lines.append(f"{t.id}\t{t.type}\t{t.w}x{t.h}\t{tags}\t{t.title}")
    return "\n".join(lines)


def tag_coverage(reg: Registry) -> dict:
    """tag -> {tile type: count} (how well each function is covered)."""
    out: dict = {}
    for t in reg.tiles.values():
        for tag in t.tags:
            out.setdefault(tag, {}).setdefault(t.type, 0)
            out[tag][t.type] += 1
    return out


def gap_report(reg: Registry, arch: dict) -> list:
    """Zones of ``arch`` with no suitable tile (they will be drawn procedurally)."""
    picker = TilePicker(reg, None)
    gaps = []
    for z in arch["zones"]:
        if z.get("filler_only"):
            gaps.append({"zone": z["id"], "name": z["name"], "reason": "filler only (no tile fits: "
                         f"{z.get('filler', 'ground')} is drawn procedurally)"})
            continue
        if not picker.pool("standard", 20, 20, z.get("tags", [])):
            gaps.append({"zone": z["id"], "name": z["name"],
                         "reason": f"no standard tile tagged {'/'.join(z.get('tags', []))}"})
    return gaps


def all_gaps(reg: Registry, archetypes=None) -> dict:
    A = archetypes or archmod.load_all()
    return {name: gap_report(reg, a) for name, a in A.items() if name != "_problems"}


def edge_review_list(reg: Registry) -> list:
    """Tiles whose edge detection was low-confidence (for manual correction)."""
    out = []
    for t in reg.tiles.values():
        if t.review:
            hs = hull_sides(t)
            out.append({"id": t.id, "type": t.type, "title": t.title,
                        "sides": {s: e["conf"] for s, e in t.edges.items() if s not in hs and e["conf"] < 0.9}})
    return sorted(out, key=lambda r: r["id"])
