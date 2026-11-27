# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A wxPython desktop app that lists every project (folder) the user has run an AI
coding CLI in - currently **Claude Code**, **OpenCode** and **Codex** - and lets them read
each conversation. Click a project -> its conversations; click a conversation ->
its content (prompts, replies, tool calls/results, thinking).

Templated from the sibling `my-redis-viewer` app: same composition root,
repository pattern, `.sql` migrations, settings table,
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
- **codex.py** - `$CODEX_HOME` (default `~/.codex`, setting `codex_home`):
  one `sessions/YYYY/MM/DD/rollout-*.jsonl` per thread (+ `archived_sessions/`),
  lines `{timestamp, type, payload}`. `session_meta` (first line) gives id/cwd/
  git; `response_item` holds assistant messages, reasoning (summaries only -
  the rest is encrypted), `function_call`/`custom_tool_call` + `*_output`
  (matched by `call_id`; error = non-zero exit code); user prompts come from
  `event_msg` `user_message` (or `item_completed` UserMessage in newer CLIs),
  because user-role `response_item`s include injected context - only rollouts
  without such events fall back to them, filtered by prefix. Tokens = last
  `token_count.total_token_usage` (input includes cached); no cost recorded.
  `state_<N>.sqlite` (read-only, optional) supplies user-set thread names
  (folded into the fingerprint) and subagent links (`thread_spawn_edges`, or
  `session_meta.source.subagent.thread_spawn`). Legacy 2025 `rollout-*.json`
  files (TypeScript CLI) have no cwd and are skipped.

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

- `frame.py` - composition root; hosts `ProjectsPage` directly (no sidebar -
  removed on purpose, unlike the sibling apps), menus (File > Rescan F5 /
  Preferences Ctrl+, / Exit; Projects > Export conversations as HTML
  Ctrl+Shift+S; Conversation > the view's actions; Help > About),
  sync scheduling. The menu bar is built after `ProjectsPage` because the
  Conversation menu is generated from `ConversationView.actions`.
- Conversation actions (copy resume command, copy id, open project folder,
  open source file, export HTML Ctrl+S) are `ConversationAction`s defined once
  in `ConversationView._build_actions` and shown in two places: the view's
  "Actions ▾" dropdown button (popup menu, enabled state evaluated on open) and
  the frame's Conversation menu (enabled via `EVT_UPDATE_UI`).
- `projects_page.py` - splitters: projects `ListCtrl` | conversations
  `TreeListCtrl` (subagents as children) | `ConversationView`. Projects
  columns sort on header click (wx-free `project_list.sort_projects`, arrow
  in the label; default Last activity, newest first). Tokens/Cost columns are
  SQL sums in `list_projects` over every conversation incl. subagents; cost
  gets `*` when a top-level conversation has none recorded (as in Stats). Restores last
  project/conversation and sash positions from settings; `refresh_after_sync()`
  keeps selection and only re-renders the open conversation if it changed.
  Title filter + sorting live in wx-free `conversation_list.py` (`arrange`):
  columns are *not* `COL_SORTABLE` because on wxGTK 3.2 a custom
  `TreeListItemComparator` is never called - header clicks are caught on the
  underlying DataViewCtrl and the tree is repopulated in order (arrow in the
  header label). Esc unsplits the conversation view (list gets full width);
  selecting/activating a row re-splits. `_hide_view` focuses the tree *before*
  unselecting, under a `_hiding` guard: on GTK focusing the tree selects its
  cursor row, which would immediately reopen the view.
- `conversation_view.py` + `conversation_html.py` - rendering is a pure function
  producing HTML for `wx.html.HtmlWindow` (no CSS/JS: colours via table
  `bgcolor`; collapsing via `toggle:<seq>` links that re-render). Tool calls and
  thinking are collapsed by default; the "Expand ..." checkboxes flip the
  default and a click flips one block. Large blocks are clipped for display
  (and at 200k chars when archived, `providers/_util.clip`).
- Outline + "Q&A only": `conversation_turns.split_turns` cuts blocks into turns
  (prompt = user text block, consecutive ones merged, "[Request interrupted"
  excluded; answer = last assistant text of the turn; outline label skips
  `<command-name>` markup blocks). The renderer emits `turn:N` / `answer:N`
  links; `ConversationView` finds their page y by walking the laid-out
  `wx.html` cells summing `GetPosY()` (no public anchor lookup, `GetAbsPos` is
  private in wxPython), uses that to scroll on outline clicks, and a 250ms
  timer polls the scroll offset to highlight the current turn. The outline is
  a `SimpleHtmlListBox` - refill it with `Set()`, since `Clear()` resolves to
  VListBox's and leaves stale strings. `qa_only` drops every block except
  prompts and final answers.
- `markdown_html.py` - the "Render markdown" toggle (setting `render_markdown`,
  default on) renders assistant text + thinking via python-markdown; user
  prompts stay plain. `_normalize` bends LLM-style markdown into what
  python-markdown accepts (2/3-space nested lists, no blank line before
  lists/tables, fences inside list items); output is rewritten for wx.html
  (code -> bgcolor table, bordered tables, headings shifted down), raw HTML is
  escaped and only http(s)/mailto links stay clickable. Off (or the library
  missing) falls back to `_markdownish` (fences + headings only).
- `conversation_export.py` - Conversation > Export as HTML (Ctrl+S) /
  Actions > Export as HTML... (`ConversationView.export_html`). It is a pure renderer
  separate from `conversation_html` because it targets a real browser: one
  self-contained file (inline CSS/JS, no CDN, works offline), rendered once
  with every block present, and the toggles are flipped client-side. Q&A only =
  `.nonqa` blocks hidden by `body.qa-only`. Role headers are computed for
  both modes (`.h-full`/`.h-qa`). Markdown blocks are embedded twice (`.md`
  via `markdown_html.markdown_to_html` + `.alt-plain`) and switched by
  `body.md-off`. Collapsibles are `<details>`. Initial state = the app's
  current toggles + individually flipped blocks. Subagents are appended as
  collapsible sections with `s<N>-` id prefixes. Outline scroll-spy, search
  (`<mark>` highlighting, which opens collapsed ancestors), a theme switch
  (auto/light/dark, localStorage) and copy buttons are JS-only. The last export folder is
  saved as setting `export_dir`. Each conversation is a `<section class="conv">`
  and the outline holds one `<ol data-conv>` per conversation, so the same
  page shell/JS serves Projects > Export conversations as HTML
  (`render_project_export_html`, driven by `ProjectsPage.export_project_html`
  + `project_export_dialog.ProjectExportDialog`, a checkable list of the
  project's top-level conversations, all checked): ids get a `c<N>-` prefix,
  only the active section is shown, a collapsible conversation list sits on
  the left (`body.project`, outline moves right), search covers every
  conversation (per-conversation match counts, Enter jumps across them) and
  `#c<N>-...` hashes switch conversation.
- `preferences_dialog.py` - modal File > Preferences, three notebook tabs.
  Sources: Claude projects dir + OpenCode DB path + Codex home (stored in `settings`,
  defaults from each provider's `default_*()`); Save rescans only if a path
  changed. Conversation: the viewer toggles, driven by
  `conversation_view.CONVERSATION_OPTIONS` (same keys as the toolbar
  checkboxes); on save the frame calls `ConversationView.apply_settings()`.
  Visual: Theme (System / Light / Dark, setting `theme`).
  Window size/position/maximized are saved on close (`frame._save_geometry`).
- `theme.py` - wx 3.2 can't re-theme native widgets at runtime, so two
  layers: the palettes we draw (`conversation_html.LIGHT/DARK`, outline +
  html window background) follow the setting immediately via
  `ConversationView.refresh_theme()` (also called on EVT_SYS_COLOUR_CHANGED
  and the 60s timer, so "System" tracks the desktop); native widgets get
  `GTK_THEME=Adwaita[:dark]`, set in `main.py` *before* `wx.App` (the setting
  is read early via `frame.read_setting_before_app`) and removed from the env
  right after so launched programs keep the user's theme. A user-exported
  `GTK_THEME` wins. Changing it offers a restart (`os.execv`). While forced,
  `IsDark()` reports the forced theme, so `system_is_dark` asks the
  freedesktop portal (`gdbus`) instead. Exports default to the app's theme
  (`ExportOptions.theme`) unless the reader picked one in the page.
- Stats: File > Stats... (Ctrl+I) opens `stats_frame.StatsFrame`, a separate
  non-modal window (one instance; reloaded after each sync that changed data,
  re-themed with the view). Range + agent filters (saved as `stats_range` /
  `stats_provider`, last tab `stats_tab`), KPI tiles, notebook tabs Agents /
  Projects / Conversations / Activity / Tools = chart over a click-to-sort
  `StatsTable`. Double-click a project/conversation -> `MainFrame._reveal` ->
  `ProjectsPage.reveal` (clears list filters that would hide it). Numbers come
  from wx-free `stats.load_stats` (tested): subagents roll into their parent
  (tokens/tools/thinking, never prompts); prompts = turn starts computed in SQL
  with `LAG()` (same rule as `split_turns`); tool names merge
  case-insensitively (Claude `Bash` + OpenCode `bash`). The range keeps
  conversations *active* in it with whole-conversation totals (tokens/cost
  aren't stored per message); activity charts use exact prompt timestamps.
  Claude cost is NULL without a `cost-state` line -> counted as
  `cost_unknown` and flagged `*`. Charts are drawn by hand in
  `stats_charts.py` (wx.GraphicsContext, no plotting dependency) with their
  own LIGHT/DARK `ChartTheme`; a provider's colour is its slot in
  `PROVIDER_LABELS`, never its rank.
- `about_dialog.py` - Help > About.

### Not done yet (ideas the user has expressed)

- Cross-project search over conversation content (the `messages` table was
  shaped for an FTS5 index).
- Stats phase 2/3: store per-conversation counters at index time, estimate
  missing Claude cost from tokens, and a per-message usage table (timestamp,
  model, tokens, cost) for exact cost/tokens over time and by model.
