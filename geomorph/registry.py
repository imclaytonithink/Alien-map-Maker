"""Tile registry / manifest: id, type, size, weighted function tags, edges.

Tile names in the pack carry the room list in brackets, e.g.
``101 [100x100] Multipurpose (Offices, Fuel, Engineering, ...).png`` — that is
the index the tags are built from (``data/tags.json`` holds the keyword table
so it is editable). Edge/door data is analysed once from the images
(``edges.py``) and stored in the manifest so generation never re-reads images.
User corrections live in ``edge_overrides.json`` and win over detection.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import DATA_DIR

SIDES = ("N", "E", "S", "W")          # clockwise, in the tile's own frame
TILE_TYPES = {"100x100 Core": "standard", "100x50 Edge": "edge",
              "50x50 Corner": "corner", "100x100 End": "end",
              "200x100 Megamorph": "megamorph"}
NAME_RE = re.compile(r"^(?P<num>[A-Za-z]*\d+)\s*\[(?P<w>\d+)x(?P<h>\d+)\]\s*(?P<rest>.*)\.png$",
                     re.IGNORECASE)
PX_PER_SQUARE = 300      # pack renders 300 px per 5 ft square
BORDER_SQUARES = 2       # transparent border around every tile image


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def number_range_type(num: int) -> str:
    """Pearce's numbering ranges (Standard 101-220, Edge 301-460, ...)."""
    if 101 <= num <= 220: return "standard"
    if 301 <= num <= 460: return "edge"
    if 501 <= num <= 620: return "corner"
    if 701 <= num <= 760: return "end"
    return "other"


@dataclass
class Tile:
    id: str                       # unique key, e.g. "101", "106b", "502m"
    number: str
    type: str                     # standard | edge | corner | end | megamorph
    w: int                        # size in squares (unrotated)
    h: int
    title: str
    rooms: list
    tags: dict                    # tag -> weight (0..1]
    image: str                    # path relative to the tiles folder
    mirror_of: str = ""
    overlays: list = field(default_factory=list)
    px: tuple = (0, 0)
    edges: dict = field(default_factory=dict)   # side -> {"cls":[0 wall|1 door|2 void ..],"raw":[..],"conf":f}
    review: bool = False
    bbox: list = field(default_factory=list)    # custom tiles: plan box in squares inside the image [l, t, r, b]
    pair: str = ""                              # wings: id of the matching port/starboard tile

    def origin(self):
        """Squares from the image's top-left corner to the plan's top-left."""
        return tuple(self.bbox[:2]) if self.bbox else (BORDER_SQUARES, BORDER_SQUARES)

    def image_geometry(self, rot=0, mirror=False):
        """Plan origin and image size in squares after mirror + clockwise rotation."""
        S = PX_PER_SQUARE
        W, H = (self.px[0] / S, self.px[1] / S) if self.px[0] else (self.w + 4, self.h + 4)
        l, t, rr, b = self.bbox if self.bbox else (BORDER_SQUARES, BORDER_SQUARES, BORDER_SQUARES + self.w, BORDER_SQUARES + self.h)
        if mirror:
            l, rr = W - rr, W - l
        for _ in range((rot % 360) // 90):
            l, t, rr, b = H - b, l, H - t, rr
            W, H = H, W
        return l, t, W, H

    def to_json(self):
        d = dict(self.__dict__)
        d["px"] = list(self.px)
        return d

    @classmethod
    def from_json(cls, d):
        d = dict(d)
        d["px"] = tuple(d.get("px", (0, 0)))
        return cls(**d)


def tag_table(path=None):
    return load_json(path or DATA_DIR / "tags.json")


def derive_tags(title: str, rooms: list, table: dict) -> dict:
    """Weighted tags from the title and bracketed rooms via the keyword table."""
    tags: dict = {}
    rules = table["keywords"]
    for source, weight in ((" ".join(rooms).lower(), 0.6), (title.lower(), 1.0)):
        for tag, words in rules.items():
            for word in words:
                if re.search(r"\b" + re.escape(word), source):
                    tags[tag] = max(tags.get(tag, 0.0), weight)
                    break
    return {k: round(v, 2) for k, v in tags.items()}


def parse_name(filename: str):
    m = NAME_RE.match(filename)
    if not m:
        return None
    rest = m.group("rest").strip()
    flags = {"overlay": "[overlay]" in rest.lower(), "mirror": "[mirror]" in rest.lower()}
    rest = re.sub(r"\[(overlay|mirror)\]\s*", "", rest, flags=re.IGNORECASE).strip()
    variant = ""
    vm = re.match(r"^\((\d+)\)\s*", rest)
    if vm:
        variant = vm.group(1)
        rest = rest[vm.end():]
    rooms = []
    rm = re.search(r"\(([^()]*)\)\s*$", rest)
    title = rest
    if rm:
        rooms = [r.strip() for r in rm.group(1).split(",") if r.strip()]
        title = rest[:rm.start()].strip(" -")
    return {"number": m.group("num"), "w_ft": int(m.group("w")), "h_ft": int(m.group("h")),
            "title": title or rest, "rooms": rooms, "variant": variant, **flags}


def scan_tiles(tiles_dir, table=None) -> list:
    """Parse filenames only (fast, no images). Edge data is added separately."""
    tiles_dir = Path(tiles_dir)
    table = table or tag_table()
    tiles: dict = {}
    overlays: dict = {}
    for folder, ttype in TILE_TYPES.items():
        d = tiles_dir / folder
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.png")):
            info = parse_name(f.name)
            if not info:
                continue
            rel = f"{folder}/{f.name}"
            if info["overlay"]:
                overlays.setdefault(info["number"], []).append(rel)
                continue
            key = info["number"] + (("-" + info["variant"]) if info["variant"] else "")
            if info["mirror"]:
                key += "m"
            wsq, hsq = info["w_ft"] // 5, info["h_ft"] // 5
            tags = derive_tags(info["title"], info["rooms"], table)
            tile = Tile(id=key, number=info["number"], type=ttype, w=wsq, h=hsq,
                        title=info["title"], rooms=info["rooms"], tags=tags, image=rel,
                        mirror_of=(key[:-1] if info["mirror"] else ""))
            if key in tiles:               # duplicate numbering: keep both
                key = key + "x"
                tile.id = key
            tiles[key] = tile
    for tile in tiles.values():
        tile.overlays = overlays.get(tile.number, [])
    return list(tiles.values())


WING_RE = re.compile(r"^(?P<num>A\d+)\s*\[(?P<w>\d+)[xX](?P<h>\d+)\]\s*(?:\((?P<var>\d+)\)\s*)?(?P<side>Port|Starboard)\s*(?P<rest>.*)\.png$")


def scan_wings(tiles_dir, table=None) -> list:
    """Aerofin wing tiles from the Custom Tiles pack (``Misc/A###`` Port/Starboard files).

    A wing is not a rectangle with a clear border, so its plan box is measured from the
    image. Port and starboard files with the same number and variant are paired.
    """
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    table = table or tag_table()
    d = Path(tiles_dir) / "Misc"
    out = {}
    for f in sorted(d.glob("*.png")) if d.is_dir() else []:
        m = WING_RE.match(f.name)
        if not m or "overlay" in f.name.lower():
            continue
        side = "P" if m["side"] == "Port" else "S"
        wid = f"{m['num']}{('-' + m['var']) if m['var'] else ''}{side}"
        im = Image.open(f)
        px = im.size
        a = im.convert("RGBA").getchannel("A").point(lambda v: 255 if v > 8 else 0)
        bb = a.getbbox()
        if not bb:
            continue
        S = PX_PER_SQUARE
        box = [bb[0] // S, bb[1] // S, -(-bb[2] // S), -(-bb[3] // S)]
        rest = m["rest"].strip(" ()")
        rooms = [x.strip() for x in rest.split(",") if x.strip()]
        w, h = box[2] - box[0], box[3] - box[1]
        edges = {s: {"cls": [0] * (w if s in "NS" else h), "raw": [0.0] * (w if s in "NS" else h), "conf": 1.0}
                 for s in SIDES}
        out[wid] = Tile(id=wid, number=m["num"], type="wing", w=w, h=h, title=f"Wing {m['side']} {rest}".strip(),
                        rooms=rooms, tags=derive_tags(rest, rooms, table), image=f"Misc/{f.name}", px=px,
                        edges=edges, bbox=box)
    for wid, t in out.items():
        other = wid[:-1] + ("S" if wid.endswith("P") else "P")
        if other in out:
            t.pair = other
    return list(out.values())


class Registry:
    """Loaded manifest. ``tiles_dir`` is only needed to draw or re-analyse."""

    def __init__(self, tiles, tiles_dir=None, table=None):
        self.tiles = {t.id: t for t in tiles}
        self.tiles_dir = Path(tiles_dir) if tiles_dir else None
        self.table = table or tag_table()

    # -- persistence ----------------------------------------------------
    def save(self, path):
        data = {"version": 1, "tiles": [t.to_json() for t in self.tiles.values()]}
        Path(path).write_text(json.dumps(data, indent=0, separators=(",", ":")),
                              encoding="utf-8")

    @classmethod
    def load(cls, manifest=None, tiles_dir=None, overrides=None):
        manifest = manifest or DATA_DIR / "tile_manifest.json"
        tiles = [Tile.from_json(d) for d in load_json(manifest)["tiles"]]
        reg = cls(tiles, tiles_dir)
        ov = overrides or DATA_DIR / "edge_overrides.json"
        if Path(ov).exists():
            reg.apply_overrides(load_json(ov))
        extra = DATA_DIR / "custom_tiles.json"      # user-added tiles, later
        if extra.exists():
            for d in load_json(extra).get("tiles", []):
                reg.add(Tile.from_json(d))
        return reg

    def add(self, tile: Tile):
        self.tiles[tile.id] = tile

    def apply_overrides(self, overrides: dict):
        """overrides: {tile_id: {side: [0 wall|1 door|2 void per square]}} — hand fixes."""
        for tid, sides in overrides.items():
            t = self.tiles.get(tid)
            if not t:
                continue
            for side, openings in sides.items():
                e = t.edges.setdefault(side, {})
                e["cls"] = list(openings)
                e["conf"] = 1.0
            t.review = False

    # -- queries --------------------------------------------------------
    def wing_pairs(self, max_w=20, max_h=60):
        """[(port, starboard)] with matching shapes that fit the ship grid."""
        out = []
        for t in self.tiles.values():
            if t.type == "wing" and t.id.endswith("P") and t.pair in self.tiles:
                s = self.tiles[t.pair]
                if (t.w, t.h) == (s.w, s.h) and t.w <= max_w and t.h <= max_h:
                    out.append((t, s))
        return sorted(out, key=lambda p: p[0].id)

    def by_type(self, ttype):
        return [t for t in self.tiles.values() if t.type == ttype]

    def with_tag(self, tag, ttype=None, min_weight=0.5):
        out = [t for t in self.tiles.values()
               if t.tags.get(tag, 0) >= min_weight and (ttype is None or t.type == ttype)]
        return sorted(out, key=lambda t: -t.tags[tag])

    def gaps(self, tags):
        """Tags with no suitable tile at all (reported per archetype)."""
        return [g for g in tags if not self.with_tag(g)]

    def path(self, tile):
        return str(self.tiles_dir / tile.image) if self.tiles_dir else tile.image
