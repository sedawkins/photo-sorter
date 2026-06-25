"""
softdedup.py — Find near-duplicate photos that passed hash-based dedup.

Looks for photos with matching (taken_date, camera_make, camera_model) and
file sizes within SIZE_THRESHOLD bytes. These are likely the same image
re-saved by Dropbox, iCloud, or other tools with minor byte differences.

Writes a review log to _system/runs/. Does not modify any files or DB records.
Run after organize.py has completed.
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

SIZE_THRESHOLD = 10_240  # 10 KB — catches re-saves with minor byte differences

logger = logging.getLogger("photo_sorter")


def run_softdedup():
    config = load_config()
    conn = connect(DB_PATH)

    logger.info("Scanning for near-duplicates (same date + camera + similar size)...")
    logger.info(f"Size threshold: {SIZE_THRESHOLD // 1024} KB")
    logger.info("")

    # Find all groups with same taken_date + camera where more than one hash exists
    groups = conn.execute("""
        SELECT taken_date, camera_make, camera_model, COUNT(*) as cnt
        FROM photos
        WHERE taken_date IS NOT NULL
          AND camera_make IS NOT NULL
          AND status = 'organized'
        GROUP BY taken_date, camera_make, camera_model
        HAVING cnt > 1
        ORDER BY taken_date
    """).fetchall()

    logger.info(f"Found {len(groups)} date+camera groups with multiple organized photos")
    logger.info("")

    soft_dup_pairs = []

    for group in groups:
        photos = conn.execute("""
            SELECT p.hash, p.filename, p.new_path, p.file_size,
                   p.taken_date, p.camera_make, p.camera_model,
                   o.original_path
            FROM photos p
            JOIN photo_occurrences o ON o.hash = p.hash AND o.is_winner = 1
            WHERE p.taken_date = ?
              AND p.camera_make = ?
              AND p.camera_model = ?
              AND p.status = 'organized'
        """, (group["taken_date"], group["camera_make"], group["camera_model"])).fetchall()

        # Compare all pairs within this group
        for a, b in combinations(photos, 2):
            size_a = a["file_size"] or 0
            size_b = b["file_size"] or 0
            if abs(size_a - size_b) <= SIZE_THRESHOLD:
                soft_dup_pairs.append((a, b))

    logger.info(f"Near-duplicate pairs found: {len(soft_dup_pairs)}")

    if not soft_dup_pairs:
        logger.info("No near-duplicates detected.")
        return

    # Write review log
    SYSTEM_DIR.mkdir(parents=True, exist_ok=True)
    (SYSTEM_DIR / "runs").mkdir(parents=True, exist_ok=True)
    log_path = SYSTEM_DIR / "runs" / f"soft_duplicates_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"

    with open(log_path, "w") as f:
        f.write(f"Soft Duplicate Report — {datetime.now().isoformat()}\n")
        f.write(f"Size threshold: {SIZE_THRESHOLD} bytes\n")
        f.write(f"Pairs found: {len(soft_dup_pairs)}\n")
        f.write("=" * 70 + "\n\n")

        for a, b in soft_dup_pairs:
            size_a = a["file_size"] or 0
            size_b = b["file_size"] or 0
            f.write(f"Date:   {a['taken_date']}\n")
            f.write(f"Camera: {a['camera_make']} {a['camera_model']}\n")
            f.write(f"  A: {a['original_path']}\n")
            f.write(f"     Organized: {a['new_path']}  ({size_a:,} bytes)\n")
            f.write(f"     Hash: {a['hash']}\n")
            f.write(f"  B: {b['original_path']}\n")
            f.write(f"     Organized: {b['new_path']}  ({size_b:,} bytes)\n")
            f.write(f"     Hash: {b['hash']}\n")
            f.write(f"  Size diff: {abs(size_a - size_b):,} bytes\n\n")

    logger.info(f"Review log written: {log_path}")
    logger.info("")
    logger.info("=" * 50)
    logger.info("SOFT DEDUP COMPLETE")
    logger.info("=" * 50)
    logger.info(f"  Near-duplicate pairs: {len(soft_dup_pairs):>6}")
    logger.info(f"  Review log:           {log_path.name}")
    logger.info("=" * 50)
    logger.info("")
    logger.info("Next step: review the log and manually remove unwanted copies from")
    logger.info("/Photos/Sorted if desired. Originals in /Pictures are never touched.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    run_softdedup()
