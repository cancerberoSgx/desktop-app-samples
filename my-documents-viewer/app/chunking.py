from typing import List, NamedTuple

# ~300-800 tokens is the usual sweet spot for embedding chunks (see
# prompts.md's "my-documents view" section) - measuring in characters here
# rather than tokens to avoid a tokenizer dependency; ~4 chars/token puts
# CHUNK_SIZE at roughly 200 tokens, comfortably in range for short paragraphs
# in personal notes/contracts/etc.
CHUNK_SIZE = 800
CHUNK_OVERLAP = 150


class Chunk(NamedTuple):
    index: int
    start_offset: int
    end_offset: int
    text: str


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[Chunk]:
    """Split `text` into overlapping character-window chunks.

    Each window is extended, when possible, to the next whitespace within a
    short lookahead so words aren't split mid-token. Consecutive windows
    overlap by `overlap` characters so a sentence spanning a chunk boundary
    still appears whole in at least one chunk. Empty/whitespace-only
    fragments are dropped.
    """
    if not text:
        return []

    length = len(text)
    chunks: List[Chunk] = []
    start = 0

    while start < length:
        end = min(start + chunk_size, length)
        if end < length:
            lookahead_limit = min(end + 50, length)
            next_space = text.find(" ", end, lookahead_limit)
            if next_space != -1:
                end = next_space

        fragment = text[start:end].strip()
        if fragment:
            chunks.append(Chunk(index=len(chunks), start_offset=start, end_offset=end, text=fragment))

        if end >= length:
            break
        start = max(end - overlap, start + 1)

    return chunks
