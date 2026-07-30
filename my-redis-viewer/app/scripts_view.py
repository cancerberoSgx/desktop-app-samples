from typing import List, Optional

import wx
import wx.stc as stc

from .async_task import AsyncTaskRunner
from .models import Datasource, Script
from .redis_script_editor import RedisScriptEditor
from .repositories import DatasourceRepository, ScriptRepository


class ScriptsView(wx.Panel):
    """Create/save/update/delete/list named Redis scripts for the active
    datasource, and run one (its full text, or just the current
    selection) as a sequence of raw redis-cli-style commands. Left: the
    saved-script list. Right: name field, RedisScriptEditor, Execute
    buttons, and a read-only output pane."""

    def __init__(
        self,
        parent: wx.Window,
        repository: DatasourceRepository,
        script_repository: ScriptRepository,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._script_repository = script_repository
        self._datasource: Optional[Datasource] = None
        self._scripts: List[Script] = []
        self._current_script: Optional[Script] = None
        self._dirty = False
        self._async = AsyncTaskRunner(self)

        outer = wx.BoxSizer(wx.HORIZONTAL)

        left = wx.BoxSizer(wx.VERTICAL)
        list_toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self._new_btn = wx.Button(self, label="New")
        self._delete_btn = wx.Button(self, label="Delete")
        list_toolbar.Add(self._new_btn, 0, wx.RIGHT, 8)
        list_toolbar.Add(self._delete_btn, 0)
        left.Add(list_toolbar, 0, wx.EXPAND | wx.ALL, 12)

        self._list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        self._list.InsertColumn(0, "Name", width=200)
        left.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        outer.Add(left, 0, wx.EXPAND)

        right = wx.BoxSizer(wx.VERTICAL)
        name_row = wx.BoxSizer(wx.HORIZONTAL)
        name_row.Add(wx.StaticText(self, label="Name:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._name_ctrl = wx.TextCtrl(self)
        name_row.Add(self._name_ctrl, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self._save_btn = wx.Button(self, label="Save")
        name_row.Add(self._save_btn, 0)
        right.Add(name_row, 0, wx.EXPAND | wx.TOP | wx.RIGHT | wx.BOTTOM, 12)

        self._editor = RedisScriptEditor(self)
        right.Add(self._editor, 2, wx.EXPAND | wx.RIGHT | wx.BOTTOM, 12)

        exec_toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self._execute_all_btn = wx.Button(self, label="Execute All")
        self._execute_selection_btn = wx.Button(self, label="Execute Selection")
        exec_toolbar.Add(self._execute_all_btn, 0, wx.RIGHT, 8)
        exec_toolbar.Add(self._execute_selection_btn, 0)
        right.Add(exec_toolbar, 0, wx.EXPAND | wx.RIGHT | wx.BOTTOM, 12)

        right.Add(wx.StaticText(self, label="Output:"), 0, wx.RIGHT | wx.BOTTOM, 4)
        self._output = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP | wx.BORDER_SUNKEN,
        )
        self._output.SetFont(wx.Font(wx.FontInfo().Family(wx.FONTFAMILY_TELETYPE)))
        right.Add(self._output, 1, wx.EXPAND | wx.RIGHT | wx.BOTTOM, 12)

        outer.Add(right, 1, wx.EXPAND)

        self.SetSizer(outer)

        self._new_btn.Bind(wx.EVT_BUTTON, self._on_new)
        self._delete_btn.Bind(wx.EVT_BUTTON, self._on_delete)
        self._save_btn.Bind(wx.EVT_BUTTON, self._on_save)
        self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_list_select)
        self._name_ctrl.Bind(wx.EVT_TEXT, self._mark_dirty)
        self._editor.Bind(stc.EVT_STC_CHANGE, self._mark_dirty)
        self._execute_all_btn.Bind(wx.EVT_BUTTON, self._on_execute_all)
        self._execute_selection_btn.Bind(wx.EVT_BUTTON, self._on_execute_selection)

        self._update_button_states()

    # ------------------------------------------------------------------
    # Datasource lifecycle
    # ------------------------------------------------------------------
    def set_datasource(self, datasource: Datasource) -> None:
        self._datasource = datasource
        self.reload()

    def clear(self) -> None:
        self._datasource = None
        self._scripts = []
        self._list.DeleteAllItems()
        self._load_script(None)

    def reload(self) -> None:
        if self._datasource is None:
            return
        self._scripts = self._script_repository.list(self._datasource.id)
        self._list.DeleteAllItems()
        for row, script in enumerate(self._scripts):
            self._list.InsertItem(row, script.name)
        self._update_button_states()

    # ------------------------------------------------------------------
    # Selection / editing
    # ------------------------------------------------------------------
    def _selected_script(self) -> Optional[Script]:
        index = self._list.GetFirstSelected()
        return self._scripts[index] if index != -1 else None

    def _select_script_in_list(self, script: Optional[Script]) -> None:
        if script is None:
            return
        for row, candidate in enumerate(self._scripts):
            if candidate.id == script.id:
                self._list.SetItemState(row, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)
                break

    def _on_list_select(self, event: wx.ListEvent) -> None:
        if self._dirty and not self._confirm_discard():
            self._select_script_in_list(self._current_script)
            return
        self._load_script(self._selected_script())

    def _load_script(self, script: Optional[Script]) -> None:
        self._current_script = script
        self._name_ctrl.ChangeValue(script.name if script else "")
        self._editor.SetText(script.text if script else "")
        self._output.SetValue("")
        self._dirty = False
        self._update_button_states()

    def _confirm_discard(self) -> bool:
        answer = wx.MessageBox(
            "Discard unsaved changes to this script?",
            "Unsaved changes",
            wx.YES_NO | wx.ICON_WARNING,
            self,
        )
        return answer == wx.YES

    def _mark_dirty(self, event: wx.Event) -> None:
        self._dirty = True
        event.Skip()

    def _update_button_states(self) -> None:
        has_datasource = self._datasource is not None
        has_selection = self._selected_script() is not None
        self._new_btn.Enable(has_datasource)
        self._delete_btn.Enable(has_selection)
        self._save_btn.Enable(has_datasource)
        self._execute_all_btn.Enable(has_datasource)
        self._execute_selection_btn.Enable(has_datasource)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def _on_new(self, event: wx.CommandEvent) -> None:
        if self._dirty and not self._confirm_discard():
            return
        self._list.SetItemState(-1, 0, wx.LIST_STATE_SELECTED)
        self._load_script(None)
        self._name_ctrl.SetFocus()

    def _on_delete(self, event: wx.CommandEvent) -> None:
        script = self._selected_script()
        if script is None:
            return
        confirm = wx.MessageBox(
            f'Delete script "{script.name}"?',
            "Confirm delete",
            wx.YES_NO | wx.ICON_WARNING,
            self,
        )
        if confirm == wx.YES:
            self._script_repository.delete(script.id)
            self.reload()
            self._load_script(None)

    def _on_save(self, event: wx.CommandEvent) -> None:
        if self._datasource is None:
            return
        name = self._name_ctrl.GetValue().strip()
        if not name:
            wx.MessageBox("Script name is required.", "Cannot save", wx.OK | wx.ICON_WARNING, self)
            return
        text = self._editor.GetText()
        if self._current_script is None:
            self._current_script = self._script_repository.create(
                Script(id=None, name=name, datasource_id=self._datasource.id, text=text)
            )
        else:
            self._current_script.name = name
            self._current_script.text = text
            self._script_repository.update(self._current_script)
        self._dirty = False
        saved = self._current_script
        self.reload()
        self._select_script_in_list(saved)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def _on_execute_all(self, event: wx.CommandEvent) -> None:
        self._execute(self._editor.GetText())

    def _on_execute_selection(self, event: wx.CommandEvent) -> None:
        selection = self._editor.GetSelectedText()
        if not selection.strip():
            wx.MessageBox("No text selected.", "Nothing to execute", wx.OK | wx.ICON_INFORMATION, self)
            return
        self._execute(selection)

    def _execute(self, text: str) -> None:
        if self._datasource is None or not text.strip():
            return
        datasource = self._datasource
        self._output.SetValue("Executing...")

        def on_success(results) -> None:
            self._output.SetValue(self._format_results(results) if results else "No commands found.")

        def on_error(exc: Exception) -> None:
            self._output.SetValue(f"Execution failed:\n\n{exc}")

        self._async.run(
            work=lambda: self._repository.execute_script(datasource, text),
            on_success=on_success,
            on_error=on_error,
            disable=[self._execute_all_btn, self._execute_selection_btn],
        )

    @staticmethod
    def _format_results(results) -> str:
        blocks = []
        for result in results:
            marker = "ERROR" if result.is_error else "->"
            blocks.append(f"[line {result.line_number}] {result.command_text}\n{marker} {result.output_text}")
        return "\n\n".join(blocks)
