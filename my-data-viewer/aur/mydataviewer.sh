#!/bin/sh
# Launcher installed by the my-data-viewer-git AUR package at /usr/bin/mydataviewer.
# The app's main.py + app/ package live under /usr/lib/mydataviewer/ instead of
# Python's shared site-packages (see aur/PKGBUILD for why) - PYTHONPATH points at
# that private directory so `from app.frame import MainFrame` in main.py resolves.
exec env PYTHONPATH="/usr/lib/mydataviewer${PYTHONPATH:+:$PYTHONPATH}" python3 /usr/lib/mydataviewer/main.py "$@"
