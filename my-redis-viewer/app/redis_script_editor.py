import wx
import wx.stc as stc

STYLE_DEFAULT = 0
STYLE_COMMAND = 1
STYLE_STRING = 2
STYLE_COMMENT = 3


class RedisScriptEditor(stc.StyledTextCtrl):
    """A source-code editor for redis-cli-style command text: line
    numbers, a monospace font, and lightweight syntax highlighting
    (the first token of each line as the command, quoted strings, and
    "#" comment lines) driven by a manual container lexer, since Scintilla
    has no built-in Redis lexer. Used by ScriptsView, but standalone -
    nothing here depends on scripts, datasources, or execution."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent, style=wx.BORDER_SUNKEN)

        self.SetLexer(stc.STC_LEX_CONTAINER)
        self.SetMarginType(0, stc.STC_MARGIN_NUMBER)
        self.SetMarginWidth(0, 40)
        self.SetTabWidth(4)
        self.SetUseTabs(False)

        font = wx.Font(wx.FontInfo(10).Family(wx.FONTFAMILY_TELETYPE))
        self.StyleSetFont(stc.STC_STYLE_DEFAULT, font)
        self.StyleClearAll()
        self.StyleSetForeground(STYLE_COMMAND, wx.Colour(0, 80, 200))
        self.StyleSetBold(STYLE_COMMAND, True)
        self.StyleSetForeground(STYLE_STRING, wx.Colour(150, 60, 0))
        self.StyleSetForeground(STYLE_COMMENT, wx.Colour(120, 120, 120))
        self.StyleSetItalic(STYLE_COMMENT, True)

        self.Bind(stc.EVT_STC_STYLENEEDED, self._on_style_needed)

    def _on_style_needed(self, event: stc.StyledTextEvent) -> None:
        start = self.GetEndStyled()
        line = self.LineFromPosition(start)
        start = self.PositionFromLine(line)
        end = event.GetPosition()

        self.StartStyling(start)
        text = self.GetTextRange(start, end)

        for line_text in text.splitlines(keepends=True):
            stripped = line_text.strip()
            if stripped.startswith("#"):
                self.SetStyling(len(line_text.encode("utf-8")), STYLE_COMMENT)
                continue
            self._style_line(line_text)

    def _style_line(self, line_text: str) -> None:
        idx = 0
        length = len(line_text)
        seen_command = False
        while idx < length:
            ch = line_text[idx]
            if ch in ("'", '"'):
                end = idx + 1
                while end < length and line_text[end] != ch:
                    end += 1
                end = min(end + 1, length)
                token = line_text[idx:end]
                self.SetStyling(len(token.encode("utf-8")), STYLE_STRING)
                seen_command = True
                idx = end
                continue
            if ch.isspace():
                self.SetStyling(len(ch.encode("utf-8")), STYLE_DEFAULT)
                idx += 1
                continue
            end = idx
            while end < length and not line_text[end].isspace() and line_text[end] not in ("'", '"'):
                end += 1
            token = line_text[idx:end]
            style = STYLE_COMMAND if not seen_command else STYLE_DEFAULT
            self.SetStyling(len(token.encode("utf-8")), style)
            seen_command = True
            idx = end
