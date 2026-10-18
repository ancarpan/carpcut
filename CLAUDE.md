# CarpCut — CLAUDE.md

Project documentation for Claude Code sessions.

## What this project is

Code-driven video editing pipeline: a YAML file describes the entire film (clips, effects, music, sound effects), a Python script generates FFmpeg commands and renders the final video. No GUI software needed. Generic-purpose — not tied to any specific subject matter.

## Tech stack

- **Python 3.9+** — core logic, YAML parsing, orchestration
- **uv** — Python package + venv manager (deps pinned in `pyproject.toml` / `uv.lock`)
- **FFmpeg** — video rendering, filters, concatenation, audio mixing
- **Pillow** — programmatic generation of PNG overlays (AF grid, 35mm film strip)
- **PyYAML** — config file parsing

## File structure

```
carpcut/
├── render.py        # main engine
├── film.yaml        # film config (edit this)
├── pyproject.toml   # project metadata + Python deps (managed by uv)
├── uv.lock          # pinned dep versions (commit to git)
└── .python-version  # Python version pin for uv
```

During rendering, temporary files are created in a `carpcut_XXXXX/` folder under the system temp dir — auto-deleted on completion.

## How render.py works

1. Reads `film.yaml`
2. Generates PNG overlays in memory (AF grid, 35mm film strip) via Pillow
3. For each clip in the YAML: builds the FFmpeg filter chain and renders a temp `.mp4` segment
4. Concatenates all segments with `ffmpeg concat demuxer`
5. Adds music and sound effects via `amix` filter
6. Cleans up temp files

## YAML structure

```yaml
output: output_name.mp4
basedir: ./media                # optional, root for media paths (see below)
audio_fade_in: 1.5              # optional override; else auto-matches first clip fade_in
audio_fade_out: 2.0             # optional override; else auto-matches last clip fade_out

audio:                          # optional, list — unified track list (music + SFX)
  - name: soundtrack            # optional label, used in logs / filtergraph
    file: track.mp3
    at: 0.0                     # seconds from video start, default 0
    volume: 0.85                # default 1.0
    duration: 120               # optional, trim source to first N seconds

clips:
  - file: photo_or_video.jpg    # jpg, png, mp4, mov
    duration: 4                 # seconds (output length)
    start: 10                   # videos only: seek into source before reading
    effect: focus_in            # see effects table
    overlay: camera_grid        # optional
    zoom: in                    # ken_burns only: in | out
    fade_duration: 1.0          # for fade_in / fade_out
    vertical_mode: blur_fill    # vertical images only: blur_fill (default) | pan
    pan_direction: down         # pan only: down (default) | up

  # Multi-image clip (diptych / triptych) — auto-detected from `files` list
  - files: [a.jpg, b.jpg, c.jpg]
    duration: 4
```

### Vertical (portrait) images

Auto-detected via Pillow + EXIF orientation. Three modes:

| Mode | Use when | Mechanism |
|---|---|---|
| `blur_fill` (default) | Static portrait, no black bars | Same image blurred + darkened behind centered original |
| `pan` | Single dramatic portrait, want to show full content | Scale to output width, crop sliding y-window over duration |
| diptych (auto via `files: [...]`) | Burst of portraits side-by-side | Each scaled+cropped to `w/N` width, hstack |

EXIF orientation is auto-applied at runtime (ffmpeg's image2 demuxer ignores it; we pre-rotate via Pillow into temp files).

### Audio model

Single `audio` list. No separate `music` / `sound_effects`. Background music = entry with `at: 0` and no trim. SFX = entry with non-zero `at` and short `duration`. All mixed via `amix=duration=longest`, then padded with silence + trimmed to total video duration (= sum of clip durations).

### Audio fades

Tail fade resolution order:

1. Explicit `audio_fade_out: <seconds>` at YAML root → used as-is
2. Else if last clip has `effect: fade_out` → uses its `fade_duration`
3. Else → 0 (audio cuts hard at end)

`audio_fade_in` follows the same rule against the first clip's `fade_in`. Applied to the mixed `[aout]` via FFmpeg `afade=t=in/out` filters.

### `basedir` path resolution

Applies to `audio[].file`, `clips[].file`. Not to `output`.

| `basedir` value | Resolved against |
|---|---|
| Absolute path (`/Users/foo/media`) | Used as-is |
| Relative path (`./media`) | YAML file's directory |
| Omitted | YAML file's directory |

Per-file paths: absolute → used as-is; relative → joined to `basedir`.

## Available effects

| Keyword | Description | Extra params |
|---|---|---|
| `none` | No effect | — |
| `ken_burns` | Slow zoom on photo | `zoom: in \| out` |
| `focus_in` | Blurred → sharp (first 40% of clip) | `fade_duration` |
| `vignette` | Dark edges | — |
| `film_grain` | Cinematic grain | — |
| `fade_in` | Fade from black | `fade_duration` (default 1.0s) |
| `fade_out` | Fade to black | `fade_duration` (default 1.0s) |

## Available overlays

| Keyword | Description | Source |
|---|---|---|
| `camera_grid` | AF grid + rule of thirds | `generate_camera_grid_png()` (runtime, alpha PNG) |
| `film_strip` | 35mm film borders with perforations | `generate_filmstrip_png()` (runtime, alpha PNG) |
| `camera_viewfinder` | Camera UI (REC dot, HD/4K/25FPS, battery, brackets, +, MENU, timecode) | `generate_camera_viewfinder_png()` (runtime, alpha PNG) |

All three overlays are alpha PNGs generated at runtime at the target resolution and composited via `overlay=0:0`. No external asset files required.

## Output params

Default (master) profile:

- Resolution: **1920×1080**
- FPS: **25**
- Video codec: **libx264**, CRF 18, preset fast
- Audio codec: **AAC 192k**

Profiles are defined in the `PROFILES` dict in `render.py` and applied over the `RENDER` dict by CLI flags (mutually exclusive); output filename gets a `_<profile>` suffix:

| Flag | Resolution / FPS | Encode |
|---|---|---|
| `--preview` | 960×540 @ 10 | ultrafast, CRF 28, AAC 128k |
| `--mobile` | 1280×720 @ 25 | medium, CRF 24, AAC 128k |

All outputs use `-movflags +faststart`. To add a profile: add an entry to `PROFILES`, a flag in `main()`, and a row here.

- Photos scaled with `force_original_aspect_ratio=decrease` + black padding

## How to add a new effect

1. Define a function `effect_name(clip, idx)` that returns `(file, [ffmpeg_filter_list])`
2. Add entry to the `EFFECTS_MAP` dict in `render.py`
3. Document here in the effects table

Convention: function receives the clip dict from YAML and the integer index. Always returns a tuple `(str, list)` — source file path and a list of FFmpeg filter strings to apply sequentially after the base scaling filter.

## Dependencies and install

```bash
brew install ffmpeg uv
uv sync                 # creates .venv, installs deps from uv.lock
```

Add / remove / upgrade deps:

```bash
uv add <package>        # add to pyproject + lock + install
uv remove <package>     # remove
uv lock --upgrade       # refresh lockfile
uv sync --frozen        # CI: fail if lockfile out of sync
```

Min tested versions: Python 3.9, FFmpeg 6.x, Pillow 9.x.

## Run

```bash
uv run python render.py film.yaml            # 1080p master
uv run python render.py film.yaml --mobile   # 720p, small file for sharing
uv run python render.py film.yaml --preview  # 540p @ 10fps, fast iteration
```

`uv run` auto-resolves the project venv — no manual `source .venv/bin/activate` needed.

Rendering is sequential for simplicity. For long films (>30 clips), consider parallelizing segments with `multiprocessing.Pool`.

## Known limitations

- `focus_in` on video (not photos) uses a `boxblur` filter with `n`-dependent expression: on very short clips (<2s) the effect may not complete
- The `film_strip` overlay is not animated (does not scroll): static overlaid border
- Sound effects mixed via `adelay` on stereo channel — verify source files are stereo or consistently mono
- FFmpeg on Apple Silicon (M1/M2/M3) can use hardware acceleration with `-c:v hevc_videotoolbox` instead of `libx264` for faster rendering — not enabled by default for compatibility
