from pathlib import Path
from typing import List

from app.providers.base import ProviderAdapter
from app.providers.claude_code import ClaudeCodeProvider
from app.providers.codex import CodexProvider
from app.providers.opencode import OpenCodeProvider

PROVIDER_LABELS = {
    ClaudeCodeProvider.id: ClaudeCodeProvider.label,
    OpenCodeProvider.id: OpenCodeProvider.label,
    CodexProvider.id: CodexProvider.label,
}


def build_providers(claude_projects_dir: Path, opencode_db_path: Path, codex_home: Path) -> List[ProviderAdapter]:
    """Every supported tool, configured from Preferences. Register new
    providers here (and in PROVIDER_LABELS)."""
    return [
        ClaudeCodeProvider(claude_projects_dir),
        OpenCodeProvider(opencode_db_path),
        CodexProvider(codex_home),
    ]
