# Photo Sorter — Requirements Specification

## Problem Statement

50,000+ personal photos are stored across disorganized OneDrive folders accumulated
over many years from digital cameras, thumb drives, and phone backups. There are many
duplicates and no consistent organization. Some photos have full EXIF metadata including
GPS; others have only a date; scanned old prints have neither.

## Goals

1. Organize photos into two parallel folder hierarchies in OneDrive
2. Detect and quarantine duplicates for manual review before deletion
3. Maintain a database to support future incremental imports (new thumb drives, cameras)
4. Run entirely on an Azure VM using the Microsoft Graph API — no bulk download to laptop

---

## Architecture

### Why Azure VM + Graph API

- 50,000+ files makes local processing impractical
- Graph API provides file hash (`quickXorHash`), photo metadata (`takenDateTime`,
  `geoCoordinates`, resolution, camera make/model) without downloading the file
- File moves within OneDrive via Graph API are server-side (no data transfer)
- Only suspected duplicate pairs are downloaded for pixel-level confirmation

### Components

| Component | Technology |
|---|---|
| OneDrive access | Microsoft Graph API (Python `msal` + `requests`) |
| Metadata & dedup | Graph API `photo` and `file` facets |
| Duplicate confirmation | Download sample pair, pixel-level compare (`Pillow`) |
| Reverse geocoding | `reverse_geocoder` (offline, no API key needed) |
| Database | SQLite via Python `sqlite3` |
| Source control | GitHub — `sedawkins/photo-sorter` |

### Authentication

- Azure App Registration: `photo-sorter` (client ID: `d6ed1d2f-5172-4873-9c89-c147c354374d`)
- Account type: Personal Microsoft account
- Flow: Device code flow (browser prompt on first run, token cached locally)
- Permissions: `Files.Read.All`, `Files.ReadWrite.All`, `User.Read`

---

## Folder Hierarchies

All output goes into a new OneDrive root folder: `/Photos/Sorted/`  
Original folders are **not touched** until the user manually verifies and deletes them.

### Primary hierarchy
```
/Photos/Sorted/Primary/
    {Year}/
        {Month}/
            {State}/{City}/     ← US photos with GPS
            {Country}/{City}/   ← Non-US photos with GPS
            Other/              ← Photos with date but no GPS
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
Shadow only contains photos that have GPS — no `Other` or `Unsorted` branches.

### To Be Discarded
```
/Photos/Sorted/ToBeDiscarded/   ← Confirmed duplicate losers
```

---

## Duplicate Handling

1. **Detection:** group files by `quickXorHash` from Graph API (no download)
2. **Confirmation:** for each duplicate group, download one pair and do pixel-level
   comparison with Pillow to confirm they are truly identical
3. **Winner selection** (in priority order):
   - Highest resolution (width × height)
   - Most complete metadata (GPS > date only > neither)
   - Largest file size as final tiebreaker
4. **Disposition:** losers are moved to `/Photos/Sorted/ToBeDiscarded/`; winner is
   organized into the primary and shadow hierarchies normally

---

## Database Schema

SQLite file: `photo_sorter.db` (kept on the VM, checked into `.gitignore`)

```sql
CREATE TABLE photos (
    id              INTEGER PRIMARY KEY,
    hash            TEXT NOT NULL,
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
    status          TEXT,          -- 'organized', 'discarded', 'unsorted'
    processed_at    TEXT           -- ISO 8601 timestamp
);

CREATE UNIQUE INDEX idx_hash ON photos(hash);
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

## Incremental Import (Future Thumb Drives)

1. Scan new source files, compute or retrieve hash
2. Check hash against database — skip if already present
3. Process only new files through the same pipeline
4. This works even if the new source is a local drive (hash computed locally)

---

## Phased Implementation Plan

### Phase 1 — Graph API connection & metadata scan
- Authenticate via device code flow
- Walk OneDrive source folders, retrieve metadata for every photo
- Write all metadata to the database (no files moved yet)
- Output a summary report: total photos, date coverage, GPS coverage, duplicate count

### Phase 2 — Duplicate confirmation
- For each hash group with >1 file, download one pair and pixel-compare
- Mark confirmed duplicates in the database

### Phase 3 — Organization
- Create `/Photos/Sorted/` folder structure in OneDrive via Graph API
- Move winners into primary and shadow hierarchies (server-side moves)
- Move losers into `ToBeDiscarded/`

### Phase 4 — Incremental import
- CLI command to scan a new source folder or local drive
- Check hashes against DB, process only new files

---

## Out of Scope

- Manual tagging of `Unsorted/` photos (user does this later)
- Modifying or rewriting EXIF metadata
- Any deletion — user empties `ToBeDiscarded/` manually after verification
