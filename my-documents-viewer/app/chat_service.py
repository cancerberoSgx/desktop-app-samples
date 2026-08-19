from typing import List

from .chat import ChatBackend, ChatError, get_chat_backend
from .conversation_repository import ConversationRepository
from .embeddings import EmbeddingError
from .file_display import FULL_PATH, format_document_label
from .models import ChatMessage, Profile, SearchResult
from .repositories import DocumentRepository

# How many prior messages (user + assistant combined) feed into both the
# query-condensation call and the final answer prompt - bounds prompt size on
# a long-running conversation. A small number is enough: condensation only
# needs recent context to resolve pronouns/ellipsis ("on which years?"), and
# the retrieved excerpts (not old turns) carry most of an answer's real
# content anyway.
CHAT_HISTORY_TURNS = 10

# How many chunks hybrid_search() retrieves per question - deliberately
# small: these are spelled out in full (not truncated like a search
# snippet) in the answer prompt, and every one of them is shown to the user
# as a reference regardless of whether the model's answer actually drew on
# it (see CLAUDE.md-confirmed "always show full source list" design).
CHAT_RETRIEVAL_LIMIT = 6

CONDENSE_SYSTEM_PROMPT = (
    "Rewrite the user's latest message as a fully self-contained, standalone "
    "question, using the conversation so far to fill in anything it refers "
    "to implicitly (pronouns, \"that\", \"on which years\", etc). "
    "Reply with only the rewritten question - no preamble, no quotes."
)

ANSWER_SYSTEM_PROMPT_TEMPLATE = (
    "You are answering questions about the user's indexed documents in the "
    '"{profile_name}" collection. Answer using ONLY the excerpts provided '
    "below - if the answer isn't in them, say you don't know rather than "
    "guessing. Be concise and direct."
)


class ChatService:
    """Retrieval-augmented chat: turns a question into a search query
    (condensing it against recent history first, for a follow-up like "on
    which years?"), retrieves matching chunks via
    DocumentRepository.hybrid_search, and asks the profile's chat backend to
    answer from them - the RAG counterpart to DocumentRepository's own
    indexing/search logic. Persists both the question and the answer (with
    its retrieved sources) via ConversationRepository.

    `ask()` is blocking (search + one or two LLM calls) - callers (ChatPage)
    must run it through AsyncTaskRunner, never directly from a wx.EVT_*
    handler, same rule as DocumentRepository.hybrid_search/index_paths."""

    def __init__(self, document_repository: DocumentRepository, conversation_repository: ConversationRepository):
        self._documents = document_repository
        self._conversations = conversation_repository

    def ask(self, profile: Profile, conversation_id: int, question: str) -> ChatMessage:
        backend = get_chat_backend(profile)  # raises ChatError if unconfigured
        history = self._conversations.list_messages(conversation_id, limit=CHAT_HISTORY_TURNS)

        search_query = self._condense_query(backend, history, question) if history else question

        try:
            results = self._documents.hybrid_search(profile, search_query, mode="hybrid", limit=CHAT_RETRIEVAL_LIMIT)
        except EmbeddingError:
            # hybrid_search already falls back to full-text-only internally
            # on an embedding failure - this only fires if even that raised
            # (e.g. no vector table yet and no FTS fallback path taken), in
            # which case there's simply nothing retrieved this turn.
            results = []

        answer_text = self._generate_answer(backend, profile, history, results, question)

        self._conversations.add_message(conversation_id, "user", question)
        return self._conversations.add_message(conversation_id, "assistant", answer_text, references=results)

    # ------------------------------------------------------------------
    # Query condensation - resolves a follow-up like "on which years?" into
    # a standalone search query using recent conversation history.
    # ------------------------------------------------------------------
    def _condense_query(self, backend: ChatBackend, history: List[ChatMessage], question: str) -> str:
        messages = [{"role": "system", "content": CONDENSE_SYSTEM_PROMPT}]
        messages.extend({"role": m.role, "content": m.content} for m in history)
        messages.append({"role": "user", "content": question})
        try:
            rewritten = backend.complete(messages, max_tokens=200).strip()
        except ChatError:
            # Condensation is a best-effort quality improvement, not
            # essential - fall back to the raw question rather than failing
            # the whole turn over it.
            return question
        return rewritten or question

    # ------------------------------------------------------------------
    # Answer generation
    # ------------------------------------------------------------------
    def _generate_answer(
        self,
        backend: ChatBackend,
        profile: Profile,
        history: List[ChatMessage],
        results: List[SearchResult],
        question: str,
    ) -> str:
        messages = [{"role": "system", "content": ANSWER_SYSTEM_PROMPT_TEMPLATE.format(profile_name=profile.name)}]
        messages.append({"role": "system", "content": self._build_context_block(results)})
        messages.extend({"role": m.role, "content": m.content} for m in history)
        messages.append({"role": "user", "content": question})
        return backend.complete(messages).strip()

    def _build_context_block(self, results: List[SearchResult]) -> str:
        if not results:
            return "No matching excerpts were found in the document collection for this question."

        chunk_texts = self._documents.get_chunk_texts([r.chunk_id for r in results])
        parts = ["Excerpts from the document collection (most relevant first):"]
        for index, result in enumerate(results, start=1):
            document = self._documents.get(result.document_id)
            container = (
                self._documents.get(document.parent_document_id)
                if document and document.parent_document_id
                else None
            )
            label = format_document_label(document, container, FULL_PATH) if document else result.document_path
            text = chunk_texts.get(result.chunk_id, result.snippet)
            parts.append(f"[{index}] ({label}):\n{text}")
        return "\n\n".join(parts)
