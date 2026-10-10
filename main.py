"""SceneBoard — application entry point."""
from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.app_icon import app_icon, use_own_taskbar_icon
from ui.branding import APP_NAME, SETTINGS_ID


def _report_errors():
    """Show unexpected errors instead of letting Qt end the program.

    PyQt ends the whole app when an error escapes a button or menu handler. With
    this hook the error is written to ``errors.log`` in the app-data folder and
    shown in a message, and the app keeps running."""
    import traceback

    def hook(kind, value, tb):
        text = "".join(traceback.format_exception(kind, value, tb))
        if sys.__stderr__:
            sys.__stderr__.write(text)
        log = ""
        try:
            from pathlib import Path
            from PyQt6.QtCore import QStandardPaths
            folder = Path(QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation) or Path.home())
            folder.mkdir(parents=True, exist_ok=True)
            log = str(folder / "errors.log")
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except Exception:
            pass
        try:
            from PyQt6.QtWidgets import QMessageBox
            box = QMessageBox(QMessageBox.Icon.Warning, "SceneBoard",
                              f"Something went wrong: {value}\n\nThe program is still running."
                              + (f"\nDetails were saved to {log}" if log else ""))
            box.setDetailedText(text)
            box.exec()
        except Exception:
            pass

    sys.excepthook = hook


def main():
    use_own_taskbar_icon()
    app = QApplication(sys.argv)
    _report_errors()
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
