# Media for the My Docker Viewer homepage

Drop files here with these exact names — `../index.html` already references them
and will pick them up as soon as they exist (just swap the matching
`media-placeholder` div for the `<img>`/`<video>` tag shown in the HTML comment
right above it).

| File | Type | What to capture |
|---|---|---|
| `hero.png` | screenshot | The Containers screen with a real mix of running/stopped containers, live CPU%/Mem% filled in for the running ones. This is the main image people see first — make it look good. |
| `container-details.mp4` (+ `container-details.png` poster frame) | ~10-20s recording | Double-click a running container, land on the Details dialog, and let its disk usage section finish calculating. |
| `containers-disk.png` | screenshot | The Containers Disk screen with several containers listed, sizes computed, and at least one row flagged "(shared)". |
| `image-cascade.png` | screenshot | The Remove Image dialog with the cascading-cleanup option selected, showing the kept-vs-removed breakdown. |
| `volumes.png` | screenshot | The Volumes screen with the Used By column showing real container/image names and at least one Size already calculated. |

Suggested specs: 1280×800 or larger, PNG for stills. For the recording, export as
MP4 or WebM rather than GIF (much smaller file for the same quality) — any raw
format is fine, ask to have it trimmed/compressed/converted afterwards.

Use a Docker host with a handful of real-looking containers/images (not just
`hello-world`) — mixed running/stopped states and at least one shared
volume make the screenshots convincing.
