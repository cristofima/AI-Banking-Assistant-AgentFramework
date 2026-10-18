from __future__ import annotations

from collections.abc import Mapping
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from banking_data.database import get_database_url
from banking_data.models import SQLModel

config = context.config
config.set_main_option("sqlalchemy.url", get_database_url().replace("%", "%%"))

if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def include_name(
    name: str | None, type_: str, parent_names: Mapping[str, str | None],
) -> bool:
    """Reflect managed tables only; retained or unrelated tables are not drop candidates."""
    if type_ == "schema":
        return name in (None, "support", "oauth")
    if type_ == "table":
        return parent_names["schema_qualified_table_name"] in target_metadata.tables
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=include_name,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
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
        context.configure(
            connection=connection, target_metadata=target_metadata,
            include_schemas=True, include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
