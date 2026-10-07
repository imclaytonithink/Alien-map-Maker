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

from core.cutouts import clean_crop, clean_polygons, visible_at_source


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


def _is_hex_color(value: str) -> bool:
    return bool(re.fullmatch(r"#[0-9a-fA-F]{6}", str(value or "")))


def _clamped_float(value, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(low, min(high, number))


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
    color: str = ""               # optional UI color label (hex) or ""
    # False keeps the layer on the canvas but out of PNG/PDF exports (tracing
    # references, notes, work in progress).
    export: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "Layer":
        """Build a layer from saved data, ignoring unknown keys."""
        layer = cls()
        if not isinstance(data, dict):
            return layer
        layer.id = str(data.get("id") or layer.id)
        layer.name = str(data.get("name", layer.name))
        layer.visible = bool(data.get("visible", True))
        layer.locked = bool(data.get("locked", False))
        layer.opacity = _clamped_float(data.get("opacity", 1.0), 0.0, 1.0, 1.0)
        color = data.get("color", "")
        layer.color = color if _is_hex_color(color) else ""
        layer.export = bool(data.get("export", True))
        return layer


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
    # recolor / tint. ``inherit`` uses the project tint; ``override`` uses
    # this node's tint values; ``original`` opts out of all tinting.
    tint_mode: str = "inherit"
    tint_color: str = ""          # hex or "" for none
    tint_strength: float = 0.0
    # Optional node boundary. Bounds outlines may hide individual sides.
    border_mode: str = "inherit"  # inherit | override | off
    border_shape: str = "inherit" # inherit | bounds | alpha
    border_color: str = "#69b7f5"
    border_opacity: float = 1.0
    border_edges: list[bool] = field(default_factory=lambda: [True] * 4)
    # A separate, editable solid-fill cover over rasterized labels/artwork.
    is_patch: bool = False
    patch_color: str = "#10141c"
    patch_opacity: float = 1.0
    # custom uploaded image embedded in the save
    embedded: str = ""            # base64 image data or ""
    # non-destructive source-image crop, normalized to [left, top, right, bottom]
    crop_rect: list[float] = field(default_factory=lambda: [0.0, 0.0, 1.0, 1.0])
    # independent static scale-bar composition object
    is_scale_bar: bool = False
    scale_distance: float = 5.0
    scale_units: str = "ft"
    scale_caption: str = ""
    scale_color: str = "#ffffff"
    scale_line_width: float = 3.0
    # connection / transition marker composition object
    is_connector: bool = False
    connector_label: str = ""
    connector_color: str = "#ffcc66"
    connector_arrow: bool = True
    connector_width: float = 3.0
    # text element
    is_text: bool = False
    text: str = ""
    font_family: str = "Monospace"
    font_size: int = 24
    font_bold: bool = True
    font_italic: bool = False
    font_underline: bool = False
    text_halign: str = "center"  # left | center | right
    text_valign: str = "center"  # top | center | bottom
    text_padding: int = 4
    text_auto_size: bool = True
    text_background_color: str = ""
    text_background_opacity: float = 0.85
    text_color: str = "#69b7f5"
    # grouping
    group_id: str = ""
    # Non-destructive cut-outs (see core/cutouts.py): polygons in source-image
    # fractions. ``cutouts`` are holes; ``clip_shapes`` keep only their inside.
    cutouts: list = field(default_factory=list)
    clip_shapes: list = field(default_factory=list)
    # Clone patch: a piece of the same picture laid over a baked-in label.
    # ``clone_home`` is the crop that shows exactly what lies under the patch
    # (so a new source can be picked later); [] for ordinary nodes.
    clone_home: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Piece":
        known = {f for f in cls.__dataclass_fields__}
        piece = cls(**{k: v for k, v in d.items() if k in known})
        # Older projects stored only per-node tint values. Preserve their
        # appearance by treating an existing tint as an explicit override.
        if "tint_mode" not in d and piece.tint_color and piece.tint_strength > 0:
            piece.tint_mode = "override"
        if piece.tint_mode not in {"inherit", "override", "original"}:
            piece.tint_mode = "inherit"
        if piece.border_mode not in {"inherit", "override", "off"}:
            piece.border_mode = "inherit"
        if piece.border_shape not in {"inherit", "bounds", "alpha"}:
            piece.border_shape = "inherit"
        piece.border_opacity = max(0.0, min(1.0, float(piece.border_opacity)))
        piece.border_edges = [bool(value) for value in piece.border_edges[:4]]
        piece.border_edges.extend([True] * (4 - len(piece.border_edges)))
        try:
            crop = [max(0.0, min(1.0, float(value)))
                    for value in piece.crop_rect[:4]]
            if len(crop) != 4 or crop[2] - crop[0] < 0.001 or crop[3] - crop[1] < 0.001:
                raise ValueError("invalid crop rectangle")
            piece.crop_rect = crop
        except (TypeError, ValueError):
            piece.crop_rect = [0.0, 0.0, 1.0, 1.0]
        piece.scale_distance = max(0.01, min(1_000_000.0, float(piece.scale_distance)))
        piece.scale_line_width = max(0.5, min(100.0, float(piece.scale_line_width)))
        piece.connector_width = max(0.5, min(100.0, float(piece.connector_width)))
        if piece.scale_units not in {"ft", "m", "km", "mi", "squares", "custom"}:
            piece.scale_units = "ft"
        if not _is_hex_color(piece.scale_color):
            piece.scale_color = "#ffffff"
        if not _is_hex_color(piece.connector_color):
            piece.connector_color = "#ffcc66"
        if piece.text_halign not in {"left", "center", "right"}:
            piece.text_halign = "center"
        if piece.text_valign not in {"top", "center", "bottom"}:
            piece.text_valign = "center"
        piece.text_padding = max(0, min(100, int(piece.text_padding)))
        piece.text_background_opacity = max(
            0.0, min(1.0, float(piece.text_background_opacity)))
        piece.patch_opacity = max(0.0, min(1.0, float(piece.patch_opacity)))
        piece.cutouts = clean_polygons(piece.cutouts)
        piece.clip_shapes = clean_polygons(piece.clip_shapes)
        piece.clone_home = clean_crop(piece.clone_home) or []
        return piece

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
        tolerance = 8.0 / max(self.scale, 1e-6) if self.is_connector else 0.0
        inside = (abs(lx) <= self.w / 2.0 + tolerance
                  and abs(ly) <= self.h / 2.0 + tolerance)
        if inside and (self.cutouts or self.clip_shapes):
            # clicks in a cut-out hole fall through to whatever is below
            fx = lx / max(self.w, 1e-9) + 0.5
            fy = ly / max(self.h, 1e-9) + 0.5
            u0, v0, u1, v1 = self.crop_rect
            return visible_at_source(self, u0 + fx * (u1 - u0), v0 + fy * (v1 - v0))
        return inside


@dataclass
class ZoneRegion:
    """A gameplay boundary that is independent of the PNG nodes underneath."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = "Zone"
    points: list[tuple[float, float]] = field(default_factory=list)
    label: str = ""
    show_label: bool = True
    show_id: bool = False
    border_mode: str = "inherit"  # inherit | override | off
    border_color: str = "#69b7f5"
    border_opacity: float = 1.0
    edge_visible: list[bool] = field(default_factory=list)

    def __post_init__(self):
        self.points = [(float(point[0]), float(point[1]))
                       for point in self.points if len(point) >= 2]
        self.border_opacity = max(0.0, min(1.0, float(self.border_opacity)))
        self.edge_visible = [bool(value) for value in self.edge_visible[:len(self.points)]]
        self.edge_visible.extend([True] * (len(self.points) - len(self.edge_visible)))
        if self.border_mode not in {"inherit", "override", "off"}:
            self.border_mode = "inherit"

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name,
            "points": [[x, y] for x, y in self.points],
            "label": self.label, "show_label": self.show_label,
            "show_id": self.show_id,
            "border_mode": self.border_mode,
            "border_color": self.border_color,
            "border_opacity": self.border_opacity,
            "edge_visible": list(self.edge_visible),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ZoneRegion":
        return cls(
            id=data.get("id", uuid.uuid4().hex),
            name=data.get("name", "Zone"),
            points=data.get("points", []),
            label=str(data.get("label", "")),
            show_label=bool(data.get("show_label", True)),
            show_id=bool(data.get("show_id", False)),
            border_mode=data.get("border_mode", "inherit"),
            border_color=data.get("border_color", "#69b7f5"),
            border_opacity=data.get("border_opacity", 1.0),
            edge_visible=data.get("edge_visible", []),
        )

    def contains(self, x: float, y: float) -> bool:
        """Return true when a point falls inside this polygon."""
        if len(self.points) < 3:
            return False
        inside = False
        j = len(self.points) - 1
        for i, (xi, yi) in enumerate(self.points):
            xj, yj = self.points[j]
            crosses = ((yi > y) != (yj > y)
                       and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi)
            if crosses:
                inside = not inside
            j = i
        return inside


BACKDROP_MODES = ("color", "texture", "none")
GUIDE_AXES = ("v", "h")
DEFAULT_GUIDE_COLOR = "#ff2bd6"   # magenta: unused by the grid, centerlines,
                                  # smart guides, theme accents and teal art


@dataclass
class Guide:
    """A user-placed alignment guide on one level.

    ``axis`` is ``"v"`` for a vertical line at world x = ``pos`` or ``"h"``
    for a horizontal line at world y = ``pos`` (world pixels, like nodes)."""
    axis: str = "v"
    pos: float = 0.0
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict:
        return {"id": self.id, "axis": self.axis, "pos": self.pos}

    @classmethod
    def from_dict(cls, d: dict) -> "Guide":
        axis = d.get("axis") if d.get("axis") in GUIDE_AXES else "v"
        try:
            pos = float(d.get("pos", 0.0))
        except (TypeError, ValueError):
            pos = 0.0
        if not math.isfinite(pos):
            pos = 0.0
        return cls(axis=axis, pos=pos, id=str(d.get("id") or uuid.uuid4().hex))


@dataclass
class Level:
    name: str = "Level 1"
    pieces: list[Piece] = field(default_factory=list)
    zones: list[ZoneRegion] = field(default_factory=list)
    layers: list[Layer] = field(default_factory=list)
    background: str = "#10141c"
    current_layer: str = ""
    guides: list[Guide] = field(default_factory=list)
    # What lies under everything on this level: "color" (``background``),
    # "texture" (a library image tiled over the background color) or "none"
    # (transparent: the canvas shows a checkerboard, PNG exports keep alpha).
    backdrop: str = "color"
    backdrop_texture: str = ""        # store-relative library image
    backdrop_tile: float = 0.0        # squares per texture tile; 0 = natural size
    backdrop_opacity: float = 1.0     # texture strength over the background color

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
        layers = []
        for layer in self.layers:
            data = asdict(layer)
            # Older SceneBoard builds build layers with Layer(**data) and would
            # refuse an unknown "export" key, so the flag is saved as a
            # level-wide list that they simply ignore.
            data.pop("export", None)
            layers.append(data)
        return {"name": self.name, "background": self.background,
                "backdrop": self.backdrop,
                "backdrop_texture": self.backdrop_texture,
                "backdrop_tile": self.backdrop_tile,
                "backdrop_opacity": self.backdrop_opacity,
                "current_layer": self.current_layer,
                "layers": layers,
                "layers_not_exported": [l.id for l in self.layers
                                        if not getattr(l, "export", True)],
                "pieces": [p.to_dict() for p in self.pieces],
                "zones": [zone.to_dict() for zone in self.zones],
                "guides": [guide.to_dict() for guide in self.guides]}

    @classmethod
    def from_dict(cls, d: dict) -> "Level":
        lv = cls(name=d.get("name", "Level"),
                 background=d.get("background", "#10141c"),
                 current_layer=d.get("current_layer", ""),
                 layers=[Layer.from_dict(l) for l in d.get("layers", [])],
                 pieces=[Piece.from_dict(p) for p in d.get("pieces", [])],
                 zones=[ZoneRegion.from_dict(zone) for zone in d.get("zones", [])],
                 guides=[Guide.from_dict(g) for g in d.get("guides", [])
                         if isinstance(g, dict)])
        if not lv.layers:
            lv.__post_init__()
        hidden_from_export = d.get("layers_not_exported", [])
        if isinstance(hidden_from_export, list):
            for layer in lv.layers:
                if layer.id in hidden_from_export:
                    layer.export = False
        if not lv.current_layer and lv.layers:
            lv.current_layer = lv.layers[0].id
        if not isinstance(lv.background, str) or not lv.background.strip():
            lv.background = "#10141c"
        mode = d.get("backdrop", "color")
        lv.backdrop = mode if mode in BACKDROP_MODES else "color"
        texture = d.get("backdrop_texture", "")
        lv.backdrop_texture = texture if isinstance(texture, str) else ""
        lv.backdrop_tile = _clamped_float(d.get("backdrop_tile", 0.0), 0.0, 1000.0, 0.0)
        lv.backdrop_opacity = _clamped_float(d.get("backdrop_opacity", 1.0), 0.0, 1.0, 1.0)
        return lv

    def backdrop_texture_in_use(self) -> str:
        """The library image the backdrop tiles, or "" when none is used."""
        return self.backdrop_texture if self.backdrop == "texture" else ""

    # ---- guides ----
    def guide_positions(self, axis: str) -> list[float]:
        return [g.pos for g in self.guides if g.axis == axis]

    def find_guide(self, guide_id: str) -> Optional[Guide]:
        return next((g for g in self.guides if g.id == guide_id), None)

    def has_guide(self, axis: str, pos: float, tolerance: float = 1e-6) -> bool:
        return any(g.axis == axis and abs(g.pos - pos) <= tolerance
                   for g in self.guides)

    def add_guide(self, axis: str, pos: float) -> Optional[Guide]:
        """Add a guide unless an identical one exists; returns the new guide."""
        if axis not in GUIDE_AXES or not math.isfinite(pos) or self.has_guide(axis, pos):
            return None
        guide = Guide(axis=axis, pos=float(pos))
        self.guides.append(guide)
        return guide

    def remove_guide(self, guide_id: str) -> bool:
        guide = self.find_guide(guide_id)
        if guide is None:
            return False
        self.guides.remove(guide)
        return True

    def next_z(self) -> int:
        return (max((p.z for p in self.pieces), default=0)) + 1

    def paint_order(self, bounds: Optional[tuple[float, float, float, float]] = None
                    ) -> list[Piece]:
        """Visible pieces in draw order. ``bounds`` (x0, y0, x1, y1 in world
        px) culls pieces that cannot touch that rectangle."""
        idx = {l.id: i for i, l in enumerate(self.layers)}
        hidden = {l.id for l in self.layers if not l.visible}
        out = [p for p in self.pieces if p.layer not in hidden]
        if bounds is not None:
            x0, y0, x1, y1 = bounds
            kept = []
            for p in out:
                cx, cy = p.center
                r = math.hypot(p.w, p.h) * p.scale / 2.0 + 12.0
                if cx + r >= x0 and cx - r <= x1 and cy + r >= y0 and cy - r <= y1:
                    kept.append(p)
            out = kept
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
    grid_on_top: bool = True        # draw the grid over nodes (as exports do)
    show_centerlines: bool = True   # lines through the middle of the canvas (alignment)
    grid_opacity: float = 0.5
    export_grid: bool = True
    export_grid_color: str = "#2e6fdf"
    export_grid_opacity: float = 0.5
    grid_major: int = 5
    grid_style: str = "solid"     # solid | dashed | dotted
    # Project-wide tint inherited by image nodes unless overridden or disabled.
    tint_color: str = ""
    tint_strength: float = 0.0
    # Shared node/zone border defaults. Overrides live on individual objects.
    border_color: str = "#69b7f5"
    border_opacity: float = 0.85
    border_width: float = 2.0
    node_border_shape: str = "bounds"  # bounds | alpha
    show_node_borders: bool = False
    export_node_borders: bool = False
    show_zones: bool = True
    export_zones: bool = True
    # Placed guides live on each level; these settings apply to all of them.
    show_guides: bool = True
    snap_to_guides: bool = True
    lock_guides: bool = False
    guide_color: str = DEFAULT_GUIDE_COLOR
    guide_opacity: float = 0.9
    show_coordinates: bool = True     # grid coordinates (A1, B2…) on the rails
    # Editor aids stay out of exports unless explicitly included.
    export_centerlines: bool = False
    export_guides: bool = False
    export_coordinates: bool = False  # labeled border, one square wide
    canvas_w: int = 2100
    canvas_h: int = 2100
    levels: list[Level] = field(default_factory=list)
    collections: dict[str, list[str]] = field(default_factory=dict)
    group_order: list[str] = field(default_factory=list)
    thumb_size: int = 56
    # Legacy per-project UI preferences (the active application theme is global).
    accent: str = "#69b7f5"
    text_scale: float = 1.0
    flourish_scanlines: bool = False
    flourish_boot: bool = False
    flourish_cursor: bool = False
    # user settings
    autosave_min: int = 0         # 0 = off
    reopen_last: bool = False
    last_project: str = ""
    # map size in squares
    map_cols: int = 30
    map_rows: int = 30
    # Runtime only (never saved): other asset folders searched when an image
    # is not in ``asset_store`` — normally this app's own asset library.
    fallback_asset_stores: list[str] = field(default_factory=list, repr=False)

    def __post_init__(self):
        if not self.levels:
            self.levels.append(Level(name="Level 1"))
        self._sync_canvas()

    def _sync_canvas(self):
        self.canvas_w = max(100, self.map_cols * self.cell_size)
        self.canvas_h = max(100, self.map_rows * self.cell_size)

    def to_dict(self) -> dict:
        return {
            "version": 9, "name": self.name, "asset_store": self.asset_store,
            "cell_size": self.cell_size, "feet_per_square": self.feet_per_square,
            "tint_color": self.tint_color, "tint_strength": self.tint_strength,
            "border_color": self.border_color, "border_opacity": self.border_opacity,
            "border_width": self.border_width, "node_border_shape": self.node_border_shape,
            "show_node_borders": self.show_node_borders,
            "export_node_borders": self.export_node_borders,
            "show_zones": self.show_zones, "export_zones": self.export_zones,
            "show_guides": self.show_guides, "snap_to_guides": self.snap_to_guides,
            "lock_guides": self.lock_guides, "guide_color": self.guide_color,
            "guide_opacity": self.guide_opacity,
            "show_coordinates": self.show_coordinates,
            "export_centerlines": self.export_centerlines,
            "export_guides": self.export_guides,
            "export_coordinates": self.export_coordinates,
            "grid_color": self.grid_color, "show_grid": self.show_grid,
            "show_centerlines": self.show_centerlines,
            "grid_on_top": self.grid_on_top,
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
            tint_color=d.get("tint_color", ""),
            tint_strength=max(0.0, min(1.0, float(d.get("tint_strength", 0.0)))),
            border_color=d.get("border_color", "#69b7f5"),
            border_opacity=max(0.0, min(1.0, float(d.get("border_opacity", 0.85)))),
            border_width=max(0.5, min(20.0, float(d.get("border_width", 2.0)))),
            node_border_shape=(d.get("node_border_shape", "bounds")
                               if d.get("node_border_shape", "bounds") in {"bounds", "alpha"}
                               else "bounds"),
            show_node_borders=bool(d.get("show_node_borders", False)),
            export_node_borders=bool(d.get("export_node_borders", False)),
            show_zones=bool(d.get("show_zones", True)),
            export_zones=bool(d.get("export_zones", True)),
            show_guides=bool(d.get("show_guides", True)),
            snap_to_guides=bool(d.get("snap_to_guides", True)),
            lock_guides=bool(d.get("lock_guides", False)),
            guide_color=(d.get("guide_color") if _is_hex_color(d.get("guide_color"))
                         else DEFAULT_GUIDE_COLOR),
            guide_opacity=_clamped_float(d.get("guide_opacity", 0.9), 0.1, 1.0, 0.9),
            show_coordinates=bool(d.get("show_coordinates", True)),
            export_centerlines=bool(d.get("export_centerlines", False)),
            export_guides=bool(d.get("export_guides", False)),
            export_coordinates=bool(d.get("export_coordinates", False)),
            grid_color=d.get("grid_color", "#2e6fdf"),
            show_grid=d.get("show_grid", True),
            show_centerlines=d.get("show_centerlines", True),
            grid_on_top=d.get("grid_on_top", True),
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
            accent=d.get("accent", "#69b7f5"),
            text_scale=d.get("text_scale", 1.0),
            flourish_scanlines=d.get("flourish_scanlines", False),
            flourish_boot=d.get("flourish_boot", False),
            flourish_cursor=d.get("flourish_cursor", False),
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
        if path:
            for store in [self.asset_store, *self.fallback_asset_stores]:
                if not store:
                    continue
                cand = os.path.join(store, path)
                if os.path.exists(cand):
                    return os.path.abspath(cand)
        return os.path.abspath(path)

    def referenced_assets(self, levels=None) -> dict[str, int]:
        """Library images used by image nodes and level backdrops: {store path:
        use count}. Embedded custom images travel inside the map and are not
        listed."""
        counts: dict[str, int] = {}
        for level in (self.levels if levels is None else levels):
            if level is None:
                continue
            texture = level.backdrop_texture_in_use()
            if texture:
                counts[texture] = counts.get(texture, 0) + 1
            for piece in level.pieces:
                if (piece.asset_path and not piece.embedded and not piece.is_text
                        and not piece.is_patch and not piece.is_scale_bar
                        and not piece.is_connector):
                    counts[piece.asset_path] = counts.get(piece.asset_path, 0) + 1
        return counts

    def missing_assets(self, levels=None) -> dict[str, int]:
        """Referenced images that cannot be found: {store path: node count}."""
        return {path: count for path, count in self.referenced_assets(levels).items()
                if not os.path.isfile(self.resolve_asset(path))}

    def relink_assets(self, mapping: dict[str, str]) -> int:
        """Point nodes at new store paths ({old: new}); returns nodes changed."""
        changed = 0
        for level in self.levels:
            texture = mapping.get(level.backdrop_texture) if level.backdrop_texture else None
            if texture and texture != level.backdrop_texture:
                level.backdrop_texture = texture
                changed += 1
            for piece in level.pieces:
                new_path = mapping.get(piece.asset_path) if not piece.embedded else None
                if new_path and new_path != piece.asset_path:
                    piece.asset_path = new_path
                    changed += 1
        return changed


def choose_asset_store(saved: str, default: str, referenced) -> str:
    """Pick the asset folder a just-opened map should use.

    Maps record the folder they were made with. Keep it when it exists and
    holds at least as many of the map's images as this app's own library
    (``default``); otherwise — another PC, another Windows account, a moved
    folder — use the app's library, where the same packs normally live.
    """
    if not saved:
        return default
    if not default or os.path.normcase(os.path.abspath(saved)) == \
            os.path.normcase(os.path.abspath(default)):
        return saved
    if not os.path.isdir(saved):
        return default
    paths = list(referenced)
    if not paths:
        return saved

    def found(store):
        return sum(1 for rel in paths if os.path.isfile(os.path.join(store, rel)))

    return saved if found(saved) >= found(default) else default


def new_project() -> Project:
    return Project(name="Untitled Map")
