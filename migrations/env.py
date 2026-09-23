"""Cấu hình Alembic.

Nạp ĐẦY ĐỦ entity ở đây — quên một file là autogenerate sinh lệnh DROP nhầm.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.core.database import Base

# --- Nạp entity (thứ tự không quan trọng, nhưng phải đủ) ---
from app.modules.identity.entity import User  # noqa: F401
from app.modules.scraper.job.entity import JobQuery, ScrapeJob  # noqa: F401
from app.modules.scraper.place.entity import JobPlace, Place, PlaceKeyword  # noqa: F401
from app.modules.scraper.status.entity import WorkerStatus  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
