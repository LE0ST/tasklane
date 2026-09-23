import os
from datetime import datetime, timezone
from pathlib import Path
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from alembic.config import Config
from alembic import command

from models import Task
from schemas import TaskStatus, TaskPriority

from sqlalchemy.engine import make_url

TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/tasklane_test"
)

# Safety check: ensure test URL targets a dedicated test database by checking parsed database name
_test_url_obj = make_url(TEST_DB_URL)
_test_db_name = _test_url_obj.database or ""

assert _test_db_name == "tasklane_test" or _test_db_name.endswith("_test"), (
    f"Safety violation: integration tests must target a dedicated test database (ending in '_test' or named 'tasklane_test'), got database name: '{_test_db_name}' from URL: {TEST_DB_URL}"
)
assert _test_db_name != "tasklane", (
    f"Safety violation: integration tests must never run against main database '{_test_db_name}'"
)


def is_postgres_available() -> bool:
    try:
        engine = create_engine(TEST_DB_URL, connect_args={"connect_timeout": 2})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not is_postgres_available(),
        reason="PostgreSQL test database not available at localhost:5432/tasklane_test"
    )
]


@pytest.fixture(scope="session")
def setup_postgres_schema():
    """Apply Alembic migrations to the isolated test database."""
    alembic_ini_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    alembic_cfg = Config(str(alembic_ini_path))
    alembic_cfg.set_main_option("sqlalchemy.url", TEST_DB_URL)

    # Run upgrade head on test database
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(TEST_DB_URL, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(setup_postgres_schema):
    """Provide an isolated database session per test, rolling back changes after test."""
    connection = setup_postgres_schema.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")
    session = Session()

    yield session

    session.close()
    if transaction.is_active:
        transaction.rollback()
    connection.close()


def test_postgres_alembic_migration_schema(setup_postgres_schema):
    """Verify that Alembic migrations create the tasks and alembic_version tables in PostgreSQL."""
    with setup_postgres_schema.connect() as conn:
        # Check alembic_version
        result = conn.execute(text("SELECT version_num FROM alembic_version;"))
        version = result.scalar()
        assert version == "1ea1353bfe87"

        # Check tasks table columns
        cols = conn.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'tasks' ORDER BY ordinal_position;"
        )).fetchall()
        col_names = [c[0] for c in cols]
        assert "id" in col_names
        assert "title" in col_names
        assert "description" in col_names
        assert "status" in col_names
        assert "priority" in col_names
        assert "created_at" in col_names
        assert "updated_at" in col_names


def test_postgres_task_creation_and_retrieval(db_session):
    """Verify task creation and retrieval in PostgreSQL with autoincrement sequence."""
    task = Task(
        title="PostgreSQL Integration Task",
        description="Verify relational storage in Postgres",
        status=TaskStatus.backlog.value,
        priority=TaskPriority.high.value,
    )
    db_session.add(task)
    db_session.commit()

    assert task.id is not None
    assert task.id > 0
    assert task.title == "PostgreSQL Integration Task"
    assert task.status == "backlog"
    assert task.priority == "high"

    # Query back
    fetched = db_session.query(Task).filter(Task.id == task.id).first()
    assert fetched is not None
    assert fetched.id == task.id
    assert fetched.description == "Verify relational storage in Postgres"


def test_postgres_task_status_transitions(db_session):
    """Verify valid status progression in PostgreSQL."""
    task = Task(
        title="Lifecycle Task",
        description="Testing column transitions",
        status="backlog",
        priority="medium",
    )
    db_session.add(task)
    db_session.commit()

    # Move to in_progress
    task.status = "in_progress"
    task.updated_at = datetime.now(timezone.utc)
    db_session.commit()

    fetched = db_session.query(Task).filter(Task.id == task.id).first()
    assert fetched.status == "in_progress"

    # Move to done
    task.status = "done"
    task.updated_at = datetime.now(timezone.utc)
    db_session.commit()

    fetched2 = db_session.query(Task).filter(Task.id == task.id).first()
    assert fetched2.status == "done"


def test_postgres_task_deletion(db_session):
    """Verify task deletion removes row cleanly from PostgreSQL."""
    task = Task(title="Task to delete", status="backlog")
    db_session.add(task)
    db_session.commit()
    task_id = task.id

    db_session.delete(task)
    db_session.commit()

    deleted = db_session.query(Task).filter(Task.id == task_id).first()
    assert deleted is None


def test_postgres_persistence_in_separate_session(setup_postgres_schema):
    """Verify data is genuinely committed to PostgreSQL and queryable from a separate connection."""
    Session = sessionmaker(bind=setup_postgres_schema)

    # Session 1: create and commit
    s1 = Session()
    try:
        task = Task(title="Multi-Session Postgres Task", status="review", priority="urgent")
        s1.add(task)
        s1.commit()
        persisted_id = task.id
    finally:
        s1.close()

    # Session 2: retrieve from separate connection
    s2 = Session()
    try:
        found = s2.query(Task).filter(Task.id == persisted_id).first()
        assert found is not None
        assert found.title == "Multi-Session Postgres Task"
        assert found.status == "review"
        assert found.priority == "urgent"
    finally:
        try:
            to_delete = s2.query(Task).filter(Task.id == persisted_id).first()
            if to_delete:
                s2.delete(to_delete)
                s2.commit()
        finally:
            s2.close()
