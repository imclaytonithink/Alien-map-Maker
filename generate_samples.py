"""Generate a handful of sample PNG pieces so the app is testable.

These mimic a sci-fi asset library: fixed sizes encoded in the file names,
a few overlays, and a folder structure. Real users point the app at their
own asset folder instead.
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont


def _font(size=14):
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except Exception:
        return ImageFont.load_default()


def make(path, w, h, color, label, overlay=False):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if overlay:
        d.rectangle([0, 0, w - 1, h - 1], fill=color + (110,))
        d.rectangle([0, 0, w - 1, h - 1], outline=(255, 255, 255, 200), width=2)
    else:
        d.rectangle([0, 0, w - 1, h - 1], fill=color + (255,))
        d.rectangle([0, 0, w - 1, h - 1], outline=(0, 0, 0, 255), width=2)
    f = _font(max(10, min(18, h // 4)))
    # center label, wrap if needed
    lines = _wrap(label, w // (f.size // 2))
    y = h / 2 - (len(lines) * (f.size + 2)) / 2
    for ln in lines:
        tw = d.textlength(ln, font=f)
        d.text(((w - tw) / 2, y), ln, fill=(255, 255, 255, 255), font=f)
        y += f.size + 2
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
    print("wrote", path)


def _wrap(text, max_chars):
    words = text.split()
    lines, cur = [], ""
    for wd in words:
        if len(cur) + len(wd) + 1 <= max_chars or not cur:
            cur = (cur + " " + wd).strip()
        else:
            lines.append(cur); cur = wd
    if cur:
        lines.append(cur)
    return lines or [text]


ROOT = os.path.join(os.path.dirname(__file__), "sample_assets")

ASSETS = [
    ("floors/corridor_40x120.png", 40, 120, (60, 70, 90), "corridor"),
    ("floors/room_100x100.png", 100, 100, (80, 90, 110), "room"),
    ("floors/room_200x100.png", 200, 100, (80, 90, 110), "room wide"),
    ("walls/wall_10x50.png", 10, 50, (120, 120, 130), "wall"),
    ("walls/wall_25x50.png", 25, 50, (120, 120, 130), "wall"),
    ("props/terminal_25x50.png", 25, 50, (40, 160, 120), "terminal"),
    ("props/computer_10x50.png", 10, 50, (40, 160, 120), "computer"),
    ("props/keyboard_40x15.png", 40, 15, (180, 120, 40), "keyboard"),
    ("overlays/overlay_glow_100x100.png", 100, 100, (80, 200, 255), "glow", True),
    ("overlays/overlay_hazard_40x120.png", 40, 120, (255, 180, 40), "hazard", True),
]

if __name__ == "__main__":
    for a in ASSETS:
        make(os.path.join(ROOT, a[0]), *a[1:])
    print("\nSample assets ready in", ROOT)
