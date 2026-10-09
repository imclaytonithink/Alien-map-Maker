"""Symbols pack: room layouts and loose items at their real size.

The pack's sprites are drawn at 60 px per foot (300 px per 5-ft square) with transparent padding; the real size of
a symbol is its opaque bounding box, not the image or the [WxH] in the file name. ``data/symbols_manifest.json``
holds that box, the category (the pack's folder) and a role: ``item`` (<= 2.5 squares: a bed, console, crate) or
``layout`` (a whole furnished room).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from . import DATA_DIR

Image.MAX_IMAGE_PIXELS = None
SYM_PPS = 300                     # pixels per grid square in the pack
SKIP = re.compile(r"checkerboard|iris valves|window|railing|banana|licen", re.I)
ITEM_MAX_SQ = 2.5


@dataclass
class Symbol:
    id: str
    cat: str
    name: str
    rel: str                      # path relative to the pack folder
    bbox: tuple                   # opaque box in pixels (l, t, r, b)
    px: tuple                     # image size in pixels
    role: str = "item"

    @property
    def w(self):                  # real size in grid squares
        return (self.bbox[2] - self.bbox[0]) / SYM_PPS

    @property
    def h(self):
        return (self.bbox[3] - self.bbox[1]) / SYM_PPS

    def to_json(self):
        return [self.id, self.cat, self.name, self.rel, list(self.bbox), list(self.px), self.role]

    @classmethod
    def from_json(cls, d):
        return cls(d[0], d[1], d[2], d[3], tuple(d[4]), tuple(d[5]), d[6])


def scan(pack_dir) -> list:
    pack = Path(pack_dir)
    out = []
    for f in sorted(pack.rglob("*.png")):
        rel = f.relative_to(pack).as_posix()
        if SKIP.search(rel) or "/" not in rel:
            continue
        with Image.open(f) as im:
            a = im.convert("RGBA").getchannel("A").point(lambda v: 255 if v > 8 else 0)
            bb, px = a.getbbox(), im.size
        if not bb:
            continue
        s = Symbol(id=rel, cat=rel.split("/", 1)[0], name=f.stem, rel=rel, bbox=bb, px=px)
        s.role = "item" if max(s.w, s.h) <= ITEM_MAX_SQ else "layout"
        out.append(s)
    return out


def _scan_one(args):
    return scan_one(*args)


def scan_one(pack, rel):
    f = Path(pack) / rel
    with Image.open(f) as im:
        a = im.convert("RGBA").getchannel("A").point(lambda v: 255 if v > 8 else 0)
        return rel, a.getbbox(), im.size


def build(pack_dir, out=None, workers=None):
    from concurrent.futures import ProcessPoolExecutor
    pack = Path(pack_dir)
    rels = [f.relative_to(pack).as_posix() for f in sorted(pack.rglob("*.png"))]
    rels = [r for r in rels if "/" in r and not SKIP.search(r)]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(_scan_one, [(str(pack), r) for r in rels], chunksize=16))
    syms = []
    for rel, bb, px in res:
        if not bb:
            continue
        s = Symbol(rel, rel.split("/", 1)[0], Path(rel).stem, rel, bb, px)
        s.role = "item" if max(s.w, s.h) <= ITEM_MAX_SQ else "layout"
        syms.append(s)
    out = Path(out or DATA_DIR / "symbols_manifest.json")
    out.write_text(json.dumps([s.to_json() for s in syms], separators=(",", ":")), encoding="utf-8")
    return syms


def load(path=None) -> dict:
    p = Path(path or DATA_DIR / "symbols_manifest.json")
    if not p.exists():
        return {}
    return {d[0]: Symbol.from_json(d) for d in json.loads(p.read_text(encoding="utf-8"))}
