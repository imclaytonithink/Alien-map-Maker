"""Demo images for tests, generated on demand instead of being checked in."""
from __future__ import annotations

import atexit
import shutil
import tempfile

from generate_samples import generate

_ROOT: str | None = None


def sample_assets_dir() -> str:
    """Folder of freshly generated demo assets (created once per process)."""
    global _ROOT
    if _ROOT is None:
        _ROOT = tempfile.mkdtemp(prefix="sceneboard-samples-")
        atexit.register(shutil.rmtree, _ROOT, ignore_errors=True)
        generate(_ROOT)
    return _ROOT
