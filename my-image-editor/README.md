# My Image Editor

A lightweight wxPython + Pillow image editor for Linux, macOS and Windows: open
an image, cut out the part you want, resize it, annotate it with shapes and
text, save it as JPG or PNG.

- **Open** any image Pillow reads (PNG, JPEG, BMP, GIF, WebP, TIFF) - File >
  Open (Ctrl+O), drag & drop onto the window, a click on the empty canvas, or
  `python3 main.py photo.jpg`. Phone photos are rotated by their EXIF
  orientation.
- **Save** as PNG or JPEG (File > Save / Save As, Ctrl+S / Ctrl+Shift+S). JPEG
  quality and PNG compression are in File > Preferences. Transparent areas
  become white in a JPEG. The colour profile (ICC) is kept.
- **Crop** (C): drag the area to keep, adjust it with the handles or move it,
  then Enter (or the sidebar's Crop button, or Image > Crop to Selection).
- **Resize** (Image > Resize, Ctrl+R): pixels or percent, aspect ratio locked
  by default, Lanczos / bicubic / bilinear / nearest.
- **Shapes**: Rectangle (R), Ellipse (E), Line (L), Arrow (A), Text (T). Shift
  draws squares / circles / 45° lines. Colour (with opacity), fill, line width,
  font (Sans / Serif / Mono), size, bold and italic are in the sidebar; they
  apply to the selected shapes and to the next ones you draw.
- **Shapes stay editable** until you close the app: Select (V) to click, drag,
  resize with handles, Shift+click or drag a box to select several,
  double-click text to edit it, arrow keys to nudge (Shift: 10 px), Delete,
  Duplicate (Ctrl+D), Bring to front / Send to back (Ctrl+] / Ctrl+[). Saving
  writes them into the file but keeps them editable; Image > Flatten Shapes
  bakes them into the pixels on purpose.
- **Undo / redo** everything (Ctrl+Z, Ctrl+Shift+Z or Ctrl+Y) - the menu says
  what will be undone. Crop and resize carry the shapes along.
- **Zoom & pan**: Ctrl+wheel zooms around the pointer, +/- or View > Zoom
  In/Out, Fit (Ctrl+0), 100% (Ctrl+1). Pan with the wheel (Shift: sideways),
  the middle button, Space+drag or the Pan tool (H). Sharp on HiDPI screens.
- **WYSIWYG**: the canvas and the saved file are drawn by the same code with
  the bundled DejaVu fonts, so a saved image looks like the screen - and the
  same on every OS.
- **Preferences** (File > Preferences): JPEG quality, PNG compression, fit or
  100% when opening, theme (system / light / dark). Window size, sidebar
  state, last folder and the drawing style are remembered
  (`~/.my-image-editor/`, SQLite).

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 main.py [image]
```

## Test

```bash
pip install pytest
python -m pytest -q test
```

## Build

```bash
pip install pyinstaller
pyinstaller --noconfirm myimageeditor.spec
./dist/myimageeditor/myimageeditor
```

Releases are built for Linux, Windows and macOS by
`.github/workflows/my-image-editor-build.yml` when a `my-image-editor-v*` tag is
pushed (see the root `README.md`, "Versioning").

See `CLAUDE.md` for the architecture.

The bundled fonts in `app/fonts/` are DejaVu (free license, see
`app/fonts/LICENSE-DejaVu`).
