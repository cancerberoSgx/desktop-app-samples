# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A wxPython desktop app that lists every project (folder) the user has run an AI
coding CLI in - currently **Claude Code** and **OpenCode** - and lets them read
each conversation. Click a project -> its conversations; click a conversation ->
its content (prompts, replies, tool calls/results, thinking).

Templated from the sibling `my-redis-viewer` app: same composition root,
repository pattern, `.sql` migrations, settings table, collapsible sidebar,
`AsyncTaskRunner`, VERSION/PyInstaller/AUR/workflow setup. Profiles and
datasources were deliberately **not** carried over (explicit product decision).

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 main.py

pip install pytest && python -m pytest -q test      # wx-free, no real user data touched

.venv/bin/pip install pyinstaller
.venv/bin/pyinstaller --noconfirm myaicodeviewer.spec
./dist/myaicodeviewer/myaicodeviewer
```

## Architecture

### Providers (`app/providers/`) - the only tool-specific code

`ProviderAdapter` (base.py) has three methods: `is_available()`,
`list_sources()` (cheap: id + fingerprint per conversation, no parsing) and
`parse(source)` (-> `ParsedConversation` of normalized `MessageBlock`s with
role user/assistant/system and kind text/thinking/tool_use/tool_result/error).
To support another CLI, add a subclass and register it in
`providers/__init__.py` (`build_providers` + `PROVIDER_LABELS`) - nothing in the
schema or UI is provider-specific.

- **claude_code.py** - `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`. The folder
  name replaces every non-alphanumeric char with `-` (lossy), so the real path is
  recovered by finding a `cwd` in the transcript whose encoding equals the folder
  name (`_pick_project_path`), falling back to a naive `-`->`/` decode. Only
  `user`/`assistant` lines are content; one API message is split over several
  lines sharing `message.id` (usage counted once per id); lines are deduped by
  `uuid`; `isMeta` lines skipped. Title from `ai-title`/`summary`, cost from the
  last `cost-state`. Subagents: `<uuid>/subagents/agent-<id>.jsonl` (+`.meta.json`
  description), indexed as conversations with `parent_external_id` set and shown
  nested under the parent in the UI.
- **opencode.py** - `~/.local/share/opencode/opencode.db`, opened `mode=ro`.
  `session`/`message`/`part` tables with JSON `data`. Projects are grouped by
  `session.directory` (not OpenCode's `project` table, which is the git root) to
  match Claude Code's per-launch-folder grouping. Placeholder titles
  (`New session - ...`) are dropped so the first prompt is shown instead.

### Archive (`app/indexer.py`, `app/repositories.py`, migrations)

The UI never reads provider data directly - it reads the app's own SQLite DB
(`~/.my-ai-code-viewer/my-ai-code-viewer.db`): `projects` (one row per
provider+project_key), `conversations`, `messages` (one row per block). This
exists because Claude Code prunes transcripts after ~30 days: a conversation
whose source disappears gets `missing_from_source = 1` and stays readable
("(archived)" in the list). `messages` is flat text on purpose so a future
cross-project search can be an FTS5 table over `messages.text`.

`indexer.sync()` is incremental: re-parse only when the source fingerprint
(mtime+size, or OpenCode's `time_updated`) changed. It runs on a worker thread
via `AsyncTaskRunner` with **its own connection** (`sync_in_new_connection`;
sqlite connections can't cross threads) - hence WAL + busy timeout in
`db/connection.py`. `MainFrame` runs it at startup (after first showing what's
already archived) and every 60s, plus File > Rescan (F5) / the Rescan button /
saving Preferences.

`IndexRepository.list_project_groups()` merges project rows of different
providers that share a path into one `ProjectGroup` for the UI.

### UI

- `frame.py` - composition root; sidebar (Projects / Preferences / About, order
  must match `SIDEBAR_ITEMS`), sync scheduling.
- `projects_page.py` - splitters: projects `ListCtrl` | conversations
  `TreeListCtrl` (subagents as children, numeric-aware sort comparator) |
  `ConversationView`. Restores last project/conversation and sash positions
  from settings; `refresh_after_sync()` keeps selection and only re-renders the
  open conversation if it changed.
- `conversation_view.py` + `conversation_html.py` - rendering is a pure function
  producing HTML for `wx.html.HtmlWindow` (no CSS/JS: colours via table
  `bgcolor`; collapsing via `toggle:<seq>` links that re-render). Tool calls and
  thinking are collapsed by default; the "Expand ..." checkboxes flip the
  default and a click flips one block. Large blocks are clipped for display
  (and at 200k chars when archived, `providers/_util.clip`).
- `preferences_page.py` - Claude projects dir + OpenCode DB path (stored in
  `settings`, defaults from each provider's `default_*()`).

### Not done yet (ideas the user has expressed)

- Cross-project search over conversation content (the `messages` table was
  shaped for an FTS5 index).
