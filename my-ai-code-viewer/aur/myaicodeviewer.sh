#!/bin/sh
# Launcher installed by the my-ai-code-viewer-git AUR package at /usr/bin/myaicodeviewer.
# The app's main.py + app/ package live under /usr/lib/myaicodeviewer/ instead of
# Python's shared site-packages (see aur/PKGBUILD for why) - PYTHONPATH points at
# that private directory so `from app.frame import MainFrame` in main.py resolves.
exec env PYTHONPATH="/usr/lib/myaicodeviewer${PYTHONPATH:+:$PYTHONPATH}" python3 /usr/lib/myaicodeviewer/main.py "$@"
