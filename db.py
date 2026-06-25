"""
db.py — SQLite database initialization and access.

Schema design:
  photos            — one row per unique photo (keyed by hash), tracks the winner
  photo_occurrences — one row per file path seen, so duplicates are never lost
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
            processed_at       TEXT,
            media_type         TEXT,   -- 'photo' or 'movie'
            file_size          INTEGER -- bytes, used for soft duplicate detection
        );
    """)
    # Migrate existing DBs that predate these columns
    for col, col_type in [("media_type", "TEXT"), ("file_size", "INTEGER")]:
        try:
            conn.execute(f"ALTER TABLE photos ADD COLUMN {col} {col_type}")
            conn.commit()
        except Exception:
            pass  # Column already exists
    conn.executescript("""

        CREATE UNIQUE INDEX IF NOT EXISTS idx_hash
            ON photos(hash);

        CREATE INDEX IF NOT EXISTS idx_image_unique_id
            ON photos(image_unique_id)
            WHERE image_unique_id IS NOT NULL;

        CREATE INDEX IF NOT EXISTS idx_status
            ON photos(status);

        -- Every file path seen for a given hash, including duplicates.
        -- original_path is the full OneDrive path of this specific file.
        -- is_winner = 1 for the copy that will be organised into the hierarchy.
        CREATE TABLE IF NOT EXISTS photo_occurrences (
            id            INTEGER PRIMARY KEY,
            hash          TEXT NOT NULL,
            original_path TEXT NOT NULL,
            folder_description TEXT,
            is_winner     INTEGER NOT NULL DEFAULT 0,
            scanned_at    TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_occurrence_path
            ON photo_occurrences(original_path);

        CREATE INDEX IF NOT EXISTS idx_occurrence_hash
            ON photo_occurrences(hash);
    """)
    conn.commit()


def upsert_photo(conn: sqlite3.Connection, photo: dict):
    """Insert or update the canonical photo record (one per hash)."""
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


def record_occurrence(conn: sqlite3.Connection, hash_val: str,
                      original_path: str, folder_description: str | None,
                      scanned_at: str):
    """Record every file path seen for a hash. Idempotent on original_path."""
    conn.execute(
        """
        INSERT INTO photo_occurrences (hash, original_path, folder_description, scanned_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(original_path) DO UPDATE SET
            folder_description = excluded.folder_description,
            scanned_at = excluded.scanned_at
        """,
        (hash_val, original_path, folder_description, scanned_at),
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
    """Return occurrence groups where the same hash appears more than once."""
    rows = conn.execute("""
        SELECT * FROM photo_occurrences
        WHERE hash IN (
            SELECT hash FROM photo_occurrences GROUP BY hash HAVING COUNT(*) > 1
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
