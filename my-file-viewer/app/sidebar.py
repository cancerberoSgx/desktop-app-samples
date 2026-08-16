from typing import Callable, List, Optional

import wx

from .models import Favorite

"""Left navigation sidebar: the favorite-folders shortcut list, per
CLAUDE.md's collapsible-sidebar convention shared with the sibling apps
(my-data-viewer/my-redis-viewer/my-docker-viewer/my-disk-viewer). Unlike
those apps' Sidebar, this one doesn't switch between a fixed set of pages -
there's only one main screen (FolderExplorerPage) - it's a dynamic list of
favorite folders that each open in that same screen when clicked."""


class FavoritesSidebar(wx.Panel):
    """Collapsible via the arrow button at the top: collapsed mode shrinks
    the bar down to a icon-only strip (folder icons + tooltips) to save
    horizontal space, expanded mode shows the full folder name per
    favorite. Content is rebuilt from scratch on every refresh() - the
    favorites list is expected to stay small (a handful to a few dozen
    pinned folders), so this is simpler than diffing rows in and out."""

    EXPANDED_WIDTH = 220
    COLLAPSED_WIDTH = 48

    def __init__(
        self,
        parent: wx.Window,
        on_select: Callable[[str], None],
        on_remove: Callable[[int], None],
        on_toggle_collapsed: Optional[Callable[[bool], None]] = None,
    ) -> None:
        super().__init__(parent, style=wx.BORDER_NONE)
        self._on_select = on_select
        self._on_remove = on_remove
        self._on_toggle_collapsed = on_toggle_collapsed
        self._favorites: List[Favorite] = []
        self._buttons: List[wx.Button] = []
        self._collapsed = False
        self._selected_path: Optional[str] = None

        self.SetBackgroundColour(wx.Colour(45, 51, 59))

        self._sizer = wx.BoxSizer(wx.VERTICAL)

        self._toggle_btn = wx.Button(self, label="«", size=(28, 28), style=wx.BORDER_NONE)
        self._toggle_btn.SetBackgroundColour(self.GetBackgroundColour())
        self._toggle_btn.SetForegroundColour(wx.Colour(230, 230, 230))
        self._toggle_btn.SetToolTip("Collapse sidebar")
        self._toggle_btn.Bind(wx.EVT_BUTTON, self._on_toggle_clicked)
        self._sizer.Add(self._toggle_btn, 0, wx.ALIGN_RIGHT | wx.RIGHT | wx.TOP, 4)

        self._title = wx.StaticText(self, label="My File Viewer")
        self._title.SetForegroundColour(wx.Colour(230, 230, 230))
        font = self._title.GetFont()
        font.SetPointSize(font.GetPointSize() + 1)
        font.MakeBold()
        self._title.SetFont(font)
        self._sizer.Add(self._title, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL, 16)

        self._sizer.Add(wx.StaticLine(self), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        self._section_label = wx.StaticText(self, label="FAVORITES")
        self._section_label.SetForegroundColour(wx.Colour(160, 168, 178))
        self._sizer.Add(self._section_label, 0, wx.LEFT | wx.TOP, 12)

        self._favorites_panel = wx.Panel(self)
        self._favorites_panel.SetBackgroundColour(self.GetBackgroundColour())
        self._favorites_sizer = wx.BoxSizer(wx.VERTICAL)
        self._favorites_panel.SetSizer(self._favorites_sizer)
        self._sizer.Add(self._favorites_panel, 0, wx.EXPAND | wx.TOP, 4)

        self._empty_label = wx.StaticText(self, label="No favorites yet.\nOpen a folder and\nclick 'Add to\nFavorites'.")
        self._empty_label.SetForegroundColour(wx.Colour(150, 156, 164))
        self._sizer.Add(self._empty_label, 0, wx.ALL, 12)

        self._sizer.AddStretchSpacer()

        self._exit_btn = wx.Button(self, label="  Exit")
        self._exit_btn.SetBackgroundColour(self.GetBackgroundColour())
        self._exit_btn.SetForegroundColour(wx.Colour(230, 230, 230))
        self._exit_btn.SetBitmap(wx.ArtProvider.GetBitmap(wx.ART_QUIT, wx.ART_BUTTON, (20, 20)))
        self._exit_btn.Bind(wx.EVT_BUTTON, lambda evt: self.GetTopLevelParent().Close())
        self._sizer.Add(self._exit_btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.SetSizer(self._sizer)
        self.SetMinSize((self.EXPANDED_WIDTH, -1))
        self.refresh([])

    # ------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------
    def refresh(self, favorites: List[Favorite]) -> None:
        """Rebuild the favorites row list - called on startup and after any
        add/remove (see MainFrame._on_favorites_changed)."""
        self._favorites = favorites
        self._favorites_sizer.Clear(delete_windows=True)
        self._buttons = []

        for favorite in favorites:
            btn = self._make_favorite_button(favorite)
            self._buttons.append(btn)
            self._favorites_sizer.Add(btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 4)

        self._empty_label.Show(not favorites and not self._collapsed)
        self._apply_collapsed_labels()
        self._favorites_panel.Layout()
        self.Layout()

    def set_selected_path(self, path: Optional[str]) -> None:
        """Highlights whichever favorite row matches the folder currently
        open in the explorer (no highlight if the open folder isn't
        favorited)."""
        self._selected_path = path
        for favorite, btn in zip(self._favorites, self._buttons):
            is_selected = path is not None and favorite.path == path
            btn.SetBackgroundColour(wx.Colour(70, 78, 90) if is_selected else self.GetBackgroundColour())
            btn.Refresh()

    def _make_favorite_button(self, favorite: Favorite) -> wx.Button:
        btn = wx.Button(self._favorites_panel, label=f"  {favorite.name}")
        btn.SetBitmap(wx.ArtProvider.GetBitmap(wx.ART_FOLDER, wx.ART_BUTTON, (18, 18)))
        btn.SetBitmapMargins(6, 4)
        btn.SetBackgroundColour(self.GetBackgroundColour())
        btn.SetForegroundColour(wx.Colour(230, 230, 230))
        btn.SetToolTip(favorite.path)
        btn.Bind(wx.EVT_BUTTON, lambda evt, p=favorite.path: self._on_select(p))
        btn.Bind(wx.EVT_CONTEXT_MENU, lambda evt, f=favorite: self._show_context_menu(f))
        return btn

    def _show_context_menu(self, favorite: Favorite) -> None:
        menu = wx.Menu()
        remove_item = menu.Append(wx.ID_ANY, "Remove from Favorites")
        self.Bind(wx.EVT_MENU, lambda evt, f=favorite: self._on_remove(f.id), remove_item)
        self.PopupMenu(menu)
        menu.Destroy()

    # ------------------------------------------------------------------
    # Collapse
    # ------------------------------------------------------------------
    def _on_toggle_clicked(self, event: wx.CommandEvent) -> None:
        self.set_collapsed(not self._collapsed)
        if self._on_toggle_collapsed is not None:
            self._on_toggle_collapsed(self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed

        self._title.Show(not collapsed)
        self._section_label.Show(not collapsed)
        self._toggle_btn.SetLabel("»" if collapsed else "«")
        self._toggle_btn.SetToolTip("Expand sidebar" if collapsed else "Collapse sidebar")

        self._apply_collapsed_labels()
        self._empty_label.Show(not self._favorites and not collapsed)

        self.SetMinSize((self.COLLAPSED_WIDTH if collapsed else self.EXPANDED_WIDTH, -1))
        root_panel = self.GetParent()
        root_panel.Layout()
        root_panel.Refresh()

    def _apply_collapsed_labels(self) -> None:
        for favorite, btn in zip(self._favorites, self._buttons):
            btn.SetLabel("" if self._collapsed else f"  {favorite.name}")
        self._exit_btn.SetLabel("" if self._collapsed else "  Exit")
