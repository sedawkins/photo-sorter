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
     location-tagging utility (item 1 below) is still a separate, pending build.

Future enhancements (backlog):
1.   Location tagging utility — A write-enabled GUI (Vercel app) to batch-tag the
     ~11,000 pre-GPS photos in Year/Month/Other by date+camera group. User picks a
     location, photos are re-organized into the geo hierarchy.
2.   Connectors to pull picture folders from other places like Google Drive or DropBox.
4.   Shadow shortcuts instead of full copies (+ orphan cleanup utility).
6.   AI image tagging (Azure Computer Vision, queryable tags in DB — enables Tucker/dog search).
7.   Email summary on run completion.
8.   iCloud integration (Apple Privacy export, one-time bulk import).
9.   Soft duplicate review UI (Vercel) — show near-duplicate pairs side by side for review.
10.  Phase 4 incremental import for new photos.
