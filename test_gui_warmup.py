"""The start-up warm-up builds tile previews off the GUI thread with a loading screen."""
import os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])
cache = tempfile.mkdtemp(prefix="geo-cache-")
px = tempfile.mkdtemp(prefix="px-cache-")
os.environ["GEOMORPH_CACHE"] = cache
os.environ["SCENEBOARD_PX_CACHE"] = px

import ui.launch_screen as ls
ls.LaunchScreen.exec = lambda self, *a, **k: 1
from geomorph.registry import Registry
from ui.main_window import MainWindow
from ui.warmup import SLOW_TILE_THRESHOLD, Warmup, WarmupWorker

MainWindow._offer_recovery = lambda self: self.overlay.open_menu()
store = tempfile.mkdtemp(prefix="sceneboard-warm-")
reg = Registry.load()
tiles = [t for t in reg.tiles.values() if t.type == "standard"][:SLOW_TILE_THRESHOLD + 4]
for t in tiles:
    path = os.path.join(store, "Pack", *t.image.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = QImage((t.w + 4) * 20, (t.h + 4) * 20, QImage.Format.Format_ARGB32)
    img.fill(QColor("#336677"))
    assert img.save(path)

win = MainWindow()
win.settings.clear()
win.show()
app.processEvents()
win.project.asset_store = store

seen = {"planned": None, "progress": 0, "ready": False, "shown": False}
w = Warmup(win, lambda text, ms=0: None)
_show = w.overlay.show
w.overlay.show = lambda: (seen.__setitem__("shown", True), _show())
w.start(store)
w.worker.planned.connect(lambda n: seen.__setitem__("planned", n))
w.worker.progress.connect(lambda d, t, x: seen.__setitem__("progress", d))
w.worker.ready.connect(lambda: seen.__setitem__("ready", True))
t0 = time.time()
while not seen["ready"] and time.time() - t0 < 60:
    app.processEvents()
    time.sleep(0.01)
w.worker.wait(5000)
assert seen["ready"], "warm-up finished"
assert seen["planned"] == len(tiles), seen
assert seen["shown"], "loading screen appeared for real work"
assert not w.overlay.isVisible(), "loading screen gone when ready"
assert len(os.listdir(cache)) == len(tiles), "every tile preview built"
assert os.path.isfile(os.path.join(px, "image_sizes.json")), "image sizes remembered"

# second run: everything is cached, so no loading screen at all
w2 = Warmup(win, lambda text, ms=0: None)
shown = []
w2.start(store)
w2.worker.planned.connect(lambda n: shown.append(n))
w2.worker.wait(20000)
app.processEvents()
assert shown == [0] and not w2.overlay.isVisible(), shown

# skip button keeps working in the background
for f in os.listdir(cache):
    os.remove(os.path.join(cache, f))
w3 = Warmup(win, lambda text, ms=0: None)
w3.start(store)
t0 = time.time()
while not w3.overlay.isVisible() and time.time() - t0 < 20:
    app.processEvents(); time.sleep(0.01)
if w3.overlay.isVisible():          # (a fast machine may already be done)
    w3.overlay.btn.click()
assert not w3.overlay.isVisible()
w3.worker.wait(30000)
app.processEvents()
assert len(os.listdir(cache)) == len(tiles)
w.drain(); w2.drain(); w3.drain()
win._confirm_discard = lambda *a, **k: True
win.close()
print("ALL WARMUP CHECKS PASSED")
