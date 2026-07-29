"""
geocode_cities.py — Look up approximate lat/lon for manually-tagged cities
that have no GPS coordinates, using Nominatim (OpenStreetMap). Stores the
city-center coordinates so they appear as dots on the browse map.

Nominatim requires: max 1 request/second, descriptive User-Agent.
Free, no API key needed.

Dry-run by default — pass --execute to write to the DB.

Run on the VM:
    python3 geocode_cities.py             # see what would be geocoded
    python3 geocode_cities.py --execute   # apply
"""

import argparse
import logging
import time
from pathlib import Path

from db import connect
from geocode import geocode

APP_DIR = Path(__file__).parent
SYSTEM_DIR = APP_DIR / "_system"
DB_PATH = SYSTEM_DIR / "photo_sorter.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("geocode_cities")


def run(dry_run: bool):
    conn = connect(DB_PATH)

    # Find distinct cities that have no GPS coordinates at all
    cities = conn.execute("""
        SELECT city, state_or_region, country, COUNT(*) n
        FROM photos
        WHERE status = 'organized'
          AND city IS NOT NULL
        GROUP BY city, state_or_region, country
        HAVING AVG(latitude) IS NULL
        ORDER BY n DESC
    """).fetchall()

    if not cities:
        logger.info("All cities already have coordinates — nothing to geocode.")
        return

    logger.info(f"{'DRY RUN — ' if dry_run else ''}Found {len(cities)} cities to geocode")

    found = missing = 0

    for row in [dict(r) for r in cities]:
        city    = row["city"]
        state   = row["state_or_region"]
        country = row["country"]
        n       = row["n"]

        label = f"{city} / {state or country}"
        result = geocode(city, state, country)
        time.sleep(1.1)  # Nominatim rate limit: 1 req/sec

        if result:
            lat, lon = result
            logger.info(f"  ✓ {label} ({n} photos) → {lat:.4f}, {lon:.4f}")
            if not dry_run:
                conn.execute("""
                    UPDATE photos
                    SET latitude = ?, longitude = ?
                    WHERE city = ?
                      AND (state_or_region = ? OR (state_or_region IS NULL AND ? IS NULL))
                      AND country = ?
                      AND latitude IS NULL
                      AND status = 'organized'
                """, (lat, lon, city, state, state, country))
                conn.commit()
            found += 1
        else:
            logger.warning(f"  ✗ {label} ({n} photos) — not found")
            missing += 1

    logger.info(
        f"\n{'DRY RUN ' if dry_run else ''}Summary: "
        f"{found} geocoded, {missing} not found"
    )
    if dry_run and found:
        logger.info("Re-run with --execute to store coordinates.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="Write coordinates to DB (default is dry run)")
    args = parser.parse_args()
    run(dry_run=not args.execute)
