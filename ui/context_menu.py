"""Right-click context menu for the canvas."""
from __future__ import annotations

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu


def fill_mirror_menu(main, menu: QMenu) -> QMenu:
    """Entries that place mirrored copies of the selection: across the map's
    center lines, then across each guide on this level."""
    canvas = main.canvas
    has_selection = bool(canvas.selected_pieces())
    lines = canvas.mirror_lines()
    for index, (axis, pos, label) in enumerate(lines):
        if index == 2:
            menu.addSeparator()
        action = QAction(f"Across the {label[0].lower()}{label[1:]}", menu)
        action.setEnabled(has_selection)
        action.triggered.connect(
            lambda _=False, a=axis, p=pos: main._mirror_selection(a, p))
        menu.addAction(action)
    if len(lines) <= 2:
        hint = QAction("Tip: drag a guide out of a canvas edge rail to mirror "
                       "across it", menu)
        hint.setEnabled(False)
        menu.addSeparator()
        menu.addAction(hint)
    if not has_selection:
        hint = QAction("Select the nodes to mirror first", menu)
        hint.setEnabled(False)
        menu.insertAction(menu.actions()[0] if menu.actions() else None, hint)
    return menu


def fill_stamp_menu(main, menu: QMenu) -> QMenu:
    """Pin the single selected node to one of the hotbar keys 1-9."""
    from core.stamps import slot_label
    for index, slot in enumerate(main.stamp_slots):
        label = slot_label(slot)
        text = f"{index + 1}  —  " + (f"replace “{label}”" if label else "empty")
        action = QAction(text, menu)
        action.triggered.connect(lambda _=False, n=index: main._pin_selected_stamp(n))
        menu.addAction(action)
    return menu


def build_canvas_menu(main, hit_piece) -> QMenu:
    canvas = main.canvas
    menu = QMenu(main)
    sel = canvas.selected_pieces()

    def add(target, label, slot, enabled=True, shortcut=""):
        action = QAction(label + (f"\t{shortcut}" if shortcut else ""), menu)
        action.setEnabled(enabled)
        action.triggered.connect(lambda _=False: slot())
        target.addAction(action)
        return action

    if sel:
        add(menu, "Duplicate", canvas.duplicate)
        add(menu, "Duplicate as grid…", main._duplicate_as_grid_dialog,
            shortcut="Ctrl+Shift+D")
        fill_mirror_menu(main, menu.addMenu("Mirror copy"))
        add(menu, "Copy", canvas.copy)
        add(menu, "Paste", canvas.paste, bool(canvas._clipboard))
        add(menu, "Delete", canvas.delete_selected, shortcut="Del")
        menu.addSeparator()
        arrange = menu.addMenu("Arrange")
        add(arrange, "Bring forward", lambda: canvas._quick("up"))
        add(arrange, "Send backward", lambda: canvas._quick("down"))
        transform = menu.addMenu("Transform")
        add(transform, "Rotate 90° clockwise", lambda: canvas._quick("rotR"))
        add(transform, "Rotate 90° counter-clockwise", lambda: canvas._quick("rotL"))
        add(transform, "Free transform", main._toggle_free_transform,
            len(sel) == 1, "Ctrl+T")
        add(transform, "Flip horizontal", lambda: canvas._quick("fh"))
        add(transform, "Flip vertical", lambda: canvas._quick("fv"))
        add(menu, "Lock / unlock", lambda: canvas._quick("lock"))
        menu.addSeparator()
        multi = len(sel) > 1
        add(menu, "Group", canvas.group, multi)
        add(menu, "Ungroup", canvas.ungroup, any(p.group_id for p in sel))
        align = menu.addMenu("Align")
        for label, kind in (("Left", "left"), ("Right", "right"), ("Top", "top"),
                            ("Bottom", "bottom"), ("Center horizontally", "hcenter"),
                            ("Center vertically", "vcenter")):
            add(align, label, lambda k=kind: canvas.align(k), multi)
        overlap = add(align, "Allow overlap", lambda: main._set_allow_overlap(
            not canvas.allow_overlap))
        overlap.setCheckable(True)
        overlap.setChecked(canvas.allow_overlap)
        center = menu.addMenu("Center on canvas")
        add(center, "Horizontally", lambda: canvas.center_selection_on_canvas("h"))
        add(center, "Vertically", lambda: canvas.center_selection_on_canvas("v"))
        add(center, "Both", lambda: canvas.center_selection_on_canvas("both"))
        dist = menu.addMenu("Distribute")
        add(dist, "Horizontally", lambda: canvas.distribute("h"), len(sel) > 2)
        add(dist, "Vertically", lambda: canvas.distribute("v"), len(sel) > 2)
        menu.addSeparator()
        add(menu, "Select similar", main._select_similar)
        add(menu, "Copy style…", main._start_copy_style)
        add(menu, "Replace image…", main._replace_selected_image)
        add(menu, "Tighten to visible pixels…", main._tighten_dialog)
        if len(sel) == 1:
            fill_stamp_menu(main, menu.addMenu("Pin to stamp key"))
        guides = menu.addMenu("Add guides")
        add(guides, "At the selection's edges",
            lambda: canvas.add_guides_around_selection("edges"))
        add(guides, "Through its center", lambda: canvas.add_guides_around_selection("center"))
        add(guides, "Edges and center", lambda: canvas.add_guides_around_selection("both"))
    else:
        add(menu, "Paste", canvas.paste, bool(canvas._clipboard))
        add(menu, "Add text", main._add_text)
        menu.addSeparator()
        add(menu, "Fit to view", canvas.fit_to_view)
        add(menu, "Zoom 100%", lambda: canvas.set_zoom(1.0))
    menu.addSeparator()
    add(menu, "Command palette…", main._open_palette, shortcut="Ctrl+Shift+P")
    return menu


def build_guide_menu(main, target: dict) -> QMenu:
    """Menu for a right-click on a placed guide ({"guide": id}) or on one of
    the guide rails along the canvas edges ({"rail": side})."""
    canvas = main.canvas
    project = canvas.project
    menu = QMenu(main)

    def add(label, slot, enabled=True, checked=None, shortcut=""):
        action = QAction(label + (f"\t{shortcut}" if shortcut else ""), menu)
        action.setEnabled(enabled)
        if checked is not None:
            action.setCheckable(True)
            action.setChecked(bool(checked))
        action.triggered.connect(lambda _=False: slot())
        menu.addAction(action)
        return action

    has_guides = bool(canvas.level and canvas.level.guides)
    multi_level = bool(project and len(project.levels) > 1)
    has_selection = bool(canvas.selected_pieces())
    centerline = target.get("centerline")
    if centerline:
        pos = project.canvas_w / 2.0 if centerline == "v" else project.canvas_h / 2.0
        add("Mirror selection across this line" if has_selection
            else "Mirror selection across this line (select nodes first)",
            lambda: main._mirror_selection(centerline, pos), has_selection)
        add("Add a guide on this line", lambda: canvas.add_guide(centerline, pos))
        menu.addSeparator()
        add("Hide center lines", lambda: main._set_show_centerlines(False))
        return menu
    guide_id = target.get("guide")
    if guide_id:
        guide = canvas.level.find_guide(guide_id) if canvas.level else None
        add("Set position…", lambda: main._edit_guide_position(guide_id))
        if guide is not None:
            add("Mirror selection across this guide" if has_selection
                else "Mirror selection across this guide (select nodes first)",
                lambda: main._mirror_selection(guide.axis, guide.pos), has_selection)
        add("Delete guide", lambda: canvas.remove_guide(guide_id))
        menu.addSeparator()
        add("Lock guides", lambda: canvas.set_guide_flag(
            "lock_guides", not project.lock_guides), checked=project.lock_guides,
            shortcut="Ctrl+Alt+;")
    else:
        add("Show guides", lambda: canvas.set_guide_flag(
            "show_guides", not project.show_guides), checked=project.show_guides,
            shortcut="Ctrl+;")
        add("Grid coordinates", lambda: canvas.set_show_coordinates(
            not project.show_coordinates), checked=project.show_coordinates)
        add("Guide layout…", main._guide_layout_dialog)
    add("Copy guides to all levels", main._copy_guides_to_all_levels,
        has_guides and multi_level)
    add("Clear guides on this level", canvas.clear_guides, has_guides)
    if not guide_id:
        menu.addSeparator()
        add("Hide guide rails", lambda: main._view_set("rails", False))
    return menu
