from app import theme
from app.conversation_export import ExportOptions, _page_theme


def test_resolve_dark():
    assert theme.resolve_dark("system", True) and not theme.resolve_dark("system", False)
    assert theme.resolve_dark("dark", False) and not theme.resolve_dark("light", True)
    assert theme.resolve_dark(None, True) and theme.resolve_dark("bogus", True)  # -> system


def test_gtk_theme_for():
    assert theme.gtk_theme_for("dark", {}, "linux") == "Adwaita:dark"
    assert theme.gtk_theme_for("light", {}, "linux") == "Adwaita"
    assert theme.gtk_theme_for("system", {}, "linux") is None
    assert theme.gtk_theme_for("dark", {"GTK_THEME": "Mine"}, "linux") is None  # user's wins
    assert theme.gtk_theme_for("dark", {}, "win32") is None


def test_apply_and_forget_native_override():
    env = {}
    value = theme.apply_native_theme("dark", env)
    assert env["GTK_THEME"] == "Adwaita:dark"
    theme.forget_native_override(value, env)
    assert "GTK_THEME" not in env
    env = {"GTK_THEME": "Mine"}
    theme.forget_native_override(theme.apply_native_theme("light", env), env)
    assert env == {"GTK_THEME": "Mine"}


def test_parse_portal_color_scheme():
    assert theme.parse_portal_color_scheme("(<uint32 1>,)\n") is True
    assert theme.parse_portal_color_scheme("(<<uint32 2>>,)\n") is False
    assert theme.parse_portal_color_scheme("(<uint32 0>,)\n") is None
    assert theme.parse_portal_color_scheme("") is None


def test_settings_round_trip(settings_repo):
    assert theme.normalize(settings_repo.get(theme.THEME_SETTING_KEY)) == "system"
    settings_repo.set(theme.THEME_SETTING_KEY, "dark")
    assert theme.normalize(settings_repo.get(theme.THEME_SETTING_KEY)) == "dark"


def test_export_default_theme():
    assert _page_theme("dark") == "dark" and _page_theme("light") == "light"
    assert _page_theme("system") == "auto"
    assert ExportOptions().theme == "system"
