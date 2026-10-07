"""Canvas editing tools added on top of CanvasView (see ui/canvas.py).

* Selection basics: select all / invert / by layer, bring to front / send to
  back, cut, paste at a point, and sending nodes to another level.
* Swapping a node's picture in place (library image, or the next/previous
  image in its library folder).
* Cut-out tool: select part of image node(s) with a rectangle, ellipse, lasso
  or polygon, then delete it, cut / copy it (paste makes a new node), turn it
  into a new node in place, or keep only that part. Non-destructive: holes and
  shapes are node data (core/cutouts.py); the image file is never changed.
* Clone patch: cover a baked-in label with a clean piece of the same picture.

The mixins rely on CanvasView's state and signals; they hold no Qt signals.
"""
from __future__ import annotations

import copy
import math
import uuid

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QCursor, QPainterPath, QPen, QPolygonF

from core import cutouts
from core.project import Piece, snap_value
from core.stamps import edge_placement
from core.transforms import remap_groups

CUTOUT_SHAPES = ("rect", "ellipse", "lasso", "polygon")
CUTOUT_SHAPE_LABELS = {"rect": "Rectangle", "ellipse": "Ellipse",
                       "lasso": "Lasso (freehand)", "polygon": "Polygon (click points)"}


def _layer_blocked(level) -> set:
    return {layer.id for layer in level.layers if layer.locked or not layer.visible}


class SelectionToolsMixin:
    """Select all / invert / by layer, arrange, cut, paste here, send to level,
    swap pictures."""

    # -- selecting ------------------------------------------------------------
    def selectable_pieces(self) -> list[Piece]:
        """Nodes a click could pick here: unlocked, on a visible unlocked layer
        (and on the soloed layer while one is soloed)."""
        if not self.level:
            return []
        blocked = _layer_blocked(self.level)
        solo = getattr(self, "solo_layer_id", None)
        return [piece for piece in self.level.pieces
                if not piece.locked and piece.layer not in blocked
                and (not solo or piece.layer == solo)]

    def select_all(self) -> list[Piece]:
        pieces = self.selectable_pieces()
        self.select(pieces)
        return pieces

    def invert_selection(self) -> list[Piece]:
        chosen = self.selection
        pieces = [piece for piece in self.selectable_pieces() if piece.id not in chosen]
        self.select(pieces)
        return pieces

    def select_layer(self, layer_id: str) -> list[Piece]:
        """Every unlocked node on one layer (the layer must be visible and
        unlocked, like a click)."""
        if not self.level:
            return []
        layer = self.level.layer_by_id(layer_id)
        if layer is None:
            return []
        if layer.locked or not layer.visible:
            state = "locked" if layer.locked else "hidden"
            self.statusMessage.emit(
                f"Layer “{layer.name}” is {state}; unlock or show it to select its nodes.")
            return []
        pieces = [piece for piece in self.level.pieces
                  if piece.layer == layer_id and not piece.locked]
        self.select(pieces)
        return pieces

    # -- arranging --------------------------------------------------------------
    def _paint_sequence(self) -> list[Piece]:
        index = {layer.id: i for i, layer in enumerate(self.level.layers)}
        return sorted(self.level.pieces, key=lambda p: (index.get(p.layer, 999), p.z))

    def _renumber(self, ordered: list[Piece]):
        for position, piece in enumerate(ordered):
            piece.z = position

    def bring_to_front(self) -> int:
        selected = self.selected_pieces()
        if not selected or not self.level:
            return 0
        self.push_history("Bring to front")
        chosen = {piece.id for piece in selected}
        ordered = self._paint_sequence()
        self._renumber([p for p in ordered if p.id not in chosen]
                       + [p for p in ordered if p.id in chosen])
        self.dirty.emit()
        self.update()
        return len(selected)

    def send_to_back(self) -> int:
        selected = self.selected_pieces()
        if not selected or not self.level:
            return 0
        self.push_history("Send to back")
        chosen = {piece.id for piece in selected}
        ordered = self._paint_sequence()
        self._renumber([p for p in ordered if p.id in chosen]
                       + [p for p in ordered if p.id not in chosen])
        self.dirty.emit()
        self.update()
        return len(selected)

    def _step_order(self, selected, up: bool) -> bool:
        """Move each selected node past the next overlapping node on its layer
        (up = forward). Returns whether anything moved."""
        chosen = {piece.id for piece in selected}
        ordered = self._paint_sequence()
        moved = False
        walk = sorted((p for p in ordered if p.id in chosen),
                      key=lambda p: ordered.index(p), reverse=up)
        for piece in walk:
            i = ordered.index(piece)
            box = self._aabb(piece)
            span = range(i + 1, len(ordered)) if up else range(i - 1, -1, -1)
            for j in span:
                other = ordered[j]
                if other.layer != piece.layer or other.id in chosen:
                    continue
                if not self._boxes_overlap(box, self._aabb(other)):
                    continue
                ordered.pop(i)
                ordered.insert(j, piece)
                moved = True
                break
        self._renumber(ordered)
        return moved

    def insert_above(self, piece: Piece, anchor: Piece):
        """Put ``piece`` (already on this level) directly above ``anchor``."""
        ordered = [p for p in self._paint_sequence() if p.id != piece.id]
        position = ordered.index(anchor) + 1 if anchor in ordered else len(ordered)
        ordered.insert(position, piece)
        self._renumber(ordered)

    # -- clipboard ----------------------------------------------------------------
    def cut(self) -> int:
        """Copy the selection to the clipboard and remove it."""
        selected = self.selected_pieces()
        if not selected:
            return 0
        self._clipboard = [piece.to_dict() for piece in sorted(selected, key=lambda p: p.z)]
        self.push_history("Cut")
        for piece in selected:
            self.level.remove(piece)
        self.select([])
        self.dirty.emit()
        return len(selected)

    def paste_at(self, wx: float, wy: float) -> list[Piece]:
        """Paste the clipboard centered on a map point (right-click → Paste
        here). Copies snap to the grid like the originals did."""
        if not self.level or not self._clipboard:
            return []
        batch = copy.deepcopy(self._clipboard)
        remap_groups(batch)
        boxes = [self._aabb(Piece.from_dict(data)) for data in batch]
        cx = (min(b[0] for b in boxes) + max(b[2] for b in boxes)) / 2.0
        cy = (min(b[1] for b in boxes) + max(b[3] for b in boxes)) / 2.0
        dx, dy = wx - cx, wy - cy
        first = Piece.from_dict(batch[0])
        if first.snap:
            cell = max(1, self.project.cell_size)
            dx = snap_value(first.x + dx, cell) - first.x
            dy = snap_value(first.y + dy, cell) - first.y
        self.push_history("Paste here")
        created = []
        for data in batch:
            piece = Piece.from_dict(data)
            piece.id = uuid.uuid4().hex
            piece.x += dx
            piece.y += dy
            self.level.add(piece)
            created.append(piece)
        self.select(created)
        self.dirty.emit()
        return created

    # -- levels -------------------------------------------------------------------
    @staticmethod
    def _matching_layer(source_level, target_level, layer_id: str) -> str:
        """The target level's layer for a node: same name, else same position,
        else the target's active layer."""
        layer = source_level.layer_by_id(layer_id) if source_level else None
        if layer is not None:
            for candidate in target_level.layers:
                if candidate.name.casefold() == layer.name.casefold():
                    return candidate.id
            index = source_level.layers.index(layer)
            if index < len(target_level.layers):
                return target_level.layers[index].id
        return target_level.current_layer or (
            target_level.layers[0].id if target_level.layers else "")

    def send_to_levels(self, targets, copy_nodes: bool) -> int:
        """Move (or copy) the selection to other levels at the same spot."""
        if not self.project or not self.level:
            return 0
        selected = sorted(self.selected_pieces(), key=lambda piece: piece.z)
        levels = [self.project.levels[i] for i in targets
                  if 0 <= i < len(self.project.levels) and i != self.level_index]
        if not selected or not levels:
            return 0
        self.push_history("Copy to level" if copy_nodes else "Move to level")
        source = self.level
        for target in levels:
            batch = [piece.to_dict() for piece in selected]
            if copy_nodes:
                for data in batch:
                    data["id"] = uuid.uuid4().hex
                remap_groups(batch)
            for data in batch:
                data["layer"] = self._matching_layer(source, target, data.get("layer", ""))
                piece = Piece.from_dict(data)
                target.add(piece)
        if not copy_nodes:
            for piece in selected:
                source.remove(piece)
            self.select([])
        names = ", ".join(f"“{level.name}”" for level in levels[:3])
        if len(levels) > 3:
            names += f" and {len(levels) - 3} more"
        verb = "Copied" if copy_nodes else "Moved"
        self.statusMessage.emit(f"{verb} {len(selected)} node(s) to {names}.")
        self.dirty.emit()
        self.update()
        return len(selected)

    # -- swapping pictures ------------------------------------------------------
    def _apply_image(self, piece: Piece, new_path: str) -> bool:
        if not cutouts.is_image(piece) or not new_path:
            return False
        asset = self.library.get(new_path) if self.library else None
        name = asset.name if asset else new_path.replace("\\", "/").split("/")[-1]
        if piece.clip_shapes:
            # a pasted part keeps its shape and crop; only the picture changes
            piece.asset_path, piece.embedded, piece.name = new_path, "", name
            return True
        center = cutouts.source_to_world(piece, 0.5, 0.5)
        width, height = self.asset_world_size(new_path, asset)
        piece.asset_path, piece.embedded, piece.name = new_path, "", name
        piece.is_overlay = bool(asset and asset.is_overlay)
        piece.w, piece.h = float(width), float(height)
        piece.crop_rect = [0.0, 0.0, 1.0, 1.0]
        piece.x = center[0] - piece.w * piece.scale / 2.0
        piece.y = center[1] - piece.h * piece.scale / 2.0
        if self.auto_tighten and not piece.is_overlay:
            self.tighten_piece_async(piece, only_if_untouched=True)
        return True

    def swap_image(self, pieces, new_path: str, label: str = "Swap image",
                   coalesce: bool = False) -> int:
        """Give image nodes another library picture. They keep their place,
        rotation, flips, scale, layer, tint and cut-outs; the size follows the
        new picture (a 100x100 ft tile stays 20x20 squares)."""
        pieces = [piece for piece in pieces if cutouts.is_image(piece)]
        if not pieces or not new_path:
            return 0
        self.push_history(label, coalesce)
        changed = sum(1 for piece in pieces if self._apply_image(piece, new_path))
        self.selectionChanged.emit(self.selected_pieces())
        self.dirty.emit()
        self.update()
        return changed

    def folder_variants(self, path: str) -> list[str]:
        """Library images in the same folder (overlays stay with overlays)."""
        asset = self.library.get(path) if self.library and path else None
        if asset is None:
            return []
        return [item.path for item in self.library.assets_in_group(asset.folder)
                if bool(item.is_overlay) == bool(asset.is_overlay)]

    def cycle_variant(self, step: int) -> tuple[int, str]:
        """Swap each selected image to the next (+1) / previous (-1) picture
        in its library folder. Returns (nodes changed, last picture name)."""
        changed, last = 0, ""
        targets = [piece for piece in self.selected_pieces()
                   if cutouts.is_image(piece) and piece.asset_path and not piece.embedded]
        plans = []
        for piece in targets:
            options = self.folder_variants(piece.asset_path)
            if len(options) < 2 or piece.asset_path not in options:
                continue
            index = (options.index(piece.asset_path) + step) % len(options)
            plans.append((piece, options[index], index, len(options)))
        if not plans:
            return 0, ""
        self.push_history("Swap to next image" if step > 0 else "Swap to previous image",
                          True)
        for piece, path, index, total in plans:
            if self._apply_image(piece, path):
                changed += 1
                last = f"{piece.name} ({index + 1}/{total})"
        self.selectionChanged.emit(self.selected_pieces())
        self.dirty.emit()
        self.update()
        return changed, last

    def swap_every_copy(self, old_path: str, new_path: str) -> int:
        """Swap every node on every level that uses ``old_path``."""
        if not self.project or not old_path or not new_path or old_path == new_path:
            return 0
        pieces = [piece for level in self.project.levels for piece in level.pieces
                  if piece.asset_path == old_path and not piece.embedded
                  and cutouts.is_image(piece)]
        if not pieces:
            return 0
        self.push_history("Swap every copy")
        changed = sum(1 for piece in pieces if self._apply_image(piece, new_path))
        self.selectionChanged.emit(self.selected_pieces())
        self.dirty.emit()
        self.update()
        return changed

    # -- door mode for stamps ---------------------------------------------------
    def _edge_stamp_spot(self, wx: float, wy: float, template: dict):
        width = float(template.get("w", 0.0)) * float(template.get("scale", 1.0) or 1.0)
        height = float(template.get("h", 0.0)) * float(template.get("scale", 1.0) or 1.0)
        return edge_placement(wx, wy, self.project.cell_size, width, height,
                              float(template.get("rotation", 0.0) or 0.0))

    def _stamp_spot_taken(self, piece: Piece) -> bool:
        """Is an identical copy already sitting exactly there?"""
        cx, cy = piece.center
        for other in self.level.pieces:
            if (other.asset_path == piece.asset_path and other.is_text == piece.is_text
                    and abs(other.center[0] - cx) < 0.75 and abs(other.center[1] - cy) < 0.75
                    and abs((other.rotation - piece.rotation) % 360.0) < 0.5
                    and abs(other.w - piece.w) < 0.5 and abs(other.h - piece.h) < 0.5):
                return True
        return False


class CutoutToolMixin:
    """Select part of image node(s), then delete / cut / copy / keep it."""

    def _init_cutout_state(self):
        self.cutout_tool = False
        self.cutout_shape = "rect"
        self.cutout_snap = True
        self._cutout_targets: list[str] = []
        self._cutout_drag = None
        self._cutout_poly: list[tuple[float, float]] = []
        self._cutout_area = None
        self._cutout_move = None
        self._ants = 0
        self._ants_timer = QTimer(self)
        self._ants_timer.setInterval(140)
        self._ants_timer.timeout.connect(self._advance_ants)

    def _reset_cutout_state(self) -> bool:
        was = bool(getattr(self, "cutout_tool", False))
        self.cutout_tool = False
        self._cutout_targets = []
        self._cutout_drag = None
        self._cutout_poly = []
        self._cutout_area = None
        self._cutout_move = None
        if hasattr(self, "_ants_timer"):
            self._ants_timer.stop()
        return was

    def _advance_ants(self):
        if not self._cutout_area:
            self._ants_timer.stop()
            return
        self._ants = (self._ants + 1) % 8
        self.update()

    # -- state -----------------------------------------------------------------
    def set_cutout_tool(self, active: bool = True, shape: str | None = None) -> bool:
        targets = ([piece.id for piece in self.selected_pieces()
                    if cutouts.is_image(piece) and not piece.locked] if active else [])
        self._reset_tool_state()
        self.cutout_tool = bool(active)
        if shape in CUTOUT_SHAPES:
            self.cutout_shape = shape
        self._cutout_targets = targets
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor if active
                               else Qt.CursorShape.ArrowCursor))
        if active:
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.cutoutChanged.emit()
        self.update()
        return True

    def set_cutout_shape(self, shape: str):
        if shape not in CUTOUT_SHAPES:
            return
        self.cutout_shape = shape
        self._cutout_drag = None
        self._cutout_poly = []
        self.cutoutChanged.emit()
        self.update()

    def set_cutout_snap(self, on: bool):
        self.cutout_snap = bool(on)
        self.cutoutChanged.emit()

    def cutout_target_pieces(self) -> list[Piece]:
        if not self.level:
            return []
        wanted = set(self._cutout_targets)
        return [piece for piece in self.level.pieces if piece.id in wanted]

    def has_cutout_area(self) -> bool:
        return bool(self.cutout_tool and self._cutout_area)

    def clear_cutout_area(self):
        self._cutout_area = None
        self._cutout_drag = None
        self._cutout_poly = []
        self._ants_timer.stop()
        self.cutoutChanged.emit()
        self.update()

    def _image_at(self, wx: float, wy: float):
        if not self.level:
            return None
        blocked = _layer_blocked(self.level)
        for piece in reversed(self.level.paint_order()):
            if (cutouts.is_image(piece) and not piece.locked
                    and piece.layer not in blocked and piece.hit_test(wx, wy)):
                return piece
        return None

    def _cutout_point(self, wx: float, wy: float, mods):
        if mods & Qt.KeyboardModifier.AltModifier:
            return wx, wy
        if self.cutout_snap:
            cell = self.project.cell_size
            return snap_value(wx, cell), snap_value(wy, cell)
        return self._snap_point_to_guides(wx, wy)

    def _set_cutout_area(self, points, start=None):
        if not points or len(points) < 3:
            self.clear_cutout_area()
            return
        self._cutout_area = [(float(x), float(y)) for x, y in points]
        if not self._cutout_targets:
            cx = sum(x for x, _y in points) / len(points)
            cy = sum(y for _x, y in points) / len(points)
            hit = self._image_at(cx, cy) or (self._image_at(*start) if start else None)
            if hit is not None:
                self._cutout_targets = [hit.id]
                self.select([hit])
        self._ants_timer.start()
        if self._cutout_targets:
            self.statusMessage.emit(
                "Area selected — Delete removes it, Ctrl+X cuts it, Ctrl+C copies it "
                "(Ctrl+V pastes it as a new node). Right-click the area for more.")
        else:
            self.statusMessage.emit(
                "That area isn't over an image. Select the image(s) to cut, or draw "
                "over one.")
        self.cutoutChanged.emit()
        self.update()

    def _finish_cutout_polygon(self):
        points = list(self._cutout_poly)
        self._cutout_poly = []
        if len(points) >= 3:
            self._set_cutout_area(points, points[0])
        else:
            self.update()

    def _drag_area_points(self, drag):
        x0, y0 = drag["start"]
        x1, y1 = drag["current"]
        if drag.get("constrain"):
            side = max(abs(x1 - x0), abs(y1 - y0))
            x1 = x0 + math.copysign(side, (x1 - x0) or 1.0)
            y1 = y0 + math.copysign(side, (y1 - y0) or 1.0)
        if self.cutout_shape == "ellipse":
            return cutouts.ellipse_polygon(x0, y0, x1, y1)
        return cutouts.rect_polygon(x0, y0, x1, y1)

    # -- mouse -------------------------------------------------------------------
    def _cutout_press(self, sx, sy, event) -> bool:
        wx, wy = self.screen_to_world(sx, sy)
        button = event.button()
        mods = event.modifiers()
        if button == Qt.MouseButton.RightButton:
            if self._cutout_poly:
                self._finish_cutout_polygon()
            elif self._cutout_area and cutouts.point_in_polygon(wx, wy, self._cutout_area):
                self.cutoutMenuRequested.emit(event.globalPosition().toPoint())
            else:
                self.set_cutout_tool(False)
            event.accept()
            return True
        if button != Qt.MouseButton.LeftButton:
            return False
        event.accept()
        if mods & Qt.KeyboardModifier.ControlModifier:
            hit = self._image_at(wx, wy)
            if hit is not None:
                if hit.id in self._cutout_targets:
                    self._cutout_targets.remove(hit.id)
                else:
                    self._cutout_targets.append(hit.id)
                self.select(self.cutout_target_pieces())
                self.cutoutChanged.emit()
                self.update()
            return True
        if self.cutout_shape == "polygon":
            point = self._cutout_point(wx, wy, mods)
            if len(self._cutout_poly) >= 3:
                fx, fy = self.world_to_screen(*self._cutout_poly[0])
                if math.hypot(fx - sx, fy - sy) <= 8:
                    self._finish_cutout_polygon()
                    return True
            if not self._cutout_poly:
                self._cutout_area = None
                self._ants_timer.stop()
            self._cutout_poly.append(point)
            self.cutoutChanged.emit()
            self.update()
            return True
        if self._cutout_area and cutouts.point_in_polygon(wx, wy, self._cutout_area):
            self._cutout_move = {"start": (wx, wy), "points": list(self._cutout_area)}
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            return True
        self._cutout_area = None
        self._ants_timer.stop()
        if self.cutout_shape == "lasso":
            start = (wx, wy)
        else:
            start = self._cutout_point(wx, wy, mods)
        self._cutout_drag = {"start": start, "current": start, "points": [start],
                             "constrain": bool(mods & Qt.KeyboardModifier.ShiftModifier)}
        self.cutoutChanged.emit()
        self.update()
        return True

    def _cutout_motion(self, sx, sy, event) -> bool:
        wx, wy = self.screen_to_world(sx, sy)
        mods = event.modifiers()
        if self._cutout_move:
            move = self._cutout_move
            dx, dy = wx - move["start"][0], wy - move["start"][1]
            if self.cutout_snap and not mods & Qt.KeyboardModifier.AltModifier:
                cell = max(1, self.project.cell_size)
                dx, dy = round(dx / cell) * cell, round(dy / cell) * cell
            self._cutout_area = [(x + dx, y + dy) for x, y in move["points"]]
            self.update()
            return True
        if self._cutout_drag:
            drag = self._cutout_drag
            drag["constrain"] = bool(mods & Qt.KeyboardModifier.ShiftModifier)
            if self.cutout_shape == "lasso":
                last = drag["points"][-1]
                if math.dist(last, (wx, wy)) * self.zoom >= 3.0:
                    drag["points"].append((wx, wy))
                drag["current"] = (wx, wy)
            else:
                drag["current"] = self._cutout_point(wx, wy, mods)
            self.cursorMoved.emit(wx, wy)
            self.update()
            return True
        if self._cutout_poly:
            self.update()
        return False

    def _cutout_release(self, sx, sy, event) -> bool:
        if self._cutout_move:
            self._cutout_move = None
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            self.cutoutChanged.emit()
            event.accept()
            return True
        if self._cutout_drag:
            drag = self._cutout_drag
            self._cutout_drag = None
            if self.cutout_shape == "lasso":
                wx, wy = self.screen_to_world(sx, sy)
                points = drag["points"] + [(wx, wy)]
                points = cutouts.simplify_path(points, 0.8 / max(self.zoom, 1e-6))
                big_enough = (len(points) >= 3 and cutouts.polygon_area(points)
                              * self.zoom * self.zoom >= 16.0)
                self._set_cutout_area(points if big_enough else None, drag["start"])
            else:
                points = self._drag_area_points(drag)
                x0, y0, x1, y1 = cutouts.polygon_bounds(points)
                big_enough = (x1 - x0) * self.zoom >= 3 and (y1 - y0) * self.zoom >= 3
                self._set_cutout_area(points if big_enough else None, drag["start"])
            event.accept()
            return True
        return False

    def _cutout_double_click(self) -> bool:
        if self.cutout_tool and self.cutout_shape == "polygon" and self._cutout_poly:
            self._finish_cutout_polygon()
            return True
        return False

    # -- keyboard ----------------------------------------------------------------
    def handle_cutout_key(self, event) -> bool:
        """Keys while the cut-out tool is active. True when handled."""
        if not self.cutout_tool:
            return False
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if key == Qt.Key.Key_Escape:
            if self._cutout_poly or self._cutout_drag:
                self._cutout_poly = []
                self._cutout_drag = None
                self.update()
            elif self._cutout_area:
                self.clear_cutout_area()
            else:
                self.set_cutout_tool(False)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._cutout_poly:
            self._finish_cutout_polygon()
            return True
        if key == Qt.Key.Key_Backspace and self._cutout_poly:
            self._cutout_poly.pop()
            self.update()
            return True
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self._cutout_area:
            self.cutout_delete()
            return True
        if ctrl and key == Qt.Key.Key_X:
            self.cutout_cut()
            return True
        if ctrl and key == Qt.Key.Key_C:
            self.cutout_copy()
            return True
        if ctrl and key == Qt.Key.Key_V:
            self.set_cutout_tool(False)
            self.paste()
            return True
        arrows = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
                  Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if key in arrows and self._cutout_area:
            step = (self.project.cell_size
                    if mods & Qt.KeyboardModifier.ShiftModifier else 1)
            dx, dy = arrows[key]
            self._cutout_area = [(x + dx * step, y + dy * step) for x, y in self._cutout_area]
            self.update()
            return True
        return key in arrows or key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)

    # -- operations ----------------------------------------------------------------
    def _cutout_jobs(self) -> list[tuple[Piece, list]]:
        if not self._cutout_area:
            return []
        jobs = []
        for piece in self.cutout_target_pieces():
            poly = cutouts.world_polygon_to_source(piece, self._cutout_area)
            if poly is not None and cutouts.overlaps(piece, poly):
                jobs.append((piece, poly))
        return jobs

    def _no_jobs_message(self):
        if not self._cutout_area:
            self.statusMessage.emit("Draw an area over the image first.")
        elif not self._cutout_targets:
            self.statusMessage.emit("Select the image(s) to cut, then draw the area.")
        else:
            self.statusMessage.emit("The area doesn't touch the selected image(s).")

    def _sections(self, jobs) -> list[tuple[Piece, dict]]:
        """(original node, new node data) for the part of each job's node
        inside the area. Parts taken from several nodes form one group."""
        out = []
        for piece, poly in jobs:
            data = cutouts.section_data(piece.to_dict(), poly, min_size=1.0)
            if data is not None:
                out.append((piece, data))
        group = uuid.uuid4().hex if len(out) > 1 else ""
        for _piece, data in out:
            data["group_id"] = group
        return out

    def _punch(self, jobs) -> tuple[int, int]:
        """Add the area as a hole to each job's node; a node the area covers
        completely is removed. Returns (holes added, nodes removed)."""
        holes = removed = 0
        for piece, poly in jobs:
            if cutouts.covers(piece, poly):
                self.level.remove(piece)
                if piece.id in self._cutout_targets:
                    self._cutout_targets.remove(piece.id)
                self.selection.discard(piece.id)
                removed += 1
                continue
            data = {"cutouts": piece.cutouts, "crop_rect": piece.crop_rect}
            if cutouts.add_cutout(data, poly):
                piece.cutouts = data["cutouts"]
                holes += 1
        return holes, removed

    def _after_cutout_edit(self, message: str, keep_area: bool = False):
        if not keep_area:
            self._cutout_area = None
            self._ants_timer.stop()
        self.statusMessage.emit(message)
        self.selectionChanged.emit(self.selected_pieces())
        self.cutoutChanged.emit()
        self.dirty.emit()
        self.update()

    def cutout_delete(self) -> int:
        jobs = self._cutout_jobs()
        if not jobs:
            self._no_jobs_message()
            return 0
        self.push_history("Delete image area")
        holes, removed = self._punch(jobs)
        message = f"Deleted the area from {holes} image(s)." if holes else ""
        if removed:
            message += f" Removed {removed} image(s) the area covered completely."
        self._after_cutout_edit(message.strip())
        return len(jobs)

    def cutout_copy(self) -> int:
        sections = self._sections(self._cutout_jobs())
        if not sections:
            self._no_jobs_message()
            return 0
        self._clipboard = [data for _piece, data in sections]
        self.statusMessage.emit(
            f"Copied the area ({len(sections)} piece(s)). Ctrl+V pastes it as a new "
            "node; right-click the map → Paste here puts it where you click.")
        self.cutoutChanged.emit()
        return len(sections)

    def cutout_cut(self) -> int:
        jobs = self._cutout_jobs()
        sections = self._sections(jobs)
        if not sections:
            self._no_jobs_message()
            return 0
        self._clipboard = [data for _piece, data in sections]
        self.push_history("Cut image area")
        self._punch(jobs)
        self._after_cutout_edit(
            f"Cut the area ({len(sections)} piece(s)). Ctrl+V pastes it as a new node; "
            "right-click the map → Paste here puts it where you click.")
        return len(sections)

    def cutout_to_new_node(self, cut: bool = True) -> list[Piece]:
        """Turn the area into new node(s) in place (selected, ready to drag)."""
        jobs = self._cutout_jobs()
        sections = self._sections(jobs)
        if not sections:
            self._no_jobs_message()
            return []
        self.push_history("Cut area to a new node" if cut else "Copy area to a new node")
        created = []
        for anchor, data in sections:
            piece = Piece.from_dict(data)
            self.level.add(piece)
            self.insert_above(piece, anchor)
            created.append(piece)
        if cut:
            self._punch(jobs)
        self._reset_tool_state()
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.select(created)
        self.statusMessage.emit(
            f"Made {len(created)} new node(s) from the area — drag to move "
            "(it stays grouped if it spans several images).")
        self.cutoutChanged.emit()
        self.dirty.emit()
        self.update()
        return created

    def cutout_keep_only(self) -> int:
        jobs = self._cutout_jobs()
        if not jobs:
            self._no_jobs_message()
            return 0
        self.push_history("Keep only the area")
        changed = 0
        for piece, poly in jobs:
            data = cutouts.keep_only(piece.to_dict(), poly, min_size=1.0)
            if data is None:
                continue
            for name in ("x", "y", "w", "h", "crop_rect", "clip_shapes", "cutouts"):
                setattr(piece, name, data[name])
            changed += 1
        self._after_cutout_edit(f"Kept only the area of {changed} image(s).")
        return changed

    def restore_cutouts(self, pieces, last_only: bool = False) -> int:
        pieces = [piece for piece in pieces if piece.cutouts]
        if not pieces:
            return 0
        self.push_history("Restore last cut-out" if last_only else "Restore cut-outs")
        for piece in pieces:
            piece.cutouts = piece.cutouts[:-1] if last_only else []
        self.selectionChanged.emit(self.selected_pieces())
        self.dirty.emit()
        self.update()
        return len(pieces)

    # -- painting ------------------------------------------------------------------
    def _draw_cutout_overlay(self, painter):
        if not self.cutout_tool or not self.level:
            return
        accent = QColor(self.theme_accent)
        painter.save()
        # images being cut, and the holes they already have
        for piece in self.cutout_target_pieces():
            corners = self._piece_corners_screen(piece)
            pen = QPen(accent)
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setWidthF(1.0)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in corners]))
            hole_pen = QPen(QColor(255, 255, 255, 120))
            hole_pen.setStyle(Qt.PenStyle.DotLine)
            painter.setPen(hole_pen)
            for hole in piece.cutouts:
                points = [QPointF(*self.world_to_screen(x, y))
                          for x, y in cutouts.source_polygon_to_world(piece, hole)]
                painter.drawPolygon(QPolygonF(points))
        # area being drawn
        preview = None
        if self._cutout_drag:
            preview = (self._cutout_drag["points"] + [self._cutout_drag["current"]]
                       if self.cutout_shape == "lasso"
                       else self._drag_area_points(self._cutout_drag))
        if preview and len(preview) >= 2:
            fill = QColor(accent)
            fill.setAlpha(36)
            pen = QPen(accent)
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setWidthF(1.5)
            painter.setPen(pen)
            painter.setBrush(QBrush(fill))
            painter.drawPolygon(QPolygonF([QPointF(*self.world_to_screen(x, y))
                                           for x, y in preview]))
        if self._cutout_poly:
            points = [QPointF(*self.world_to_screen(x, y)) for x, y in self._cutout_poly]
            cursor = self._cursor_world
            if cursor[0] >= 0:
                points.append(QPointF(*self.world_to_screen(*cursor)))
            pen = QPen(accent)
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setWidthF(1.5)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline(QPolygonF(points))
            painter.setBrush(QBrush(accent))
            for index, point in enumerate(points[:len(self._cutout_poly)]):
                painter.drawEllipse(point, 5 if index == 0 else 3.5, 5 if index == 0 else 3.5)
        # finished area: marching ants
        if self._cutout_area:
            path = QPainterPath()
            path.addPolygon(QPolygonF([QPointF(*self.world_to_screen(x, y))
                                       for x, y in self._cutout_area]))
            path.closeSubpath()
            fill = QColor(accent)
            fill.setAlpha(40)
            painter.fillPath(path, fill)
            black = QPen(QColor(0, 0, 0, 220))
            black.setWidthF(1.2)
            painter.strokePath(path, black)
            white = QPen(QColor(255, 255, 255, 235))
            white.setWidthF(1.2)
            white.setDashPattern([4.0, 4.0])
            white.setDashOffset(float(self._ants))
            painter.strokePath(path, white)
        painter.restore()


class CloneToolMixin:
    """Cover a baked-in label with a clean piece of the same picture."""

    def _init_clone_state(self):
        self.clone_tool = False
        self._clone_drag = None       # drawing the cover rectangle
        self._clone_base = None       # patch data showing exactly what's under it
        self._clone_patch = None      # patch data with the picked source
        self._clone_center = None     # world center of the patch
        self._clone_offset = (0.0, 0.0)
        self._clone_anchor_id = None  # node the patch copies (it goes right above it)
        self._clone_edit_id = None    # existing patch whose source is re-picked

    def _reset_clone_state(self) -> bool:
        was = bool(getattr(self, "clone_tool", False))
        self.clone_tool = False
        self._clone_drag = None
        self._clone_base = None
        self._clone_patch = None
        self._clone_center = None
        self._clone_offset = (0.0, 0.0)
        self._clone_anchor_id = None
        self._clone_edit_id = None
        return was

    def set_clone_tool(self, active: bool = True) -> bool:
        self._reset_tool_state()
        self.clone_tool = bool(active)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor if active
                               else Qt.CursorShape.ArrowCursor))
        if active:
            self.setFocus(Qt.FocusReason.OtherFocusReason)
            self.statusMessage.emit(
                "Clone patch: drag a box over the label you want to hide.")
        self.update()
        return True

    def begin_clone_repick(self, piece: Piece) -> bool:
        """Pick a new source for an existing clone patch."""
        if not piece or not piece.clone_home:
            return False
        self._reset_tool_state()
        self.clone_tool = True
        base = piece.to_dict()
        base["crop_rect"] = list(piece.clone_home)
        self._clone_base = base
        self._clone_patch = piece.to_dict()
        self._clone_center = piece.center
        self._clone_edit_id = piece.id
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.statusMessage.emit(
            "Point at clean floor — the dashed box is what gets copied. Click to "
            "use it, Esc to keep the old one. (Alt = off the grid)")
        self.update()
        return True

    def clone_phase(self) -> str:
        if not self.clone_tool:
            return ""
        return "source" if self._clone_base is not None else "cover"

    def _begin_clone_source(self, x0, y0, x1, y1) -> bool:
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        cx, cy = (left + right) / 2.0, (top + bottom) / 2.0
        tile = self._image_at(cx, cy)
        if tile is None:
            self.statusMessage.emit("Draw the box over a picture's label.")
            return False
        corners = [cutouts.world_to_source(tile, x, y)
                   for x, y in cutouts.rect_polygon(left, top, right, bottom)]
        us = [u for u, _v in corners]
        vs = [v for _u, v in corners]
        u0, v0, u1, v1 = cutouts.crop_box(tile)
        box = (max(u0, min(us)), max(v0, min(vs)), min(u1, max(us)), min(v1, max(vs)))
        if box[2] - box[0] <= 1e-4 or box[3] - box[1] <= 1e-4:
            self.statusMessage.emit("Draw the box over a picture's label.")
            return False
        base = cutouts.reframe(tile.to_dict(), box)
        number = sum(1 for piece in self.level.pieces if piece.clone_home) + 1
        base.update({"id": uuid.uuid4().hex, "name": f"Clone patch {number}",
                     "cutouts": [], "clip_shapes": [], "locked": False,
                     "group_id": "", "snap": False,
                     "clone_home": list(base["crop_rect"])})
        self._clone_base = base
        self._clone_patch = copy.deepcopy(base)
        self._clone_center = Piece.from_dict(base).center
        self._clone_anchor_id = tile.id
        self._clone_offset = (0.0, 0.0)
        self.statusMessage.emit(
            "Now point at clean floor — the dashed box is what gets copied (it moves "
            "in whole squares so floor patterns line up). Click to place; Esc cancels; "
            "Alt = off the grid.")
        self.update()
        return True

    def _update_clone_source(self, wx: float, wy: float, mods):
        base = self._clone_base
        if base is None or self._clone_center is None:
            return
        dx, dy = wx - self._clone_center[0], wy - self._clone_center[1]
        if not mods & Qt.KeyboardModifier.AltModifier:
            cell = max(1, self.project.cell_size)
            dx, dy = round(dx / cell) * cell, round(dy / cell) * cell
        du, dv = cutouts.world_offset_to_source(base, dx, dy)
        self._clone_patch = cutouts.clone_window(base, du, dv)
        # where the copied area really is (the window stops at the picture's edge)
        u0, v0, u1, v1 = base["crop_rect"]
        uc, vc = (u0 + u1) / 2.0, (v0 + v1) / 2.0
        crop = self._clone_patch["crop_rect"]
        ox, oy = cutouts.source_to_world(base, uc, vc)
        tx, ty = cutouts.source_to_world(base, uc + crop[0] - u0, vc + crop[1] - v0)
        self._clone_offset = (tx - ox, ty - oy)
        self.update()

    def _commit_clone(self):
        data = self._clone_patch
        if data is None:
            return None
        if self._clone_edit_id:
            piece = next((p for p in self.level.pieces if p.id == self._clone_edit_id), None)
            self._clone_edit_id = None
            if piece is not None:
                self.push_history("Clone patch source")
                piece.crop_rect = list(data["crop_rect"])
                self._reset_tool_state()
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
                self.select([piece])
                self.dirty.emit()
                self.update()
            return piece
        self.push_history("Clone patch")
        piece = Piece.from_dict(data)
        self.level.add(piece)
        anchor = next((p for p in self.level.pieces if p.id == self._clone_anchor_id), None)
        if anchor is not None:
            piece.layer = anchor.layer
            self.insert_above(piece, anchor)
        self._clone_base = self._clone_patch = self._clone_center = None
        self._clone_anchor_id = None
        self.select([piece])
        self.statusMessage.emit(
            "Clone patch placed. Drag another box to hide the next label, or press Esc.")
        self.dirty.emit()
        self.update()
        return piece

    def _cancel_clone_source(self):
        editing = self._clone_edit_id
        self._clone_base = self._clone_patch = self._clone_center = None
        self._clone_anchor_id = None
        self._clone_edit_id = None
        if editing:
            self.set_clone_tool(False)
        self.update()

    def _clone_press(self, sx, sy, event) -> bool:
        wx, wy = self.screen_to_world(sx, sy)
        button = event.button()
        if button == Qt.MouseButton.RightButton:
            if self._clone_base is not None:
                self._cancel_clone_source()
            else:
                self.set_clone_tool(False)
            event.accept()
            return True
        if button != Qt.MouseButton.LeftButton:
            return False
        event.accept()
        if self._clone_base is not None:
            self._update_clone_source(wx, wy, event.modifiers())
            self._commit_clone()
            return True
        start = self._snap_tool_point(wx, wy, event.modifiers())
        self._clone_drag = {"start": start, "current": start}
        self.update()
        return True

    def _clone_motion(self, sx, sy, event) -> bool:
        wx, wy = self.screen_to_world(sx, sy)
        if self._clone_drag:
            self._clone_drag["current"] = self._snap_tool_point(wx, wy, event.modifiers())
            self.update()
            return True
        if self._clone_base is not None:
            self._update_clone_source(wx, wy, event.modifiers())
            return True
        return False

    def _clone_release(self, sx, sy, event) -> bool:
        if not self._clone_drag:
            return False
        drag = self._clone_drag
        self._clone_drag = None
        (x0, y0), (x1, y1) = drag["start"], drag["current"]
        if abs(x1 - x0) * self.zoom >= 3 and abs(y1 - y0) * self.zoom >= 3:
            if self._begin_clone_source(x0, y0, x1, y1):
                wx, wy = self.screen_to_world(sx, sy)
                self._update_clone_source(wx, wy, event.modifiers())
        self.update()
        event.accept()
        return True

    def handle_clone_key(self, event) -> bool:
        if not self.clone_tool:
            return False
        if event.key() == Qt.Key.Key_Escape:
            if self._clone_base is not None or self._clone_drag:
                self._clone_drag = None
                self._cancel_clone_source()
            else:
                self.set_clone_tool(False)
            return True
        return False

    def _draw_clone_overlay(self, painter):
        if not self.clone_tool or not self.level:
            return
        accent = QColor(self.theme_accent)
        painter.save()
        if self._clone_drag:
            (x0, y0), (x1, y1) = self._clone_drag["start"], self._clone_drag["current"]
            a = self.world_to_screen(min(x0, x1), min(y0, y1))
            b = self.world_to_screen(max(x0, x1), max(y0, y1))
            fill = QColor(accent)
            fill.setAlpha(36)
            pen = QPen(accent)
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setWidthF(1.5)
            painter.setPen(pen)
            painter.setBrush(QBrush(fill))
            painter.drawRect(QRectF(a[0], a[1], b[0] - a[0], b[1] - a[1]))
        if self._clone_patch is not None:
            ghost = Piece.from_dict(self._clone_patch)
            self._draw_piece(painter, ghost, 1.0, mark_missing=False)
            corners = self._piece_corners_screen(ghost)
            pen = QPen(accent)
            pen.setWidthF(2.0)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in corners]))
            ox, oy = self._clone_offset
            source = [QPointF(x + ox * self.zoom, y + oy * self.zoom) for x, y in corners]
            dashed = QPen(QColor(255, 255, 255, 230))
            dashed.setStyle(Qt.PenStyle.DashLine)
            dashed.setWidthF(1.5)
            painter.setPen(dashed)
            painter.drawPolygon(QPolygonF(source))
            if abs(ox) + abs(oy) > 0.5:
                center = QPolygonF([QPointF(x, y) for x, y in corners]).boundingRect().center()
                painter.drawLine(center, QPointF(center.x() + ox * self.zoom,
                                                 center.y() + oy * self.zoom))
        painter.restore()
