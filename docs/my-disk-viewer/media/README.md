# Media for the My Disk Viewer homepage

Drop files here with these exact names — `../index.html` already references them
and will pick them up as soon as they exist (just swap the matching
`media-placeholder` div for the `<img>`/`<video>` tag shown in the HTML comment
right above it).

| File | Type | What to capture |
|---|---|---|
| `hero.png` | screenshot | The Table tab for a real, already-scanned folder with a mix of folders/files, sorted by Size descending, breadcrumb and folder summary visible. This is the main image people see first — make it look good. |
| `scan-progress.mp4` (+ `scan-progress.png` poster frame) | ~10-20s recording | Open a folder that hasn't been scanned yet, hit Reload, and let the "Scanning... (n/total)" progress run to completion. |
| `breadcrumb.png` | screenshot | The breadcrumb trail a few levels deep (e.g. Home / Projects / app / src), with the current folder shown in bold. |
| `chart-by-folder.png` | screenshot | The Chart tab in "By subfolder/file" mode, with the legend and a hover tooltip on a wedge if possible. |
| `chart-by-type.png` | screenshot | The Chart tab in "By file type" mode, showing a recognizable set of extensions (e.g. .mp4, .log, .jpg). |

Suggested specs: 1280×800 or larger, PNG for stills. For the recording, export as
MP4 or WebM rather than GIF (much smaller file for the same quality) — any raw
format is fine, ask to have it trimmed/compressed/converted afterwards.

Scan a real, moderately large folder (a home directory, a project with build
artifacts, a Downloads folder) rather than an empty test directory — this is
what makes the screenshots convincing.
