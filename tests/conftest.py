import os

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key")


@pytest.fixture(autouse=True)
def _isolate_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove DATABASE_URL before every test so tests do not depend on whatever the
    environment provides. Tests that need a specific value set it themselves with
    monkeypatch.setenv."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
