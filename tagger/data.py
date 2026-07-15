"""
data.py — Read-only DB queries for the photo tagger GUI (Phase 1).
All functions accept a sqlite3.Connection and return plain dicts/lists
so the FastAPI layer stays thin.
"""

import sqlite3
from pathlib import Path


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ── Summary stats ─────────────────────────────────────────────────────────────

def get_stats(conn: sqlite3.Connection) -> dict:
    """Top-level numbers for the hero section."""
    row = conn.execute("""
        SELECT
            COUNT(*) FILTER (WHERE status='organized') AS organized,
            COUNT(*) FILTER (WHERE status='organized' AND city IS NOT NULL) AS with_location,
            COUNT(*) FILTER (WHERE status='organized' AND city IS NULL
                              AND taken_date IS NOT NULL) AS date_only,
            COUNT(*) FILTER (WHERE status='organized' AND taken_date IS NULL) AS unsorted,
            COUNT(*) FILTER (WHERE media_type='movie' AND status='organized') AS movies
        FROM photos
    """).fetchone()
    dup_count = conn.execute("""
        SELECT COUNT(*) FROM photo_occurrences
        WHERE hash IN (SELECT hash FROM photo_occurrences GROUP BY hash HAVING COUNT(*) > 1)
    """).fetchone()[0]
    return {**dict(row), "duplicates_skipped": dup_count}


# ── Date view ─────────────────────────────────────────────────────────────────

def get_years(conn: sqlite3.Connection) -> list[dict]:
    """All years with photo counts, newest first."""
    rows = conn.execute("""
        SELECT year, COUNT(*) AS count
        FROM photos
        WHERE status='organized' AND year IS NOT NULL
        GROUP BY year
        ORDER BY year DESC
    """).fetchall()
    return [dict(r) for r in rows]


def get_months(conn: sqlite3.Connection, year: str) -> list[dict]:
    """Months for a given year with counts, in calendar order."""
    month_order = {
        "January": 1, "February": 2, "March": 3, "April": 4,
        "May": 5, "June": 6, "July": 7, "August": 8,
        "September": 9, "October": 10, "November": 11, "December": 12,
    }
    rows = conn.execute("""
        SELECT month, COUNT(*) AS count
        FROM photos
        WHERE status='organized' AND year=? AND month IS NOT NULL
        GROUP BY month
    """, (year,)).fetchall()
    result = [dict(r) for r in rows]
    result.sort(key=lambda r: month_order.get(r["month"], 99))
    return result


def get_photos_by_month(conn: sqlite3.Connection, year: str, month: str,
                        limit: int = 200, offset: int = 0) -> list[dict]:
    """Photos for a year/month, grouped by location for display."""
    rows = conn.execute("""
        SELECT hash, filename, new_path, city, state_or_region, country,
               taken_date, camera_make, camera_model, media_type,
               folder_description, user_description
        FROM photos
        WHERE status='organized' AND year=? AND month=?
        ORDER BY
            CASE WHEN city IS NOT NULL THEN 0 ELSE 1 END,
            country, state_or_region, city, taken_date
        LIMIT ? OFFSET ?
    """, (year, month, limit, offset)).fetchall()
    return [dict(r) for r in rows]


# ── Location view ─────────────────────────────────────────────────────────────

def get_locations(conn: sqlite3.Connection) -> list[dict]:
    """All locations with photo counts, sorted by count descending."""
    rows = conn.execute("""
        SELECT city, state_or_region, country,
               AVG(latitude) AS lat, AVG(longitude) AS lon,
               COUNT(*) AS count
        FROM photos
        WHERE status='organized' AND city IS NOT NULL
        GROUP BY city, state_or_region, country
        ORDER BY count DESC
    """).fetchall()
    return [dict(r) for r in rows]


def get_years_for_location(conn: sqlite3.Connection,
                           city: str, country: str) -> list[dict]:
    """Year/month breakdown for a specific location."""
    rows = conn.execute("""
        SELECT year, month, COUNT(*) AS count
        FROM photos
        WHERE status='organized' AND city=? AND country=?
        GROUP BY year, month
        ORDER BY year DESC, month
    """, (city, country)).fetchall()
    return [dict(r) for r in rows]


def get_photos_by_location(conn: sqlite3.Connection, city: str, country: str,
                           year: str | None = None, month: str | None = None,
                           limit: int = 200, offset: int = 0) -> list[dict]:
    """Photos for a location, optionally filtered by year/month."""
    query = """
        SELECT hash, filename, new_path, city, state_or_region, country,
               taken_date, year, month, camera_make, camera_model, media_type,
               folder_description, user_description
        FROM photos
        WHERE status='organized' AND city=? AND country=?
    """
    params: list = [city, country]
    if year:
        query += " AND year=?"
        params.append(year)
    if month:
        query += " AND month=?"
        params.append(month)
    query += " ORDER BY taken_date LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ── Thumbnail path helper ─────────────────────────────────────────────────────

def onedrive_path_for_photo(photo: dict, sorted_root: str) -> str:
    """
    Reconstruct the full OneDrive path for a photo given its new_path.
    new_path is relative e.g. '2019/June/California/San Francisco/IMG_1234.JPG'
    """
    new_path = photo.get("new_path") or ""
    if new_path.startswith("Unsorted"):
        return f"{sorted_root}/Unsorted/{new_path.removeprefix('Unsorted/').removeprefix('Unsorted')}"
    return f"{sorted_root}/Primary/{new_path}"
