"""SQLAlchemy Core engine and session helpers.

Uses Core (not ORM) to keep the footprint light for Raspberry Pi deployment.
All table definitions live in alembic/versions; this module only manages
the engine and provides a context-managed connection helper.
"""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

# Re-export text so callers can do: from src.common.database import get_connection, text

from src.common.config import settings

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.database_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,  # recycle stale connections
        )
    return _engine


@contextmanager
def get_connection() -> Generator[Connection, None, None]:
    """Yield a SQLAlchemy connection, auto-committing on clean exit."""
    with get_engine().begin() as conn:
        yield conn


def check_connectivity() -> bool:
    """Return True if the database is reachable."""
    try:
        with get_connection() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
