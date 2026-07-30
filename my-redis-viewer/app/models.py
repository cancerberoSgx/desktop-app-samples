from dataclasses import dataclass
from typing import Optional


@dataclass
class Datasource:
    """A Redis connection profile can query against."""

    id: Optional[int]
    name: str
    profile_id: Optional[int] = None
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_user: Optional[str] = None
    redis_password: Optional[str] = None


@dataclass
class Profile:
    id: Optional[int]
    name: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class Script:
    """A saved, named block of raw Redis commands (redis-cli-style text)
    that can be re-run against the datasource it belongs to."""

    id: Optional[int]
    name: str
    datasource_id: Optional[int] = None
    text: str = ""
