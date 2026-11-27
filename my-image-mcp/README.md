# my-image-mcp

A headless [MCP](https://modelcontextprotocol.io) server that gives LLM agents
(Claude Code, Claude Desktop, OpenAI Codex / Agents SDK, Cursor, ...) the
OpenCV toolbox: load images, chain operations, measure, annotate, look at the
results, and export the whole thing as a reproducible pipeline.

```
load_image ─► convert_color ─► threshold ─► find_contours ─► draw_contours ─► preview / save_image
   img_1          img_2          img_3          cnt_1            img_4          (the model looks)
```

Images stay in server memory and are referenced by **handles** (`img_3`,
`cnt_1`). Every op returns *new* handles (inputs are never modified), plus
measurements as JSON that the model can reason about. `preview` returns an
actual image so a vision-capable model can inspect a result, which lets it mix
its own visual analysis with exact OpenCV operations.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) (or plain pip, Python 3.10+).

```bash
cd my-image-mcp
uv sync
uv run my-image-mcp tools                # list the tools
uv run pytest -q                         # run the tests
```

### Claude Code

```bash
claude mcp add my-image-mcp -- \
  uv --directory /abs/path/to/my-image-mcp run my-image-mcp --root /abs/path/to/your/images
```

Then ask, e.g.: *"Load photo.jpg, find the coins, count them, draw their
outlines and save annotated.png."*

### Claude Desktop / any client with an `mcpServers` JSON config

```json
{
  "mcpServers": {
    "my-image-mcp": {
      "command": "uv",
      "args": ["--directory", "/abs/path/to/my-image-mcp", "run", "my-image-mcp",
               "--root", "/abs/path/to/your/images"]
    }
  }
}
```

### OpenAI Codex CLI (`~/.codex/config.toml`)

```toml
[mcp_servers.my-image-mcp]
command = "uv"
args = ["--directory", "/abs/path/to/my-image-mcp", "run", "my-image-mcp", "--root", "/abs/path/to/images"]
```

### OpenAI Agents SDK (Python)

```python
from agents import Agent, Runner
from agents.mcp import MCPServerStdio

async with MCPServerStdio(
    params={
        "command": "uv",
        "args": ["--directory", "/abs/path/to/my-image-mcp", "run", "my-image-mcp", "--root", "/data"],
    }
) as images:
    agent = Agent(name="vision", instructions="Use the image tools.", mcp_servers=[images])
    print((await Runner.run(agent, "How many contours are in /data/shapes.png?")).final_output)
```

### HTTP (remote clients, e.g. OpenAI Responses API hosted MCP)

```bash
uv run my-image-mcp serve --transport streamable-http --host 127.0.0.1 --port 8000
# endpoint: http://127.0.0.1:8000/mcp
```

Hosted-MCP features of cloud APIs need a publicly reachable HTTPS URL
(tunnel/reverse proxy). There is no authentication yet, so only bind to
localhost unless you put it behind something that adds auth.

## Building locally (Linux)

This is a pure-Python package, so "building" means producing a wheel. You
don't need a compiler, system OpenCV, libvips or a display: OpenCV, numpy and
libvips (`pyvips[binary]`) come as prebuilt wheels from PyPI. ImageMagick is
the only optional system dependency (see
[Image formats and ImageMagick](#image-formats-and-imagemagick)).

1. **Install uv** (it also fetches the right Python for you, so no system
   Python setup is needed):

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh   # any distro
   # or: sudo pacman -S uv (Arch/Manjaro), brew install uv, pipx install uv
   ```

2. **Get the source and install the locked dependencies** into `.venv/`:

   ```bash
   git clone <repo-url> && cd desktop-apps-samples/my-image-mcp
   uv sync --locked          # exact versions from uv.lock, like CI
   ```

3. **Check it** (the same checks CI runs):

   ```bash
   uv run ruff check . && uv run ruff format --check .
   uv run pytest -q
   uv run my-image-mcp tools
   ```

4. **Build the wheel and sdist** into `dist/`:

   ```bash
   uv build
   # dist/my_image_mcp-<version>-py3-none-any.whl
   # dist/my_image_mcp-<version>.tar.gz
   ```

5. **Install it** (optional). To get a standalone `my-image-mcp` command on
   your `PATH` (`~/.local/bin`), isolated from the source tree:

   ```bash
   uv tool install dist/my_image_mcp-*.whl
   my-image-mcp --version
   ```

   Then point MCP clients at `my-image-mcp --root /path/to/images` directly
   instead of `uv --directory ... run my-image-mcp`. Use
   `uv tool install --force ...` after rebuilding, and `uv tool uninstall
   my-image-mcp` to remove it. Without uv: `pip install dist/*.whl` inside a
   virtualenv works too (Python 3.10+).

For development you don't need steps 4–5: `uv run my-image-mcp ...` always
runs the current source.

Optionally install ImageMagick 7 (`sudo pacman -S imagemagick` on
Arch/Manjaro) to enable the `magick` tools and their tests. Without it, those
tests are skipped.

## Tools

At most 10 curated tools per module, plus session/pipeline helpers and a
generic bridge to the rest of OpenCV. Run `my-image-mcp tools` for the full
list with descriptions, or open **[docs/tools.html](docs/tools.html)**: a
searchable catalog for end users, with a plain-language summary, tags
(effects, drawing, layout, metadata, ...), backend and parameters for every
tool.

| Module | Tools |
|---|---|
| io | `load_image`, `save_image`, `create_image` |
| color | `convert_color`, `split_channels`, `merge_channels`, `brightness_contrast`, `gamma_correction`, `equalize_histogram`, `histogram`, `in_range`, `invert`, `normalize` |
| filters | `blur`, `sharpen`, `denoise`, `canny`, `gradient`, `inpaint`, `convolve` |
| threshold | `threshold`, `adaptive_threshold` |
| morphology | `morphology`, `distance_transform`, `connected_components`, `skeletonize`, `fill_holes` |
| contours | `find_contours`, `contour_properties`, `filter_contours`, `approximate_contours`, `convex_hulls`, `contour_mask` |
| geometry | `resize`, `crop`, `rotate`, `flip`, `pad`, `warp_affine`, `warp_perspective`, `stitch_images`, `trim` |
| drawing | `draw_line`, `draw_rectangle`, `draw_circle`, `draw_ellipse`, `draw_polygon`, `draw_text`, `draw_contours`, `draw_boxes`, `draw_markers` |
| composite | `blend`, `bitwise`, `apply_mask`, `difference`, `overlay`, `compare_images` |
| features | `detect_corners`, `detect_keypoints`, `match_features`, `align_images`, `match_template` |
| detection | `hough_lines`, `hough_circles`, `detect_haar`, `detect_people`, `detect_codes`, `detect_markers`, `create_marker` |
| segmentation | `kmeans_colors`, `flood_fill`, `watershed`, `grabcut`, `superpixels`, `mean_shift` |
| media (libvips) | `render_text`, `blend_mode`, `smart_crop`, `image_metadata` |
| magick (ImageMagick, optional) | `color_grade`, `apply_lut`, `quantize`, `distort`, `liquid_rescale`, `artistic_effect`, `montage`, `frame`, `generate_image` |
| session | `list_images`, `image_info`, `preview`, `delete_images`, `opencv_info` |
| pipeline | `history`, `export_pipeline`, `run_pipeline` |
| generic | `cv_call`, `cv_search`, `cv_doc` |

**Conventions.** Pixel data is BGR (OpenCV's order). Color *parameters* are
RGB strings (`"#ff0000"`, `"red"`, `"255,0,0"`), which avoids the classic BGR
mix-up. Coordinates are pixels, origin top-left. Detections are returned as
JSON with boxes as `[x, y, width, height]`, the format `draw_boxes` and
`crop` take. Pass `annotate=true` to also get a marked-up copy of the image.

Drawing tools also return a new image, so several drawings have to be
chained (each call takes the previous output) or done in one `run_pipeline`.
If two drawing calls start from the same handle, for example because a model
ran them in parallel, each result has only its own shape. The second result
then carries a `warning` naming the handle the model probably meant.

### The generic bridge: `cv_call`

Anything without a curated tool (optical flow, ximgproc filters, photo
effects, calibration, ...) is reachable through `cv_call`. The model can
discover it with `cv_search("binarization")` and
`cv_doc("ximgproc.niBlackThreshold")`, then call it:

```json
{"function": "ximgproc.niBlackThreshold",
 "args": ["img_2", 255, "cv2.THRESH_BINARY", 31, -0.2],
 "kwargs": {"binarizationMethod": "cv2.ximgproc.BINARIZATION_SAUVOLA"}}
```

- Handle strings resolve to stored values. They are copied first, so
  in-place cv2 functions never mutate stored images.
- `"cv2.NAME"`, `"cv2.A | cv2.B"` and submodule constants such as
  `"cv2.ximgproc.BINARIZATION_SAUVOLA"` resolve to their values.
- `{"$array": [[...]], "dtype": "float32"}` becomes a numpy array, and
  `{"$tuple": [...]}` becomes a tuple.
- Return values that look like images become new handles. Small arrays and
  numbers come back inline, KeyPoints/DMatches as dicts, and OpenCV objects
  (e.g. `createCLAHE()`, `SIFT_create()`) become `obj_N` handles whose
  methods can be called: `{"function": "obj_3.apply", "args": ["img_2"]}`.
- File, GUI and video I/O functions are blocked. Use `load_image` and
  `save_image` instead.

### Pipelines

Every op is recorded. `export_pipeline(["img_7"])` returns only the steps
that led to `img_7`:

- **JSON**, which you can replay with `run_pipeline(steps=...)`,
  `run_pipeline(path="p.json")`, or from the shell with
  `my-image-mcp run p.json`.
- **Python** (`format="python"`), a script using `my_image_mcp.ops` and `cv2`
  directly.

`run_pipeline` also lets the model run several ops in one call. Steps can
name outputs (`"outputs": ["mask"]`) and reference `"$prev"`.

Values the model copied out of JSON results (e.g. bounding boxes passed to
`draw_boxes`) are recorded as literals, so the lineage follows handles, not
the model's reasoning.

### Using the ops as a library

```python
from my_image_mcp import ops

img = ops.load_image("in.png").image
edges = ops.canny(ops.blur(img, ksize=5).image, auto=True).image
```

## Image formats and ImageMagick

Three backends work together, and the model doesn't have to choose between
them:

| Backend | Comes from | Adds |
|---|---|---|
| OpenCV | wheel | png, jpg, tiff, webp, bmp, jp2, avif writing; all the vision ops |
| libvips | `pyvips[binary]` wheel (Linux, macOS, Windows) | AVIF, SVG, animated GIF/TIFF pages, ICC color management, real-font text, blend modes, smart crop, metadata |
| ImageMagick 7 | **optional system install** | HEIC (iPhone photos), JPEG XL, PDF (needs Ghostscript), PSD, camera RAW; the `magick` tools |

`load_image` tries OpenCV, then libvips, then ImageMagick. Whichever one
loads the file, EXIF orientation is applied and embedded color profiles
(Display P3, Adobe RGB, ...) are converted to sRGB. The result says which
`loader` was used. `save_image` picks the writer from the extension.

ImageMagick isn't bundled because there's no pip wheel for it and shipping a
build per OS is costly. Install it if you want the extra formats and the
`magick` tools:

| OS | Install |
|---|---|
| macOS | `brew install imagemagick` (add `ghostscript` for PDF) |
| Arch / Manjaro | `sudo pacman -S imagemagick` |
| Windows | `winget install ImageMagick.ImageMagick` |
| Debian / Ubuntu | apt ships ImageMagick 6, which isn't supported; use the official AppImage or build 7 |

The server looks for `magick` on `PATH`. Set `MY_IMAGE_MCP_MAGICK` to a path
to use a specific binary, or to `off` to disable it. `opencv_info` reports the
libvips and ImageMagick versions it found (`null` = not available). Without
ImageMagick, the `magick` tools are simply not listed.

**Security.** ImageMagick can read files and URLs by itself (`@file`, `url:`,
`msl:`, `text:`), which would bypass the path sandbox. So every call runs
with a bundled allowlist policy (`ops/magick_config/policy.xml`) that only
permits known image formats, blocks `@` indirection, network and script
formats, and caps memory and time. The tools only build arguments from
validated parameters; there is no raw "run magick" tool.

## Configuration

| CLI | Env var | Default |
|---|---|---|
| `--root DIR` (repeatable) | `MY_IMAGE_MCP_ROOTS` (path-separator list) | current directory |
| `--allow-any-path` | `MY_IMAGE_MCP_ALLOW_ANY_PATH=1` | off |
| `--max-mb N` | `MY_IMAGE_MCP_MAX_MB` | 2048 (LRU eviction beyond) |
| `--transport stdio\|streamable-http\|sse`, `--host`, `--port` | | stdio, 127.0.0.1, 8000 |
| | `MY_IMAGE_MCP_MAGICK` (path to `magick`, or `off`) | `magick` on `PATH` |

All file reads and writes (`load_image`, `save_image`, `export_pipeline`,
`run_pipeline(path=...)`) are confined to the roots. Relative paths resolve
against the first root.

## Updating OpenCV

OpenCV comes from the `opencv-contrib-python-headless` wheel. It includes the
contrib modules (ximgproc, xfeatures2d, face, ...) and has no GUI
dependencies. The allowed range is set in `pyproject.toml` (`>=4.10,<5`), and
the exact version is pinned in `uv.lock`.

```bash
uv lock --upgrade-package opencv-contrib-python-headless   # newest within range
uv sync && uv run pytest -q
```

- The CI job `latest-deps` does the same on every push as an early warning.
- OpenCV 5 will need a deliberate bump of the `<5` cap.
- Don't install `opencv-python` alongside: all OpenCV wheels provide the same
  `cv2` module and overwrite each other.
- For CUDA you would need a custom OpenCV build; that is out of scope for now.

## CI and releases

`.github/workflows/my-image-mcp-build.yml` (at the repo root) runs on pushes
and PRs touching `my-image-mcp/**`:

1. Lint and tests on Linux, Windows and macOS × Python 3.10 and 3.13.
2. The `latest-deps` canary.
3. Wheel and sdist build, then a smoke test that installs the wheel on all
   three OSes.

Releases follow the repo convention: `scripts/bump-app-version.py
my-image-mcp 0.2.0` (updates `VERSION` and `pyproject.toml`), commit,
`git tag my-image-mcp-v0.2.0`, push the tag. A GitHub Release with the wheel
and sdist is then published.

## Layout

```
src/my_image_mcp/
  cli.py            entry point: serve | run | tools
  server.py         MCP layer: op -> tool wrappers, preview, pipeline, cv_* tools
  config.py         settings + path sandbox
  core/
    types.py        Result, Contours, handle/path annotations
    registry.py     @op decorator; type hints drive the MCP schema
    engine.py       executes ops, stores outputs, records history, exports
    store.py        handle store with LRU eviction
    cvbridge.py     generic cv2 access: arg decoding, result encoding, denylist
  ops/              one module per category; plain functions, no MCP code
    _vips.py        libvips bridge (numpy <-> pyvips, loader with ICC handling)
    _magick.py      ImageMagick bridge (subprocess + temp PNGs, policy, detection)
    magick_config/  policy.xml applied to every ImageMagick call
tests/
```

Ops know nothing about MCP, so a future visual UI or web server can reuse
`core/` and `ops/` directly.
