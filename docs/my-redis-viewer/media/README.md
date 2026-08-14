# Media for the My Redis Viewer homepage

Drop files here with these exact names — `../index.html` already references them
and will pick them up as soon as they exist (just swap the matching
`media-placeholder` div for the `<img>`/`<video>` tag shown in the HTML comment
right above it).

| File | Type | What to capture |
|---|---|---|
| `hero.png` | screenshot | The Data Explorer's Tree tab with a real keyspace expanded, ideally with the Key Details dialog open over it. Main image people see first. |
| `connect-flow.mp4` (+ `connect-flow.png` poster frame) | ~10-20s recording | Add a new data source (host/port/password), click Connect, land in the Data Explorer as the keyspace scan runs. |
| `tree-search.png` | screenshot | The Search tab with a glob pattern typed in and a type filter selected. |
| `key-details.png` | screenshot | The Key Details dialog for a real key — type, TTL, encoding, memory, value all visible. |
| `scripts.png` | screenshot | The Scripts tab with a named, saved command script and its output pane showing a result. |
| `script-results.png` | screenshot | A script run whose output has multiple per-command blocks, at least one with a "View keys..." button visible (e.g. from a `KEYS pattern` command). |

Suggested specs: 1280×800 or larger, PNG for stills. For the recording, export as
MP4 or WebM rather than GIF (much smaller file for the same quality) — any raw
format is fine, ask to have it trimmed/compressed/converted afterwards.

Use a Redis instance with realistic-looking keys (e.g. `user:1042:session`,
`cache:product:8871`), not `foo`/`bar` — this is what makes the screenshots
convincing.
