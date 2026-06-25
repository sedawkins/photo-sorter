"""
softdedup.py — Find near-duplicate photos that passed hash-based dedup.

Looks for photos with matching (taken_date, camera_make, camera_model). These
are almost certainly the same image re-saved by Dropbox, iCloud, or other tools
with minor byte differences producing a different hash.

Within each near-duplicate group, keeps the largest file (highest quality) and
marks the rest as 'soft_duplicate' in the DB so organize.py skips them.

Run after scan.py and before organize.py.
"""

import logging
import sys
from datetime import datetime
from itertools import combinations
from pathlib import Path

from db import connect
from onedrive_sync import load_config

APP_DIR = Path(__file__).parent
SYSTEM_DIR = APP_DIR / "_system"
DB_PATH = SYSTEM_DIR / "photo_sorter.db"

logger = logging.getLogger("photo_sorter")


def run_softdedup():
    config = load_config()
    conn = connect(DB_PATH)

    logger.info("Scanning for near-duplicates (same taken_date + camera)...")
    logger.info("")

    # Find all groups with same taken_date + camera where more than one hash exists
    groups = conn.execute("""
        SELECT taken_date, camera_make, camera_model, COUNT(*) as cnt
        FROM photos
        WHERE taken_date IS NOT NULL
          AND camera_make IS NOT NULL
          AND status = 'scanned'
        GROUP BY taken_date, camera_make, camera_model
        HAVING cnt > 1
        ORDER BY taken_date
    """).fetchall()

    logger.info(f"Found {len(groups)} date+camera groups with multiple photos")

    soft_dup_groups = []

    for group in groups:
        photos = conn.execute("""
            SELECT hash, filename, file_size, taken_date, camera_make, camera_model
            FROM photos
            WHERE taken_date = ?
              AND camera_make = ?
              AND camera_model = ?
              AND status = 'scanned'
            ORDER BY file_size DESC NULLS LAST
        """, (group["taken_date"], group["camera_make"], group["camera_model"])).fetchall()

        if len(photos) > 1:
            soft_dup_groups.append(list(photos))

    if not soft_dup_groups:
        logger.info("No near-duplicates found.")
        logger.info("")
        return 0

    total_losers = sum(len(g) - 1 for g in soft_dup_groups)
    logger.info(f"Near-duplicate groups: {len(soft_dup_groups)}")
    logger.info(f"Photos to mark as soft_duplicate: {total_losers}")
    logger.info("")

    # Write review log
    (SYSTEM_DIR / "runs").mkdir(parents=True, exist_ok=True)
    log_path = SYSTEM_DIR / "runs" / f"soft_duplicates_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"

    marked = 0
    with open(log_path, "w") as f:
        f.write(f"Soft Duplicate Report — {datetime.now().isoformat()}\n")
        f.write(f"Groups: {len(soft_dup_groups)}  Losers: {total_losers}\n")
        f.write("=" * 70 + "\n\n")

        for group in soft_dup_groups:
            winner = group[0]  # largest file_size first
            losers = group[1:]

            f.write(f"Date:   {winner['taken_date']}\n")
            f.write(f"Camera: {winner['camera_make']} {winner['camera_model']}\n")
            f.write(f"  KEPT:   {winner['filename']}  ({winner['file_size'] or '?':,} bytes)  {winner['hash']}\n")

            for loser in losers:
                f.write(f"  SOFT DUP: {loser['filename']}  ({loser['file_size'] or '?':,} bytes)  {loser['hash']}\n")
                conn.execute(
                    "UPDATE photos SET status='soft_duplicate' WHERE hash=?",
                    (loser["hash"],)
                )
                marked += 1

            f.write("\n")

    conn.commit()

    logger.info(f"Marked {marked} photos as soft_duplicate")
    logger.info(f"Log: {log_path}")
    logger.info("")
    logger.info("=" * 50)
    logger.info("SOFT DEDUP COMPLETE")
    logger.info("=" * 50)
    logger.info(f"  Groups found:       {len(soft_dup_groups):>6}")
    logger.info(f"  Soft dups marked:   {marked:>6}")
    logger.info(f"  Log: {log_path.name}")
    logger.info("=" * 50)

    return marked


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    run_softdedup()
