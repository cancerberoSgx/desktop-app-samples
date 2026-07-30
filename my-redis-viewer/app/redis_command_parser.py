import shlex
from dataclasses import dataclass
from typing import List


@dataclass
class ParsedCommand:
    line_number: int
    raw_text: str
    args: List[str]


def parse_commands(text: str) -> List[ParsedCommand]:
    """Split redis-cli-style script text into one command per non-blank,
    non-comment ("#") line. Each line is tokenized with shlex so quoted
    arguments containing spaces work the same way they do in redis-cli."""
    commands = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            args = shlex.split(stripped)
        except ValueError as exc:
            raise ValueError(f"Line {line_number}: {exc}") from exc
        if args:
            commands.append(ParsedCommand(line_number=line_number, raw_text=stripped, args=args))
    return commands
