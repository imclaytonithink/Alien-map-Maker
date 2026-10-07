"""Sort assets into generator roles: review the automatic sorting and fix it."""
from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QTableView,
    QVBoxLayout, QWidget,
)

from core.asset_roles import ROLE_ABOUT, ROLE_DEFS, ROLE_IDS, ROLE_LABELS, ROLE_USE

NEEDS_REVIEW = "__review__"
ALL_ROLES = "__all__"
COLUMNS = ("Name", "Size", "Role", "Why", "Folder")


class RoleTableModel(QAbstractTableModel):
    def __init__(self, assets, roles, parent=None):
        super().__init__(parent)
        self.assets = list(assets)
        self.roles = roles

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.assets)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        asset = self.assets[index.row()]
        info = self.roles.get(asset.path)
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return asset.name
            if column == 1:
                return f"{asset.size[0]}x{asset.size[1]}" if asset.size else ""
            if column == 2:
                label = ROLE_LABELS.get(info.role, info.role) if info else ""
                return label + (" ★" if info and info.overridden else "")
            if column == 3:
                return info.reason if info else ""
            return asset.folder
        if role == Qt.ItemDataRole.ToolTipRole:
            if info is None:
                return asset.path
            tip = f"{asset.path}\n{ROLE_LABELS[info.role]} — {info.reason} ({info.confidence})"
            if info.overridden:
                tip += f"\nAutomatic sorting would say: {ROLE_LABELS[info.auto_role]}"
            return tip
        if role == Qt.ItemDataRole.ForegroundRole and column == 2 and info:
            if info.overridden:
                return QColor("#69b7f5")
            if info.confidence == "low":
                return QColor("#e5933b")
        if role == Qt.ItemDataRole.UserRole:
            return asset.path
        return None

    def set_roles(self, roles):
        self.beginResetModel()
        self.roles = roles
        self.endResetModel()


class RoleFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.role_filter = ALL_ROLES
        self.text = ""

    def set_filters(self, role_filter, text):
        self.role_filter, self.text = role_filter, text.strip().casefold()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        model = self.sourceModel()
        asset = model.assets[row]
        info = model.roles.get(asset.path)
        if self.role_filter == NEEDS_REVIEW:
            if not info or (info.confidence != "low" and info.role != "other"):
                return False
        elif self.role_filter != ALL_ROLES and (not info or info.role != self.role_filter):
            return False
        if self.text and self.text not in asset.name.casefold() \
                and self.text not in asset.folder.casefold():
            return False
        return True


class SortDialog(QDialog):
    """Left: the roles and what each is for. Right: every asset and its role."""

    def __init__(self, library, on_changed=None, parent=None, initial_role=ALL_ROLES):
        super().__init__(parent)
        self.setWindowTitle("Sort assets for the generator")
        self.resize(1040, 640)
        self.library = library
        self.on_changed = on_changed
        self.model = RoleTableModel(library.assets, library.roles())
        self.proxy = RoleFilterProxy(self)
        self.proxy.setSourceModel(self.model)

        root = QVBoxLayout(self)
        intro = QLabel(
            "Every asset has one <b>role</b> that tells the generator what it is "
            "for. They are sorted automatically from the file name, folder and "
            "size, but the packs are big, so check the list: anything the "
            "automatic sorting gets wrong, pick it and give it the right role. "
            "Your choices (★) are remembered with the asset store and always win.")
        intro.setWordWrap(True)
        root.addWidget(intro)

        body = QHBoxLayout()
        root.addLayout(body, 1)

        left = QVBoxLayout()
        self.role_list = QListWidget()
        self.role_list.setMinimumWidth(270)
        self.role_list.setMaximumWidth(330)
        left.addWidget(self.role_list, 1)
        self.lbl_about = QLabel("")
        self.lbl_about.setWordWrap(True)
        self.lbl_about.setMinimumHeight(86)
        left.addWidget(self.lbl_about)
        body.addLayout(left)

        right = QVBoxLayout()
        filter_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by name or folder…")
        self.search.textChanged.connect(self._apply_filters)
        filter_row.addWidget(self.search, 1)
        self.lbl_shown = QLabel("")
        filter_row.addWidget(self.lbl_shown)
        right.addLayout(filter_row)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.setColumnWidth(0, 300)
        self.table.setColumnWidth(1, 70)
        self.table.setColumnWidth(2, 190)
        self.table.setColumnWidth(3, 250)
        self.table.selectionModel().selectionChanged.connect(self._update_selection_label)
        right.addWidget(self.table, 1)

        action_row = QHBoxLayout()
        self.lbl_selected = QLabel("")
        action_row.addWidget(self.lbl_selected, 1)
        select_all = QPushButton("Select all shown")
        select_all.clicked.connect(self.table.selectAll)
        action_row.addWidget(select_all)
        action_row.addWidget(QLabel("Set selected to"))
        self.cmb_role = QComboBox()
        for role_id, label, _about, _use in ROLE_DEFS:
            self.cmb_role.addItem(label, role_id)
        action_row.addWidget(self.cmb_role)
        apply_button = QPushButton("Apply")
        apply_button.setToolTip("Give the selected assets this role.")
        apply_button.clicked.connect(self._apply_role)
        action_row.addWidget(apply_button)
        reset = QPushButton("Back to automatic")
        reset.setToolTip("Forget your choice for the selected assets.")
        reset.clicked.connect(self._reset_role)
        action_row.addWidget(reset)
        right.addLayout(action_row)
        body.addLayout(right, 1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close = QPushButton("Done")
        close.clicked.connect(self.accept)
        close_row.addWidget(close)
        root.addLayout(close_row)

        self._fill_role_list()
        self.role_list.currentItemChanged.connect(self._role_picked)
        self._select_role(initial_role)
        self._update_selection_label()

    # -- role list ---------------------------------------------------------
    def _fill_role_list(self):
        current = self.role_list.currentItem().data(Qt.ItemDataRole.UserRole) \
            if self.role_list.currentItem() else ALL_ROLES
        counts = {role_id: 0 for role_id in ROLE_IDS}
        review = 0
        for info in self.model.roles.values():
            counts[info.role] = counts.get(info.role, 0) + 1
            if info.confidence == "low" or info.role == "other":
                review += 1
        self.role_list.blockSignals(True)
        self.role_list.clear()

        def add(key, label, tip):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(tip)
            self.role_list.addItem(item)

        add(ALL_ROLES, f"All assets ({len(self.model.assets):,})", "Every asset")
        add(NEEDS_REVIEW, f"⚠ Needs a look ({review:,})",
            "Unsorted or low-confidence guesses — start here.")
        for role_id, label, about, use in ROLE_DEFS:
            add(role_id, f"{label} ({counts[role_id]:,})", f"{about}\n{use}")
        self.role_list.blockSignals(False)
        self._select_role(current, apply=False)

    def _select_role(self, key, apply=True):
        for row in range(self.role_list.count()):
            item = self.role_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                self.role_list.blockSignals(True)
                self.role_list.setCurrentItem(item)
                self.role_list.blockSignals(False)
                break
        if apply:
            self._role_picked(self.role_list.currentItem(), None)

    def _role_picked(self, item, _previous):
        key = item.data(Qt.ItemDataRole.UserRole) if item else ALL_ROLES
        self.proxy.set_filters(key, self.search.text())
        if key in ROLE_IDS:
            self.lbl_about.setText(f"<b>{ROLE_LABELS[key]}</b><br>{ROLE_ABOUT[key]}"
                                   f"<br><i>{ROLE_USE[key]}</i>")
            self.cmb_role.setCurrentIndex(ROLE_IDS.index(key))
        elif key == NEEDS_REVIEW:
            self.lbl_about.setText("<b>Needs a look</b><br>Assets the sorting could not "
                                   "place confidently. Give them a role below.")
        else:
            self.lbl_about.setText("<b>All assets</b><br>Pick a role on the left to see "
                                   "what is in it.")
        self._update_shown()

    def _apply_filters(self, _text=None):
        item = self.role_list.currentItem()
        key = item.data(Qt.ItemDataRole.UserRole) if item else ALL_ROLES
        self.proxy.set_filters(key, self.search.text())
        self._update_shown()

    def _update_shown(self):
        self.lbl_shown.setText(f"{self.proxy.rowCount():,} shown")
        self._update_selection_label()

    def _selected_paths(self):
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        return [self.proxy.index(row, 0).data(Qt.ItemDataRole.UserRole) for row in sorted(rows)]

    def _update_selection_label(self, *_):
        count = len(self.table.selectionModel().selectedRows())
        self.lbl_selected.setText(
            f"{count:,} selected" if count else "Click rows to select (Shift/Ctrl for several).")

    # -- changing roles ------------------------------------------------------
    def _commit(self, role):
        paths = self._selected_paths()
        if not paths:
            return 0
        changed = self.library.set_role(paths, role)
        roles = self.library.roles()
        self.model.set_roles(roles)
        self._fill_role_list()
        self._apply_filters()
        if self.on_changed:
            self.on_changed()
        return changed

    def _apply_role(self):
        return self._commit(self.cmb_role.currentData())

    def _reset_role(self):
        return self._commit(None)
