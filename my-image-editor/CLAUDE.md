# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A lightweight wxPython + Pillow image editor: open JPG/PNG (anything Pillow
reads), crop, resize, draw rectangles / ellipses / lines / arrows / text, save
as JPG or PNG. Linux, macOS and Windows.

Templated from the sibling `my-ai-code-viewer` app: same composition root
(`frame.py`), `.sql` migrations + `settings` table + `SettingsRepository`,
Preferences/About dialogs, theme handling (`theme.py`), window geometry,
VERSION/PyInstaller/workflow setup. Profiles and datasources were
deliberately **not** carried over, and there is no AUR package yet (explicit
decision). SQLite only holds preferences for now; future features add tables
via new `app/db/migrations/000N_*.sql` files.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 main.py [image]

pip install pytest && python -m pytest -q test      # wx-free, no display needed

.venv/bin/pip install pyinstaller
.venv/bin/pyinstaller --noconfirm myimageeditor.spec
./dist/myimageeditor/myimageeditor
```

## Architecture

### Editing core (`app/editor/`) - wx-free, unit-tested

- `shapes.py` - `RectShape`, `EllipseShape` (both `BoxShape`), `LineShape`,
  `ArrowShape`, `TextShape`; plus `Style` (what new shapes get / the sidebar
  edits). Image-pixel float coordinates. Each shape: `bounds()` (includes the
  stroke, which is centred on the geometry), `hit()`, `handles()` +
  `drag_handle()` (returns the handle's new name when a box flips inside
  out), `move()`, `scale()` (used by resize), `STYLE_FIELDS` (which `Style`
  fields apply), `to_dict()`/`shape_from_dict()`. Stable `id` survives
  copies, so selection survives undo/redo.
- `document.py` - `Document` = base RGBA image + shape list + `History`.
  **Every mutation calls `checkpoint(label)` first** (that's what makes it
  undoable). Drags checkpoint once at press and `cancel_checkpoint()` if
  nothing changed. `coalesce=` merges repeats (arrow nudges, spin-button
  clicks on one style field) into one step. `modified` = `state_id !=
  saved_state_id` (works through undo back to the save point). `crop` moves
  shapes, `resize` scales them, `flatten` bakes them in. `save()` renders
  shapes into a *copy*; the document keeps them editable.
- `history.py` - snapshot undo: (image ref, shape copies, label). Images are
  never mutated in place, so steps that don't touch pixels share one image.
  Limit 100 steps.
- `render.py` - **the only renderer**, used by both the canvas and `save`
  (WYSIWYG). Each shape -> own RGBA tile over its bounds, supersampled 3x
  (capped by `MAX_TILE_PIXELS`) for anti-aliasing since ImageDraw doesn't AA;
  text isn't supersampled (FreeType AAs). `clip` limits tiles to the visible
  area (essential at high zoom). `ViewRenderer` builds one frame: crop of the
  base (cached downscale when zoom < 1, NEAREST box-resize when >= 1) + cached
  shape tiles (keyed by `shape.key()`, scale, clip) + checkerboard for
  transparent images. Needs an integer device origin.
- `fonts.py` - Sans/Serif/Mono -> bundled DejaVu TTFs in `app/fonts/`
  (bundled so output is identical on every OS; in the PyInstaller spec's
  `datas`). Fallback: Pillow's built-in font.
- `imageio.py` - load (EXIF transpose, -> RGBA, keep ICC) / save (by
  extension: PNG, or JPEG flattened on white; PNG without transparency saved
  as RGB; temp file + `os.replace`).
- `viewport.py` - zoom/pan math: `screen = origin + image * zoom` in logical
  px; `device_origin(scale)` for HiDPI rendering; `clamp()` centres an axis
  that fits and stops panning past the edges otherwise; `set_zoom(anchor)`
  keeps the point under the cursor fixed; `ZOOM_STEPS`.
- `tools.py` - tools as state machines taking image coords:
  `press/drag/release/double_click/cursor_at/key/cancel/preview`. Select
  (move, handles, marquee, Shift toggles), Hand (`PANS = True`; the canvas
  pans), Rect/Ellipse/Line/Arrow (`ShapeTool`), Text (acts on *release* -
  it opens a modal dialog, not allowed while the mouse is captured), Crop
  (rect + handles + move; Enter applies).
- `editor.py` - `Editor`: doc + selection (ids) + style + active tool +
  listeners. Notifies `"document"` (content changed, also mid-drag),
  `"selection"`, `"style"`, `"tool"`, `"overlay"` (only decorations). Selecting
  a shape copies its style into `editor.style` (sidebar shows it; new shapes
  use it). `request_text` is the UI callback for the text dialog. Hit
  tolerance / handle reach are screen px converted via `set_zoom`.

### UI (`app/`)

- `frame.py` - composition root: DB + migrations, settings, `Editor`
  (style restored from setting `default_style` JSON, saved on every style
  change), sidebar | canvas, menus (File / Edit / Image / View / Tools / Help),
  open/save/save-as with "save changes?" (`confirm_discard`) on open, drop and
  close, window geometry, theme. Keyboard goes through `EVT_CHAR_HOOK`
  (`_on_char_hook`): tool letters, Enter/Esc/Delete/arrows (-> `Editor.key`),
  Space (pan), +/-; skipped while a text/spin field has focus or for
  Ctrl/Alt combos (menu accelerators). Tool letters and Delete are
  deliberately **not** menu accelerators (they'd fire while typing). Ctrl+Y
  redo is in the hook too (a frame accelerator table would replace the menu
  accelerators on MSW).
- `canvas.py` - `wx.Panel`; paints `ViewRenderer` output at device resolution
  (bitmap `SetScaleFactor`), then overlays (selection, handles, marquee, crop
  shade/frame/size label) with `wx.GCDC`. Uses `PaintDC` when the platform
  double-buffers, else `BufferedPaintDC` (GCDC rejects `AutoBufferedPaintDC`).
  Mouse capture during drags; middle-drag / Space+drag / Hand tool pan;
  Ctrl+wheel zoom at pointer. Space state is tracked from the frame's key hook
  + canvas key-up (wx.GetKeyState can't read Space on GTK/Wayland).
- `sidebar.py` - collapsible (user preference: icon-only strip, setting
  `sidebar_collapsed`, Ctrl+B): tool toggle buttons, style controls (disabled
  when not applicable to the selection / active tool), crop panel while the
  crop tool is active, tool hint.
- `icons.py` - tool icons drawn with GraphicsContext in the theme's text colour.
- `dialogs.py` - `TextDialog` (Ctrl+Enter accepts), `ResizeDialog`.
- `preferences_dialog.py` - Files tab (JPEG quality, PNG compression, open at
  fit/100%), Visual tab (theme). `theme.py` - same two-layer approach as the
  siblings: the canvas palette switches live; native widgets via
  `GTK_THEME` before `wx.App` (restart offered).
- `about_dialog.py` - Help > About.

## Not done yet (ideas)

- A project format that keeps shapes editable across sessions (shapes already
  serialize with `to_dict`; could live in SQLite or a sidecar file).
- Recent files (SQLite), paste image from clipboard / copy result, rotate /
  flip, freehand pen, highlight / blur (redact) tools, text background box.
- GitHub Pages homepage (`docs/my-image-editor/`) and an AUR package - when
  added, copy the docs version-stamp steps from `my-ai-code-viewer-build.yml`.
