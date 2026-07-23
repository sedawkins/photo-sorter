Context:  I have many directories of digital pictures on OneDrive.  Over the years, I have copied old digital cameras, thumb drives, etc that have family pictures.  There are many duplicates, and the directories aren’t organized.  Most have some metadata, depending on how old the camera was.  A few are scans of very old pictures with no metadata.
Initial Goals:  
1.	Go through the photos and organize them into folders by year, then month, then location in the geo tags if the pictures have that info.  Otherwise “other” folders in the hierarchy.
2.	Location is State/City for locations in the US, Country/City for outside the US.
3.	Create a shadow folder hierarchy starting by location, then year/month.
4.	Convert .heic format (iPhone) to .jpg
5.	Find duplicates by comparing hashes.  Randomly pixel check some duplicate pairs occasionally.
6.	Duplicates are skipped (not moved) and logged to a duplicates log file for review. No “to be discarded” folder — the log is the safety net.
7.	Keep a database of pictures in the new folders so when I find new (old) phones, thumb drives or cameras I can scan through the pictures and add any not already in the new folders.
8.	Organize the source code on github.
9.	Run the app on an Azure VM, since most of my (disorganized) file folders are on OneDrive.	

## Running on the VM

Always `git pull` first to get the latest code, then:

```bash
# Scan a source folder into the DB (fast — metadata only, no file copies)
python3 scan.py /Pictures/iPhone-Sed-2026 > _system/runs/scan.log 2>&1 &
tail -f _system/runs/scan.log

# Organize scanned photos into /Photos/Sorted (slow — server-side OneDrive copies)
python3 organize.py > _system/runs/organize.log 2>&1 &
tail -f _system/runs/organize.log

# Move tagged clumps from Other/ into location folders (after tagging in the web app)
python3 retag.py              # dry run — shows what would move
python3 retag.py --execute > _system/runs/retag.log 2>&1 &
tail -f _system/runs/retag.log
```

Use `jobs` to see background jobs, `kill %1` (or `pkill -f organize.py`) to stop one.
The tagger web app runs as a systemd service — `sudo systemctl restart photo-sorter` to restart it.

---

Completed enhancements:
3.   Parallel copy optimization — DONE. ThreadPoolExecutor(max_workers=3), per-thread
     GraphClient + SQLite connection via threading.local(). 429 rate-limit backoff included.
5.   Movie file handling — DONE. .mov/.mp4/.avi/.mkv/.m4v/.wmv routed to Year/Movies/
     in Primary; no Shadow copy for movies.
11.  Cleanup cruft (non-image files) — DONE, see `cleanup_kruft.py` and SPEC.md's
     “Source cleanup utility” section. Note: Windows Phone `.nar` files turned out
     to NOT be junk — they're ZIP bundles containing unique alternate-exposure
     JPEGs, so they're excluded from auto-cleanup and flagged for manual review.
12.  Thumbnail detection (small dimensions + size threshold, with safety checks) —
     DONE as part of `cleanup_kruft.py` (sidecar pairing + size-ceiling checks) and
     the `Data.noindex` preview-cache exclusion added to `graph.py`.
13.  Read-only “muse” GUI — DONE, see `gui/`. A local, read-only web app to browse
     the organized archive: years, places, dedup stats, and a resurfaced-memory card.
     Runs `uvicorn gui.server:app` and reads a snapshot of the metadata DB; photo
     previews are fetched as on-demand Graph thumbnails, never bulk-downloaded.
     Architected to deploy to Vercel later. Does not write to the DB — the
     location-tagging utility (item 14 below) is now complete.
14.  Location tagging web app (Vercel) — DONE. See `tagger/` and GUI_SPEC.md.
     Three-view SPA: Date browse, Location browse, and Tag view for batch-tagging
     ~11,000 pre-GPS photos by date+camera clump. Backend FastAPI on Azure VM,
     frontend deployed to Vercel with auto-deploy on git push. Includes:
     - Two-pass clump clustering (tight 3h window + 24h fringe absorption into anchors ≥3)
     - AI scan via Claude Haiku — analyzes up to 5 sample thumbnails, returns location hint
     - Selective AI scan — hover/long-press overlay to choose specific photos for AI analysis
     - iPhone long-press support (500ms hold shows Scan / Open overlay)
     - Disk thumbnail cache (`_system/thumb_cache/`) to stay within Vercel's 10s timeout
     - Full-size lightbox viewer
     - Trash clump action (sets status='trashed', skipped by future organize runs)
15.  retag.py — DONE. Utility that moves tagged-clump photos from date-only
     `Year/Month/Other/` folders into their location folders in OneDrive, then
     updates the DB. Server-side Graph API copy + delete. Dry-run by default;
     `--execute` to move. Handles partial prior runs (checks destination before
     giving up on a missing source file).

Future enhancements (backlog):
2.   Connectors to pull picture folders from other places like Google Drive or DropBox.
4.   Shadow shortcuts instead of full copies (+ orphan cleanup utility).
6.   AI image tagging (Azure Computer Vision, queryable tags in DB — enables Tucker/dog search).
7.   Email summary on run completion.
8.   iCloud integration (Apple Privacy export, one-time bulk import).
9.   Soft duplicate review UI (Vercel) — show near-duplicate pairs side by side for review.
10.  Phase 4 incremental import for new photos.
