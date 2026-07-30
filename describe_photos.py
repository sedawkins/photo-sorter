"""
describe_photos.py — Generate AI tags for photos using Claude Haiku.

Sends each photo's thumbnail to Claude Haiku and stores comma-separated
tags in the ai_description column. Tags cover subjects, animals (with
breed), people descriptors, setting, activities, and landmarks.

Skips photos that already have ai_description set (safe to re-run).
Movies are skipped — thumbnails are unreliable for video.

Usage:
    python3 describe_photos.py --year 2013            # test on one year
    python3 describe_photos.py --year 2013 --limit 20 # quick sample
    python3 describe_photos.py                        # all undescribed photos
    python3 describe_photos.py --refill               # re-describe all (overwrite)

Run on the VM (ANTHROPIC_API_KEY must be set in ~/.bashrc):
    python3 describe_photos.py --year 2013 --limit 20
"""

import anthropic
import argparse
import base64
import hashlib
import json
import logging
import os
import time
from pathlib import Path

from db import connect
from onedrive_sync import acquire_token, load_config, make_token_refresher
from graph import GraphClient

APP_DIR    = Path(__file__).parent
SYSTEM_DIR = APP_DIR / "_system"
DB_PATH    = SYSTEM_DIR / "photo_sorter.db"
THUMB_DIR  = SYSTEM_DIR / "thumb_cache"
SORTED_ROOT = "/Photos/Sorted"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("describe")

PROMPT = """\
Look at this photo and return ONLY a comma-separated list of short tags \
(no sentences, no explanation). Include:
- People: use descriptors like "elderly man", "young woman", "teenage boy", \
"toddler", "baby", "group of people", "couple"
- Animals: use specific breed if visible, e.g. "yellow Labrador Retriever", \
"Cavalier King Charles Spaniel", "cat"
- Setting: e.g. "beach", "mountains", "ski slope", "restaurant", "backyard", \
"living room", "forest", "city street", "hotel"
- Activities: e.g. "swimming", "skiing", "hiking", "birthday party", \
"graduation", "wedding", "Christmas", "Thanksgiving"
- Landmarks or notable subjects: e.g. "Taj Mahal", "Eiffel Tower", \
"Golden Gate Bridge", "Christmas tree", "swimming pool"
- Season or condition if clear: "snow", "sunny", "autumn leaves"

Return 5-12 tags. Example output:
yellow Labrador Retriever, beach, young woman, toddler, sunny, swimming pool
"""


def fetch_thumbnail(new_path: str, graph_client: GraphClient) -> bytes | None:
    """Return JPEG thumbnail bytes from disk cache or OneDrive."""
    cache_key  = hashlib.md5(new_path.encode()).hexdigest()
    cache_file = THUMB_DIR / f"{cache_key}.jpg"
    if cache_file.exists():
        return cache_file.read_bytes()

    from urllib.parse import quote
    full_path = f"{SORTED_ROOT}/Primary/{new_path}"
    encoded   = quote(full_path, safe="/")
    url = (f"https://graph.microsoft.com/v1.0/me/drive/root:{encoded}:"
           f"/thumbnails/0/large/content")
    # Use graph_client._session so token refresh is handled automatically
    resp = graph_client._session.get(url, timeout=20)
    if not resp.ok:
        return None
    cache_file.write_bytes(resp.content)
    return resp.content


def describe_photo(thumb_bytes: bytes, client: anthropic.Anthropic) -> str | None:
    """Send thumbnail to Haiku, return comma-separated tag string."""
    b64 = base64.standard_b64encode(thumb_bytes).decode()
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": PROMPT},
                ],
            }],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        logger.warning(f"  Haiku error: {exc}")
        return None


def run(year: str | None, path: str | None, limit: int | None, refill: bool):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set — add it to ~/.bashrc")
        return

    config = load_config()
    token  = acquire_token(config)
    # Simple token holder — refresh handled by graph client internally
    graph_client = GraphClient(token, token_refresher=make_token_refresher(config))
    ai_client    = anthropic.Anthropic(api_key=api_key)
    conn         = connect(DB_PATH)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)

    where  = ["status = 'organized'", "new_path IS NOT NULL",
               "(media_type = 'photo' OR media_type IS NULL)"]
    params = []
    if not refill:
        where.append("ai_description IS NULL")
    if year:
        where.append("year = ?")
        params.append(year)
    if path:
        where.append("new_path LIKE ?")
        params.append(path.rstrip("/") + "/%")

    query = f"""
        SELECT id, new_path, filename, year, month, city, country
        FROM photos
        WHERE {' AND '.join(where)}
        ORDER BY year, month, new_path
    """
    if limit:
        query += f" LIMIT {limit}"

    rows = conn.execute(query, params).fetchall()

    if not rows:
        logger.info("No photos to describe — all done!")
        return

    logger.info(f"Describing {len(rows)} photos" +
                (f" from {year}" if year else "") +
                (f" matching '{path}'" if path else "") +
                (f" (limit {limit})" if limit else ""))

    done = skipped = errors = 0

    for num, row in enumerate(rows, start=1):
        photo = dict(row)
        logger.info(f"  ({num}/{len(rows)}) {photo['new_path']}")

        thumb = fetch_thumbnail(photo["new_path"], graph_client)
        if not thumb:
            logger.warning(f"    thumbnail not found — skip")
            skipped += 1
            continue

        tags = describe_photo(thumb, ai_client)
        if not tags:
            errors += 1
            continue

        logger.info(f"    → {tags}")

        conn.execute("UPDATE photos SET ai_description = ? WHERE id = ?",
                     (tags, photo["id"]))
        conn.commit()
        done += 1

    logger.info(f"\nSummary: {done} described, {skipped} skipped, {errors} errors")
    if done:
        logger.info("Reload the app and try searching!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year",   help="Only process photos from this year (e.g. 2013)")
    parser.add_argument("--path",   help="Only process photos whose new_path starts with this prefix (e.g. '2013/November/California/Cambrian Park')")
    parser.add_argument("--limit",  type=int, help="Stop after N photos (for testing)")
    parser.add_argument("--refill", action="store_true",
                        help="Re-describe photos that already have ai_description")
    args = parser.parse_args()
    run(year=args.year, path=args.path, limit=args.limit, refill=args.refill)
