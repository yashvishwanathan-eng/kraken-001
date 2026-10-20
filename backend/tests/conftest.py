"""Test setup: point the app at a separate test database before anything imports app.db.

The test database is the dev DATABASE_URL with "_test" added to the database name
(override with TEST_DATABASE_URL). It is migrated once per run, and wiped between tests that use it.
If it cannot be reached, database tests are skipped instead of failing.
"""

import os

import pytest

from app.config import BACKEND_DIR, Settings


def _test_database_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    dev_url = Settings().database_url
    base, _, name = dev_url.rpartition("/")
    return f"{base}/{name}_test" if dev_url else ""


os.environ["DATABASE_URL"] = _test_database_url()


@pytest.fixture(scope="session")
def migrated_db():
    from alembic import command
    from alembic.config import Config

    if not os.environ["DATABASE_URL"]:
        pytest.skip("no DATABASE_URL configured")
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    try:
        command.upgrade(config, "head")
    except Exception as exc:  # database down or test database missing
        pytest.skip(f"test database not available: {exc}")
    return os.environ["DATABASE_URL"]


@pytest.fixture
def db(migrated_db):
    """A session on a freshly emptied test database."""
    from sqlalchemy import text

    from app.db import SessionLocal, engine
    from app.models import Base

    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    with SessionLocal() as session:
        yield session
