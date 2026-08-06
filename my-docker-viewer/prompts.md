# my-docker-viewer

create a new project "my-docker-viewer" in folder "./my-docker-viewer". This is a desktop project to admin docker containers, images, stats, purges, etc. User the project my-redis-viewer (wxwindows, python) as a template. But don't copy any features, not even profiles or datasources.
Instead use docker CLI to build the following features in separate screens (sidebar). Assume docker is installed in current system and execute all docker related operations using docker cli or fail if not installed.
 * containers:     list all docker containers and their status, dates, names, etc
    * be able to know how much disk and memory and cpu each docker is taking
    *  be able to stop or rm each container
    * filter containers by name, image name, status, etc
 * .github/workflows/my-redis-viewer-build.yml - add a similar my-docker-viewer.yml for this project with similar builds.

You will still use sqlite and migrations as in my-redis-viewer to store user settings in the future.


# loading

when the app starts, the "containers" table is empty for some seconds (until docker information retrieval finishes). Add a "loading" visual feedback when this happens or when it's refreshed.

# sort columns

in "containers" table, user can sort all columns by clicking the column header asc and desc.
the "created" column must be friendlier, for example, X hours ago, x days ago, etc instead of a timestamp.

# auto-refresh
currently the application auto-refresh every N seconds. Make a change so the user can activate auto-refresh every N seconds with a checkbox/toggle button. By default auto-refresh is disable (user must manually click the refresh button)

# remove bug
when a container is removed or stopped, the UI must be updated instantaneusly if the docker command runs successfully. (update the table without a full refresh, just virtually)

# see disk space
question. in current project how difficult and costly is to see disk space consumed by each container ? Don't implement just recommend if feasible and how fast / difficult to execute this could be. Remember that this needs to work in linux, windows and macos

p2

I really want to see all containers volumes disk usage, in all of its variants: containers, volumes, mounted volumes, shared volumes, bind mounts. 

I want to see this in a separate main screen "containers disk" which display a table with columns: container id, container name, image, disk usage (human friendly using GB, MB, KB, etc). Also include any relevant information about the type of storage, example: volume, bind mounts, shared, etc. 

The main objective is to see which containers uses most disk space in the case I need to release space (full disk).
For linux and macos use 'du' command if available, and for windows show an empty data in case you need to use 'du' or similar command and we'll implement it later. This new view will have a "Calculate" button which performs the calculation with a "loading" UX and it will only be executed when users press that button.

If for some containers the disk usage is faster than for others, display their disk information earlier with the "loading" still displayed. If for some containers this information cannot be obtained (windows & du command) display Not supported.

There will be no other actions in this screen like stoping a container, since it's only read-only information

Make sure when this calculations are taking place, the UI is not blocked (already OK in the current "containers" screen.)



---


# images and other docker info

In the context of the current project which supports docker containers info, actions and even disk usage per container. Now I want to include images, volumes, networks, etc (other docker concepts) that can be useful. Without writing any code, analyze new views such as "images", "volumes", etc that can be useful to docker users to implement in separate screens. For example, I want to see the size and container count for each image. Also image actions such as remove, prune, etc... Design a UX and describe, without writing code


A:

# New Docker Views: UX Design Analysis

Scoping this the same way `Containers` is scoped today: each new concept gets its own sidebar page, its own thin CLI-wrapper repository, list + filter + actions, and optimistic updates on success. No SDK, no daemon socket — everything below maps to a `docker ... --format '{{json .}}'` invocation.

## Recommended new sidebar pages

```
Containers (existing)
Images        ← new
Volumes       ← new
Networks      ← new
Disk Usage    ← new (system-level dashboard, ties the other three together)
About (existing)
```

Icon language: keep the sidebar's existing icon-button style — stacked layers for Images, a drum/cylinder for Volumes, connected nodes for Networks, a gauge/pie-chart for Disk Usage.

---

## 1. Images

**Why it matters:** images accumulate silently (every build/pull leaves a layer behind) and "which image is eating my disk, and is anything still using it" is the #1 thing users want answered.

**List columns**
- Repository:Tag (dangling images shown as a muted "`<none>`" badge, not blank)
- Image ID (short SHA, copyable)
- Created (relative time, sortable — "3 weeks ago")
- Size
- **Containers** (count) — the number you specifically asked for
- Status badge, derived client-side: **In use** (≥1 container, any state) / **Unused** (tagged, 0 containers) / **Dangling** (untagged, 0 containers)

Container count + status come from cross-referencing `docker image ls` with `docker ps -a` (or from `docker system df -v`, which reports both size and container-links per image in one call — worth using here since it avoids a second round trip).

**Filters:** free-text on repo/tag, plus a status dropdown (All / In use / Unused / Dangling) — same interaction pattern as the Containers status filter, applied client-side against the last fetch.

**Actions**
- **Remove** — blocked with a clear inline reason if the image is in use ("Used by 2 containers: web, worker"), rather than letting the raw docker error surface. A secondary **Force** checkbox (unchecked by default, styled as a warning) allows `docker image rm -f`, but the dialog should still name what will happen: force-removing an image behind a *running* container will fail regardless, so the dialog should distinguish "used by stopped containers" (force is enough) from "used by running containers" (force won't help — tell the user to stop the container first).
- **Remove unused / Prune** — a distinct bulk action from single-row remove. Opens a confirm dialog showing *count* and *total reclaimable size* before running, with a checkbox: "Include unused tagged images, not just dangling" (maps to `docker image prune` vs `docker image prune -a`). This preview step matters — pruning is the one action users are most nervous about, so show them the bill before they pay it.
- **Inspect** — opens a read-only panel/dialog with the raw `docker inspect` JSON (layers, env, entrypoint, labels). Cheap to add, high value for debugging "why does this container behave differently."
- **History** (nice-to-have) — `docker history`, a simple layer-by-layer size breakdown, answers "why is this image 2GB."
- **Copy ID / Copy full reference** — context-menu convenience.

**Cross-navigation:** clicking the Containers count badge switches to the Containers page pre-filtered to that image name — mirrors how a user's mental model already works ("show me who's using this").

---

## 2. Volumes

**Why it matters:** volumes are the most common source of "docker is full" surprises because they're invisible from `docker ps` and don't get cleaned up when a container is removed (unless `-v` was passed).

**List columns**
- Name
- Driver (almost always `local`, but show it — matters for anyone using plugins)
- Mountpoint (host path)
- Size (from `docker system df -v`; flag this column as slightly stale/expensive since Docker doesn't index volume size cheaply — a "calculating…" state or a manual "Refresh sizes" affordance is reasonable rather than fetching it on every load)
- **Containers attached** (count) — again, needs correlating volume names against containers' mounts, not a single flag from `docker volume ls`
- In use / Unused badge

**Filters:** name filter, driver filter, "Unused only" checkbox (the one users actually reach for before pruning).

**Actions**
- **Remove** — blocked with the attached container names listed if in use, same "explain, don't just fail" pattern as Images.
- **Prune unused volumes** — confirm dialog with count + reclaimable size preview, same as Image prune. Call out explicitly that this removes *named* volumes too (not just anonymous ones), since that surprises people coming from `docker volume prune`'s default behavior.
- **Inspect** — raw JSON (mountpoint, labels, options).
- **Open mountpoint in file manager** — since this is a *desktop* app with host filesystem access (unlike a container shelling into itself), this is a genuinely differentiated action: right-click → "Open in file manager" / "Copy path." Worth calling out as a nice platform-native touch that a purely CLI/web tool couldn't offer as easily.

---

## 3. Networks

**Why it matters:** less about disk space, more about "why can't container A reach container B" — networking is the thing users can't inspect at a glance from `docker ps`.

**List columns**
- Name
- Network ID
- Driver (bridge / host / overlay / macvlan / none)
- Scope (local / swarm)
- **Containers attached** (count)
- Subnet / Gateway (from IPAM config — genuinely useful for connectivity troubleshooting, worth the extra `inspect` call per row or a lazy-loaded detail panel)

**Filters:** name filter, driver filter.

**Actions**
- **Remove** — the three predefined networks (`bridge`, `host`, `none`) should have the Remove action disabled/greyed out with a tooltip explaining why, rather than letting the user hit a docker error.
- **Prune unused networks** — same confirm-with-count pattern as the other two.
- **Inspect / Connected containers detail** — expand a row (or side panel) to list each attached container with its assigned IP on that network — this is the actual troubleshooting payoff of a Networks screen.
- **Disconnect container** (nice-to-have) — an action available from the "connected containers" detail list, per container, for `docker network disconnect`.

---

## 4. Disk Usage (system dashboard)

This is the page that ties the other three together and mirrors `docker system df -v`. It's not strictly required (the per-resource pages already show size), but it earns its place as the *first stop* for "why is my disk full" before drilling into a specific resource type.

**Layout:** four summary tiles — Images, Containers, Volumes, Build Cache — each showing Total count / Active count / Size / Reclaimable size. This is the dashboard-style entry point; clicking a tile navigates to that resource's page (pre-filtered to "unused" where applicable).

**Actions**
- **Prune everything** — the `docker system prune` equivalent, presented as checkboxes per resource type (Images, Volumes, Networks, Build cache) so the user opts into exactly what gets swept, with a live-updating total reclaimable estimate as they toggle checkboxes. This is the single highest-leverage action for the "reclaim disk space" use case, and putting it behind a granular, previewed confirm dialog is the guardrail that makes it safe to offer at all.

---

## Cross-cutting UX patterns (apply to all three new pages)

- **"Explain before it fails" for blocked removes.** Docker's own CLI error ("container is using its referenced image") is accurate but unhelpful in a GUI. Every remove action should proactively check usage and name the blockers in the confirm dialog rather than round-tripping to a raw stderr message.
- **Prune = preview, not just confirm.** Every prune action (Images/Volumes/Networks/System) should show *what* and *how much* before executing — a plain "Are you sure?" isn't enough for a bulk destructive action.
- **Optimistic updates on success**, consistent with how Containers already handles stop/remove: mutate the loaded list in place rather than forcing a full reload round-trip.
- **Auto-refresh is off by default here too, and arguably less needed.** Unlike container CPU/mem (which goes stale in seconds), image/volume/network lists only change when the user (or another docker client) acts — a manual Refresh button is probably sufficient, and I'd skip the timer entirely on these three pages rather than add the checkbox out of habit. Keep it only on Disk Usage if you want the reclaimable totals to stay live while the user is deciding what to prune.
- **Inspect as a shared pattern.** A generic "raw JSON inspect panel" is useful on all four new pages (and arguably retroactively on Containers) — worth designing once as a reusable dialog rather than four bespoke ones.
- **Filtering stays client-side**, same rule as Containers: one CLI call per manual refresh, filter/search boxes just re-render from the cached list.

## Suggested phasing

1. **MVP:** Images + Volumes + Networks list pages with columns/counts as above, single-item Remove (with usage-aware blocking), Inspect.
2. **Phase 2:** Prune actions with size-preview confirm dialogs (the highest-value but highest-care feature).
3. **Phase 3:** Disk Usage dashboard + cross-navigation between pages + "Open in file manager" for volumes.
4. **Backlog / stretch:** Image History (layers), Network disconnect-container action, extending the Containers filter model to filter by volume/network (so the cross-navigation story is fully bidirectional).



p2
ok, let's implement the "images" main screen. 

p22
in "images" screen, when user removes an image, the confirmation modal should also let the user to choose remove this image, all its containers and associated containers, volumes, networks, etc. The idea is that they can just remove the image, but also alternatively remove all associated resources to that image to release the most space 

p3
in "volumes" main screen, add a column "size" which is calculated re-using the solution implemented for "containers disk" which is able to calculate disk space of different volumes

p33
in "volumes" main screen, there's a column with the container(s) id using the volume

p5
on the sidebar, the current screen must be highlighted
---


# volumnes view image or container
question  (don't write code), in the volumes view, is it possible to display a column indicating which containers or images are associated with it. The idea is to have better notion of what the volume is used for, right now the names of most of my containers are just hashes so i don't have a clue of how it's being used or by whom. 

p2
please add the two columns "used by" and "images". Remember that I want to see the names of containers and images if possible, if not a small hash.

# containers start
in the containers screen, add a "start" button to start selected container

# container details
in the containers screen, if user select a container, there will be a "info" button which displays the "container details" screen.
This screen will detail everything relevant data of an existing container, for example, ports, mem, disk, cpu, etc. 
Put the container details code in a separate reusable component since we will be calling it from different places of the app.