"""Scan a directory, move vertical photos (height > width) into a 'vertical/' subdir."""
import argparse
import shutil
from pathlib import Path

from PIL import Image, UnidentifiedImageError

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp", ".gif"}


def is_vertical(path: Path) -> bool:
    with Image.open(path) as img:
        w, h = img.size
    return h > w


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("directory", type=Path, help="Directory to scan")
    ap.add_argument("--dry-run", action="store_true", help="Print actions, do not move")
    args = ap.parse_args()

    src = args.directory.resolve()
    if not src.is_dir():
        raise SystemExit(f"Not a directory: {src}")

    dest = src / "vertical"
    moved = 0

    for entry in src.iterdir():
        if not entry.is_file() or entry.suffix.lower() not in IMAGE_EXTS:
            continue
        try:
            vertical = is_vertical(entry)
        except (UnidentifiedImageError, OSError) as e:
            print(f"skip {entry.name}: {e}")
            continue
        if not vertical:
            continue

        target = dest / entry.name
        if args.dry_run:
            print(f"[dry-run] {entry.name} -> vertical/")
        else:
            dest.mkdir(exist_ok=True)
            shutil.move(str(entry), str(target))
            print(f"moved {entry.name} -> vertical/")
        moved += 1

    print(f"\n{moved} vertical photo(s) {'would be ' if args.dry_run else ''}moved.")


if __name__ == "__main__":
    main()
