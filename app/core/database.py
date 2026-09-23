"""Kết nối PostgreSQL (SQLAlchemy 2.0, sync).

- Schema do Alembic quản lý — cấm `Base.metadata.create_all()`.
- Ranh giới transaction nằm ở tầng SERVICE: router không commit, repository không commit.
- Worker là tiến trình riêng nên có pool riêng, nhỏ hơn pool của API.
"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,          # tránh dùng connection đã chết sau khi Postgres restart
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """Dependency cấp Session cho mỗi request.

    Rollback tường minh khi use case ném lỗi: `close()` có rollback ngầm khi trả
    connection về pool, nhưng dựa vào hành vi ngầm là mong manh.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
