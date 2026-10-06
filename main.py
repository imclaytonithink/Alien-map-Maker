"""SceneBoard — application entry point."""
from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.branding import APP_NAME, SETTINGS_ID


def main():
    app = QApplication(sys.argv)
    # Keep the internal identity stable so pre-existing settings and autosave
    # locations survive the working-title change.
    app.setApplicationName(SETTINGS_ID)
    app.setApplicationDisplayName(APP_NAME)
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
