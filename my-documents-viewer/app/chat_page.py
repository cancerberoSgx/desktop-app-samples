from typing import List, Optional

import wx

from .async_task import AsyncTaskRunner
from .chat_service import ChatService
from .conversation_repository import DEFAULT_CONVERSATION_TITLE, ConversationRepository
from .document_open import open_document_at_matches
from .document_viewer import DocumentViewerFrame
from .file_display import FILE_NAME_DISPLAY_DEFAULT, format_document_label
from .models import ChatMessage, Conversation, Profile, SearchResult
from .repositories import DocumentRepository, ProfileRepository

# Fixed wrap width for a message bubble's text - simpler and more robust than
# re-wrapping every bubble on every window resize (wx.StaticText.Wrap()
# mutates the label in place, so a correct re-wrap needs the original
# unwrapped text kept around separately); matches the general simplicity of
# this app's other pages, none of which do dynamic text reflow either.
BUBBLE_WRAP_WIDTH = 480


class ChatPage(wx.Panel):
    """Ask natural-language questions about the active profile's indexed
    documents, in named, resumable, multi-turn conversations - a
    retrieval-augmented layer on top of DocumentRepository.hybrid_search
    (see ChatService). A conversation is a sequence of message bubbles (own
    question, assistant answer); an assistant answer that drew on retrieved
    chunks lists every one of them as a clickable reference chip underneath,
    which opens the same DocumentViewerFrame the Search page uses, with that
    one chunk highlighted (see app/document_open.py).

    Left: the profile's conversation list (New/Rename/Delete), same
    list+toolbar shape as ProfilesPage. Right: the selected conversation's
    transcript plus a question input row."""

    def __init__(
        self,
        parent: wx.Window,
        document_repository: DocumentRepository,
        conversation_repository: ConversationRepository,
        chat_service: ChatService,
        profile_repository: ProfileRepository,
        profile_id: int,
        file_name_display: str = FILE_NAME_DISPLAY_DEFAULT,
    ) -> None:
        super().__init__(parent)
        self._documents = document_repository
        self._conversations = conversation_repository
        self._chat_service = chat_service
        self._profile_repository = profile_repository
        self._profile_id = profile_id
        self._file_name_display = file_name_display

        self._conversation_list: List[Conversation] = []
        self._current_conversation: Optional[Conversation] = None

        # Two runners, same reasoning as SearchPage's _async/_viewer_async
        # split: AsyncTaskRunner only runs one job at a time per instance, so
        # asking a question and loading a clicked reference's content need
        # separate runners to not block on each other.
        self._ask_async = AsyncTaskRunner(self)
        self._viewer_async = AsyncTaskRunner(self)
        self._viewer_frame: Optional[DocumentViewerFrame] = None

        self._build_ui()
        self.set_profile(profile_id)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(wx.StaticText(self, label="Chat"), 0, wx.ALL, 12)

        self._splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        self._splitter.SetMinimumPaneSize(180)
        outer.Add(self._splitter, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self.SetSizer(outer)

        left_panel = wx.Panel(self._splitter)
        left_sizer = wx.BoxSizer(wx.VERTICAL)
        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self._new_conv_btn = wx.Button(left_panel, label="New")
        self._rename_conv_btn = wx.Button(left_panel, label="Rename")
        self._delete_conv_btn = wx.Button(left_panel, label="Delete")
        toolbar.Add(self._new_conv_btn, 0, wx.RIGHT, 4)
        toolbar.Add(self._rename_conv_btn, 0, wx.RIGHT, 4)
        toolbar.Add(self._delete_conv_btn, 0)
        left_sizer.Add(toolbar, 0, wx.EXPAND | wx.ALL, 8)

        self._conv_list = wx.ListCtrl(left_panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        self._conv_list.InsertColumn(0, "Conversation", width=200)
        left_sizer.Add(self._conv_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        left_panel.SetSizer(left_sizer)

        right_panel = wx.Panel(self._splitter)
        right_sizer = wx.BoxSizer(wx.VERTICAL)

        self._unavailable_label = wx.StaticText(
            right_panel,
            label="Chat isn't configured for this profile - set a chat model on the Profiles screen.",
        )
        self._unavailable_label.SetForegroundColour(wx.Colour(170, 100, 0))
        right_sizer.Add(self._unavailable_label, 0, wx.EXPAND | wx.ALL, 8)
        self._unavailable_label.Hide()

        self._conversation_title_label = wx.StaticText(right_panel, label="")
        font = self._conversation_title_label.GetFont()
        font.MakeBold()
        self._conversation_title_label.SetFont(font)
        right_sizer.Add(self._conversation_title_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

        self._transcript = wx.ScrolledWindow(right_panel, style=wx.VSCROLL | wx.BORDER_SUNKEN)
        self._transcript.SetScrollRate(0, 12)
        self._transcript_sizer = wx.BoxSizer(wx.VERTICAL)
        self._transcript.SetSizer(self._transcript_sizer)
        right_sizer.Add(self._transcript, 1, wx.EXPAND | wx.ALL, 8)

        input_row = wx.BoxSizer(wx.HORIZONTAL)
        self._input_ctrl = wx.TextCtrl(right_panel, style=wx.TE_PROCESS_ENTER)
        input_row.Add(self._input_ctrl, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self._send_btn = wx.Button(right_panel, label="Send")
        input_row.Add(self._send_btn, 0)
        right_sizer.Add(input_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        right_panel.SetSizer(right_sizer)

        self._splitter.SplitVertically(left_panel, right_panel, 260)

        self._new_conv_btn.Bind(wx.EVT_BUTTON, self._on_new_conversation)
        self._rename_conv_btn.Bind(wx.EVT_BUTTON, self._on_rename_conversation)
        self._delete_conv_btn.Bind(wx.EVT_BUTTON, self._on_delete_conversation)
        self._conv_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_conversation_selected)
        self._conv_list.Bind(wx.EVT_LIST_ITEM_DESELECTED, lambda evt: self._update_button_states())
        self._input_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_send)
        self._send_btn.Bind(wx.EVT_BUTTON, self._on_send)

    # ------------------------------------------------------------------
    # Profile switching
    # ------------------------------------------------------------------
    def set_profile(self, profile_id: int) -> None:
        self._profile_id = profile_id
        self._current_conversation = None
        self._update_availability()
        self._reload_conversation_list()

    def set_file_name_display(self, mode: str) -> None:
        self._file_name_display = mode

    def _chat_configured(self, profile: Optional[Profile]) -> bool:
        if profile is None or not profile.chat_backend or not profile.chat_model:
            return False
        if profile.chat_backend == "openai":
            return bool(profile.openai_api_key)
        if profile.chat_backend == "gemini":
            return bool(profile.gemini_api_key)
        return False

    def _update_availability(self) -> None:
        configured = self._chat_configured(self._profile_repository.get(self._profile_id))
        self._unavailable_label.Show(not configured)
        self._new_conv_btn.Enable(configured)
        self._input_ctrl.Enable(configured)
        self._send_btn.Enable(configured)
        self.Layout()

    # ------------------------------------------------------------------
    # Conversation list
    # ------------------------------------------------------------------
    def _reload_conversation_list(self, select_id: Optional[int] = None) -> None:
        """Refresh the conversation list from the database and (re)select
        one row - `select_id` if given, else whichever conversation is
        currently loaded, else the first one. Selecting a row whose id
        matches the already-loaded conversation only refreshes its cached
        title/updated_at (see _on_conversation_selected) - it does not
        reload the transcript, so this is safe to call after every send
        without disrupting what's on screen."""
        self._conversation_list = self._conversations.list(self._profile_id)
        self._conv_list.DeleteAllItems()
        for row, conversation in enumerate(self._conversation_list):
            self._conv_list.InsertItem(row, conversation.title)

        target_id = select_id if select_id is not None else (
            self._current_conversation.id if self._current_conversation else None
        )
        target_row = next(
            (row for row, c in enumerate(self._conversation_list) if c.id == target_id),
            0 if self._conversation_list else None,
        )

        if target_row is not None:
            self._conv_list.Select(target_row)
            self._conv_list.Focus(target_row)
            self._conv_list.EnsureVisible(target_row)
        else:
            self._current_conversation = None
            self._clear_transcript()
            self._conversation_title_label.SetLabel("")
        self._update_button_states()

    def _on_conversation_selected(self, event: wx.ListEvent) -> None:
        index = event.GetIndex()
        if 0 <= index < len(self._conversation_list):
            conversation = self._conversation_list[index]
            if self._current_conversation and conversation.id == self._current_conversation.id:
                # Same conversation, just a metadata refresh (e.g. after
                # auto-titling) - don't reload the transcript under the
                # user's feet.
                self._current_conversation = conversation
                self._conversation_title_label.SetLabel(conversation.title)
            else:
                self._select_conversation(conversation)
        self._update_button_states()

    def _select_conversation(self, conversation: Conversation) -> None:
        self._current_conversation = conversation
        self._conversation_title_label.SetLabel(conversation.title)
        self._clear_transcript()
        for message in self._conversations.list_messages(conversation.id):
            self._append_bubble(message)
        self._scroll_to_bottom()

    def _update_button_states(self) -> None:
        has_selection = self._current_conversation is not None
        self._rename_conv_btn.Enable(has_selection)
        self._delete_conv_btn.Enable(has_selection)

    def _on_new_conversation(self, event: wx.CommandEvent) -> None:
        conversation = self._conversations.create(self._profile_id)
        self._reload_conversation_list(select_id=conversation.id)

    def _on_rename_conversation(self, event: wx.CommandEvent) -> None:
        if self._current_conversation is None:
            return
        dlg = wx.TextEntryDialog(self, "Conversation name:", "Rename Conversation", value=self._current_conversation.title)
        if dlg.ShowModal() == wx.ID_OK:
            title = dlg.GetValue().strip()
            if title:
                self._conversations.rename(self._current_conversation.id, title)
                self._reload_conversation_list()
        dlg.Destroy()

    def _on_delete_conversation(self, event: wx.CommandEvent) -> None:
        if self._current_conversation is None:
            return
        confirm = wx.MessageBox(
            f'Delete conversation "{self._current_conversation.title}"? This cannot be undone.',
            "Confirm delete",
            wx.YES_NO | wx.ICON_WARNING,
            self,
        )
        if confirm == wx.YES:
            self._conversations.delete(self._current_conversation.id)
            self._current_conversation = None
            self._reload_conversation_list()

    def _maybe_auto_title(self, conversation_id: int, question: str) -> None:
        """First message sent in a still-default-titled conversation renames
        it to a prefix of that question - purely local truncation, no extra
        LLM call. Only fires while the title is still the create()-time
        default, so a user's own rename always wins."""
        conversation = self._conversations.get(conversation_id)
        if conversation and conversation.title == DEFAULT_CONVERSATION_TITLE:
            title = question if len(question) <= 60 else question[:60].rstrip() + "…"
            self._conversations.rename(conversation_id, title)
        self._reload_conversation_list()

    # ------------------------------------------------------------------
    # Asking a question
    # ------------------------------------------------------------------
    def _on_send(self, event: wx.CommandEvent) -> None:
        if self._current_conversation is None or self._ask_async.is_busy():
            return
        question = self._input_ctrl.GetValue().strip()
        if not question:
            return
        profile = self._profile_repository.get(self._profile_id)
        if not self._chat_configured(profile):
            return

        conversation_id = self._current_conversation.id
        self._input_ctrl.SetValue("")
        # Optimistic UI: show the question immediately - ChatService.ask()
        # persists it (and the answer) to the database itself, so nothing
        # here writes to conversation_repository directly.
        self._append_bubble(ChatMessage(id=None, conversation_id=conversation_id, role="user", content=question))
        thinking_panel = self._append_thinking_bubble()
        self._scroll_to_bottom()

        def on_success(answer: ChatMessage) -> None:
            self._remove_bubble(thinking_panel)
            self._append_bubble(answer)
            self._scroll_to_bottom()
            self._maybe_auto_title(conversation_id, question)

        def on_error(exc: Exception) -> None:
            self._remove_bubble(thinking_panel)
            wx.MessageBox(f"Chat failed:\n\n{exc}", "Chat error", wx.OK | wx.ICON_ERROR, self)

        self._ask_async.run(
            work=lambda: self._chat_service.ask(profile, conversation_id, question),
            on_success=on_success,
            on_error=on_error,
            disable=[self._send_btn, self._input_ctrl],
        )

    # ------------------------------------------------------------------
    # Transcript rendering
    # ------------------------------------------------------------------
    def _clear_transcript(self) -> None:
        self._transcript_sizer.Clear(delete_windows=True)
        self._transcript.Layout()
        self._transcript.FitInside()

    def _scroll_to_bottom(self) -> None:
        self._transcript.Layout()
        self._transcript.FitInside()
        wx.CallAfter(self._transcript.Scroll, 0, self._transcript.GetScrollRange(wx.VERTICAL))

    def _append_bubble(self, message: ChatMessage) -> wx.Panel:
        panel = wx.Panel(self._transcript)
        sizer = wx.BoxSizer(wx.VERTICAL)

        role_label = wx.StaticText(panel, label="You" if message.role == "user" else "Assistant")
        font = role_label.GetFont()
        font.MakeBold()
        role_label.SetFont(font)
        role_label.SetForegroundColour(
            wx.Colour(40, 90, 200) if message.role == "user" else wx.Colour(50, 140, 90)
        )
        sizer.Add(role_label, 0, wx.BOTTOM, 2)

        content_label = wx.StaticText(panel, label=message.content)
        content_label.Wrap(BUBBLE_WRAP_WIDTH)
        sizer.Add(content_label, 0, wx.EXPAND)

        if message.references:
            refs_sizer = wx.WrapSizer(wx.HORIZONTAL)
            for result in message.references:
                chip = wx.Button(panel, label=self._reference_chip_label(result), style=wx.BU_EXACTFIT)
                chip.SetToolTip(result.snippet)
                chip.Bind(wx.EVT_BUTTON, lambda evt, r=result: self._open_reference(r))
                refs_sizer.Add(chip, 0, wx.ALL, 2)
            sizer.Add(refs_sizer, 0, wx.EXPAND | wx.TOP, 4)

        panel.SetSizer(sizer)
        self._transcript_sizer.Add(panel, 0, wx.EXPAND | wx.ALL, 8)
        self._transcript.Layout()
        self._transcript.FitInside()
        return panel

    def _append_thinking_bubble(self) -> wx.Panel:
        panel = wx.Panel(self._transcript)
        sizer = wx.BoxSizer(wx.VERTICAL)
        role_label = wx.StaticText(panel, label="Assistant")
        font = role_label.GetFont()
        font.MakeBold()
        role_label.SetFont(font)
        sizer.Add(role_label, 0, wx.BOTTOM, 2)
        sizer.Add(wx.StaticText(panel, label="Thinking..."), 0)
        panel.SetSizer(sizer)
        self._transcript_sizer.Add(panel, 0, wx.EXPAND | wx.ALL, 8)
        self._transcript.Layout()
        self._transcript.FitInside()
        return panel

    def _remove_bubble(self, panel: wx.Panel) -> None:
        self._transcript_sizer.Detach(panel)
        panel.Destroy()
        self._transcript.Layout()
        self._transcript.FitInside()

    def _reference_chip_label(self, result: SearchResult) -> str:
        document = self._documents.get(result.document_id)
        if document is None:
            return "(removed document)"
        container = self._documents.get(document.parent_document_id) if document.parent_document_id else None
        return format_document_label(document, container, self._file_name_display)

    # ------------------------------------------------------------------
    # Opening a reference - same DocumentViewerFrame the Search page uses,
    # with just this one cited chunk highlighted (see app/document_open.py).
    # ------------------------------------------------------------------
    def _get_viewer_frame(self) -> DocumentViewerFrame:
        if self._viewer_frame is None:
            self._viewer_frame = DocumentViewerFrame(self)
            self._viewer_frame.Bind(wx.EVT_CLOSE, self._on_viewer_closed)
        return self._viewer_frame

    def _on_viewer_closed(self, event: wx.CloseEvent) -> None:
        self._viewer_frame = None
        event.Skip()

    def _open_reference(self, result: SearchResult) -> None:
        open_document_at_matches(
            self._get_viewer_frame(),
            self._documents,
            self._viewer_async,
            result.document_id,
            [result],
            self._file_name_display,
        )
