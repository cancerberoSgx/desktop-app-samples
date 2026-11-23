"""Split a conversation into turns (pure, wx-free) - backs the outline pane
and the "Q&A only" view.

A turn starts at a user text block (a prompt; consecutive user text blocks
are one prompt) and runs until the next prompt. Its answer is the turn's
last assistant text block - the final reply after any tool calls. Blocks
before the first prompt belong to no turn. "[Request interrupted ...]"
markers are user text but not prompts.
"""
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from app.models import MessageBlock

_INTERRUPT = "[Request interrupted"
_TAG = re.compile(r"<[^>]+>")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


@dataclass
class Turn:
    number: int                       # 1-based, as shown ("#3")
    prompt_seqs: List[int] = field(default_factory=list)
    prompt_text: str = ""
    label_text: str = ""              # for the outline: first prompt block that isn't markup
    answer_seq: Optional[int] = None
    answer_text: str = ""

    @property
    def first_seq(self) -> int:
        return self.prompt_seqs[0]


def _is_prompt(block: MessageBlock) -> bool:
    return block.role == "user" and block.kind == "text" and not block.text.lstrip().startswith(_INTERRUPT)


def split_turns(blocks: Iterable[MessageBlock]) -> List[Turn]:
    turns: List[Turn] = []
    prev_was_prompt = False
    for b in blocks:
        if _is_prompt(b):
            if prev_was_prompt and turns:
                turn = turns[-1]
                turn.prompt_seqs.append(b.seq)
                turn.prompt_text += "\n" + b.text
                # "/clear"-style command markup arrives as its own block
                # before the real prompt - label the turn by the prompt.
                if turn.label_text.lstrip().startswith("<") and not b.text.lstrip().startswith("<"):
                    turn.label_text = b.text
            else:
                turns.append(Turn(number=len(turns) + 1, prompt_seqs=[b.seq], prompt_text=b.text,
                                  label_text=b.text))
            prev_was_prompt = True
            continue
        prev_was_prompt = False
        if turns and b.role == "assistant" and b.kind == "text" and b.text.strip():
            turns[-1].answer_seq = b.seq
            turns[-1].answer_text = b.text
    return turns


def first_line(text: str, limit: int = 160) -> str:
    """Readable one-line preview: markup tags and markdown noise stripped."""
    for line in _TAG.sub(" ", text).splitlines():
        line = line.strip().lstrip("#>*-` ").strip()
        line = _LINK.sub(r"\1", line).replace("**", "").replace("`", "")
        if line:
            line = re.sub(r"\s+", " ", line).strip()
            return line[:limit] + ("..." if len(line) > limit else "")
    return ""
