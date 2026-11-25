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
  thinking". Assistant replies render as markdown. An outline of prompts jumps
  to each turn, and "Q&A only" shows just your prompts and the final answers.
  The "Actions ▾" menu (also the Conversation menu) copies a `claude --resume` /
  `opencode --session` command or the session id, opens the project folder /
  raw transcript, or exports.
- **Export** (Conversation > Export as HTML, Ctrl+S, or Actions > Export as
  HTML...): saves the conversation and its subagents as one
  self-contained HTML page that works offline. It opens with the viewer's current options, and they can
  be flipped in the browser (outline, Q&A only, expand tool calls/thinking,
  render markdown). The page also has search, a light/dark theme and copy buttons on
  code.
- **Archive**: every conversation is copied into `~/.my-ai-code-viewer/`
  (SQLite), so it stays readable after Claude Code prunes old transcripts
  (~30 days by default). Rescans run every minute and are incremental.
- **Stats** (File > Stats, Ctrl+I): charts and sortable tables of usage per
  agent, top projects (by cost, tokens, conversations, prompts...),
  conversation length, thinking / tool calls / errors, most used tools, and
  when you prompt (per day, weekday × hour). Filter by date range and agent;
  double-click a project or conversation to open it.
- **File > Preferences**: where to find `~/.claude/projects` and
  `~/.local/share/opencode/opencode.db`, the conversation view defaults, and
  the theme (system / light / dark).

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
