"""Generate docs/tools.html: a human-readable, filterable catalog of every MCP tool.

Tool names, parameters and the detailed descriptions come from the live server
(the same data an MCP client sees), so they can't drift from the code. The only
hand-maintained part is CATALOG below: a plain-language summary and tags per
tool. tests/test_tools_doc.py fails if a tool is missing here.

    uv run python scripts/gen_tools_doc.py          # needs ImageMagick 7 installed
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

import anyio

# Tag id -> (label, explanation shown as tooltip). Order = order of the filter chips.
TAGS: dict[str, tuple[str, str]] = {
    "io": ("Input / output", "Load, save and create images"),
    "color": ("Color & tone", "Color spaces, channels, brightness, contrast, grading"),
    "enhance": ("Enhance & repair", "Sharpen, denoise, fix exposure, remove defects"),
    "filter": ("Filters", "Blur, convolution and other pixel filters"),
    "edges": ("Edges & lines", "Edge maps, gradients, straight lines and circles"),
    "segmentation": ("Segmentation & masks", "Split an image into regions, select objects, build masks"),
    "morphology": ("Morphology", "Grow, shrink, clean and analyse binary shapes"),
    "shapes": ("Shapes & contours", "Outlines of objects and their geometry"),
    "measure": ("Measurement", "Numbers about the image: areas, counts, histograms, scores"),
    "geometry": ("Geometry & transform", "Resize, rotate, warp, distort, align"),
    "layout": ("Layout & composition", "Crop, pad, frame, arrange several images, panoramas"),
    "compositing": ("Compositing", "Combine images: blend modes, overlays, masks"),
    "drawing": ("Drawing & annotation", "Draw shapes, boxes and markers on top of images"),
    "text": ("Text", "Put text on images"),
    "effects": ("Artistic effects", "Stylise: paint, sketch, posterize, vintage looks"),
    "detection": ("Detection", "Find faces, people, codes, markers and other objects"),
    "features": ("Features & matching", "Keypoints, matching, template search, registration"),
    "compare": ("Comparison", "How similar are two images, and where do they differ"),
    "metadata": ("Metadata & info", "File metadata, image properties, server information"),
    "generate": ("Generate", "Create images from scratch: gradients, patterns, codes"),
    "session": ("Session", "Manage the images held in memory and look at them"),
    "pipeline": ("Pipelines", "Record, export and replay a sequence of operations"),
    "advanced": ("Advanced (OpenCV)", "Direct access to the rest of the OpenCV library"),
}

# tool -> (tags, plain-language summary for humans)
CATALOG: dict[str, tuple[list[str], str]] = {
    # io
    "load_image": (
        ["io"],
        "Open an image file: JPEG, PNG, TIFF, WebP, AVIF, SVG, GIF and, with ImageMagick, HEIC (iPhone photos), JPEG XL, PDF pages, PSD and camera RAW. Photos are auto-rotated and their colors converted to standard sRGB.",
    ),
    "save_image": (
        ["io"],
        "Write an image to disk. The format follows the file extension (PNG, JPEG, WebP, TIFF, AVIF, GIF, and HEIC/JPEG XL/PDF/PSD with ImageMagick).",
    ),
    "create_image": (
        ["io", "generate"],
        "Make a blank canvas of a given size and color, e.g. as a background or a mask to draw on.",
    ),
    # color
    "convert_color": (
        ["color"],
        "Change color space: to grayscale, HSV, Lab, YCrCb and more. Many detection steps work best on grayscale or HSV.",
    ),
    "split_channels": (
        ["color"],
        "Separate an image into its individual channels (e.g. blue, green, red) as grayscale images.",
    ),
    "merge_channels": (["color"], "Combine single-channel images back into one color image."),
    "brightness_contrast": (["color", "enhance"], "Make an image brighter or darker and increase or reduce contrast."),
    "gamma_correction": (
        ["color", "enhance"],
        "Brighten or darken the midtones without clipping highlights and shadows.",
    ),
    "equalize_histogram": (
        ["color", "enhance"],
        "Automatically improve contrast, globally or locally (CLAHE), to reveal detail in dark or flat images.",
    ),
    "histogram": (
        ["color", "measure"],
        "Count how many pixels have each brightness/color value, plus mean, min and max per channel.",
    ),
    "in_range": (
        ["color", "segmentation"],
        "Select all pixels whose color falls in a range (e.g. 'all green things') and get a mask of them.",
    ),
    "invert": (["color"], "Make a negative: dark becomes light and colors flip to their opposites."),
    "normalize": (
        ["color", "enhance"],
        "Stretch pixel values to use the full range, or convert between 8-bit, 16-bit and floating point.",
    ),
    # filters
    "blur": (
        ["filter"],
        "Smooth an image: Gaussian, median (removes speckles), box, or bilateral (smooths while keeping edges sharp).",
    ),
    "sharpen": (["filter", "enhance"], "Make details crisper with an unsharp mask."),
    "denoise": (["filter", "enhance"], "Remove grain and noise from photos while keeping detail."),
    "canny": (["edges"], "Find edges (outlines) as a black-and-white edge map; can pick thresholds automatically."),
    "gradient": (
        ["edges", "filter"],
        "Show where brightness changes (Sobel, Scharr, Laplacian): edge strength and direction.",
    ),
    "inpaint": (
        ["enhance", "segmentation"],
        "Repair parts of an image (scratches, dust, text, small objects) by filling them in from their surroundings.",
    ),
    "convolve": (["filter", "advanced"], "Apply your own filter kernel (a small matrix of weights) to the image."),
    # threshold
    "threshold": (
        ["segmentation"],
        "Turn an image into black and white at a brightness level, or pick the level automatically (Otsu).",
    ),
    "adaptive_threshold": (
        ["segmentation"],
        "Black-and-white conversion that adapts to uneven lighting, ideal for documents and text.",
    ),
    # morphology
    "morphology": (
        ["morphology", "segmentation"],
        "Grow, shrink, open or close shapes in a mask: remove specks, fill gaps, separate touching blobs.",
    ),
    "distance_transform": (
        ["morphology", "measure"],
        "For every foreground pixel, how far it is from the background; useful for thickness and finding centers.",
    ),
    "connected_components": (
        ["morphology", "segmentation", "measure"],
        "Label each separate blob in a mask and report its area, bounding box and center.",
    ),
    "skeletonize": (["morphology", "shapes"], "Thin shapes down to one-pixel-wide center lines (skeletons)."),
    "fill_holes": (["morphology", "segmentation"], "Fill the enclosed holes inside shapes in a mask."),
    # contours
    "find_contours": (
        ["shapes", "measure"],
        "Trace the outlines of objects in a mask and measure each: area, perimeter, bounding box, center, roundness.",
    ),
    "contour_properties": (
        ["shapes", "measure"],
        "Detailed geometry of each outline: rotated box, enclosing circle, fitted ellipse, solidity, aspect ratio.",
    ),
    "filter_contours": (
        ["shapes"],
        "Keep only the outlines that match criteria such as size, roundness or aspect ratio.",
    ),
    "approximate_contours": (
        ["shapes", "measure"],
        "Simplify outlines to polygons and name the shape: triangle, rectangle, circle, ...",
    ),
    "convex_hulls": (["shapes"], "Wrap each outline in its tightest convex 'rubber band' shape."),
    "contour_mask": (["shapes", "segmentation"], "Turn outlines into a filled (or outlined) mask image."),
    # geometry
    "resize": (["geometry"], "Scale an image up or down, by factor or to a target width/height, keeping proportions."),
    "crop": (["geometry", "layout"], "Cut out a rectangular region."),
    "rotate": (["geometry"], "Rotate by any angle, optionally enlarging the canvas so nothing is cut off."),
    "flip": (["geometry"], "Mirror horizontally, vertically or both."),
    "pad": (["geometry", "layout"], "Add margins around an image in a color or by repeating the edges."),
    "warp_affine": (["geometry"], "Apply a general affine transform (move, rotate, scale, shear) given as a matrix."),
    "warp_perspective": (
        ["geometry"],
        "Straighten a photographed document, screen or sign from its four corners (bird's-eye view).",
    ),
    "stitch_images": (
        ["geometry", "layout"],
        "Merge overlapping photos into one panorama, or overlapping scans into one page.",
    ),
    "trim": (["layout", "geometry"], "Cut away plain borders around the content (scans, screenshots, renders)."),
    # drawing
    "draw_line": (["drawing"], "Draw a straight line or arrow."),
    "draw_rectangle": (["drawing"], "Draw an outlined or filled rectangle."),
    "draw_circle": (["drawing"], "Draw an outlined or filled circle."),
    "draw_ellipse": (["drawing"], "Draw an ellipse or an arc."),
    "draw_polygon": (["drawing"], "Draw a polygon or open polyline through a list of points."),
    "draw_text": (
        ["drawing", "text"],
        "Write simple text with OpenCV's built-in fonts (basic Latin letters only). For nicer text use render_text.",
    ),
    "draw_contours": (["drawing", "shapes"], "Draw detected outlines on an image, optionally numbered."),
    "draw_boxes": (["drawing"], "Draw many labelled bounding boxes at once, e.g. to show detection results."),
    "draw_markers": (["drawing"], "Mark points with crosses, stars, dots, diamonds, etc."),
    # composite
    "blend": (["compositing"], "Mix two images with adjustable weights (cross-fade)."),
    "bitwise": (["compositing", "segmentation"], "Combine images or masks with AND / OR / XOR / NOT logic."),
    "apply_mask": (
        ["compositing", "segmentation"],
        "Keep only the masked area: replace the rest with a color or make it transparent (cut-outs).",
    ),
    "difference": (["compare", "compositing"], "Show where two images differ, with a mask of changed pixels."),
    "overlay": (["compositing", "layout"], "Paste one image on top of another at a position, respecting transparency."),
    "compare_images": (
        ["compare", "measure"],
        "Score how similar two images are (SSIM, PSNR, changed pixels, perceptual hash) and get a highlighted diff image.",
    ),
    # features
    "detect_corners": (["features"], "Find strong corner points, e.g. for tracking or measurement."),
    "detect_keypoints": (
        ["features"],
        "Find distinctive points (ORB, SIFT, AKAZE, BRISK) that can be recognised again in other images.",
    ),
    "match_features": (
        ["features", "compare"],
        "Match distinctive points between two images to tell whether they show the same thing and how it moved.",
    ),
    "align_images": (
        ["features", "geometry"],
        "Automatically line up one photo with another of the same scene or document.",
    ),
    "match_template": (
        ["features", "detection"],
        "Find every place where a small reference picture (logo, icon, part) appears in an image.",
    ),
    # detection
    "hough_lines": (["edges", "detection"], "Detect straight line segments with their length and angle."),
    "hough_circles": (
        ["edges", "detection"],
        "Detect circles (coins, wheels, holes, cells) with their centers and radii.",
    ),
    "detect_haar": (["detection"], "Classic detector for faces, eyes, smiles, bodies, cat faces and license plates."),
    "detect_people": (["detection"], "Detect standing or walking people (pedestrian detector)."),
    "detect_codes": (["detection"], "Find and read QR codes and barcodes (EAN, UPC, Code 128, ...)."),
    "detect_markers": (["detection", "measure"], "Find ArUco / AprilTag markers with their IDs, corners and rotation."),
    "create_marker": (["generate", "detection"], "Generate a QR code from text, or an ArUco/AprilTag marker."),
    # segmentation
    "kmeans_colors": (
        ["segmentation", "color", "measure"],
        "Find the dominant colors of an image (palette with percentages) and a posterized version.",
    ),
    "flood_fill": (
        ["segmentation", "drawing"],
        "Paint-bucket fill from a point, and get the mask of the filled region.",
    ),
    "watershed": (
        ["segmentation", "measure"],
        "Separate touching objects (coins, cells, grains) so each can be counted and measured.",
    ),
    "grabcut": (["segmentation"], "Cut out the main object inside a rectangle from its background."),
    "superpixels": (["segmentation"], "Split an image into small regions of similar color (superpixels)."),
    "mean_shift": (
        ["segmentation", "effects", "filter"],
        "Flatten textures into uniform color areas while keeping edges (cartoon / posterized look).",
    ),
    # media (libvips)
    "render_text": (
        ["text", "drawing"],
        "Add high-quality text with real fonts: any language, accents, emoji, bold/italic, word wrap, background box.",
    ),
    "blend_mode": (
        ["compositing", "effects"],
        "Combine two images with Photoshop-style blend modes: multiply, screen, overlay, soft light, difference, ...",
    ),
    "smart_crop": (
        ["layout", "geometry"],
        "Crop to a target size while keeping the most interesting part, e.g. for thumbnails.",
    ),
    "image_metadata": (
        ["metadata", "io"],
        "Read a file's information without opening it: size, pages, color profile, DPI, camera EXIF data, GPS.",
    ),
    # magick (ImageMagick)
    "color_grade": (
        ["color", "enhance", "effects"],
        "Photo color grading in one step: auto levels, levels, saturation, hue, S-curve contrast, sepia, tint.",
    ),
    "apply_lut": (
        ["color", "effects"],
        "Apply a color look-up table (film emulation, camera look, grading preset) from a .cube file or Hald image.",
    ),
    "quantize": (
        ["color", "effects"],
        "Reduce to a few colors with dithering (GIF / retro / print look), or map onto a given palette.",
    ),
    "distort": (
        ["geometry", "effects"],
        "Lens (barrel) correction, bend into an arc, polar unwrap, or smooth warps driven by control points.",
    ),
    "liquid_rescale": (
        ["geometry", "layout"],
        "Content-aware resize (seam carving): change proportions while people and objects keep their shape.",
    ),
    "artistic_effect": (
        ["effects"],
        "Artistic filters: charcoal, pencil sketch, oil paint, swirl, vignette, emboss, solarize, polaroid, ...",
    ),
    "montage": (["layout"], "Arrange several images in a labelled grid (contact sheet) to compare them side by side."),
    "frame": (["layout", "effects"], "Decorate with a border, 3-D frame, drop shadow or rounded corners."),
    "generate_image": (
        ["generate"],
        "Create gradients, plasma clouds, tiled patterns, or an identity color look-up table.",
    ),
    # session
    "list_images": (["session"], "List all images and contour sets currently held in memory."),
    "image_info": (
        ["session", "metadata", "measure"],
        "Size, type and value statistics of an image in memory, and which step produced it.",
    ),
    "preview": (["session"], "Show an image (or several side by side) to the assistant so it can look at the result."),
    "delete_images": (["session"], "Free memory by deleting images that are no longer needed."),
    "opencv_info": (
        ["metadata", "session"],
        "Versions of OpenCV, libvips and ImageMagick, available modules, allowed folders and limits.",
    ),
    # pipeline
    "history": (["pipeline"], "List the operations performed so far in this session."),
    "export_pipeline": (
        ["pipeline"],
        "Export the steps that produced an image as a replayable JSON pipeline or a Python script.",
    ),
    "run_pipeline": (["pipeline"], "Run a series of operations in one go, or replay a saved pipeline file."),
    # generic OpenCV bridge
    "cv_call": (
        ["advanced"],
        "Call any other OpenCV function directly (file, window and video functions are blocked).",
    ),
    "cv_search": (["advanced"], "Search the OpenCV library for functions and constants by name."),
    "cv_doc": (["advanced"], "Show the documentation of an OpenCV function."),
}

# Modules for server-level tools that have no "module: name" title.
SERVER_MODULE = {
    "list_images": "session",
    "image_info": "session",
    "preview": "session",
    "delete_images": "session",
    "opencv_info": "session",
    "history": "pipeline",
    "export_pipeline": "pipeline",
    "run_pipeline": "pipeline",
    "cv_call": "generic",
    "cv_search": "generic",
    "cv_doc": "generic",
}
BACKEND = {"media": "libvips", "magick": "ImageMagick"}
BACKEND_NOTE = {
    "OpenCV": "Always available.",
    "libvips": "Always available (bundled with the pyvips[binary] package).",
    "ImageMagick": "Requires ImageMagick 7 installed on the computer (e.g. brew install imagemagick).",
}

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "tools.html"


async def fetch_tools() -> list:
    from mcp import Client

    from my_image_mcp.server import build_server

    async with Client(build_server()) as client:
        return list((await client.list_tools()).tools)


def _type(schema: dict) -> str:
    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"])
    if "anyOf" in schema:
        parts = [_type(s) for s in schema["anyOf"] if s.get("type") != "null"]
        nullable = any(s.get("type") == "null" for s in schema["anyOf"])
        return " | ".join(parts) + (" (optional)" if nullable else "")
    t = schema.get("type", "any")
    if t == "array":
        return f"list of {_type(schema.get('items', {}))}"
    return {
        "integer": "integer",
        "number": "number",
        "string": "text",
        "boolean": "true/false",
        "object": "object",
    }.get(t, t)


def _params(schema: dict) -> list[dict]:
    required = set(schema.get("required", []))
    out = []
    for name, s in schema.get("properties", {}).items():
        default = "required" if name in required else json.dumps(s.get("default"))
        out.append({"name": name, "type": _type(s), "default": default, "description": s.get("description", "")})
    return out


def build_entries(tools: list) -> list[dict]:
    entries = []
    for t in tools:
        if t.name not in CATALOG:
            raise SystemExit(f"tool {t.name!r} has no entry in CATALOG; add tags and a summary")
        tags, summary = CATALOG[t.name]
        module = t.title.split(":")[0] if t.title and ":" in t.title else SERVER_MODULE.get(t.name, "other")
        backend = BACKEND.get(module, "OpenCV")
        entries.append(
            {
                "name": t.name,
                "module": module,
                "backend": backend,
                "tags": tags,
                "summary": summary,
                "description": t.description or "",
                "params": _params(t.input_schema),
            }
        )
    return entries


def _e(s: str) -> str:
    return html.escape(str(s), quote=True)


def render(entries: list[dict]) -> str:
    used = {tag for e in entries for tag in e["tags"]}
    chips = "\n".join(
        f'<button type="button" class="chip" data-tag="{t}" title="{_e(tip)}" aria-pressed="false">'
        f'{_e(label)} <span class="n">{sum(t in e["tags"] for e in entries)}</span></button>'
        for t, (label, tip) in TAGS.items()
        if t in used
    )
    backends = "\n".join(
        f'<label><input type="checkbox" class="be" value="{b}" checked> {b} '
        f'<span class="n">{sum(e["backend"] == b for e in entries)}</span></label>'
        for b in ("OpenCV", "libvips", "ImageMagick")
    )
    cards = []
    for e in entries:
        tag_html = " ".join(
            f'<button type="button" class="tag" data-tag="{t}">{_e(TAGS[t][0])}</button>' for t in e["tags"]
        )
        rows = "".join(
            f"<tr><td><code>{_e(p['name'])}</code></td><td>{_e(p['type'])}</td>"
            f"<td><code>{_e(p['default'])}</code></td><td>{_e(p['description'])}</td></tr>"
            for p in e["params"]
        )
        params = (
            f"<table><thead><tr><th>Parameter</th><th>Type</th><th>Default</th><th>Notes</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
            if e["params"]
            else "<p class='muted'>No parameters.</p>"
        )
        search = " ".join([e["name"], e["module"], e["summary"], e["description"], *e["tags"]]).lower()
        cards.append(
            f'<article class="tool" id="{e["name"]}" data-tags="{" ".join(e["tags"])}" '
            f'data-backend="{e["backend"]}" data-search="{_e(search)}">\n'
            f'<header><h3><a href="#{e["name"]}">{_e(e["name"])}</a></h3>'
            f'<span class="module">{_e(e["module"])}</span>'
            f'<span class="backend b-{e["backend"].lower()}" title="{_e(BACKEND_NOTE[e["backend"]])}">'
            f"{_e(e['backend'])}</span></header>\n"
            f'<p class="summary">{_e(e["summary"])}</p>\n<div class="tags">{tag_html}</div>\n'
            f"<details><summary>Details and parameters</summary>"
            f'<pre class="desc">{_e(e["description"])}</pre>{params}</details>\n</article>'
        )
    return (
        TEMPLATE.replace("{{COUNT}}", str(len(entries)))
        .replace("{{CHIPS}}", chips)
        .replace("{{BACKENDS}}", backends)
        .replace("{{CARDS}}", "\n".join(cards))
    )


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>my-image-mcp tools</title>
<!-- Generated by scripts/gen_tools_doc.py - do not edit by hand. -->
<style>
:root {
  --bg: #fafaf9; --card: #ffffff; --text: #1c1917; --muted: #6b6560; --line: #e7e5e4;
  --accent: #2563eb; --accent-soft: #dbeafe; --chip: #f1f0ee;
  --cv: #0f766e; --vips: #7c3aed; --im: #c2410c;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #161514; --card: #201f1d; --text: #ecebe9; --muted: #a29d97; --line: #34312e;
    --accent: #60a5fa; --accent-soft: #1e3a5f; --chip: #2a2826;
    --cv: #2dd4bf; --vips: #a78bfa; --im: #fb923c;
  }
}
:root[data-theme="dark"] {
  --bg: #161514; --card: #201f1d; --text: #ecebe9; --muted: #a29d97; --line: #34312e;
  --accent: #60a5fa; --accent-soft: #1e3a5f; --chip: #2a2826;
  --cv: #2dd4bf; --vips: #a78bfa; --im: #fb923c;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 1.8rem; margin: 0 0 4px; }
h2 { font-size: 1.1rem; margin: 28px 0 8px; }
.lead { color: var(--muted); margin: 0 0 16px; max-width: 75ch; }
.intro { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); margin: 16px 0 8px; }
.intro div { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; font-size: 14px; }
.intro b { display: block; margin-bottom: 2px; }
.controls { background: var(--bg); padding: 12px 0 10px; border-bottom: 1px solid var(--line); margin-bottom: 16px; }
@media (min-width: 760px) { .controls { position: sticky; top: 0; z-index: 2; } }
.row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 8px; }
input[type=search] { flex: 1 1 260px; padding: 9px 12px; border-radius: 8px; border: 1px solid var(--line);
  background: var(--card); color: var(--text); font: inherit; }
.chip, .tag, .clear { border: 1px solid var(--line); background: var(--chip); color: var(--text);
  border-radius: 999px; padding: 3px 10px; font: inherit; font-size: 13px; cursor: pointer; }
.chip[aria-pressed=true] { background: var(--accent); border-color: var(--accent); color: #fff; }
.chip .n, label .n { opacity: .65; font-size: 12px; }
.tag { font-size: 12px; padding: 1px 8px; }
.tag:hover, .chip:hover { border-color: var(--accent); }
.clear { background: transparent; }
.be-row label { font-size: 13px; margin-right: 6px; cursor: pointer; }
.status { color: var(--muted); font-size: 13px; }
.grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }
.tool { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px;
  display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.tool[hidden] { display: none; }
.tool:target { outline: 2px solid var(--accent); }
.tool header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; }
.tool h3 { margin: 0; font: 600 15px/1.3 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.tool h3 a { color: inherit; text-decoration: none; }
.module { font-size: 12px; color: var(--muted); }
.backend { margin-left: auto; font-size: 11px; font-weight: 600; padding: 1px 8px; border-radius: 999px;
  border: 1px solid currentColor; }
.b-opencv { color: var(--cv); } .b-libvips { color: var(--vips); } .b-imagemagick { color: var(--im); }
.summary { margin: 0; }
.tags { display: flex; flex-wrap: wrap; gap: 4px; }
details summary { cursor: pointer; color: var(--accent); font-size: 13px; }
.desc { white-space: pre-wrap; font: 13px/1.5 inherit; font-family: inherit; color: var(--muted); margin: 8px 0; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; display: block; overflow-x: auto; }
th, td { text-align: left; padding: 4px 6px; border-top: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; }
td code { word-break: break-word; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .92em; }
.muted { color: var(--muted); font-size: 13px; }
.empty { display: none; color: var(--muted); padding: 24px 0; }
.theme { margin-left: auto; }
footer { margin-top: 40px; color: var(--muted); font-size: 13px; }
</style>
</head>
<body>
<main>
<h1>my-image-mcp tools</h1>
<p class="lead">my-image-mcp gives an AI assistant (Claude, ChatGPT and other MCP clients) a toolbox of
{{COUNT}} image tools built on OpenCV, libvips and ImageMagick. You don't call these tools yourself:
ask the assistant in plain language (for example <i>"count the coins in photo.jpg and outline them"</i>)
and it picks the tools. This page shows what it can do.</p>

<div class="intro">
  <div><b>Images stay in memory</b>Each result gets a short name like <code>img_3</code>. The original is never
  changed: every step makes a new image, so the assistant can compare and go back.</div>
  <div><b>Numbers, not just pictures</b>Many tools also return measurements (counts, areas, positions,
  scores) that the assistant can reason about and report to you.</div>
  <div><b>Colors and boxes</b>Colors are written like <code>#ff0000</code>, <code>red</code> or
  <code>255,0,0</code>. Positions are in pixels from the top-left corner; boxes are <code>[x, y, width, height]</code>.</div>
  <div><b>Requirements</b><span class="b-opencv">OpenCV</span> and <span class="b-libvips">libvips</span>
  tools always work. <span class="b-imagemagick">ImageMagick</span> tools need ImageMagick 7 installed
  on the computer running the server.</div>
</div>

<div class="controls">
  <div class="row">
    <input type="search" id="q" placeholder="Search tools, e.g. &quot;face&quot;, &quot;crop&quot;, &quot;text&quot;..." aria-label="Search tools">
    <button type="button" class="clear" id="clear">Clear filters</button>
    <button type="button" class="clear theme" id="theme" aria-label="Toggle dark mode">Light / dark</button>
  </div>
  <div class="row" id="chips" aria-label="Filter by functionality">
{{CHIPS}}
  </div>
  <div class="row be-row"><span class="status">Backend:</span>
{{BACKENDS}}
    <span class="status" id="status"></span>
  </div>
</div>

<section class="grid" id="grid">
{{CARDS}}
</section>
<p class="empty" id="empty">No tool matches these filters.</p>

<footer>Generated from the live tool list by <code>scripts/gen_tools_doc.py</code>. Tags combine with
<b>AND</b> (a tool must have every selected tag). The detailed descriptions are the same text the
assistant reads.</footer>
</main>
<script>
(() => {
  const q = document.getElementById('q');
  const tools = [...document.querySelectorAll('.tool')];
  const chips = [...document.querySelectorAll('.chip')];
  const bes = [...document.querySelectorAll('.be')];
  const status = document.getElementById('status');
  const empty = document.getElementById('empty');
  const selected = new Set();

  function apply() {
    const words = q.value.toLowerCase().split(/\\s+/).filter(Boolean);
    const allowed = new Set(bes.filter(b => b.checked).map(b => b.value));
    let shown = 0;
    for (const t of tools) {
      const tags = t.dataset.tags.split(' ');
      const ok = [...selected].every(s => tags.includes(s))
        && allowed.has(t.dataset.backend)
        && words.every(w => t.dataset.search.includes(w));
      t.hidden = !ok;
      if (ok) shown++;
    }
    chips.forEach(c => c.setAttribute('aria-pressed', selected.has(c.dataset.tag)));
    status.textContent = `Showing ${shown} of ${tools.length} tools`;
    empty.style.display = shown ? 'none' : 'block';
    const params = new URLSearchParams();
    if (q.value) params.set('q', q.value);
    if (selected.size) params.set('tags', [...selected].join(','));
    history.replaceState(null, '', (params.toString() ? '?' + params : location.pathname) + location.hash);
  }
  function toggle(tag) { selected.has(tag) ? selected.delete(tag) : selected.add(tag); apply(); }

  chips.forEach(c => c.addEventListener('click', () => toggle(c.dataset.tag)));
  document.querySelectorAll('.tag').forEach(b => b.addEventListener('click', () => {
    if (!selected.has(b.dataset.tag)) toggle(b.dataset.tag);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }));
  q.addEventListener('input', apply);
  bes.forEach(b => b.addEventListener('change', apply));
  document.getElementById('clear').addEventListener('click', () => {
    selected.clear(); q.value = ''; bes.forEach(b => b.checked = true); apply();
  });
  document.getElementById('theme').addEventListener('click', () => {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === 'dark'
      : matchMedia('(prefers-color-scheme: dark)').matches;
    root.dataset.theme = dark ? 'light' : 'dark';
  });

  const p = new URLSearchParams(location.search);
  if (p.get('q')) q.value = p.get('q');
  (p.get('tags') || '').split(',').filter(t => chips.some(c => c.dataset.tag === t)).forEach(t => selected.add(t));
  apply();
})();
</script>
</body>
</html>
"""


def main() -> None:
    tools = anyio.run(fetch_tools)
    names = {t.name for t in tools}
    missing = sorted(n for n in CATALOG if n not in names)
    if missing:
        raise SystemExit(
            f"these catalogued tools are not registered: {missing}. "
            "Install ImageMagick 7 so the magick tools are available, or remove stale CATALOG entries."
        )
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(render(build_entries(tools)), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(tools)} tools)")


if __name__ == "__main__":
    sys.exit(main())
