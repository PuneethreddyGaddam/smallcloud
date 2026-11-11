"""Tests for app.runtime.base and app.runtime.fake (T1.7).

Covers lifecycle state transitions, port allocation, error queuing, failure
classification, input validation, thread safety, protocol compliance, and
import-time isolation.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.friendly_errors import (
    CODE_MISSING_PACKAGE,
    CODE_OUT_OF_MEMORY,
    CODE_PORT_NOT_LISTENING,
)
from app.runtime.base import (
    INTERNAL_PORT,
    STATES,
    Runtime,
    RuntimeFailure,
    StartResult,
)
from app.runtime.fake import FakeRuntime

# ---------------------------------------------------------------------------
# 1. Required Result: Full Lifecycle
# ---------------------------------------------------------------------------


def test_required_lifecycle(tmp_path: Path) -> None:
    """1. start -> running -> pause -> paused -> unpause -> running -> stop -> stopped -> delete -> missing."""
    fake = FakeRuntime()
    slug = "my-streamlit-app"

    assert fake.status(slug) == "missing"

    res = fake.start(slug, tmp_path, "streamlit", env={"MODE": "prod"})
    assert isinstance(res, StartResult)
    assert res.host_port == 18000
    assert res.instance_id == "fake-1"
    assert fake.status(slug) == "running"

    fake.pause(slug)
    assert fake.status(slug) == "paused"

    fake.unpause(slug)
    assert fake.status(slug) == "running"

    fake.stop(slug)
    assert fake.status(slug) == "stopped"

    fake.delete(slug)
    assert fake.status(slug) == "missing"

    assert fake.calls == [
        ("status", slug),
        ("start", slug),
        ("status", slug),
        ("pause", slug),
        ("status", slug),
        ("unpause", slug),
        ("status", slug),
        ("stop", slug),
        ("status", slug),
        ("delete", slug),
        ("status", slug),
    ]


# ---------------------------------------------------------------------------
# 2. Static vs Dynamic Kinds, State Guards, and Idempotence
# ---------------------------------------------------------------------------


def test_static_start_returns_none_port_and_id(tmp_path: Path) -> None:
    """2a. Static app starts cleanly with StartResult(None, None) and status 'running'."""
    fake = FakeRuntime()
    res = fake.start("static-site", tmp_path, "static")
    assert res == StartResult(None, None)
    assert fake.status("static-site") == "running"


def test_dynamic_kinds_allocate_distinct_ports(tmp_path: Path) -> None:
    """2b. Each non-static kind gets a distinct sequential port."""
    fake = FakeRuntime()
    ports: list[int | None] = []
    for kind in ("streamlit", "flask", "fastapi", "script"):
        res = fake.start(f"app-{kind}", tmp_path, kind)
        assert res.host_port is not None
        assert res.instance_id is not None
        ports.append(res.host_port)

    assert len(set(ports)) == 4
    assert ports == [18000, 18001, 18002, 18003]


def test_double_start_raises_runtime_failure(tmp_path: Path) -> None:
    """2c. Starting an already running slug raises RuntimeFailure."""
    fake = FakeRuntime()
    fake.start("running-app", tmp_path, "flask")
    with pytest.raises(RuntimeFailure):
        fake.start("running-app", tmp_path, "flask")


def test_stop_and_delete_are_idempotent(tmp_path: Path) -> None:
    """2d. stop and delete succeed repeatedly regardless of current state."""
    fake = FakeRuntime()

    # Unknown slug
    fake.stop("missing-app")
    fake.delete("missing-app")
    assert fake.status("missing-app") == "missing"

    # After start -> stop -> stop -> delete -> delete
    fake.start("web-app", tmp_path, "fastapi")
    fake.stop("web-app")
    fake.stop("web-app")
    assert fake.status("web-app") == "stopped"
    fake.delete("web-app")
    fake.delete("web-app")
    assert fake.status("web-app") == "missing"


def test_pause_and_unpause_guards(tmp_path: Path) -> None:
    """2e. pause requires running; unpause requires paused."""
    fake = FakeRuntime()

    # Missing app
    with pytest.raises(RuntimeFailure):
        fake.pause("nope")
    with pytest.raises(RuntimeFailure):
        fake.unpause("nope")

    # Running app cannot be unpaused
    fake.start("app-1", tmp_path, "flask")
    with pytest.raises(RuntimeFailure):
        fake.unpause("app-1")

    # Stopped app cannot be paused or unpaused
    fake.stop("app-1")
    with pytest.raises(RuntimeFailure):
        fake.pause("app-1")
    with pytest.raises(RuntimeFailure):
        fake.unpause("app-1")


# ---------------------------------------------------------------------------
# 3. Queued Failures Fire Exactly Once
# ---------------------------------------------------------------------------


def test_queued_start_and_wait_failures(tmp_path: Path) -> None:
    """3. fail_next_start and fail_next_wait fire once, then next call succeeds."""
    fake = FakeRuntime()
    slug = "flaky-app"

    # Queued start failure
    queued_start_err = RuntimeFailure(log_tail="boom during start", exit_code=1)
    fake.fail_next_start(queued_start_err)
    with pytest.raises(RuntimeFailure) as exc_info:
        fake.start(slug, tmp_path, "flask")
    assert exc_info.value is queued_start_err
    assert fake.status(slug) == "missing"

    # Next start succeeds
    res = fake.start(slug, tmp_path, "flask")
    assert res.host_port == 18000
    assert fake.status(slug) == "running"

    # Queued wait failure
    queued_wait_err = RuntimeFailure(timed_out_waiting_for_port=True)
    fake.fail_next_wait(queued_wait_err)
    with pytest.raises(RuntimeFailure) as wait_exc:
        fake.wait_until_ready(slug)
    assert wait_exc.value is queued_wait_err

    # Next wait succeeds
    fake.wait_until_ready(slug)


def test_wait_until_ready_non_running_raises(tmp_path: Path) -> None:
    """wait_until_ready raises RuntimeFailure when slug is not running."""
    fake = FakeRuntime()
    with pytest.raises(RuntimeFailure):
        fake.wait_until_ready("not-started")

    fake.start("stopped-app", tmp_path, "script")
    fake.stop("stopped-app")
    with pytest.raises(RuntimeFailure):
        fake.wait_until_ready("stopped-app")


# ---------------------------------------------------------------------------
# 4. RuntimeFailure Classification and Safety
# ---------------------------------------------------------------------------


def test_runtime_failure_classify_and_str_safety() -> None:
    """4. RuntimeFailure.classify delegates accurately and str() never leaks logs."""
    # a. OOM
    oom_failure = RuntimeFailure(oom_killed=True, log_tail="SECRET_TOKEN=xyz")
    oom_err = oom_failure.classify(memory_limit_mib=512)
    assert oom_err.code == CODE_OUT_OF_MEMORY
    assert "512 MB" in oom_err.message
    assert "SECRET_TOKEN" not in str(oom_failure)

    # b. Port timeout
    port_failure = RuntimeFailure(
        timed_out_waiting_for_port=True, log_tail="/secret/path/info"
    )
    port_err = port_failure.classify(kind="streamlit")
    assert port_err.code == CODE_PORT_NOT_LISTENING
    assert "/secret/path/info" not in str(port_failure)

    # c. Missing package
    pandas_log = (
        "Traceback (most recent call last):\n"
        '  File "/app/main.py", line 1, in <module>\n'
        "    import pandas\n"
        "ModuleNotFoundError: No module named 'pandas'"
    )
    pkg_failure = RuntimeFailure(log_tail=pandas_log, exit_code=1)
    pkg_err = pkg_failure.classify(kind="streamlit")
    assert pkg_err.code == CODE_MISSING_PACKAGE
    assert pkg_err.package == "pandas"
    assert "ModuleNotFoundError" not in str(pkg_failure)
    assert "pandas" not in str(pkg_failure)


# ---------------------------------------------------------------------------
# 5. Input Validation & Logs Helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_slug",
    ["", "A", "MyApp", "a/b", "a_b", "x" * 64, "hello world", None, 123],
)
def test_invalid_slugs_raise_value_error(bad_slug: object, tmp_path: Path) -> None:
    """5a. Invalid slugs raise ValueError in start."""
    fake = FakeRuntime()
    with pytest.raises(ValueError):
        fake.start(bad_slug, tmp_path, "flask")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad_kind",
    ["", "django", "STATIC", "python", None, 42],
)
def test_invalid_kinds_raise_value_error(bad_kind: object, tmp_path: Path) -> None:
    """5b. Unknown kinds raise ValueError in start."""
    fake = FakeRuntime()
    with pytest.raises(ValueError):
        fake.start("valid-slug", tmp_path, bad_kind)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad_tail",
    [0, -1, 10_001, True, False, "200", None, 1.5],
)
def test_invalid_tail_raises_value_error(bad_tail: object) -> None:
    """5c. Out-of-range or wrong-typed tail raises ValueError in logs."""
    fake = FakeRuntime()
    with pytest.raises(ValueError):
        fake.logs("valid-slug", tail=bad_tail)  # type: ignore[arg-type]


def test_logs_and_reset_helpers(tmp_path: Path) -> None:
    """Verify set_logs tail truncation and reset clearing state."""
    fake = FakeRuntime()
    assert fake.logs("app-1") == ""

    fake.set_logs("app-1", "line1\nline2\nline3\nline4")
    assert fake.logs("app-1", tail=2) == "line3\nline4"
    assert fake.logs("app-1", tail=10) == "line1\nline2\nline3\nline4"

    fake.start("app-1", tmp_path, "flask")
    fake.reset()
    assert fake.status("app-1") == "missing"
    assert fake.logs("app-1") == ""
    assert fake.calls == [("status", "app-1"), ("logs", "app-1")]

    # Port restarts cleanly from 18000 after reset
    res = fake.start("app-2", tmp_path, "flask")
    assert res.host_port == 18000
    assert res.instance_id == "fake-1"


# ---------------------------------------------------------------------------
# 6. Thread Safety
# ---------------------------------------------------------------------------


def test_thread_safety_concurrent_starts(tmp_path: Path) -> None:
    """6. 8 threads each start 25 distinct slugs (200 total): unique ports, no errors."""
    fake = FakeRuntime()
    num_threads = 8
    starts_per_thread = 25

    def worker(thread_id: int) -> list[int]:
        allocated: list[int] = []
        for idx in range(starts_per_thread):
            slug = f"thread-{thread_id}-app-{idx}"
            res = fake.start(slug, tmp_path, "fastapi")
            assert res.host_port is not None
            allocated.append(res.host_port)
        return allocated

    with ThreadPoolExecutor(max_workers=num_threads) as pool:
        futures = [pool.submit(worker, tid) for tid in range(num_threads)]
        all_ports: list[int] = []
        for fut in futures:
            all_ports.extend(fut.result())

    total = num_threads * starts_per_thread
    assert len(all_ports) == total
    assert len(set(all_ports)) == total, "Duplicate host ports were allocated"


# ---------------------------------------------------------------------------
# 7. Protocol & Constants Compliance
# ---------------------------------------------------------------------------


def test_fake_runtime_implements_protocol() -> None:
    """7. FakeRuntime satisfies isinstance(fake, Runtime) and base constants."""
    fake = FakeRuntime()
    assert isinstance(fake, Runtime)
    assert INTERNAL_PORT == 8080
    assert STATES == ("running", "paused", "stopped", "missing")


# ---------------------------------------------------------------------------
# 8. Import Safety
# ---------------------------------------------------------------------------


def test_import_safety() -> None:
    """8. Subprocess import without SECRET_KEY avoids config/db/models."""
    env = os.environ.copy()
    env.pop("SECRET_KEY", None)

    code = (
        "import sys\n"
        "import app.runtime.base\n"
        "import app.runtime.fake\n"
        "assert 'app.config' not in sys.modules, 'app.config was imported'\n"
        "assert 'app.db' not in sys.modules, 'app.db was imported'\n"
        "assert 'app.models' not in sys.modules, 'app.models was imported'\n"
        "print('ok')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        f"Subprocess failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stdout.strip() == "ok"
