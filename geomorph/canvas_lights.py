"""Lights on the canvas: the room lighting of Geomorph tiles, kept editable after the map is placed.

Every lit tile has one *lighting* node on the atmosphere layer, right over the tile. The node remembers what it
shows (``Piece.lighting``): the tile, how it is turned, the room's state and its lamps. Moving, adding or deleting a
lamp re-draws the node with the generator's own lighting (:func:`atmosphere.room_overlay`), so a lamp on the canvas
looks exactly like one in the generator preview: it stops at walls, pools softly, and a dark room stays dark
everywhere its lamps cannot reach.

Lamp positions are kept in squares inside the tile (its turned plan box), so the node can always be re-drawn.
Pure Python + PIL: the canvas tool (ui/canvas_tools.py) does the clicking.
"""
from __future__ import annotations

import base64
import io

from . import atmosphere as A

PPS = 30                                 # pixels per square of a lighting node's picture
KINDS = ("wall", "ceiling")
REACH = {"Small": 2.5, "Medium": 3.8, "Large": 5.5}     # squares a lamp reaches


def tile_of(room: dict, name: str, registry):
    """The registry tile a canvas tile node shows: by the id kept on it, else by its name ("101 Bridge")."""
    tid = (room or {}).get("tile")
    if tid:
        try:
            return registry.lookup(tid)
        except KeyError:
            pass
    name = (name or "").strip()
    if not name:
        return None
    for t in registry.tiles.values():
        if f"{t.number} {t.title}".strip() == name:
            return t
    return None


def placed_for(tile, rotation: float, flip_h: bool, flip_v: bool):
    """A placed tile at (0, 0), turned like the canvas node (rotation snapped to quarter turns)."""
    from .placement import Placed, orientation_for
    rot = int(round(float(rotation) / 90.0)) * 90 % 360
    mirror = bool(flip_h)
    if flip_v:                           # a vertical flip is a mirror turned half way round
        mirror, rot = not mirror, (rot + 180) % 360
    return Placed(tile=tile, x=0, y=0, o=orientation_for(tile, rot, mirror))


def plan_box(node, placed) -> tuple:
    """(x0, y0, cell): world px of the top-left of the tile's plan box, and px per square, for a canvas tile node
    (anything with x, y, w, h, scale, rotation and room)."""
    from .registry import BORDER_SQUARES, PX_PER_SQUARE
    t = placed.tile
    if t.bbox:
        cell = float(node.scale) * PX_PER_SQUARE
    else:
        cell = float(node.w) * float(node.scale) / (t.w + 2 * BORDER_SQUARES)
    cx = float(node.x) + float(node.w) * float(node.scale) / 2.0
    cy = float(node.y) + float(node.h) * float(node.scale) / 2.0
    room = getattr(node, "room", None) or {}
    ox, oy = float(room.get("ox", 0.0)), float(room.get("oy", 0.0))
    if ox or oy:                         # a wing's plan box sits off-centre in its picture: turn the offset along
        import math
        turn = math.radians(float(node.rotation) - float(room.get("rot", node.rotation)))
        ox, oy = ox * math.cos(turn) - oy * math.sin(turn), ox * math.sin(turn) + oy * math.cos(turn)
    return cx + ox - placed.w * cell / 2.0, cy + oy - placed.h * cell / 2.0, cell


def record(node, placed, states=(), colors=None, lights=()) -> dict:
    """The lighting record kept on a lighting node."""
    return {"host": getattr(node, "id", ""), "tile": placed.tile.id, "rot": placed.o.rot, "mirror": placed.o.mirror,
            "states": [s for s in states if s in ("power_failure", "lockdown", "quarantine")],
            "colors": list(colors or (A.DEFAULT_LIGHT, A.DEFAULT_FIXTURE)), "lights": [dict(L) for L in lights]}


def _placed(lighting, registry):
    from .placement import Placed, orientation_for
    tile = registry.lookup(lighting["tile"])
    return Placed(tile=tile, x=0, y=0, o=orientation_for(tile, int(lighting.get("rot", 0)), bool(lighting.get("mirror"))))


def _room(lighting, placed):
    return {"tile": placed, "states": list(lighting.get("states") or []), "colors": tuple(lighting.get("colors") or
            (A.DEFAULT_LIGHT, A.DEFAULT_FIXTURE)), "lights": list(lighting.get("lights") or []), "auto": False}


def _art(placed, images):
    from . import render
    return render.oriented_thumb(images, placed) if images is not None else None


def picture(lighting, registry, images=None):
    """The lighting node's picture (RGBA, the tile's plan box at :data:`PPS`), or None when it shows nothing."""
    p = _placed(lighting, registry)
    im = A.room_overlay(_room(lighting, p), PPS, "public", _art(p, images))
    return im if im is not None and im.getbbox() is not None else None


def embed(im) -> str:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def snap(lighting, registry, x, y, kind="wall", images=None):
    """(x, y, wall) for a lamp clicked at (x, y) squares inside the tile, or None (see atmosphere.snap_in_tile)."""
    p = _placed(lighting, registry)
    return A.snap_in_tile(p, x, y, kind, _art(p, images) if kind == "wall" else None)


def new_light(x, y, wall, kind="wall", reach=A.LIGHT_RADIUS, color=None, fixture=None, strength=1.0) -> dict:
    return {"x": x, "y": y, "kind": kind, "wall": wall if kind == "wall" else "", "radius": float(reach),
            "strength": float(strength), "color": color or A.WORK_LIGHT, "fixture": fixture or A.WORK_FIXTURE}


def automatic(lighting, registry, images=None) -> list:
    """The tile's automatic lamps for its state: red emergency lamps with the power out, amber alarm beacons in a
    lockdown, warm working lights otherwise; only corridors and big open spaces get them."""
    p = _placed(lighting, registry)
    return A.auto_lights(_room(lighting, p), None, _art(p, images), working=True)


def nearest(lighting, x, y, within=0.8):
    """Index of the lamp nearest (x, y) squares, if one is within ``within`` squares, else None."""
    best = None
    for i, L in enumerate(lighting.get("lights") or []):
        d = ((L["x"] - x) ** 2 + (L["y"] - y) ** 2) ** 0.5
        if d <= within and (best is None or d < best[0]):
            best = (d, i)
    return None if best is None else best[1]


def piece_dict(lighting, registry, node, placed, layer, images=None) -> dict | None:
    """A canvas piece dict for the lighting node over tile node ``node`` (None when nothing is lit)."""
    im = picture(lighting, registry, images)
    if im is None:
        return None
    x0, y0, cell = plan_box(node, placed)
    return {"asset_path": "", "embedded": embed(im), "name": f"Lighting {getattr(node, 'name', '')}".strip()[:60],
            "x": x0, "y": y0, "w": im.width, "h": im.height, "scale": cell / PPS, "rotation": 0, "flip_h": False,
            "flip_v": False, "layer_name": layer, "snap": False, "opacity": 1.0, "lighting": lighting}
