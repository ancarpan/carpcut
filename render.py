#!/usr/bin/env python3
"""
CarpCut Renderer
Reads a YAML file and renders an MP4 video via FFmpeg.
Usage: python render.py example.yaml
"""

import argparse
import yaml
import subprocess
import sys
import os
import json
import tempfile
import shutil
from pathlib import Path

# ─────────────────────────────────────────────
# RENDER PROFILE (mutated by --preview / --mobile)
# ─────────────────────────────────────────────

RENDER = {
    'w': 1920,
    'h': 1080,
    'fps': 25,
    'preset': 'fast',
    'crf': 18,
    'audio_bitrate': '192k',
}

# Named profiles applied over RENDER by the CLI flags.
PROFILES = {
    'preview': dict(w=960, h=540, fps=10,
                    preset='ultrafast', crf=28, audio_bitrate='128k'),
    'mobile': dict(w=1280, h=720, fps=25,
                   preset='medium', crf=24, audio_bitrate='128k'),
}

# ─────────────────────────────────────────────
# AVAILABLE EFFECTS
# ─────────────────────────────────────────────

def effect_none(clip, idx):
    """No effect, raw clip."""
    return clip['file'], []

def effect_ken_burns(clip, idx):
    """Slow zoom on photo. direction: in | out"""
    duration = clip.get('duration', 4)
    direction = clip.get('zoom', 'in')
    w, h = RENDER['w'], RENDER['h']
    fps = RENDER['fps']
    n_frames = int(duration * fps)

    if direction == 'in':
        zoompan = (
            f"zoompan=z='min(zoom+0.0015,1.3)':d={n_frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={w}x{h},fps={fps}"
        )
    else:
        zoompan = (
            f"zoompan=z='if(eq(on,1),1.3,max(zoom-0.0015,1))':d={n_frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={w}x{h},fps={fps}"
        )
    return clip['file'], [zoompan]

def effect_focus_in(clip, idx):
    """Blurred → sharp. Handled specially in build_clip_segment via filter_complex
    (split → static boxblur → blend), because boxblur radius is init-time only and
    cannot be animated with `n`. Kept here for EFFECTS_MAP registration only."""
    return clip['file'], []

def effect_focus_in_grid(clip, idx, grid_png):
    """Focus in with camera grid overlay."""
    file, filters = effect_focus_in(clip, idx)
    # grid is added as a separate overlay during compositing
    return file, filters

def effect_vignette(clip, idx):
    """Dark vignette edges."""
    return clip['file'], ["vignette=PI/4"]

def effect_film_grain(clip, idx):
    """Cinematic film grain."""
    return clip['file'], ["noise=alls=8:allf=t+u"]

def effect_fade_in(clip, idx):
    """Fade from black."""
    duration = clip.get('fade_duration', 1.0)
    return clip['file'], [f"fade=t=in:st=0:d={duration}"]

def effect_fade_out(clip, idx):
    """Fade to black."""
    duration_clip = clip.get('duration', 4)
    duration_fade = clip.get('fade_duration', 1.0)
    start = duration_clip - duration_fade
    return clip['file'], [f"fade=t=out:st={start}:d={duration_fade}"]


EFFECTS_MAP = {
    'none':       effect_none,
    'ken_burns':  effect_ken_burns,
    'focus_in':   effect_focus_in,
    'vignette':   effect_vignette,
    'film_grain': effect_film_grain,
    'fade_in':    effect_fade_in,
    'fade_out':   effect_fade_out,
}

TRANSITIONS = ['fade', 'crossfade', 'none']

# ─────────────────────────────────────────────
# PNG OVERLAY GENERATION (AF grid + film strip)
# ─────────────────────────────────────────────

def generate_camera_grid_png(output_path, width=1920, height=1080):
    """Generate a transparent PNG with a camera viewfinder grid (AF reticle)."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("  [!] Pillow not installed. AF grid unavailable.")
        print("      Install with: pip3 install Pillow")
        return False

    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Reticle color: translucent green
    color = (0, 255, 100, 180)
    lw = 2

    # Central AF rectangle
    cx, cy = width // 2, height // 2
    bw, bh = 200, 140
    draw.rectangle([cx-bw, cy-bh, cx+bw, cy+bh], outline=color, width=lw)

    # Reticle corner brackets
    bracket_len = 30
    corners = [
        (cx-bw, cy-bh), (cx+bw, cy-bh),
        (cx-bw, cy+bh), (cx+bw, cy+bh)
    ]
    for (x, y) in corners:
        sx = 1 if x < cx else -1
        sy = 1 if y < cy else -1
        draw.line([(x, y), (x + sx*bracket_len, y)], fill=color, width=lw+1)
        draw.line([(x, y), (x, y + sy*bracket_len)], fill=color, width=lw+1)

    # Rule-of-thirds lines (thin, semi-transparent)
    grid_color = (255, 255, 255, 40)
    for i in [1, 2]:
        x = width * i // 3
        y = height * i // 3
        draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
        draw.line([(0, y), (width, y)], fill=grid_color, width=1)

    # Small central dot
    r = 8
    draw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=color, width=lw)

    img.save(output_path, 'PNG')
    return True


def generate_filmstrip_png(output_path, width=1920, height=1080):
    """Generate a PNG with 35mm film strip borders (perforations + black edges)."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("  [!] Pillow not installed. Film strip unavailable.")
        return False

    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    border_h = 80   # film strip border height, top and bottom
    hole_w = 28
    hole_h = 40
    hole_spacing = 60
    hole_color = (0, 0, 0, 255)
    border_color = (20, 20, 20, 230)

    # Top border
    draw.rectangle([0, 0, width, border_h], fill=border_color)
    # Bottom border
    draw.rectangle([0, height - border_h, width, height], fill=border_color)

    # Top perforations
    x = 20
    while x < width - hole_w:
        cy_top = border_h // 2
        draw.rectangle(
            [x, cy_top - hole_h//2, x + hole_w, cy_top + hole_h//2],
            fill=hole_color, outline=(80, 80, 80, 255), width=1
        )
        x += hole_spacing

    # Bottom perforations
    x = 20
    while x < width - hole_w:
        cy_bot = height - border_h // 2
        draw.rectangle(
            [x, cy_bot - hole_h//2, x + hole_w, cy_bot + hole_h//2],
            fill=hole_color, outline=(80, 80, 80, 255), width=1
        )
        x += hole_spacing

    img.save(output_path, 'PNG')
    return True


def generate_camera_viewfinder_png(output_path, width=1920, height=1080):
    """Generate a programmatic camera viewfinder UI (alpha PNG) — no external asset."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("  [!] Pillow not installed. Camera viewfinder unavailable.")
        return False

    WHITE = (255, 255, 255, 255)
    BLACK = (0, 0, 0, 255)
    RED = (220, 30, 30, 255)
    GREEN = (80, 220, 80, 255)

    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s = height / 1080.0  # scale factor; all dimensions designed at 1080p

    def _font(size_px):
        for fp in ('/System/Library/Fonts/Helvetica.ttc',
                   '/System/Library/Fonts/Supplemental/Arial.ttf',
                   '/Library/Fonts/Arial.ttf',
                   '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'):
            try:
                return ImageFont.truetype(fp, int(size_px * s))
            except Exception:
                continue
        try:
            return ImageFont.load_default(size=int(size_px * s))
        except TypeError:
            return ImageFont.load_default()

    f_big = _font(50)
    f_mid = _font(34)

    # Top + bottom black bars
    bar_h = int(80 * s)
    draw.rectangle([0, 0, width, bar_h], fill=BLACK)
    draw.rectangle([0, height - bar_h, width, height], fill=BLACK)

    # 4 outer L-corners
    leg = int(80 * s)
    stroke = max(2, int(6 * s))
    margin = int(40 * s)
    for (cx, cy, hdir, vdir) in (
        (margin, bar_h + margin, 1, 1),
        (width - margin, bar_h + margin, -1, 1),
        (margin, height - bar_h - margin, 1, -1),
        (width - margin, height - bar_h - margin, -1, -1),
    ):
        draw.line([(cx, cy), (cx + hdir * leg, cy)], fill=WHITE, width=stroke)
        draw.line([(cx, cy), (cx, cy + vdir * leg)], fill=WHITE, width=stroke)

    # Top-left: REC dot + "REC" text
    rec_cy = bar_h // 2
    rec_r = int(18 * s)
    rec_cx = int(110 * s)
    draw.ellipse([rec_cx - rec_r, rec_cy - rec_r,
                  rec_cx + rec_r, rec_cy + rec_r], fill=RED)
    draw.text((rec_cx + rec_r + int(15 * s), rec_cy - int(28 * s)),
              "REC", fill=WHITE, font=f_big)

    # Top-right cluster: HD | 4K | 25FPS | battery | 99%
    box_w = int(70 * s)
    box_h = int(48 * s)
    box_stroke = max(2, int(3 * s))

    # Battery icon (rightmost-but-one, then "99%" to right edge)
    bat_w = int(80 * s)
    bat_h = int(40 * s)
    pct_w = int(80 * s)  # approx space for "99%"
    bat_x = width - margin - pct_w - bat_w - int(12 * s)
    bat_y = rec_cy - bat_h // 2
    draw.rectangle([bat_x, bat_y, bat_x + bat_w, bat_y + bat_h],
                   outline=WHITE, width=box_stroke)
    nub_w = int(8 * s)
    nub_h = bat_h // 2
    draw.rectangle([bat_x + bat_w, bat_y + (bat_h - nub_h) // 2,
                    bat_x + bat_w + nub_w, bat_y + (bat_h + nub_h) // 2],
                   fill=WHITE)
    # 4 green fill bars
    pad = int(5 * s)
    n_bars = 4
    inner_w = bat_w - 2 * pad
    gap_b = max(1, pad // 2)
    bw_each = (inner_w - (n_bars - 1) * gap_b) // n_bars
    for i in range(n_bars):
        bx = bat_x + pad + i * (bw_each + gap_b)
        draw.rectangle([bx, bat_y + pad, bx + bw_each, bat_y + bat_h - pad],
                       fill=GREEN)
    draw.text((bat_x + bat_w + nub_w + int(12 * s), rec_cy - int(20 * s)),
              "99%", fill=WHITE, font=f_mid)

    # "25FPS" text
    fps_x = bat_x - int(150 * s)
    draw.text((fps_x, rec_cy - int(20 * s)), "25FPS", fill=WHITE, font=f_mid)

    # "4K" box (outline)
    k4_x = fps_x - int(20 * s) - box_w
    k4_y = rec_cy - box_h // 2
    draw.rectangle([k4_x, k4_y, k4_x + box_w, k4_y + box_h],
                   outline=WHITE, width=box_stroke)
    draw.text((k4_x + int(13 * s), k4_y + int(7 * s)),
              "4K", fill=WHITE, font=f_mid)

    # "HD" box (white-filled, black text)
    hd_x = k4_x - int(15 * s) - box_w
    hd_y = k4_y
    draw.rectangle([hd_x, hd_y, hd_x + box_w, hd_y + box_h], fill=WHITE)
    draw.text((hd_x + int(13 * s), hd_y + int(7 * s)),
              "HD", fill=BLACK, font=f_mid)

    # Center "+"
    ccx, ccy = width // 2, height // 2
    arm = int(40 * s)
    cstroke = max(2, int(4 * s))
    draw.line([(ccx - arm, ccy), (ccx + arm, ccy)], fill=WHITE, width=cstroke)
    draw.line([(ccx, ccy - arm), (ccx, ccy + arm)], fill=WHITE, width=cstroke)

    # 4 inner brackets around center
    bw_in = int(280 * s)
    bh_in = int(180 * s)
    leg_in = int(60 * s)
    stroke_in = max(2, int(5 * s))
    for (px, py, hdir, vdir) in (
        (ccx - bw_in, ccy - bh_in, 1, 1),
        (ccx + bw_in, ccy - bh_in, -1, 1),
        (ccx - bw_in, ccy + bh_in, 1, -1),
        (ccx + bw_in, ccy + bh_in, -1, -1),
    ):
        draw.line([(px, py), (px + hdir * leg_in, py)], fill=WHITE, width=stroke_in)
        draw.line([(px, py), (px, py + vdir * leg_in)], fill=WHITE, width=stroke_in)

    # Bottom-right: "MENU" + hamburger
    menu_text_y = height - bar_h // 2 - int(20 * s)
    menu_x = width - int(220 * s)
    draw.text((menu_x, menu_text_y), "MENU", fill=WHITE, font=f_mid)
    ham_x = menu_x + int(135 * s)
    ham_y = menu_text_y + int(10 * s)
    ham_w = int(40 * s)
    ham_gap = int(8 * s)
    ham_h = max(2, int(4 * s))
    for i in range(3):
        y = ham_y + i * (ham_h + ham_gap)
        draw.rectangle([ham_x, y, ham_x + ham_w, y + ham_h], fill=WHITE)

    # Bottom-center: static timecode
    tc_text = "00:00:00:00"
    try:
        bbox = draw.textbbox((0, 0), tc_text, font=f_mid)
        tc_w = bbox[2] - bbox[0]
    except AttributeError:
        tc_w = int(220 * s)
    tc_x = (width - tc_w) // 2
    tc_y = height - bar_h // 2 - int(20 * s)
    draw.text((tc_x, tc_y), tc_text, fill=WHITE, font=f_mid)

    img.save(output_path, 'PNG')
    return True


# ─────────────────────────────────────────────
# FFMPEG PIPELINE CONSTRUCTION
# ─────────────────────────────────────────────

def build_clip_segment(clip, idx, tmp_dir, overlays):
    """
    Build a video segment for a single clip/photo.
    Returns the temp .mp4 path.
    """
    src = clip.get('file', '')
    duration = clip.get('duration', 4)
    effect = clip.get('effect', 'none')
    fps = RENDER['fps']
    w, h = RENDER['w'], RENDER['h']
    out_path = os.path.join(tmp_dir, f"seg_{idx:03d}.mp4")
    vmode = get_vertical_mode(clip)

    # Normalize EXIF orientation for image inputs (ffmpeg image2 demuxer ignores EXIF rotate).
    if src:
        src = normalize_orientation(src, tmp_dir)
    if 'files' in clip:
        clip = dict(clip, files=[normalize_orientation(f, tmp_dir) for f in clip['files']])

    # ── Diptych / triptych: multiple vertical images side-by-side ──
    if vmode == 'diptych':
        files = clip['files']
        n = len(files)
        each_w = (w // n) - ((w // n) % 2)  # even width for libx264
        base_input = []
        chain_parts = []
        labels = ''
        for i, f in enumerate(files):
            base_input += ['-loop', '1', '-t', str(duration), '-i', f]
            chain_parts.append(
                f"[{i}:v]scale={each_w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={each_w}:{h},setsar=1,fps={fps}[v{i}]"
            )
            labels += f"[v{i}]"
        # Pad final stack to full w (in case n*each_w < w)
        graph = ';'.join(chain_parts) + (
            f";{labels}hstack=inputs={n},"
            f"pad={w}:{h}:(ow-iw)/2:0:color=black[out]"
        )
        cmd = (
            base_input +
            ['-filter_complex', graph,
             '-map', '[out]',
             '-t', str(duration),
             '-c:v', 'libx264', '-preset', RENDER['preset'], '-crf', str(RENDER['crf']),
             '-an', out_path]
        )
        full_cmd = ['ffmpeg', '-y'] + cmd
        print(f"  → Segment {idx+1}: diptych×{n} {[os.path.basename(f) for f in files]}")
        result = subprocess.run(full_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  [ERROR] FFmpeg:\n{result.stderr[-800:]}")
            sys.exit(1)
        return out_path

    # Base filter: scale + fit the source file
    is_image = src.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))

    if is_image:
        base_input = ['-loop', '1', '-t', str(duration), '-i', src]
    else:
        # Video: optional 'start' field seeks into source before reading `duration` seconds.
        start = clip.get('start', 0)
        base_input = []
        if start:
            base_input += ['-ss', str(start)]
        base_input += ['-i', src, '-t', str(duration)]

    # ── Vertical pan: scale to output width, crop sliding y-window (linear chain) ──
    if vmode == 'pan':
        pan_dir = clip.get('pan_direction', 'down')
        if pan_dir == 'up':
            y_expr = f"(ih-{h})*(1-t/{duration})"
        else:
            y_expr = f"(ih-{h})*t/{duration}"
        base_vf = (f"scale={w}:-2,setsar=1,fps={fps},"
                   f"crop={w}:{h}:0:{y_expr}")
    else:
        base_vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                   f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}")

    # ── Vertical blur_fill: dedicated filter_complex (split/blur/overlay) ──
    if vmode == 'blur_fill':
        prep_graph = (
            f"[0:v]fps={fps},split[bg][fg];"
            f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},boxblur=luma_radius=40:luma_power=2,"
            f"eq=brightness=-0.15,setsar=1[bgblur];"
            f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease,setsar=1[fgs];"
            f"[bgblur][fgs]overlay=(W-w)/2:(H-h)/2[main]"
        )

        effect_fn = EFFECTS_MAP.get(effect, effect_none)
        _, extra_filters = effect_fn(clip, idx)
        if extra_filters:
            prep_graph += f";[main]{','.join(extra_filters)}[fx]"
            cur = '[fx]'
        else:
            cur = '[main]'

        overlay_type = clip.get('overlay')
        overlay_input = []
        final_label = cur
        if overlay_type == 'camera_grid' and 'camera_grid' in overlays:
            overlay_input = ['-i', overlays['camera_grid']]
            prep_graph += f";{cur}[1:v]overlay=0:0[out]"
            final_label = '[out]'
        elif overlay_type == 'film_strip' and 'filmstrip' in overlays:
            overlay_input = ['-i', overlays['filmstrip']]
            prep_graph += f";{cur}[1:v]overlay=0:0[out]"
            final_label = '[out]'
        elif overlay_type == 'camera_viewfinder' and 'camera_viewfinder' in overlays:
            overlay_input = ['-i', overlays['camera_viewfinder']]
            prep_graph += (
                f";[1:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=#00000000,"
                f"format=rgba,setsar=1[vf];"
                f"{cur}[vf]overlay=0:0[out]"
            )
            final_label = '[out]'

        cmd = (
            base_input + overlay_input +
            ['-filter_complex', prep_graph,
             '-map', final_label,
             '-t', str(duration),
             '-c:v', 'libx264', '-preset', RENDER['preset'], '-crf', str(RENDER['crf']),
             '-an', out_path]
        )
        full_cmd = ['ffmpeg', '-y'] + cmd
        print(f"  → Segment {idx+1}: {os.path.basename(src)} [vert_blur_fill+{effect}]")
        result = subprocess.run(full_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  [ERROR] FFmpeg:\n{result.stderr[-800:]}")
            sys.exit(1)
        return out_path

    # Apply primary effect
    effect_fn = EFFECTS_MAP.get(effect, effect_none)
    _, extra_filters = effect_fn(clip, idx)

    # Build the filter chain
    all_filters = [base_vf] + extra_filters

    overlay_type = clip.get('overlay')

    # focus_in: needs filter_complex (split + static boxblur + blend ramp).
    # boxblur radius is init-time; can't be animated with n.
    if effect == 'focus_in':
        blur_dur = clip.get('fade_duration', duration * 0.4)
        blur_frames = max(1, int(blur_dur * fps))
        focus_graph = (
            f"[0:v]{base_vf},split[fa][fb];"
            f"[fa]boxblur=luma_radius=20:luma_power=1[blur];"
            f"[blur][fb]blend=all_expr='if(lt(N,{blur_frames}),"
            f"lerp(A,B,N/{blur_frames}),B)'[fx]"
        )

        overlay_input = []
        out_label = '[fx]'
        if overlay_type == 'camera_grid' and 'camera_grid' in overlays:
            overlay_input = ['-i', overlays['camera_grid']]
            focus_graph += ";[fx][1:v]overlay=0:0[out]"
            out_label = '[out]'
        elif overlay_type == 'film_strip' and 'filmstrip' in overlays:
            overlay_input = ['-i', overlays['filmstrip']]
            focus_graph += ";[fx][1:v]overlay=0:0[out]"
            out_label = '[out]'
        elif overlay_type == 'camera_viewfinder' and 'camera_viewfinder' in overlays:
            overlay_input = ['-i', overlays['camera_viewfinder']]
            focus_graph += (
                f";[1:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=#00000000,"
                f"format=rgba,setsar=1[vf];"
                f"[fx][vf]overlay=0:0[out]"
            )
            out_label = '[out]'

        cmd = (
            base_input + overlay_input +
            ['-filter_complex', focus_graph,
             '-map', out_label,
             '-t', str(duration),
             '-c:v', 'libx264', '-preset', RENDER['preset'], '-crf', str(RENDER['crf']),
             '-an', out_path]
        )
        full_cmd = ['ffmpeg', '-y'] + cmd
        print(f"  → Segment {idx+1}: {os.path.basename(src)} [{effect}]")
        result = subprocess.run(full_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  [ERROR] FFmpeg:\n{result.stderr[-800:]}")
            sys.exit(1)
        return out_path

    # Special overlays
    if overlay_type == 'camera_grid' and 'camera_grid' in overlays:
        # Grid is composited on top after the main filter chain
        grid_png = overlays['camera_grid']
        main_vf = ','.join(all_filters)
        # Blur animates first, then the grid is overlaid
        cmd = (
            base_input +
            ['-i', grid_png] +
            ['-filter_complex',
             f"[0:v]{main_vf}[base];[base][1:v]overlay=0:0[out]",
             '-map', '[out]',
             '-t', str(duration),
             '-c:v', 'libx264', '-preset', RENDER['preset'], '-crf', str(RENDER['crf']),
             '-an', out_path]
        )
    elif overlay_type == 'film_strip' and 'filmstrip' in overlays:
        strip_png = overlays['filmstrip']
        main_vf = ','.join(all_filters)
        cmd = (
            base_input +
            ['-i', strip_png] +
            ['-filter_complex',
             f"[0:v]{main_vf}[base];[base][1:v]overlay=0:0[out]",
             '-map', '[out]',
             '-t', str(duration),
             '-c:v', 'libx264', '-preset', RENDER['preset'], '-crf', str(RENDER['crf']),
             '-an', out_path]
        )
    elif overlay_type == 'camera_viewfinder' and 'camera_viewfinder' in overlays:
        vf_src = overlays['camera_viewfinder']
        main_vf = ','.join(all_filters)
        graph = (
            f"[0:v]{main_vf}[base];"
            f"[1:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=#00000000,"
            f"format=rgba,setsar=1[vf];"
            f"[base][vf]overlay=0:0[out]"
        )
        cmd = (
            base_input +
            ['-i', vf_src] +
            ['-filter_complex', graph,
             '-map', '[out]',
             '-t', str(duration),
             '-c:v', 'libx264', '-preset', RENDER['preset'], '-crf', str(RENDER['crf']),
             '-an', out_path]
        )
    else:
        main_vf = ','.join(all_filters)
        cmd = (
            base_input +
            ['-vf', main_vf,
             '-t', str(duration),
             '-c:v', 'libx264', '-preset', RENDER['preset'], '-crf', str(RENDER['crf']),
             '-an', out_path]
        )

    full_cmd = ['ffmpeg', '-y'] + cmd
    print(f"  → Segment {idx+1}: {os.path.basename(src)} [{effect}]")
    result = subprocess.run(full_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [ERROR] FFmpeg:\n{result.stderr[-800:]}")
        sys.exit(1)

    return out_path


def concatenate_segments(segments, output, audio_tracks=None, total_duration=None,
                          audio_fade_in=0.0, audio_fade_out=0.0):
    """Concatenate all segments and mix every audio track into a single timeline."""

    with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
        for seg in segments:
            f.write(f"file '{seg}'\n")
        concat_list = f.name

    tmp_video = output.replace('.mp4', '_noaudio.mp4')

    # Step 1: concat video
    cmd_concat = [
        'ffmpeg', '-y',
        '-f', 'concat', '-safe', '0',
        '-i', concat_list,
        '-c:v', 'libx264', '-preset', RENDER['preset'], '-crf', str(RENDER['crf']),
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
        tmp_video
    ]
    print("\n→ Concatenating segments...")
    result = subprocess.run(cmd_concat, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] Concat:\n{result.stderr[-800:]}")
        sys.exit(1)

    # Probe ACTUAL duration of concatenated video — ffmpeg encoding granularity
    # (keyframes, frame-rounding, video clips ending early) can make this differ
    # from sum(clip.duration). Use the probed value for audio pad/trim/fade math.
    probed = probe_video_duration(tmp_video)
    if probed is not None and probed > 0:
        if total_duration is not None and abs(probed - total_duration) > 0.05:
            print(f"  [i] Video actual duration {probed:.2f}s "
                  f"(vs. computed {total_duration:.2f}s) — using actual for audio sync")
        total_duration = probed

    # Step 2: filter resolved audio tracks (existing files only)
    tracks = []
    for a in (audio_tracks or []):
        path = a.get('file')
        if not path or not os.path.exists(path):
            print(f"  [!] Missing audio, skipping: {path}")
            continue
        tracks.append(a)

    if not tracks:
        shutil.copy(tmp_video, output)
        os.unlink(concat_list)
        if os.path.exists(tmp_video):
            os.unlink(tmp_video)
        return

    print(f"→ Audio mix: {len(tracks)} track(s)")

    audio_inputs = ['-i', tmp_video]
    filter_parts = []
    mix_labels = ''
    for i, a in enumerate(tracks):
        audio_inputs += ['-i', a['file']]
        label = a.get('name', f'a{i}')
        # sanitize label for filtergraph (alnum/underscore)
        label = ''.join(c if c.isalnum() or c == '_' else '_' for c in str(label))
        delay_ms = int(a.get('at', 0) * 1000)
        vol = a.get('volume', 1.0)
        trim = a.get('duration')

        chain = f"[{i + 1}:a]"
        if trim is not None:
            chain += f"atrim=0:{trim},asetpts=PTS-STARTPTS,"
        if delay_ms > 0:
            chain += f"adelay={delay_ms}|{delay_ms},"
        chain += f"volume={vol}[{label}]"
        filter_parts.append(chain)
        mix_labels += f"[{label}]"
        print(f"  • {a.get('name', os.path.basename(a['file']))} "
              f"@ {a.get('at', 0)}s vol={vol}"
              f"{f' trim={trim}s' if trim is not None else ''}")

    # Mix → pad with silence to total_duration → trim to total_duration → afade in/out
    mix_chain = f"{mix_labels}amix=inputs={len(tracks)}:duration=longest"
    if total_duration is not None:
        mix_chain += (f",apad=whole_dur={total_duration},"
                      f"atrim=0:{total_duration}")
    if audio_fade_in and audio_fade_in > 0:
        mix_chain += f",afade=t=in:st=0:d={audio_fade_in}"
    if audio_fade_out and audio_fade_out > 0 and total_duration:
        fade_start = max(0.0, total_duration - audio_fade_out)
        mix_chain += f",afade=t=out:st={fade_start}:d={audio_fade_out}"
    mix_chain += "[aout]"
    filter_parts.append(mix_chain)

    cmd_audio = (
        ['ffmpeg', '-y'] + audio_inputs +
        ['-filter_complex', ';'.join(filter_parts),
         '-map', '0:v', '-map', '[aout]',
         '-c:v', 'copy', '-c:a', 'aac', '-b:a', RENDER['audio_bitrate'],
         '-movflags', '+faststart',
         '-shortest', output]
    )
    result = subprocess.run(cmd_audio, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] Audio mix:\n{result.stderr[-800:]}")
        shutil.copy(tmp_video, output)

    os.unlink(concat_list)
    if os.path.exists(tmp_video):
        os.unlink(tmp_video)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def resolve_path(p, basedir):
    """Resolve p against basedir if relative; pass through if absolute or None."""
    if not p:
        return p
    path = Path(p)
    if path.is_absolute():
        return str(path)
    return str((basedir / path).resolve())


def probe_video_duration(path):
    """Return source duration in seconds via ffprobe, or None on failure."""
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', path],
            capture_output=True, text=True
        )
        return float(r.stdout.strip())
    except (ValueError, subprocess.SubprocessError):
        return None


IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.webp')

try:
    from PIL import Image as _PILImage, ImageOps as _PILImageOps
except ImportError:
    _PILImage = None
    _PILImageOps = None


def is_vertical_image(path):
    """Respects EXIF orientation — a 4000x1848 JPG tagged rotate-90 is vertical."""
    if not path or not path.lower().endswith(IMAGE_EXTS) or _PILImage is None:
        return False
    try:
        with _PILImage.open(path) as img:
            img = _PILImageOps.exif_transpose(img)
            return img.height > img.width
    except Exception:
        return False


def get_vertical_mode(clip):
    """Returns 'blur_fill' | 'pan' | 'diptych' | None"""
    if 'files' in clip:
        return 'diptych'
    if is_vertical_image(clip.get('file', '')):
        return clip.get('vertical_mode', 'blur_fill')
    return None


def normalize_orientation(path, tmp_dir):
    """If JPG/PNG has non-trivial EXIF orientation, write a rotated copy to tmp_dir.
    Returns the path to feed ffmpeg (original if no rotation needed).
    FFmpeg's image2 demuxer does NOT auto-apply JPG EXIF orientation."""
    if not path or not path.lower().endswith(IMAGE_EXTS) or _PILImage is None:
        return path
    try:
        with _PILImage.open(path) as img:
            exif = img.getexif()
            orientation = exif.get(0x0112, 1) if exif else 1
            if orientation == 1:
                return path
            corrected = _PILImageOps.exif_transpose(img)
            new_path = os.path.join(tmp_dir, f"oriented_{os.path.basename(path)}")
            # Re-save as JPEG to keep size small; PNG for .png inputs
            save_fmt = 'PNG' if path.lower().endswith('.png') else 'JPEG'
            if save_fmt == 'JPEG' and corrected.mode != 'RGB':
                corrected = corrected.convert('RGB')
            corrected.save(new_path, save_fmt, quality=92)
            return new_path
    except Exception as e:
        print(f"  [!] EXIF normalize failed for {path}: {e}")
        return path


def resolve_clip_duration(clip):
    """Compute effective duration. Explicit > video natural length > 4s default."""
    if 'duration' in clip:
        return float(clip['duration'])
    if 'files' in clip:
        return 4.0  # diptych / multi-image default
    src = clip.get('file', '')
    if src.lower().endswith(IMAGE_EXTS):
        return 4.0
    # video: probe source, subtract start
    src_dur = probe_video_duration(src)
    if src_dur is None:
        print(f"  [!] Could not probe {src}; defaulting to 4s")
        return 4.0
    start = float(clip.get('start', 0))
    return max(0.1, src_dur - start)


def main():
    ap = argparse.ArgumentParser(description="CarpCut renderer")
    ap.add_argument('yaml', help="path to film YAML")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('-p', '--preview', action='store_true',
                      help="fast iteration: 960x540 @ 10fps, preset=ultrafast, crf=28")
    mode.add_argument('-m', '--mobile', action='store_true',
                      help="small shareable file: 1280x720 @ 25fps, crf=24, 128k audio")
    args = ap.parse_args()

    suffix = None
    if args.preview:
        RENDER.update(PROFILES['preview'])
        suffix = 'preview'
        print("⚡ PREVIEW mode: 960x540 @ 10fps, ultrafast/crf28")
    elif args.mobile:
        RENDER.update(PROFILES['mobile'])
        suffix = 'mobile'
        print("📱 MOBILE mode: 1280x720 @ 25fps, medium/crf24")

    yaml_path = Path(args.yaml).resolve()
    with open(yaml_path, 'r') as f:
        config = yaml.safe_load(f)

    # basedir: explicit in YAML, else YAML file's directory.
    # Relative basedir resolved against YAML file's directory.
    raw_basedir = config.get('basedir')
    yaml_dir = yaml_path.parent
    if raw_basedir:
        basedir = Path(raw_basedir)
        if not basedir.is_absolute():
            basedir = (yaml_dir / basedir).resolve()
    else:
        basedir = yaml_dir
    print(f"   Basedir: {basedir}")

    output = config.get('output', 'output.mp4')
    if suffix:
        op = Path(output)
        output = str(op.with_name(f"{op.stem}_{suffix}{op.suffix}"))
    audio_tracks = config.get('audio', []) or []
    for a in audio_tracks:
        a['file'] = resolve_path(a.get('file'), basedir)
    clips = config.get('clips', []) or []
    for clip in clips:
        if 'files' in clip:
            clip['files'] = [resolve_path(f, basedir) for f in clip['files']]
        if 'file' in clip:
            clip['file'] = resolve_path(clip['file'], basedir)
        # Resolve duration once: explicit > video natural length > 4s default
        clip['duration'] = resolve_clip_duration(clip)

    total_duration = sum(c['duration'] for c in clips)

    def _resolve_audio_fade(key, edge_clip, edge_effect):
        """Explicit YAML value wins; else fall back to edge clip's matching video fade."""
        explicit = config.get(key)
        if explicit is not None:
            return float(explicit)
        if edge_clip and edge_clip.get('effect') == edge_effect:
            return float(edge_clip.get('fade_duration', 1.0))
        return 0.0

    first_clip = clips[0] if clips else None
    last_clip = clips[-1] if clips else None
    audio_fade_in = _resolve_audio_fade('audio_fade_in', first_clip, 'fade_in')
    audio_fade_out = _resolve_audio_fade('audio_fade_out', last_clip, 'fade_out')

    print(f"\n🎬 CarpCut Renderer")
    print(f"   Output: {output}")
    print(f"   Clips: {len(clips)}  ({total_duration:.1f}s total)")
    print(f"   Audio tracks: {len(audio_tracks)}")
    if audio_fade_in or audio_fade_out:
        print(f"   Audio fades: in={audio_fade_in}s out={audio_fade_out}s")
    print()

    # Generate overlay PNGs
    tmp_dir = tempfile.mkdtemp(prefix='mountainfilm_')
    overlays = {}

    print("→ Generating graphic overlays...")
    grid_path = os.path.join(tmp_dir, 'camera_grid.png')
    if generate_camera_grid_png(grid_path, width=RENDER['w'], height=RENDER['h']):
        overlays['camera_grid'] = grid_path
        print("  ✓ AF grid generated")

    strip_path = os.path.join(tmp_dir, 'filmstrip.png')
    if generate_filmstrip_png(strip_path, width=RENDER['w'], height=RENDER['h']):
        overlays['filmstrip'] = strip_path
        print("  ✓ 35mm film strip generated")

    vf_path = os.path.join(tmp_dir, 'camera_viewfinder.png')
    if generate_camera_viewfinder_png(vf_path, width=RENDER['w'], height=RENDER['h']):
        overlays['camera_viewfinder'] = vf_path
        print("  ✓ Camera viewfinder generated")

    print(f"\n→ Processing {len(clips)} clip(s)...\n")

    segments = []
    for idx, clip in enumerate(clips):
        seg = build_clip_segment(clip, idx, tmp_dir, overlays)
        segments.append(seg)

    concatenate_segments(
        segments, output,
        audio_tracks=audio_tracks,
        total_duration=total_duration,
        audio_fade_in=audio_fade_in,
        audio_fade_out=audio_fade_out,
    )

    # Cleanup
    shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"\n✅ Video done: {output}")


if __name__ == '__main__':
    main()
