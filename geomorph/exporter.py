"""Packaging and export: JSON, PNG per level, multi-page PDF, save/load of layouts.

A *package* is the full output in the style of a published site layout: title,
short description, Notes (GM guidance), Adventure Hooks (each with a type), a
map per level with numbered markers, the numbered key, a building section and
a credits footer. The GM version includes secrets and the threat overlay; the
player version hides them.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw

from . import CREDITS, render
from . import filler as F
from .model import Result, ZoneInst
from .placement import LevelGrid, orientation_for
from .registry import Registry

PAGE = (1275, 1650)          # US Letter at 150 dpi
MARGIN = 90


# ---------------------------------------------------------------------------
# JSON package (also the save/load format for layouts)
# ---------------------------------------------------------------------------
def to_package(res: Result, gm=True) -> dict:
    lay = res.layout
    levels = []
    for g in res.grids:
        levels.append({"index": g.index, "name": g.name, "cols": g.cols, "rows": g.rows,
                       "tiles": [p.to_json() for p in g.placed], "filler": [dict(f) for f in g.filler]})
    markers = [m for m in res.markers if gm or not m.get("gm_only")]
    pkg = {
        "version": 1, "kind": res.kind, "meta": _jsonable(res.meta), "options": _jsonable(res.options),
        "title": res.text.get("title", res.meta.get("name", "")),
        "description": res.text.get("description", ""),
        "notes": res.text.get("notes", []) if gm else [],
        "hooks": res.text.get("hooks", []) if gm else [],
        "levels": levels,
        "zones": {zid: {"name": z.name, "tags": z.tags, "access": z.access, "checkpoint": z.checkpoint,
                        "base": z.base, "entrance": z.entrance} for zid, z in res.zones.items()},
        "key": [{k: v for k, v in e.items()} for e in res.key] if gm else
               [{"n": e["n"], "level": e["level"], "title": e["title"]} for e in res.key],
        "markers": markers, "overlays": res.overlays if gm else {k: v for k, v in res.overlays.items() if k != "lockdown"},
        "links": res.links, "section": res.section, "issues": res.issues, "gaps": res.gaps,
        "entrance": (lay.entrance if lay is not None else {}),
        "credits": CREDITS, "gm": bool(gm),
    }
    if lay is not None:
        pkg["layout"] = {"topology": lay.topology, "level_names": lay.level_names, "vertical": lay.vertical,
                         "volumes": lay.volumes, "shaft_markers": lay.markers, "notes": lay.notes}
    return pkg


def _jsonable(o):
    return json.loads(json.dumps(o, default=lambda x: sorted(x) if isinstance(x, set) else str(x)))


def save_layout(res: Result, path, gm=True):
    Path(path).write_text(json.dumps(to_package(res, gm), indent=1), encoding="utf-8")


def load_layout(path_or_dict, registry: Registry) -> Result:
    """Rebuild a result from a saved layout (tiles are looked up by id in ``registry``)."""
    pkg = path_or_dict if isinstance(path_or_dict, dict) else json.loads(Path(path_or_dict).read_text(encoding="utf-8"))
    grids = []
    for lv in pkg["levels"]:
        g = LevelGrid(lv["index"], lv["name"], lv["cols"], lv["rows"])
        for t in lv["tiles"]:
            tile = registry.tiles[t["tile"]]
            o = orientation_for(tile, t["rot"], t["mirror"])
            p = g.place(tile, t["x"], t["y"], o, zone=t.get("zone", ""))
            p.key = t.get("key", 0)
        g.filler = [dict(f) for f in lv["filler"]]
        grids.append(g)
    zones = {}
    for zid, z in pkg.get("zones", {}).items():
        zones[zid] = ZoneInst(id=zid, base=z.get("base", zid.split("#")[0]), name=z["name"], tags=z["tags"],
                              access=z.get("access", "staff"), checkpoint=z.get("checkpoint", False),
                              entrance=z.get("entrance", False))
    res = Result(kind=pkg["kind"], meta=pkg["meta"], grids=grids, zones=zones, options=pkg.get("options", {}),
                 registry=registry, markers=pkg.get("markers", []), overlays=pkg.get("overlays", {}),
                 key=pkg.get("key", []), links=pkg.get("links", []), section=pkg.get("section", {}),
                 issues=pkg.get("issues", []), gaps=pkg.get("gaps", {}),
                 text={"title": pkg.get("title", ""), "description": pkg.get("description", ""),
                       "notes": pkg.get("notes", []), "hooks": pkg.get("hooks", [])})
    return res


# ---------------------------------------------------------------------------
# PNG
# ---------------------------------------------------------------------------
def export_png(res: Result, out_dir, images: render.TileImages, pps=16, gm=True, prefix=None):
    """One PNG per level plus the building section. Returns the file paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = prefix or _slug(res.meta.get("name", "map"))
    paths = []
    for g in res.grids:
        if not g.placed and not any(f["kind"] not in ("ground", "rock") for f in g.filler) and len(res.grids) > 1 \
                and res.layout is not None and g.name == "Surface" and not g.filler:
            continue
        im = render.render_level(res, g.index, images, pps=pps, gm=gm)
        p = out_dir / f"{prefix}_level{g.index + 1}_{'gm' if gm else 'player'}.png"
        im.convert("RGB").save(p)
        paths.append(str(p))
    if len(res.grids) > 1 and res.section.get("levels"):
        im = render.render_section(res)
        p = out_dir / f"{prefix}_section.png"
        im.convert("RGB").save(p)
        paths.append(str(p))
    return paths


def _slug(s):
    return "".join(c if c.isalnum() else "_" for c in s.lower()).strip("_") or "map"


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
class _Page:
    def __init__(self, title=""):
        self.im = Image.new("RGB", PAGE, (250, 248, 244))
        self.d = ImageDraw.Draw(self.im)
        self.y = MARGIN
        self.title = title

    def heading(self, text, size=40):
        f = render._font(size)
        self.d.text((MARGIN, self.y), text, fill=(20, 40, 44), font=f)
        self.y += int(size * 1.5)

    def paragraph(self, text, size=24, color=(30, 30, 30), indent=0, gap=12, bold=False):
        f = render._font(size, bold)
        width = PAGE[0] - 2 * MARGIN - indent
        for line in self._wrap(text, f, width):
            self.d.text((MARGIN + indent, self.y), line, fill=color, font=f)
            self.y += int(size * 1.35)
        self.y += gap

    def _wrap(self, text, font, width):
        lines, cur = [], ""
        for word in str(text).split():
            trial = (cur + " " + word).strip()
            if self.d.textlength(trial, font=font) <= width:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = word
        lines.append(cur)
        return lines

    def room(self, need):
        return self.y + need < PAGE[1] - MARGIN - 60

    def footer(self, n, total=None):
        f = render._font(15, False)
        self.d.line((MARGIN, PAGE[1] - MARGIN - 30, PAGE[0] - MARGIN, PAGE[1] - MARGIN - 30), fill=(150, 150, 150))
        for i, line in enumerate(self._wrap(CREDITS, f, PAGE[0] - 2 * MARGIN - 40)[:3]):
            self.d.text((MARGIN, PAGE[1] - MARGIN - 24 + i * 18), line, fill=(90, 90, 90), font=f)
        self.d.text((PAGE[0] - MARGIN - 40, PAGE[1] - MARGIN + 8), str(n), fill=(90, 90, 90), font=f)


def build_pdf_pages(res: Result, images: render.TileImages, gm=True, pps=14):
    pages = []
    p = _Page()
    p.heading(res.text.get("title") or res.meta.get("name", ""), 54)
    sub = res.meta.get("archetype") or res.meta.get("ship_type", "")
    p.paragraph(f"{sub} — seed {res.meta.get('seed', '')} — {'GM version' if gm else 'Player version'}", 22, (90, 90, 90))
    p.paragraph(res.text.get("description", ""), 26)
    if gm:
        p.heading("Notes", 32)
        for n in res.text.get("notes", []):
            if not p.room(120):
                pages.append(p); p = _Page()
            p.paragraph("• " + n, 22, indent=10, gap=4)
        p.y += 10
        if not p.room(250):
            pages.append(p); p = _Page()
        p.heading("Adventure hooks", 32)
        for h in res.text.get("hooks", []):
            if not p.room(140):
                pages.append(p); p = _Page()
            p.paragraph(f"{h['type']}", 22, (120, 30, 30), gap=0)
            p.paragraph(h["text"], 22, indent=20, gap=10)
    pages.append(p)
    for g in res.grids:
        if res.layout is not None and not g.placed and not any(f["kind"] in ("building", "pad", "pit") for f in g.filler) \
                and g.name == "Surface" and len(res.grids) > 1:
            continue
        im = render.render_level(res, g.index, images, pps=pps, gm=gm)
        pg = _Page()
        scale = min((PAGE[0] - 2 * MARGIN) / im.width, (PAGE[1] - 2 * MARGIN - 80) / im.height)
        im2 = im.convert("RGB").resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
        pg.im.paste(im2, ((PAGE[0] - im2.width) // 2, MARGIN))
        pages.append(pg)
    if len(res.grids) > 1 and res.section.get("levels"):
        sec = render.render_section(res).convert("RGB")
        pg = _Page()
        scale = min(1.6, (PAGE[0] - 2 * MARGIN) / sec.width)
        sec = sec.resize((int(sec.width * scale), int(sec.height * scale)), Image.LANCZOS)
        pg.im.paste(sec, (MARGIN, MARGIN))
        pages.append(pg)
    kp = _Page()
    kp.heading("Key", 38)
    for e in res.key:
        text = f"{e['n']}. {e['title']}" if not gm else f"{e['n']}. {e['title']} — {e.get('text', '')}"
        if not kp.room(90):
            pages.append(kp); kp = _Page()
        kp.paragraph(text, 20, gap=6)
    if gm:
        secrets = [m for m in res.markers if m.get("type") == "secret"]
        threats = [m for m in res.markers if m.get("type") == "threat"]
        if secrets or threats:
            kp.heading("GM only", 30)
            for m in secrets + threats:
                if not kp.room(70):
                    pages.append(kp); kp = _Page()
                kp.paragraph(f"{m['type'].title()}: {m['label']} (level {m['level'] + 1})", 20, (110, 30, 120), gap=4)
    pages.append(kp)
    for i, pg in enumerate(pages, 1):
        pg.footer(i)
    return pages


def export_pdf(res: Result, path, images: render.TileImages, gm=True, pps=14):
    pages = build_pdf_pages(res, images, gm, pps)
    ims = [p.im for p in pages]
    ims[0].save(path, "PDF", save_all=True, append_images=ims[1:], resolution=150.0)
    return str(path), len(ims)


def export_all(res: Result, out_dir, images: render.TileImages, pps=16, gm_and_player=True):
    """PNG per level + PDF package + JSON, for GM and player versions."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = _slug(res.meta.get("name", "map"))
    files = []
    for gm in ((True, False) if gm_and_player else (True,)):
        files += export_png(res, out_dir, images, pps=pps, gm=gm, prefix=prefix)
        path, _n = export_pdf(res, out_dir / f"{prefix}_{'gm' if gm else 'player'}.pdf", images, gm=gm, pps=max(10, pps - 2))
        files.append(path)
        jp = out_dir / f"{prefix}_{'gm' if gm else 'player'}.json"
        save_layout(res, jp, gm=gm)
        files.append(str(jp))
    return files
