"""Run the repository's pure-Python checks and optional offscreen GUI smoke tests.

Use ``python run_tests.py --require-gui`` in CI after installing requirements.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
PURE_TESTS = [f"test_batch{number}.py" for number in range(1, 18)]
GUI_TESTS = [
    "test_generator.py",
    "test_gui2.py",
    "test_gui_headless.py",
    "test_gui_usability.py",
    "test_gui_fixes.py",
    "test_gui_generator.py",
    "test_gui_guides.py",
    "test_gui_tools.py",
    "test_gui_edit.py",
    "test_library_virtualization.py",
    "test_headless.py",
]


def _run(command, env):
    print("+", " ".join(map(str, command)), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def _qt_is_usable(env) -> tuple[bool, str]:
    probe = subprocess.run(
        [sys.executable, "-c",
         "from PyQt6.QtWidgets import QApplication; QApplication([])"],
        cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    return probe.returncode == 0, probe.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-gui", action="store_true",
        help="fail rather than skip GUI smoke tests if Qt is unavailable")
    args = parser.parse_args()

    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")

    for script in PURE_TESTS:
        _run([sys.executable, script], env)

    usable, reason = _qt_is_usable(env)
    if not usable:
        message = "PyQt6/offscreen Qt is unavailable"
        if reason:
            message += f": {reason.splitlines()[-1]}"
        if args.require_gui:
            print(f"ERROR: {message}", file=sys.stderr)
            return 1
        print(f"SKIP: GUI smoke tests — {message}")
        return 0

    with tempfile.TemporaryDirectory(prefix="sceneboard-test-") as temp:
        demo = os.path.join(temp, "demo.png")
        _run([sys.executable, "demo_render.py", "--output", demo], env)
        if not os.path.isfile(demo) or os.path.getsize(demo) == 0:
            raise RuntimeError("demo renderer did not produce a non-empty PNG")
        for script in GUI_TESTS:
            _run([sys.executable, script], env)

    print("All pure-Python and offscreen GUI checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
