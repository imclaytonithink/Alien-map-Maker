"""Regression checks for a large, model-backed, lazy-thumbnail asset view."""
from __future__ import annotations

import os
import sys
import tempfile

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from core.asset_manager import Asset, AssetLibrary
from core.project import new_project
from ui.library import AssetList, AssetListModel, LibraryPanel


def main():
    # A large result set should be represented by rows in a model, not one
    # QListWidgetItem (and a collection of Qt child objects) per image.
    assets = [
        Asset(path=f"Pack/folder_{i % 20}/asset_{i:05}.png",
              name=f"asset_{i:05}.png", folder=f"Pack/folder_{i % 20}",
              width=7200, height=7200)
        for i in range(30_000)
    ]
    model = AssetListModel()
    model.set_assets(assets)
    assert model.rowCount() == len(assets)
    assert model.data(model.index(20_001, 0), Qt.ItemDataRole.UserRole) == \
        assets[20_001].path
    assert model.data(model.index(20_001, 0), Qt.ItemDataRole.DisplayRole) == \
        assets[20_001].name
    assert not model._icons, "thumbnail data should stay lazy until requested"

    view = AssetList()
    view.set_assets(assets)
    assert view.count() == len(assets)

    # Re-selecting a project backed by the already-scanned store must reuse the
    # in-memory index rather than walking and reopening every image again.
    with tempfile.TemporaryDirectory(prefix="library-model-") as root:
        library = AssetLibrary()
        library.scan(root)
        scans = []
        original_scan = library.scan

        def counted_scan(path):
            scans.append(path)
            return original_scan(path)

        library.scan = counted_scan
        project = new_project()
        project.asset_store = root
        panel = LibraryPanel()
        panel.set_project(project, library)
        panel.set_project(project, library)
        assert not scans, f"same-store project switch rescanned: {scans}"
        assert panel.list.count() == 0

    print("Large asset library virtualization checks passed.")


if __name__ == "__main__":
    main()
