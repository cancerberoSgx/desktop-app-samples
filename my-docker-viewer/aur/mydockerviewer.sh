#!/bin/sh
# Launcher installed by the my-docker-viewer-git AUR package at /usr/bin/mydockerviewer.
# The app's main.py + app/ package live under /usr/lib/mydockerviewer/ instead of
# Python's shared site-packages (see aur/PKGBUILD for why) - PYTHONPATH points at
# that private directory so `from app.frame import MainFrame` in main.py resolves.
exec env PYTHONPATH="/usr/lib/mydockerviewer${PYTHONPATH:+:$PYTHONPATH}" python3 /usr/lib/mydockerviewer/main.py "$@"
