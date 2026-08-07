#!/bin/sh
# Launcher installed by the my-redis-viewer-git AUR package at /usr/bin/myredisviewer.
# The app's main.py + app/ package live under /usr/lib/myredisviewer/ instead of
# Python's shared site-packages (see aur/PKGBUILD for why) - PYTHONPATH points at
# that private directory so `from app.frame import MainFrame` in main.py resolves.
exec env PYTHONPATH="/usr/lib/myredisviewer${PYTHONPATH:+:$PYTHONPATH}" python3 /usr/lib/myredisviewer/main.py "$@"
