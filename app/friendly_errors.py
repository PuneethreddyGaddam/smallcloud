"""Plain-language error classification for Small Cloud applications.

SAFETY RULES:
    Log text and exception messages originate from USER code and are UNTRUSTED.
    They may contain server paths, secrets, control characters, and hostile text.
    Therefore this module NEVER copies raw log text or str(exc) into any output field.
    Every output string is built from fixed literal sentences, plus a small set of
    strictly validated values: a package name, a pip install name, an integer line
    number, and an integer memory limit.  Nothing else from the log ever appears in
    a FriendlyError field.

Error codes produced by this module:
    missing_package      – a required Python package was not installed.
    syntax_error         – a SyntaxError / IndentationError / TabError in user code.
    port_not_listening   – the app never bound its HTTP port in time.
    out_of_memory        – the app exceeded its memory allowance.
    unknown_error        – none of the above patterns were recognised.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CODE_MISSING_PACKAGE: str = "missing_package"
"""Error code: a required Python package was not installed."""

CODE_SYNTAX_ERROR: str = "syntax_error"
"""Error code: a SyntaxError / IndentationError / TabError in user code."""

CODE_PORT_NOT_LISTENING: str = "port_not_listening"
"""Error code: the app never bound its HTTP port within the allowed time."""

CODE_OUT_OF_MEMORY: str = "out_of_memory"
"""Error code: the app exceeded its memory allowance."""

CODE_UNKNOWN: str = "unknown_error"
"""Error code: failure cause could not be identified."""

ALL_CODES: frozenset[str] = frozenset(
    {
        CODE_MISSING_PACKAGE,
        CODE_SYNTAX_ERROR,
        CODE_PORT_NOT_LISTENING,
        CODE_OUT_OF_MEMORY,
        CODE_UNKNOWN,
    }
)
"""Frozenset of every error code this module can produce."""

MAX_LOG_CHARS: int = 50_000
"""Only the last MAX_LOG_CHARS characters of a log are examined."""

# Top-level module name: ASCII identifiers only, 1-64 chars.
_MODULE_NAME_RE: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

# Pip package install name: letters, digits, dots, hyphens, underscores, 1-64 chars.
_PIP_NAME_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# ANSI escape sequence – bounded repetition prevents catastrophic backtracking.
_ANSI_RE: re.Pattern[str] = re.compile(r"\x1b\[[0-9;]{0,32}[A-Za-z]")

# Pattern matching ModuleNotFoundError or old ImportError in a log line.
_MODULE_NOT_FOUND_RE: re.Pattern[str] = re.compile(
    r"""(?:ModuleNotFoundError|ImportError):\s+No module named\s+['"]([^'"]{1,200})['"]"""
)

# Pattern matching the Python traceback File/line annotation for main.py.
# Matches: File "...main.py", line N    (the quoted path must end with main.py)
_FILE_LINE_RE: re.Pattern[str] = re.compile(
    r"""File\s+"[^"]*main\.py",\s+line\s+(\d{1,7})"""
)

# Pattern matching SyntaxError / IndentationError / TabError at start of line.
_SYNTAX_ERR_RE: re.Pattern[str] = re.compile(
    r"^\s*(SyntaxError|IndentationError|TabError)\b"
)

# Mapping from import names to pip install names.
PIP_NAMES: dict[str, str] = {
    "cv2": "opencv-python",
    "PIL": "pillow",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "skimage": "scikit-image",
    "dateutil": "python-dateutil",
    "serial": "pyserial",
    "jwt": "pyjwt",
    "Crypto": "pycryptodome",
    "attr": "attrs",
    "git": "gitpython",
    "OpenSSL": "pyopenssl",
    "MySQLdb": "mysqlclient",
    "psycopg2": "psycopg2-binary",
}
"""Mapping from Python import names to their pip install names."""


# ---------------------------------------------------------------------------
# Data Model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FriendlyError:
    """Immutable plain-language description of an application failure.

    Every user-facing string (title, message, suggestion) obeys these rules:
        - At most 300 characters, one line, no control characters.
        - Short sentences a non-technical person can understand.
        - Calm tone; never blames the user.
        - No forbidden technical words, file paths, URLs, or container ids.
        - The only file name that may appear is 'main.py'.

    Attributes:
        code:        One of the ALL_CODES values.
        title:       A 3–8 word heading.
        message:     What went wrong, in plain language.
        suggestion:  What the user should do next.
        line:        Source line number involved, or None.
        package:     Python import name of the missing package, or None.
    """

    code: str
    title: str
    message: str
    suggestion: str
    line: int | None = None
    package: str | None = None

    @property
    def text(self) -> str:
        """Combined message and suggestion as a single string."""
        return f"{self.message} {self.suggestion}"

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe dict with exactly the six canonical keys."""
        return {
            "code": self.code,
            "title": self.title,
            "message": self.message,
            "suggestion": self.suggestion,
            "line": self.line,
            "package": self.package,
        }


# ---------------------------------------------------------------------------
# Private sentence builders (shared between classify_failure and explain_exception
# so wording can never drift between the two code paths)
# ---------------------------------------------------------------------------


def _make_oom(memory_limit_mib: int | None = None) -> FriendlyError:
    """Build a FriendlyError for the out_of_memory code."""
    if (
        isinstance(memory_limit_mib, int)
        and not isinstance(memory_limit_mib, bool)
        and 1 <= memory_limit_mib <= 100_000
    ):
        msg = f"Your app used more than {memory_limit_mib} MB of memory, so it was stopped."
    else:
        msg = "Your app used more memory than it is allowed, so it was stopped."
    return FriendlyError(
        code=CODE_OUT_OF_MEMORY,
        title="Your app ran out of memory",
        message=msg,
        suggestion=(
            "Try loading less data at once, or reduce what the app keeps in memory,"
            " then deploy again."
        ),
    )


def _make_syntax(line: int | None) -> FriendlyError:
    """Build a FriendlyError for the syntax_error code."""
    if line is not None:
        msg = f"Your code has a mistake near line {line} and could not start."
    else:
        msg = "Your code has a mistake and could not start."
    return FriendlyError(
        code=CODE_SYNTAX_ERROR,
        title="Your code has a typing mistake",
        message=msg,
        suggestion=(
            "Check that line and the lines just above it for a missing bracket, quote or colon,"
            " then deploy again."
        ),
        line=line,
    )


def _make_missing_package(name: str) -> FriendlyError:
    """Build a FriendlyError for the missing_package code.

    Args:
        name: Validated top-level import name (already matched _MODULE_NAME_RE).
    """
    raw_install = PIP_NAMES.get(name, name)
    install = raw_install if _PIP_NAME_RE.fullmatch(raw_install) else name
    return FriendlyError(
        code=CODE_MISSING_PACKAGE,
        title="A package is missing",
        message=f"Your app needs a package called {name}, but it is not installed.",
        suggestion=(
            f"If {name} is a library, add {install} to a file named requirements.txt"
            " and deploy again."
            f" If it is one of your own files, put its code into main.py for now."
        ),
        package=name,
    )


def _make_port_not_listening(kind: str | None) -> FriendlyError:
    """Build a FriendlyError for the port_not_listening code."""
    if kind == "script":
        return FriendlyError(
            code=CODE_PORT_NOT_LISTENING,
            title="Nothing to open yet",
            message="Your file ran, but it does not open a web page, so there is nothing to show.",
            suggestion=(
                "If you wanted a web app, build it with Streamlit, Flask or FastAPI,"
                " or upload a single HTML file."
            ),
        )
    return FriendlyError(
        code=CODE_PORT_NOT_LISTENING,
        title="Your app did not start in time",
        message="Your app started but was not ready after waiting a while.",
        suggestion=(
            "Open the logs to see what it was doing."
            " A long set-up step or a big download at the start is the most common cause."
            " Make the start-up work smaller and deploy again."
        ),
    )


def _make_unknown() -> FriendlyError:
    """Build a FriendlyError for the unknown_error code."""
    return FriendlyError(
        code=CODE_UNKNOWN,
        title="Your app stopped",
        message="Your app stopped because of a problem we could not identify.",
        suggestion=(
            "Open the logs for details, or share the last lines with your developer to investigate."
        ),
    )


# ---------------------------------------------------------------------------
# Log preparation helpers
# ---------------------------------------------------------------------------


def _prepare_log(log_text: str | None) -> list[str]:
    """Clean and split a raw log string into lines.

    Steps:
        1. If not a str, treat as "".
        2. Take only the last MAX_LOG_CHARS characters.
        3. Remove ANSI escape sequences (bounded pattern, safe on hostile input).
        4. Replace NUL and other control characters (except \\n and \\t) with space.
        5. Split on newlines.
    """
    if not isinstance(log_text, str):
        log_text = ""
    text = log_text[-MAX_LOG_CHARS:]
    text = _ANSI_RE.sub("", text)
    cleaned = []
    for ch in text:
        code = ord(ch)
        if code < 32 and ch not in ("\n", "\t"):
            cleaned.append(" ")
        elif code == 127:
            cleaned.append(" ")
        else:
            cleaned.append(ch)
    return "".join(cleaned).splitlines()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_failure(
    log_text: str | None = None,
    *,
    exit_code: int | None = None,  # noqa: ARG001 – reserved for future use
    oom_killed: bool = False,
    timed_out_waiting_for_port: bool = False,
    kind: str | None = None,
    memory_limit_mib: int | None = None,
) -> FriendlyError:
    """Classify an application failure into a plain-language FriendlyError.

    Decision order (first match wins):
        a. OUT OF MEMORY  – oom_killed is exactly True, or the last non-empty log
           line starts with "MemoryError".
           NOTE: exit_code 137 alone does NOT trigger this check. Exit code 137 also
           occurs when the platform stops a slow app (SIGKILL), so it is ambiguous.
        b. SYNTAX ERROR   – a SyntaxError/IndentationError/TabError line found.
        c. MISSING PACKAGE – a ModuleNotFoundError/ImportError line found.
        d. PORT NOT LISTENING – timed_out_waiting_for_port is exactly True.
        e. UNKNOWN        – none of the above matched.

    SAFETY: Never raises for any input. All keyword arguments of the wrong type
    are silently ignored.

    Args:
        log_text:                   Raw log output from the application container.
        exit_code:                  Process exit code (reserved; not used in decisions).
        oom_killed:                 True when the kernel reported an OOM kill.
        timed_out_waiting_for_port: True when the health-check timed out.
        kind:                       App kind string ('streamlit', 'flask', 'fastapi',
                                    'script', 'static', or None).
        memory_limit_mib:           Container memory limit in MiB (1..100000).

    Returns:
        A FriendlyError with the most specific matching code.
    """
    try:
        lines = _prepare_log(log_text)

        # --- a. OUT OF MEMORY ---
        # Exact type check: oom_killed must be exactly True (not a truthy value).
        if oom_killed is True:
            return _make_oom(memory_limit_mib)

        # Also trigger on "MemoryError" at the start of the last non-empty line.
        for line in reversed(lines):
            stripped = line.strip()
            if stripped:
                if stripped.startswith("MemoryError"):
                    return _make_oom(memory_limit_mib)
                break

        # --- b. SYNTAX ERROR ---
        # Search from the END for the error class name.
        syntax_match_idx: int | None = None
        for i in range(len(lines) - 1, -1, -1):
            if _SYNTAX_ERR_RE.match(lines[i]):
                syntax_match_idx = i
                break

        if syntax_match_idx is not None:
            # Look at up to 8 lines BEFORE the syntax error line for a File/line ref.
            line_number: int | None = None
            start = max(0, syntax_match_idx - 8)
            for j in range(syntax_match_idx - 1, start - 1, -1):
                m = _FILE_LINE_RE.search(lines[j])
                if m:
                    candidate = int(m.group(1))
                    if 1 <= candidate <= 1_000_000:
                        line_number = candidate
                    break  # use the closest match (nearest to the error line)
            return _make_syntax(line_number)

        # --- c. MISSING PACKAGE ---
        for i in range(len(lines) - 1, -1, -1):
            m = _MODULE_NOT_FOUND_RE.search(lines[i])
            if m:
                raw_name = m.group(1)
                # Take only the top-level component (before the first ".")
                top_name = raw_name.split(".")[0]
                if _MODULE_NAME_RE.fullmatch(top_name):
                    return _make_missing_package(top_name)
                # Hostile name – fall through

        # --- d. PORT NOT LISTENING ---
        if timed_out_waiting_for_port is True:
            # Normalise kind: only accept str values.
            safe_kind = kind if isinstance(kind, str) else None
            return _make_port_not_listening(safe_kind)

        # --- e. UNKNOWN ---
        return _make_unknown()

    except Exception:  # pragma: no cover – defensive belt-and-suspenders
        return _make_unknown()


def explain_exception(exc: BaseException) -> FriendlyError:
    """Translate a platform-side exception into a plain-language FriendlyError.

    SAFETY: Never includes str(exc) or repr(exc) in any output field.

    Decision rules:
        ModuleNotFoundError  → missing_package using exc.name (if valid top-level name).
        SyntaxError subclass → syntax_error with line=exc.lineno (if valid int).
        MemoryError          → out_of_memory.
        TimeoutError / asyncio.TimeoutError → port_not_listening (generic wording).
        Anything else        → unknown_error.

    Args:
        exc: Any BaseException instance.

    Returns:
        A FriendlyError with the most specific matching code.
    """
    try:
        # ModuleNotFoundError (subclass of ImportError)
        if isinstance(exc, ModuleNotFoundError):
            raw_name = getattr(exc, "name", None)
            if isinstance(raw_name, str):
                top_name = raw_name.split(".")[0]
                if _MODULE_NAME_RE.fullmatch(top_name):
                    return _make_missing_package(top_name)
            return _make_unknown()

        # SyntaxError and its subclasses (IndentationError, TabError)
        if isinstance(exc, SyntaxError):
            lineno = getattr(exc, "lineno", None)
            line_number = None
            if isinstance(lineno, int) and not isinstance(lineno, bool):
                if 1 <= lineno <= 1_000_000:
                    line_number = lineno
            return _make_syntax(line_number)

        # MemoryError
        if isinstance(exc, MemoryError):
            return _make_oom()

        # TimeoutError (also covers asyncio.TimeoutError which inherits TimeoutError
        # in Python 3.11+ and also from asyncio.TimeoutError separately)
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            return _make_port_not_listening(None)

        return _make_unknown()

    except Exception:  # pragma: no cover – defensive
        return _make_unknown()
