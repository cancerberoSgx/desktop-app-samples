import csv
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# Structured data files this app can import as a container + record
# documents (see repositories.DocumentRepository.import_data_file) - the
# structured-data counterpart to text_extract.SUPPORTED_EXTENSIONS. JSON
# import requires a top-level array of objects (not e.g. a dict-of-arrays or
# NDJSON) - see read_records.
SUPPORTED_DATA_EXTENSIONS = {".csv", ".json"}

# Whole file is parsed into memory (same tradeoff text_extract.extract_text
# already makes for plain files) - capped here rather than left unbounded,
# since a structured import can plausibly be much larger than a hand-picked
# .txt/.md file ever would be.
MAX_ROWS = 100_000


class DataImportError(Exception):
    """Raised for anything wrong with a structured data file itself - bad
    format, too many rows, etc. Callers surface this directly to the user,
    the same way EmbeddingError is surfaced by the embeddings package."""


def is_supported_data_file(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_DATA_EXTENSIONS


def read_records(path: Path) -> List[Dict[str, object]]:
    """Parse a CSV or JSON (array-of-objects) file into one dict per
    row/object, preserving column/key order."""
    print(f"[data_import] read_records: opening {path} ({path.stat().st_size} bytes)")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            records = list(csv.DictReader(handle))
        print(f"[data_import] read_records: parsed CSV, {len(records)} row(s), "
              f"columns={records[0].keys() if records else '(no rows)'}")
    elif suffix == ".json":
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise DataImportError("JSON import requires a top-level array of objects.")
        records = data
        print(f"[data_import] read_records: parsed JSON array, {len(records)} object(s)")
    else:
        raise DataImportError(f"Unsupported data file type: {path.suffix}")

    if len(records) > MAX_ROWS:
        raise DataImportError(
            f"{path.name} has {len(records)} rows, over this app's {MAX_ROWS}-row import limit."
        )
    return records


@dataclass
class DataFilePreview:
    """Just enough of a parsed data file to populate
    data_import_dialog.ImportMappingDialog - `sample_rows` is only the first
    few rows for display, not the full parse (import_data_file re-reads the
    file itself when it actually runs, so this isn't threaded through)."""

    columns: List[str]
    row_count: int
    sample_rows: List[Dict[str, object]] = field(default_factory=list)


def preview(path: Path, max_sample_rows: int = 5) -> DataFilePreview:
    """Parse the whole file (an accurate row_count needs it) and summarize
    it for the mapping dialog. Intended to be called through
    AsyncTaskRunner, not directly on the UI thread - see
    DocumentsPage._on_import_data_file - since parsing scales with file
    size, not with what's actually displayed."""
    print(f"[data_import] preview: parsing {path} for the mapping dialog")
    records = read_records(path)
    columns: List[str] = []
    seen = set()
    for record in records:
        for key in record.keys():
            if key not in seen:
                seen.add(key)
                columns.append(key)
    print(f"[data_import] preview: {len(records)} row(s), columns={columns}")
    return DataFilePreview(columns=columns, row_count=len(records), sample_rows=records[:max_sample_rows])


@dataclass
class ImportMapping:
    """User's column-mapping choices from ImportMappingDialog."""

    content_columns: List[str]
    id_column: Optional[str] = None
    title_column: Optional[str] = None


def build_record_text(record: Dict[str, object], mapping: ImportMapping) -> str:
    """The text that gets chunked/FTS-indexed/(optionally) embedded for one
    record - only `content_columns`, not every field. All original columns
    still end up in properties_json regardless of mapping (see
    DocumentRepository.import_data_file), so nothing is lost for display
    even when excluded here from search."""
    lines = []
    for column in mapping.content_columns:
        value = record.get(column)
        if value is None or value == "":
            continue
        lines.append(f"{column}: {value}")
    return "\n".join(lines)


def resolve_row_key(record: Dict[str, object], mapping: ImportMapping) -> str:
    """A stable identity for this row across re-imports. Prefers the user's
    chosen id column; falls back to a content hash of the whole row - NOT a
    positional index. A hash survives reordering/insertion/deletion sanely
    (a row's identity depends only on its own content, not where it sits in
    the file); a positional index would silently misattribute every
    downstream row as "changed" on any insert/delete/reorder."""
    if mapping.id_column:
        return str(record.get(mapping.id_column, ""))
    canonical = json.dumps(record, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def find_duplicate_id_values(records: List[Dict[str, object]], id_column: str) -> List[str]:
    """Used by ImportMappingDialog to reject an id column whose values
    collide - two rows sharing a row_key would otherwise silently clobber
    each other on import (the second write wins)."""
    seen = set()
    duplicates: List[str] = []
    for record in records:
        value = str(record.get(id_column, ""))
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates
