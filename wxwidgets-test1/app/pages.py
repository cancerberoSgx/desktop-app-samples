import csv
import os
import threading

import wx
import wx.adv


def _section(parent, title):
    """Create a labelled StaticBoxSizer with a vertical box inside for content."""
    box = wx.StaticBox(parent, label=title)
    sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
    return sizer


class HomePage(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)

        sizer = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(self, label="Welcome to the wxPython Demo")
        font = title.GetFont()
        font.SetPointSize(font.GetPointSize() + 8)
        font.MakeBold()
        title.SetFont(font)

        body = wx.StaticText(
            self,
            label=(
                "This sample application demonstrates a typical desktop layout:\n\n"
                "  - A left-hand sidebar for navigation, with icon buttons.\n"
                "  - A main content area that swaps pages when a sidebar\n"
                "    option is selected.\n"
                "  - A top menu bar with nested sub-menus.\n\n"
                "Use the sidebar on the left to browse a gallery of the most\n"
                "common wxWidgets form controls."
            ),
        )

        sizer.Add(title, 0, wx.ALL, 24)
        sizer.Add(body, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        self.SetSizer(sizer)


class BasicControlsPage(wx.Panel):
    """Text entry, choices and buttons - the bread-and-butter form controls."""

    def __init__(self, parent):
        super().__init__(parent)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(
            wx.StaticText(self, label="Basic Controls"),
            0, wx.ALL, 12
        )

        grid = wx.FlexGridSizer(cols=2, gap=(16, 12))
        grid.AddGrowableCol(0, 1)
        grid.AddGrowableCol(1, 1)

        # --- Text entry section ---
        text_sizer = _section(self, "Text Entry")
        grid_text = wx.FlexGridSizer(cols=2, gap=(8, 8))
        grid_text.AddGrowableCol(1, 1)

        grid_text.Add(wx.StaticText(self, label="Name:"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid_text.Add(wx.TextCtrl(self, value="Jane Doe"), 1, wx.EXPAND)

        grid_text.Add(wx.StaticText(self, label="Password:"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid_text.Add(wx.TextCtrl(self, style=wx.TE_PASSWORD), 1, wx.EXPAND)

        grid_text.Add(wx.StaticText(self, label="Comments:"), 0, wx.TOP, 4)
        grid_text.Add(
            wx.TextCtrl(self, value="Multi-line text area...", style=wx.TE_MULTILINE, size=(-1, 70)),
            1, wx.EXPAND
        )
        text_sizer.Add(grid_text, 1, wx.EXPAND | wx.ALL, 10)

        # --- Selection section ---
        select_sizer = _section(self, "Selection Controls")
        grid_select = wx.FlexGridSizer(cols=2, gap=(8, 8))
        grid_select.AddGrowableCol(1, 1)

        grid_select.Add(wx.StaticText(self, label="Country:"), 0, wx.ALIGN_CENTER_VERTICAL)
        combo = wx.ComboBox(
            self, value="Spain",
            choices=["Spain", "France", "Germany", "Italy", "Portugal"],
            style=wx.CB_READONLY
        )
        grid_select.Add(combo, 1, wx.EXPAND)

        grid_select.Add(wx.StaticText(self, label="Language:"), 0, wx.ALIGN_CENTER_VERTICAL)
        choice = wx.Choice(self, choices=["Python", "C++", "JavaScript", "Rust"])
        choice.SetSelection(0)
        grid_select.Add(choice, 1, wx.EXPAND)

        grid_select.Add(wx.StaticText(self, label="Quantity:"), 0, wx.ALIGN_CENTER_VERTICAL)
        spin = wx.SpinCtrl(self, min=0, max=100, initial=5)
        grid_select.Add(spin, 1, wx.EXPAND)

        select_sizer.Add(grid_select, 1, wx.EXPAND | wx.ALL, 10)

        # --- Checkboxes / radio buttons section ---
        toggle_sizer = _section(self, "Checkboxes && Radio Buttons")
        toggle_box = wx.BoxSizer(wx.VERTICAL)
        toggle_box.Add(wx.CheckBox(self, label="Subscribe to newsletter"), 0, wx.BOTTOM, 6)
        toggle_box.Add(wx.CheckBox(self, label="Remember me", style=wx.CHK_3STATE), 0, wx.BOTTOM, 10)

        toggle_box.Add(wx.StaticText(self, label="Preferred contact method:"), 0, wx.BOTTOM, 4)
        radio1 = wx.RadioButton(self, label="Email", style=wx.RB_GROUP)
        radio2 = wx.RadioButton(self, label="Phone")
        radio3 = wx.RadioButton(self, label="Mail")
        radio1.SetValue(True)
        toggle_box.Add(radio1, 0, wx.BOTTOM, 4)
        toggle_box.Add(radio2, 0, wx.BOTTOM, 4)
        toggle_box.Add(radio3, 0)
        toggle_sizer.Add(toggle_box, 1, wx.EXPAND | wx.ALL, 10)

        # --- Buttons section ---
        button_sizer = _section(self, "Buttons")
        button_box = wx.BoxSizer(wx.HORIZONTAL)
        button_box.Add(wx.Button(self, label="Submit"), 0, wx.RIGHT, 8)
        button_box.Add(wx.Button(self, label="Cancel"), 0, wx.RIGHT, 8)
        bmp = wx.ArtProvider.GetBitmap(wx.ART_TICK_MARK, wx.ART_BUTTON, (16, 16))
        bmp_btn = wx.Button(self, label=" Confirm")
        bmp_btn.SetBitmap(bmp)
        button_box.Add(bmp_btn, 0, wx.RIGHT, 8)
        button_box.Add(wx.ToggleButton(self, label="Toggle me"), 0)
        button_sizer.Add(button_box, 1, wx.ALL, 10)

        grid.Add(text_sizer, 1, wx.EXPAND)
        grid.Add(select_sizer, 1, wx.EXPAND)
        grid.Add(toggle_sizer, 1, wx.EXPAND)
        grid.Add(button_sizer, 1, wx.EXPAND)

        outer.Add(grid, 1, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(outer)


class AdvancedControlsPage(wx.Panel):
    """Sliders, pickers, lists and other richer wxWidgets controls."""

    def __init__(self, parent):
        super().__init__(parent)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(
            wx.StaticText(self, label="Advanced Controls"),
            0, wx.ALL, 12
        )

        grid = wx.FlexGridSizer(cols=2, gap=(16, 12))
        grid.AddGrowableCol(0, 1)
        grid.AddGrowableCol(1, 1)

        # --- Range / progress section ---
        range_sizer = _section(self, "Sliders && Progress")
        range_box = wx.BoxSizer(wx.VERTICAL)
        range_box.Add(wx.StaticText(self, label="Volume:"), 0, wx.BOTTOM, 4)
        range_box.Add(wx.Slider(self, value=40, minValue=0, maxValue=100,
                                 style=wx.SL_HORIZONTAL | wx.SL_LABELS), 0, wx.EXPAND | wx.BOTTOM, 10)
        range_box.Add(wx.StaticText(self, label="Progress:"), 0, wx.BOTTOM, 4)
        gauge = wx.Gauge(self, range=100)
        gauge.SetValue(65)
        range_box.Add(gauge, 0, wx.EXPAND | wx.BOTTOM, 10)
        range_box.Add(wx.SpinCtrlDouble(self, min=0.0, max=10.0, inc=0.1, initial=3.5), 0, wx.EXPAND)
        range_sizer.Add(range_box, 1, wx.EXPAND | wx.ALL, 10)

        # --- Date / pickers section ---
        picker_sizer = _section(self, "Pickers")
        picker_grid = wx.FlexGridSizer(cols=2, gap=(8, 8))
        picker_grid.AddGrowableCol(1, 1)

        picker_grid.Add(wx.StaticText(self, label="Date:"), 0, wx.ALIGN_CENTER_VERTICAL)
        picker_grid.Add(wx.adv.DatePickerCtrl(self), 1, wx.EXPAND)

        picker_grid.Add(wx.StaticText(self, label="Time:"), 0, wx.ALIGN_CENTER_VERTICAL)
        picker_grid.Add(wx.adv.TimePickerCtrl(self), 1, wx.EXPAND)

        picker_grid.Add(wx.StaticText(self, label="Colour:"), 0, wx.ALIGN_CENTER_VERTICAL)
        picker_grid.Add(wx.ColourPickerCtrl(self), 1, wx.EXPAND)

        picker_grid.Add(wx.StaticText(self, label="File:"), 0, wx.ALIGN_CENTER_VERTICAL)
        picker_grid.Add(wx.FilePickerCtrl(self), 1, wx.EXPAND)

        picker_grid.Add(wx.StaticText(self, label="Font:"), 0, wx.ALIGN_CENTER_VERTICAL)
        picker_grid.Add(wx.FontPickerCtrl(self), 1, wx.EXPAND)

        picker_sizer.Add(picker_grid, 1, wx.EXPAND | wx.ALL, 10)

        # --- List section ---
        list_sizer = _section(self, "Lists")
        list_box = wx.BoxSizer(wx.HORIZONTAL)

        listbox = wx.ListBox(self, choices=["Alpha", "Bravo", "Charlie", "Delta"])
        listbox.SetSelection(1)
        list_box.Add(listbox, 1, wx.EXPAND | wx.RIGHT, 10)

        checklist = wx.CheckListBox(self, choices=["Read", "Write", "Execute", "Delete"])
        checklist.Check(0)
        checklist.Check(2)
        list_box.Add(checklist, 1, wx.EXPAND)

        list_sizer.Add(list_box, 1, wx.EXPAND | wx.ALL, 10)

        # --- Report view section ---
        report_sizer = _section(self, "List Control (Report View)")
        report_box = wx.BoxSizer(wx.VERTICAL)
        list_ctrl = wx.ListCtrl(self, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        list_ctrl.InsertColumn(0, "Name", width=120)
        list_ctrl.InsertColumn(1, "Role", width=140)
        sample_rows = [("Ada Lovelace", "Mathematician"), ("Grace Hopper", "Computer Scientist")]
        for row, (name, role) in enumerate(sample_rows):
            list_ctrl.InsertItem(row, name)
            list_ctrl.SetItem(row, 1, role)
        report_box.Add(list_ctrl, 1, wx.EXPAND)
        report_sizer.Add(report_box, 1, wx.EXPAND | wx.ALL, 10)

        grid.Add(range_sizer, 1, wx.EXPAND)
        grid.Add(picker_sizer, 1, wx.EXPAND)
        grid.Add(list_sizer, 1, wx.EXPAND)
        grid.Add(report_sizer, 1, wx.EXPAND)

        outer.Add(grid, 1, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(outer)


class _CsvListCtrl(wx.ListCtrl):
    """A virtual report-mode list control backing the CSV table.

    Virtual mode means wx only ever asks for the rows currently on screen
    (via OnGetItemText), so the widget stays responsive no matter how many
    rows are loaded - only `view` (the currently visible/filtered/sorted
    index list) needs to fit in memory, not a rendered widget per row.
    """

    def __init__(self, parent):
        super().__init__(
            parent,
            style=wx.LC_REPORT | wx.LC_VIRTUAL | wx.LC_HRULES | wx.LC_VRULES | wx.BORDER_SUNKEN,
        )
        self.rows = []   # full dataset: list of tuples of str
        self.view = []   # indices into `rows`, after filtering/sorting

    def OnGetItemText(self, item, col):
        row = self.rows[self.view[item]]
        return row[col] if col < len(row) else ""


class TablePage(wx.Panel):
    """Open a CSV file and browse it in a sortable, filterable table.

    Built to cope with large files: parsing happens on a background thread
    so the UI never blocks while a file is read from disk, and the grid
    itself is a virtual wx.ListCtrl that only renders the rows currently
    scrolled into view rather than creating a widget per row.
    """

    COLUMN_MIN_WIDTH = 80    # px - keeps narrow columns (e.g. "id") readable
    COLUMN_MAX_WIDTH = 400   # px - keeps one huge cell from pushing every other column off-screen
    COLUMN_SAMPLE_ROWS = 200  # only measure the first N rows, so opening a huge file stays fast

    def __init__(self, parent):
        super().__init__(parent)

        self._headers = []
        self._filename = ""
        self._sort_col = -1
        self._sort_ascending = True
        self._filter_timer = wx.Timer(self)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(wx.StaticText(self, label="Table"), 0, wx.ALL, 12)

        toolbar = wx.BoxSizer(wx.HORIZONTAL)

        self._open_btn = wx.Button(self, label="Open CSV...")
        toolbar.Add(self._open_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)

        toolbar.Add(wx.StaticText(self, label="Filter:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)

        self._filter_col_choice = wx.Choice(self, choices=["All columns"])
        self._filter_col_choice.SetSelection(0)
        toolbar.Add(self._filter_col_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        self._filter_ctrl = wx.SearchCtrl(self, size=(220, -1))
        self._filter_ctrl.ShowCancelButton(True)
        toolbar.Add(self._filter_ctrl, 0, wx.ALIGN_CENTER_VERTICAL)

        toolbar.AddStretchSpacer()

        self._status_label = wx.StaticText(self, label="No file loaded")
        toolbar.Add(self._status_label, 0, wx.ALIGN_CENTER_VERTICAL)

        outer.Add(toolbar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._list = _CsvListCtrl(self)
        self._list.InsertColumn(0, "(no file loaded - click \"Open CSV...\")")
        self._list.SetItemCount(0)
        outer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)

        self._open_btn.Bind(wx.EVT_BUTTON, self._on_open)
        self._list.Bind(wx.EVT_LIST_COL_CLICK, self._on_col_click)
        self._filter_ctrl.Bind(wx.EVT_TEXT, self._on_filter_text)
        self._filter_ctrl.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_filter_cancel)
        self._filter_col_choice.Bind(wx.EVT_CHOICE, self._on_filter_column_changed)
        self.Bind(wx.EVT_TIMER, self._on_filter_timer, self._filter_timer)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def _on_open(self, event):
        with wx.FileDialog(
            self,
            "Open CSV file",
            wildcard="CSV files (*.csv)|*.csv|All files (*.*)|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()

        self._open_btn.Disable()
        self._status_label.SetLabel(f"Loading {os.path.basename(path)}...")
        threading.Thread(target=self._load_csv, args=(path,), daemon=True).start()

    def _load_csv(self, path):
        """Runs on a background thread - must not touch any wx widgets directly."""
        try:
            with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
                reader = csv.reader(f)
                try:
                    headers = next(reader)
                except StopIteration:
                    wx.CallAfter(self._on_load_error, "The file is empty.")
                    return
                rows = [tuple(row) for row in reader]
        except OSError as exc:
            wx.CallAfter(self._on_load_error, str(exc))
            return

        wx.CallAfter(self._on_csv_loaded, path, headers, rows)

    def _on_load_error(self, message):
        self._open_btn.Enable()
        self._status_label.SetLabel("No file loaded")
        wx.MessageBox(f"Could not load the CSV file:\n\n{message}", "Error", wx.OK | wx.ICON_ERROR, self)

    def _on_csv_loaded(self, path, headers, rows):
        self._headers = headers
        self._filename = os.path.basename(path)
        self._sort_col = -1
        self._sort_ascending = True
        self._filter_ctrl.SetValue("")

        self._filter_col_choice.Set(["All columns"] + list(headers))
        self._filter_col_choice.SetSelection(0)

        self._list.SetItemCount(0)
        while self._list.GetColumnCount() > 0:
            self._list.DeleteColumn(0)
        for i, header in enumerate(headers):
            self._list.InsertColumn(i, header)

        self._list.rows = rows
        self._apply_filter()
        self._autosize_columns(headers, rows)

        self._open_btn.Enable()

    def _autosize_columns(self, headers, rows):
        """Give every column a sane initial width so none is squeezed unreadably
        thin - the user can still drag column-header edges to resize further."""
        sample = rows[: self.COLUMN_SAMPLE_ROWS]
        for i, header in enumerate(headers):
            width = self._list.GetTextExtent(header)[0] + 24
            for row in sample:
                if i < len(row):
                    cell_width = self._list.GetTextExtent(row[i])[0] + 16
                    width = max(width, cell_width)
            width = max(self.COLUMN_MIN_WIDTH, min(width, self.COLUMN_MAX_WIDTH))
            self._list.SetColumnWidth(i, width)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------
    def _on_filter_text(self, event):
        # Debounce: wait for a short pause in typing before re-filtering, so a
        # large dataset isn't re-scanned on every single keystroke.
        self._filter_timer.Start(300, wx.TIMER_ONE_SHOT)

    def _on_filter_timer(self, event):
        self._apply_filter()

    def _on_filter_cancel(self, event):
        self._filter_ctrl.SetValue("")
        self._apply_filter()

    def _on_filter_column_changed(self, event):
        self._apply_filter()

    def _apply_filter(self):
        needle = self._filter_ctrl.GetValue().strip().lower()
        col_choice = self._filter_col_choice.GetSelection()  # 0 = "All columns"
        rows = self._list.rows

        if not needle:
            view = list(range(len(rows)))
        elif col_choice <= 0:
            view = [i for i, row in enumerate(rows) if any(needle in cell.lower() for cell in row)]
        else:
            idx = col_choice - 1
            view = [i for i, row in enumerate(rows) if idx < len(row) and needle in row[idx].lower()]

        if self._sort_col >= 0:
            view = self._sorted_view(view)

        self._list.view = view
        self._list.SetItemCount(len(view))
        self._list.Refresh()
        self._update_status()

    # ------------------------------------------------------------------
    # Sorting
    # ------------------------------------------------------------------
    def _on_col_click(self, event):
        col = event.GetColumn()
        if col == self._sort_col:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_col = col
            self._sort_ascending = True

        with wx.BusyCursor():
            self._list.view = self._sorted_view(self._list.view)
        self._list.Refresh()
        self._update_sort_indicators()

    def _sorted_view(self, view):
        col = self._sort_col
        rows = self._list.rows

        def key(i):
            row = rows[i]
            cell = row[col] if col < len(row) else ""
            try:
                return (0, float(cell))
            except ValueError:
                return (1, cell.lower())

        return sorted(view, key=key, reverse=not self._sort_ascending)

    def _update_sort_indicators(self):
        for i, header in enumerate(self._headers):
            label = header
            if i == self._sort_col:
                label += " ▲" if self._sort_ascending else " ▼"
            item = self._list.GetColumn(i)
            item.SetText(label)
            self._list.SetColumn(i, item)

    def _update_status(self):
        total = len(self._list.rows)
        shown = len(self._list.view)
        if shown == total:
            self._status_label.SetLabel(f"{self._filename} — {total:,} rows")
        else:
            self._status_label.SetLabel(f"{self._filename} — {shown:,} of {total:,} rows")


class DialogsPage(wx.Panel):
    """Buttons that open the standard stock wx dialogs."""

    def __init__(self, parent):
        super().__init__(parent)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(wx.StaticText(self, label="Dialogs"), 0, wx.ALL, 12)

        box = _section(self, "Common Dialogs")
        grid = wx.GridSizer(cols=2, gap=(10, 10))

        buttons = [
            ("Message Dialog...", self._show_message),
            ("Text Entry Dialog...", self._show_text_entry),
            ("Colour Dialog...", self._show_colour),
            ("File Open Dialog...", self._show_file_open),
            ("Directory Dialog...", self._show_dir),
            ("Font Dialog...", self._show_font),
        ]
        for label, handler in buttons:
            btn = wx.Button(self, label=label)
            btn.Bind(wx.EVT_BUTTON, handler)
            grid.Add(btn, 0, wx.EXPAND)

        box.Add(grid, 0, wx.ALL, 10)
        outer.Add(box, 0, wx.ALL, 12)
        self.SetSizer(outer)

    def _show_message(self, event):
        wx.MessageBox("This is a standard message dialog.", "Message Dialog",
                       wx.OK | wx.ICON_INFORMATION, self)

    def _show_text_entry(self, event):
        dlg = wx.TextEntryDialog(self, "Enter your name:", "Text Entry Dialog")
        dlg.ShowModal()
        dlg.Destroy()

    def _show_colour(self, event):
        dlg = wx.ColourDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def _show_file_open(self, event):
        dlg = wx.FileDialog(self, "Choose a file", style=wx.FD_OPEN)
        dlg.ShowModal()
        dlg.Destroy()

    def _show_dir(self, event):
        dlg = wx.DirDialog(self, "Choose a directory")
        dlg.ShowModal()
        dlg.Destroy()

    def _show_font(self, event):
        dlg = wx.FontDialog(self)
        dlg.ShowModal()
        dlg.Destroy()


class AboutPage(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)

        sizer = wx.BoxSizer(wx.VERTICAL)
        title = wx.StaticText(self, label="About This Demo")
        font = title.GetFont()
        font.SetPointSize(font.GetPointSize() + 6)
        font.MakeBold()
        title.SetFont(font)

        body = wx.StaticText(
            self,
            label=(
                "A minimal wxPython application skeleton showing:\n\n"
                "  - Sidebar navigation with icon buttons (app/sidebar.py)\n"
                "  - Page switching via wx.Simplebook (app/frame.py)\n"
                "  - A gallery of form widgets (app/pages.py)\n"
                "  - A nested menu bar with sub-menus (app/frame.py)\n\n"
                "Built with wxPython (https://wxpython.org)."
            ),
        )

        sizer.Add(title, 0, wx.ALL, 24)
        sizer.Add(body, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        self.SetSizer(sizer)
