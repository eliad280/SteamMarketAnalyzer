"""SQLite storage for price snapshots and a short-lived price cache.

The Steam Market has no public, unauthenticated price-history endpoint, so
this app builds its own history over time: every time a price is looked up
it is recorded here as a snapshot. The longer the app is used / left running,
the better the statistical prediction gets.
"""
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "market.db"
DB_PATH.parent.mkdir(exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appid INTEGER NOT NULL,
    market_hash_name TEXT NOT NULL,
    currency TEXT NOT NULL,
    price REAL NOT NULL,
    volume INTEGER,
    ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snap_lookup
    ON price_snapshots (appid, market_hash_name, currency, ts);

CREATE TABLE IF NOT EXISTS price_cache (
    appid INTEGER NOT NULL,
    market_hash_name TEXT NOT NULL,
    currency TEXT NOT NULL,
    price REAL,
    volume INTEGER,
    raw_lowest TEXT,
    raw_median TEXT,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (appid, market_hash_name, currency)
);

CREATE TABLE IF NOT EXISTS watchlist (
    appid INTEGER NOT NULL,
    market_hash_name TEXT NOT NULL,
    currency TEXT NOT NULL,
    added_at INTEGER NOT NULL,
    PRIMARY KEY (appid, market_hash_name, currency)
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def add_snapshot(appid: int, market_hash_name: str, currency: str, price: float, volume: int | None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO price_snapshots (appid, market_hash_name, currency, price, volume, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (appid, market_hash_name, currency, price, volume, int(time.time())),
        )
        conn.execute(
            "INSERT INTO watchlist (appid, market_hash_name, currency, added_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(appid, market_hash_name, currency) DO NOTHING",
            (appid, market_hash_name, currency, int(time.time())),
        )


def get_history(appid: int, market_hash_name: str, currency: str, limit: int = 500):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT price, volume, ts FROM price_snapshots "
            "WHERE appid=? AND market_hash_name=? AND currency=? ORDER BY ts ASC LIMIT ?",
            (appid, market_hash_name, currency, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_watchlist():
    with get_conn() as conn:
        rows = conn.execute("SELECT appid, market_hash_name, currency FROM watchlist").fetchall()
        return [dict(r) for r in rows]


def cache_get(appid: int, market_hash_name: str, currency: str, max_age_seconds: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT price, volume, raw_lowest, raw_median, fetched_at FROM price_cache "
            "WHERE appid=? AND market_hash_name=? AND currency=?",
            (appid, market_hash_name, currency),
        ).fetchone()
        if not row:
            return None
        if time.time() - row["fetched_at"] > max_age_seconds:
            return None
        return dict(row)


def cache_put(appid: int, market_hash_name: str, currency: str, price: float | None,
              volume: int | None, raw_lowest: str | None, raw_median: str | None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO price_cache (appid, market_hash_name, currency, price, volume, raw_lowest, raw_median, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(appid, market_hash_name, currency) DO UPDATE SET "
            "price=excluded.price, volume=excluded.volume, raw_lowest=excluded.raw_lowest, "
            "raw_median=excluded.raw_median, fetched_at=excluded.fetched_at",
            (appid, market_hash_name, currency, price, volume, raw_lowest, raw_median, int(time.time())),
        )
