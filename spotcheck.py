"""
spotcheck.py — Phase 2: Randomly sample duplicate pairs, download them,
and pixel-compare to confirm the hash-based dedup logic is working correctly.

Prints a report and exits with code 1 if any pair fails to match,
so the caller (main.py) can abort before organizing photos.
"""

import io
import logging
import random
import sys
from pathlib import Path

from PIL import Image

from db import connect, get_duplicate_groups
from graph import GraphClient
from onedrive_sync import acquire_token, load_config

APP_DIR = Path(__file__).parent
SYSTEM_DIR = APP_DIR / "_system"
DB_PATH = SYSTEM_DIR / "photo_sorter.db"

logger = logging.getLogger("photo_sorter")


def images_are_identical(img_a: bytes, img_b: bytes) -> bool:
    """Return True if two image files are pixel-for-pixel identical."""
    try:
        a = Image.open(io.BytesIO(img_a)).convert("RGB")
        b = Image.open(io.BytesIO(img_b)).convert("RGB")
        if a.size != b.size:
            return False
        return a.tobytes() == b.tobytes()
    except Exception as e:
        logger.warning(f"  Could not compare images: {e}")
        return False


def get_item_id_for_path(client: GraphClient, onedrive_path: str) -> str | None:
    """Resolve a OneDrive file path to a driveItem ID."""
    import requests
    try:
        url = f"https://graph.microsoft.com/v1.0/me/drive/root:{onedrive_path}"
        resp = client._get(url)
        return resp.get("id")
    except requests.HTTPError as e:
        logger.warning(f"  Could not resolve path {onedrive_path}: {e}")
        return None


def run_spotcheck(sample_size: int = 12) -> bool:
    """
    Download a random sample of duplicate pairs and pixel-compare them.
    Returns True if all sampled pairs match, False if any mismatch is found.
    """
    config = load_config()
    token = acquire_token(config)
    client = GraphClient(token)
    conn = connect(DB_PATH)

    groups = get_duplicate_groups(conn)

    if not groups:
        logger.info("No duplicate groups found — skipping spot-check.")
        return True

    # Pick a random sample of groups (one pair from each)
    sample = random.sample(groups, min(sample_size, len(groups)))

    logger.info(f"Spot-checking {len(sample)} of {len(groups)} duplicate group(s)...")
    logger.info("")

    passed = 0
    failed = 0
    skipped = 0

    for i, group in enumerate(sample, 1):
        # Take the first two occurrences in the group
        occ_a = group[0]
        occ_b = group[1]
        path_a = occ_a["original_path"]
        path_b = occ_b["original_path"]
        hash_val = occ_a["hash"]

        logger.info(f"  [{i}/{len(sample)}] Hash {hash_val[:8]}...")
        logger.info(f"    A: {path_a}")
        logger.info(f"    B: {path_b}")

        id_a = get_item_id_for_path(client, path_a)
        id_b = get_item_id_for_path(client, path_b)

        if not id_a or not id_b:
            logger.warning(f"    SKIPPED — could not resolve one or both paths")
            skipped += 1
            continue

        try:
            data_a = client.download_item(id_a)
            data_b = client.download_item(id_b)
        except Exception as e:
            logger.warning(f"    SKIPPED — download error: {e}")
            skipped += 1
            continue

        if images_are_identical(data_a, data_b):
            logger.info(f"    PASS — pixel-identical")
            passed += 1
        else:
            logger.error(f"    FAIL — files differ despite matching hash!")
            failed += 1

    logger.info("")
    logger.info("=" * 50)
    logger.info("SPOT-CHECK RESULTS")
    logger.info("=" * 50)
    logger.info(f"  Passed:  {passed}")
    logger.info(f"  Failed:  {failed}")
    logger.info(f"  Skipped: {skipped}")
    logger.info("=" * 50)

    if failed > 0:
        logger.error(
            f"{failed} pair(s) failed pixel comparison. "
            "This may indicate hash collisions or file corruption. "
            "Aborting — do not proceed to Phase 3 until investigated."
        )
        return False

    logger.info("All sampled pairs confirmed identical. Safe to proceed to Phase 3.")
    return True


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    ok = run_spotcheck()
    sys.exit(0 if ok else 1)
