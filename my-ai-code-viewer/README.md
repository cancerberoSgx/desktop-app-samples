# My AI Code Viewer

A wxPython desktop app to browse every project and conversation you've had with
AI coding CLIs - currently **Claude Code** and **OpenCode**.

- **Projects**: every folder you ran either tool in (a folder used with both shows
  once), with conversation count and last activity; filter by tool or name.
- **Conversations**: title, tool, last activity, prompts, tool calls, tokens and
  cost - sortable; subagent threads nested under their parent.
- **Conversation**: header with model, duration, tokens, cost, branch and tools
  used, then the chat. Tool calls/results and thinking are collapsed to a
  one-line preview - click to expand, or tick "Expand tool calls" / "Expand
  thinking". Buttons copy a `claude --resume` / `opencode --session` command,
  the session id, or open the project folder / raw transcript.
- **Archive**: every conversation is copied into `~/.my-ai-code-viewer/`
  (SQLite), so it stays readable after Claude Code prunes old transcripts
  (~30 days by default). Rescans run every minute and are incremental.
- **Preferences**: where to find `~/.claude/projects` and
  `~/.local/share/opencode/opencode.db`.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

## Test

```bash
pip install pytest
python -m pytest -q test
```

See `CLAUDE.md` for architecture and how to add another CLI tool.
