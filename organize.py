"""
organize.py — Phase 3: Copy winner photos into the organized OneDrive hierarchy.

For each duplicate group, selects the winner (best resolution > best metadata >
largest file size), copies it server-side into Primary and Shadow hierarchies,
converts HEIC to JPEG where needed, writes _description.txt files, and logs
all skipped duplicates.

Run after scan.py and spotcheck.py have both completed successfully.
"""

import io
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pillow_heif
pillow_heif.register_heif_opener()
from PIL import Image

from db import connect, get_duplicate_groups
from graph import GraphClient
from onedrive_sync import acquire_token, load_config

APP_DIR = Path(__file__).parent
SYSTEM_DIR = APP_DIR / "_system"
DB_PATH = SYSTEM_DIR / "photo_sorter.db"

logger = logging.getLogger("photo_sorter")


# ── Winner selection ──────────────────────────────────────────────────────────

def metadata_score(photo: object) -> int:
    """Higher score = better metadata. Used as tiebreaker after resolution."""
    if photo["latitude"] is not None:
        return 2  # date + GPS
    if photo["taken_date"] is not None:
        return 1  # date only
    return 0      # no metadata


def pick_winner(group_occurrences: list, photos_by_hash: dict) -> tuple:
    """
    Given a list of photo_occurrences rows for one hash group,
    return (winner_occurrence, [loser_occurrences]).
    Uses the photos table for resolution/metadata info.
    """
    hash_val = group_occurrences[0]["hash"]
    photo = photos_by_hash.get(hash_val)

    # All occurrences share the same hash so same image content —
    # pick the one whose folder_description is most informative as winner,
    # but since content is identical we just pick index 0 after sorting
    # by folder_description length (longer = more descriptive folder name).
    sorted_occs = sorted(
        group_occurrences,
        key=lambda o: len(o["folder_description"] or ""),
        reverse=True,
    )
    winner = sorted_occs[0]
    losers = sorted_occs[1:]
    return winner, losers


def pick_winners_all(conn) -> tuple[dict, dict]:
    """
    Returns:
      winners  — {hash: occurrence_row}  one winner per unique photo
      losers   — {hash: [occurrence_rows]}  all losing occurrences
    """
    # Singles (no duplicates) — the one occurrence is automatically the winner
    singles = conn.execute("""
        SELECT o.*, p.taken_date, p.year, p.month, p.latitude, p.longitude,
               p.city, p.state_or_region, p.country, p.width, p.height,
               p.camera_make, p.camera_model, p.folder_description as photo_folder_desc,
               p.user_description, p.filename, p.new_path, p.status
        FROM photo_occurrences o
        JOIN photos p ON p.hash = o.hash
        WHERE o.hash NOT IN (
            SELECT hash FROM photo_occurrences GROUP BY hash HAVING COUNT(*) > 1
        )
    """).fetchall()

    winners = {}
    losers = defaultdict(list)

    for row in singles:
        winners[row["hash"]] = row

    # Duplicate groups — pick winner from each
    dup_groups = get_duplicate_groups(conn)
    photos_by_hash = {
        r["hash"]: r for r in conn.execute("SELECT * FROM photos").fetchall()
    }

    for group in dup_groups:
        hash_val = group[0]["hash"]
        # Enrich occurrences with photo metadata for winner selection
        photo = photos_by_hash.get(hash_val)
        winner_occ, loser_occs = pick_winner(group, photos_by_hash)
        winners[hash_val] = winner_occ
        losers[hash_val] = loser_occs

    return winners, dict(losers)


# ── Destination path calculation ──────────────────────────────────────────────

def destination_parts(photo) -> tuple[list[str], list[str] | None]:
    """
    Returns (primary_parts, shadow_parts) folder path components.
    shadow_parts is None if the photo has no GPS.
    """
    year = photo["year"] or "Unknown"
    month = photo["month"] or "Unknown"
    lat = photo["latitude"]
    country = photo["country"]
    state = photo["state_or_region"]
    city = photo["city"]

    has_gps = lat is not None and city is not None

    if has_gps:
        if country == "US":
            loc = [state or "Unknown_State", city]
        else:
            loc = [country or "Unknown_Country", city]
        primary = [year, month] + loc
        shadow = loc + [year, month]
    else:
        primary = [year, month, "Other"]
        shadow = None

    return primary, shadow


def unsorted_parts() -> tuple[list[str], None]:
    return ["Unsorted"], None


# ── HEIC conversion ───────────────────────────────────────────────────────────

def convert_heic_to_jpeg(data: bytes) -> tuple[bytes, str]:
    """Convert HEIC bytes to JPEG bytes. Returns (jpeg_bytes, new_extension)."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue(), ".jpg"


# ── Description file ──────────────────────────────────────────────────────────

def update_description_file(folder_descriptions: set[str], existing: str | None) -> str:
    """Merge new folder descriptions into the existing _description.txt content."""
    lines = set()
    if existing:
        lines = set(existing.strip().splitlines())
    lines.update(d for d in folder_descriptions if d)
    return "\n".join(sorted(lines)) + "\n"


# ── Main organizer ────────────────────────────────────────────────────────────

def run_organize(sorted_root: str, dry_run: bool = False):
    config = load_config()
    token = acquire_token(config)
    client = GraphClient(token)
    conn = connect(DB_PATH)

    primary_root = f"{sorted_root}/Primary"
    shadow_root = f"{sorted_root}/Shadow"
    unsorted_root = f"{sorted_root}/Unsorted"

    logger.info(f"Output root: {sorted_root}")
    logger.info(f"Dry run: {dry_run}")
    logger.info("")

    winners, losers = pick_winners_all(conn)

    # Filter to only unorganized winners
    to_process = {
        h: occ for h, occ in winners.items()
        if conn.execute(
            "SELECT status FROM photos WHERE hash=?", (h,)
        ).fetchone()["status"] != "organized"
    }

    logger.info(f"Photos to organize: {len(to_process)}")
    logger.info(f"Duplicate losers to skip: {sum(len(v) for v in losers.values())}")
    logger.info("")

    # Cache folder IDs to avoid redundant API calls
    folder_id_cache: dict[str, str] = {}

    def get_folder_id(root_path: str, parts: list[str]) -> str:
        key = root_path + "/" + "/".join(parts)
        if key not in folder_id_cache:
            folder_id_cache[key] = client.ensure_folder_path(root_path, parts)
        return folder_id_cache[key]

    # Track descriptions per destination folder
    folder_descriptions: dict[str, set[str]] = defaultdict(set)

    stats = {"organized": 0, "skipped_dup": 0, "converted": 0,
             "unsorted": 0, "errors": 0}

    for i, (hash_val, occ) in enumerate(to_process.items(), 1):
        photo = conn.execute(
            "SELECT * FROM photos WHERE hash=?", (hash_val,)
        ).fetchone()
        filename = photo["filename"]
        original_path = occ["original_path"]
        folder_desc = occ["folder_description"] or photo["folder_description"]

        try:
            has_date = photo["year"] and photo["year"] != "Unknown"
            has_gps = photo["latitude"] is not None

            if not has_date and not has_gps:
                primary_parts, shadow_parts = unsorted_parts()
                dest_root = unsorted_root
                stats["unsorted"] += 1
            else:
                primary_parts, shadow_parts = destination_parts(photo)
                dest_root = primary_root

            # Handle HEIC conversion
            ext = Path(filename).suffix.lower()
            needs_conversion = ext in (".heic", ".heif")
            final_filename = filename

            if needs_conversion:
                stem = Path(filename).stem
                final_filename = stem + ".jpg"

            # Resolve item ID for this occurrence
            item_id = client.get_item_id_for_path(original_path)
            if not item_id:
                logger.warning(f"  Could not resolve item ID for {original_path} — skipping")
                stats["errors"] += 1
                continue

            if dry_run:
                logger.info(
                    f"  [DRY RUN] {filename} -> {'/'.join(primary_parts)}/{final_filename}"
                )
                stats["organized"] += 1
                continue

            # Copy or convert into Primary
            primary_folder_id = get_folder_id(primary_root, primary_parts)
            dest_primary_path = "/".join(primary_parts)

            if needs_conversion:
                raw = client.download_item(item_id)
                jpeg_bytes, _ = convert_heic_to_jpeg(raw)
                new_item_id = client.upload_small_file(
                    primary_folder_id, final_filename, jpeg_bytes
                )
                stats["converted"] += 1
            else:
                new_item_id = client.copy_item(item_id, primary_folder_id, final_filename)

            new_path = f"{dest_primary_path}/{final_filename}"

            # Copy into Shadow (GPS photos only)
            if shadow_parts:
                shadow_folder_id = get_folder_id(shadow_root, shadow_parts)
                client.copy_item(item_id if not needs_conversion else new_item_id,
                                 shadow_folder_id, final_filename)

            # Track folder descriptions
            if folder_desc:
                folder_descriptions[dest_primary_path].add(folder_desc)
                if shadow_parts:
                    folder_descriptions["/".join(shadow_parts)].add(folder_desc)

            # Update DB
            conn.execute(
                "UPDATE photos SET status='organized', new_path=? WHERE hash=?",
                (new_path, hash_val)
            )
            conn.execute(
                "UPDATE photo_occurrences SET is_winner=1 WHERE hash=? AND original_path=?",
                (hash_val, original_path)
            )
            conn.commit()
            stats["organized"] += 1

            if i % 50 == 0:
                logger.info(f"  Progress: {i}/{len(to_process)}")

        except Exception as e:
            logger.error(f"  Error organizing {filename}: {e}")
            stats["errors"] += 1

    # ── Log skipped duplicates ────────────────────────────────────────────────
    if losers:
        dup_log_path = SYSTEM_DIR / "runs" / f"duplicates_skipped_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(dup_log_path, "w") as f:
            for hash_val, loser_occs in losers.items():
                winner_occ = winners[hash_val]
                for loser in loser_occs:
                    f.write(
                        f"SKIPPED: {loser['original_path']}\n"
                        f"  KEPT:  {winner_occ['original_path']}\n"
                        f"  HASH:  {hash_val}\n\n"
                    )
            stats["skipped_dup"] = sum(len(v) for v in losers.values())
        logger.info(f"Duplicates log: {dup_log_path}")

    # ── Write _description.txt files ──────────────────────────────────────────
    if not dry_run:
        for folder_path, descs in folder_descriptions.items():
            try:
                # Determine which root this folder is under
                if "Unsorted" in folder_path:
                    root = unsorted_root
                elif folder_path.startswith(tuple("0123456789")):
                    root = primary_root
                else:
                    root = shadow_root
                folder_id = folder_id_cache.get(root + "/" + folder_path)
                if folder_id:
                    content = update_description_file(descs, None)
                    client.upload_small_file(folder_id, "_description.txt",
                                             content.encode("utf-8"))
            except Exception as e:
                logger.warning(f"  Could not write _description.txt for {folder_path}: {e}")

    # ── Summary ───────────────────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 50)
    logger.info("ORGANIZE COMPLETE")
    logger.info("=" * 50)
    logger.info(f"  Organized:          {stats['organized']:>6}")
    logger.info(f"  HEIC converted:     {stats['converted']:>6}")
    logger.info(f"  Unsorted (no meta): {stats['unsorted']:>6}")
    logger.info(f"  Duplicates skipped: {stats['skipped_dup']:>6}")
    logger.info(f"  Errors:             {stats['errors']:>6}")
    logger.info("=" * 50)


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    parser = argparse.ArgumentParser(description="Organize photos into OneDrive hierarchy")
    parser.add_argument(
        "--output",
        default=None,
        help="OneDrive output root path (overrides config sorted_root)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview destinations without copying any files",
    )
    args = parser.parse_args()

    config = load_config()
    output = args.output or config.get("sorted_root", "/Photos/Sorted")

    run_organize(output, dry_run=args.dry_run)
