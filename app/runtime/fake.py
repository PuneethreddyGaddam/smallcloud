"""In-memory, thread-safe fake runtime for Small Cloud tests.

Implements :class:`~app.runtime.base.Runtime` without Docker, subprocesses,
disk writes, or database access.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.runtime.base import (
    RuntimeFailure,
    RuntimeState,
    StartResult,
)

# ---------------------------------------------------------------------------
# Validation Constants
# ---------------------------------------------------------------------------

_SLUG_RE: re.Pattern[str] = re.compile(r"^[a-z0-9-]{1,63}$")

_ALLOWED_KINDS: frozenset[str] = frozenset(
    {"static", "streamlit", "flask", "fastapi", "script"}
)

_FIRST_PORT: int = 18000
_MIN_TAIL: int = 1
_MAX_TAIL: int = 10_000


# ---------------------------------------------------------------------------
# Internal State Record
# ---------------------------------------------------------------------------


@dataclass
class _FakeInstance:
    """Internal mutable state for a single simulated runtime instance."""

    state: RuntimeState
    kind: str
    host_port: int | None
    instance_id: str | None
    version_dir: Path
    env: dict[str, str]


# ---------------------------------------------------------------------------
# FakeRuntime Implementation
# ---------------------------------------------------------------------------


class FakeRuntime:
    """In-memory, thread-safe test double implementing the Runtime protocol."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instances: dict[str, _FakeInstance] = {}
        self._logs: dict[str, str] = {}
        self._calls: list[tuple[str, str]] = []
        self._next_port: int = _FIRST_PORT
        self._next_instance_num: int = 1
        self._next_start_failure: RuntimeFailure | None = None
        self._next_wait_failure: RuntimeFailure | None = None

    # ------------------------------------------------------------------
    # Runtime Protocol Methods
    # ------------------------------------------------------------------

    def start(
        self,
        slug: str,
        version_dir: Path,
        kind: str,
        env: Mapping[str, str] | None = None,
    ) -> StartResult:
        """Start an in-memory simulated instance for ``slug``."""
        if type(slug) is not str or _SLUG_RE.fullmatch(slug) is None:
            raise ValueError("Invalid app slug.")
        if type(kind) is not str or kind not in _ALLOWED_KINDS:
            raise ValueError("Invalid app kind.")

        env_copy = dict(env) if env is not None else {}

        with self._lock:
            self._calls.append(("start", slug))

            if self._next_start_failure is not None:
                failure = self._next_start_failure
                self._next_start_failure = None
                raise failure

            existing = self._instances.get(slug)
            if existing is not None and existing.state in {"running", "paused"}:
                raise RuntimeFailure(log_tail=self._logs.get(slug, ""))

            if kind == "static":
                host_port: int | None = None
                instance_id: str | None = None
            else:
                # Ensure port is not in use by any existing instance
                active_ports = {
                    inst.host_port
                    for inst in self._instances.values()
                    if inst.host_port is not None
                }
                while self._next_port in active_ports:
                    self._next_port += 1
                host_port = self._next_port
                self._next_port += 1

                instance_id = f"fake-{self._next_instance_num}"
                self._next_instance_num += 1

            self._instances[slug] = _FakeInstance(
                state="running",
                kind=kind,
                host_port=host_port,
                instance_id=instance_id,
                version_dir=version_dir,
                env=env_copy,
            )
            return StartResult(host_port=host_port, instance_id=instance_id)

    def pause(self, slug: str) -> None:
        """Pause a currently running instance."""
        with self._lock:
            self._calls.append(("pause", slug))
            inst = self._instances.get(slug)
            if inst is None or inst.state != "running":
                raise RuntimeFailure(log_tail=self._logs.get(slug, ""))
            inst.state = "paused"

    def unpause(self, slug: str) -> None:
        """Resume a currently paused instance."""
        with self._lock:
            self._calls.append(("unpause", slug))
            inst = self._instances.get(slug)
            if inst is None or inst.state != "paused":
                raise RuntimeFailure(log_tail=self._logs.get(slug, ""))
            inst.state = "running"

    def stop(self, slug: str) -> None:
        """Stop an instance if running or paused (idempotent)."""
        with self._lock:
            self._calls.append(("stop", slug))
            inst = self._instances.get(slug)
            if inst is not None:
                inst.state = "stopped"
                inst.host_port = None

    def delete(self, slug: str) -> None:
        """Remove an instance completely (idempotent)."""
        with self._lock:
            self._calls.append(("delete", slug))
            self._instances.pop(slug, None)

    def status(self, slug: str) -> RuntimeState:
        """Return the current RuntimeState of ``slug`` ('missing' if unknown)."""
        with self._lock:
            self._calls.append(("status", slug))
            inst = self._instances.get(slug)
            if inst is None:
                return "missing"
            return inst.state

    def logs(self, slug: str, tail: int = 200) -> str:
        """Return the last ``tail`` lines configured for ``slug``."""
        if type(tail) is not int or not (_MIN_TAIL <= tail <= _MAX_TAIL):
            raise ValueError("tail must be an integer from 1 to 10000.")

        with self._lock:
            self._calls.append(("logs", slug))
            text = self._logs.get(slug, "")
            if not text:
                return ""
            lines = text.splitlines()
            return "\n".join(lines[-tail:])

    def wait_until_ready(self, slug: str, timeout: float = 30.0) -> None:  # noqa: ARG002
        """Verify instance is running and raise queued failure if set."""
        with self._lock:
            self._calls.append(("wait_until_ready", slug))
            inst = self._instances.get(slug)
            if inst is None or inst.state != "running":
                raise RuntimeFailure(log_tail=self._logs.get(slug, ""))
            if self._next_wait_failure is not None:
                failure = self._next_wait_failure
                self._next_wait_failure = None
                raise failure

    # ------------------------------------------------------------------
    # Test Helpers
    # ------------------------------------------------------------------

    def fail_next_start(self, failure: RuntimeFailure) -> None:
        """Queue a RuntimeFailure to be raised on the next ``start`` call."""
        with self._lock:
            self._next_start_failure = failure

    def fail_next_wait(self, failure: RuntimeFailure) -> None:
        """Queue a RuntimeFailure to be raised on the next ``wait_until_ready`` call."""
        with self._lock:
            self._next_wait_failure = failure

    def set_logs(self, slug: str, text: str) -> None:
        """Set the simulated log text for ``slug``."""
        with self._lock:
            self._logs[slug] = text

    @property
    def calls(self) -> list[tuple[str, str]]:
        """Snapshot list of recorded ``(method, slug)`` calls in order."""
        with self._lock:
            return list(self._calls)

    def reset(self) -> None:
        """Clear all instances, logs, queued failures, calls, and counters."""
        with self._lock:
            self._instances.clear()
            self._logs.clear()
            self._calls.clear()
            self._next_port = _FIRST_PORT
            self._next_instance_num = 1
            self._next_start_failure = None
            self._next_wait_failure = None
