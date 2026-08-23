# DaVinci Resolve on the MacBook Air — proxy-first setup

The M3 Air edits from **local proxies only**. Raw renders stay on the NAS;
`studio sync-local` pulls them and builds 720p H.264 proxies on the internal
SSD. Nothing heavy ever streams over the 1GbE wire during editing.

Two one-time setups: **A** the shared project library (where timelines live),
**B** the proxy/cache folders (where media reads from). Then a short
per-project routine.

## A. Connect the shared project library (PostgreSQL on the Spark)

A "project library" is the database Resolve keeps projects and timelines in.
By default it is a private local one. Ours runs on the Spark, so every
machine opens the same projects.

1. **Get the database password** (generated on the Spark, never committed):
   ```bash
   ssh pizzacat@spark-d1a9.local cat '~/.config/studio/resolve-db.env'
   ```
2. Open Resolve → **Project Manager** (house icon, bottom-right of any page).
3. Click **Project Libraries** in the top-left of the Project Manager
   (older Resolve 18: the database sidebar icon).
4. Click **Add Project Library** → choose the **Create** tab (first time
   only — the server is empty until Resolve initializes it):
   - **Name:** `resolve_studio_lib`
   - **Type / Location:** Network / PostgreSQL
   - **Host:** `spark-d1a9.local`
   - **Username:** `resolve`
   - **Password:** the `RESOLVE_DB_PASS` value from step 1
5. Click **Create**. Resolve builds its schema on the Spark's PostgreSQL 13
   and the library appears in the sidebar. Select it — new projects now save
   there.
6. Any other editing machine repeats steps 2–4 but uses the **Connect** tab
   with the same values.

Nightly `pg_dumpall` backups land in
`Portfolio_Archive/resolve-db-backups/` automatically (03:00 timer on the
Spark).

## B. Point Resolve at local cache + proxies (once per machine)

1. **Preferences** (Cmd-,) → **Media Storage**:
   - Add `/Users/Shared/Resolve_Cache` and move it to the **top** (first
     entry = scratch disk).
   - Add `/Users/<you>/StudioProxies`.
   - Restart Resolve when prompted.
2. Open (or create) a project in the shared library → **Project Settings**
   (gear icon) → **Master Settings** → **Working Folders**:
   - **Proxy generation location:** `/Users/<you>/StudioProxies`
   - **Cache files location:** `/Users/Shared/Resolve_Cache/CacheClip`
   - **Gallery stills location:** `/Users/Shared/Resolve_Cache/.gallery`
3. Same page, **Optimized Media and Render Cache**: format H.264 (or
   ProRes 422 Proxy), resolution Half.
4. Menu bar → **Playback** → **Proxy Handling** → **Prefer Proxies**.

## C. Per project (after `studio sync-local --project <name>`)

1. Media page → import clips from `~/StudioProxies/<name>/raw/`
   (local copies of the NAS originals — never import from the mounted share).
2. Select the clips → right-click → **Proxy Media** → **Link Proxy Media…**
   → pick the matching file in `~/StudioProxies/<name>/proxy/`
   (`*_proxy.mp4`). Repeat when new shots arrive.
3. Edit at 1920×1080/24. Deliver the master to
   `~/StudioMounts/Portfolio_Archive/<name>/` — that writes it straight to
   the NAS archive.

## Notes

- `raw/` files are also local, so Resolve never relinks across the network
  mid-edit; the proxies just keep playback light on the Air.
- If the timeline stutters anyway: Playback → Render Cache → **Smart** (the
  cache lives on the local scratch disk from step B).
- `resolve_project_settings.xml` in this folder mirrors these values for
  reference.
