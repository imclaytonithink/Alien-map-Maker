"""Right-hand properties panel: node / multi-node controls, text, tint,
layers, grid settings, and reference-floor overlay."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QFormLayout, QDoubleSpinBox, QSpinBox,
    QSlider, QCheckBox, QPushButton, QHBoxLayout, QGridLayout, QLabel,
    QComboBox, QLineEdit, QScrollArea, QTextEdit, QFileDialog, QSizePolicy,
)
from core.project import Piece, Project, snap_value
from core.render import compute_text_size
from ui.color_picker import choose_color

FONTS = ["Monospace", "Consolas", "Courier New", "Arial", "Times New Roman"]


class PropertiesPanel(QWidget):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.project: Optional[Project] = None
        self.pieces: list[Piece] = []
        self._text_history_open = False
        self._build_ui()
        self.edit_text.installEventFilter(self)
        self.canvas.selectionChanged.connect(self.load_selection)

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

        # ---- project-wide tint inherited by image nodes ----
        self.project_tint_box = QGroupBox("Project-wide tint")
        project_tint_form = QFormLayout(self.project_tint_box)
        self.btn_project_tint = QPushButton("Choose project tint…")
        self.btn_project_tint.clicked.connect(self._pick_project_tint)
        project_tint_form.addRow("Overlay", self.btn_project_tint)
        project_tint_row = QHBoxLayout()
        self.sl_project_tint = QSlider(Qt.Orientation.Horizontal)
        self.sl_project_tint.setRange(0, 100)
        self.sl_project_tint.setToolTip(
            "Tint strength used by image nodes set to Inherit project.")
        self.sl_project_tint.valueChanged.connect(self._project_tint_strength)
        project_tint_row.addWidget(self.sl_project_tint, 1)
        self.btn_clear_project_tint = QPushButton("Clear")
        self.btn_clear_project_tint.setMaximumWidth(60)
        self.btn_clear_project_tint.clicked.connect(self._clear_project_tint)
        project_tint_row.addWidget(self.btn_clear_project_tint)
        project_tint_form.addRow("Strength", project_tint_row)
        root.addWidget(self.project_tint_box)

        # ---- single node ----
        self.single = QGroupBox("Selected node")
        sf = QFormLayout(self.single)
        self.lbl_name = QLabel("(none)")
        self.lbl_name.setWordWrap(True)
        self.lbl_name.setMinimumWidth(0)
        self.lbl_name.setSizePolicy(QSizePolicy.Policy.Ignored,
                                    QSizePolicy.Policy.Preferred)
        sf.addRow("Name", self.lbl_name)
        self.lbl_node_type = QLabel("")
        self.lbl_node_type.setWordWrap(True)
        sf.addRow("Type", self.lbl_node_type)
        self.spin_x = QDoubleSpinBox(); self.spin_x.setRange(-100000, 100000)
        self.spin_y = QDoubleSpinBox(); self.spin_y.setRange(-100000, 100000)
        self.spin_x.valueChanged.connect(lambda v: self._set("x", v))
        self.spin_y.valueChanged.connect(lambda v: self._set("y", v))
        sf.addRow("X", self.spin_x); sf.addRow("Y", self.spin_y)
        self.spin_w = QDoubleSpinBox(); self.spin_w.setRange(1, 100000)
        self.spin_h = QDoubleSpinBox(); self.spin_h.setRange(1, 100000)
        self.spin_w.valueChanged.connect(lambda v: self._set_size("w", v))
        self.spin_h.valueChanged.connect(lambda v: self._set_size("h", v))
        sf.addRow("Width", self.spin_w); sf.addRow("Height", self.spin_h)
        rot_row = QHBoxLayout()
        self.spin_rot = QDoubleSpinBox(); self.spin_rot.setRange(-360, 360); self.spin_rot.setSuffix("°")
        self.spin_rot.valueChanged.connect(lambda v: self._set("rotation", v))
        b90 = QPushButton("⟲90"); b90.clicked.connect(lambda: self._rotate(90))
        b_90 = QPushButton("⟳90"); b_90.clicked.connect(lambda: self._rotate(-90))
        rot_row.addWidget(self.spin_rot); rot_row.addWidget(b90); rot_row.addWidget(b_90)
        sf.addRow("Rotation", rot_row)
        self.spin_scale = QDoubleSpinBox(); self.spin_scale.setRange(0.05, 10); self.spin_scale.setSingleStep(0.05)
        self.spin_scale.valueChanged.connect(lambda v: self._set("scale", v))
        sf.addRow("Scale", self.spin_scale)
        op_row = QHBoxLayout()
        self.sl_op = QSlider(Qt.Orientation.Horizontal); self.sl_op.setRange(0, 100); self.sl_op.setValue(100)
        self.sl_op.valueChanged.connect(lambda v: self._set("opacity", v / 100.0))
        op_row.addWidget(self.sl_op); sf.addRow("Opacity", op_row)
        self.chk_snap = QCheckBox("Snap to grid"); self.chk_snap.toggled.connect(lambda v: self._set("snap", v))
        sf.addRow(self.chk_snap)
        self.chk_lock = QCheckBox("Locked"); self.chk_lock.toggled.connect(lambda v: self._set("locked", v))
        sf.addRow(self.chk_lock)
        flip_row = QHBoxLayout()
        fh = QPushButton("Flip H"); fh.clicked.connect(lambda: self._toggle("flip_h"))
        fv = QPushButton("Flip V"); fv.clicked.connect(lambda: self._toggle("flip_v"))
        flip_row.addWidget(fh); flip_row.addWidget(fv); sf.addRow("Flip", flip_row)
        self.cmb_layer = QComboBox(); self.cmb_layer.currentIndexChanged.connect(self._set_layer)
        sf.addRow("Layer", self.cmb_layer)
        z_row = QHBoxLayout()
        bf = QPushButton("Front"); bf.clicked.connect(lambda: self._z("front"))
        bk = QPushButton("Back"); bk.clicked.connect(lambda: self._z("back"))
        bup = QPushButton("▲"); bup.clicked.connect(lambda: self.canvas._raise(self.pieces))
        bdn = QPushButton("▼"); bdn.clicked.connect(lambda: self.canvas._lower(self.pieces))
        z_row.addWidget(bf); z_row.addWidget(bk); z_row.addWidget(bup); z_row.addWidget(bdn)
        sf.addRow("Order", z_row)
        root.addWidget(self.single)

        self.image_tools_box = QGroupBox("Image tools")
        image_tools = QGridLayout(self.image_tools_box)
        self.btn_crop_image = QPushButton("Crop image…")
        self.btn_crop_image.setToolTip("Crop this image non-destructively.")
        self.btn_crop_image.clicked.connect(self._start_crop)
        self.btn_replace_image = QPushButton("Replace image…")
        self.btn_replace_image.setToolTip(
            "Choose a different source image for this node.")
        self.btn_replace_image.clicked.connect(self._replace_image)
        self.btn_reset_crop = QPushButton("Reset crop")
        self.btn_reset_crop.setToolTip("Restore the full source image.")
        self.btn_reset_crop.clicked.connect(self.canvas.reset_selected_crop)
        image_tools.addWidget(self.btn_crop_image, 0, 0)
        image_tools.addWidget(self.btn_replace_image, 0, 1)
        image_tools.addWidget(self.btn_reset_crop, 1, 0, 1, 2)
        root.addWidget(self.image_tools_box)

        # ---- editable text node controls ----
        self.text_box = QGroupBox("Editable text node")
        tf = QFormLayout(self.text_box)
        self.lbl_text_help = QLabel(
            "This is an editable text node, separate from lettering baked into a source image.")
        self.lbl_text_help.setWordWrap(True)
        tf.addRow(self.lbl_text_help)
        self.edit_text = QTextEdit(); self.edit_text.setMaximumHeight(90)
        self.edit_text.textChanged.connect(self._text_changed)
        tf.addRow("Text", self.edit_text)
        self.cmb_font = QComboBox(); self.cmb_font.addItems(FONTS)
        self.cmb_font.currentTextChanged.connect(self._font_changed)
        tf.addRow("Font", self.cmb_font)
        self.spin_fsize = QSpinBox(); self.spin_fsize.setRange(6, 400); self.spin_fsize.setValue(24)
        self.spin_fsize.valueChanged.connect(self._fsize_changed)
        tf.addRow("Size", self.spin_fsize)
        style_row = QHBoxLayout()
        self.chk_bold = QCheckBox("Bold"); self.chk_bold.toggled.connect(self._bold_changed)
        self.chk_italic = QCheckBox("Italic"); self.chk_italic.toggled.connect(self._italic_changed)
        self.chk_underline = QCheckBox("Underline")
        self.chk_underline.toggled.connect(self._underline_changed)
        style_row.addWidget(self.chk_bold)
        style_row.addWidget(self.chk_italic)
        style_row.addWidget(self.chk_underline)
        tf.addRow("Style", style_row)
        self.cmb_text_halign = QComboBox()
        self.cmb_text_halign.addItem("Left", "left")
        self.cmb_text_halign.addItem("Center", "center")
        self.cmb_text_halign.addItem("Right", "right")
        self.cmb_text_halign.currentIndexChanged.connect(self._text_alignment_changed)
        tf.addRow("Horizontal", self.cmb_text_halign)
        self.cmb_text_valign = QComboBox()
        self.cmb_text_valign.addItem("Top", "top")
        self.cmb_text_valign.addItem("Center", "center")
        self.cmb_text_valign.addItem("Bottom", "bottom")
        self.cmb_text_valign.currentIndexChanged.connect(self._text_alignment_changed)
        tf.addRow("Vertical", self.cmb_text_valign)
        self.chk_text_auto_size = QCheckBox("Auto-fit text box")
        self.chk_text_auto_size.toggled.connect(self._text_auto_size_changed)
        tf.addRow(self.chk_text_auto_size)
        dimensions = QHBoxLayout()
        self.spin_text_width = QSpinBox(); self.spin_text_width.setRange(10, 10000)
        self.spin_text_width.setSuffix(" px")
        self.spin_text_width.valueChanged.connect(
            lambda value: self._text_dimension_changed("w", value))
        self.spin_text_height = QSpinBox(); self.spin_text_height.setRange(10, 10000)
        self.spin_text_height.setSuffix(" px")
        self.spin_text_height.valueChanged.connect(
            lambda value: self._text_dimension_changed("h", value))
        dimensions.addWidget(self.spin_text_width)
        dimensions.addWidget(QLabel("×"))
        dimensions.addWidget(self.spin_text_height)
        tf.addRow("Box size", dimensions)
        self.spin_text_padding = QSpinBox(); self.spin_text_padding.setRange(0, 100)
        self.spin_text_padding.valueChanged.connect(self._text_padding_changed)
        tf.addRow("Padding", self.spin_text_padding)
        self.btn_textcol = QPushButton("Choose text color…")
        self.btn_textcol.clicked.connect(self._pick_text_color)
        tf.addRow("Text color", self.btn_textcol)
        bg_row = QHBoxLayout()
        self.chk_text_bg = QCheckBox("Background")
        self.chk_text_bg.toggled.connect(self._text_background_toggled)
        self.btn_text_bg = QPushButton("Choose fill…")
        self.btn_text_bg.clicked.connect(self._pick_text_background)
        self.btn_clear_text_bg = QPushButton("Clear")
        self.btn_clear_text_bg.setMaximumWidth(55)
        self.btn_clear_text_bg.clicked.connect(self._clear_text_background)
        bg_row.addWidget(self.chk_text_bg)
        bg_row.addWidget(self.btn_text_bg, 1)
        bg_row.addWidget(self.btn_clear_text_bg)
        tf.addRow(bg_row)
        self.sl_text_bg_opacity = QSlider(Qt.Orientation.Horizontal)
        self.sl_text_bg_opacity.setRange(0, 100)
        self.sl_text_bg_opacity.setValue(85)
        self.sl_text_bg_opacity.valueChanged.connect(self._text_background_opacity_changed)
        tf.addRow("Fill opacity", self.sl_text_bg_opacity)
        root.addWidget(self.text_box)

        # ---- PNG recolor overlay: available for a single piece or a selection ----
        self.tint_box = QGroupBox("Tint / recolor PNGs")
        tint_form = QFormLayout(self.tint_box)
        self.cmb_tint_mode = QComboBox()
        self.cmb_tint_mode.addItem("Inherit project", "inherit")
        self.cmb_tint_mode.addItem("Override", "override")
        self.cmb_tint_mode.addItem("Original / no tint", "original")
        self.cmb_tint_mode.addItem("Mixed selection", None)
        self.cmb_tint_mode.currentIndexChanged.connect(self._tint_mode_changed)
        tint_form.addRow("Mode", self.cmb_tint_mode)
        self.btn_tint = QPushButton("Choose overlay color…")
        self.btn_tint.clicked.connect(self._pick_tint)
        tint_form.addRow("Overlay", self.btn_tint)
        self.sl_tint = QSlider(Qt.Orientation.Horizontal)
        self.sl_tint.setRange(0, 100)
        self.sl_tint.setValue(0)
        self.sl_tint.setToolTip("0% removes the overlay; 100% applies the full color.")
        self.sl_tint.valueChanged.connect(self._tint_strength)
        tint_form.addRow("Strength", self.sl_tint)
        root.addWidget(self.tint_box)

        # ---- optional outline for an individual image node ----
        self.node_border_box = QGroupBox("Node outline")
        bf = QFormLayout(self.node_border_box)
        self.cmb_border_mode = QComboBox()
        self.cmb_border_mode.addItem("Inherit project", "inherit")
        self.cmb_border_mode.addItem("Override", "override")
        self.cmb_border_mode.addItem("Off", "off")
        self.cmb_border_mode.currentIndexChanged.connect(self._border_mode_changed)
        bf.addRow("Mode", self.cmb_border_mode)
        self.cmb_border_shape = QComboBox()
        self.cmb_border_shape.addItem("Use project shape", "inherit")
        self.cmb_border_shape.addItem("Image bounds", "bounds")
        self.cmb_border_shape.addItem("Alpha silhouette", "alpha")
        self.cmb_border_shape.currentIndexChanged.connect(self._border_shape_changed)
        bf.addRow("Outline shape", self.cmb_border_shape)
        self.btn_node_border_color = QPushButton("Use project border color")
        self.btn_node_border_color.clicked.connect(self._pick_node_border_color)
        bf.addRow("Override color", self.btn_node_border_color)
        self.sl_node_border_opacity = QSlider(Qt.Orientation.Horizontal)
        self.sl_node_border_opacity.setRange(0, 100)
        self.sl_node_border_opacity.valueChanged.connect(self._node_border_opacity_changed)
        bf.addRow("Override opacity", self.sl_node_border_opacity)
        edge_row = QHBoxLayout()
        self.node_border_edges = []
        for label in ("Top", "Right", "Bottom", "Left"):
            check = QCheckBox(label)
            check.toggled.connect(
                lambda value, i=len(self.node_border_edges):
                    self._node_border_edge_changed(i, value))
            self.node_border_edges.append(check)
            edge_row.addWidget(check)
        bf.addRow("Bounds sides", edge_row)
        root.addWidget(self.node_border_box)

        # ---- separate cover patch for rasterized image lettering ----
        self.patch_box = QGroupBox("Non-destructive raster-label patch")
        pf = QFormLayout(self.patch_box)
        self.lbl_patch_help = QLabel(
            "This fill is a separate node over the image; it never changes the source file. "
            "Sample nearby artwork for a matching color, then add editable text above it.")
        self.lbl_patch_help.setWordWrap(True)
        pf.addRow(self.lbl_patch_help)
        self.btn_patch_color = QPushButton("Choose patch fill…")
        self.btn_patch_color.clicked.connect(self._pick_patch_color)
        pf.addRow("Fill color", self.btn_patch_color)
        self.sl_patch_opacity = QSlider(Qt.Orientation.Horizontal)
        self.sl_patch_opacity.setRange(0, 100)
        self.sl_patch_opacity.setValue(100)
        self.sl_patch_opacity.valueChanged.connect(self._patch_opacity_changed)
        pf.addRow("Fill opacity", self.sl_patch_opacity)
        self.btn_patch_add_text = QPushButton("Add editable text over patch")
        self.btn_patch_add_text.clicked.connect(self._add_text_over_patch)
        pf.addRow(self.btn_patch_add_text)
        root.addWidget(self.patch_box)

        self.scale_box = QGroupBox("Static scale bar")
        scale_form = QFormLayout(self.scale_box)
        self.spin_scale_distance = QDoubleSpinBox()
        self.spin_scale_distance.setRange(0.01, 1_000_000)
        self.spin_scale_distance.setDecimals(2)
        self.spin_scale_distance.valueChanged.connect(self._scale_distance_changed)
        scale_form.addRow("Label distance", self.spin_scale_distance)
        self.cmb_scale_units = QComboBox()
        for label, value in (("Feet", "ft"), ("Meters", "m"),
                             ("Kilometers", "km"), ("Miles", "mi"),
                             ("Squares", "squares"), ("Custom", "custom")):
            self.cmb_scale_units.addItem(label, value)
        self.cmb_scale_units.currentIndexChanged.connect(self._scale_units_changed)
        scale_form.addRow("Units", self.cmb_scale_units)
        self.edit_scale_caption = QLineEdit()
        self.edit_scale_caption.setPlaceholderText("Auto: distance + units")
        self.edit_scale_caption.editingFinished.connect(self._scale_caption_changed)
        scale_form.addRow("Custom caption", self.edit_scale_caption)
        self.btn_scale_color = QPushButton("Scale color…")
        self.btn_scale_color.clicked.connect(self._pick_scale_color)
        scale_form.addRow("Color", self.btn_scale_color)
        root.addWidget(self.scale_box)

        self.connector_box = QGroupBox("Connection / transition marker")
        connector_form = QFormLayout(self.connector_box)
        self.edit_connector_label = QLineEdit()
        self.edit_connector_label.setPlaceholderText("e.g. To level 2 / airlock")
        self.edit_connector_label.editingFinished.connect(self._connector_label_changed)
        connector_form.addRow("Label", self.edit_connector_label)
        self.btn_connector_color = QPushButton("Marker color…")
        self.btn_connector_color.clicked.connect(self._pick_connector_color)
        connector_form.addRow("Color", self.btn_connector_color)
        self.chk_connector_arrow = QCheckBox("Arrow at endpoint")
        self.chk_connector_arrow.toggled.connect(self._connector_arrow_changed)
        connector_form.addRow(self.chk_connector_arrow)
        self.spin_connector_width = QDoubleSpinBox()
        self.spin_connector_width.setRange(0.5, 30)
        self.spin_connector_width.setSingleStep(0.5)
        self.spin_connector_width.valueChanged.connect(self._connector_width_changed)
        connector_form.addRow("Line width", self.spin_connector_width)
        root.addWidget(self.connector_box)

        # ---- multi piece ----
        self.multi = QGroupBox("Multiple selected")
        mf = QFormLayout(self.multi)
        self.lbl_count = QLabel("0")
        mf.addRow("Count", self.lbl_count)
        align = QHBoxLayout()
        align.setContentsMargins(0, 0, 0, 0)
        align.setSpacing(3)
        align_actions = [
            ("L", "Align left edges", "left"),
            ("R", "Align right edges", "right"),
            ("T", "Align top edges", "top"),
            ("B", "Align bottom edges", "bottom"),
            ("H", "Align horizontal centers", "hcenter"),
            ("V", "Align vertical centers", "vcenter"),
        ]
        for label, tooltip, action in align_actions:
            button = QPushButton(label)
            button.setObjectName("PropertyIconButton")
            button.setFixedSize(34, 32)
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            button.clicked.connect(
                lambda _checked=False, fn=action: self.canvas.align(fn))
            align.addWidget(button)
        mf.addRow("Align", align)
        dist = QHBoxLayout()
        dh = QPushButton("Horizontal")
        dh.setToolTip("Space selected nodes evenly from left to right.")
        dh.clicked.connect(lambda: self.canvas.distribute("h"))
        dv = QPushButton("Vertical")
        dv.setToolTip("Space selected nodes evenly from top to bottom.")
        dv.clicked.connect(lambda: self.canvas.distribute("v"))
        dist.addWidget(dh)
        dist.addWidget(dv)
        mf.addRow("Distribute", dist)
        self.chk_allow_overlap = QCheckBox("Allow overlap")
        self.chk_allow_overlap.setToolTip(
            "Off (default): align and distribute never leave nodes overlapping — "
            "colliding nodes are stacked side by side instead.")
        self.chk_allow_overlap.toggled.connect(self._allow_overlap_toggled)
        mf.addRow(self.chk_allow_overlap)
        grp = QHBoxLayout()
        bg = QPushButton("Group"); bg.clicked.connect(self.canvas.group)
        bu = QPushButton("Ungroup"); bu.clicked.connect(self.canvas.ungroup)
        grp.addWidget(bg); grp.addWidget(bu); mf.addRow("Glue", grp)
        root.addWidget(self.multi)

        # ---- grid ----
        g = QGroupBox("Canvas size & grid")
        gf = QFormLayout(g)
        self.chk_grid = QCheckBox("Show grid"); self.chk_grid.toggled.connect(self._apply_grid)
        gf.addRow(self.chk_grid)
        self.chk_centerlines = QCheckBox("Centerlines (through each square)")
        self.chk_centerlines.setToolTip(
            "Draw a faint dashed line through the middle of every grid square. "
            "Node centers also snap to them.")
        self.chk_centerlines.toggled.connect(self._apply_grid)
        gf.addRow(self.chk_centerlines)
        self.sl_gop = QSlider(Qt.Orientation.Horizontal); self.sl_gop.setRange(0, 100); self.sl_gop.setValue(50)
        self.sl_gop.valueChanged.connect(self._apply_grid)
        gf.addRow("Grid opacity", self.sl_gop)
        self.cmb_style = QComboBox(); self.cmb_style.addItems(["solid", "dashed", "dotted"])
        self.cmb_style.currentTextChanged.connect(self._apply_grid)
        gf.addRow("Grid style", self.cmb_style)
        self.spin_major = QSpinBox(); self.spin_major.setRange(1, 50); self.spin_major.setValue(5)
        self.spin_major.valueChanged.connect(self._apply_grid)
        gf.addRow("Major every", self.spin_major)
        self.spin_cell = QSpinBox(); self.spin_cell.setRange(1, 1000); self.spin_cell.setValue(70)
        self.spin_cell.setToolTip(
            "Pixel width and height of one grid square. Change this to alter "
            "the map's pixel dimensions without changing its square count.")
        self.spin_cell.valueChanged.connect(self._apply_grid)
        gf.addRow("Square size (px)", self.spin_cell)
        self.cmb_feet = QComboBox(); self.cmb_feet.addItems(["5 ft", "10 ft"])
        self.cmb_feet.currentTextChanged.connect(self._apply_grid)
        gf.addRow("Square =", self.cmb_feet)
        self.btn_color = QPushButton("Grid color"); self.btn_color.clicked.connect(self._pick_color)
        gf.addRow(self.btn_color)
        row = QHBoxLayout()
        self.spin_cols = QSpinBox(); self.spin_cols.setRange(1, 200); self.spin_cols.setValue(30)
        self.spin_rows = QSpinBox(); self.spin_rows.setRange(1, 200); self.spin_rows.setValue(30)
        self.spin_cols.setToolTip("Canvas width in grid squares.")
        self.spin_rows.setToolTip("Canvas height in grid squares.")
        self.spin_cols.valueChanged.connect(self._apply_size)
        self.spin_rows.valueChanged.connect(self._apply_size)
        row.addWidget(self.spin_cols); row.addWidget(QLabel("×")); row.addWidget(self.spin_rows)
        gf.addRow("Canvas size (squares)", row)
        self.lbl_canvas_dimensions = QLabel("")
        self.lbl_canvas_dimensions.setWordWrap(True)
        gf.addRow("Canvas pixels", self.lbl_canvas_dimensions)
        root.addWidget(g)

        # ---- reference overlay ----
        r = QGroupBox("Reference floor overlay")
        rf = QFormLayout(r)
        self.chk_ref = QCheckBox("Show reference floor"); self.chk_ref.toggled.connect(self._apply_ref)
        rf.addRow(self.chk_ref)
        self.cmb_ref = QComboBox(); self.cmb_ref.addItems(["Floor below", "Floor above"])
        self.cmb_ref.currentIndexChanged.connect(self._apply_ref)
        rf.addRow("Reference", self.cmb_ref)
        self.sl_ref = QSlider(Qt.Orientation.Horizontal); self.sl_ref.setRange(5, 80); self.sl_ref.setValue(28)
        self.sl_ref.valueChanged.connect(self._apply_ref)
        rf.addRow("Opacity", self.sl_ref)
        root.addWidget(r)

        root.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------
    def set_project(self, project: Project):
        self.project = project
        self.sl_project_tint.blockSignals(True)
        self.sl_project_tint.setValue(int(round(project.tint_strength * 100)))
        self.sl_project_tint.blockSignals(False)
        self._update_project_tint_button()
        grid_controls = (self.chk_grid, self.chk_centerlines, self.sl_gop,
                         self.cmb_style, self.spin_major)
        for control in grid_controls:
            control.blockSignals(True)
        self.chk_grid.setChecked(project.show_grid)
        self.chk_centerlines.setChecked(project.show_centerlines)
        self.sl_gop.setValue(int(project.grid_opacity * 100))
        self.cmb_style.setCurrentText(project.grid_style)
        self.spin_major.setValue(project.grid_major)
        for control in grid_controls:
            control.blockSignals(False)
        self.refresh_canvas_size()
        self._update_color_btn()
        self._refresh_layers()

    def refresh_canvas_size(self):
        """Refresh size controls after project load, resize, or history restore."""
        if not self.project:
            return
        controls = (self.spin_cell, self.cmb_feet, self.spin_cols, self.spin_rows)
        for control in controls:
            control.blockSignals(True)
        self.spin_cell.setValue(self.project.cell_size)
        self.cmb_feet.setCurrentText(f"{self.project.feet_per_square} ft")
        self.spin_cols.setValue(self.project.map_cols)
        self.spin_rows.setValue(self.project.map_rows)
        for control in controls:
            control.blockSignals(False)
        self._update_canvas_dimensions()

    def _update_canvas_dimensions(self):
        if not self.project:
            return
        self.lbl_canvas_dimensions.setText(
            f"{self.project.canvas_w:,} × {self.project.canvas_h:,} px")

    def _update_project_tint_button(self):
        if not self.project:
            self.btn_project_tint.setText("Choose project tint…")
            self.btn_project_tint.setStyleSheet("")
            return
        color = QColor(self.project.tint_color) if self.project.tint_color else QColor()
        if color.isValid():
            text_color = "#000000" if color.lightness() > 128 else "#ffffff"
            self.btn_project_tint.setText(f"{color.name().upper()} — Change…")
            self.btn_project_tint.setStyleSheet(
                f"background:{color.name()}; color:{text_color};")
        else:
            self.btn_project_tint.setText("Choose project tint…")
            self.btn_project_tint.setStyleSheet("")

    def _refresh_layers(self):
        self.cmb_layer.blockSignals(True)
        self.cmb_layer.clear()
        if self.project and self.canvas.level:
            for l in self.canvas.level.layers:
                self.cmb_layer.addItem(l.name, l.id)
        self.cmb_layer.blockSignals(False)

    def _update_color_btn(self):
        c = QColor(self.project.grid_color)
        self.btn_color.setStyleSheet(f"background:{c.name()}; color:{'#000' if c.lightness()>128 else '#fff'}")

    # ------------------------------------------------------------------
    def load_selection(self, pieces: list[Piece]):
        self.pieces = pieces or []
        n = len(self.pieces)
        single_piece = self.pieces[0] if n == 1 else None
        raster_image = bool(single_piece and not (
            single_piece.is_text or single_piece.is_patch
            or single_piece.is_scale_bar or single_piece.is_connector))
        self.single.setVisible(n == 1)
        self.image_tools_box.setVisible(raster_image)
        self.btn_reset_crop.setEnabled(bool(
            raster_image and single_piece.crop_rect != [0.0, 0.0, 1.0, 1.0]))
        self.text_box.setVisible(n == 1 and self.pieces[0].is_text)
        self.patch_box.setVisible(n == 1 and self.pieces[0].is_patch)
        self.scale_box.setVisible(n == 1 and self.pieces[0].is_scale_bar)
        self.connector_box.setVisible(n == 1 and self.pieces[0].is_connector)
        self.node_border_box.setVisible(raster_image)
        self.multi.setVisible(n >= 2)
        tint_pieces = [p for p in self.pieces
                       if not (p.is_text or p.is_patch or p.is_scale_bar
                               or p.is_connector)]
        self.tint_box.setVisible(bool(tint_pieces))
        self.sl_tint.blockSignals(True)
        self.cmb_tint_mode.blockSignals(True)
        if tint_pieces:
            modes = {getattr(piece, "tint_mode", "inherit")
                     for piece in tint_pieces}
            mode = next(iter(modes)) if len(modes) == 1 else None
            mode_index = self.cmb_tint_mode.findData(mode)
            self.cmb_tint_mode.setCurrentIndex(
                mode_index if mode_index >= 0 else 3)
            self.cmb_tint_mode.setEnabled(True)

            tint_piece = tint_pieces[0]
            if mode == "override":
                shown_strength = tint_piece.tint_strength
                shown_color = tint_piece.tint_color
            elif mode == "inherit":
                shown_strength = self.project.tint_strength if self.project else 0
                shown_color = self.project.tint_color if self.project else ""
            elif mode == "original":
                shown_strength = 0
                shown_color = ""
            else:
                shown_strength = tint_piece.tint_strength
                shown_color = tint_piece.tint_color
            self.sl_tint.setValue(int(round(shown_strength * 100)))
            tint_color = QColor(shown_color) if shown_color else QColor()
            if tint_color.isValid():
                text_color = "#000000" if tint_color.lightness() > 128 else "#ffffff"
                self.btn_tint.setText(f"{tint_color.name().upper()} — Change…")
                self.btn_tint.setStyleSheet(
                    f"background:{tint_color.name()}; color:{text_color};")
            else:
                self.btn_tint.setText("Choose override color…")
                self.btn_tint.setStyleSheet("")
        else:
            self.cmb_tint_mode.setCurrentIndex(0)
            self.cmb_tint_mode.setEnabled(False)
            self.sl_tint.setValue(0)
            self.btn_tint.setText("Choose override color…")
            self.btn_tint.setStyleSheet("")
        self.cmb_tint_mode.blockSignals(False)
        self.sl_tint.blockSignals(False)
        self._refresh_layers()
        if n == 1:
            p = self.pieces[0]
            if not (p.is_text or p.is_patch or p.is_scale_bar or p.is_connector):
                self._load_node_border(p)
            if p.is_text:
                self.lbl_node_type.setText("Editable text node")
            elif p.is_patch:
                self.lbl_node_type.setText(
                    "Patch overlay — covers rasterized artwork without changing the source image.")
            elif p.is_scale_bar:
                self.lbl_node_type.setText(
                    "Static scale bar — its saved pixel length and label stay fixed if grid calibration changes.")
            elif p.is_connector:
                self.lbl_node_type.setText("Connection / transition marker")
            else:
                self.lbl_node_type.setText(
                    "Image node — lettering inside the source image is rasterized, "
                    "not editable text.")
            self.lbl_name.setText(p.name)
            self.lbl_name.setToolTip(p.name)
            position_controls = (self.spin_x, self.spin_y, self.spin_rot,
                                 self.spin_scale, self.sl_op,
                                 self.spin_w, self.spin_h)
            for w in position_controls:
                w.blockSignals(True); w.setEnabled(True)
            for w in (self.chk_snap, self.chk_lock, self.cmb_layer):
                w.blockSignals(True)
            self.chk_snap.setEnabled(True); self.chk_lock.setEnabled(True)
            self.spin_x.setValue(p.x); self.spin_y.setValue(p.y)
            self.spin_w.setValue(p.vis_w); self.spin_h.setValue(p.vis_h)
            self.spin_rot.setValue(p.rotation); self.spin_scale.setValue(p.scale)
            self.sl_op.setValue(int(p.opacity * 100))
            self.chk_snap.setChecked(p.snap); self.chk_lock.setChecked(p.locked)
            idx = self.cmb_layer.findData(p.layer)
            if idx >= 0:
                self.cmb_layer.setCurrentIndex(idx)
            if p.is_text:
                self._load_text_controls(p)
            elif p.is_patch:
                self._load_patch_controls(p)
            elif p.is_scale_bar:
                self._load_scale_controls(p)
            elif p.is_connector:
                self._load_connector_controls(p)
            for w in position_controls:
                w.blockSignals(False)
            for w in (self.chk_snap, self.chk_lock, self.cmb_layer):
                w.blockSignals(False)
        elif n == 0:
            self.lbl_name.setText("(none)")
            self.lbl_name.setToolTip("")
            self.lbl_node_type.setText("")
            for w in (self.spin_x, self.spin_y, self.spin_rot, self.spin_scale, self.sl_op,
                      self.spin_w, self.spin_h):
                w.blockSignals(True); w.setEnabled(False)
            self.chk_snap.setEnabled(False); self.chk_lock.setEnabled(False)
        else:
            self.lbl_count.setText(str(n))

    def _load_node_border(self, piece):
        controls = [self.cmb_border_mode, self.cmb_border_shape,
                    self.btn_node_border_color, self.sl_node_border_opacity,
                    *self.node_border_edges]
        for control in controls:
            control.blockSignals(True)
        mode_index = self.cmb_border_mode.findData(piece.border_mode)
        self.cmb_border_mode.setCurrentIndex(max(0, mode_index))
        shape_index = self.cmb_border_shape.findData(piece.border_shape)
        self.cmb_border_shape.setCurrentIndex(max(0, shape_index))
        mode = piece.border_mode
        color_name = (piece.border_color if mode == "override"
                      else self.project.border_color if self.project
                      else "#69b7f5")
        opacity = (piece.border_opacity if mode == "override"
                   else self.project.border_opacity if self.project else 0.85)
        self.sl_node_border_opacity.setValue(round(opacity * 100))
        color = QColor(color_name)
        if color.isValid():
            text_color = "#000000" if color.lightness() > 128 else "#ffffff"
            self.btn_node_border_color.setText(
                f"{color.name().upper()} — Change…")
            self.btn_node_border_color.setStyleSheet(
                f"background:{color.name()}; color:{text_color};")
        active = mode != "off"
        self.btn_node_border_color.setEnabled(active)
        self.sl_node_border_opacity.setEnabled(active)
        shape = piece.border_shape
        if shape == "inherit" and self.project:
            shape = self.project.node_border_shape
        for index, check in enumerate(self.node_border_edges):
            check.setChecked(piece.border_edges[index])
            check.setEnabled(active and shape == "bounds")
        for control in controls:
            control.blockSignals(False)

    def _border_mode_changed(self, index):
        if not self.pieces or index < 0:
            return
        mode = self.cmb_border_mode.itemData(index)
        piece = self.pieces[0]
        if mode == piece.border_mode:
            return
        self.canvas.push_history("Node border mode")
        if mode == "override" and piece.border_mode != "override" and self.project:
            piece.border_color = self.project.border_color
            piece.border_opacity = self.project.border_opacity
        piece.border_mode = mode
        self._load_node_border(piece)
        self.canvas.update(); self.canvas.dirty.emit()

    def _border_shape_changed(self, index):
        if not self.pieces or index < 0:
            return
        shape = self.cmb_border_shape.itemData(index)
        piece = self.pieces[0]
        if shape == piece.border_shape:
            return
        self.canvas.push_history("Node border shape")
        piece.border_shape = shape
        self._load_node_border(piece)
        self.canvas.update(); self.canvas.dirty.emit()

    def _pick_node_border_color(self):
        if not self.pieces:
            return
        piece = self.pieces[0]
        initial = (piece.border_color if piece.border_mode == "override"
                   else self.project.border_color if self.project else "#69b7f5")
        color = choose_color(QColor(initial), self, self.canvas,
                             "Choose node border color")
        if not color.isValid():
            return
        self.canvas.push_history("Node border color")
        piece.border_mode = "override"
        piece.border_color = color.name()
        self._load_node_border(piece)
        self.canvas.update(); self.canvas.dirty.emit()

    def _node_border_opacity_changed(self, value):
        if not self.pieces:
            return
        piece = self.pieces[0]
        if piece.border_mode != "override":
            piece.border_mode = "override"
            if self.project:
                piece.border_color = self.project.border_color
        self.canvas.push_history("Node border opacity", coalesce=True)
        piece.border_opacity = value / 100.0
        self._load_node_border(piece)
        self.canvas.update(); self.canvas.dirty.emit()

    def _node_border_edge_changed(self, index, visible):
        if not self.pieces:
            return
        piece = self.pieces[0]
        if piece.border_edges[index] == visible:
            return
        self.canvas.push_history("Node border sides")
        piece.border_edges[index] = visible
        self.canvas.update(); self.canvas.dirty.emit()

    # ------------------------------------------------------------------
    def _set(self, attr, value):
        if not self.pieces:
            return
        self.canvas.push_history(f"Edit {attr}", coalesce=True)
        for p in self.pieces:
            setattr(p, attr, value)
            if attr in ("x", "y") and p.snap and self.project:
                setattr(p, attr, snap_value(value, self.project.cell_size))
                getattr(self, f"spin_{attr}").blockSignals(True)
                getattr(self, f"spin_{attr}").setValue(getattr(p, attr))
                getattr(self, f"spin_{attr}").blockSignals(False)
        self.canvas.update(); self.canvas.dirty.emit()

    def _allow_overlap_toggled(self, on):
        main = self.window()
        if hasattr(main, "_set_allow_overlap"):
            main._set_allow_overlap(on)
        else:
            self.canvas.allow_overlap = bool(on)

    def _set_size(self, axis, visual_value):
        """Set the on-canvas width/height (post-scale), keeping the top-left."""
        if len(self.pieces) != 1:
            return
        p = self.pieces[0]
        self.canvas.push_history(f"Edit size {axis}", coalesce=True)
        setattr(p, axis, visual_value / max(p.scale, 1e-6))
        if p.is_text:
            p.text_auto_size = False
        self.canvas.update(); self.canvas.dirty.emit()

    def _rotate(self, delta):
        if not self.pieces:
            return
        self.canvas.push_history("Rotate")
        for p in self.pieces:
            p.rotation = (p.rotation + delta) % 360
        self.spin_rot.blockSignals(True); self.spin_rot.setValue(self.pieces[0].rotation); self.spin_rot.blockSignals(False)
        self.canvas.update(); self.canvas.dirty.emit()

    def _toggle(self, attr):
        if not self.pieces:
            return
        self.canvas.push_history("Flip")
        for p in self.pieces:
            setattr(p, attr, not getattr(p, attr))
        self.canvas.update(); self.canvas.dirty.emit()

    def _set_layer(self, idx):
        if not self.pieces:
            return
        lid = self.cmb_layer.itemData(idx)
        self.canvas.push_history("Layer")
        for p in self.pieces:
            p.layer = lid
        self.canvas.dirty.emit()

    def _z(self, where):
        if not self.pieces or not self.canvas.level:
            return
        self.canvas.push_history("Order")
        pieces = self.canvas.level.pieces
        pieces.sort(key=lambda q: q.z)
        if where == "front":
            z = max(p.z for p in pieces) + 1
            for p in self.pieces:
                p.z = z
        else:
            z = min(p.z for p in pieces) - 1
            for p in self.pieces:
                p.z = z
        self.canvas.update(); self.canvas.dirty.emit()

    # ---- editable text node ----
    def eventFilter(self, obj, event):
        if obj is self.edit_text and event.type() == QEvent.Type.FocusOut:
            self._text_history_open = False
        return super().eventFilter(obj, event)

    def _text_piece(self) -> Piece | None:
        if len(self.pieces) == 1 and self.pieces[0].is_text:
            return self.pieces[0]
        return None

    def _load_text_controls(self, piece):
        controls = [
            self.edit_text, self.cmb_font, self.spin_fsize, self.chk_bold,
            self.chk_italic, self.chk_underline, self.cmb_text_halign,
            self.cmb_text_valign, self.chk_text_auto_size,
            self.spin_text_width, self.spin_text_height, self.spin_text_padding,
            self.btn_textcol, self.chk_text_bg, self.btn_text_bg,
            self.btn_clear_text_bg, self.sl_text_bg_opacity,
        ]
        for control in controls:
            control.blockSignals(True)
        self.edit_text.setPlainText(piece.text)
        self.cmb_font.setCurrentText(piece.font_family)
        self.spin_fsize.setValue(piece.font_size)
        self.chk_bold.setChecked(piece.font_bold)
        self.chk_italic.setChecked(piece.font_italic)
        self.chk_underline.setChecked(piece.font_underline)
        self.cmb_text_halign.setCurrentIndex(max(
            0, self.cmb_text_halign.findData(piece.text_halign)))
        self.cmb_text_valign.setCurrentIndex(max(
            0, self.cmb_text_valign.findData(piece.text_valign)))
        self.chk_text_auto_size.setChecked(piece.text_auto_size)
        self.spin_text_width.setValue(round(piece.w))
        self.spin_text_height.setValue(round(piece.h))
        self.spin_text_width.setEnabled(not piece.text_auto_size)
        self.spin_text_height.setEnabled(not piece.text_auto_size)
        self.spin_text_padding.setValue(piece.text_padding)
        self.sl_text_bg_opacity.setValue(round(piece.text_background_opacity * 100))
        self.chk_text_bg.setChecked(bool(piece.text_background_color))
        self.btn_text_bg.setEnabled(bool(piece.text_background_color))
        self.sl_text_bg_opacity.setEnabled(bool(piece.text_background_color))
        self.btn_clear_text_bg.setEnabled(bool(piece.text_background_color))
        text_color = QColor(piece.text_color)
        if text_color.isValid():
            foreground = "#000000" if text_color.lightness() > 128 else "#ffffff"
            self.btn_textcol.setText(f"{text_color.name().upper()} — Change…")
            self.btn_textcol.setStyleSheet(
                f"background:{text_color.name()}; color:{foreground};")
        self._update_text_background_button(piece.text_background_color)
        for control in controls:
            control.blockSignals(False)
        self._text_history_open = False

    def _update_text_dimensions(self, piece):
        for control, value in ((self.spin_text_width, piece.w),
                               (self.spin_text_height, piece.h)):
            control.blockSignals(True)
            control.setValue(round(value))
            control.blockSignals(False)

    def _fit_text_box(self, piece):
        if piece.text_auto_size:
            piece.w, piece.h = compute_text_size(
                piece.text, piece.font_family, piece.font_size,
                piece.font_bold, piece.font_italic, piece.text_padding)
        self._update_text_dimensions(piece)

    def _text_changed(self):
        piece = self._text_piece()
        if not piece:
            return
        if not self._text_history_open:
            self.canvas.push_history("Edit text")
            self._text_history_open = True
        piece.text = self.edit_text.toPlainText()
        self._fit_text_box(piece)
        self.canvas.update()
        self.canvas.dirty.emit()

    _CONTINUOUS_TEXT_EDITS = frozenset({
        "Text size", "Text box size", "Text padding", "Text background opacity"})

    def _set_text_property(self, label, attr, value, refit=False):
        piece = self._text_piece()
        if not piece or getattr(piece, attr) == value:
            return
        self.canvas.push_history(label, coalesce=label in self._CONTINUOUS_TEXT_EDITS)
        setattr(piece, attr, value)
        if refit:
            self._fit_text_box(piece)
        self.canvas.update()
        self.canvas.dirty.emit()

    def _font_changed(self, name):
        self._set_text_property("Text font", "font_family", name, True)

    def _fsize_changed(self, value):
        self._set_text_property("Text size", "font_size", value, True)

    def _bold_changed(self, value):
        self._set_text_property("Text bold", "font_bold", value, True)

    def _italic_changed(self, value):
        self._set_text_property("Text italic", "font_italic", value, True)

    def _underline_changed(self, value):
        self._set_text_property("Text underline", "font_underline", value)

    def _text_alignment_changed(self, _index):
        piece = self._text_piece()
        if not piece:
            return
        self.canvas.push_history("Text alignment")
        piece.text_halign = self.cmb_text_halign.currentData() or "center"
        piece.text_valign = self.cmb_text_valign.currentData() or "center"
        self.canvas.update()
        self.canvas.dirty.emit()

    def _text_auto_size_changed(self, enabled):
        piece = self._text_piece()
        if not piece or piece.text_auto_size == enabled:
            return
        self.canvas.push_history("Text box auto-fit")
        piece.text_auto_size = enabled
        if enabled:
            self._fit_text_box(piece)
        self.spin_text_width.setEnabled(not enabled)
        self.spin_text_height.setEnabled(not enabled)
        self.canvas.update()
        self.canvas.dirty.emit()

    def _text_dimension_changed(self, attr, value):
        piece = self._text_piece()
        if not piece or piece.text_auto_size:
            return
        self._set_text_property("Text box size", attr, value)

    def _text_padding_changed(self, value):
        self._set_text_property("Text padding", "text_padding", value, True)

    def _text_background_toggled(self, enabled):
        piece = self._text_piece()
        if not piece:
            return
        color = piece.text_background_color
        if enabled and not color:
            color = "#202020"
        elif not enabled:
            color = ""
        self._set_text_property("Text background", "text_background_color", color)
        has_background = bool(color)
        self.btn_text_bg.setEnabled(has_background)
        self.sl_text_bg_opacity.setEnabled(has_background)
        self.btn_clear_text_bg.setEnabled(has_background)
        self._update_text_background_button(color)

    def _pick_text_background(self):
        piece = self._text_piece()
        if not piece:
            return
        initial = piece.text_background_color or "#202020"
        color = choose_color(QColor(initial), self, self.canvas,
                             "Choose text background color")
        if not color.isValid():
            return
        self.canvas.push_history("Text background color")
        piece.text_background_color = color.name()
        self.chk_text_bg.blockSignals(True)
        self.chk_text_bg.setChecked(True)
        self.chk_text_bg.blockSignals(False)
        self._load_text_controls(piece)
        self.canvas.update()
        self.canvas.dirty.emit()

    def _clear_text_background(self):
        piece = self._text_piece()
        if not piece or not piece.text_background_color:
            return
        self.canvas.push_history("Clear text background")
        piece.text_background_color = ""
        self._load_text_controls(piece)
        self.canvas.update()
        self.canvas.dirty.emit()

    def _text_background_opacity_changed(self, value):
        self._set_text_property("Text background opacity",
                                "text_background_opacity", value / 100.0)

    def _update_text_background_button(self, color_name):
        color = QColor(color_name) if color_name else QColor()
        if color.isValid():
            foreground = "#000000" if color.lightness() > 128 else "#ffffff"
            self.btn_text_bg.setText(f"{color.name().upper()} — Change…")
            self.btn_text_bg.setStyleSheet(
                f"background:{color.name()}; color:{foreground};")
        else:
            self.btn_text_bg.setText("Choose fill…")
            self.btn_text_bg.setStyleSheet("")

    def _pick_text_color(self):
        piece = self._text_piece()
        if not piece:
            return
        color = choose_color(QColor(piece.text_color), self,
                             self.canvas, "Choose text color")
        if color.isValid():
            self.canvas.push_history("Text color")
            piece.text_color = color.name()
            self._load_text_controls(piece)
            self.canvas.update()
            self.canvas.dirty.emit()

    # ---- independent non-destructive raster-label patch overlay ----
    def _patch_piece(self) -> Piece | None:
        if len(self.pieces) == 1 and self.pieces[0].is_patch:
            return self.pieces[0]
        return None

    def _load_patch_controls(self, patch):
        self.sl_patch_opacity.blockSignals(True)
        self.sl_patch_opacity.setValue(round(patch.patch_opacity * 100))
        self.sl_patch_opacity.blockSignals(False)
        color = QColor(patch.patch_color)
        if color.isValid():
            foreground = "#000000" if color.lightness() > 128 else "#ffffff"
            self.btn_patch_color.setText(f"{color.name().upper()} — Change…")
            self.btn_patch_color.setStyleSheet(
                f"background:{color.name()}; color:{foreground};")

    def _scale_piece(self):
        return self.pieces[0] if len(self.pieces) == 1 and self.pieces[0].is_scale_bar else None

    def _load_scale_controls(self, piece):
        self.spin_scale_distance.blockSignals(True)
        self.cmb_scale_units.blockSignals(True)
        self.edit_scale_caption.blockSignals(True)
        self.spin_scale_distance.setValue(piece.scale_distance)
        index = self.cmb_scale_units.findData(piece.scale_units)
        self.cmb_scale_units.setCurrentIndex(max(0, index))
        self.edit_scale_caption.setText(piece.scale_caption)
        self.spin_scale_distance.blockSignals(False)
        self.cmb_scale_units.blockSignals(False)
        self.edit_scale_caption.blockSignals(False)
        color = QColor(piece.scale_color)
        if color.isValid():
            foreground = "#000000" if color.lightness() > 128 else "#ffffff"
            self.btn_scale_color.setText(f"{color.name().upper()} — Change…")
            self.btn_scale_color.setStyleSheet(
                f"background:{color.name()}; color:{foreground};")

    def _scale_distance_changed(self, value):
        piece = self._scale_piece()
        if not piece or abs(piece.scale_distance - value) < 1e-9:
            return
        self.canvas.push_history("Scale bar distance", coalesce=True)
        piece.scale_distance = value
        self.canvas.update(); self.canvas.dirty.emit()

    def _scale_units_changed(self, index):
        piece = self._scale_piece()
        if not piece or index < 0:
            return
        units = self.cmb_scale_units.itemData(index)
        if units == piece.scale_units:
            return
        self.canvas.push_history("Scale bar units")
        piece.scale_units = units
        self.canvas.update(); self.canvas.dirty.emit()

    def _scale_caption_changed(self):
        piece = self._scale_piece()
        if not piece:
            return
        caption = self.edit_scale_caption.text().strip()
        if caption == piece.scale_caption:
            return
        self.canvas.push_history("Scale bar caption")
        piece.scale_caption = caption
        self.canvas.update(); self.canvas.dirty.emit()

    def _pick_scale_color(self):
        piece = self._scale_piece()
        if not piece:
            return
        color = choose_color(QColor(piece.scale_color), self, self.canvas,
                             "Choose scale-bar color")
        if color.isValid():
            self.canvas.push_history("Scale bar color")
            piece.scale_color = color.name()
            self._load_scale_controls(piece)
            self.canvas.update(); self.canvas.dirty.emit()

    def _connector_piece(self):
        return self.pieces[0] if len(self.pieces) == 1 and self.pieces[0].is_connector else None

    def _load_connector_controls(self, piece):
        self.edit_connector_label.blockSignals(True)
        self.chk_connector_arrow.blockSignals(True)
        self.spin_connector_width.blockSignals(True)
        self.edit_connector_label.setText(piece.connector_label)
        self.chk_connector_arrow.setChecked(piece.connector_arrow)
        self.spin_connector_width.setValue(piece.connector_width)
        self.edit_connector_label.blockSignals(False)
        self.chk_connector_arrow.blockSignals(False)
        self.spin_connector_width.blockSignals(False)
        color = QColor(piece.connector_color)
        if color.isValid():
            foreground = "#000000" if color.lightness() > 128 else "#ffffff"
            self.btn_connector_color.setText(f"{color.name().upper()} — Change…")
            self.btn_connector_color.setStyleSheet(
                f"background:{color.name()}; color:{foreground};")

    def _connector_label_changed(self):
        piece = self._connector_piece()
        if not piece:
            return
        label = self.edit_connector_label.text().strip()
        if label == piece.connector_label:
            return
        self.canvas.push_history("Connection marker label")
        piece.connector_label = label
        self.canvas.update(); self.canvas.dirty.emit()

    def _connector_arrow_changed(self, checked):
        piece = self._connector_piece()
        if not piece or piece.connector_arrow == checked:
            return
        self.canvas.push_history("Connection marker arrow")
        piece.connector_arrow = checked
        self.canvas.update(); self.canvas.dirty.emit()

    def _connector_width_changed(self, value):
        piece = self._connector_piece()
        if not piece or abs(piece.connector_width - value) < 1e-9:
            return
        self.canvas.push_history("Connection marker width", coalesce=True)
        piece.connector_width = value
        self.canvas.update(); self.canvas.dirty.emit()

    def _pick_connector_color(self):
        piece = self._connector_piece()
        if not piece:
            return
        color = choose_color(QColor(piece.connector_color), self, self.canvas,
                             "Choose connection-marker color")
        if color.isValid():
            self.canvas.push_history("Connection marker color")
            piece.connector_color = color.name()
            self._load_connector_controls(piece)
            self.canvas.update(); self.canvas.dirty.emit()

    def _start_crop(self):
        if not self.canvas.set_crop_tool(True):
            self.btn_crop_image.setToolTip("Select one image node before cropping.")
            return
        window = self.window()
        if hasattr(window, "status"):
            window.status.showMessage(
                "Crop tool active — drag the area to keep; the source image remains unchanged.",
                8000)

    def _replace_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Replace selected node image", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path and not self.canvas.replace_selected_image(path):
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Replace image",
                                "Choose a valid image and select one image node first.")

    def _pick_patch_color(self):
        patch = self._patch_piece()
        if not patch:
            return
        initial = patch.patch_color or "#10141c"
        color = choose_color(QColor(initial), self, self.canvas,
                             "Choose raster patch fill color")
        if not color.isValid():
            return
        self.canvas.push_history("Raster patch color")
        patch.patch_color = color.name()
        self._load_patch_controls(patch)
        self.canvas.update()
        self.canvas.dirty.emit()

    def _patch_opacity_changed(self, value):
        patch = self._patch_piece()
        if not patch or patch.patch_opacity == value / 100.0:
            return
        self.canvas.push_history("Raster patch opacity", coalesce=True)
        patch.patch_opacity = value / 100.0
        self.canvas.update()
        self.canvas.dirty.emit()

    def _add_text_over_patch(self):
        patch = self._patch_piece()
        if not patch:
            return
        label = self.canvas.add_text(*patch.center)
        label.x, label.y = patch.x, patch.y
        label.w, label.h = patch.w, patch.h
        label.text_auto_size = False
        label.text_halign = "center"
        label.text_valign = "center"
        if self.canvas.level:
            label.layer = patch.layer
            label.z = self.canvas.level.next_z()
            self.load_selection([label])
            self.canvas.update()
            self.canvas.dirty.emit()

    # ---- project-wide tint inherited by default by image nodes ----
    def _pick_project_tint(self):
        if not self.project:
            return
        initial = self.project.tint_color or "#ffffff"
        color = choose_color(QColor(initial), self, self.canvas,
                             "Choose project tint")
        if not color.isValid():
            return
        self.canvas.push_history("Project tint")
        self.project.tint_color = color.name()
        if self.sl_project_tint.value() == 0:
            self.sl_project_tint.blockSignals(True)
            self.sl_project_tint.setValue(50)
            self.sl_project_tint.blockSignals(False)
            self.project.tint_strength = 0.5
        self._update_project_tint_button()
        self.canvas.update(); self.canvas.dirty.emit()
        self.load_selection(self.pieces)

    def _project_tint_strength(self, value):
        if not self.project:
            return
        self.canvas.push_history("Project tint strength", coalesce=True)
        self.project.tint_strength = value / 100.0
        self.canvas.update(); self.canvas.dirty.emit()
        self.load_selection(self.pieces)

    def _clear_project_tint(self):
        if not self.project:
            return
        if not self.project.tint_color and self.project.tint_strength == 0:
            return
        self.canvas.push_history("Clear project tint")
        self.project.tint_color = ""
        self.project.tint_strength = 0.0
        self.sl_project_tint.blockSignals(True)
        self.sl_project_tint.setValue(0)
        self.sl_project_tint.blockSignals(False)
        self._update_project_tint_button()
        self.canvas.update(); self.canvas.dirty.emit()
        self.load_selection(self.pieces)

    # ---- alpha-preserving tint/recolor overlay ----
    def _tint_mode_changed(self, index):
        if not self.pieces or index < 0:
            return
        mode = self.cmb_tint_mode.itemData(index)
        if mode not in {"inherit", "override", "original"}:
            return  # the mixed-selection placeholder is display-only
        image_pieces = [piece for piece in self.pieces
                        if not piece.is_text and not piece.is_patch]
        if not image_pieces or all(piece.tint_mode == mode for piece in image_pieces):
            return
        self.canvas.push_history("Tint mode")
        for piece in image_pieces:
            if mode == "override":
                if not piece.tint_color:
                    piece.tint_color = (self.project.tint_color
                                        if self.project and self.project.tint_color
                                        else "#ffffff")
                if piece.tint_strength <= 0:
                    project_strength = (self.project.tint_strength
                                        if self.project else 0.0)
                    piece.tint_strength = project_strength or 0.5
            piece.tint_mode = mode
        self.canvas.update(); self.canvas.dirty.emit()
        self.load_selection(self.pieces)

    def _pick_tint(self):
        image_pieces = [piece for piece in self.pieces
                        if not piece.is_text and not piece.is_patch]
        if not image_pieces:
            return
        current = next((piece.tint_color for piece in image_pieces
                        if piece.tint_mode == "override" and piece.tint_color),
                       self.project.tint_color if self.project and self.project.tint_color
                       else "#ffffff")
        color = choose_color(QColor(current), self, self.canvas,
                             "Choose node tint")
        if not color.isValid():
            return
        self.canvas.push_history("Node tint")
        strength = self.sl_tint.value() / 100.0
        if strength == 0:
            strength = 0.5
            self.sl_tint.blockSignals(True)
            self.sl_tint.setValue(50)
            self.sl_tint.blockSignals(False)
        for piece in image_pieces:
            piece.tint_mode = "override"
            piece.tint_color = color.name()
            piece.tint_strength = strength
        self.canvas.update(); self.canvas.dirty.emit()
        self.load_selection(self.pieces)

    def _tint_strength(self, value):
        image_pieces = [piece for piece in self.pieces
                        if not piece.is_text and not piece.is_patch]
        if not image_pieces:
            return
        self.canvas.push_history("Node tint strength", coalesce=True)
        for piece in image_pieces:
            if piece.tint_mode != "override":
                piece.tint_mode = "override"
                if not piece.tint_color:
                    piece.tint_color = (self.project.tint_color
                                        if self.project and self.project.tint_color
                                        else "#ffffff")
            piece.tint_strength = value / 100.0
        self.canvas.update(); self.canvas.dirty.emit()
        self.load_selection(self.pieces)

    # ------------------------------------------------------------------
    def _apply_grid(self):
        if not self.project:
            return
        self.project.show_grid = self.chk_grid.isChecked()
        self.project.show_centerlines = self.chk_centerlines.isChecked()
        self.project.grid_opacity = self.sl_gop.value() / 100.0
        old_canvas_size = (self.project.canvas_w, self.project.canvas_h)
        self.project.grid_style = self.cmb_style.currentText()
        self.project.grid_major = self.spin_major.value()
        self.project.cell_size = self.spin_cell.value()
        self.project.feet_per_square = int(self.cmb_feet.currentText().split()[0])
        self.project._sync_canvas()
        self._update_canvas_dimensions()
        if old_canvas_size != (self.project.canvas_w, self.project.canvas_h):
            self.canvas.fit_to_view()
        else:
            self.canvas.update()
        self.canvas.dirty.emit()

    def _apply_size(self):
        if not self.project:
            return
        self.project.map_cols = self.spin_cols.value()
        self.project.map_rows = self.spin_rows.value()
        self.project._sync_canvas()
        self._update_canvas_dimensions()
        self.canvas.fit_to_view(); self.canvas.dirty.emit()

    def _pick_color(self):
        c = choose_color(QColor(self.project.grid_color), self, self.canvas,
                         "Choose grid color")
        if c.isValid():
            self.project.grid_color = c.name()
            self._update_color_btn(); self.canvas.update(); self.canvas.dirty.emit()

    def _apply_ref(self):
        enabled = self.chk_ref.isChecked()
        offset = -1 if self.cmb_ref.currentIndex() == 0 else 1
        opacity = self.sl_ref.value() / 100.0
        self.canvas.set_ref(enabled, offset, opacity)
        self.canvas.dirty.emit()
