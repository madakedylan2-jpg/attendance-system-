"""
database.py
Lightweight SQLite data-access layer for the Barcode Attendance Tracking System.
No hardware drivers, no external DB server required — everything lives in one file
(instance/attendance.db) so the whole system runs on plain software.
"""
import sqlite3
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "instance", "attendance.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(reset=False):
    """Create (or reset) the database from schema.sql."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    first_time = not os.path.exists(DB_PATH)
    conn = get_db()
    if first_time or reset:
        with open(SCHEMA_PATH, "r") as f:
            conn.executescript(f.read())
        conn.commit()
    ensure_messaging_tables(conn)
    ensure_reset_requests_table(conn)
    conn.close()
    return first_time or reset


def ensure_messaging_tables(conn):
    """
    Adds the internal messaging tables if they don't exist yet — a safe,
    additive migration so upgrading an existing database (with real
    students/attendance already in it) never has to be reset. Runs every
    startup; CREATE TABLE IF NOT EXISTS makes repeats harmless.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_role TEXT NOT NULL CHECK (sender_role IN ('admin','lecturer')),
            sender_id   INTEGER NOT NULL,
            sender_name TEXT NOT NULL,
            target_desc TEXT NOT NULL,
            subject     TEXT NOT NULL,
            body        TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS message_recipients (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id     INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            recipient_type TEXT NOT NULL CHECK (recipient_type IN ('student','lecturer')),
            recipient_id   INTEGER NOT NULL,
            read_at        TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_msg_recipient
            ON message_recipients (recipient_type, recipient_id, read_at);
        """
    )
    conn.commit()

    # --- Safety-net column migration -----------------------------------
    # CREATE TABLE IF NOT EXISTS is a no-op on a `messages` table that was
    # already created by an OLDER version of this schema (before
    # target_desc existed). That leaves the table missing this column
    # even though the code above expects it, which crashes every send
    # with "table messages has no column named target_desc". This adds
    # the column to an existing table if it's missing, without touching
    # or deleting a single row of existing message data.
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
    if "target_desc" not in existing_columns:
        conn.execute("ALTER TABLE messages ADD COLUMN target_desc TEXT NOT NULL DEFAULT ''")
        conn.commit()


def ensure_reset_requests_table(conn):
    """
    Adds the password_reset_requests table if it doesn't exist yet — same
    safe, additive pattern as ensure_messaging_tables. Lets a student or
    lecturer who forgot their password submit a real request (instead of
    just being told "contact your administrator" with nothing to click),
    which then shows up for admin to act on.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS password_reset_requests (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            requester_type TEXT NOT NULL CHECK (requester_type IN ('student','lecturer')),
            requester_id   INTEGER,
            username       TEXT NOT NULL,
            full_name      TEXT,
            message        TEXT,
            status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','resolved')),
            created_at     TEXT NOT NULL DEFAULT (datetime('now')),
            resolved_at    TEXT,
            resolved_by    TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_reset_requests_status
            ON password_reset_requests (status, created_at);
        """
    )
    conn.commit()


def dict_from_row(row):
    return dict(row) if row else None


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------
# Settings helpers (admin-configurable key/value pairs)
# ----------------------------------------------------------------------
DEFAULT_SETTINGS = {
    "institution_name": "Telone Centre for Learning",
    "late_threshold_minutes": "15",
    "low_attendance_percent": "75",
}


def get_setting(key, default=None):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if row is not None:
        return row["value"]
    return DEFAULT_SETTINGS.get(key, default)


def get_all_settings():
    conn = get_db()
    rows = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    conn.close()
    merged = dict(DEFAULT_SETTINGS)
    merged.update(rows)
    return merged


def set_setting(key, value):
    conn = get_db()
    conn.execute(
        """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')""",
        (key, str(value)),
    )
    conn.commit()
    conn.close()
