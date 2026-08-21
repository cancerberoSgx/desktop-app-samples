#!/bin/sh
# Launcher installed by the my-documents-viewer-git AUR package at /usr/bin/mydocumentsviewer.
# The app's main.py + app/ package live under /usr/lib/mydocumentsviewer/ instead of
# Python's shared site-packages (see aur/PKGBUILD for why) - PYTHONPATH points at
# that private directory so `from app.frame import MainFrame` in main.py resolves.
exec env PYTHONPATH="/usr/lib/mydocumentsviewer${PYTHONPATH:+:$PYTHONPATH}" python3 /usr/lib/mydocumentsviewer/main.py "$@"
