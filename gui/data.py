"""data.py — read-only access to the photo_sorter metadata DB.

This is the ONE module that talks to the database. It opens the SQLite file
read-only (immutable snapshot) and only ever runs SELECTs. To deploy on Vercel
later, swap the connection here for Postgres/Turso and keep the same functions.

No photos are read from disk here — only metadata. Previews come from Graph
thumbnails (see auth.py), so the 140 GB of originals never touch the laptop.
"""

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

DEFAULT_SYSTEM_DIR = Path(
    os.environ.get(
        "PHOTO_SORTER_SYSTEM_DIR",
        r"C:\Users\sedaw\OneDrive\Photos\Sorted\_system",
    )
)
DB_PATH = Path(os.environ.get("PHOTO_SORTER_DB", str(DEFAULT_SYSTEM_DIR / "photo_sorter.db")))

# OneDrive root that the stored new_path values are relative to.
SORTED_ROOT = os.environ.get("PHOTO_SORTER_SORTED_ROOT", "/Photos/Sorted")

# ISO alpha-2 country codes seen in the archive, mapped to friendly names.
_COUNTRY_NAMES = {
    "US": "United States", "AU": "Australia", "NZ": "New Zealand",
    "CA": "Canada", "DE": "Germany", "ES": "Spain", "FR": "France",
    "IT": "Italy", "VA": "Vatican City", "HK": "Hong Kong", "GB": "United Kingdom",
    "MX": "Mexico", "JP": "Japan", "CN": "China", "NL": "Netherlands",
    "CH": "Switzerland", "AT": "Austria", "IE": "Ireland", "PT": "Portugal",
    "BE": "Belgium", "CZ": "Czechia", "GR": "Greece",
}


def country_name(code):
    if not code:
        return None
    return _COUNTRY_NAMES.get(code, code)


def onedrive_path_for(new_path):
    """Full OneDrive path for a stored new_path.

    Year-prefixed and Movies paths live under Primary/; the Unsorted bucket
    sits directly under the sorted root.
    """
    np = (new_path or "").lstrip("/")
    if np.startswith("Unsorted/"):
        return f"{SORTED_ROOT}/{np}"
    return f"{SORTED_ROOT}/Primary/{np}"


_SNAPSHOT = None


def _snapshot_path():
    """A stable read-only copy of the DB for this session.

    The source DB lives in a OneDrive-synced folder and can be replaced
    underneath us mid-session (a fresh sync, a cleanup run on the VM). Reading a
    private snapshot gives a consistent view for the whole session and can never
    touch the original. Restart the server to pick up a newer DB.
    """
    global _SNAPSHOT
    if _SNAPSHOT and Path(_SNAPSHOT).exists():
        return _SNAPSHOT
    snap = Path(tempfile.gettempdir()) / "photo_muse_snapshot.db"
    shutil.copy2(DB_PATH, snap)
    for suffix in ("-wal", "-shm"):  # include WAL sidecars if the source has them
        side = Path(str(DB_PATH) + suffix)
        if side.exists():
            shutil.copy2(side, Path(str(snap) + suffix))
    _SNAPSHOT = str(snap)
    return _SNAPSHOT


def _conn():
    c = sqlite3.connect(_snapshot_path(), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")  # belt-and-suspenders: never write, even to the copy
    return c


def _all(sql, params=()):
    c = _conn()
    try:
        return [dict(r) for r in c.execute(sql, params).fetchall()]
    finally:
        c.close()


def _one(sql, params=()):
    c = _conn()
    try:
        r = c.execute(sql, params).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def _caption(row):
    """A human place/time caption, e.g. 'Camperdown, Australia · 2013'."""
    place_bits = []
    if row.get("city"):
        place_bits.append(row["city"])
    if row.get("country") == "US" and row.get("state_or_region"):
        place_bits.append(row["state_or_region"])
    elif row.get("country"):
        place_bits.append(country_name(row["country"]))
    place = ", ".join(place_bits) if place_bits else "Somewhere"
    when = row.get("year") or ""
    if row.get("month") and when:
        when = f"{row['month']} {when}"
    return f"{place} · {when}".strip(" ·") if when else place


# ---------------------------------------------------------------- endpoints

def summary():
    kept = _one("SELECT COUNT(*) n FROM photos")["n"]
    photos = _one("SELECT COUNT(*) n FROM photos WHERE media_type='photo'")["n"]
    movies = _one("SELECT COUNT(*) n FROM photos WHERE media_type='movie'")["n"]
    seen = _one("SELECT COUNT(*) n FROM photo_occurrences")["n"]
    located = _one("SELECT COUNT(*) n FROM photos WHERE latitude IS NOT NULL")["n"]
    needs_where = _one(
        "SELECT COUNT(*) n FROM photos WHERE latitude IS NULL AND taken_date IS NOT NULL"
    )["n"]
    unsorted = _one(
        "SELECT COUNT(*) n FROM photos WHERE latitude IS NULL AND taken_date IS NULL"
    )["n"]
    years = [
        r["year"]
        for r in _all(
            "SELECT DISTINCT year FROM photos WHERE year GLOB '[0-9][0-9][0-9][0-9]'"
            " ORDER BY year"
        )
    ]
    cities = _one(
        "SELECT COUNT(DISTINCT city) n FROM photos WHERE city IS NOT NULL"
    )["n"]
    countries = _one(
        "SELECT COUNT(DISTINCT country) n FROM photos WHERE country IS NOT NULL"
    )["n"]
    span = f"{years[0]}–{years[-1]}" if years else ""
    return {
        "kept": kept, "photos": photos, "movies": movies, "seen": seen,
        "cleared": seen - kept, "located": located, "needs_where": needs_where,
        "unsorted": unsorted, "cities": cities, "countries": countries,
        "year_span": span, "first_year": years[0] if years else None,
        "last_year": years[-1] if years else None,
    }


def timeline():
    rows = _all(
        "SELECT year, COUNT(*) n FROM photos"
        " WHERE year GLOB '[0-9][0-9][0-9][0-9]' GROUP BY year ORDER BY year"
    )
    return {"years": rows}


def places(limit=None):
    rows = _all(
        "SELECT country, state_or_region, city, COUNT(*) n,"
        " AVG(latitude) lat, AVG(longitude) lon"
        " FROM photos WHERE city IS NOT NULL"
        " GROUP BY country, state_or_region, city ORDER BY n DESC"
    )
    for r in rows:
        r["label"] = r["city"]
        r["region"] = (
            r["state_or_region"] if r.get("country") == "US" else country_name(r.get("country"))
        )
        r["lat"] = round(r["lat"], 5) if r["lat"] is not None else None
        r["lon"] = round(r["lon"], 5) if r["lon"] is not None else None
    return {"places": rows[:limit] if limit else rows}


def story():
    s = summary()
    return {
        "seen": s["seen"], "cleared": s["cleared"], "kept": s["kept"],
        "photos": s["photos"], "movies": s["movies"], "located": s["located"],
        "needs_where": s["needs_where"], "unsorted": s["unsorted"],
        "cities": s["cities"], "countries": s["countries"], "year_span": s["year_span"],
    }


def serendipity():
    row = _one(
        "SELECT new_path, filename, year, month, city, state_or_region, country"
        " FROM photos WHERE media_type='photo' AND new_path IS NOT NULL"
        " AND latitude IS NOT NULL ORDER BY RANDOM() LIMIT 1"
    )
    if not row:
        return None
    row["caption"] = _caption(row)
    row["path"] = row["new_path"]
    return row


def browse(year=None, state=None, city=None, limit=120, offset=0):
    where, params = ["new_path IS NOT NULL"], []
    if year:
        where.append("year = ?"); params.append(year)
    if state:
        where.append("state_or_region = ?"); params.append(state)
    if city:
        where.append("city = ?"); params.append(city)
    clause = " AND ".join(where)
    total = _one(f"SELECT COUNT(*) n FROM photos WHERE {clause}", params)["n"]
    rows = _all(
        f"SELECT new_path, filename, year, month, city, state_or_region, country, media_type"
        f" FROM photos WHERE {clause} ORDER BY taken_date, filename LIMIT ? OFFSET ?",
        params + [limit, offset],
    )
    for r in rows:
        r["caption"] = _caption(r)
        r["path"] = r["new_path"]
    title_bits = [b for b in [city, state, year] if b]
    return {
        "title": " · ".join(title_bits) if title_bits else "Everything",
        "total": total, "offset": offset, "limit": limit, "items": rows,
    }
