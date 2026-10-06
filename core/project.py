"""Core data model for the Map Builder (no Qt imports)."""
from __future__ import annotations

import base64
import io
import math
import os
import re
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------
def snap_value(value: float, step: float) -> float:
    if step <= 0:
        return value
    return round(value / step) * step


SIZE_RE = re.compile(r"(\d+)\s*[xX]\s*(\d+)")


def parse_size_from_name(name: str) -> Optional[tuple[int, int]]:
    m = SIZE_RE.search(name)
    return (int(m.group(1)), int(m.group(2))) if m else None


def embed_png(path: str) -> str:
    """Read a PNG file and return base64 text (for custom uploads)."""
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


def decode_embed(b64: str) -> bytes:
    return base64.b64decode(b64)


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------
@dataclass
class Layer:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = "Layer"
    visible: bool = True
    locked: bool = False
    opacity: float = 1.0


@dataclass
class Piece:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    asset_path: str = ""          # store-relative path (app assets) OR "" if embedded
    name: str = ""
    # top-left in world px
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    rotation: float = 0.0
    scale: float = 1.0
    opacity: float = 1.0
    z: int = 0
    snap: bool = True
    flip_h: bool = False
    flip_v: bool = False
    is_overlay: bool = False
    locked: bool = False
    layer: str = ""               # Layer id
    # recolor / tint
    tint_color: str = ""          # hex or "" for none
    tint_strength: float = 0.0
    # custom uploaded image embedded in the save
    embedded: str = ""            # base64 PNG or ""
    # text element
    is_text: bool = False
    text: str = ""
    font_family: str = "Monospace"
    font_size: int = 24
    font_bold: bool = True
    text_color: str = "#9bff9b"
    # grouping
    group_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Piece":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def center(self) -> tuple[float, float]:
        # x/y is the visual top-left; the drawn/hit area is w*scale x h*scale.
        return (self.x + self.w * self.scale / 2.0,
                self.y + self.h * self.scale / 2.0)

    @property
    def vis_w(self) -> float:
        """Visual (scaled) width in world px."""
        return self.w * self.scale

    @property
    def vis_h(self) -> float:
        """Visual (scaled) height in world px."""
        return self.h * self.scale

    def hit_test(self, wx: float, wy: float) -> bool:
        cx, cy = self.center
        dx = wx - cx
        dy = wy - cy
        ang = -math.radians(self.rotation)
        lx = dx * math.cos(ang) - dy * math.sin(ang)
        ly = dx * math.sin(ang) + dy * math.cos(ang)
        if self.flip_h:
            lx = -lx
        if self.flip_v:
            ly = -ly
        lx /= max(self.scale, 1e-6)
        ly /= max(self.scale, 1e-6)
        return abs(lx) <= self.w / 2.0 and abs(ly) <= self.h / 2.0


@dataclass
class Level:
    name: str = "Level 1"
    pieces: list[Piece] = field(default_factory=list)
    layers: list[Layer] = field(default_factory=list)
    background: str = "#10141c"
    current_layer: str = ""

    def __post_init__(self):
        if not self.layers:
            for nm in ("Floor", "Walls", "Props", "Overlay"):
                self.layers.append(Layer(name=nm))
        if not self.current_layer and self.layers:
            self.current_layer = self.layers[0].id

    def layer_by_id(self, lid: str) -> Optional[Layer]:
        for l in self.layers:
            if l.id == lid:
                return l
        return None

    def to_dict(self) -> dict:
        return {"name": self.name, "background": self.background,
                "current_layer": self.current_layer,
                "layers": [asdict(l) for l in self.layers],
                "pieces": [p.to_dict() for p in self.pieces]}

    @classmethod
    def from_dict(cls, d: dict) -> "Level":
        lv = cls(name=d.get("name", "Level"),
                 background=d.get("background", "#10141c"),
                 current_layer=d.get("current_layer", ""),
                 layers=[Layer(**l) for l in d.get("layers", [])],
                 pieces=[Piece.from_dict(p) for p in d.get("pieces", [])])
        if not lv.layers:
            lv.__post_init__()
        if not lv.current_layer and lv.layers:
            lv.current_layer = lv.layers[0].id
        return lv

    def next_z(self) -> int:
        return (max((p.z for p in self.pieces), default=0)) + 1

    def paint_order(self) -> list[Piece]:
        idx = {l.id: i for i, l in enumerate(self.layers)}
        out = [p for p in self.pieces
               if (l := self.layer_by_id(p.layer)) is None or l.visible]
        out.sort(key=lambda p: (idx.get(p.layer, 999), p.z))
        return out

    def selectable_at(self, wx: float, wy: float) -> Optional[Piece]:
        for p in reversed(self.paint_order()):
            l = self.layer_by_id(p.layer)
            if l and l.locked:
                continue
            if p.locked:
                continue
            if p.hit_test(wx, wy):
                return p
        return None

    def add(self, piece: Piece) -> None:
        if not piece.layer:
            piece.layer = self.current_layer
        piece.z = self.next_z()
        self.pieces.append(piece)

    def remove(self, piece: Piece) -> None:
        if piece in self.pieces:
            self.pieces.remove(piece)


@dataclass
class Project:
    name: str = "Untitled Map"
    asset_store: str = ""         # absolute path to internal asset library
    cell_size: int = 70           # px per square
    feet_per_square: int = 5
    grid_color: str = "#2e6fdf"
    show_grid: bool = True
    grid_opacity: float = 0.5
    export_grid: bool = True
    export_grid_color: str = "#2e6fdf"
    export_grid_opacity: float = 0.5
    grid_major: int = 5
    grid_style: str = "solid"     # solid | dashed | dotted
    canvas_w: int = 2100
    canvas_h: int = 2100
    levels: list[Level] = field(default_factory=list)
    collections: dict[str, list[str]] = field(default_factory=dict)
    group_order: list[str] = field(default_factory=list)
    thumb_size: int = 56
    # theme
    accent: str = "#9bff9b"
    text_scale: float = 1.0
    flourish_scanlines: bool = True
    flourish_boot: bool = True
    flourish_cursor: bool = True
    # user settings
    autosave_min: int = 0         # 0 = off
    reopen_last: bool = False
    last_project: str = ""
    # map size in squares
    map_cols: int = 30
    map_rows: int = 30

    def __post_init__(self):
        if not self.levels:
            self.levels.append(Level(name="Level 1"))
        self._sync_canvas()

    def _sync_canvas(self):
        self.canvas_w = max(100, self.map_cols * self.cell_size)
        self.canvas_h = max(100, self.map_rows * self.cell_size)

    def to_dict(self) -> dict:
        return {
            "version": 2, "name": self.name, "asset_store": self.asset_store,
            "cell_size": self.cell_size, "feet_per_square": self.feet_per_square,
            "grid_color": self.grid_color, "show_grid": self.show_grid,
            "grid_opacity": self.grid_opacity, "export_grid": self.export_grid,
            "export_grid_color": self.export_grid_color,
            "export_grid_opacity": self.export_grid_opacity,
            "grid_major": self.grid_major, "grid_style": self.grid_style,
            "canvas_w": self.canvas_w, "canvas_h": self.canvas_h,
            "map_cols": self.map_cols, "map_rows": self.map_rows,
            "collections": self.collections,
            "group_order": self.group_order,
            "thumb_size": self.thumb_size,
            "accent": self.accent, "text_scale": self.text_scale,
            "flourish_scanlines": self.flourish_scanlines,
            "flourish_boot": self.flourish_boot,
            "flourish_cursor": self.flourish_cursor,
            "autosave_min": self.autosave_min, "reopen_last": self.reopen_last,
            "last_project": self.last_project,
            "levels": [lv.to_dict() for lv in self.levels],
        }

    def restore_from(self, other: "Project"):
        for k, v in other.to_dict().items():
            if k == "levels":
                self.levels = [Level.from_dict(d) for d in v]
            else:
                setattr(self, k, v)
        self._sync_canvas()

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        proj = cls(
            name=d.get("name", "Untitled Map"),
            asset_store=d.get("asset_store", ""),
            cell_size=d.get("cell_size", 70),
            feet_per_square=d.get("feet_per_square", 5),
            grid_color=d.get("grid_color", "#2e6fdf"),
            show_grid=d.get("show_grid", True),
            grid_opacity=d.get("grid_opacity", 0.5),
            export_grid=d.get("export_grid", True),
            export_grid_color=d.get("export_grid_color", "#2e6fdf"),
            export_grid_opacity=d.get("export_grid_opacity", 0.5),
            grid_major=d.get("grid_major", 5),
            grid_style=d.get("grid_style", "solid"),
            map_cols=d.get("map_cols", 30),
            map_rows=d.get("map_rows", 30),
            collections=d.get("collections", {}),
            group_order=d.get("group_order", []),
            thumb_size=d.get("thumb_size", 56),
            accent=d.get("accent", "#9bff9b"),
            text_scale=d.get("text_scale", 1.0),
            flourish_scanlines=d.get("flourish_scanlines", True),
            flourish_boot=d.get("flourish_boot", True),
            flourish_cursor=d.get("flourish_cursor", True),
            autosave_min=d.get("autosave_min", 0),
            reopen_last=d.get("reopen_last", False),
            last_project=d.get("last_project", ""),
        )
        proj.levels = [Level.from_dict(lv) for lv in d.get("levels", [])]
        if not proj.levels:
            proj.levels.append(Level(name="Level 1"))
        proj._sync_canvas()
        return proj

    # ---- helpers ----
    def add_level(self, name: str | None = None) -> Level:
        idx = len(self.levels) + 1
        lv = Level(name=name or f"Level {idx}")
        self.levels.append(lv)
        return lv

    def move_level(self, index: int, delta: int) -> None:
        j = index + delta
        if 0 <= j < len(self.levels):
            self.levels[index], self.levels[j] = self.levels[j], self.levels[index]

    def remove_level(self, index: int) -> None:
        if len(self.levels) > 1:
            self.levels.pop(index)

    def resolve_asset(self, path: str) -> str:
        if path and os.path.isabs(path) and os.path.exists(path):
            return path
        if self.asset_store and path:
            cand = os.path.join(self.asset_store, path)
            if os.path.exists(cand):
                return os.path.abspath(cand)
        return os.path.abspath(path)


def new_project() -> Project:
    return Project(name="Untitled Map")
