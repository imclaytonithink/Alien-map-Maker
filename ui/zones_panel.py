"""Project border defaults and independent editable gameplay-zone controls."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QScrollArea,
    QSlider, QVBoxLayout, QWidget, QSizePolicy,
)

from core.project import Project, ZoneRegion
from ui.color_picker import choose_color
from ui.responsive import FitFormLayout, WrapButton, WrapCheckBox


class ZonesPanel(QWidget):
    defaultsChanged = pyqtSignal()

    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.project: Project | None = None
        self._loading = False
        self._build_ui()
        self.canvas.zoneSelectionChanged.connect(self._canvas_zone_selected)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(0)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content = QWidget()
        content.setMinimumWidth(0)
        content.setSizePolicy(QSizePolicy.Policy.Ignored,
                              QSizePolicy.Policy.Preferred)
        root = QVBoxLayout(content)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        defaults = QGroupBox("Project border defaults")
        form = FitFormLayout(defaults)
        self.btn_color = WrapButton("Choose border color…")
        self.btn_color.clicked.connect(self._pick_project_color)
        form.addRow("Color", self.btn_color)

        self.sl_opacity = QSlider(Qt.Orientation.Horizontal)
        self.sl_opacity.setRange(0, 100)
        self.sl_opacity.valueChanged.connect(self._apply_defaults)
        form.addRow("Opacity", self.sl_opacity)

        self.spin_width = QDoubleSpinBox()
        self.spin_width.setRange(0.5, 20.0)
        self.spin_width.setSingleStep(0.5)
        self.spin_width.setSuffix(" px")
        self.spin_width.valueChanged.connect(self._apply_defaults)
        form.addRow("Line width", self.spin_width)

        self.cmb_shape = QComboBox()
        self.cmb_shape.addItem("Image bounds", "bounds")
        self.cmb_shape.addItem("Alpha silhouette", "alpha")
        self.cmb_shape.currentIndexChanged.connect(self._apply_defaults)
        form.addRow("Node outline", self.cmb_shape)

        self.chk_canvas_nodes = WrapCheckBox("Show node outlines on canvas")
        self.chk_canvas_nodes.toggled.connect(self._apply_defaults)
        form.addRow(self.chk_canvas_nodes)
        self.chk_canvas_zones = WrapCheckBox("Show gameplay zones on canvas")
        self.chk_canvas_zones.toggled.connect(self._apply_defaults)
        form.addRow(self.chk_canvas_zones)
        self.chk_export_nodes = WrapCheckBox("Include node outlines in exports")
        self.chk_export_nodes.toggled.connect(self._apply_defaults)
        form.addRow(self.chk_export_nodes)
        self.chk_export_zones = WrapCheckBox("Include gameplay zones in exports")
        self.chk_export_zones.toggled.connect(self._apply_defaults)
        form.addRow(self.chk_export_zones)
        root.addWidget(defaults)

        zones_group = QGroupBox("Gameplay zones")
        zones_layout = QVBoxLayout(zones_group)
        self.list_zones = QListWidget()
        self.list_zones.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_zones.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list_zones.currentItemChanged.connect(self._list_zone_selected)
        zones_layout.addWidget(self.list_zones)

        tool_row = QHBoxLayout()
        self.btn_rect = WrapButton("Draw rectangle")
        self.btn_rect.clicked.connect(lambda: self.canvas.set_zone_tool("rectangle"))
        self.btn_poly = WrapButton("Draw polygon")
        self.btn_poly.clicked.connect(lambda: self.canvas.set_zone_tool("polygon"))
        tool_row.addWidget(self.btn_rect)
        tool_row.addWidget(self.btn_poly)
        zones_layout.addLayout(tool_row)
        action_row = QHBoxLayout()
        self.btn_finish = WrapButton("Finish polygon")
        self.btn_finish.clicked.connect(self.canvas.finish_zone_polygon)
        self.btn_delete = WrapButton("Delete zone")
        self.btn_delete.clicked.connect(self.canvas.delete_selected_zone)
        action_row.addWidget(self.btn_finish)
        action_row.addWidget(self.btn_delete)
        zones_layout.addLayout(action_row)
        self.lbl_help = QLabel(
            "Rectangle: drag on the canvas. Polygon: click vertices, then "
            "double-click, press Enter, or right-click to finish. Select a zone; "
            "drag its handles to reshape or drag an edge/body to move it.")
        self.lbl_help.setWordWrap(True)
        zones_layout.addWidget(self.lbl_help)
        root.addWidget(zones_group)

        self.zone_box = QGroupBox("Selected gameplay zone")
        zf = FitFormLayout(self.zone_box)
        self.edit_name = QLineEdit()
        self.edit_name.editingFinished.connect(self._rename_zone)
        zf.addRow("Name", self.edit_name)
        self.edit_label = QLineEdit()
        self.edit_label.setPlaceholderText("Uses the zone name when blank")
        self.edit_label.editingFinished.connect(self._zone_label_changed)
        zf.addRow("Canvas / export label", self.edit_label)
        self.chk_show_label = WrapCheckBox("Show label")
        self.chk_show_label.toggled.connect(self._zone_label_options_changed)
        zf.addRow(self.chk_show_label)
        self.chk_show_id = WrapCheckBox("Show short zone ID")
        self.chk_show_id.toggled.connect(self._zone_label_options_changed)
        zf.addRow(self.chk_show_id)
        self.cmb_zone_mode = QComboBox()
        self.cmb_zone_mode.addItem("Inherit project", "inherit")
        self.cmb_zone_mode.addItem("Override", "override")
        self.cmb_zone_mode.addItem("Off", "off")
        self.cmb_zone_mode.setToolTip(
            "Inherit project: use the project border defaults above. Override: this zone's own color and opacity (below). Off: no border.")
        self.cmb_zone_mode.currentIndexChanged.connect(self._zone_mode_changed)
        zf.addRow("Border", self.cmb_zone_mode)
        self.btn_zone_color = WrapButton("Use project border color")
        self.btn_zone_color.clicked.connect(self._pick_zone_color)
        zf.addRow("Override color", self.btn_zone_color)
        self.sl_zone_opacity = QSlider(Qt.Orientation.Horizontal)
        self.sl_zone_opacity.setRange(0, 100)
        self.sl_zone_opacity.valueChanged.connect(self._zone_opacity_changed)
        zf.addRow("Override opacity", self.sl_zone_opacity)
        self.list_edges = QListWidget()
        self.list_edges.setMaximumHeight(130)
        self.list_edges.itemChanged.connect(self._zone_edge_changed)
        zf.addRow("Visible segments", self.list_edges)
        root.addWidget(self.zone_box)
        root.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------
    def set_project(self, project: Project):
        self.project = project
        self._loading = True
        self.sl_opacity.setValue(round(project.border_opacity * 100))
        self.spin_width.setValue(project.border_width)
        index = self.cmb_shape.findData(project.node_border_shape)
        self.cmb_shape.setCurrentIndex(max(0, index))
        self.chk_canvas_nodes.setChecked(project.show_node_borders)
        self.chk_canvas_zones.setChecked(project.show_zones)
        self.chk_export_nodes.setChecked(project.export_node_borders)
        self.chk_export_zones.setChecked(project.export_zones)
        self._loading = False
        self._update_project_color_button()
        self.refresh_level()

    def refresh_level(self):
        selected = self.canvas.selected_zone_id
        self._loading = True
        self.list_zones.blockSignals(True)
        self.list_zones.clear()
        if self.canvas.level:
            for zone in self.canvas.level.zones:
                item = QListWidgetItem(zone.name)
                item.setToolTip(zone.name)
                item.setData(Qt.ItemDataRole.UserRole, zone.id)
                self.list_zones.addItem(item)
                if zone.id == selected:
                    self.list_zones.setCurrentItem(item)
        self.list_zones.blockSignals(False)
        self._loading = False
        self._load_zone(self.canvas.selected_zone)

    def _update_project_color_button(self):
        if not self.project:
            return
        color = QColor(self.project.border_color)
        if color.isValid():
            text_color = "#000000" if color.lightness() > 128 else "#ffffff"
            self.btn_color.setText(f"{color.name().upper()} — Change…")
            self.btn_color.setStyleSheet(
                f"background:{color.name()}; color:{text_color};")

    def _apply_defaults(self, *_):
        if self._loading or not self.project:
            return
        self.canvas.push_history("Border defaults")
        self.project.border_opacity = self.sl_opacity.value() / 100.0
        self.project.border_width = self.spin_width.value()
        self.project.node_border_shape = self.cmb_shape.currentData() or "bounds"
        self.project.show_node_borders = self.chk_canvas_nodes.isChecked()
        self.project.show_zones = self.chk_canvas_zones.isChecked()
        self.project.export_node_borders = self.chk_export_nodes.isChecked()
        self.project.export_zones = self.chk_export_zones.isChecked()
        self._load_zone(self.canvas.selected_zone)
        self.canvas.update()
        self.canvas.dirty.emit()
        self.defaultsChanged.emit()

    def _pick_project_color(self):
        if not self.project:
            return
        color = choose_color(QColor(self.project.border_color), self,
                             self.canvas, "Choose project border color")
        if not color.isValid():
            return
        self.canvas.push_history("Project border color")
        self.project.border_color = color.name()
        self._update_project_color_button()
        self._load_zone(self.canvas.selected_zone)
        self.canvas.update()
        self.canvas.dirty.emit()
        self.defaultsChanged.emit()

    # ------------------------------------------------------------------
    def _list_zone_selected(self, current, _previous=None):
        if self._loading or current is None:
            return
        self.canvas.select_zone(current.data(Qt.ItemDataRole.UserRole))

    def _canvas_zone_selected(self, _zone):
        if not self._loading:
            self.refresh_level()

    def _current_zone(self) -> ZoneRegion | None:
        return self.canvas.selected_zone

    def _load_zone(self, zone: ZoneRegion | None):
        self._loading = True
        self.zone_box.setVisible(zone is not None)
        self.list_edges.blockSignals(True)
        self.list_edges.clear()
        if zone is None:
            self.edit_name.clear()
            self.edit_label.clear()
            self.chk_show_label.setChecked(True)
            self.chk_show_id.setChecked(False)
            self.sl_zone_opacity.setValue(100)
            self.list_edges.blockSignals(False)
            self._loading = False
            return
        self.edit_name.setText(zone.name)
        self.edit_label.setText(zone.label)
        self.chk_show_label.setChecked(zone.show_label)
        self.chk_show_id.setChecked(zone.show_id)
        index = self.cmb_zone_mode.findData(zone.border_mode)
        self.cmb_zone_mode.setCurrentIndex(max(0, index))
        color = (zone.border_color if zone.border_mode == "override"
                 else self.project.border_color if self.project else "#69b7f5")
        self.sl_zone_opacity.setValue(round(
            (zone.border_opacity if zone.border_mode == "override"
             else self.project.border_opacity if self.project else 0.85) * 100))
        self._set_zone_color_button(color)
        self.btn_zone_color.setEnabled(zone.border_mode != "off")
        self.sl_zone_opacity.setEnabled(zone.border_mode != "off")
        for index, visible in enumerate(zone.edge_visible):
            item = QListWidgetItem(f"Segment {index + 1}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if visible
                               else Qt.CheckState.Unchecked)
            self.list_edges.addItem(item)
        self.list_edges.blockSignals(False)
        self._loading = False

    def _set_zone_color_button(self, value):
        color = QColor(value)
        if color.isValid():
            text_color = "#000000" if color.lightness() > 128 else "#ffffff"
            self.btn_zone_color.setText(f"{color.name().upper()} — Change…")
            self.btn_zone_color.setStyleSheet(
                f"background:{color.name()}; color:{text_color};")

    def _rename_zone(self):
        zone = self._current_zone()
        if not zone or self._loading:
            return
        name = self.edit_name.text().strip() or "Zone"
        if name == zone.name:
            return
        self.canvas.push_history("Rename zone")
        zone.name = name
        item = self.list_zones.currentItem()
        if item:
            item.setText(name)
        self.canvas.update()
        self.canvas.dirty.emit()

    def _zone_label_changed(self):
        zone = self._current_zone()
        if not zone or self._loading:
            return
        label = self.edit_label.text().strip()
        if label == zone.label:
            return
        self.canvas.push_history("Edit zone label")
        zone.label = label
        self.canvas.update()
        self.canvas.dirty.emit()

    def _zone_label_options_changed(self, *_):
        zone = self._current_zone()
        if not zone or self._loading:
            return
        show_label = self.chk_show_label.isChecked()
        show_id = self.chk_show_id.isChecked()
        if (show_label, show_id) == (zone.show_label, zone.show_id):
            return
        self.canvas.push_history("Zone label display")
        zone.show_label = show_label
        zone.show_id = show_id
        self.canvas.update()
        self.canvas.dirty.emit()

    def _zone_mode_changed(self, index):
        zone = self._current_zone()
        if not zone or self._loading or index < 0:
            return
        mode = self.cmb_zone_mode.itemData(index)
        if mode == zone.border_mode:
            return
        self.canvas.push_history("Zone border mode")
        if mode == "override" and zone.border_mode != "override":
            if self.project:
                zone.border_color = self.project.border_color
                zone.border_opacity = self.project.border_opacity
        zone.border_mode = mode
        self._load_zone(zone)
        self.canvas.update()
        self.canvas.dirty.emit()

    def _pick_zone_color(self):
        zone = self._current_zone()
        if not zone:
            return
        initial = zone.border_color if zone.border_mode == "override" else (
            self.project.border_color if self.project else "#69b7f5")
        color = choose_color(QColor(initial), self, self.canvas,
                             "Choose zone border color")
        if not color.isValid():
            return
        self.canvas.push_history("Zone border color")
        zone.border_mode = "override"
        zone.border_color = color.name()
        self._load_zone(zone)
        self.canvas.update()
        self.canvas.dirty.emit()

    def _zone_opacity_changed(self, value):
        zone = self._current_zone()
        if not zone or self._loading:
            return
        self.canvas.push_history("Zone border opacity")
        if zone.border_mode != "override":
            zone.border_mode = "override"
            zone.border_color = self.project.border_color if self.project else "#69b7f5"
        zone.border_opacity = value / 100.0
        self.canvas.update()
        self.canvas.dirty.emit()
        self._load_zone(zone)

    def _zone_edge_changed(self, item):
        zone = self._current_zone()
        if not zone or self._loading:
            return
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is None or not 0 <= index < len(zone.edge_visible):
            return
        value = item.checkState() == Qt.CheckState.Checked
        if zone.edge_visible[index] == value:
            return
        self.canvas.push_history("Zone segment visibility")
        zone.edge_visible[index] = value
        self.canvas.update()
        self.canvas.dirty.emit()
