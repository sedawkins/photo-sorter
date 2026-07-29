"""
backfill_shadow.py — One-time script to create Shadow copies for photos that
were moved by retag.py before Shadow support was added.

organize.py writes GPS-tagged photos to both Primary and Shadow. retag.py
only wrote to Primary. This script finds manually-tagged photos (city set,
no GPS) and copies them into the Shadow hierarchy to match.

Dry-run by default — pass --execute to actually create the copies.

Run on the VM:
    python3 backfill_shadow.py             # dry run
    python3 backfill_shadow.py --execute   # do it
"""

import argparse
import logging
import re
import sys
from pathlib import Path

from db import connect
from graph import GraphClient
from onedrive_sync import acquire_token, load_config, make_token_refresher

APP_DIR = Path(__file__).parent
SYSTEM_DIR = APP_DIR / "_system"
DB_PATH = SYSTEM_DIR / "photo_sorter.db"
SORTED_ROOT = "/Photos/Sorted"
PRIMARY_ROOT = f"{SORTED_ROOT}/Primary"
SHADOW_ROOT  = f"{SORTED_ROOT}/Shadow"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_shadow")

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|]')

def sanitize(name: str) -> str:
    name = _INVALID_CHARS.sub("_", name).strip()
    return name.rstrip(".")


def build_shadow_parts(photo: dict) -> list[str]:
    """Shadow layout: Location/City/Year/Month — matches organize.py."""
    return [
        sanitize(photo["country"] or "Unknown"),
        sanitize(photo["city"]    or "Unknown"),
        photo["year"]  or "Unknown",
        photo["month"] or "Unknown",
    ]


def backfill(dry_run: bool):
    config = load_config()
    token  = acquire_token(config)
    client = GraphClient(token, token_refresher=make_token_refresher(config))
    conn   = connect(DB_PATH)

    # Manually-tagged photos: city set by retag.py, no GPS (latitude IS NULL).
    # GPS-tagged photos have latitude set and already got their Shadow copy
    # from organize.py, so we skip them.
    rows = conn.execute("""
        SELECT filename, new_path, year, month, city, country
        FROM photos
        WHERE status = 'organized'
          AND city IS NOT NULL
          AND latitude IS NULL
        ORDER BY year, month, country, city
    """).fetchall()

    if not rows:
        logger.info("No manually-tagged photos found — nothing to backfill.")
        return

    logger.info(f"{'DRY RUN — ' if dry_run else ''}Found {len(rows)} photos to backfill")

    done = skipped = errors = 0

    for photo in [dict(r) for r in rows]:
        shadow_parts = build_shadow_parts(photo)
        shadow_rel   = "/".join(shadow_parts) + "/" + photo["filename"]
        primary_full = f"{PRIMARY_ROOT}/{photo['new_path']}"
        shadow_full  = f"{SHADOW_ROOT}/{shadow_rel}"

        logger.info(f"  {photo['new_path']}")
        logger.info(f"    shadow → {shadow_rel}")

        if dry_run:
            done += 1
            continue

        try:
            # Skip if already exists in Shadow
            if client.get_item_id_for_path(shadow_full):
                logger.info(f"    already in Shadow — skip")
                skipped += 1
                continue

            # Get source from Primary
            item_id = client.get_item_id_for_path(primary_full)
            if not item_id:
                logger.warning(f"    NOT FOUND in Primary — skip: {primary_full}")
                skipped += 1
                continue

            shadow_folder_id = client.ensure_folder_path(SHADOW_ROOT, shadow_parts)
            client.copy_item(item_id, shadow_folder_id, photo["filename"])
            logger.info(f"    ✓ done")
            done += 1

        except Exception as exc:
            logger.error(f"    ERROR: {exc}")
            errors += 1

    logger.info(
        f"\n{'DRY RUN ' if dry_run else ''}Summary: "
        f"{done} {'would copy' if dry_run else 'copied'}, "
        f"{skipped} skipped, {errors} errors"
    )
    if dry_run and done:
        logger.info("Re-run with --execute to create the Shadow copies.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="Actually create Shadow copies (default is dry run)")
    args = parser.parse_args()
    backfill(dry_run=not args.execute)
