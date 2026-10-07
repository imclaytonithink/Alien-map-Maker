"""Write SceneBoard's icon files from the drawing in ui/app_icon.py.

    python make_icon.py

Creates ``ui/icons/SceneBoard.ico`` (16-256 px, used by build.bat for the
EXE) and ``ui/icons/SceneBoard.png`` (256 px preview). The window icon itself
is drawn at runtime, so re-run this only after changing the drawing.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QGuiApplication  # noqa: E402

from ui.app_icon import ico_bytes, icon_image  # noqa: E402

ICON_DIR = os.path.join(ROOT, "ui", "icons")
ICO_PATH = os.path.join(ICON_DIR, "SceneBoard.ico")
PNG_PATH = os.path.join(ICON_DIR, "SceneBoard.png")


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    os.makedirs(ICON_DIR, exist_ok=True)
    with open(ICO_PATH, "wb") as handle:
        handle.write(ico_bytes())
    if not icon_image(256).save(PNG_PATH, "PNG"):
        print(f"Could not write {PNG_PATH}", file=sys.stderr)
        return 1
    print(f"Wrote {os.path.relpath(ICO_PATH, ROOT)} and {os.path.relpath(PNG_PATH, ROOT)}")
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
