#!/bin/sh
# Launcher installed by the my-disk-viewer-git AUR package at /usr/bin/mydiskviewer.
# The app's main.py + app/ package live under /usr/lib/mydiskviewer/ instead of
# Python's shared site-packages (see aur/PKGBUILD for why) - PYTHONPATH points at
# that private directory so `from app.frame import MainFrame` in main.py resolves.
exec env PYTHONPATH="/usr/lib/mydiskviewer${PYTHONPATH:+:$PYTHONPATH}" python3 /usr/lib/mydiskviewer/main.py "$@"
