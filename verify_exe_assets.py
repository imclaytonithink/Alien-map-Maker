"""Verify that the high-resolution asset packs survive into a built SceneBoard.

build.bat downloads the release ZIPs, filters them to supported images with
filter_asset_packs.py, and bundles the filtered ZIPs with the app under
``asset_packs/``: as files inside the app folder (the default folder build,
``dist\\SceneBoard\\_internal\\asset_packs``) or inside a one-file EXE
(``build.bat onefile``). On first launch the app installs them into the user's
persistent asset store. This script checks every hop of that chain:

1. release ZIP -> filtered ZIP (``--source-dir``): every supported image
   survived filtering. Excluded non-image files are listed, and image-like
   formats the app cannot import are flagged.
2. filtered ZIP -> build: each ZIP is bundled byte-for-byte (SHA-256), and
   ``--zip`` checks the shareable ``SceneBoard-Windows.zip`` holds the app and
   every pack (CRC-32).
3. build -> asset store (``--launch``): a first launch installs every image
   byte-for-byte (CRC-32), and the app's own AssetLibrary scanner lists all
   of them.

``--icon`` also checks that the EXE carries the given .ico, image for image.

Run it with the Python environment that built the executable, because it uses
PyInstaller's archive and resource readers and PyQt6's standard paths. After
build.bat:

    venv\\Scripts\\python verify_exe_assets.py --exe dist\\SceneBoard\\SceneBoard.exe
        --zip dist\\SceneBoard-Windows.zip --icon ui\\icons\\SceneBoard.ico
        --source-dir "%TEMP%\\SceneBoard-highres-packs"
        --filtered-dir "%TEMP%\\SceneBoard-supported-asset-packs" --launch

``--launch`` force-closes the app once the install has been verified. If the
packs are already installed for this user, the existing copies are verified
instead (``--require-fresh`` turns that into a failure, as on CI).
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.asset_manager import (  # noqa: E402
    _IMPORT_MARKER,
    SUPPORTED_EXTS,
    AssetLibrary,
    _normalize_archive_path,
    _safe_archive_folder,
)
from ui.branding import SETTINGS_ID  # noqa: E402

ORGANIZATION = "ArenaMaps"   # must match setOrganizationName() in main.py
EMBED_PREFIX = "asset_packs/"
STAGING_PREFIX = ".sceneboard-import-"
# Formats that look like artwork but are not accepted by the importer.
IMAGE_LIKE_UNSUPPORTED = {
    ".avif", ".dds", ".exr", ".gif", ".heic", ".ico", ".jfif", ".jpe", ".kra",
    ".psd", ".svg", ".tga", ".tif", ".xcf",
}


class Report:
    """Collect pass/fail checks and per-pack facts for the console and summary."""

    def __init__(self):
        self.checks: list[tuple[bool, str]] = []
        self.packs: dict[str, dict] = collections.OrderedDict()
        self.notes: list[str] = []
        self.install_seconds: float | None = None

    def check(self, ok: bool, message: str) -> bool:
        self.checks.append((bool(ok), message))
        print(("PASS  " if ok else "FAIL  ") + message, flush=True)
        return bool(ok)

    def note(self, message: str):
        self.notes.append(message)
        print("NOTE  " + message, flush=True)

    def pack(self, name: str) -> dict:
        return self.packs.setdefault(name, {})

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(ok for ok, _ in self.checks)


def _mb(size: int) -> str:
    return f"{size / 1_000_000:,.1f} MB"


def _file_digest(path: str, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_crc32(path: str) -> int:
    crc = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            crc = zlib.crc32(chunk, crc)
    return crc & 0xFFFFFFFF


def _zip_names(directory: str) -> list[str]:
    return sorted(name for name in os.listdir(directory)
                  if name.lower().endswith(".zip") and
                  os.path.isfile(os.path.join(directory, name)))


# ---------------------------------------------------------------------------
# 1. release ZIP -> filtered ZIP
# ---------------------------------------------------------------------------
def source_inventory(path: str) -> tuple[int, collections.Counter]:
    """Count supported images and skipped files using the importer's rules."""
    images = 0
    skipped: collections.Counter = collections.Counter()
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir() or info.filename.endswith(("/", "\\")):
                continue
            relative = _normalize_archive_path(info.filename)
            if relative is None:
                skipped["(unsafe path)"] += 1
            elif stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
                skipped["(symlink)"] += 1
            elif relative.lower().endswith(SUPPORTED_EXTS):
                images += 1
            else:
                skipped[os.path.splitext(relative)[1].lower() or "(no extension)"] += 1
    return images, skipped


def filtered_entries(path: str) -> dict[str, tuple[int, int]]:
    """Map each file in a filtered ZIP to its (CRC-32, size)."""
    with zipfile.ZipFile(path) as archive:
        return {info.filename: (info.CRC, info.file_size)
                for info in archive.infolist() if not info.is_dir()}


def check_filtering(report: Report, source_dir: str, filtered_dir: str,
                    entries_by_pack: dict) -> None:
    sources = _zip_names(source_dir)
    report.check(bool(sources), f"Release ZIPs found in {source_dir}: {len(sources)}")
    for name in sources:
        info = report.pack(name)
        source_path = os.path.join(source_dir, name)
        images, skipped = source_inventory(source_path)
        info["source_size"] = os.path.getsize(source_path)
        info["source_images"] = images
        info["skipped"] = skipped
        entries = entries_by_pack.get(name)
        if entries is None:
            report.check(False, f"{name}: no filtered copy was produced for the EXE")
            continue
        report.check(len(entries) == images,
                     f"{name}: all {images:,} supported images kept by the build "
                     f"filter (filtered ZIP has {len(entries):,})")
        risky = {ext: count for ext, count in skipped.items()
                 if ext in IMAGE_LIKE_UNSUPPORTED}
        if risky:
            listed = ", ".join(f"{count:,} {ext}" for ext, count in sorted(risky.items()))
            report.note(f"{name}: artwork in formats SceneBoard cannot import was "
                        f"left out: {listed}")


# ---------------------------------------------------------------------------
# 2. filtered ZIP -> build (folder build or one-file EXE)
# ---------------------------------------------------------------------------
def app_layout(exe: str) -> dict:
    """How a build stores its files. A folder build keeps them beside the EXE
    (PyInstaller 6 puts them in ``_internal``); a one-file build inside it."""
    folder = os.path.dirname(os.path.abspath(exe))
    for root in (os.path.join(folder, "_internal"), folder):
        packs = os.path.join(root, EMBED_PREFIX.rstrip("/"))
        if os.path.isdir(packs):
            return {"kind": "folder", "app_dir": folder, "root": root, "packs": packs}
    return {"kind": "onefile", "app_dir": folder}


def build_size(exe: str, layout: dict) -> int:
    if layout["kind"] != "folder":
        return os.path.getsize(exe)
    total = 0
    for dirpath, _dirs, files in os.walk(layout["app_dir"]):
        for filename in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, filename))
            except OSError:
                pass
    return total


def describe_build(exe: str, layout: dict) -> str:
    if layout["kind"] == "folder":
        return (f"folder build {os.path.basename(layout['app_dir'])}{os.sep} "
                f"({_mb(build_size(exe, layout))})")
    return f"{os.path.basename(exe)} ({_mb(os.path.getsize(exe))})"


def check_folder_packs(report: Report, layout: dict, entries_by_pack: dict,
                       filtered_dir: str) -> bool:
    packs = layout["packs"]
    present = set(_zip_names(packs))
    shown = os.path.relpath(packs, os.path.dirname(layout["app_dir"]))
    report.check(bool(present), f"Folder build holds {len(present)} asset pack(s) in {shown}")
    unexpected = sorted(present - set(entries_by_pack))
    report.check(not unexpected,
                 "Folder build holds no stale/unknown packs" +
                 (f" (found: {', '.join(unexpected)})" if unexpected else ""))
    for name in entries_by_pack:
        info = report.pack(name)
        expected_path = os.path.join(filtered_dir, name)
        bundled = os.path.join(packs, name)
        info["filtered_size"] = os.path.getsize(expected_path)
        if name not in present:
            info["embedded"] = False
            report.check(False, f"{name}: missing from the folder build")
            continue
        same = (os.path.getsize(bundled) == info["filtered_size"] and
                _file_digest(bundled) == _file_digest(expected_path))
        info["embedded"] = same
        report.check(same, f"{name}: bundled byte-for-byte in the app folder "
                           f"({_mb(info['filtered_size'])}, SHA-256 match)" if same else
                           f"{name}: the app folder holds a different copy "
                           f"({_mb(os.path.getsize(bundled))}) than the filtered ZIP — "
                           f"SHA-256 mismatch; was it built from other packs?")
    for dirpath, _dirs, files in os.walk(layout["root"]):
        if os.path.basename(dirpath).lower() == "platforms" and any(
                "offscreen" in filename.lower() for filename in files):
            return True
    return False


def check_zip(report: Report, zip_path: str, exe: str, layout: dict,
              entries_by_pack: dict, filtered_dir: str) -> None:
    """The shareable zip must hold the whole app folder, packs included."""
    if layout["kind"] != "folder":
        report.check(False, "--zip only applies to folder builds")
        return
    if not os.path.isfile(zip_path):
        report.check(False, f"Distribution zip not found: {zip_path}")
        return
    app_name = os.path.basename(layout["app_dir"])
    exe_name = os.path.basename(exe)
    pack_dir = os.path.relpath(layout["packs"], layout["app_dir"]).replace(os.sep, "/")
    with zipfile.ZipFile(zip_path) as archive:
        members = {info.filename.replace("\\", "/"): info for info in archive.infolist()}
    report.check(f"{app_name}/{exe_name}" in members,
                 f"{os.path.basename(zip_path)} ({_mb(os.path.getsize(zip_path))}) "
                 f"holds {app_name}/{exe_name}")
    on_disk = sum(len(files) for _dirpath, _dirs, files in os.walk(layout["app_dir"]))
    in_zip = sum(1 for name, info in members.items()
                 if not info.is_dir() and name.startswith(app_name + "/"))
    report.check(in_zip == on_disk,
                 f"{os.path.basename(zip_path)} holds all {on_disk:,} files of the "
                 f"app folder (found {in_zip:,})")
    for name in entries_by_pack:
        info = members.get(f"{app_name}/{pack_dir}/{name}")
        expected_path = os.path.join(filtered_dir, name)
        same = bool(info) and (info.file_size == os.path.getsize(expected_path) and
                               info.CRC == _file_crc32(expected_path))
        report.check(same, f"{name}: inside {os.path.basename(zip_path)} byte-for-byte "
                           f"(CRC-32 match)" if same else
                           f"{name}: missing or different inside {os.path.basename(zip_path)}")


def _ico_images(path: str) -> list[bytes]:
    import struct
    with open(path, "rb") as handle:
        data = handle.read()
    _reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    if kind != 1:
        raise ValueError(f"{path} is not an .ico file")
    images = []
    for index in range(count):
        size, offset = struct.unpack_from("<II", data, 6 + 16 * index + 8)
        images.append(data[offset:offset + size])
    return images


def check_icon(report: Report, exe: str, icon_path: str) -> None:
    """The EXE should carry the app icon: every image of the .ico must be
    among the EXE's RT_ICON resources."""
    try:
        wanted = _ico_images(icon_path)
    except (OSError, ValueError) as exc:
        report.check(False, f"Could not read the icon {icon_path}: {exc}")
        return
    try:
        from PyInstaller.utils.win32 import winresource
        resources = winresource.get_resources(exe, types=[3])     # 3 = RT_ICON
    except Exception as exc:          # not on Windows, or no resource reader
        message = f"Could not read the EXE's icon resources: {exc}"
        if os.name == "nt":
            report.check(False, message)
        else:
            report.note(message + " (icon resources can only be read on Windows)")
        return
    blobs = {bytes(data) for names in resources.values() for langs in names.values()
             for data in langs.values()}
    found = sum(1 for image in wanted if image in blobs)
    report.check(found == len(wanted),
                 f"EXE carries the app icon {os.path.basename(icon_path)} "
                 f"({found}/{len(wanted)} sizes)")


def check_embedded(report: Report, exe: str, filtered_dir: str,
                   entries_by_pack: dict) -> bool:
    layout = app_layout(exe)
    if layout["kind"] == "folder":
        return check_folder_packs(report, layout, entries_by_pack, filtered_dir)
    from PyInstaller.archive.readers import CArchiveReader

    reader = CArchiveReader(exe)
    embedded = {}
    for toc_name in reader.toc:
        normalized = toc_name.replace("\\", "/")
        if normalized.startswith(EMBED_PREFIX) and normalized.lower().endswith(".zip"):
            embedded[normalized[len(EMBED_PREFIX):]] = toc_name
    report.check(bool(embedded),
                 f"EXE contains {len(embedded)} asset pack(s) under {EMBED_PREFIX}")

    unexpected = sorted(set(embedded) - set(entries_by_pack))
    report.check(not unexpected,
                 "EXE holds no stale/unknown packs" +
                 (f" (found: {', '.join(unexpected)})" if unexpected else ""))

    for name in entries_by_pack:
        info = report.pack(name)
        expected_path = os.path.join(filtered_dir, name)
        toc_name = embedded.get(name)
        if toc_name is None:
            info["embedded"] = False
            report.check(False, f"{name}: missing from the EXE")
            continue
        data = reader.extract(toc_name)
        same = (len(data) == os.path.getsize(expected_path) and
                hashlib.sha256(data).hexdigest() == _file_digest(expected_path))
        info["embedded"] = same
        info["filtered_size"] = os.path.getsize(expected_path)
        report.check(same, f"{name}: embedded byte-for-byte in the EXE "
                           f"({_mb(len(data))}, SHA-256 match)" if same else
                           f"{name}: the EXE holds a different copy ({_mb(len(data))}) "
                           f"than the filtered ZIP ({_mb(info['filtered_size'])}) — "
                           f"SHA-256 mismatch; was it built from other packs?")
        del data

    return any("platforms" in name.replace("\\", "/").split("/") and
               "offscreen" in name.lower() for name in reader.toc)


# ---------------------------------------------------------------------------
# 3. executable -> persistent asset store
# ---------------------------------------------------------------------------
def frozen_asset_store_path() -> str:
    """Return the asset store a packaged build uses (see ui/branding.py)."""
    from PyQt6.QtCore import QCoreApplication, QStandardPaths

    app = QCoreApplication.instance() or QCoreApplication([sys.argv[0]])
    app.setOrganizationName(ORGANIZATION)
    app.setApplicationName(SETTINGS_ID)
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation)
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".map-studio")
    return os.path.abspath(os.path.join(base, "asset_store"))


def installed_folder(store: str, zip_name: str) -> str | None:
    """Find a pack's import folder exactly as the app's first-launch check does."""
    base = _safe_archive_folder(zip_name)
    try:
        folders = sorted(os.listdir(store))
    except OSError:
        return None
    for folder in folders:
        if folder != base and not folder.startswith(base + " ("):
            continue
        marker = os.path.join(store, folder, _IMPORT_MARKER)
        try:
            with open(marker, encoding="utf-8") as fh:
                metadata = json.load(fh)
        except (OSError, ValueError):
            continue
        if (metadata.get("format") == "sceneboard-asset-archive" and
                metadata.get("source") == zip_name):
            return os.path.join(store, folder)
    return None


def _staging_bytes(store: str) -> int:
    total = 0
    try:
        names = os.listdir(store)
    except OSError:
        return -1
    for name in names:
        if not name.startswith(STAGING_PREFIX):
            continue
        for dirpath, _dirs, files in os.walk(os.path.join(store, name)):
            for filename in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, filename))
                except OSError:
                    pass
    return total


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        # A one-file bootloader runs the app in a child process; /T ends both.
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       capture_output=True, check=False)
    else:
        process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=30)


def launch_and_wait(report: Report, exe: str, store: str, names: list[str],
                    offscreen: bool, timeout: float, stall_timeout: float) -> bool:
    env = os.environ.copy()
    if offscreen:
        env["QT_QPA_PLATFORM"] = "offscreen"
    workdir = tempfile.mkdtemp(prefix="sceneboard-exe-launch-")
    log_path = os.path.join(workdir, "app-output.log")
    print(f"Launching {exe} (platform: {'offscreen' if offscreen else 'native'}); "
          f"waiting for the first-launch install into {store}", flush=True)
    started = time.monotonic()
    with open(log_path, "wb") as log:
        process = subprocess.Popen([os.path.abspath(exe)], cwd=workdir, env=env,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        try:
            last_state = None
            last_change = started
            while True:
                now = time.monotonic()
                done = [name for name in names if installed_folder(store, name)]
                state = (len(done), _staging_bytes(store))
                if state != last_state:
                    last_state, last_change = state, now
                    progress = (f"{state[1] / 1_000_000:,.0f} MB staged"
                                if state[1] >= 0 else "store not created yet")
                    print(f"  {now - started:6.0f}s  installed {len(done)}/{len(names)} "
                          f"packs; {progress}", flush=True)
                if len(done) == len(names):
                    elapsed = now - started
                    report.install_seconds = elapsed
                    return report.check(True, f"First launch installed all {len(names)} "
                                              f"packs in {elapsed:,.0f}s")
                code = process.poll()
                if code is not None:
                    report.check(False, f"The EXE exited with code {code} before the "
                                        f"install finished ({len(done)}/{len(names)} packs)")
                    break
                if now - started > timeout:
                    report.check(False, f"Timed out after {timeout:,.0f}s waiting for the "
                                        f"install ({len(done)}/{len(names)} packs)")
                    break
                if now - last_change > stall_timeout:
                    report.check(False, f"No install progress for {stall_timeout:,.0f}s "
                                        f"({len(done)}/{len(names)} packs) — the app may "
                                        f"be showing an error dialog")
                    break
                time.sleep(3)
        finally:
            _stop(process)
    try:
        with open(log_path, "rb") as fh:
            tail = fh.read()[-3000:].decode("utf-8", "replace").strip()
        if tail:
            print("---- app output (tail) ----\n" + tail + "\n---------------------------")
    except OSError:
        pass
    return False


def check_installed(report: Report, store: str, entries_by_pack: dict) -> None:
    library = AssetLibrary()
    library.scan(store)
    listed = collections.Counter(asset.path.split("/", 1)[0] for asset in library.assets)

    for name, entries in entries_by_pack.items():
        info = report.pack(name)
        folder = installed_folder(store, name)
        if not folder:
            info["installed"] = "missing"
            report.check(False, f"{name}: not installed in {store}")
            continue
        # Compare paths case-insensitively: the build filter makes every path
        # unique ignoring case, and Windows merges folders such as "Symbols"
        # and "symbols" into one on disk.
        expected = {relative.casefold(): (relative, crc, size)
                    for relative, (crc, size) in entries.items()}
        on_disk = {}
        for dirpath, _dirs, files in os.walk(folder):
            for filename in files:
                full = os.path.join(dirpath, filename)
                relative = os.path.relpath(full, folder).replace(os.sep, "/")
                if relative != _IMPORT_MARKER:
                    on_disk[relative.casefold()] = (relative, full)
        missing = sorted(expected[key][0] for key in set(expected) - set(on_disk))
        extra = sorted(on_disk[key][0] for key in set(on_disk) - set(expected))
        damaged = sorted(
            expected[key][0] for key in set(expected) & set(on_disk)
            if os.path.getsize(on_disk[key][1]) != expected[key][2] or
            _file_crc32(on_disk[key][1]) != expected[key][1])
        intact = len(entries) - len(missing) - len(damaged)
        info["installed"] = intact
        info["folder"] = os.path.basename(folder)
        detail = ""
        for label, items in (("missing", missing), ("damaged", damaged),
                             ("unexpected", extra)):
            if items:
                detail += f"; {len(items):,} {label}, e.g. {items[0]}"
        report.check(not (missing or damaged or extra),
                     f"{name}: {intact:,}/{len(entries):,} images installed "
                     f"byte-for-byte (CRC-32){detail}")

        seen = listed.get(os.path.basename(folder), 0)
        info["listed"] = seen
        report.check(seen == len(entries),
                     f"{name}: the app's library scanner lists {seen:,}/"
                     f"{len(entries):,} images")


# ---------------------------------------------------------------------------
def write_summary(report: Report, path: str, exe: str, store: str | None) -> None:
    yes, no = "✅", "❌"
    lines = ["## SceneBoard build asset verification", "",
             f"**Result: {'PASS' if report.ok else 'FAIL'}** — "
             f"{describe_build(exe, app_layout(exe))}", ""]
    lines += ["| Pack | Release ZIP | Images in release | Filtered ZIP in build "
              "| Installed on first launch | Listed by library |",
              "|---|---|---|---|---|---|"]
    for name, info in report.packs.items():
        source = (f"{_mb(info['source_size'])}" if "source_size" in info else "—")
        images = (f"{info['source_images']:,}" if "source_images" in info else "—")
        embedded = info.get("embedded")
        embedded_text = ("—" if embedded is None else
                         f"{yes} {_mb(info.get('filtered_size', 0))}, SHA-256 match"
                         if embedded else f"{no} missing or different")
        installed = info.get("installed")
        installed_text = ("—" if installed is None else
                          f"{no} not installed" if installed == "missing" else
                          f"{yes if installed == info.get('expected') else no} "
                          f"{installed:,}/{info.get('expected', 0):,} byte-identical")
        listed = info.get("listed")
        listed_text = ("—" if listed is None else
                       f"{yes if listed == info.get('expected') else no} {listed:,}")
        lines.append(f"| `{name}` | {source} | {images} | {embedded_text} "
                     f"| {installed_text} | {listed_text} |")
    skipped_rows = [(name, info["skipped"]) for name, info in report.packs.items()
                    if info.get("skipped")]
    if skipped_rows:
        lines += ["", "Non-image files intentionally left out by the build filter:", ""]
        for name, skipped in skipped_rows:
            listed = ", ".join(f"{count:,}× `{ext}`" for ext, count in
                               sorted(skipped.items(), key=lambda item: (-item[1], item[0])))
            lines.append(f"- `{name}`: {listed}")
    if store:
        lines += ["", f"Asset store checked: `{store}`"]
    if report.install_seconds is not None:
        lines.append(f"First-launch install time: {report.install_seconds:,.0f} s")
    if report.notes:
        lines += ["", "Notes:", ""] + [f"- {note}" for note in report.notes]
    lines += ["", "<details><summary>All checks</summary>", ""]
    lines += [f"- {yes if ok else no} {message}" for ok, message in report.checks]
    lines += ["", "</details>", ""]
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _escape_command(value: str, property_value: bool = False) -> str:
    value = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if property_value:
        value = value.replace(":", "%3A").replace(",", "%2C")
    return value


def emit_github_annotations(report: Report, exe: str) -> None:
    """Surface the results at the top of the GitHub Actions run page."""
    def annotate(level: str, title: str, message: str):
        print(f"::{level} title={_escape_command(title, True)}::"
              f"{_escape_command(message)}", flush=True)

    for name, info in report.packs.items():
        parts = []
        if "source_images" in info:
            parts.append(f"{info['source_images']:,} images in the "
                         f"{_mb(info['source_size'])} release ZIP")
        if info.get("embedded"):
            parts.append(f"bundled byte-for-byte "
                         f"({_mb(info.get('filtered_size', 0))})")
        elif "embedded" in info:
            parts.append("NOT bundled correctly")
        installed = info.get("installed")
        if installed == "missing":
            parts.append("NOT installed on first launch")
        elif installed is not None:
            parts.append(f"{installed:,}/{info.get('expected', 0):,} installed "
                         f"byte-for-byte on first launch")
        if info.get("listed") is not None:
            parts.append(f"library lists {info['listed']:,}")
        if info.get("skipped"):
            parts.append("left out as non-images: " + ", ".join(
                f"{count:,} {ext}" for ext, count in sorted(info["skipped"].items())))
        annotate("notice", name, "; ".join(parts) or "no details")
    for note in report.notes:
        annotate("warning", "Build asset verification note", note)
    failures = [message for ok, message in report.checks if not ok]
    for message in failures[:10]:
        annotate("error", "Build asset verification failed", message)
    passed = len(report.checks) - len(failures)
    timing = (f"; first-launch install took {report.install_seconds:,.0f} s"
              if report.install_seconds is not None else "")
    annotate("notice" if report.ok else "error",
             f"Build asset verification {'PASSED' if report.ok else 'FAILED'}",
             f"{passed}/{len(report.checks)} checks passed for "
             f"{describe_build(exe, app_layout(exe))}{timing}.")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exe", required=True,
                        help="built executable to verify (dist\\SceneBoard\\SceneBoard.exe "
                             "for a folder build)")
    parser.add_argument("--zip", dest="zip_path",
                        help="shareable zip of a folder build to verify as well")
    parser.add_argument("--icon", help=".ico file the EXE should carry")
    parser.add_argument("--filtered-dir", required=True,
                        help="image-only ZIPs that build.bat embedded")
    parser.add_argument("--source-dir",
                        help="original release ZIPs downloaded by build.bat")
    parser.add_argument("--expect", action="append", default=[], metavar="ZIP_NAME",
                        help="pack ZIP name that must be present (repeatable)")
    parser.add_argument("--launch", action="store_true",
                        help="run the EXE and verify the first-launch install")
    parser.add_argument("--require-fresh", action="store_true",
                        help="fail if the packs are already installed before launch")
    parser.add_argument("--platform", choices=("auto", "offscreen", "native"),
                        default="auto",
                        help="Qt platform for --launch (auto = offscreen when bundled)")
    parser.add_argument("--timeout", type=float, default=1800,
                        help="maximum seconds to wait for the install")
    parser.add_argument("--stall-timeout", type=float, default=600,
                        help="fail after this many seconds without install progress")
    parser.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY", ""),
                        help="append a Markdown report here (default: $GITHUB_STEP_SUMMARY)")
    args = parser.parse_args()

    report = Report()
    store = None
    if not os.path.isfile(args.exe):
        report.check(False, f"Executable not found: {args.exe}")
        return 1
    if not os.path.isdir(args.filtered_dir):
        report.check(False, f"Filtered pack directory not found: {args.filtered_dir}")
        return 1

    names = _zip_names(args.filtered_dir)
    entries_by_pack = collections.OrderedDict(
        (name, filtered_entries(os.path.join(args.filtered_dir, name))) for name in names)
    report.check(bool(names), f"Filtered packs to verify: {len(names)}")
    for name in args.expect:
        report.check(name in entries_by_pack, f"Expected pack is part of the build: {name}")
    for name, entries in entries_by_pack.items():
        info = report.pack(name)
        info["expected"] = len(entries)
        unsupported = [path for path in entries if not path.lower().endswith(SUPPORTED_EXTS)]
        report.check(entries and not unsupported,
                     f"{name}: filtered ZIP holds {len(entries):,} supported images only"
                     + (f" ({len(unsupported):,} other files)" if unsupported else ""))

    if args.source_dir:
        check_filtering(report, args.source_dir, args.filtered_dir, entries_by_pack)

    offscreen_bundled = check_embedded(report, args.exe, args.filtered_dir, entries_by_pack)
    if args.zip_path:
        check_zip(report, args.zip_path, args.exe, app_layout(args.exe),
                  entries_by_pack, args.filtered_dir)
    if args.icon:
        check_icon(report, args.exe, args.icon)

    if args.launch and entries_by_pack:
        store = frozen_asset_store_path()
        already = [name for name in entries_by_pack if installed_folder(store, name)]
        if already and args.require_fresh:
            report.check(False, f"{len(already)} pack(s) were already installed in "
                                f"{store}; a first launch cannot be observed")
        else:
            if already:
                report.note(f"{len(already)} pack(s) were already installed for this "
                            "user, so the existing copies were verified (no first-launch "
                            "install was observed).")
            if len(already) < len(entries_by_pack):
                offscreen = (args.platform == "offscreen" or
                             (args.platform == "auto" and offscreen_bundled))
                launch_and_wait(report, args.exe, store, list(entries_by_pack),
                                offscreen, args.timeout, args.stall_timeout)
            check_installed(report, store, entries_by_pack)

    if args.summary:
        write_summary(report, args.summary, args.exe, store)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        emit_github_annotations(report, args.exe)
    passed = sum(ok for ok, _ in report.checks)
    print(f"\n{'ALL CHECKS PASSED' if report.ok else 'VERIFICATION FAILED'}: "
          f"{passed}/{len(report.checks)} checks passed.")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
