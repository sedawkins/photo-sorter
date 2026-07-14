# Photo Sorter — GUI Specification

## Purpose

A web application for browsing the organized photo archive and manually assigning
locations to photos that lack GPS metadata. Intended for family use — accessed
occasionally when someone has time to work on the photo galleries.

---

## Mental Model

| Layer | Technology | Persistent? |
|---|---|---|
| Photo files | OneDrive `/Photos/Sorted/` | ✅ Yes |
| Metadata DB | SQLite on Azure VM (synced from OneDrive `_system/`) | ✅ Yes |
| Source code | GitHub `sedawkins/photo-sorter` | ✅ Yes |
| Azure VM | Ephemeral compute — spun up when needed | ❌ No |
| Vercel app | Hosted frontend — always available | ✅ Yes |

The DB lives on the Azure VM, co-located with the OneDrive sync. The VM is the
only component with write access to the DB. Vercel hosts the frontend permanently;
the backend API only needs to be reachable when a family member is actively using
the app (VM is running).

---

## Typical Usage Session

1. Start the Azure VM
2. Open the Vercel app URL (always available, no deploy step)
3. Browse photos, tag unlocated clumps
4. When done: run `organize.py` on the VM to move newly-tagged photos into the geo hierarchy
5. Shut down the VM

---

## Architecture

```
┌─────────────────────┐         ┌──────────────────────────────┐
│   Vercel (always on)│         │   Azure VM (running when used)│
│                     │         │                               │
│   Static SPA        │ HTTPS   │   FastAPI  (tagger/server.py) │
│   HTML/CSS/JS   ────┼────────►│   tagger/data.py  (DB read/  │
│                     │  API    │   write)                      │
│   No server-side    │         │   photo_sorter.db  (SQLite)   │
│   rendering         │         │   OneDrive sync (via Graph)   │
└─────────────────────┘         └──────────────────────────────┘
```

### Why this split

- Vercel is excellent at hosting static frontends — free, always-on, CDN-backed
- The DB and OneDrive access stay on the VM — no sync to an external DB service needed
- `organize.py` already runs on the VM; tagged photos can be re-organized immediately
  without any additional infrastructure

### VM endpoint stability

Azure VMs get a new IP on each deallocation. Solution: enable the free Azure DNS
name on the VM's public IP (`photo-sorter-vm.westus2.cloudapp.azure.com`). This
stays stable across deallocations. The Vercel app stores this as an environment
variable (`VITE_API_BASE` or equivalent).

### Authentication / security

A shared API key protects the backend:
- Generated once, stored as a Vercel environment variable and in the VM's `_system/config.json`
- Every API request from the frontend includes `X-API-Key: <key>` header
- FastAPI middleware rejects requests without the valid key with 401
- The VM's Azure Network Security Group restricts port 8000 to HTTPS only

---

## Three Views

### 1. Date View (calendar icon)

Browse the archive chronologically.

**Year list page:**
- Cards for each year with photo count and a representative thumbnail
- Click a year → Month grid

**Month grid page:**
- 12 month cards showing photo count for that year/month
- Click a month → Photo grid

**Photo grid page:**
- Thumbnails for that year/month, grouped by location subfolder
- Location subfolders shown as section headers (e.g. "California / San Francisco", "Other")
- Click a photo → lightbox view

---

### 2. Location View (map/globe icon)

Browse the archive by place.

**Map + ranked list page:**
- World map with pins sized by photo count (Leaflet, CARTO tiles)
- Ranked list of locations alongside the map
- Click a location → Year/month drill-in

**Location drill-in page:**
- Year/month grid for that location showing photo counts
- Click a month → Photo grid (same as Date View grid, filtered to this location)

---

### 3. Tag View (tag icon) — Phase 2

Assign locations to photos that have a date but no GPS.

**Clump list page:**
- A "clump" = group of photos taken with the same camera within a 3-hour time window
- Only photos currently in `Year/Month/Other` (date but no GPS) are shown
- List sorted by date (newest first), showing: date range, camera, photo count, sample thumbnail
- Minimum clump size: configurable (default 3 photos) — single stray photos skipped

**Clump detail page:**
- Grid of thumbnails for the clump (up to 20 shown, with "show all" option)
- Metadata summary: date range, camera make/model, folder description if available
- Location picker:
  - Type-ahead text search against known cities in the DB (already organized photos)
  - Shows `City, State` (US) or `City, Country` (non-US) suggestions
  - "Other / Unknown" option if location can't be identified
- **Apply** button:
  - Writes `city`, `state_or_region`, `country`, `latitude`, `longitude` to all photos in clump
  - Sets `status = 'tagged'` on those rows
  - Does NOT move files — that happens on the next `organize.py` run on the VM
- **Skip** button: leave clump untagged for now, move to next

**After tagging session:**
- Run `organize.py --output "/Photos/Sorted"` on the VM
- It picks up `status='tagged'` photos, re-copies them into the correct geo folder,
  sets `status='organized'`

---

## Clump Detection Algorithm

```sql
-- Find all photos with date but no GPS, ordered by camera + time
SELECT hash, taken_date, camera_make, camera_model, filename, new_path
FROM photos
WHERE status IN ('organized', 'tagged')
  AND taken_date IS NOT NULL
  AND city IS NULL
  AND media_type = 'photo'
ORDER BY camera_make, camera_model, taken_date
```

Post-query in Python:
- Sort by `(camera_make, camera_model, taken_date)`
- Walk the list: start a new clump when the gap to the next photo exceeds **3 hours**
- Discard clumps with fewer than 3 photos (configurable)
- Return clumps sorted by date descending

**Design rationale for 3-hour window:**
- Covers a morning at one location before traveling
- Short enough to separate morning/afternoon stops on a travel day
  (e.g., London morning → Eurostar → Paris afternoon = two separate clumps)
- Long enough to cover a leisurely day at one location with gaps between shots

---

## DB Changes for Phase 2

Add `status = 'tagged'` as a valid status value in `organize.py`'s filter logic.
No schema change needed — `status` is already a free-text field.

`organize.py` update: treat `status='tagged'` the same as `status='scanned'` but
also use the user-assigned location fields instead of re-deriving from GPS.

---

## File Structure

```
tagger/
    server.py       ← FastAPI app (replaces gui/server.py)
    data.py         ← All DB reads and writes
    auth.py         ← API key middleware + MSAL token for Graph thumbnails
    static/
        index.html  ← SPA shell
        app.js      ← Three views: Date, Location, Tag
        styles.css  ← Geist-inspired: monochrome, whitespace, hairlines
```

The existing `gui/` folder (Muse GUI) is retired in favor of `tagger/`.

---

## Phased Delivery

### Phase 1 — Browse (read-only)
- VM backend: FastAPI with API key auth, CORS for Vercel domain, Azure DNS setup
- Date View: year list → month grid → photo grid with location sections
- Location View: ranked list + map → drill-in grid
- Vercel deploy: static SPA calling VM API, API key as env var

### Phase 2 — Tag (write-enabled)
- Tag View added to the same SPA
- Clump detection endpoint on the backend
- Location picker with type-ahead
- Write endpoint: saves location fields + sets `status='tagged'`
- `organize.py` updated to process `tagged` photos

### Phase 3 — Enhancements (backlog)
- Soft duplicate review: side-by-side pair viewer, confirm/reject
- AI tag search: find Tucker, find beach photos, etc.
- Mobile-responsive layout for phone use

---

## Open Questions

1. **Photo thumbnails:** Graph API provides ~200px thumbnails on demand by item ID.
   The VM proxy endpoint fetches and disk-caches them. Works well for browse grids;
   may be slow for large grids on first load.

2. **Vercel domain:** Vercel assigns a `*.vercel.app` subdomain on deploy. A custom
   domain (e.g. `photos.dawkins.family`) can be added later via Vercel's domain settings.

3. **Multi-user:** No auth on the frontend — anyone with the URL can browse.
   The API key only protects writes. Acceptable for family use; add login if needed later.
