"""Canvas Lights tool: place, move and remove lamps on Geomorph tiles, or light rooms automatically.

* Click on a tile: a wall lamp snaps to the nearest wall (or a ceiling lamp goes where you click).
* Drag a lamp to move it; it snaps again where you let go.
* Right-click a lamp: wall / ceiling, colour, reach, delete. Delete or Backspace removes the lamp under the mouse.
* Auto-light (bar): lamps in the corridors and big open spaces of the selected tiles (or every tile on the
  level): red emergency lamps with the power out, amber alarm beacons in a lockdown, warm working lights otherwise.

Each lit tile has one lighting node over it (geomorph.canvas_lights); the tool edits the lamps kept on that node
and re-draws it, so lamps stop at walls and dark rooms stay dark where no lamp reaches, as in the generator.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QCursor, QPen
from PyQt6.QtWidgets import (QBoxLayout, QButtonGroup, QColorDialog, QComboBox, QFrame, QHBoxLayout, QLabel,
                             QMenu, QToolButton)

from core.project import Piece
from ui.theme import theme_colors

COLORS = (("Working light", "#fff0c8", "#fff7e2"), ("Emergency red", "#ff241c", "#e8261c"),
          ("Alarm amber", "#ffa21c", "#ffbe2e"), ("Cold blue", "#7cc8ff", "#cfeaff"),
          ("Toxic green", "#62f08a", "#b8ffcc"))
GRAB_SQ = 0.7                     # squares from a lamp that still pick it up


def _hint(kind):
    where = "snaps to the nearest wall" if kind == "wall" else "goes exactly where you click"
    return (f"Lights: click a Geomorph tile to add a lamp ({where}; Ctrl = the other kind). Drag a lamp to move it, "
            "right-click it for colour, reach or delete. Esc when done.")


class LightsToolMixin:
    """Mixed into CanvasView. ``light_context`` returns (registry, tile images) or None."""

    def _init_lights_state(self):
        self.lights_tool = False
        self.light_kind = "wall"
        self.light_color = COLORS[0][1:]
        self.light_reach = 3.8
        self.light_context = None
        self._light_hover = None          # {"spot": (wx, wy, wall, kind)} ghost, or {"node", "index"} a lamp
        self._light_drag = None           # {"node", "index", "start", "moved", "ghost"}

    def _reset_lights_state(self) -> bool:
        was = bool(getattr(self, "lights_tool", False))
        self.lights_tool = False
        self._light_hover = None
        self._light_drag = None
        return was

    def set_lights_tool(self, active: bool = True) -> bool:
        self._reset_tool_state()
        if active and self._light_ctx() is None:
            self.statusMessage.emit("Lights need the Geomorph tile data, which could not be loaded.")
            active = False
        self.lights_tool = bool(active)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor if active else Qt.CursorShape.ArrowCursor))
        if active:
            self.setFocus(Qt.FocusReason.OtherFocusReason)
            self.statusMessage.emit(_hint(self.light_kind))
        self.lightsChanged.emit()
        self.update()
        return True

    # -- tiles and their lighting nodes ---------------------------------------------------------------------
    def _light_ctx(self):
        fn = self.light_context
        try:
            return fn() if fn else None
        except Exception:                 # no tile data: the tool says so instead of failing
            return None

    def _light_hosts(self):
        """[(node, placed, x0, y0, cell)] for the Geomorph tiles on this level that have a floor map, top first."""
        from geomorph import atmosphere as A
        from geomorph import canvas_lights as CL
        ctx = self._light_ctx()
        if ctx is None or not self.level:
            return []
        reg = ctx[0]
        out = []
        for node in reversed(self._paint_sequence()):
            if not node.room:                 # only Geomorph tile nodes carry their room
                continue
            layer = self.level.layer_by_id(node.layer) if hasattr(self.level, "layer_by_id") else None
            if layer is not None and not layer.visible:
                continue
            tile = CL.tile_of(node.room, node.name, reg)
            if tile is None:
                continue
            placed = CL.placed_for(tile, node.rotation, node.flip_h, node.flip_v)
            if A._floor(placed) is None:
                continue
            out.append((node, placed) + CL.plan_box(node, placed))
        return out

    def _light_host_at(self, wx, wy):
        for host in self._light_hosts():
            node, placed, x0, y0, cell = host
            if x0 <= wx < x0 + placed.w * cell and y0 <= wy < y0 + placed.h * cell:
                return host
        return None

    def _lighting_node(self, host, create=False):
        """The lighting node over ``host`` (adopting one placed before lamps were editable), or a new one."""
        from geomorph import canvas_export as CE
        from geomorph import canvas_lights as CL
        node, placed, x0, y0, cell = host
        near = lambda p: abs(p.x - x0) < 1.0 and abs(p.y - y0) < 1.0 and abs(p.w * p.scale - placed.w * cell) < 2.0
        for p in self.level.pieces:
            if p.lighting and (p.lighting.get("host") == node.id
                               or not p.lighting.get("host") and p.lighting.get("tile") == placed.tile.id and near(p)):
                p.lighting["host"] = node.id
                p.lighting["rot"], p.lighting["mirror"] = placed.o.rot, placed.o.mirror
                return p
        ctx = self._light_ctx()
        for p in self.level.pieces:       # an older map: its atmosphere picture already holds the automatic lamps
            if not p.lighting and p.embedded and p.name.startswith(CE.LAYER_ATMO) and near(p):
                rec = CL.record(node, placed, node.room.get("states") or [])
                rec["lights"] = CL.automatic(rec, ctx[0], ctx[1]) if rec["states"] else []
                p.lighting = rec
                return p
        if not create:
            return None
        rec = CL.record(node, placed, node.room.get("states") or [])
        p = Piece(name=f"Lighting {node.name}"[:60], snap=False, lighting=rec, w=1.0, h=1.0)
        p.layer = self._light_layer(node)
        self.level.add(p)
        self.insert_above(p, node)
        return p

    def _light_layer(self, host_node) -> str:
        from geomorph import canvas_export as CE
        for layer in self.level.layers:
            if layer.name == CE.LAYER_ATMO:
                return layer.id
        from core.project import Layer
        layer = Layer(name=CE.LAYER_ATMO)
        ids = [l.id for l in self.level.layers]
        at = ids.index(host_node.layer) + 1 if host_node.layer in ids else len(ids)
        self.level.layers.insert(at, layer)
        return layer.id

    def _redraw_lighting(self, node, host) -> bool:
        """Re-draw a lighting node from its record (removed when it shows nothing any more)."""
        from geomorph import canvas_lights as CL
        reg, images = self._light_ctx()
        hnode, placed, x0, y0, cell = host
        im = CL.picture(node.lighting, reg, images)
        if im is None:
            self.level.remove(node)
            self.selection.discard(node.id)
        else:
            node.embedded, node.asset_path = CL.embed(im), ""
            node.w, node.h = float(im.width), float(im.height)
            node.x, node.y, node.scale = x0, y0, cell / CL.PPS
            node.rotation, node.flip_h, node.flip_v = 0.0, False, False
            node.crop_rect = [0.0, 0.0, 1.0, 1.0]
        self._cache.pop("emb:" + node.id, None)
        self._clear_pixmaps()
        return im is not None

    def _light_host_of(self, node):
        for host in self._light_hosts():
            if node.lighting.get("host") == host[0].id:
                return host
        return None

    def _lamp_at(self, wx, wy):
        """(lighting node, index, host) of the lamp under the world point, or None."""
        for host in self._light_hosts():
            node, placed, x0, y0, cell = host
            if not (x0 - cell <= wx < x0 + (placed.w + 1) * cell and y0 - cell <= wy < y0 + (placed.h + 1) * cell):
                continue
            ln = self._lighting_node(host)
            if ln is None:
                continue
            from geomorph import canvas_lights as CL
            i = CL.nearest(ln.lighting, (wx - x0) / cell, (wy - y0) / cell, GRAB_SQ)
            if i is not None:
                return ln, i, host
        return None

    def _snap_spot(self, host, wx, wy, kind):
        from geomorph import canvas_lights as CL
        node, placed, x0, y0, cell = host
        reg, images = self._light_ctx()
        rec = {"tile": placed.tile.id, "rot": placed.o.rot, "mirror": placed.o.mirror}
        spot = CL.snap(rec, reg, (wx - x0) / cell, (wy - y0) / cell, kind, images)
        return spot

    def _press_kind(self, event):
        flip = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        return ("ceiling" if self.light_kind == "wall" else "wall") if flip else self.light_kind

    # -- editing --------------------------------------------------------------------------------------------
    def add_light_at(self, wx, wy, kind=None) -> bool:
        from geomorph import canvas_lights as CL
        kind = kind or self.light_kind
        host = self._light_host_at(wx, wy)
        if host is None:
            self.statusMessage.emit("Lights go on Geomorph tiles: click inside a room or corridor.")
            return False
        spot = self._snap_spot(host, wx, wy, kind)
        if spot is None:
            self.statusMessage.emit("No wall to hang a lamp on here: click nearer a wall, or hold Ctrl for a ceiling lamp."
                                    if kind == "wall" else "That spot is outside the building.")
            return False
        self.push_history("Add light")
        node = self._lighting_node(host, create=True)
        light, fixture = self.light_color
        node.lighting.setdefault("lights", []).append(
            CL.new_light(spot[0], spot[1], spot[2], kind, self.light_reach, light, fixture))
        self._redraw_lighting(node, host)
        self.statusMessage.emit("Lamp added. Drag it to move it, right-click it for options.")
        self.dirty.emit()
        self.update()
        return True

    def remove_light(self, node, index, host) -> None:
        self.push_history("Remove light")
        del node.lighting["lights"][index]
        self._redraw_lighting(node, host)
        self._light_hover = None
        self.dirty.emit()
        self.update()

    def change_light(self, node, index, host, **changes) -> None:
        self.push_history("Change light")
        L = node.lighting["lights"][index]
        L.update(changes)
        L.pop("auto", None)                   # changed by hand: it is the user's lamp now
        if "kind" in changes:
            nx0, ny0, cell = host[2], host[3], host[4]
            spot = self._snap_spot(host, nx0 + L["x"] * cell, ny0 + L["y"] * cell, changes["kind"])
            if spot is not None:
                L["x"], L["y"], L["wall"] = spot[0], spot[1], spot[2] if changes["kind"] == "wall" else ""
        self._redraw_lighting(node, host)
        self.dirty.emit()
        self.update()

    def light_targets(self):
        """Tiles the bar's buttons act on: the selected Geomorph tiles, else every one on this level."""
        hosts = self._light_hosts()
        chosen = self.selection
        picked = [h for h in hosts if h[0].id in chosen or any(
            p.id in chosen and p.lighting.get("host") == h[0].id for p in self.level.pieces)]
        return picked or hosts

    def auto_light(self) -> int:
        """Automatic lamps in the target tiles (hand-placed lamps are kept). Returns the number of lamps placed."""
        from geomorph import canvas_lights as CL
        targets = self.light_targets()
        if not targets:
            self.statusMessage.emit("No Geomorph tiles on this level to light.")
            return 0
        reg, images = self._light_ctx()
        self.push_history("Auto-light")
        n = 0
        for host in targets:
            node = self._lighting_node(host)
            rec = node.lighting if node is not None else CL.record(host[0], host[1], host[0].room.get("states") or [])
            auto = CL.automatic(rec, reg, images)
            if not auto and node is None:
                continue
            if node is None:
                node = self._lighting_node(host, create=True)
            node.lighting["lights"] = [L for L in node.lighting.get("lights", []) if not L.get("auto")] + auto
            n += len(auto)
            self._redraw_lighting(node, host)
        self.statusMessage.emit(f"Auto-light: {n} lamp(s) in {len(targets)} tile(s) — corridors and big open "
                                "spaces only. Drag any of them to move it.")
        self.dirty.emit()
        self.update()
        return n

    def clear_lights(self) -> int:
        targets = self.light_targets()
        nodes = [(self._lighting_node(h), h) for h in targets]
        nodes = [(n, h) for n, h in nodes if n is not None and n.lighting.get("lights")]
        if not nodes:
            return 0
        self.push_history("Remove lights")
        count = 0
        for node, host in nodes:
            count += len(node.lighting["lights"])
            node.lighting["lights"] = []
            self._redraw_lighting(node, host)
        self.statusMessage.emit(f"Removed {count} lamp(s).")
        self.dirty.emit()
        self.update()
        return count

    # -- mouse and keys -------------------------------------------------------------------------------------
    def _lights_press(self, sx, sy, event) -> bool:
        wx, wy = self.screen_to_world(sx, sy)
        if event.button() == Qt.MouseButton.RightButton:
            hit = self._lamp_at(wx, wy)
            if hit is None:
                return False                  # right-drag still pans; a right-click shows the canvas menu
            event.accept()
            self._light_menu(hit, event.globalPosition().toPoint())
            return True
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        event.accept()
        hit = self._lamp_at(wx, wy)
        if hit is not None:
            node, index, host = hit
            self._light_drag = {"node": node, "index": index, "host": host, "start": (wx, wy), "moved": False,
                                "ghost": None}
            return True
        self.add_light_at(wx, wy, self._press_kind(event))
        return True

    def _lights_motion(self, sx, sy, event) -> bool:
        wx, wy = self.screen_to_world(sx, sy)
        drag = self._light_drag
        if drag:
            if abs(wx - drag["start"][0]) * self.zoom + abs(wy - drag["start"][1]) * self.zoom > 3:
                drag["moved"] = True
            if drag["moved"]:
                L = drag["node"].lighting["lights"][drag["index"]]
                kind = L.get("kind", "wall")
                host = self._light_host_at(wx, wy)
                spot = self._snap_spot(host, wx, wy, kind) if host is not None and host[0].id == drag["host"][0].id else None
                if spot is not None:
                    _n, _p, x0, y0, cell = drag["host"]
                    drag["ghost"] = (x0 + spot[0] * cell, y0 + spot[1] * cell, spot[2], kind, spot)
                else:
                    drag["ghost"] = None
            self.update()
            return True
        hit = self._lamp_at(wx, wy)
        if hit is not None:
            self._light_hover = {"node": hit[0], "index": hit[1], "host": hit[2]}
            self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        else:
            kind = self._press_kind(event)
            host = self._light_host_at(wx, wy)
            spot = self._snap_spot(host, wx, wy, kind) if host is not None else None
            if spot is not None:
                _n, _p, x0, y0, cell = host
                self._light_hover = {"spot": (x0 + spot[0] * cell, y0 + spot[1] * cell, spot[2], kind)}
            else:
                self._light_hover = None
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.update()
        return False                          # hovering only: panning and the rest carry on as usual

    def _lights_release(self, sx, sy, event) -> bool:
        drag = self._light_drag
        if not drag:
            return False
        self._light_drag = None
        event.accept()
        if drag["moved"]:
            if drag["ghost"] is None:
                self.statusMessage.emit("A lamp stays on its own tile: drop it on a wall of the same tile.")
            else:
                spot = drag["ghost"][4]
                self.push_history("Move light")
                L = drag["node"].lighting["lights"][drag["index"]]
                L["x"], L["y"] = spot[0], spot[1]
                L["wall"] = spot[2] if L.get("kind", "wall") == "wall" else ""
                L.pop("auto", None)
                self._redraw_lighting(drag["node"], drag["host"])
                self.dirty.emit()
        self.update()
        return True

    def handle_lights_key(self, event) -> bool:
        if not self.lights_tool:
            return False
        if event.key() == Qt.Key.Key_Escape:
            if self._light_drag:
                self._light_drag = None
                self.update()
            else:
                self.set_lights_tool(False)
            return True
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            hover = self._light_hover or {}
            if "node" in hover:
                self.remove_light(hover["node"], hover["index"], hover["host"])
            return True
        return False

    def _light_menu(self, hit, pos):
        node, index, host = hit
        L = node.lighting["lights"][index]
        menu = QMenu(self)
        kind = L.get("kind", "wall")
        act_kind = menu.addAction("Make it a ceiling lamp" if kind == "wall" else "Hang it on the nearest wall")
        colors = menu.addMenu("Colour")
        picks = {}
        for name, light, fixture in COLORS:
            a = colors.addAction(name)
            a.setCheckable(True)
            a.setChecked(L.get("color", "").lower() == light)
            picks[a] = (light, fixture)
        custom = colors.addAction("Custom…")
        reach = menu.addMenu("Reach")
        from geomorph.canvas_lights import REACH
        sizes = {}
        for name, r in REACH.items():
            a = reach.addAction(f"{name} ({r:g} squares)")
            a.setCheckable(True)
            a.setChecked(abs(float(L.get("radius", 3.8)) - r) < 0.05)
            sizes[a] = r
        menu.addSeparator()
        act_del = menu.addAction("Delete lamp")
        chosen = menu.exec(pos)
        if chosen is None:
            return
        if chosen is act_del:
            self.remove_light(node, index, host)
        elif chosen is act_kind:
            self.change_light(node, index, host, kind="ceiling" if kind == "wall" else "wall")
        elif chosen in picks:
            self.change_light(node, index, host, color=picks[chosen][0], fixture=picks[chosen][1])
        elif chosen is custom:
            c = QColorDialog.getColor(QColor(L.get("color") or "#fff0c8"), self, "Lamp colour")
            if c.isValid():
                self.change_light(node, index, host, color=c.name(), fixture=c.lighter(125).name())
        elif chosen in sizes:
            self.change_light(node, index, host, radius=sizes[chosen])

    # -- drawing --------------------------------------------------------------------------------------------
    def _draw_lights_overlay(self, painter):
        if not self.lights_tool or not self.level:
            return
        painter.save()
        accent = QColor(self.theme_accent)
        hover = self._light_hover or {}
        for host in self._light_hosts():                       # a ring round every lamp, so each is easy to find
            node = self._lighting_node(host)
            if node is None:
                continue
            _n, _p, x0, y0, cell = host
            for i, L in enumerate(node.lighting.get("lights") or []):
                sx, sy = self.world_to_screen(x0 + L["x"] * cell, y0 + L["y"] * cell)
                hot = hover.get("node") is node and hover.get("index") == i
                pen = QPen(QColor("#ffffff") if hot else accent)
                pen.setWidthF(2.5 if hot else 1.5)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                r = max(7.0, GRAB_SQ * cell * self.zoom * (0.75 if not hot else 0.9))
                painter.drawEllipse(QPointF(sx, sy), r, r)
        ghost = None
        if self._light_drag and self._light_drag.get("ghost"):
            g = self._light_drag["ghost"]
            ghost = (g[0], g[1], g[2], g[3])
        elif "spot" in hover:
            ghost = hover["spot"]
        if ghost is not None:
            gx, gy, wall, kind = ghost
            sx, sy = self.world_to_screen(gx, gy)
            color = QColor(self.light_color[0] if not self._light_drag
                           else self._light_drag["node"].lighting["lights"][self._light_drag["index"]].get("color", "#fff0c8"))
            glow = QColor(color)
            glow.setAlpha(70)
            cell = (self._light_hosts() or [(None, None, 0, 0, 40.0)])[0][4]
            reach = self.light_reach * cell * self.zoom
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(glow))
            painter.drawEllipse(QPointF(sx, sy), reach * 0.35, reach * 0.35)
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor(0, 0, 0, 160), 1))
            r = max(4.0, 0.3 * cell * self.zoom)
            if kind == "wall" and wall:
                start = {"N": 0, "S": 180, "W": -90, "E": 90}[wall]
                painter.drawPie(int(sx - r), int(sy - r), int(2 * r), int(2 * r), -start * 16, -180 * 16)
            else:
                painter.drawEllipse(QPointF(sx, sy), r, r)
        painter.restore()


class LightsBar(QFrame):
    """Floating bar shown at the top of the canvas while the Lights tool is on."""
    kindChosen = pyqtSignal(str)
    colorChosen = pyqtSignal(str, str)
    reachChosen = pyqtSignal(float)
    actionRequested = pyqtSignal(str)       # auto | clear | done

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LightsBar")
        self.outer = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.outer.setContentsMargins(8, 4, 6, 4)
        self.outer.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(4)
        acts = QHBoxLayout()
        acts.setSpacing(4)
        self.outer.addLayout(row)
        self.outer.addLayout(acts)
        self.title = QLabel("Lights")
        self.title.setObjectName("LightsTitle")
        row.addWidget(self.title)
        self.kind_group = QButtonGroup(self)
        self.kind_buttons = {}
        for kind, label, tip in (("wall", "Wall lamp", "Snaps to the nearest wall: a half-round lamp."),
                                 ("ceiling", "Ceiling lamp", "Goes exactly where you click: a round lamp.")):
            b = QToolButton()
            b.setText(label)
            b.setCheckable(True)
            b.setToolTip(tip + " Hold Ctrl while clicking for the other kind.")
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, k=kind: self.kindChosen.emit(k))
            self.kind_group.addButton(b)
            self.kind_buttons[kind] = b
            row.addWidget(b)
        self.cb_color = QComboBox()
        self.cb_color.setToolTip("Colour of new lamps (right-click a lamp to change its own).")
        self.cb_color.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for name, light, fixture in COLORS:
            self.cb_color.addItem(self._swatch(light), name, (light, fixture))
        self.cb_color.addItem("Custom…", None)
        self.cb_color.activated.connect(self._color_picked)
        row.addWidget(self.cb_color)
        self.cb_reach = QComboBox()
        self.cb_reach.setToolTip("How far new lamps reach (walls still stop the light).")
        self.cb_reach.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        from geomorph.canvas_lights import REACH
        for name, r in REACH.items():
            self.cb_reach.addItem(f"Reach: {name.lower()}", r)
        self.cb_reach.activated.connect(lambda i: self.reachChosen.emit(float(self.cb_reach.itemData(i))))
        row.addWidget(self.cb_reach)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedWidth(8)
        self.separator = sep
        acts.addWidget(sep)
        for name, label, tip in (("auto", "Auto-light", "Lamps in the corridors and big open spaces of the selected "
                                  "tiles (or every tile on this level). Keeps lamps you placed by hand."),
                                 ("clear", "Remove lamps", "Remove every lamp from the selected tiles (or the level).")):
            b = QToolButton()
            b.setText(label)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, n=name: self.actionRequested.emit(n))
            acts.addWidget(b)
        self.btn_done = QToolButton()
        self.btn_done.setText("Done")
        self.btn_done.setToolTip("Put the Lights tool away (Esc).")
        self.btn_done.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_done.clicked.connect(lambda: self.actionRequested.emit("done"))
        acts.addWidget(self.btn_done)
        self.set_theme("dark", theme_colors("dark")["accent"])
        self.hide()

    @staticmethod
    def _swatch(color):
        from PyQt6.QtGui import QIcon, QPixmap
        pm = QPixmap(14, 14)
        pm.fill(QColor(color))
        return QIcon(pm)

    def _color_picked(self, i):
        data = self.cb_color.itemData(i)
        if data is None:
            c = QColorDialog.getColor(QColor("#fff0c8"), self, "Lamp colour")
            if not c.isValid():
                return
            data = (c.name(), c.lighter(125).name())
            self.cb_color.insertItem(self.cb_color.count() - 1, self._swatch(data[0]), c.name(), data)
            self.cb_color.setCurrentIndex(self.cb_color.count() - 2)
        self.colorChosen.emit(*data)

    def fit_width(self, available: int):
        self.outer.setDirection(QBoxLayout.Direction.LeftToRight)
        self.separator.show()
        self.adjustSize()
        if self.sizeHint().width() > available:
            self.outer.setDirection(QBoxLayout.Direction.TopToBottom)
            self.separator.hide()
        self.adjustSize()

    def set_theme(self, mode: str, accent: str):
        colors = theme_colors(mode, accent)
        panel = QColor(colors["panel"])
        self.setStyleSheet(
            "QFrame#LightsBar {"
            f" background: rgba({panel.red()}, {panel.green()}, {panel.blue()}, 232);"
            f" border: 1px solid {colors['border_hot']}; border-radius: 7px; }}"
            f" QLabel#LightsTitle {{ color: {colors['accent']}; font-weight: bold; padding-right: 4px; }}"
            f" QToolButton {{ color: {colors['text']}; padding: 2px 6px; border-radius: 4px;"
            " border: 1px solid transparent; }"
            f" QToolButton:hover {{ background: {colors['hover']}; }}"
            f" QToolButton:checked {{ background: {colors['selection']}; border-color: {colors['border_hot']}; }}"
            f" QFrame[frameShape=\"5\"] {{ color: {colors['border']}; }}")

    def set_state(self, kind: str, color: tuple, reach: float):
        for k, b in self.kind_buttons.items():
            b.setChecked(k == kind)
        for i in range(self.cb_color.count()):
            d = self.cb_color.itemData(i)
            if d is not None and d[0] == color[0]:
                self.cb_color.setCurrentIndex(i)
                break
        for i in range(self.cb_reach.count()):
            if abs(float(self.cb_reach.itemData(i)) - reach) < 0.05:
                self.cb_reach.setCurrentIndex(i)
        self.adjustSize()
