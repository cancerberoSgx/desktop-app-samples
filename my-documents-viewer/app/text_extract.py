from pathlib import Path

# Extensions this app can currently index. Both are read as plain UTF-8 text
# (Markdown is indexed with its formatting characters left in - they're
# cheap signal for FTS5 and mostly ignored by embedding models). Extending
# this to PDFs/Office docs/etc. later just means adding an extractor here and
# to SUPPORTED_EXTENSIONS - the rest of the indexing pipeline
# (repositories.DocumentRepository.index_paths) is format-agnostic, it only
# ever sees the extracted plain text.
SUPPORTED_EXTENSIONS = {".txt", ".md"}


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def extract_text(path: Path) -> str:
    """Read a supported file as text. Raises UnicodeDecodeError/OSError on
    failure - callers (DocumentRepository.index_paths) are expected to catch
    per-file and continue with the rest of the batch rather than let one bad
    file abort an entire folder import."""
    return path.read_text(encoding="utf-8")
