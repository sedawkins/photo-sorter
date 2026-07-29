"""
fix_state_names.py — Fix existing DB records where manually-tagged photos
have country="California" (or any US state name/abbrev) instead of the
correct country="US" and state_or_region="California".

This merges the duplicate location groups that appeared because:
  - GPS-tagged photos: country="US", state_or_region="California"
  - Manually-tagged:   country="California", state_or_region=NULL

Dry-run by default — pass --execute to apply changes.

Run on the VM:
    python3 fix_state_names.py             # see what would change
    python3 fix_state_names.py --execute   # apply
"""

import argparse
import logging
from pathlib import Path

from db import connect
from us_states import normalize_state

APP_DIR = Path(__file__).parent
SYSTEM_DIR = APP_DIR / "_system"
DB_PATH = SYSTEM_DIR / "photo_sorter.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("fix_state_names")


def fix(dry_run: bool):
    conn = connect(DB_PATH)

    # Find all organized photos where country looks like a US state
    # (state_or_region is NULL means it was set by retag.py before this fix)
    rows = conn.execute("""
        SELECT id, filename, city, country, state_or_region
        FROM photos
        WHERE status = 'organized'
          AND city IS NOT NULL
          AND country IS NOT NULL
          AND state_or_region IS NULL
        ORDER BY country, city
    """).fetchall()

    to_fix = []
    for row in [dict(r) for r in rows]:
        state, normalized_country = normalize_state(row["country"])
        if normalized_country == "US":
            # country column holds a US state name — needs fixing
            to_fix.append((row, state, normalized_country))

    if not to_fix:
        logger.info("No records need fixing — all US states are already normalized.")
        return

    # Show a summary by state
    from collections import Counter
    counts = Counter(state for _, state, _ in to_fix)
    logger.info(f"{'DRY RUN — ' if dry_run else ''}Found {len(to_fix)} records to fix:")
    for state, n in sorted(counts.items()):
        logger.info(f"  {state}: {n} photos")

    if dry_run:
        logger.info("\nRe-run with --execute to apply changes.")
        return

    updated = 0
    for row, state, country in to_fix:
        conn.execute("""
            UPDATE photos
            SET state_or_region = ?,
                country         = ?
            WHERE id = ?
        """, (state, country, row["id"]))
        updated += 1

    conn.commit()
    logger.info(f"\nUpdated {updated} records.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="Apply changes (default is dry run)")
    args = parser.parse_args()
    fix(dry_run=not args.execute)
