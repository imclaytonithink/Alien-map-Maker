"""Build a low-resolution visual contact-sheet bundle from the RPG-Mobius packs.

The bundle contains only thumbnails and a path/index manifest, never originals.
It can optionally be sent as chunked commit comments by the real-pack CI job so
large private-to-the-sandbox downloads are not required for a manual review.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from PIL import Image, ImageDraw, ImageFont


PACK_MARKERS = {
    "geomorphs": "geomorphs-geomorphs-high-res",
    "custom_tiles": "custom-tiles-high-res",
    "symbols": "symbols-high-res",
}
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
PAGE_SIZE = 100
COLUMNS = 10
TILE_WIDTH = 76
TILE_HEIGHT = 76
THUMBNAIL_SIZE = (68, 58)
HEADER_HEIGHT = 34
PAGE_MARGIN = 8
COMMENT_CHUNK_SIZE = 40_000
COMMENT_MARKER = "<!-- asset-visual-audit:v1 "


def find_pack_archives(directory: str | os.PathLike[str]) -> dict[str, Path]:
    """Locate exactly one published archive for each pack."""
    root = Path(directory)
    archives = sorted(root.glob("*.zip"))
    matches: dict[str, Path] = {}
    for kind, marker in PACK_MARKERS.items():
        candidates = [path for path in archives if marker in path.name.casefold()]
        if len(candidates) != 1:
            raise ValueError(
                f"Expected exactly one {kind} pack matching {marker!r}; "
                f"found {len(candidates)}.")
        matches[kind] = candidates[0]
    if len(archives) != len(PACK_MARKERS):
        raise ValueError(f"Expected three pack ZIPs, found {len(archives)}.")
    return matches


def _image_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = [
        info for info in archive.infolist()
        if not info.is_dir()
        and PurePosixPath(info.filename.replace("\\", "/")).suffix.casefold()
        in SUPPORTED_EXTENSIONS
    ]
    return sorted(members, key=lambda info: info.filename.replace("\\", "/").casefold())


def _member_path(info: zipfile.ZipInfo) -> str:
    return str(PurePosixPath(info.filename.replace("\\", "/")))


def _draw_thumbnail(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> tuple[Image.Image, tuple[int, int]]:
    """Decode one image, preserving its aspect ratio and alpha on a neutral tile."""
    with archive.open(info, "r") as stream:
        with Image.open(stream) as source:
            source.seek(0)
            original_size = source.size
            # Let Pillow's reduced-gap thumbnail path shrink very large sources
            # before the final resample; converting a 7k square PNG to RGBA first
            # wastes both memory and time when the output is only 68 by 58 px.
            source.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS,
                             reducing_gap=3.0)
            thumb = source.convert("RGBA")
    tile_image = Image.new("RGBA", THUMBNAIL_SIZE, (235, 239, 241, 255))
    x = (THUMBNAIL_SIZE[0] - thumb.width) // 2
    y = (THUMBNAIL_SIZE[1] - thumb.height) // 2
    tile_image.alpha_composite(thumb, (x, y))
    return tile_image.convert("RGB"), original_size


def _render_page(archive: zipfile.ZipFile, kind: str, page_number: int,
                 page_members: list[zipfile.ZipInfo], first_asset: int,
                 total_assets: int, manifest_writer: csv.writer) -> Image.Image:
    rows = (len(page_members) + COLUMNS - 1) // COLUMNS
    sheet = Image.new(
        "RGB",
        (PAGE_MARGIN * 2 + COLUMNS * TILE_WIDTH,
         PAGE_MARGIN * 2 + HEADER_HEIGHT + rows * TILE_HEIGHT),
        (28, 42, 54),
    )
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    last_asset = first_asset + len(page_members) - 1
    draw.text((PAGE_MARGIN, PAGE_MARGIN),
              f"{kind.replace('_', ' ').title()} — page {page_number:03d} "
              f"— assets {first_asset:04d}–{last_asset:04d} of {total_assets:,}",
              fill=(244, 247, 249), font=font)

    for index, info in enumerate(page_members, start=1):
        column = (index - 1) % COLUMNS
        row = (index - 1) // COLUMNS
        x = PAGE_MARGIN + column * TILE_WIDTH
        y = PAGE_MARGIN + HEADER_HEIGHT + row * TILE_HEIGHT
        thumb, (width, height) = _draw_thumbnail(archive, info)
        tile = Image.new("RGB", (TILE_WIDTH - 2, TILE_HEIGHT - 2),
                         (224, 230, 233))
        tile.paste(thumb, ((tile.width - thumb.width) // 2, 2))
        sheet.paste(tile, (x + 1, y + 1))
        label_y = y + TILE_HEIGHT - 17
        draw.rectangle((x + 1, label_y, x + TILE_WIDTH - 2, y + TILE_HEIGHT - 2),
                       fill=(13, 25, 35))
        local_index = index
        draw.text((x + 4, label_y + 2), f"{local_index:03d}",
                  fill=(255, 255, 255), font=font)
        manifest_writer.writerow([
            f"{kind}-{page_number:03d}.jpg", local_index,
            _member_path(info), width, height, info.file_size,
        ])
    return sheet


def build_bundle(packs_dir: str | os.PathLike[str], output_path: str | os.PathLike[str]) -> tuple[int, int]:
    """Create contact sheets for every supported image in each source ZIP."""
    archives = find_pack_archives(packs_dir)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image_count = 0
    page_count = 0

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as bundle:
        manifest = io.StringIO(newline="")
        writer = csv.writer(manifest, delimiter="\t", lineterminator="\n")
        writer.writerow(["sheet", "tile", "original_path", "width", "height", "bytes"])
        for kind, archive_path in archives.items():
            with zipfile.ZipFile(archive_path) as archive:
                members = _image_members(archive)
                page_total = (len(members) + PAGE_SIZE - 1) // PAGE_SIZE
                for offset in range(0, len(members), PAGE_SIZE):
                    page_number = offset // PAGE_SIZE + 1
                    page_members = members[offset:offset + PAGE_SIZE]
                    image = _render_page(archive, kind, page_number, page_members,
                                         offset + 1, len(members), writer)
                    buffer = io.BytesIO()
                    image.save(buffer, format="JPEG", quality=58, optimize=False,
                               progressive=False)
                    bundle.writestr(
                        f"sheets/{kind}-{page_number:03d}.jpg", buffer.getvalue())
                    image_count += len(page_members)
                    page_count += 1
                    print(f"Rendered {kind} sheet {page_number}/{page_total} "
                          f"({image_count:,} images total so far).", flush=True)
        bundle.writestr("manifest.tsv", manifest.getvalue().encode("utf-8"))
        bundle.writestr(
            "README.txt",
            ("Manual visual review contact sheets for the three RPG-Mobius packs.\n"
             "Each sheet shows up to 100 reduced thumbnails. manifest.tsv maps each\n"
             "sheet tile number to the image's original ZIP path and dimensions.\n"
             "The original-resolution images are not included in this bundle.\n")
            .encode("utf-8"),
        )
    return image_count, page_count


def _post_comment(repository: str, commit_sha: str, body: str, token: str) -> None:
    payload = json.dumps({"body": body}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/commits/{commit_sha}/comments",
        data=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "Alien-map-Maker-visual-audit",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status != 201:
                raise RuntimeError(f"GitHub comment API returned HTTP {response.status}.")
    except urllib.error.HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Could not publish visual-audit chunk (HTTP {exc.code}): {detail}") from exc


def publish_bundle(bundle_path: str | os.PathLike[str], repository: str,
                   commit_sha: str, token: str) -> int:
    """Base64-split a small thumbnail bundle into retrievable commit comments."""
    data = Path(bundle_path).read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    chunks = [encoded[start:start + COMMENT_CHUNK_SIZE]
              for start in range(0, len(encoded), COMMENT_CHUNK_SIZE)]
    digest = hashlib.sha256(data).hexdigest()
    total = len(chunks)
    for part, chunk in enumerate(chunks, start=1):
        marker = f"{COMMENT_MARKER}sha256={digest} part={part}/{total} -->\n"
        body = marker + chunk
        if len(body.encode("utf-8")) > 50_000:
            raise AssertionError("Visual-audit comment exceeds the safe size limit.")
        _post_comment(repository, commit_sha, body, token)
        print(f"Published visual audit chunk {part}/{total}.", flush=True)
        if part < total:
            # Keep comment creation under GitHub's secondary write-rate limits.
            time.sleep(1.0)
    print(f"Published {total} chunks ({len(data):,} bytes; sha256 {digest}).")
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packs-dir", required=True,
                        help="directory containing the three original ZIP packs")
    parser.add_argument("--output", required=True,
                        help="path for the thumbnail-only ZIP bundle")
    parser.add_argument("--publish-commit", default="",
                        help="optionally publish the bundle in comments on this commit")
    args = parser.parse_args()

    image_count, page_count = build_bundle(args.packs_dir, args.output)
    bundle_size = Path(args.output).stat().st_size
    print(f"Built {page_count} contact sheets for {image_count:,} images "
          f"({bundle_size:,} byte bundle) at {args.output}.")

    if args.publish_commit:
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        token = os.environ.get("GH_TOKEN", "")
        if not repository or not token:
            parser.error("--publish-commit requires GITHUB_REPOSITORY and GH_TOKEN.")
        publish_bundle(args.output, repository, args.publish_commit, token)


if __name__ == "__main__":
    main()
