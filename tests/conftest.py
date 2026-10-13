# Adapted from P03 (p03-ai-operations-agent/tests/conftest.py).
import psycopg
import pytest

from app import tracing
from app.db.migrate import apply_migrations
from app.settings import get_settings


@pytest.fixture(autouse=True)
def _test_settings(monkeypatch):
    # Tests never depend on the developer's .env secrets, and never send traces to the real Langfuse project.
    monkeypatch.setenv("PHONE_HASH_KEY", "test-key")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    tracing.get_client.cache_clear()
    yield
    get_settings.cache_clear()
    tracing.get_client.cache_clear()


@pytest.fixture
def conn():
    """A connection to the real dev Postgres as the app role. Whatever a test writes is rolled back."""
    settings = get_settings()
    try:
        c = psycopg.connect(settings.database_url, connect_timeout=10)
    except psycopg.OperationalError:
        pytest.skip("Postgres is not running (make up)")
    apply_migrations()
    # Start a transaction now: on a fresh connection the first `conn.transaction()` block would be the OUTER
    # transaction and would really commit. Inside an open one it is a savepoint, so every test rolls back.
    c.execute("SELECT 1")
    yield c
    c.rollback()
    c.close()
