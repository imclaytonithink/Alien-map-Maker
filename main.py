"""Sci-Fi Battlemap Builder — application entry point."""
from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Sci-Fi Battlemap Builder")
    app.setOrganizationName("ArenaMaps")
    # Enable high-DPI scaling where supported (no-op / deprecated on Qt 6).
    try:
        app.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    except AttributeError:
        pass
    try:
        app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    except AttributeError:
        pass
    win = MainWindow()
    win.show()
    win.canvas.fit_to_view()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
