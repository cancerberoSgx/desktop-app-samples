import json
import sqlite3
from dataclasses import asdict
from typing import List, Optional

from .models import ChatMessage, Conversation, SearchResult

DEFAULT_CONVERSATION_TITLE = "New Conversation"


class ConversationRepository:
    """CRUD for `conversations`/`conversation_messages` (pure SQL against
    SQLite) - the Chat page's counterpart to ProfileRepository/
    SettingsRepository. Conversations are scoped by profile_id, same as
    DocumentRepository's documents; ON DELETE CASCADE (profiles ->
    conversations -> conversation_messages) handles cleanup when a profile
    is deleted, same as it already does for documents/chunks.

    The actual retrieval-augmented-generation logic (searching, prompting a
    ChatBackend) lives in ChatService (app/chat_service.py), which uses this
    repository purely for persistence - same separation DocumentRepository
    keeps between hybrid_search (logic) and its own plain document CRUD."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------
    def create(self, profile_id: int, title: str = DEFAULT_CONVERSATION_TITLE) -> Conversation:
        cursor = self._conn.execute(
            "INSERT INTO conversations (profile_id, title) VALUES (?, ?)", (profile_id, title)
        )
        self._conn.commit()
        return self.get(cursor.lastrowid)

    def list(self, profile_id: int) -> List[Conversation]:
        rows = self._conn.execute(
            "SELECT * FROM conversations WHERE profile_id = ? ORDER BY updated_at DESC", (profile_id,)
        ).fetchall()
        return [self._row_to_conversation(row) for row in rows]

    def get(self, conversation_id: int) -> Optional[Conversation]:
        row = self._conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        return self._row_to_conversation(row) if row else None

    def rename(self, conversation_id: int, title: str) -> None:
        self._conn.execute(
            "UPDATE conversations SET title = ?, updated_at = datetime('now') WHERE id = ?",
            (title, conversation_id),
        )
        self._conn.commit()

    def touch(self, conversation_id: int) -> None:
        """Bump updated_at - called after add_message so the conversation
        list (sorted by updated_at desc) surfaces recently-active
        conversations first."""
        self._conn.execute(
            "UPDATE conversations SET updated_at = datetime('now') WHERE id = ?", (conversation_id,)
        )
        self._conn.commit()

    def delete(self, conversation_id: int) -> None:
        self._conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        self._conn.commit()

    @staticmethod
    def _row_to_conversation(row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            profile_id=row["profile_id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------
    def list_messages(self, conversation_id: int, limit: Optional[int] = None) -> List[ChatMessage]:
        """Every message in a conversation, oldest first (chronological -
        the order a transcript is read/replayed in). `limit`, if given,
        returns only the most recent `limit` messages (still oldest-first) -
        used by ChatService to bound how much history feeds into a prompt
        without loading (and re-serializing) an entire long conversation."""
        query = "SELECT * FROM conversation_messages WHERE conversation_id = ? ORDER BY id"
        if limit is not None:
            # Take the last `limit` rows by id, then re-sort ascending - a
            # plain "ORDER BY id DESC LIMIT ?" would return them newest-first.
            rows = self._conn.execute(
                "SELECT * FROM conversation_messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
            rows = list(reversed(rows))
        else:
            rows = self._conn.execute(query, (conversation_id,)).fetchall()
        return [self._row_to_message(row) for row in rows]

    def add_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        references: Optional[List[SearchResult]] = None,
    ) -> ChatMessage:
        references_json = json.dumps([asdict(r) for r in references]) if references else None
        cursor = self._conn.execute(
            """
            INSERT INTO conversation_messages (conversation_id, role, content, references_json)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, role, content, references_json),
        )
        self._conn.commit()
        self.touch(conversation_id)
        row = self._conn.execute(
            "SELECT * FROM conversation_messages WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return self._row_to_message(row)

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> ChatMessage:
        references_json = row["references_json"]
        references = (
            [SearchResult(**r) for r in json.loads(references_json)] if references_json else []
        )
        return ChatMessage(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            created_at=row["created_at"],
            references=references,
        )
