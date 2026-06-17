"""
db.py — SQLite database initialization and access.
"""

import sqlite3
from pathlib import Path


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS photos (
            id              INTEGER PRIMARY KEY,
            hash            TEXT NOT NULL,
            image_unique_id TEXT,
            original_path   TEXT NOT NULL,
            new_path        TEXT,
            filename        TEXT NOT NULL,
            taken_date      TEXT,
            year            TEXT,
            month           TEXT,
            latitude        REAL,
            longitude       REAL,
            city            TEXT,
            state_or_region TEXT,
            country         TEXT,
            width           INTEGER,
            height          INTEGER,
            camera_make        TEXT,
            camera_model       TEXT,
            folder_description TEXT,
            user_description   TEXT,
            status             TEXT,
            processed_at       TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_hash
            ON photos(hash);

        CREATE INDEX IF NOT EXISTS idx_image_unique_id
            ON photos(image_unique_id)
            WHERE image_unique_id IS NOT NULL;

        CREATE INDEX IF NOT EXISTS idx_status
            ON photos(status);
    """)
    conn.commit()


def upsert_photo(conn: sqlite3.Connection, photo: dict):
    cols = ", ".join(photo.keys())
    placeholders = ", ".join(f":{k}" for k in photo.keys())
    updates = ", ".join(
        f"{k} = excluded.{k}"
        for k in photo.keys()
        if k not in ("id", "hash")
    )
    conn.execute(
        f"""
        INSERT INTO photos ({cols}) VALUES ({placeholders})
        ON CONFLICT(hash) DO UPDATE SET {updates}
        """,
        photo,
    )
    conn.commit()


def get_by_hash(conn: sqlite3.Connection, hash_val: str):
    return conn.execute(
        "SELECT * FROM photos WHERE hash = ?", (hash_val,)
    ).fetchone()


def get_by_image_unique_id(conn: sqlite3.Connection, uid: str):
    return conn.execute(
        "SELECT * FROM photos WHERE image_unique_id = ?", (uid,)
    ).fetchone()


def get_duplicate_groups(conn: sqlite3.Connection) -> list[list[sqlite3.Row]]:
    """Return groups of rows that share a hash (more than one file per hash)."""
    rows = conn.execute("""
        SELECT * FROM photos
        WHERE hash IN (
            SELECT hash FROM photos GROUP BY hash HAVING COUNT(*) > 1
        )
        ORDER BY hash
    """).fetchall()

    groups: list[list[sqlite3.Row]] = []
    current_hash = None
    current_group: list[sqlite3.Row] = []
    for row in rows:
        if row["hash"] != current_hash:
            if current_group:
                groups.append(current_group)
            current_group = [row]
            current_hash = row["hash"]
        else:
            current_group.append(row)
    if current_group:
        groups.append(current_group)
    return groups
