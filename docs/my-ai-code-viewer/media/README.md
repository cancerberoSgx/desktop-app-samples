# Media for the My AI Code Viewer homepage

Drop files here with these exact names — `../index.html` already references them
and will pick them up as soon as they exist (just swap the matching
`media-placeholder` div for the `<img>`/`<video>` tag shown in the HTML comment
right above it). `hero.png` is also used by the landing page card
(`../../index.html`).

| File | Type | What to capture |
|---|---|---|
| `hero.png` | screenshot | Main window: projects list, a project's conversations (one with subagents expanded), and a conversation open with the outline pane showing. The main image people see first — make it look good. |
| `outline-qa.png` | screenshot | A long conversation with "Q&A only" ticked and the outline highlighting the current turn. |
| `tools.png` | screenshot | A conversation with several collapsed tool calls / thinking blocks and one expanded. |
| `actions.png` | screenshot | The "Actions ▾" dropdown open (copy resume command, copy id, open folder, open source file, export). |
| `export.png` | screenshot | An exported conversation open in a browser: outline, search box with a highlighted match, toggles visible. |
| `stats.png` | screenshot | File > Stats on the Projects tab: KPI tiles on top, top-projects chart (e.g. metric = Cost) and the table. Range "Last 90 days" or "All time" so there's plenty of data. |
| `stats-activity.png` | screenshot | Stats > Activity: prompts per day + weekday × hour heatmap. |
| `stats-conversations.png` | screenshot | Stats > Conversations: length histogram + longest conversations. |
| `stats-tools.png` | screenshot | Stats > Tools: most used tools chart + table with error rates. |
| `dark.png` | screenshot | The main window (or Stats) with Preferences > Visual > Theme = Dark. |

Suggested specs: 1280×800 or larger, PNG for stills.

Use conversations you don't mind publishing — titles, prompts and project
names are all visible. A mix of Claude Code and OpenCode conversations makes
the per-agent charts more convincing.
