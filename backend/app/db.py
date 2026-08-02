"""Database engine, session factory, and the FastAPI session dependency."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

# pool_pre_ping recovers from connections the DB dropped while idle. Pool kept
# small on purpose: 2 gunicorn workers × (5 + 2 overflow) = 14 connections max,
# well under the Azure Postgres B1ms ceiling (~35). Ignored under SQLite tests,
# which build their own StaticPool engine (see tests/conftest.py).
engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=2,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    """One session per request; always closed, even on error."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
