# Media for the My Data Viewer homepage

Drop files here with these exact names — `../index.html` already references them
and will pick them up as soon as they exist (just swap the matching
`media-placeholder` div for the `<img>`/`<video>` tag shown in the HTML comment
right above it).

| File | Type | What to capture |
|---|---|---|
| `hero.png` | screenshot | The SQL editor with a query typed in and results showing in the grid. This is the main image people see first — make it look good. |
| `open-and-query.mp4` (+ `open-and-query.png` poster frame) | ~10-20s recording | Drag a CSV onto the window (or create a datasource), browse its columns, type a query, run it, see results appear. |
| `datasources.png` | screenshot | The Datasources screen with at least one real datasource in the list. |
| `schema-browser.png` | screenshot | The tables/columns/indexes panel for an open datasource. |
| `export.png` | screenshot | The "Export as Parquet" dialog/flow. |
| `postgres-datasource.png` | screenshot | The New Datasource dialog with the PostgreSQL connection fields (or a connection URL) filled in. |
| `point-and-click.png` | screenshot | The Data tab with a column filter dropdown open and a sort arrow visible on a column header — no SQL editor in view. |
| `infer-types.png` | screenshot | The "Infer types" grid showing detected column names/types for a CSV or JSON file before saving. |

Suggested specs: 1280×800 or larger, PNG for stills. For the recording, export as
MP4 or WebM rather than GIF (much smaller file for the same quality) — any raw
format is fine, ask to have it trimmed/compressed/converted afterwards.

Use realistic sample data (a real-looking CSV with meaningful column names), not
`test1,test2,test3` — this is what makes the screenshots convincing.
