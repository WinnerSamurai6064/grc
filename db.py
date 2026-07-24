"""
Green Recruiters - Database Layer
Handles the PostgreSQL (Neon) connection pool and low-level query helpers.
All other backend modules import from here rather than opening their own connections.
"""

import os
from contextlib import contextmanager
from typing import Any, Iterable, Optional

import psycopg2
import psycopg2.extras
from psycopg2.pool import SimpleConnectionPool

DATABASE_URL = os.environ["DATABASE_URL"]

MIN_CONN = int(os.environ.get("DB_POOL_MIN", "1"))
MAX_CONN = int(os.environ.get("DB_POOL_MAX", "10"))

_pool: Optional[SimpleConnectionPool] = None


def init_pool() -> None:
    """Create the connection pool. Call once on application startup."""
    global _pool
    if _pool is None:
        _pool = SimpleConnectionPool(MIN_CONN, MAX_CONN, dsn=DATABASE_URL)


def close_pool() -> None:
    """Close all connections. Call once on application shutdown."""
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


@contextmanager
def get_conn():
    """Borrow a connection from the pool and guarantee it is returned."""
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_pool() first.")
    conn = _pool.getconn()
    try:
        yield conn
    finally:
        _pool.putconn(conn)


@contextmanager
def get_cursor(commit: bool = False):
    """
    Yield a RealDictCursor (rows behave like dicts).
    Set commit=True for INSERT/UPDATE/DELETE statements.
    Rolls back automatically if an exception is raised.
    """
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cur
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


def fetch_one(query: str, params: Iterable[Any] = ()) -> Optional[dict]:
    """Run a SELECT and return the first row as a dict, or None."""
    with get_cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
        return dict(row) if row else None


def fetch_all(query: str, params: Iterable[Any] = ()) -> list[dict]:
    """Run a SELECT and return all rows as a list of dicts."""
    with get_cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]


def execute(query: str, params: Iterable[Any] = ()) -> None:
    """Run an INSERT/UPDATE/DELETE with no return value needed."""
    with get_cursor(commit=True) as cur:
        cur.execute(query, params)


def execute_returning(query: str, params: Iterable[Any] = ()) -> Optional[dict]:
    """
    Run an INSERT/UPDATE with a RETURNING clause.
    Returns the returned row as a dict, or None.
    """
    with get_cursor(commit=True) as cur:
        cur.execute(query, params)
        row = cur.fetchone()
        return dict(row) if row else None
