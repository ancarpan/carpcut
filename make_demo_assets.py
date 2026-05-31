"""Generate placeholder JPGs for the CarpCut demo (example.yaml).

Run once after `uv sync`:

    uv run python make_demo_assets.py

Creates ./demo_assets/demo_01.jpg ... demo_06.jpg — 1920x1080 gradient color
cards with a centered label. Idempotent: skips files that already exist.
"""
import colorsys
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).parent / "demo_assets"
W, H = 1920, 1080
N_CLIPS = 6


def _font(size_px):
    for fp in ('/System/Library/Fonts/Helvetica.ttc',
               '/System/Library/Fonts/Supplemental/Arial.ttf',
               '/Library/Fonts/Arial.ttf',
               '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'):
        try:
            return ImageFont.truetype(fp, size_px)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size_px)
    except TypeError:
        return ImageFont.load_default()


def make_clip(idx, path):
    hue = (idx / N_CLIPS) % 1.0
    img = Image.new('RGB', (W, H))
    px = img.load()
    # Horizontal gradient: same hue, lightness varies left → right
    for x in range(W):
        light = 0.30 + 0.45 * (x / W)
        r, g, b = colorsys.hls_to_rgb(hue, light, 0.65)
        col = (int(r * 255), int(g * 255), int(b * 255))
        for y in range(H):
            px[x, y] = col

    draw = ImageDraw.Draw(img)
    label = f"Demo Clip {idx + 1}"
    font = _font(180)
    try:
        bbox = draw.textbbox((0, 0), label, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except AttributeError:
        tw, th = font.getsize(label)
    x = (W - tw) // 2
    y = (H - th) // 2
    # Drop shadow + main text
    draw.text((x + 6, y + 6), label, fill=(0, 0, 0), font=font)
    draw.text((x, y), label, fill=(255, 255, 255), font=font)

    img.save(path, 'JPEG', quality=88)


def main():
    OUT_DIR.mkdir(exist_ok=True)
    for i in range(N_CLIPS):
        path = OUT_DIR / f"demo_{i + 1:02d}.jpg"
        if path.exists():
            print(f"  ✓ {path.name} (exists, skipped)")
            continue
        make_clip(i, path)
        print(f"  ✓ {path.name} generated")
    print(f"\nDemo assets in {OUT_DIR}")


if __name__ == "__main__":
    main()
