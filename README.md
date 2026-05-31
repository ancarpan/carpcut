# CarpCut

Code-driven video editing pipeline. YAML in, MP4 out. FFmpeg under the hood.

## Setup

Requires [Homebrew](https://brew.sh) and [uv](https://docs.astral.sh/uv/).

```bash
brew install ffmpeg uv
uv sync
```

## Run

```bash
uv run python render.py film.yaml
uv run python render.py film.yaml --preview   # 960x540 @ 10fps, fast iteration
```

## Try the demo

```bash
uv run python make_demo_assets.py     # one-time, generates ./demo_assets/
uv run python render.py example.yaml --preview
```

Produces `demo_preview.mp4` — six placeholder clips covering `focus_in`,
`ken_burns`, `vignette`, `film_grain`, `fade_out`, plus the `camera_viewfinder`
overlay. No audio. ~15s.

`example_full.yaml` is a richer reference covering audio tracks, vertical
image modes, shutter-synced flicker sequences, and video clips with start
offsets — it references private media and is not directly runnable.

## YAML reference

### Top-level

| key | type | default | notes |
|---|---|---|---|
| `output` | string | `output.mp4` | output filename |
| `basedir` | path | YAML dir | root for relative media paths |
| `audio` | list | `[]` | unified audio tracks (music + SFX) |
| `audio_fade_in` | seconds | auto* | fade-in on the mixed audio (`afade=t=in`) |
| `audio_fade_out` | seconds | auto* | fade-out on the mixed audio (`afade=t=out`) |

*Auto-match rule: if not set explicitly, falls back to the first/last clip's `fade_duration` when that clip has `effect: fade_in` / `effect: fade_out`. Set to `0` to disable.
| `clips` | list | `[]` | ordered segments |

### `audio[]` entry

| key | type | default | notes |
|---|---|---|---|
| `file` | path | — | required |
| `name` | string | `a{idx}` | label for logs / filtergraph |
| `at` | seconds | `0` | start offset in timeline |
| `duration` | seconds | full | trim source from start |
| `volume` | float | `1.0` | gain |

### `clips[]` — single-source entry

| key | type | default | notes |
|---|---|---|---|
| `file` | path | — | required (or use `files`) |
| `duration` | seconds | image=4, video=natural | output length |
| `start` | seconds | `0` | videos only: seek into source |
| `effect` | enum | `none` | see Effects |
| `overlay` | enum | — | see Overlays |
| `fade_duration` | seconds | `1.0` | for `fade_in` / `fade_out` / `focus_in` |
| `zoom` | enum | `in` | `ken_burns` only: `in` \| `out` |
| `vertical_mode` | enum | `blur_fill` | vertical images: `blur_fill` \| `pan` |
| `pan_direction` | enum | `down` | `pan` only: `down` \| `up` |

### `clips[]` — multi-source entry (diptych / triptych)

| key | type | notes |
|---|---|---|
| `files` | list[path] | 2–N portrait images, side-by-side via hstack |
| `duration` | seconds | output length |

### Effects (`effect:`)

| value | applies to | extra params |
|---|---|---|
| `none` | any | — |
| `ken_burns` | image | `zoom: in\|out` |
| `focus_in` | image | `fade_duration` (default `duration * 0.4`) |
| `vignette` | any | — |
| `film_grain` | any | — |
| `fade_in` | any | `fade_duration` |
| `fade_out` | any | `fade_duration` |

### Overlays (`overlay:`)

| value | source | blend |
|---|---|---|
| `camera_grid` | generated PNG (AF grid + rule of thirds) | alpha overlay |
| `film_strip` | generated PNG (35mm perforated borders) | alpha overlay |
| `camera_viewfinder` | generated PNG (REC, HD/4K/25FPS, battery, brackets, +, MENU, timecode) | alpha overlay |

### Vertical (portrait) image modes

Auto-detected via Pillow + EXIF orientation.

| mode | trigger | mechanism |
|---|---|---|
| `blur_fill` | default for vertical `file:` | blurred cover behind centered portrait |
| `pan` | `vertical_mode: pan` | scale to output width, crop sliding y-window |
| diptych | `files: [...]` (any orientation) | hstack of N scaled+cropped images |

## Path resolution

`audio[].file`, `clips[].file`, `clips[].files[]` are resolved against `basedir`:

- absolute path → used as-is
- relative path → joined to `basedir`
- `basedir` omitted → defaults to the YAML file's directory
