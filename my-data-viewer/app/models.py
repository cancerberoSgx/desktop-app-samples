from dataclasses import dataclass, field
from typing import List, Optional

DATASOURCE_TYPES = ("postgres", "mysql", "csv", "json")


@dataclass
class DatasourceField:
    """A user-declared (or inferred) column name/type for a csv datasource,
    persisted 1-N against its owning datasource so it can be reapplied the
    next time the datasource is loaded."""

    name: str
    type: str
    position: int = 0
    id: Optional[int] = None
    datasource_id: Optional[int] = None


@dataclass
class Datasource:
    id: Optional[int]
    name: str
    type: str
    profile_id: Optional[int] = None
    file_path: Optional[str] = None
    url: Optional[str] = None
    db_host: Optional[str] = None
    db_port: Optional[int] = None
    db_name: Optional[str] = None
    db_user: Optional[str] = None
    db_password: Optional[str] = None
    fields: List[DatasourceField] = field(default_factory=list)


@dataclass
class Script:
    """A saved SQL script belonging to one datasource (and its profile)."""

    id: Optional[int]
    name: str
    content: str = ""
    profile_id: Optional[int] = None
    datasource_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class Profile:
    id: Optional[int]
    name: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class ColumnInfo:
    name: str
    type: str
    constraints: str = ""


@dataclass
class IndexInfo:
    name: str
    columns: List[str]


@dataclass
class QueryResult:
    columns: List[str]
    rows: List[tuple]
