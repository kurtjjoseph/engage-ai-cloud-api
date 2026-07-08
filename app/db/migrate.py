from sqlalchemy import inspect, text
from app.db.session import Base, engine


def sync_missing_columns() -> None:
    """Base.metadata.create_all() only creates tables that don't exist yet -
    it never alters a table that's already there. That's exactly what broke
    production 2026-07-08: Organization gained enabled_modules/agent_profiles
    after real rows already existed, so every query touching Organization
    started raising UndefinedColumn.

    This is a stopgap short of real Alembic migrations (still worth doing
    eventually - see ARCHITECTURE.md): on every startup, diff each model's
    columns against the live table and ADD COLUMN IF NOT EXISTS for
    anything missing. New columns land as nullable with no default, which
    matches how the codebase already treats them (e.g. `org.enabled_modules
    or []`), so existing rows getting NULL is the correct, expected outcome.

    Postgres only - SQLite (local dev) just uses a fresh file each time, so
    there's nothing to reconcile there.
    """
    if engine.dialect.name != "postgresql":
        return

    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue  # brand-new table - create_all already made it correctly
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                conn.execute(text(
                    f'ALTER TABLE "{table.name}" ADD COLUMN IF NOT EXISTS "{column.name}" {col_type}'
                ))
