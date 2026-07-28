# wxPython Demo App

A sample desktop GUI application built with [wxPython](https://wxpython.org) showing:

- A left-hand **sidebar** with icon buttons for navigation.
- A **main content area** that swaps pages when a sidebar option is clicked,
  including a full gallery of common form widgets (text entry, choices,
  sliders, pickers, lists, dialogs...).
- A top **menu bar** with nested sub-menus (File > New > Project/File,
  Edit > Advanced, View > Theme).

## Project layout

```
main.py            Entry point
app/
  frame.py          Main window: menu bar + sidebar + page-switching area
  sidebar.py         Left navigation sidebar with icon buttons
  pages.py            Individual pages, including the widgets gallery
requirements.txt
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate      # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
python3 main.py
```

## Building standalone executables

Standalone executables are built with [PyInstaller](https://pyinstaller.org).
PyInstaller does not cross-compile, so each executable must be built on its
target OS (build the Linux binary on Linux, the Windows `.exe` on Windows,
and the macOS `.app` on macOS).

Install PyInstaller into the same virtual environment used to run the app:

```bash
pip install pyinstaller
```

### Linux

```bash
pyinstaller --noconfirm --windowed --name wxdemo main.py
```

The executable is created at `dist/wxdemo/wxdemo`. Copy the whole
`dist/wxdemo/` folder when distributing it, since the executable depends on
the other files placed alongside it. GTK and its shared libraries must be
present on the target machine (already the case on most desktop Linux
distributions).

### Windows

Run this from a Windows machine with Python and `requirements.txt` installed:

```bash
pyinstaller --noconfirm --windowed --name wxdemo main.py
```

The executable is created at `dist\wxdemo\wxdemo.exe`. Distribute the whole
`dist\wxdemo\` folder, not just the `.exe`, since it depends on the DLLs
placed alongside it.

### macOS

Run this from a Mac with Python and `requirements.txt` installed:

```bash
pyinstaller --noconfirm --windowed --name wxdemo main.py
```

This produces both `dist/wxdemo.app` (an app bundle you can double-click or
drag into `/Applications`) and `dist/wxdemo/` (the raw folder build). If
macOS Gatekeeper blocks the unsigned app on first launch, right-click it and
choose "Open", or sign/notarize it with your own Apple Developer certificate
before distributing it.

### Notes

- `--windowed` (a.k.a. `--noconsole`) hides the background terminal/console
  window, which is what you want for a GUI app.
- Add `--onefile` to any of the commands above to bundle everything into a
  single executable instead of a folder. It is more convenient to hand out
  but starts up slower, since it has to unpack itself into a temp directory
  each time it runs.
- Add `--icon path/to/icon.ico` (Windows), `--icon path/to/icon.icns`
  (macOS), or set the icon via a `.spec` file (Linux desktop entries use
  their own separate `.desktop` file) to give the executable a custom icon.
