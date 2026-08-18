from typing import List, Optional

import wx

from .chunking import CHUNK_SIZE
from .embeddings.registry import (
    BACKEND_LABELS,
    DEFAULT_MODEL,
    EmbeddingModelInfo,
    max_chars_for,
    models_for_backend,
)
from .models import Profile


class ProfileDialog(wx.Dialog):
    """Create/edit form for a Profile: its name (the document "kind", e.g.
    "History"/"Development"/"Contracts") plus its embedding backend, model
    and (implied by the model) vector dimension, and the OpenAI/Gemini API
    keys needed if a hosted backend is chosen."""

    def __init__(self, parent: wx.Window, profile: Optional[Profile] = None) -> None:
        title = "Edit Profile" if profile else "New Profile"
        super().__init__(parent, title=title, size=(460, 440))
        self._profile = profile
        self._result: Optional[Profile] = None
        self._models: List[EmbeddingModelInfo] = []

        outer = wx.BoxSizer(wx.VERTICAL)

        grid = wx.FlexGridSizer(cols=2, gap=(8, 10))
        grid.AddGrowableCol(1, 1)

        grid.Add(wx.StaticText(self, label="Name:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._name_ctrl = wx.TextCtrl(self, value=profile.name if profile else "")
        grid.Add(self._name_ctrl, 1, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Embedding backend:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._backend_choice = wx.Choice(self, choices=[label for _key, label in BACKEND_LABELS])
        grid.Add(self._backend_choice, 1, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Embedding model:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._model_choice = wx.Choice(self)
        grid.Add(self._model_choice, 1, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="OpenAI API key:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._openai_key_ctrl = wx.TextCtrl(
            self, value=(profile.openai_api_key or "") if profile else "", style=wx.TE_PASSWORD
        )
        grid.Add(self._openai_key_ctrl, 1, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Gemini API key:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._gemini_key_ctrl = wx.TextCtrl(
            self, value=(profile.gemini_api_key or "") if profile else "", style=wx.TE_PASSWORD
        )
        grid.Add(self._gemini_key_ctrl, 1, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Chunk size (chars):"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._chunk_size_ctrl = wx.SpinCtrl(
            self, min=100, max=50000, initial=(profile.chunk_size if profile else CHUNK_SIZE)
        )
        grid.Add(self._chunk_size_ctrl, 1, wx.EXPAND)

        outer.Add(grid, 0, wx.EXPAND | wx.ALL, 16)

        self._dim_label = wx.StaticText(self, label="")
        outer.Add(self._dim_label, 0, wx.LEFT | wx.RIGHT, 16)

        note = wx.StaticText(
            self,
            label=(
                "Changing the embedding backend/model changes the vector\n"
                "dimension - existing documents stay full-text searchable, but\n"
                "need \"Reindex All\" (Documents screen) before vector/hybrid\n"
                "search includes them again. Changing the chunk size only\n"
                "affects documents indexed/reindexed after this change."
            ),
        )
        note.SetForegroundColour(wx.Colour(120, 120, 120))
        outer.Add(note, 0, wx.LEFT | wx.RIGHT | wx.TOP, 16)

        outer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 16)
        self.SetSizer(outer)

        self._backend_choice.Bind(wx.EVT_CHOICE, self._on_backend_changed)
        self._model_choice.Bind(wx.EVT_CHOICE, lambda evt: self._update_dim_label())
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)

        initial_backend = profile.embedding_backend if profile else DEFAULT_MODEL.backend
        initial_model = profile.embedding_model if profile else DEFAULT_MODEL.model_name
        backend_index = next(
            (i for i, (key, _label) in enumerate(BACKEND_LABELS) if key == initial_backend), 0
        )
        self._backend_choice.SetSelection(backend_index)
        self._populate_models(initial_backend, initial_model)
        self._update_api_key_state()
        self._update_dim_label()

    def _current_backend_key(self) -> str:
        return BACKEND_LABELS[self._backend_choice.GetSelection()][0]

    def _populate_models(self, backend: str, selected_model_name: Optional[str] = None) -> None:
        self._models = models_for_backend(backend)
        self._model_choice.Clear()
        for model in self._models:
            self._model_choice.Append(model.display_name)

        index = 0
        if selected_model_name:
            for i, model in enumerate(self._models):
                if model.model_name == selected_model_name:
                    index = i
                    break
        if self._models:
            self._model_choice.SetSelection(index)

    def _on_backend_changed(self, event: wx.CommandEvent) -> None:
        self._populate_models(self._current_backend_key())
        self._update_api_key_state()
        self._update_dim_label()

    def _update_api_key_state(self) -> None:
        backend = self._current_backend_key()
        self._openai_key_ctrl.Enable(backend == "openai")
        self._gemini_key_ctrl.Enable(backend == "gemini")

    def _update_dim_label(self) -> None:
        model = self._selected_model()
        if model is None:
            self._dim_label.SetLabel("")
            return
        self._dim_label.SetLabel(
            f"Vector dimension: {model.dimension}  |  "
            f"Max chunk size for this model: ~{max_chars_for(model)} chars "
            f"(~{model.max_input_tokens} tokens)"
        )

    def _selected_model(self) -> Optional[EmbeddingModelInfo]:
        index = self._model_choice.GetSelection()
        if index == wx.NOT_FOUND or index >= len(self._models):
            return None
        return self._models[index]

    def _on_ok(self, event: wx.CommandEvent) -> None:
        name = self._name_ctrl.GetValue().strip()
        if not name:
            wx.MessageBox("Name is required.", "Validation error", wx.OK | wx.ICON_WARNING, self)
            return

        model = self._selected_model()
        if model is None:
            wx.MessageBox("Choose an embedding model.", "Validation error", wx.OK | wx.ICON_WARNING, self)
            return

        openai_key = self._openai_key_ctrl.GetValue().strip() or None
        gemini_key = self._gemini_key_ctrl.GetValue().strip() or None
        if model.backend == "openai" and not openai_key:
            wx.MessageBox("This model needs an OpenAI API key.", "Validation error", wx.OK | wx.ICON_WARNING, self)
            return
        if model.backend == "gemini" and not gemini_key:
            wx.MessageBox("This model needs a Gemini API key.", "Validation error", wx.OK | wx.ICON_WARNING, self)
            return

        chunk_size = self._chunk_size_ctrl.GetValue()
        max_chars = max_chars_for(model)
        if chunk_size > max_chars:
            wx.MessageBox(
                f"Chunk size {chunk_size} chars is too large for {model.display_name} "
                f"(max ~{max_chars} chars / {model.max_input_tokens} tokens). "
                "Choose a smaller chunk size.",
                "Validation error",
                wx.OK | wx.ICON_WARNING,
                self,
            )
            return

        self._result = Profile(
            id=self._profile.id if self._profile else None,
            name=name,
            embedding_backend=model.backend,
            embedding_model=model.model_name,
            embedding_dim=model.dimension,
            openai_api_key=openai_key,
            gemini_api_key=gemini_key,
            chunk_size=chunk_size,
            created_at=self._profile.created_at if self._profile else None,
            updated_at=self._profile.updated_at if self._profile else None,
        )
        self.EndModal(wx.ID_OK)

    def get_profile(self) -> Optional[Profile]:
        return self._result
