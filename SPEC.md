# Photo Sorter — Requirements Specification

## Problem Statement

50,000+ personal photos are stored across disorganized OneDrive folders accumulated
over many years from digital cameras, thumb drives, and phone backups. There are many
duplicates and no consistent organization. Some photos have full EXIF metadata including
GPS; others have only a date; scanned old prints have neither.

## Goals

1. Organize photos into two parallel folder hierarchies in OneDrive
2. Detect and skip duplicates, logging them for reference
3. Maintain a database to support future incremental imports (new thumb drives, cameras)
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
| Metadata & dedup | Graph API `photo` and `file` facets + EXIF `ImageUniqueID` |
| Duplicate spot-check | Download ~12 random pairs, pixel-level compare (`Pillow`) |
| HEIC conversion | `pillow-heif` plugin (winner files only, after dedup) |
| Reverse geocoding | `reverse_geocoder` (offline, no API key needed) |
| Database | SQLite via Python `sqlite3` |
| Logging | Log file + email summary, both stored in OneDrive `_system/` |
| Persistence | OneDrive `_system/` folder (DB, token cache, config, logs) |
| VM setup | Bash setup script — VM is fully stateless |
| Source control | GitHub — `sedawkins/photo-sorter` |

### Authentication

- Azure App Registration: `photo-sorter` (client ID: `d6ed1d2f-5172-4873-9c89-c147c354374d`)
- Account type: Personal Microsoft account
- Flow: Device code flow (browser prompt on first run, token cached locally)
- Permissions: `Files.Read.All`, `Files.ReadWrite.All`, `User.Read`

### VM Lifecycle — Stateless Design

The Azure VM holds no persistent state. Everything lives in OneDrive under `_system/`.

**Startup sequence (handled by setup script):**
1. Install system packages and Python dependencies
2. Clone latest source from GitHub (`sedawkins/photo-sorter`)
3. Authenticate via device code flow to get initial Graph API token
4. Download `_system/` contents from OneDrive to the VM (DB, token cache, config)
5. Run the app

**Shutdown sequence:**
1. Upload updated DB, token cache, and run log back to `_system/` in OneDrive
2. VM can be deallocated — nothing is lost

**First run (bootstrap):**
No token cache exists yet, so the setup script triggers the device code flow first,
then downloads `_system/` (which will only contain `config.json` at that point).

### System Folder Structure

```
/Photos/Sorted/_system/
    photo_sorter.db        ← SQLite database
    token_cache.json       ← cached Graph API auth token
    config.json            ← SMTP credentials, source paths, run settings
    runs/
        2026-06-17_14-30.log
        2026-06-17_15-45.log
```

> **Security note:** `config.json` contains SMTP credentials. The `_system/` folder
> is protected by your OneDrive account access controls and Microsoft's encryption
> at rest. Do not share this folder. This is appropriate for a personal project
> where you control all access.

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
/Photos/Sorted/Unsorted/        ← Photos with no date and no GPS (manually reviewed later)
```

### Shadow hierarchy
```
/Photos/Sorted/Shadow/
    {State}/{City}/             ← US
    {Country}/{City}/           ← Non-US
        {Year}/
            {Month}/
```
Shadow only contains photos that have GPS — no `Other` or `Unsorted` branches.

---

## Duplicate Handling

### Detection (no download required)
Duplicates are identified by two methods, in order:
1. **`ImageUniqueID`** (EXIF field) — catches iPhone photos that exist as both HEIC and
   JPEG copies of the same shot, even after format conversion
2. **`quickXorHash`** — catches exact file duplicates across any format

### Spot-check confirmation
- Randomly select ~12 duplicate pairs from the full set
- Download those pairs and do a pixel-level comparison with Pillow
- Validates that the detection logic is working correctly before processing all 50k files
- Not intended to confirm every duplicate — hashes are trusted after the spot-check passes

### Winner selection (in priority order)
1. Highest resolution (width × height)
2. Most complete metadata (GPS > date only > neither)
3. Largest file size as final tiebreaker

### Disposition
- **Winner:** copied into the primary and shadow hierarchies
- **Losers:** skipped (not copied anywhere); logged in the run log with original path,
  winner path, hash, and reason for losing

---

## Image Format Handling

- **HEIC files:** converted to JPEG at copy time (winners only, after dedup)
- **Live Photos (iPhone):** `.jpg` is kept; paired `.mov` file is skipped
- All other formats (JPG, PNG, TIFF, BMP, WebP) are copied as-is

---

## Database Schema

SQLite file: `photo_sorter.db` (kept on the VM, listed in `.gitignore`)

```sql
CREATE TABLE photos (
    id              INTEGER PRIMARY KEY,
    hash            TEXT NOT NULL,
    image_unique_id TEXT,          -- EXIF ImageUniqueID (iPhone cross-format dedup)
    original_path   TEXT NOT NULL,
    new_path        TEXT,
    filename        TEXT NOT NULL,
    taken_date      TEXT,          -- ISO 8601
    year            TEXT,
    month           TEXT,
    latitude        REAL,
    longitude       REAL,
    city            TEXT,
    state_or_region TEXT,
    country         TEXT,
    width           INTEGER,
    height          INTEGER,
    camera_make     TEXT,
    camera_model    TEXT,
    status          TEXT,          -- 'organized', 'skipped_duplicate', 'unsorted'
    processed_at    TEXT           -- ISO 8601 timestamp
);

CREATE UNIQUE INDEX idx_hash ON photos(hash);
CREATE INDEX idx_image_unique_id ON photos(image_unique_id);
CREATE INDEX idx_status ON photos(status);
```

---

## Metadata Resolution

| Condition | Primary path | Shadow path |
|---|---|---|
| Date + GPS (US) | `Year/Month/State/City` | `State/City/Year/Month` |
| Date + GPS (non-US) | `Year/Month/Country/City` | `Country/City/Year/Month` |
| Date, no GPS | `Year/Month/Other` | *(not added to shadow)* |
| No date, no GPS | `Unsorted/` | *(not added to shadow)* |

---

## Logging

Every run produces:
- **Log file** uploaded to `/Photos/Sorted/_system/runs/YYYY-MM-DD_HH-MM-SS.log`
  after the run completes — permanent record of every file processed, skipped,
  converted, or errored
- **Email summary** sent on run completion with counts:
  - Photos processed / copied / skipped
  - Duplicates detected and logged
  - HEIC files converted
  - Errors encountered
  - Run duration

Email is sent via SMTP (configurable — Gmail or any provider).

---

## Incremental Import (Future Thumb Drives / iPhones)

1. Point the script at a new source folder (OneDrive subfolder, local thumb drive, etc.)
2. For each file, compute or retrieve hash and check `ImageUniqueID`
3. Skip any file already in the database (by hash or ImageUniqueID)
4. Process only new files through the full pipeline
5. Works for both OneDrive sources (via Graph API) and local drives (hash computed locally)

---

## Phased Implementation Plan

### Phase 0 — VM setup script
- Bash script (`setup.sh`) that fully provisions a fresh Azure Ubuntu VM:
  - Installs Python 3, pip, git, and all required packages
  - Clones `sedawkins/photo-sorter` from GitHub
  - Triggers device code auth flow if no token cache found in OneDrive
  - Downloads `_system/` from OneDrive
- Companion `teardown.sh` that uploads DB, token cache, and log back to OneDrive
- Both scripts checked into the repo so they're available on every fresh VM

### Phase 1 — Graph API connection & metadata scan
- Authenticate via device code flow
- Walk OneDrive source folders, retrieve metadata for every photo
- Write all metadata to the database (no files copied yet)
- Output a summary report: total photos, date coverage, GPS coverage, duplicate count
- **Test against a small sample folder before running on all 50k**

### Phase 2 — Duplicate spot-check
- Randomly select ~12 duplicate pairs
- Download and pixel-compare with Pillow
- Log results; abort full run if spot-check fails

### Phase 3 — Organization
- Create `/Photos/Sorted/` folder structure in OneDrive via Graph API
- Copy winners into primary and shadow hierarchies (server-side copies)
- Convert HEIC winners to JPEG at copy time
- Skip Live Photo `.mov` files
- Log all skipped duplicates with full detail

### Phase 4 — Incremental import
- CLI command to scan a new source (OneDrive subfolder or local drive)
- Check hashes and ImageUniqueIDs against DB, process only new files

---

## Future Enhancement — Location Inference for Pre-GPS Photos

For photos with a date but no GPS (typically older digital cameras):
- Group by date and camera model
- Present a few sample images from each group for manual location identification
- Once a location is tagged, update the database and **move the photos** from
  `Year/Month/Other` into the correct `Year/Month/State/City` position in both
  the primary and shadow hierarchies

---

## Out of Scope

- Manual tagging of `Unsorted/` photos (no date, no GPS — deferred)
- Modifying or rewriting EXIF metadata in original files
- Any deletion — originals are never touched; user cleans up old folders manually
