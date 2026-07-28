from dataclasses import dataclass
from typing import List, Optional

DATASOURCE_TYPES = ("postgres", "mysql", "csv")


@dataclass
class Datasource:
    id: Optional[int]
    name: str
    type: str
    file_path: Optional[str] = None
    db_host: Optional[str] = None
    db_port: Optional[int] = None
    db_name: Optional[str] = None
    db_user: Optional[str] = None
    db_password: Optional[str] = None


@dataclass
class ColumnInfo:
    name: str
    type: str


@dataclass
class IndexInfo:
    name: str
    columns: List[str]


@dataclass
class QueryResult:
    columns: List[str]
    rows: List[tuple]
