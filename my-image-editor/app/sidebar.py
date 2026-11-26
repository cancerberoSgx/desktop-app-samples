"""Left tool sidebar: tool buttons on top, then the style of new / selected
shapes (colour, fill, line width, font). Collapsible to an icon-only strip
(View > Collapse Sidebar, Ctrl+B, or its « button); the state is remembered.

Style edits go through `Editor.set_style_value`, which sets the style for
new shapes and applies it to the selected shapes it fits (one undo step per
field). Rows that don't apply to the selection (or to the active drawing
tool) are disabled.
"""
from typing import Callable, Dict, Set

import wx

from app import icons
from app.editor import fonts
from app.editor.editor import Editor
from app.editor.shapes import TextShape
from app.editor.tools import TOOL_CLASSES, ShapeTool, TextTool

EXPANDED_WIDTH = 210
ALL_FIELDS = {"color", "fill", "width", "font_family", "font_size", "bold", "italic"}


def _to_wx(rgba) -> wx.Colour:
    return wx.Colour(*rgba)


def _from_wx(colour: wx.Colour):
    return (colour.Red(), colour.Green(), colour.Blue(), colour.Alpha())


class Sidebar(wx.Panel):
    def __init__(self, parent: wx.Window, editor: Editor, collapsed: bool,
                 on_collapsed_changed: Callable[[bool], None], on_done: Callable[[], None]) -> None:
        super().__init__(parent)
        self.editor = editor
        self.collapsed = collapsed
        self._on_collapsed_changed = on_collapsed_changed
        self._on_done = on_done  # give the keyboard back to the canvas
        self._updating = False

        self._toggle = wx.BitmapButton(self, style=wx.BORDER_NONE)
        self._toggle.Bind(wx.EVT_BUTTON, lambda e: self.set_collapsed(not self.collapsed))

        self._tool_buttons: Dict[str, wx.ToggleButton] = {}
        tools_sizer = wx.BoxSizer(wx.VERTICAL)
        for cls in TOOL_CLASSES:
            button = wx.ToggleButton(self, label=cls.LABEL, style=wx.BU_LEFT)
            button.SetToolTip(f"{cls.LABEL} ({cls.KEY})")
            button.Bind(wx.EVT_TOGGLEBUTTON, lambda e, name=cls.NAME: self._on_tool(name))
            self._tool_buttons[cls.NAME] = button
            tools_sizer.Add(button, 0, wx.EXPAND | wx.BOTTOM, 2)

        self._props = self._build_properties()

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self._toggle, 0, wx.ALIGN_RIGHT | wx.ALL, 4)
        sizer.Add(tools_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        sizer.Add(self._props, 1, wx.EXPAND | wx.TOP, 8)
        self.SetSizer(sizer)

        self.refresh_icons()
        self.set_collapsed(collapsed, notify=False)
        editor.subscribe(self._on_editor)
        self._sync_tool()
        self._sync_style()

    # ------------------------------------------------------------ build
    def _build_properties(self) -> wx.Panel:
        panel = wx.Panel(self)
        grey = wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT)

        def heading(text: str) -> wx.StaticText:
            label = wx.StaticText(panel, label=text)
            label.SetForegroundColour(grey)
            return label

        self._color = wx.ColourPickerCtrl(panel, style=wx.CLRP_SHOW_ALPHA)
        self._color.SetToolTip("Line / text colour (with opacity)")
        self._fill_on = wx.CheckBox(panel, label="Fill")
        self._fill = wx.ColourPickerCtrl(panel, colour=wx.Colour(255, 235, 59), style=wx.CLRP_SHOW_ALPHA)
        self._fill.SetToolTip("Fill colour of rectangles and ellipses")
        self._width = wx.SpinCtrl(panel, min=1, max=200, initial=4, size=(70, -1))
        self._family = wx.Choice(panel, choices=list(fonts.FAMILIES))
        self._size = wx.SpinCtrl(panel, min=4, max=1000, initial=36, size=(70, -1))
        self._bold = wx.CheckBox(panel, label="Bold")
        self._italic = wx.CheckBox(panel, label="Italic")

        self._rows: Dict[str, list] = {}
        grid = wx.FlexGridSizer(cols=2, vgap=6, hgap=8)
        grid.AddGrowableCol(1)

        def row(field: str, label: wx.Window, control: wx.Window) -> None:
            grid.Add(label, 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(control, 1, wx.EXPAND)
            self._rows[field] = [label, control]

        row("color", wx.StaticText(panel, label="Colour"), self._color)
        row("fill", self._fill_on, self._fill)
        row("width", wx.StaticText(panel, label="Line width"), self._width)
        row("font_family", wx.StaticText(panel, label="Font"), self._family)
        row("font_size", wx.StaticText(panel, label="Size"), self._size)
        styles = wx.BoxSizer(wx.HORIZONTAL)
        styles.Add(self._bold, 0, wx.RIGHT, 10)
        styles.Add(self._italic, 0)
        grid.AddSpacer(0)
        grid.Add(styles)
        self._rows["bold"] = [self._bold]
        self._rows["italic"] = [self._italic]

        # Crop: only while the crop tool is active.
        self._crop_box = wx.Panel(panel)
        self._crop_info = wx.StaticText(self._crop_box, label="")
        crop_apply = wx.Button(self._crop_box, label="Crop")
        crop_cancel = wx.Button(self._crop_box, label="Clear")
        crop_apply.Bind(wx.EVT_BUTTON, lambda e: (self.editor.apply_crop(), self._on_done()))
        crop_cancel.Bind(wx.EVT_BUTTON, lambda e: (self.editor.crop_tool.cancel(),
                                                   self.editor.notify("overlay"), self._on_done()))
        self._crop_buttons = (crop_apply, crop_cancel)
        crop_buttons = wx.BoxSizer(wx.HORIZONTAL)
        crop_buttons.Add(crop_apply, 1, wx.RIGHT, 4)
        crop_buttons.Add(crop_cancel, 1)
        crop_sizer = wx.BoxSizer(wx.VERTICAL)
        crop_sizer.Add(self._crop_info, 0, wx.BOTTOM, 4)
        crop_sizer.Add(crop_buttons, 0, wx.EXPAND)
        self._crop_box.SetSizer(crop_sizer)

        self._hint = wx.StaticText(panel, label="")
        self._hint.SetForegroundColour(grey)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(heading("Style"), 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        sizer.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        sizer.Add(self._crop_box, 0, wx.EXPAND | wx.ALL, 6)
        sizer.Add(self._hint, 0, wx.EXPAND | wx.ALL, 6)
        panel.SetSizer(sizer)

        self._color.Bind(wx.EVT_COLOURPICKER_CHANGED,
                         lambda e: self._set("color", _from_wx(self._color.GetColour())))
        self._fill_on.Bind(wx.EVT_CHECKBOX, lambda e: self._set_fill())
        self._fill.Bind(wx.EVT_COLOURPICKER_CHANGED, lambda e: self._set_fill(force_on=True))
        self._width.Bind(wx.EVT_SPINCTRL, lambda e: self._set("width", float(self._width.GetValue())))
        self._family.Bind(wx.EVT_CHOICE, lambda e: self._set("font_family", fonts.FAMILIES[self._family.GetSelection()]))
        self._size.Bind(wx.EVT_SPINCTRL, lambda e: self._set("font_size", self._size.GetValue()))
        self._bold.Bind(wx.EVT_CHECKBOX, lambda e: self._set("bold", self._bold.GetValue()))
        self._italic.Bind(wx.EVT_CHECKBOX, lambda e: self._set("italic", self._italic.GetValue()))
        return panel

    # ------------------------------------------------------------ collapse / theme
    def refresh_icons(self) -> None:
        colour = wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNTEXT)
        scale = self.GetContentScaleFactor()
        for name, button in self._tool_buttons.items():
            button.SetBitmap(icons.tool_icon(name, colour, scale))
        self._toggle.SetBitmap(icons.tool_icon("expand" if self.collapsed else "collapse", colour, scale, 16))

    def set_collapsed(self, collapsed: bool, notify: bool = True) -> None:
        self.collapsed = collapsed
        for cls in TOOL_CLASSES:
            self._tool_buttons[cls.NAME].SetLabel("" if collapsed else cls.LABEL)
        self._props.Show(not collapsed)
        self._toggle.SetToolTip("Expand sidebar (Ctrl+B)" if collapsed else "Collapse sidebar (Ctrl+B)")
        self.refresh_icons()
        if collapsed:
            self.SetMinSize((-1, -1))
            self.InvalidateBestSize()
            self.SetMinSize((self.GetBestSize().width, -1))
        else:
            self.SetMinSize((EXPANDED_WIDTH, -1))
        self.Layout()
        if self.GetParent():
            self.GetParent().Layout()
        if notify:
            self._on_collapsed_changed(collapsed)

    # ------------------------------------------------------------ editor -> controls
    def _on_editor(self, what: str) -> None:
        if what == "tool":
            self._sync_tool()
            self._sync_enabled()
        elif what == "style":
            self._sync_style()
        elif what in ("selection", "document"):
            self._sync_enabled()
        if what in ("overlay", "tool", "document"):
            self._sync_crop()

    def _sync_tool(self) -> None:
        for name, button in self._tool_buttons.items():
            button.SetValue(name == self.editor.tool.NAME)
        self._hint.SetLabel(self.editor.tool.HINT)
        self._hint.Wrap(EXPANDED_WIDTH - 16)
        self._sync_crop()
        self._props.Layout()

    def _sync_crop(self) -> None:
        crop = self.editor.crop_tool
        active = self.editor.tool is crop and self.editor.doc is not None
        if active:
            rect = crop.rect
            self._crop_info.SetLabel(f"Keep {rect[2] - rect[0]} × {rect[3] - rect[1]} px" if rect
                                     else "Drag on the image to choose the area to keep.")
            self._crop_info.Wrap(EXPANDED_WIDTH - 16)
            for button in self._crop_buttons:
                button.Enable(rect is not None)
        if self._crop_box.IsShown() != active:
            self._crop_box.Show(active)
            self._props.Layout()

    def _sync_style(self) -> None:
        s = self.editor.style
        self._updating = True
        try:
            self._color.SetColour(_to_wx(s.color))
            self._fill_on.SetValue(s.fill is not None)
            if s.fill is not None:
                self._fill.SetColour(_to_wx(s.fill))
            self._width.SetValue(int(round(s.width)))
            self._family.SetSelection(fonts.FAMILIES.index(fonts.normalize_family(s.font_family)))
            self._size.SetValue(s.font_size)
            self._bold.SetValue(s.bold)
            self._italic.SetValue(s.italic)
        finally:
            self._updating = False

    def _relevant_fields(self) -> Set[str]:
        selected = self.editor.selected_shapes()
        if selected:
            return set().union(*(shape.STYLE_FIELDS for shape in selected))
        tool = self.editor.tool
        if isinstance(tool, ShapeTool):
            return set(tool.SHAPE_CLASS.STYLE_FIELDS)
        if isinstance(tool, TextTool):
            return set(TextShape.STYLE_FIELDS)
        return set(ALL_FIELDS)

    def _sync_enabled(self) -> None:
        relevant = self._relevant_fields()
        for field, widgets in self._rows.items():
            for widget in widgets:
                widget.Enable(field in relevant)
        self._fill.Enable("fill" in relevant and self._fill_on.GetValue())

    # ------------------------------------------------------------ controls -> editor
    def _on_tool(self, name: str) -> None:
        self.editor.set_tool(name)
        self._sync_tool()  # a toggle button can't be un-toggled into "no tool"
        self._on_done()

    def _set(self, name: str, value) -> None:
        if not self._updating:
            self.editor.set_style_value(name, value)

    def _set_fill(self, force_on: bool = False) -> None:
        if self._updating:
            return
        if force_on:
            self._fill_on.SetValue(True)
        on = self._fill_on.GetValue()
        self._fill.Enable(on)
        self._set("fill", _from_wx(self._fill.GetColour()) if on else None)

