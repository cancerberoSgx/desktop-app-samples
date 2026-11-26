"""App theme: "system" (follow the desktop), "light" or "dark".

Two layers, because wxWidgets 3.2 can't switch native widgets at runtime:

- What the app draws itself (the canvas background, tool icons - see
  `canvas.LIGHT/DARK`) follows the setting immediately; in "system" mode it
  follows `wx.SystemSettings.GetAppearance().IsDark()`, re-checked on
  EVT_SYS_COLOUR_CHANGED and on the frame's periodic timer.
- Native widgets (menus, buttons, dialogs) are themed by GTK. A forced
  light/dark sets `GTK_THEME` (Adwaita / Adwaita:dark, always shipped with
  GTK 3) *before* `wx.App` starts - so it applies on the next start only.
  `GTK_THEME` is removed again right after start-up so programs the app
  launches keep the user's theme. A `GTK_THEME` the
  user exported themselves always wins.

Everything except `system_is_dark` is wx-free (and unit-tested).
"""
import os
import sys
from typing import MutableMapping, Optional

THEME_SETTING_KEY = "theme"
SYSTEM, LIGHT, DARK = "system", "light", "dark"
THEMES = ((SYSTEM, "System"), (LIGHT, "Light"), (DARK, "Dark"))
DEFAULT_THEME = SYSTEM

_GTK_THEMES = {LIGHT: "Adwaita", DARK: "Adwaita:dark"}

# The theme the native widgets were started with (set by apply_native_theme);
# a different saved theme needs a restart to reach them.
_native_theme: Optional[str] = None


def normalize(theme: Optional[str]) -> str:
    return theme if theme in (SYSTEM, LIGHT, DARK) else DEFAULT_THEME


def resolve_dark(theme: Optional[str], system_dark: bool) -> bool:
    theme = normalize(theme)
    return system_dark if theme == SYSTEM else theme == DARK


def gtk_theme_for(theme: Optional[str], env: MutableMapping[str, str], platform: str = sys.platform) -> Optional[str]:
    """GTK_THEME value to start with, or None to leave GTK alone."""
    if not platform.startswith("linux") or "GTK_THEME" in env:
        return None
    return _GTK_THEMES.get(normalize(theme))


def apply_native_theme(theme: Optional[str], env: MutableMapping[str, str] = os.environ) -> Optional[str]:
    """Call before wx.App(). Returns the GTK_THEME it set (pass it to
    `forget_native_override` once the App exists)."""
    global _native_theme
    _native_theme = normalize(theme)
    value = gtk_theme_for(theme, env)
    if value:
        env["GTK_THEME"] = value
    return value


def forget_native_override(value: Optional[str], env: MutableMapping[str, str] = os.environ) -> None:
    """GTK has read GTK_THEME by now; drop it so child processes don't inherit it."""
    if value and env.get("GTK_THEME") == value:
        del env["GTK_THEME"]


def needs_restart(theme: Optional[str]) -> bool:
    """True if native widgets were started with a different theme than `theme`
    and the platform lets us switch them (GTK, no user GTK_THEME)."""
    if _native_theme is None or normalize(theme) == _native_theme:
        return False
    return sys.platform.startswith("linux") and "GTK_THEME" not in os.environ


def _portal_prefers_dark() -> Optional[bool]:
    """The desktop's colour-scheme from the freedesktop settings portal
    (1 = dark, 2 = light, 0 = no preference -> None)."""
    import subprocess
    try:
        out = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.freedesktop.portal.Desktop",
             "--object-path", "/org/freedesktop/portal/desktop",
             "--method", "org.freedesktop.portal.Settings.ReadOne",
             "org.freedesktop.appearance", "color-scheme"],
            capture_output=True, text=True, timeout=2,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_portal_color_scheme(out)


def parse_portal_color_scheme(out: str) -> Optional[bool]:
    """`(<uint32 1>,)` -> True, `(<<uint32 2>>,)` -> False, else None."""
    digits = "".join(ch for ch in out.split("uint32", 1)[-1] if ch.isdigit()) if "uint32" in out else ""
    return {"1": True, "2": False}.get(digits)


def system_is_dark() -> bool:
    """The desktop's light/dark preference. While this run's native widgets
    are forced (GTK_THEME), wx reports the forced theme, so ask the portal."""
    if _native_theme in _GTK_THEMES and sys.platform.startswith("linux"):
        portal = _portal_prefers_dark()
        if portal is not None:
            return portal
    import wx
    try:
        return wx.SystemSettings.GetAppearance().IsDark()
    except AttributeError:  # wxPython < 4.1
        return False
