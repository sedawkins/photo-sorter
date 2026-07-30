# Photo Sorter — Requirements Specification

## Problem Statement

50,000+ personal photos are stored across disorganized OneDrive folders accumulated
over many years from digital cameras, thumb drives, and phone backups. There are many
duplicates and no consistent organization. Some photos have full EXIF metadata including
GPS; others have only a date; scanned old prints have neither.

## Goals

1. Organize photos into two parallel folder hierarchies in OneDrive
2. Detect and skip duplicates, logging them for reference
3. Maintain a database to support future imports (new thumb drives, cameras, phones)
4. Run entirely on an Azure VM using the Microsoft Graph API — no bulk download to laptop

---

## Architecture

### Why Azure VM + Graph API

- 50,000+ files makes local processing impractical
- Graph API provides file hash (`quickXorHash`), photo metadata (`takenDateTime`,
  `geoCoordinates`, resolution, camera make/model) without downloading the file
- File copies within OneDrive via Graph API are server-side (no data transfer)
- Only a small random sample of duplicate pairs are downloaded for spot-check confirmation

### Components

| Component | Technology |
|---|---|
| OneDrive access | Microsoft Graph API (Python `msal` + `requests`) |
| Metadata & dedup | Graph API `photo` and `file` facets (`quickXorHash`) |
| Duplicate spot-check | Download ~12 random pairs, pixel-level compare (`Pillow`) |
| HEIC conversion | `pillow-heif` plugin (winner files only, after dedup) |
| Reverse geocoding (GPS) | `reverse_geocoder` (offline, no API key needed) |
| Forward geocoding (manual tags) | Nominatim / OpenStreetMap (free, 1 req/sec, `geocode.py`) |
| AI keyword tagging | Claude Haiku vision (`describe_photos.py`) — comma-separated tags stored in `ai_description` |
| US state normalization | `us_states.py` — maps "CA"/"california"/etc. → canonical name + country="US" |
| Database | SQLite via Python `sqlite3` |
| Logging | Log file per run, stored in OneDrive `_system/` |
| Persistence | OneDrive `_system/` folder (DB, token cache, config, logs) |
| VM setup | Bash setup script — VM is fully stateless |
| Source control | GitHub — `sedawkins/photo-sorter` |

### Authentication

- Azure App Registration: `photo-sorter` (client ID: `d6ed1d2f-5172-4873-9c89-c147c354374d`)
- Account type: Personal Microsoft account
- Flow: Device code flow (browser prompt on first run, token cached locally)
- Token refresh: `GraphClient` accepts a `token_refresher` callback and silently retries
  401s — handles the 1-hour MSAL token expiry during long runs
- Permissions: `Files.Read.All`, `Files.ReadWrite.All`, `User.Read`

### VM Lifecycle — Stateless Design

The Azure VM holds no persistent state. Everything lives in OneDrive under `_system/`.

**Startup sequence (handled by setup script):**
1. Install system packages (`python3`, `git`, `libheif-dev`, `sqlite3`) and Python dependencies
2. Clone latest source from GitHub (`sedawkins/photo-sorter`)
3. Authenticate via device code flow to get initial Graph API token
4. Download `_system/` contents from OneDrive to the VM (DB, token cache, config)
5. Run the app

**Shutdown sequence:**
1. Upload updated DB, token cache, and run logs back to `_system/` in OneDrive
2. VM can be deallocated — nothing is lost

### System Folder Structure

```
~/photo-sorter/_system/          ← on VM (synced to/from OneDrive on setup/teardown)
    photo_sorter.db              ← SQLite database
    token_cache.json             ← cached Graph API auth token
    config.json                  ← source paths, run settings
    runs/
        2026-06-19_17-30-40_scan.log
        duplicates_skipped_2026-06-21_04-10-42.log
        ...
```

> **Security note:** `config.json` contains auth credentials. The `_system/` folder
> is protected by your OneDrive account access controls and Microsoft's encryption
> at rest. Do not share this folder.

---

## Importing New Photos

To import photos from a new source (old thumb drive, camera card, phone backup):
1. Upload the folder to OneDrive
2. Run `scan.py` pointed at that folder
3. Run `organize.py` — the DB dedup automatically skips anything already organized

No special incremental import code is needed. The additive DB and hash-based dedup
handle it transparently.

---

## Folder Hierarchies

All output goes into a new OneDrive folder: `/Photos/Sorted/`
Source folders are **never modified** — originals stay in place throughout.

### Primary hierarchy
```
/Photos/Sorted/Primary/
    {Year}/
        {Month}/
            {State}/{City}/     ← US photos with GPS
            {Country}/{City}/   ← Non-US photos with GPS
            Other/              ← Photos with date but no GPS
        Movies/                 ← Video files for that year (Phase 2)
    {Country}/{City}/Movies/    ← Video files for that location (Phase 2)
/Photos/Sorted/Unsorted/        ← Photos with no date and no GPS
```

### Shadow hierarchy
```
/Photos/Sorted/Shadow/
    {State}/{City}/             ← US
    {Country}/{City}/           ← Non-US
        {Year}/
            {Month}/
```
Shadow contains GPS-tagged photos (via organize.py) and manually-tagged photos
(via retag.py). No `Other` or `Unsorted` branches.
Shadow entries are full copies (shortcut migration is a future enhancement).

### Folder description files

Each destination folder gets a `_description.txt` file written alongside the photos,
populated automatically from the source folder name(s) of photos in that folder
(e.g. if photos came from "Amsterdam 2014" and "Europe Trip", both are listed).

---

## Duplicate Handling

### Detection (no download required)
Duplicates are identified by `quickXorHash` — catches exact file duplicates across folders.

**Known limitation:** Dropbox migration and some photo apps re-save JPEGs with slightly
different bytes (e.g. 2-byte metadata difference), producing a different hash for what
is visually the same photo. These near-duplicates pass through hash dedup.

**Soft duplicate detection (Phase 2):** Flag photos where `taken_date`, `camera_make`,
`camera_model`, and file `size` all match but hashes differ. These are near-duplicates
for review.

### Filename collision detection
Multiple cameras often produce the same filename (e.g. `IMG_1234.JPG`). When two
different photos would land in the same destination folder with the same name,
`unique_filename()` appends a 6-character alphanumeric hash suffix to the second file
(e.g. `IMG_1234_aB3xKp.JPG`). Collision rate observed at ~10% across a 39k photo run.

### Spot-check confirmation
- Randomly select ~12 duplicate pairs from the full set
- Download those pairs and do a pixel-level comparison with Pillow
- Validates detection logic before processing all files
- Abort if any pair fails pixel comparison

### Winner selection (in priority order)
1. Highest resolution (width × height)
2. Most complete metadata (GPS > date only > neither)
3. Largest file size as final tiebreaker

### Disposition
- **Winner:** copied into the primary and shadow hierarchies
- **Losers:** skipped (not copied anywhere); logged in a `duplicates_skipped_*.log` file
  with original path, winner path, and hash

---

## Image Format Handling

- **HEIC files:** converted to JPEG at copy time (winners only, after dedup)
- **Video files (.mov, .mp4, .avi, .mkv):** organized into `Year/Movies/` and
  `Location/Movies/` folders (Phase 2) — currently skipped
- **Non-image files (.nar, .tln, etc.):** silently skipped by `scan.py` (no
  recognized image/movie extension) — has no effect on production runs
- **`Data.noindex` folders:** skipped entirely during scan (see
  `SKIP_FOLDER_NAMES` in `graph.py`) — this is iPhoto's internal preview
  cache and its files carry a real `.jpg` extension, so it needs an explicit
  folder-name exclusion rather than an extension check. See "Source cleanup
  utility" below.
- All other formats (JPG, PNG, TIFF, BMP, WebP) are copied as-is

---

## Known Edge Cases

- **Folder names with special characters:** OneDrive rejects `\ / : * ? " < > |` in
  folder names. Location names from `reverse_geocoder` are sanitized with
  `_INVALID_FOLDER_CHARS` regex before use.
- **Airplane photos:** GPS coordinates reflect the ground below the flight path —
  photos may appear in unexpected location folders. Harmless.
- **Rotated copies:** Some apps rotate JPEG pixels rather than setting EXIF orientation,
  producing a different hash for what is visually the same photo. Both copies are kept.
- **Counter-reset filenames:** Nikon `DSCN_xxxx` and similar schemes reset at 9999,
  so the same filename can refer to completely different photos from different years.
  Filename collision detection handles this correctly.

---

## Database Schema

SQLite file: `photo_sorter.db` (stored in `_system/`, never committed to git)

```sql
CREATE TABLE photos (
    id                 INTEGER PRIMARY KEY,
    hash               TEXT NOT NULL,       -- quickXorHash (unique per content)
    image_unique_id    TEXT,                -- reserved for future use
    new_path           TEXT,                -- destination path after organizing
    filename           TEXT NOT NULL,
    taken_date         TEXT,                -- ISO 8601 from EXIF takenDateTime
    year               TEXT,
    month              TEXT,                -- full month name e.g. "June"
    latitude           REAL,
    longitude          REAL,
    city               TEXT,
    state_or_region    TEXT,
    country            TEXT,
    width              INTEGER,
    height             INTEGER,
    camera_make        TEXT,
    camera_model       TEXT,
    folder_description TEXT,               -- auto-extracted from source folder name
    user_description   TEXT,               -- free text added manually
    status             TEXT,               -- 'scanned', 'organized', 'soft_duplicate', 'trashed'
    processed_at       TEXT,               -- ISO 8601 timestamp
    media_type         TEXT,               -- 'photo' or 'movie'
    file_size          INTEGER,            -- bytes, used for soft duplicate detection
    tagged_city        TEXT,               -- user-assigned city (tagger UI, pre-retag)
    tagged_country     TEXT,               -- user-assigned country (tagger UI, pre-retag)
    ai_location_hint   TEXT,               -- Claude Haiku location hint from thumbnail scan
    ai_description     TEXT                -- Claude Haiku short description of sample photos
);

CREATE TABLE photo_occurrences (
    id                 INTEGER PRIMARY KEY,
    hash               TEXT NOT NULL,       -- links to photos.hash
    original_path      TEXT NOT NULL,       -- full OneDrive path of this copy
    folder_description TEXT,               -- source folder name for this occurrence
    is_winner          INTEGER DEFAULT 0,   -- 1 if this occurrence was chosen as winner
    scanned_at         TEXT                -- ISO 8601 timestamp
);

CREATE UNIQUE INDEX idx_hash ON photos(hash);
CREATE INDEX idx_status ON photos(status);
CREATE INDEX idx_occurrence_hash ON photo_occurrences(hash);
CREATE UNIQUE INDEX idx_occurrence_path ON photo_occurrences(original_path);
```

---

## Metadata Resolution

| Condition | Primary path | Shadow path |
|---|---|---|
| Date + GPS (US) | `Year/Month/State/City` | `State/City/Year/Month` |
| Date + GPS (non-US) | `Year/Month/Country/City` | `Country/City/Year/Month` |
| Date, manually tagged | `Year/Month/State/City` (via retag.py) | `State/City/Year/Month` (via retag.py) |
| Date, no GPS | `Year/Month/Other` | *(not added to shadow)* |
| No date, no GPS | `Unsorted/` | *(not added to shadow)* |

---

## Logging

Every run produces a log file uploaded to `_system/runs/` after the run completes —
permanent record of every file processed, skipped, converted, or errored.
Duplicate skips are written to a separate `duplicates_skipped_*.log` file.

Email summary on run completion is planned (Phase 2 backlog).

---

## Phased Implementation Plan

### Phase 1 — Core pipeline ✅ COMPLETE

- **Phase 0:** VM setup/teardown scripts (`setup.sh`, `teardown.sh`)
- **Phase 1:** Graph API metadata scan (`scan.py`) — walks source folders, writes DB
- **Phase 2:** Duplicate spot-check (`spotcheck.py`) — pixel-compare random sample
- **Phase 3:** Organization (`organize.py`) — server-side copy into hierarchy

**Production run completed 2026-06-21:**
- 39,342 images scanned from `/Pictures` (139.4 GB)
- 11,812 duplicate groups identified (28,666 duplicate files)
- 22,488 unique photos organized into `/Photos/Sorted`
- Azure cost: ~$4.49 total

---

## Lessons Learned — Phase 1 Production Run

Observations from scanning 39,342 photos and organizing 22,488 unique images that
inform Phase 2 design decisions.

### Duplicate landscape
- **73% of files were duplicates** (28,666 out of 39,342) — years of copying to thumb
  drives, Dropbox migrations, and folder reorganizations created massive redundancy
- **~10% filename collision rate** — `IMG_xxxx`, `IMAG_xxxx`, and `DSCN_xxxx` naming
  schemes repeat across cameras and years; collision detection was essential
- **Dropbox migration near-duplicates** — Dropbox re-saved some JPEGs with slightly
  different bytes (typically 2 bytes), producing different hashes for visually identical
  photos; these pass through hash dedup and appear as collision pairs in the output

### Metadata quality
- **20% of photos had full GPS metadata** (7,653 of 39,342) — mostly iPhone photos
- **65% had date only** — point-and-shoot cameras, older DSLRs
- **15% had no metadata** — scanned prints, some very old cameras
- **Airplane photos** geo-locate to the ground below the flight path; these land in
  unexpected city folders but are harmless
- **Old slide scans** — the source folder names (written on slide trays by parents)
  are the only location metadata; these sort into `Other/` but folder descriptions
  are preserved in the DB for the location tagging utility

### Performance
- **~4-7 seconds per photo** for the organize pass — bottleneck is OneDrive's
  server-side copy API, not VM CPU or network
- Performance improved overnight (~4 sec) vs. daytime (~7 sec) — Microsoft server load
- **$4.49 total Azure cost** for the full production run (scan + organize of 39k photos)
- Sustained 30+ hour run triggered soft throttling; 2-3x parallelization may help
  but risks harder rate limiting — test carefully

### OneDrive API behavior
- **`conflictBehavior: replace` is ignored** on personal OneDrive copy operations —
  workaround: delete destination file first, then copy
- **Copy monitor URL is self-authenticating** (tempauth token in URL) — do NOT pass
  Bearer token or it conflicts and causes 401
- **Paths with special characters** (apostrophes, etc.) must be URL-encoded with
  `urllib.parse.quote(path, safe='/')` — OData `''` doubling is wrong for path segments
- **MSAL tokens expire after 1 hour** — `GraphClient` must silently refresh and retry

### Organization quality
- **Geo hierarchy is highly browsable** — location folders like `California/Los Angeles/`
  make it easy to find photos by trip or time period
- **`_description.txt` files** from source folder names provide useful context
  (e.g. "India2009", "Brian-Tucker") alongside the organized photos
- **Rotated copies** (same photo, pixel-rotated by different apps) have different hashes
  and both get organized — harmless but worth noting for future perceptual dedup
- **Counter-reset filenames** (`DSCN0460` appearing in both 2009 and 2010 folders as
  completely different photos) are handled correctly by collision detection

---

### Phase 2 — Enrichment & Discovery (PLANNED)

#### 2a. Movie file handling
- Extend `scan.py` to include `.mov`, `.mp4`, `.avi`, `.mkv` files
- Add `media_type` column to DB (`photo` vs `movie`)
- Route movies to `Year/Movies/` (primary) and `Location/Movies/` (shadow)
- No format conversion — copy as-is

#### 2b. Soft duplicate detection
- Second dedup pass: group photos by `(taken_date, camera_make, camera_model, size)`
- Flag hash-different but metadata-identical photos as near-duplicates for review
- Catches Dropbox re-saves and other re-encoding artifacts

#### 2c. Shadow shortcuts
- Replace full file copies in Shadow hierarchy with OneDrive shortcuts (remoteItem)
- Saves ~30 GB of storage (Shadow currently mirrors Primary for all GPS photos)
- Shortcuts are deleted automatically when source is deleted — no orphan problem
- Write a one-time migration utility to convert existing Shadow copies to shortcuts

#### 2d. Location tagging web app (Vercel) ✅ DONE (2026-07-22)

Three-view SPA deployed to Vercel; backend FastAPI (`tagger/server.py`) runs on the Azure VM.
See `GUI_SPEC.md` for full design and architecture.

**What was built:**
- Date View: year → month → photo grid with location section headers and lightbox
- Location View: ranked location list → year/month drill-in → photo grid
- Tag View: clump list with two-pass clustering, clump detail with tagging form and AI scan
- AI scan via Claude Haiku: sends up to 5 disk-cached thumbnails, returns location hint +
  short description; cached on all photos in the clump (`ai_location_hint`, `ai_description`)
- Selective AI scan: hover/long-press overlay on each thumbnail with "🤖 Scan" / "↗ Open"
  buttons; user picks which specific photos to send to AI
- iPhone long-press (500ms) to show the hover overlay on touch devices
- Disk thumbnail cache at `_system/thumb_cache/{md5}.jpg` — avoids Vercel's 10s timeout
  (fetching 5 thumbnails live from Graph API took ~15s)
- Trash clump action: sets `status='trashed'` on all photos in clump

**`retag.py` utility** — moves tagged-clump photos from `Year/Month/Other/` into their
location folders in OneDrive via server-side Graph API copy + delete, then updates the DB.
Dry-run by default; `--execute` to move. Handles partial prior runs by checking the
destination before giving up on a missing source.

#### 2e. AI keyword tagging ✅ DONE (2026-07-30)

`describe_photos.py` — batch AI tagging via Claude Haiku vision API.

- Sends each photo's thumbnail to Haiku; stores comma-separated tags in the `ai_description` column
- Tags cover: animal breeds (e.g. "yellow Labrador Retriever"), people descriptors ("elderly man",
  "teenage boy"), setting ("beach", "ski slope", "restaurant"), activities ("birthday party",
  "graduation", "skiing"), landmarks, and season/condition
- Args: `--year`, `--path`, `--limit`, `--refill` (re-describe already-tagged photos)
- Skips photos that already have `ai_description` — safe to re-run, resumes where it left off
- Movies skipped (thumbnails unreliable for video)
- ~1 sec/photo; 2013 test run: 1,283 described, 0 errors; full archive run started 2026-07-30

#### 2f. Source cleanup utility ✅ DONE (2026-07-04)

`cleanup_kruft.py` scans a Pictures tree and quarantines (never deletes) files
that are confirmed junk — cache/metadata files left behind by iPhoto, iPod
Photo Cache, Windows, and Picasa. It never touches the source photos
themselves; everything lands in a `_KRUFT_QUARANTINE/` folder that mirrors
the original structure, with a CSV manifest of every proposed move. Dry-run
by default; `--execute` required to actually move anything.

**Kruft types identified and quarantined automatically:**
- `.ithmb` — iPod Photo Cache thumbnail blobs (many are 0 bytes)
- `.ipmeta` — iPhoto per-album metadata sidecars
- `.data`/`.db`/`.xml` — iPhoto Library internal DB/segment files, matched by
  an exact filename whitelist (not extension alone — an unrecognized `.db` is
  flagged for review, not assumed safe)
- `.ini`, `.url`, `.lnk`, `.iphoto` — Windows/Picasa folder metadata and shortcuts
- `.thm`/`.tnl`/`.thumb` — Windows Phone/camcorder preview sidecars, but
  **only** when a same-basename real media file exists alongside them
  (proves redundancy; an orphaned sidecar is held for manual review instead)
- `Data.noindex/` folders — see below

**Important finding — `.nar` is NOT junk.** Windows Phone "Rich Capture"
`.nar` files look like cache files but are actually ZIP archives containing
multiple unique JPEG exposures (`NaturalHDR.jpg`, `ArtisticHDR.jpg`,
`EV0.jpg`) that don't exist anywhere else. `cleanup_kruft.py` deliberately
excludes `.nar` from auto-quarantine and always routes it to manual review.

**Important finding — `Data.noindex` leaked into the pipeline's own output.**
iPhoto's `Data.noindex` folder is an internal preview-render cache: every
file in it, including ones with a real `.jpg` extension (e.g. face-detection
crops named `<photo>_faceN.jpg`), is a low-res, no-EXIF derivative of a photo
that already exists in the same Library's `Originals` folder. Because these
carry a real image extension, `scan.py` correctly (by its own rules) treated
them as legitimate photos with no metadata, and because they have no EXIF at
all, soft-duplicate detection (which matches on date/camera) couldn't pair
them with the real original either. Result: **5,155 files (~2 GB) had
accumulated in `Photos/Sorted/Unsorted`** — 90% of everything in that folder
— despite every one of them being a confirmed-redundant derivative of a
photo already organized elsewhere. Verified via DB query (every occurrence
of the affected hashes traced back to a `Data.noindex` path) and spot-checked
8 random samples for a matching real original by filename — all 8 confirmed.
Cleaned up on 2026-07-04: physical files removed, `photo_occurrences` and
orphaned `photos` rows deleted, same pattern as the earlier ghost-record
cleanup. `Unsorted` went from 5,736 rows down to 581 genuinely-unsorted
photos. Fixed at the source: `graph.py`'s `list_photos()` now skips any
folder named `Data.noindex` during the recursive listing
(`SKIP_FOLDER_NAMES`), so a future re-scan can't reintroduce these.

**Safety rules the tool enforces:**
1. A hard whitelist of real media/document extensions is checked first and
   always wins — no kruft rule can override it, *except* the narrow,
   deliberate `Data.noindex` directory-name override described above.
2. Extension name alone is never trusted for ambiguous types — `.db`/`.xml`
   require an exact filename match; sidecars require a paired real file in
   the same folder.
3. A size ceiling per kruft type demotes an oversized "cache" file to manual
   review instead of auto-quarantining it — the same principle that would
   catch a future `.nar`-style surprise even under a known extension.
4. Move-only, manifest-logged, dry-run-by-default, same-volume quarantine —
   nothing is ever deleted by this tool.

#### 2g. Keyword search UI ✅ DONE (2026-07-30)

Search view added to the Vercel SPA as a top-level menu item.

- **Backend:** `search_photos(conn, query, limit, offset)` in `tagger/data.py` — `LIKE` query
  against `ai_description`; returns `(photos, total)`. `GET /api/search?q=&limit=&offset=`
  endpoint in `tagger/server.py`.
- **Frontend:** text input with Enter-key support; results grouped by year; lazy-loaded
  photo grid reusing `renderPhotoGroups`/`lazyLoadThumbs`; "Load more" pagination for
  large result sets (first 50, then +50 per click).
- Searching "retriever" returns all Tucker photos across years; "puppy" will surface
  early Tucker once the full archive is described.

---

### Phase 3 — Additional Sources (PLANNED)

- **iCloud integration:** one-time export from iCloud.com or Mac Photos app into
  OneDrive folder, then standard scan → organize pipeline
- **Other cloud sources:** Google Photos, Dropbox export if needed
- **New thumb drives / cameras:** upload to OneDrive folder, run `scan.py` —
  DB dedup handles the rest automatically, no special code needed

---

## Future Enhancements

- **Parallel copy optimization:** ✅ DONE — `ThreadPoolExecutor(max_workers=3)`,
  per-thread GraphClient + SQLite connection, 429 backoff with `Retry-After`
- **Collision counter:** ✅ DONE — shown in organize summary stats
- **Shadow shortcuts:** replace full Shadow copies with OneDrive shortcuts (~30GB savings)
- **Email summary:** send on run completion via SMTP
- **AI keyword tagging:** ✅ DONE — Claude Haiku vision, tags in `ai_description` column
- **Keyword search UI:** ✅ DONE — Search view in Vercel SPA, `/api/search` endpoint
- **Face recognition:** distinguish specific people (e.g. wife vs. father-in-law);
  keyword search ships first, face recognition is a future phase
- **Auto-describe on import:** add `describe_photos.py` call to `scan.py` so new imports
  are tagged automatically without a separate run
- **iCloud integration:** Apple Privacy export → OneDrive folder → standard scan/organize
- **Soft duplicate review UI:** Vercel — show near-duplicate pairs side by side for confirmation
- **Purge utility:** find and remove orphaned Shadow shortcuts or stale DB entries
