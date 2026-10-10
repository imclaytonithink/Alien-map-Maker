"""The Generate window: one place for both generators, each on its own tab.

1. Ships & sites (Geomorph) - the default: presets, ship and site types, rooms, damage, lights.
2. Your own tiles - lays out the library pictures you tick, picked from the folder tree.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QTabWidget, QVBoxLayout

from ui.branding import ALIEN_NAME, APP_NAME

GEOMORPH, OWN = 0, 1


class GenerateWindow(QDialog):
    def __init__(self, main, tab=GEOMORPH, selection=None):
        super().__init__(main)
        from ui.generator_dialog import GeneratorDialog
        from ui.geomorph_dialog import GeomorphDialog
        self.main = main
        mode = getattr(main, "theme_mode", "dark")
        product = ALIEN_NAME if mode == "alien" else APP_NAME
        self.setWindowTitle(f"{product} — Generate a map")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        self.geomorph = GeomorphDialog(main, main)
        self.own = GeneratorDialog(main.project, main.library, main.canvas, main._run_generator, main,
                                   selection=selection)
        for page, label, tip in (
                (self.geomorph, "Ships && sites (Geomorph)",
                 "Connected ships and sites from the Geomorph tiles, with ready-made scenarios and presets."),
                (self.own, "Your own tiles",
                 "Lay out exactly the pictures you pick from the library: rows, a scatter, or a fill.")):
            page.setWindowFlags(Qt.WindowType.Widget)      # a page in this window, not a window of its own
            index = self.tabs.addTab(page, label)
            self.tabs.setTabToolTip(index, tip)
            page.finished.connect(self.done)                # their Close buttons close the whole window
        self.tabs.setCurrentIndex(tab)
        hint = self.geomorph.sizeHint().expandedTo(self.own.sizeHint())
        self.resize(max(1100, hint.width()), max(780, hint.height()))

    def done(self, result):
        for page in (self.geomorph, self.own):
            picker = getattr(page, "picker", None)
            if picker is not None:
                picker.drain_background_work()
        super().done(result)
