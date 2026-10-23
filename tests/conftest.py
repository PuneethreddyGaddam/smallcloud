import os

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key")


@pytest.fixture(autouse=True)
def _isolate_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove DATABASE_URL from the environment before every test.

    The CI workflow sets DATABASE_URL globally so that app config can import,
    but that value leaks into tests that construct their own Settings objects
    (e.g. test_resolve_database_url_creates_dir, test_get_engine_uses_settings).
    Tests that need a specific DATABASE_URL set it themselves via
    monkeypatch.setenv, which still works because monkeypatch runs after this
    fixture removes the variable.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
