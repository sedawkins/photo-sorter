"""
scan.py — Phase 1: Walk OneDrive source folder, retrieve metadata for every
photo via Graph API, and write it all to the SQLite database.
Produces a summary report at the end. No files are copied or moved.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import reverse_geocoder as rg

from db import connect, upsert_photo, record_occurrence
from graph import GraphClient
from onedrive_sync import acquire_token, load_config

APP_DIR = Path(__file__).parent
SYSTEM_DIR = APP_DIR / "_system"
DB_PATH = SYSTEM_DIR / "photo_sorter.db"
RUNS_DIR = SYSTEM_DIR / "runs"


def setup_logging() -> logging.Logger:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = RUNS_DIR / f"{ts}_scan.log"

    logger = logging.getLogger("photo_sorter")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")

    fh = logging.FileHandler(log_path)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    logger.info(f"Log file: {log_path}")
    return logger


def parse_date(photo_facet: dict) -> tuple[str | None, str | None, str | None]:
    """Return (iso_date, year, month) from the Graph photo facet."""
    taken = photo_facet.get("takenDateTime")
    if not taken:
        return None, None, None
    try:
        dt = datetime.fromisoformat(taken.replace("Z", "+00:00"))
        return taken, str(dt.year), f"{dt.month:02d}"
    except ValueError:
        return None, None, None


MONTH_NAMES = {
    "01": "January",  "02": "February", "03": "March",
    "04": "April",    "05": "May",       "06": "June",
    "07": "July",     "08": "August",    "09": "September",
    "10": "October",  "11": "November",  "12": "December",
}


def resolve_location(lat: float, lon: float) -> tuple[str, str, str] | None:
    """
    Return (city, state_or_region, country) from GPS coordinates, or None if
    the coordinates look invalid (near 0,0 is a common EXIF corruption artifact).
    """
    # Reject coordinates suspiciously close to 0,0 (Gulf of Guinea)
    if abs(lat) < 1.0 and abs(lon) < 1.0:
        return None
    try:
        results = rg.search([(lat, lon)], mode=1)
        r = results[0]
        city = r.get("name", "Unknown")
        region = r.get("admin1", "")
        country = r.get("cc", "Unknown")
        return city, region, country
    except Exception:
        return None


def pick_location_path(city: str, region: str, country: str) -> list[str]:
    """Return folder path parts for this location."""
    if country == "US":
        return [region or "Unknown_State", city]
    return [country, city]


def metadata_coverage(photo_facet: dict, location_facet: dict | None) -> str:
    """Return 'full', 'date_only', or 'none'."""
    has_date = bool(photo_facet.get("takenDateTime"))
    has_gps = location_facet is not None and (
        location_facet.get("latitude") is not None
    )
    if has_date and has_gps:
        return "full"
    if has_date:
        return "date_only"
    return "none"


def scan(source_folder: str, logger: logging.Logger):
    config = load_config()
    token = acquire_token(config)
    client = GraphClient(token)
    conn = connect(DB_PATH)

    logger.info(f"Scanning: {source_folder}")
    logger.info("Listing files (this may take a few minutes for large folders)...")

    items = client.list_photos(source_folder)
    logger.info(f"Found {len(items)} image file(s)")

    stats = {
        "total": len(items),
        "full_metadata": 0,
        "date_only": 0,
        "no_metadata": 0,
        "duplicate_hashes": 0,
        "errors": 0,
    }

    for i, item in enumerate(items, 1):
        name = item.get("name", "unknown")
        item_id = item.get("id")

        try:
            photo_facet = item.get("photo") or {}
            location_facet = item.get("location")
            file_facet = item.get("file") or {}
            image_facet = item.get("image") or {}

            taken_date, year, month = parse_date(photo_facet)
            month_name = MONTH_NAMES.get(month, month) if month else None
            hash_val = file_facet.get("hashes", {}).get("quickXorHash")
            image_unique_id = photo_facet.get("cameraMake")  # placeholder — see note

            lat = location_facet.get("latitude") if location_facet else None
            lon = location_facet.get("longitude") if location_facet else None

            city = state_or_region = country = None
            if lat is not None and lon is not None:
                loc = resolve_location(lat, lon)
                if loc:
                    city, state_or_region, country = loc
                else:
                    # Invalid GPS — treat as no location
                    lat = lon = None

            parent_ref = item.get("parentReference", {})
            parent_path = parent_ref.get("path", "").replace("/drive/root:", "")
            original_path = f"{parent_path}/{name}"

            # Use the immediate parent folder name as context (e.g. "Amsterdam 2014")
            folder_description = parent_ref.get("name") or parent_path.rstrip("/").rsplit("/", 1)[-1] or None

            # Re-evaluate location_facet after possible invalidation above
            effective_location = location_facet if lat is not None else None
            coverage = metadata_coverage(photo_facet, effective_location)
            stats[{
                "full": "full_metadata",
                "date_only": "date_only",
                "none": "no_metadata",
            }[coverage]] += 1

            if not hash_val:
                logger.warning(f"  No hash available for {name} — skipping DB write")
                stats["errors"] += 1
                continue

            # Record every occurrence (catches duplicates across folders)
            record_occurrence(conn, hash_val, original_path, folder_description, datetime.now(timezone.utc).isoformat())

            photo_record = {
                "hash": hash_val,
                "image_unique_id": photo_facet.get("takenDateTime"),  # best proxy until EXIF parsed
                "new_path": None,
                "filename": name,
                "taken_date": taken_date,
                "year": year,
                "month": month_name,
                "latitude": lat,
                "longitude": lon,
                "city": city,
                "state_or_region": state_or_region,
                "country": country,
                "width": image_facet.get("width"),
                "height": image_facet.get("height"),
                "camera_make": photo_facet.get("cameraMake"),
                "camera_model": photo_facet.get("cameraModel"),
                "folder_description": folder_description,
                "user_description": None,
                "status": "scanned",
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }

            upsert_photo(conn, photo_record)

            if i % 100 == 0 or i == stats["total"]:
                logger.info(f"  Progress: {i}/{stats['total']}")

        except Exception as e:
            logger.error(f"  Error processing {name}: {e}")
            stats["errors"] += 1

    # ── Duplicate report ──────────────────────────────────────────────────────
    dup_rows = conn.execute("""
        SELECT hash, COUNT(*) as cnt FROM photo_occurrences
        GROUP BY hash HAVING cnt > 1
    """).fetchall()
    stats["duplicate_hashes"] = len(dup_rows)
    total_dup_files = sum(r["cnt"] for r in dup_rows)

    # ── Summary ───────────────────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 50)
    logger.info("SCAN COMPLETE")
    logger.info("=" * 50)
    logger.info(f"  Total images found:       {stats['total']:>6}")
    logger.info(f"  Full metadata (date+GPS): {stats['full_metadata']:>6}")
    logger.info(f"  Date only (no GPS):       {stats['date_only']:>6}")
    logger.info(f"  No metadata:              {stats['no_metadata']:>6}")
    logger.info(f"  Duplicate hash groups:    {stats['duplicate_hashes']:>6}")
    logger.info(f"  Total duplicate files:    {total_dup_files:>6}")
    logger.info(f"  Errors:                   {stats['errors']:>6}")
    logger.info("=" * 50)

    return stats


def main():
    logger = setup_logging()

    config = load_config()
    source = config.get("source_path", "/Pictures")

    # Allow overriding source from command line for test runs
    if len(sys.argv) > 1:
        source = sys.argv[1]
        logger.info(f"Source override from command line: {source}")

    scan(source, logger)


if __name__ == "__main__":
    main()
