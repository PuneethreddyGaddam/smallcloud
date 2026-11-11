"""Runtime interface, result types, and failure contract for Small Cloud.

Importing this module has NO side effects: standard library only at import time,
no database access, no configuration reads, no filesystem or network calls.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.friendly_errors import FriendlyError

# ---------------------------------------------------------------------------
# Constants & State Types
# ---------------------------------------------------------------------------

INTERNAL_PORT: int = 8080
"""Fixed port inside every app container on which the app must listen."""

STATES: tuple[str, ...] = ("running", "paused", "stopped", "missing")
"""All valid runtime instance states."""

RuntimeState = Literal["running", "paused", "stopped", "missing"]
"""Type alias for runtime instance state strings ('missing' = no instance exists)."""


# ---------------------------------------------------------------------------
# Data Models & Exceptions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StartResult:
    """Result returned when an application runtime instance is started.

    Attributes:
        host_port:   Local host port mapped to container port 8080, or None for static apps.
        instance_id: Backend instance identifier (e.g. container ID), or None for static apps.
    """

    host_port: int | None
    instance_id: str | None


class RuntimeFailure(Exception):
    """Exception raised when starting, inspecting, or controlling an app fails.

    ``str(failure)`` always returns a fixed plain sentence and NEVER includes raw log text.

    Attributes:
        log_tail:                   Trailing log lines captured from the instance.
        exit_code:                  Process exit code if the instance exited, else None.
        oom_killed:                 True if the instance was killed due to out-of-memory.
        timed_out_waiting_for_port: True if readiness polling timed out before port 8080 opened.
    """

    _FIXED_MESSAGE: str = "The application runtime encountered a failure."

    def __init__(
        self,
        *,
        log_tail: str = "",
        exit_code: int | None = None,
        oom_killed: bool = False,
        timed_out_waiting_for_port: bool = False,
    ) -> None:
        super().__init__(self._FIXED_MESSAGE)
        self.log_tail: str = log_tail
        self.exit_code: int | None = exit_code
        self.oom_killed: bool = oom_killed
        self.timed_out_waiting_for_port: bool = timed_out_waiting_for_port

    def __str__(self) -> str:
        """Return a fixed sentence, never leaking raw log output."""
        return self._FIXED_MESSAGE

    def classify(
        self,
        kind: str | None = None,
        memory_limit_mib: int | None = None,
    ) -> FriendlyError:
        """Classify this failure into a plain-language FriendlyError.

        Args:
            kind:             Application kind ('static', 'streamlit', 'flask', 'fastapi', 'script').
            memory_limit_mib: Container memory limit in MiB, if known.

        Returns:
            A FriendlyError describing what happened and what to do next.
        """
        from app.friendly_errors import classify_failure

        return classify_failure(
            self.log_tail,
            exit_code=self.exit_code,
            oom_killed=self.oom_killed,
            timed_out_waiting_for_port=self.timed_out_waiting_for_port,
            kind=kind,
            memory_limit_mib=memory_limit_mib,
        )


# ---------------------------------------------------------------------------
# Runtime Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Runtime(Protocol):
    """Contract that all application runtime backends (Docker, fake, etc.) implement.

    General Rules:
        - All methods are **synchronous**. Async callers (such as FastAPI routes or
          the reverse proxy) must dispatch them using ``asyncio.to_thread``.
        - ``stop`` and ``delete`` are **idempotent**: calling them when an instance is
          already stopped or missing succeeds cleanly.
        - ``start`` on an already-running slug raises ``RuntimeFailure``.
        - ``pause`` requires state ``"running"`` (otherwise raises ``RuntimeFailure``).
        - ``unpause`` requires state ``"paused"`` (otherwise raises ``RuntimeFailure``).
        - ``status`` returns ``"missing"`` when no instance exists for the slug.
        - A Runtime NEVER reads or writes the database; the caller persists state and
          port changes via ``app.route_lookup.change_status``.
    """

    def start(
        self,
        slug: str,
        version_dir: Path,
        kind: str,
        env: Mapping[str, str] | None = None,
    ) -> StartResult:
        """Start an application instance for the given slug and deployment directory."""
        ...

    def pause(self, slug: str) -> None:
        """Pause a running instance."""
        ...

    def unpause(self, slug: str) -> None:
        """Resume a paused instance."""
        ...

    def stop(self, slug: str) -> None:
        """Stop a running or paused instance (idempotent)."""
        ...

    def delete(self, slug: str) -> None:
        """Remove an instance and release its resources (idempotent)."""
        ...

    def status(self, slug: str) -> RuntimeState:
        """Return the current state of the instance ('missing' if unknown)."""
        ...

    def logs(self, slug: str, tail: int = 200) -> str:
        """Return up to the last ``tail`` lines of logs for the instance."""
        ...

    def wait_until_ready(self, slug: str, timeout: float = 30.0) -> None:
        """Wait until the instance is listening or raise RuntimeFailure on failure/timeout."""
        ...
