"""SQLite access. One file on disk, no server to run or pay for."""

import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta

from flask import g

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(HERE, "data")


def data_dir():
    path = os.environ.get("DATA_DIR", DEFAULT_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def db_path():
    return os.path.join(data_dir(), "cookbook.db")


def connect():
    conn = sqlite3.connect(db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 8000")
    return conn


def get():
    if "db" not in g:
        g.db = connect()
    return g.db


def close(_exception=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init():
    with open(os.path.join(HERE, "schema.sql"), "r", encoding="utf-8") as handle:
        script = handle.read()
    conn = connect()
    try:
        conn.executescript(script)
        migrate(conn)
        conn.commit()
    finally:
        conn.close()


def columns(conn, table):
    return {row["name"] for row in conn.execute("PRAGMA table_info(%s)" % table)}


def migrate(conn):
    """Bring an older database up to the current schema.

    schema.sql only ever CREATEs IF NOT EXISTS, so a database that already has
    recipes in it would never pick up a new column on its own. Every step here
    is checked first, so running it on an up-to-date database does nothing.
    """
    recipe_columns = columns(conn, "recipes")
    if not recipe_columns:
        return

    # Recipes used to have one "author", conflating the person who typed the
    # recipe in with the person whose recipe it is.
    if "author_id" in recipe_columns and "submitted_by_id" not in recipe_columns:
        conn.execute("ALTER TABLE recipes RENAME COLUMN author_id TO submitted_by_id")
    if "author_name" in recipe_columns and "submitted_by_name" not in recipe_columns:
        conn.execute(
            "ALTER TABLE recipes RENAME COLUMN author_name TO submitted_by_name"
        )
    if "credited_to" not in recipe_columns:
        conn.execute(
            "ALTER TABLE recipes ADD COLUMN credited_to TEXT NOT NULL DEFAULT ''"
        )


def now():
    return datetime.utcnow().isoformat(timespec="seconds")


# --- helpers ---------------------------------------------------------------

def slugify(text, fallback="recipe"):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text[:70] or fallback


def unique_slug(conn, title, ignore_id=None):
    base = slugify(title)
    slug = base
    suffix = 2
    while True:
        row = conn.execute(
            "SELECT id FROM recipes WHERE slug = ?", (slug,)
        ).fetchone()
        if row is None or row["id"] == ignore_id:
            return slug
        slug = "%s-%d" % (base, suffix)
        suffix += 1


def setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# --- rate limiting ---------------------------------------------------------

def rate_ok(conn, bucket, limit, minutes):
    """True if `bucket` has fewer than `limit` events in the last `minutes`."""
    cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM rate_events WHERE bucket = ? AND created_at > ?",
        (bucket, cutoff),
    ).fetchone()
    return row["n"] < limit


def rate_hit(conn, bucket):
    conn.execute(
        "INSERT INTO rate_events(bucket, created_at) VALUES(?, ?)", (bucket, now())
    )
    # Opportunistic sweep so the table never grows without bound.
    old = (datetime.utcnow() - timedelta(days=1)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM rate_events WHERE created_at < ?", (old,))
