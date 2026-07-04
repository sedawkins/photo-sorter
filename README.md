Context:  I have many directories of digital pictures on OneDrive.  Over the years, I have copied old digital cameras, thumb drives, etc that have family pictures.  There are many duplicates, and the directories aren’t organized.  Most have some metadata, depending on how old the camera was.  A few are scans of very old pictures with no metadata.
Initial Goals:  
1.	Go through the photos and organize them into folders by year, then month, then location in the geo tags if the pictures have that info.  Otherwise “other” folders in the hierarchy.
2.	Location is State/City for locations in the US, Country/City for outside the US.
3.	Create a shadow folder hierarchy starting by location, then year/month.
4.	Convert .heic format (iPhone) to .jpg
5.	Find duplicates by comparing hashes.  Randomly pixel check some duplicate pairs occasionally.
6.	Move duplicates into a “to be discarded” folder.  I’ll empty it after ensuring that everything worked correctly
7.	Keep a database of pictures in the new folders so when I find new (old) phones, thumb drives or cameras I can scan through the pictures and add any not already in the new folders.
8.	Organize the source code on github.
9.	Run the app on an Azure VM, since most of my (disorganized) file folders are on OneDrive.	

Future enhancements:
1.	A utility to find groups of pictures in the “other” folder (no geo tags) and show a sample to me so I can manually identify the location.  Move them into the sorted hierarchy.  Likely way to group pictures is if they were taken with the same camera on the same day, then they are likely at the same location.
2.	 Connectors to pull picture folders from other places like Google Drive or DropBox.
3.   Parallel copy optimization (ThreadPoolExecutor, 5-10 concurrent copies)
4.   Shadow shortcuts instead of full copies (+ orphan cleanup utility
5.   Movie file handling (2019/Movies/, Texas/Movies/)
6.   Location tagging utility for pre-GPS photos (promote folder descriptions like slide tray names)
7.   AI image tagging (Azure Computer Vision, queryable tags in DB, geo-scoped to keep costs down)
8.   Email summary on run completion
9.   Additional folder scans after this run
10.   Phase 4 incremental import for new photos
11.   Cleanup cruft (non-image files) — DONE, see `cleanup_kruft.py` and SPEC.md's
      "Source cleanup utility" section. Note: Windows Phone `.nar` files turned out
      to NOT be junk — they're ZIP bundles containing unique alternate-exposure
      JPEGs, so they're excluded from auto-cleanup and flagged for manual review.
12.   Thumbnail detection (small dimensions + size threshold, with safety checks) —
      DONE as part of `cleanup_kruft.py` (sidecar pairing + size-ceiling checks) and
      the `Data.noindex` preview-cache exclusion added to `graph.py`.
