import wx
import wx.adv

from app.sidebar import Sidebar, SIDEBAR_ITEMS
from app.pages import (
    HomePage,
    BasicControlsPage,
    AdvancedControlsPage,
    TablePage,
    DialogsPage,
    AboutPage,
)

ID_NEW_PROJECT = wx.NewIdRef()
ID_NEW_FILE = wx.NewIdRef()
ID_FIND = wx.NewIdRef()
ID_REPLACE = wx.NewIdRef()
ID_THEME_LIGHT = wx.NewIdRef()
ID_THEME_DARK = wx.NewIdRef()
ID_TOGGLE_SIDEBAR = wx.NewIdRef()


class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="wxPython Demo App", size=(980, 640))

        self._build_menu_bar()
        self.CreateStatusBar()
        self.SetStatusText("Ready")

        root_panel = wx.Panel(self)
        root_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.sidebar = Sidebar(root_panel, on_select=self._on_sidebar_select)
        root_sizer.Add(self.sidebar, 0, wx.EXPAND)

        self.book = wx.Simplebook(root_panel)
        self.book.AddPage(HomePage(self.book), "Home")
        self.book.AddPage(BasicControlsPage(self.book), "Basic Controls")
        self.book.AddPage(AdvancedControlsPage(self.book), "Advanced Controls")
        self.book.AddPage(TablePage(self.book), "Table")
        self.book.AddPage(DialogsPage(self.book), "Dialogs")
        self.book.AddPage(AboutPage(self.book), "About")

        root_sizer.Add(self.book, 1, wx.EXPAND | wx.ALL, 0)

        root_panel.SetSizer(root_sizer)

        self.Centre()

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------
    def _build_menu_bar(self):
        menu_bar = wx.MenuBar()

        # File menu, with a "New" sub-menu
        file_menu = wx.Menu()
        new_menu = wx.Menu()
        new_menu.Append(ID_NEW_PROJECT, "Project...", "Create a new project")
        new_menu.Append(ID_NEW_FILE, "File...\tCtrl+N", "Create a new file")
        file_menu.AppendSubMenu(new_menu, "New")

        file_menu.Append(wx.ID_OPEN, "Open...\tCtrl+O")
        file_menu.Append(wx.ID_SAVE, "Save\tCtrl+S")
        file_menu.Append(wx.ID_SAVEAS, "Save As...\tCtrl+Shift+S")
        file_menu.AppendSeparator()
        file_menu.Append(wx.ID_EXIT, "Exit\tAlt+F4")

        # Edit menu, with an "Advanced" sub-menu
        edit_menu = wx.Menu()
        edit_menu.Append(wx.ID_CUT, "Cut\tCtrl+X")
        edit_menu.Append(wx.ID_COPY, "Copy\tCtrl+C")
        edit_menu.Append(wx.ID_PASTE, "Paste\tCtrl+V")
        edit_menu.AppendSeparator()

        advanced_menu = wx.Menu()
        advanced_menu.Append(ID_FIND, "Find...\tCtrl+F")
        advanced_menu.Append(ID_REPLACE, "Replace...\tCtrl+H")
        edit_menu.AppendSubMenu(advanced_menu, "Advanced")

        # View menu, with a "Theme" sub-menu
        view_menu = wx.Menu()
        theme_menu = wx.Menu()
        theme_menu.AppendRadioItem(ID_THEME_LIGHT, "Light")
        theme_menu.AppendRadioItem(ID_THEME_DARK, "Dark")
        view_menu.AppendSubMenu(theme_menu, "Theme")
        view_menu.AppendCheckItem(ID_TOGGLE_SIDEBAR, "Show Sidebar")
        view_menu.Check(ID_TOGGLE_SIDEBAR, True)

        # Help menu
        help_menu = wx.Menu()
        help_menu.Append(wx.ID_ABOUT, "About...")

        menu_bar.Append(file_menu, "&File")
        menu_bar.Append(edit_menu, "&Edit")
        menu_bar.Append(view_menu, "&View")
        menu_bar.Append(help_menu, "&Help")

        self.SetMenuBar(menu_bar)

        self.Bind(wx.EVT_MENU, self._on_exit, id=wx.ID_EXIT)
        self.Bind(wx.EVT_MENU, self._on_about, id=wx.ID_ABOUT)
        self.Bind(wx.EVT_MENU, self._on_toggle_sidebar, id=ID_TOGGLE_SIDEBAR)
        self.Bind(wx.EVT_MENU, self._on_generic_menu_item)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _on_sidebar_select(self, index):
        self.book.ChangeSelection(index)
        label = SIDEBAR_ITEMS[index][0]
        self.SetStatusText(f"Viewing: {label}")

    def _on_exit(self, event):
        self.Close()

    def _on_about(self, event):
        wx.MessageBox(
            "wxPython Demo App\n\nA sample GUI showcasing a sidebar, "
            "a page-switching main screen and a nested menu bar.",
            "About wxPython Demo App",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )

    def _on_toggle_sidebar(self, event):
        self.sidebar.Show(event.IsChecked())
        self.sidebar.GetParent().Layout()

    def _on_generic_menu_item(self, event):
        menu_bar = self.GetMenuBar()
        item = menu_bar.FindItemById(event.GetId())
        label = item.GetItemLabelText() if item else "Unknown"
        self.SetStatusText(f"Menu item selected: {label}")
