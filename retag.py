"""
retag.py — Move tagged-clump photos from date-only folders to location folders.

For each photo where tagged_city is set but city is still NULL, this script:
  1. Builds the new OneDrive path: Year/Month/tagged_country/tagged_city/filename
  2. Copies the file server-side in OneDrive to the new location
  3. Deletes the original file
  4. Updates the DB: new_path, city, country, clears tagged_city/tagged_country

Dry-run by default — pass --execute to actually move files.

Run on the VM:
    python3 retag.py             # dry run — shows what would move
    python3 retag.py --execute   # do it
"""

import argparse
import logging
import re
import sys
from pathlib import Path

from db import connect
from geocode import geocode
from graph import GraphClient
from onedrive_sync import acquire_token, load_config, make_token_refresher
from us_states import normalize_state

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
logger = logging.getLogger("retag")

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|]')

def sanitize(name: str) -> str:
    name = _INVALID_CHARS.sub("_", name).strip()
    return name.rstrip(".")  # OneDrive rejects folder names ending with a period


def resolve_location(tagged_country: str) -> tuple[str, str, str | None]:
    """
    Return (folder_name, country_for_db, state_or_region_for_db) from user input.
    US states (by name or abbreviation) are normalized to full name + country="US".
    """
    state_or_region, country = normalize_state(tagged_country or "Unknown")
    folder = sanitize(state_or_region or country)
    return folder, country, state_or_region


def build_new_path(photo: dict) -> str:
    """Return the relative new_path (under Primary) for a tagged photo."""
    year     = photo["year"]   or "Unknown"
    month    = photo["month"]  or "Unknown"
    location, _, _ = resolve_location(photo["tagged_country"] or "Unknown")
    city     = sanitize(photo["tagged_city"] or "Unknown")
    filename = photo["filename"]
    return f"{year}/{month}/{location}/{city}/{filename}"


def build_shadow_parts(photo: dict) -> list[str]:
    """Return folder path parts under Shadow: [location, city, year, month].
    Mirrors organize.py's shadow layout so all photos end up in one tree."""
    location, _, _ = resolve_location(photo["tagged_country"] or "Unknown")
    city  = sanitize(photo["tagged_city"] or "Unknown")
    year  = photo["year"]  or "Unknown"
    month = photo["month"] or "Unknown"
    return [location, city, year, month]


def retag_photos(dry_run: bool):
    config = load_config()
    token  = acquire_token(config)
    client = GraphClient(token, token_refresher=make_token_refresher(config))
    conn   = connect(DB_PATH)

    rows = conn.execute("""
        SELECT id, hash, filename, new_path, year, month,
               tagged_city, tagged_country
        FROM photos
        WHERE tagged_city IS NOT NULL
          AND city IS NULL
          AND status = 'organized'
        ORDER BY year, month, tagged_country, tagged_city
    """).fetchall()

    if not rows:
        logger.info("No tagged photos waiting to move — all done!")
        return

    logger.info(f"{'DRY RUN — ' if dry_run else ''}Found {len(rows)} photos to relocate")

    moved = skipped = errors = 0

    for photo_num, photo in enumerate(rows, start=1):
        photo = dict(photo)
        old_rel  = photo["new_path"]
        new_rel  = build_new_path(photo)

        if old_rel == new_rel:
            logger.info(f"  SKIP (already in place): {old_rel}")
            skipped += 1
            continue

        old_full = f"{PRIMARY_ROOT}/{old_rel}"
        new_full = f"{PRIMARY_ROOT}/{new_rel}"

        logger.info(f"  {'WOULD MOVE' if dry_run else 'MOVING'} ({photo_num}/{len(rows)}): {old_rel}")
        logger.info(f"         → {new_rel}")

        if dry_run:
            shadow_parts = build_shadow_parts(photo)
            shadow_rel = "/".join(shadow_parts) + "/" + photo["filename"]
            logger.info(f"         shadow → {shadow_rel}")
            moved += 1
            continue

        try:
            # Resolve source item ID
            item_id = client.get_item_id_for_path(old_full)
            if not item_id:
                # Source missing — check if file already landed at destination
                # (copy succeeded in a prior run but delete/DB-update failed)
                dest_id = client.get_item_id_for_path(f"{PRIMARY_ROOT}/{new_rel}")
                if dest_id:
                    logger.info(f"  ALREADY AT DEST — updating DB only: {new_rel}")
                    _, db_country, db_state = resolve_location(photo["tagged_country"] or "Unknown")
                    coords = geocode(photo["tagged_city"], db_state, db_country)
                    conn.execute("""
                        UPDATE photos
                        SET new_path        = ?,
                            city            = ?,
                            state_or_region = ?,
                            country         = ?,
                            latitude        = COALESCE(latitude, ?),
                            longitude       = COALESCE(longitude, ?),
                            tagged_city     = NULL,
                            tagged_country  = NULL
                        WHERE id = ?
                    """, (new_rel, photo["tagged_city"], db_state, db_country,
                          coords[0] if coords else None,
                          coords[1] if coords else None,
                          photo["id"]))
                    conn.commit()
                    moved += 1
                else:
                    logger.warning(f"  NOT FOUND at source or dest — skipping: {old_full}")
                    skipped += 1
                continue

            # Ensure destination folder exists
            path_parts = new_rel.split("/")[:-1]   # drop filename
            dest_folder_id = client.ensure_folder_path(PRIMARY_ROOT, path_parts)

            # Copy to new Primary location
            client.copy_item(item_id, dest_folder_id, photo["filename"])

            # Create Shadow copy  (Location/City/Year/Month — same layout as organize.py)
            shadow_parts = build_shadow_parts(photo)
            shadow_folder_id = client.ensure_folder_path(SHADOW_ROOT, shadow_parts)
            client.copy_item(item_id, shadow_folder_id, photo["filename"])

            # Delete old file
            client._session.delete(
                f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}",
                timeout=30,
            ).raise_for_status()

            # Update DB — geocode city if no GPS coords already
            _, db_country, db_state = resolve_location(photo["tagged_country"] or "Unknown")
            coords = geocode(photo["tagged_city"], db_state, db_country)
            if coords:
                logger.info(f"  geocoded → {coords[0]:.4f}, {coords[1]:.4f}")
            conn.execute("""
                UPDATE photos
                SET new_path        = ?,
                    city            = ?,
                    state_or_region = ?,
                    country         = ?,
                    latitude        = COALESCE(latitude, ?),
                    longitude       = COALESCE(longitude, ?),
                    tagged_city     = NULL,
                    tagged_country  = NULL
                WHERE id = ?
            """, (new_rel, photo["tagged_city"], db_state, db_country,
                  coords[0] if coords else None,
                  coords[1] if coords else None,
                  photo["id"]))
            conn.commit()

            logger.info(f"  ✓ Done")
            moved += 1

        except Exception as exc:
            logger.error(f"  ERROR: {exc}")
            errors += 1

    logger.info(
        f"\n{'DRY RUN ' if dry_run else ''}Summary: "
        f"{moved} {'would move' if dry_run else 'moved'}, "
        f"{skipped} skipped, {errors} errors"
    )
    if dry_run and moved:
        logger.info("Re-run with --execute to perform the moves.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="Actually move files (default is dry run)")
    args = parser.parse_args()
    retag_photos(dry_run=not args.execute)
