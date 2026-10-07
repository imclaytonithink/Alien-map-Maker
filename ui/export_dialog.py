"""Shared PNG/PDF export dialog with matching grid and sizing controls."""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QRadioButton, QButtonGroup,
    QCheckBox, QPushButton, QFileDialog, QHBoxLayout,
    QLabel, QMessageBox, QComboBox, QSlider,
)

from core import exporter
from ui.color_picker import choose_color


class ExportDialog(QDialog):
    def __init__(self, project, canvas, parent=None, file_format="png",
                 default_preset=None):
        super().__init__(parent)
        self.project = project
        self.canvas = canvas
        self.file_format = "pdf" if str(file_format).lower() == "pdf" else "png"
        self.output_path = ""
        self.setWindowTitle(f"Export {self.file_format.upper()}")
        self.setMinimumWidth(440)
        self._build()
        if default_preset:
            index = self.cmb_preset.findText(default_preset)
            if index >= 0:
                self.cmb_preset.setCurrentIndex(index)

    def _build(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.scope = QButtonGroup(self)
        self.rb_current = QRadioButton("Current level only")
        self.rb_all = QRadioButton(
            "All levels (one PDF page each)" if self.file_format == "pdf"
            else "All levels (one PNG each)")
        self.rb_current.setChecked(True)
        self.scope.addButton(self.rb_current, 0)
        self.scope.addButton(self.rb_all, 1)
        scope_row = QHBoxLayout()
        scope_row.addWidget(self.rb_current)
        scope_row.addWidget(self.rb_all)
        form.addRow("Export", scope_row)

        self.cmb_preset = QComboBox()
        self.cmb_preset.addItems(list(exporter.PRESETS.keys()))
        self.cmb_preset.setToolTip(
            "Tabletop Simulator presets scale the longest map edge to the "
            "selected pixel size while preserving the map's aspect ratio. "
            "Import the resulting PNG as a Custom Board in Tabletop Simulator.")
        self.cmb_preset.currentTextChanged.connect(self._preset_changed)
        form.addRow("Image / page size", self.cmb_preset)

        self.chk_trans = QCheckBox("Leave out the backdrop (transparent)")
        self.chk_trans.setToolTip(self.TRANSPARENT_TIP)
        form.addRow(self.chk_trans)
        self.lbl_backdrop = QLabel(self._backdrop_note())
        self.lbl_backdrop.setWordWrap(True)
        self.lbl_backdrop.setToolTip("Change it in the Node tab → Backdrop (with nothing "
                                     "selected), or right-click the empty map → Backdrop….")
        form.addRow(self.lbl_backdrop)

        self.chk_grid = QCheckBox("Include grid")
        self.chk_grid.setChecked(self.project.export_grid)
        self.chk_grid.toggled.connect(self._grid_toggled)
        form.addRow(self.chk_grid)

        self.btn_color = QPushButton("Choose grid color…")
        self.btn_color.clicked.connect(self._pick)
        self._update_color_button()
        form.addRow("Grid color", self.btn_color)

        self.sl_op = QSlider(Qt.Orientation.Horizontal)
        self.sl_op.setRange(0, 100)
        self.sl_op.setValue(int(self.project.export_grid_opacity * 100))
        form.addRow("Grid opacity", self.sl_op)
        self._grid_toggled(self.chk_grid.isChecked())

        self.chk_node_borders = QCheckBox("Include node outlines")
        self.chk_node_borders.setChecked(self.project.export_node_borders)
        form.addRow(self.chk_node_borders)
        self.chk_zones = QCheckBox("Include gameplay zones")
        self.chk_zones.setChecked(self.project.export_zones)
        form.addRow(self.chk_zones)

        # Editor aids: off unless you ask for them.
        self.chk_centerlines = QCheckBox("Include canvas centerlines")
        self.chk_centerlines.setToolTip(
            "Bake the dashed amber lines through the middle of the map into the image.")
        self.chk_centerlines.setChecked(bool(getattr(self.project, "export_centerlines", False)))
        form.addRow(self.chk_centerlines)
        self.chk_guides = QCheckBox("Include guides")
        self.chk_guides.setToolTip(
            "Bake the level's placed guide lines into the image, in the guide color.")
        self.chk_guides.setChecked(bool(getattr(self.project, "export_guides", False)))
        form.addRow(self.chk_guides)
        self.chk_coordinates = QCheckBox("Include grid coordinates (labeled border)")
        self.chk_coordinates.setToolTip(
            "Add a border one square wide with column letters along the top and "
            "bottom and row numbers down the sides. The image grows by one square "
            "on every side, so a virtual-tabletop grid still lines up (offset by "
            "one square).")
        self.chk_coordinates.setChecked(bool(getattr(self.project, "export_coordinates", False)))
        form.addRow(self.chk_coordinates)

        left_out = sorted({layer.name for level in self.project.levels
                           for layer in level.layers
                           if not getattr(layer, "export", True)})
        if left_out:
            shown = ", ".join(left_out[:4]) + (" …" if len(left_out) > 4 else "")
            note = QLabel(f"Left out of exports (Layers panel): {shown}")
            note.setWordWrap(True)
            note.setToolTip("These layers are set to stay on the canvas only. Click "
                            "the picture button next to a layer to include it.")
            form.addRow(note)

        self.le_path = QLabel("No destination selected")
        self.le_path.setWordWrap(True)
        self.btn_path = QPushButton("Choose…")
        self.btn_path.clicked.connect(self._choose)
        path_row = QHBoxLayout()
        path_row.addWidget(self.le_path, 1)
        path_row.addWidget(self.btn_path)
        form.addRow("Output", path_row)

        layout.addLayout(form)
        self.status = QLabel("")
        layout.addWidget(self.status)
        btns = QHBoxLayout()
        b_cancel = QPushButton("Cancel")
        b_cancel.clicked.connect(self.reject)
        b_ok = QPushButton("Export")
        b_ok.clicked.connect(self._export)
        btns.addStretch(1)
        btns.addWidget(b_cancel)
        btns.addWidget(b_ok)
        layout.addLayout(btns)

    TRANSPARENT_TIP = (
        "Leave the level's backdrop (color or floor texture) out so empty map areas "
        "stay transparent. For PDF, transparent areas may appear white in some viewers.")

    def _backdrop_note(self) -> str:
        level = self.canvas.level if self.canvas is not None else None
        if level is None:
            return ""
        mode = getattr(level, "backdrop", "color")
        if mode == "none":
            return ("This level's backdrop is set to None, so its PNG keeps "
                    "transparency even without the box above.")
        if mode == "texture" and level.backdrop_texture:
            name = level.backdrop_texture.replace("\\", "/").split("/")[-1]
            return f"Backdrop: floor texture “{name}” over {QColor(level.background).name()}."
        return f"Backdrop: solid color {QColor(level.background).name()}."

    def _preset_changed(self, text):
        is_tts = text.startswith("Tabletop Sim")
        if is_tts and self.chk_trans.isChecked():
            self.chk_trans.setChecked(False)
        self.chk_trans.setEnabled(not is_tts)
        if is_tts:
            self.chk_trans.setToolTip(
                "Tabletop Simulator board export uses an opaque RGB PNG; "
                "the level's backdrop is included (its color when it is set to None).")
            self.chk_grid.setToolTip(
                "Tabletop Simulator Custom Boards have an in-game grid. "
                "Uncheck this to use that grid instead of baking grid lines into the PNG.")
        else:
            self.chk_trans.setToolTip(self.TRANSPARENT_TIP)
            self.chk_grid.setToolTip("Show or hide the grid in the exported image.")

    def _grid_toggled(self, on):
        self.btn_color.setEnabled(on)
        self.sl_op.setEnabled(on)

    def _update_color_button(self):
        color = QColor(self.project.export_grid_color)
        if color.isValid():
            self.btn_color.setStyleSheet(
                f"background-color:{color.name()}; color:#ffffff; "
                "font-weight:bold;")
            self.btn_color.setText(f"{color.name().upper()} — Choose…")

    def _pick(self):
        color = choose_color(QColor(self.project.export_grid_color), self,
                             self.canvas, "Choose export grid color")
        if color.isValid():
            self.project.export_grid_color = color.name()
            self._update_color_button()

    def _defaults(self) -> tuple[str, str]:
        """(base file name, folder): named after the map, in the last export
        folder — never a "map.png" in whatever folder the app started in."""
        main = self.parent()
        if main is not None and hasattr(main, "export_defaults"):
            return main.export_defaults()
        from core.userfiles import safe_file_stem
        return safe_file_stem(getattr(self.project, "name", "") or "map"), os.path.expanduser("~")

    def _default_file_name(self) -> str:
        base, _folder = self._defaults()
        if self.file_format == "pdf":
            return base + ".pdf"
        level = self.canvas.level if self.canvas is not None else None
        if level is not None and len(self.project.levels) > 1:
            from core.userfiles import safe_file_stem
            return f"{base} - {safe_file_stem(level.name, 'level')}.png"
        return base + ".png"

    def _choose(self):
        base, folder = self._defaults()
        if self.file_format == "png" and self.rb_all.isChecked():
            path = QFileDialog.getExistingDirectory(self, "Choose output folder", folder)
        else:
            if self.file_format == "pdf":
                title, file_filter = "Save PDF", "PDF (*.pdf)"
            else:
                title, file_filter = "Save PNG", "PNG (*.png)"
            path, _ = QFileDialog.getSaveFileName(
                self, title, os.path.join(folder, self._default_file_name()), file_filter)
        if path:
            self.output_path = path
            self.le_path.setText(path)

    def _confirm_missing_images(self, missing: dict) -> bool:
        """Images that can't be found export as grey boxes; ask first."""
        names = sorted(os.path.basename(path.replace("\\", "/")) for path in missing)
        listed = "\n".join(f"• {name}" for name in names[:8])
        if len(names) > 8:
            listed += f"\n… and {len(names) - 8} more"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Missing images")
        box.setText(f"{len(missing)} image(s) used here can't be found, so they would "
                    "export as grey boxes.")
        box.setInformativeText("Use File → Find missing images… to relink them, or "
                               "export anyway.\n\n" + listed)
        export = box.addButton("Export anyway", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is export

    def _export(self):
        path = self.output_path.strip()
        if not path:
            QMessageBox.warning(self, "Export", "Choose an output destination first.")
            return
        if self.file_format == "png" and self.rb_all.isChecked() and not os.path.isdir(path):
            QMessageBox.warning(self, "Export", "Choose an existing output folder.")
            return
        if self.file_format == "pdf" and not path.lower().endswith(".pdf"):
            path += ".pdf"
        if self.file_format == "png" and not self.rb_all.isChecked() and not path.lower().endswith(".png"):
            path += ".png"

        export_levels = ([self.canvas.level] if self.rb_current.isChecked()
                         else list(self.project.levels))
        missing = self.project.missing_assets([lv for lv in export_levels if lv is not None])
        if missing and not self._confirm_missing_images(missing):
            return

        scale = exporter.preset_scale(self.project, self.cmb_preset.currentText())
        transparent = self.chk_trans.isChecked()
        opaque = self.cmb_preset.currentText().startswith("Tabletop Sim")
        include_grid = self.chk_grid.isChecked()
        grid_opacity = self.sl_op.value() / 100.0
        grid_color = self.project.export_grid_color
        include_node_borders = self.chk_node_borders.isChecked()
        include_zones = self.chk_zones.isChecked()
        extras = {"include_centerlines": self.chk_centerlines.isChecked(),
                  "include_guides": self.chk_guides.isChecked(),
                  "include_coordinates": self.chk_coordinates.isChecked()}
        self.project.export_grid = include_grid
        self.project.export_grid_opacity = grid_opacity
        self.project.export_node_borders = include_node_borders
        self.project.export_zones = include_zones
        self.project.export_centerlines = extras["include_centerlines"]
        self.project.export_guides = extras["include_guides"]
        self.project.export_coordinates = extras["include_coordinates"]
        if self.parent() and hasattr(self.parent(), "_mark_dirty"):
            self.parent()._mark_dirty()

        try:
            if self.file_format == "pdf":
                levels = ([self.canvas.level] if self.rb_current.isChecked()
                          else list(self.project.levels))
                if not levels or levels[0] is None:
                    QMessageBox.warning(self, "Export", "No level selected.")
                    return
                exporter.export_pdf(
                    self.project, path, include_grid, scale, levels=levels,
                    transparent=transparent, grid_color=grid_color,
                    grid_opacity=grid_opacity,
                    include_node_borders=include_node_borders,
                    include_zones=include_zones, opaque=opaque, **extras)
                self.status.setText(f"Saved PDF:\n{path}")
            elif self.rb_all.isChecked():
                files = exporter.export_all_levels(
                    self.project, path, include_grid, scale, self._defaults()[0], transparent,
                    grid_color, grid_opacity, include_node_borders,
                    include_zones, opaque=opaque, **extras)
                self.status.setText(f"Exported {len(files)} PNG file(s) to:\n{path}")
            else:
                level = self.canvas.level
                if level is None:
                    QMessageBox.warning(self, "Export", "No level selected.")
                    return
                exporter.export_level_to_file(
                    self.project, level, path, include_grid, scale, transparent,
                    grid_color, grid_opacity, include_node_borders,
                    include_zones, opaque=opaque, **extras)
                self.status.setText(f"Saved PNG:\n{path}")
            main = self.parent()
            if main is not None and hasattr(main, "remember_export_dir"):
                main.remember_export_dir(path)
            QMessageBox.information(self, "Export", "Export complete.")
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
