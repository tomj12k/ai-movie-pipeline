# DaVinci Resolve on the MacBook Air — proxy-first setup

The M3 Air edits from **local proxies only**. Raw renders stay on the NAS;
`studio sync-local` pulls them and builds 720p H.264 proxies on the internal
SSD. Nothing heavy ever streams over the 1GbE wire during editing.

## 1. Cache and proxy locations (once per machine)

Resolve stores these in its project settings database, not an editable file —
set them once and every project made from the studio template inherits them
(`resolve_project_settings.xml` in this folder mirrors the values for
reference/import):

- Preferences → Media Storage: add `/Users/Shared/Resolve_Cache` as the first
  scratch location.
- Project Settings → Master Settings → Working Folders:
  - Cache files location:    `/Users/Shared/Resolve_Cache/CacheClip`
  - Gallery stills location: `/Users/Shared/Resolve_Cache/.gallery`
  - Proxy media location:    `/Users/<you>/StudioProxies`
- Project Settings → Master Settings → Optimized Media and Render Cache:
  - Proxy media resolution: Half (or Quarter on battery)
  - Proxy media format: H.264
- Playback → Use Proxy Media if Available: **on**

## 2. Linking media

1. `studio sync-local --project <name>` → proxies land in
   `~/StudioProxies/<name>/proxy/`, originals in `~/StudioProxies/<name>/raw/`.
2. Import from `raw/` (these files are local too, so relinking to the NAS copy
   is never needed mid-edit).
3. Right-click clips → Proxy Media → link to the matching file in `proxy/`
   (Resolve auto-links when filenames match; proxies carry the `_proxy` suffix
   Resolve recognizes).

## 3. Shared project library (both editing machines see one timeline set)

The studio's shared PostgreSQL 13 library runs on the Spark:

- Host: `spark-d1a9.local`  Port: `5432`
- Database: `resolve_studio`  User: `resolve`
- Password: on the Spark in `~/.config/studio/resolve-db.env`

In Resolve: Project Manager → ⚙ → Connect to Database → PostgreSQL, enter the
values above. Create your projects inside this library and every machine on
the LAN opens the identical timelines. Nightly dumps go to
`Portfolio_Archive/resolve-db-backups/` automatically.
