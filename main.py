"""SceneBoard — application entry point."""
from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.app_icon import app_icon, use_own_taskbar_icon
from ui.branding import APP_NAME, SETTINGS_ID


def main():
    use_own_taskbar_icon()
    app = QApplication(sys.argv)
    # Keep the internal identity stable so pre-existing settings and autosave
    # locations survive the working-title change.
    app.setApplicationName(SETTINGS_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("ArenaMaps")
    app.setWindowIcon(app_icon())
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
    code = app.exec()
    # Tear the window down while Qt is still running, so nothing in it can
    # outlive the application object on the way out.
    from PyQt6 import sip
    if not sip.isdeleted(win):
        sip.delete(win)
    del win
    sys.exit(code)


if __name__ == "__main__":
    main()
