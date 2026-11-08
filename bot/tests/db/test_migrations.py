"""Миграции применяются, откатываются и совпадают с моделями."""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from tsproxy.db.models import Base

ROOT = Path(__file__).resolve().parents[2]
TABLES = {
    "clients",
    "orders",
    "notifications",
    "events",
    "support_tickets",
    "ticket_messages",
    "refund_requests",
    "node_outages",
}


def alembic_config(db_path: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    config.attributes["configure_logger"] = False
    return config


def table_names(db_path: Path) -> set[str]:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        return set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    config = alembic_config(db_path)

    command.upgrade(config, "head")
    assert table_names(db_path) == TABLES

    command.downgrade(config, "base")
    assert table_names(db_path) == set()

    command.upgrade(config, "head")
    assert table_names(db_path) == TABLES


def test_migrations_match_models(tmp_path: Path) -> None:
    db_path = tmp_path / "compare.db"
    command.upgrade(alembic_config(db_path), "head")

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()
