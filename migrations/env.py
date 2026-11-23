"""Alembic environment: platform models, URL from Quantera settings, optional schema."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

from quantera.core.config import get_settings
from quantera.db_models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
settings = get_settings()
schema = config.attributes.get("schema") or settings.db_schema


def include_object(obj: object, name: str | None, type_: str, reflected: bool,
                   compare_to: object) -> bool:
    return not (type_ == "table" and name == "alembic_version")  # Alembic's own bookkeeping


def run_migrations_offline() -> None:
    context.configure(url=settings.database_url, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(settings.database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        if schema:
            connection.execute(text(f'SET search_path TO "{schema}"'))
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True, version_table_schema=schema,
                          include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
