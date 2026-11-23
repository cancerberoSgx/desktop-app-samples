from app.conversation_turns import first_line, split_turns
from app.models import MessageBlock


def blocks(*specs):
    return [MessageBlock(role, kind, text, seq=i) for i, (role, kind, text) in enumerate(specs)]


CONV = blocks(
    ("assistant", "text", "preamble before any prompt"),        # 0: no turn
    ("user", "text", "<command-name>/clear</command-name>"),   # 1: turn 1
    ("user", "text", "fix the login bug"),                     # 2: same turn
    ("assistant", "text", "Looking."),                         # 3: intermediate
    ("assistant", "tool_use", '{"command": "ls"}'),            # 4
    ("user", "tool_result", "a.py"),                           # 5: not a prompt
    ("assistant", "text", "Fixed **it** in `a.py`."),          # 6: answer 1
    ("user", "text", "[Request interrupted by user]"),         # 7: not a prompt
    ("user", "text", "now add tests"),                         # 8: turn 2
    ("assistant", "thinking", "hmm"),                          # 9: turn 2 has no answer
)


def test_split_turns():
    t1, t2 = split_turns(CONV)
    assert (t1.number, t1.prompt_seqs, t1.answer_seq) == (1, [1, 2], 6)
    assert (t2.number, t2.prompt_seqs, t2.answer_seq) == (2, [8], None)


def test_label_skips_command_markup():
    t1 = split_turns(CONV)[0]
    assert first_line(t1.label_text) == "fix the login bug"


def test_first_line_strips_markdown():
    assert first_line("\n## Use **gp3** via [the docs](https://x) and `io2`\nmore") == "Use gp3 via the docs and io2"
    assert first_line("x" * 300, limit=10) == "x" * 10 + "..."
