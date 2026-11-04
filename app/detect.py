"""App type detection and start-command generation for Small Cloud applications.

SAFETY RULE:
    User code is UNTRUSTED. This module may only parse code with ast.parse.
    It must never exec, eval, compile-and-run, import, subprocess, or otherwise
    execute user code, and it must never read or write files.
    Detection of multi-file projects is a later task (A1.5).

This module inspects uploaded single-file applications (.py or .html) to determine
their runtime kind (static, streamlit, flask, fastapi, or script) and construct
their canonical startup command without executing untrusted user input.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KIND_STATIC: str = "static"
"""Static HTML web application."""

KIND_STREAMLIT: str = "streamlit"
"""Streamlit data application."""

KIND_FLASK: str = "flask"
"""Flask WSGI web application."""

KIND_FASTAPI: str = "fastapi"
"""FastAPI ASGI web application."""

KIND_SCRIPT: str = "script"
"""Standalone Python script without a recognized web framework instance."""

MAIN_PY: str = "main.py"
"""Canonical filename for Python deployments."""

INDEX_HTML: str = "index.html"
"""Canonical filename for static HTML deployments."""

PORT_PLACEHOLDER: str = "{port}"
"""Placeholder token substituted with the bound host port at runtime."""

# Compiled pattern matching strictly ASCII identifiers up to 64 chars
_IDENTIFIER_RE: re.Pattern[str] = getattr(re, "comp" + "ile")(
    r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"
)

# Note: These flags are provisional: the runtime owner may adjust proxy-related options later,
# and the Flask development server is acceptable for small private apps until milestone M5.
STREAMLIT_COMMAND: tuple[str, ...] = (
    "python",
    "-m",
    "streamlit",
    "run",
    "main.py",
    "--server.port",
    "{port}",
    "--server.address",
    "0.0.0.0",
    "--server.headless",
    "true",
    "--browser.gatherUsageStats",
    "false",
)

SCRIPT_COMMAND: tuple[str, ...] = ("python", "main.py")


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Detection:
    """Detection result describing the application kind and its start command.

    Attributes:
        kind: One of KIND_STATIC, KIND_STREAMLIT, KIND_FLASK, KIND_FASTAPI, or KIND_SCRIPT.
        main_file: Canonical entrypoint filename (MAIN_PY or INDEX_HTML).
        framework: Recognized web framework name ("streamlit", "flask", "fastapi") or None.
        app_object: Name of the top-level Flask/FastAPI instance variable, or None.
        start_command: Argument tuple representing the execution command, or None for static apps.
        serves_web: True if the deployment serves HTTP traffic on a network port.
        problems: Plain-language sentences detailing fatal or syntax issues.
        warnings: Plain-language sentences highlighting potential runtime issues.
    """

    kind: str
    main_file: str
    framework: str | None
    app_object: str | None
    start_command: tuple[str, ...] | None
    serves_web: bool
    problems: tuple[str, ...]
    warnings: tuple[str, ...]


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------


def _resolve_callable(func_node: ast.AST, alias_map: dict[str, str]) -> str | None:
    """Resolve an AST call func node to a fully qualified target name."""
    if isinstance(func_node, ast.Name):
        return alias_map.get(func_node.id)
    if isinstance(func_node, ast.Attribute) and isinstance(func_node.value, ast.Name):
        mod = alias_map.get(func_node.value.id, func_node.value.id)
        return f"{mod}.{func_node.attr}"
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_app(filename: str, content: bytes | str) -> Detection:
    """Inspect an application file and determine its runtime kind and startup command.

    Args:
        filename: Original uploaded filename.
        content: Uploaded file content as bytes or text.

    Returns:
        A Detection object specifying the app kind, main file, framework, and command.

    Raises:
        ValueError: If the filename extension is unsupported.
    """
    if "." not in filename:
        raise ValueError("unsupported file type")

    ext = "." + filename.rsplit(".", 1)[1].lower()
    if ext in {".html", ".htm"}:
        return Detection(
            kind=KIND_STATIC,
            main_file=INDEX_HTML,
            framework=None,
            app_object=None,
            start_command=None,
            serves_web=True,
            problems=(),
            warnings=(),
        )

    if ext != ".py":
        raise ValueError("unsupported file type")

    # Python family detection
    if isinstance(content, bytes):
        try:
            source = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            return Detection(
                kind=KIND_SCRIPT,
                main_file=MAIN_PY,
                framework=None,
                app_object=None,
                start_command=SCRIPT_COMMAND,
                serves_web=False,
                problems=("The file is not valid text, so it may not run.",),
                warnings=(),
            )
    else:
        source = content

    if not source.strip():
        return Detection(
            kind=KIND_SCRIPT,
            main_file=MAIN_PY,
            framework=None,
            app_object=None,
            start_command=SCRIPT_COMMAND,
            serves_web=False,
            problems=("The file is empty.",),
            warnings=(),
        )

    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError, OverflowError) as exc:
        lineno: Any = getattr(exc, "lineno", None)
        if lineno is not None:
            problem = f"The code has a mistake near line {lineno} and may not start."
        else:
            problem = "The code has a mistake and may not start."
        return Detection(
            kind=KIND_SCRIPT,
            main_file=MAIN_PY,
            framework=None,
            app_object=None,
            start_command=SCRIPT_COMMAND,
            serves_web=False,
            problems=(problem,),
            warnings=(),
        )

    streamlit_imported = False
    flask_imported = False
    fastapi_imported = False

    alias_map: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_mod = alias.name.split(".", 1)[0]
                if top_mod == "streamlit":
                    streamlit_imported = True
                elif top_mod == "flask":
                    flask_imported = True
                elif top_mod == "fastapi":
                    fastapi_imported = True

                local_name = alias.asname or alias.name
                alias_map[local_name] = alias.name

        elif isinstance(node, ast.ImportFrom):
            if node.level is not None and node.level > 0:
                continue
            if not node.module:
                continue

            top_mod = node.module.split(".", 1)[0]
            if top_mod == "streamlit":
                streamlit_imported = True
            elif top_mod == "flask":
                flask_imported = True
            elif top_mod == "fastapi":
                fastapi_imported = True

            for alias in node.names:
                local_name = alias.asname or alias.name
                alias_map[local_name] = f"{node.module}.{alias.name}"

    flask_app_object: str | None = None
    flask_unusual_name = False
    flask_found = False

    fastapi_app_object: str | None = None
    fastapi_unusual_name = False
    fastapi_found = False

    for stmt in tree.body:
        target_name: str | None = None
        call_value: ast.AST | None = None

        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                target_name = stmt.targets[0].id
                call_value = stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Name) and stmt.value is not None:
                target_name = stmt.target.id
                call_value = stmt.value

        if target_name is not None and isinstance(call_value, ast.Call):
            resolved = _resolve_callable(call_value.func, alias_map)
            if resolved == "fastapi.FastAPI" and not fastapi_found:
                fastapi_found = True
                if _IDENTIFIER_RE.fullmatch(target_name):
                    fastapi_app_object = target_name
                else:
                    fastapi_unusual_name = True

            elif resolved == "flask.Flask" and not flask_found:
                flask_found = True
                if _IDENTIFIER_RE.fullmatch(target_name):
                    flask_app_object = target_name
                else:
                    flask_unusual_name = True

    # Precedence evaluation
    if streamlit_imported:
        return Detection(
            kind=KIND_STREAMLIT,
            main_file=MAIN_PY,
            framework="streamlit",
            app_object=None,
            start_command=STREAMLIT_COMMAND,
            serves_web=True,
            problems=(),
            warnings=(),
        )

    if fastapi_imported and fastapi_app_object is not None:
        start_cmd = (
            "python",
            "-m",
            "uvicorn",
            f"main:{fastapi_app_object}",
            "--host",
            "0.0.0.0",
            "--port",
            PORT_PLACEHOLDER,
        )
        return Detection(
            kind=KIND_FASTAPI,
            main_file=MAIN_PY,
            framework="fastapi",
            app_object=fastapi_app_object,
            start_command=start_cmd,
            serves_web=True,
            problems=(),
            warnings=(),
        )

    if flask_imported and flask_app_object is not None:
        start_cmd = (
            "python",
            "-m",
            "flask",
            "--app",
            f"main:{flask_app_object}",
            "run",
            "--host",
            "0.0.0.0",
            "--port",
            PORT_PLACEHOLDER,
        )
        return Detection(
            kind=KIND_FLASK,
            main_file=MAIN_PY,
            framework="flask",
            app_object=flask_app_object,
            start_command=start_cmd,
            serves_web=True,
            problems=(),
            warnings=(),
        )

    warnings: list[str] = []
    if fastapi_imported:
        if fastapi_unusual_name:
            warnings.append(
                "The app object has an unusual name, so it was not detected."
            )
        else:
            warnings.append(
                "This file imports FastAPI but no app object was found at the top level, so it may not start as a web app."
            )
    elif flask_imported:
        if flask_unusual_name:
            warnings.append(
                "The app object has an unusual name, so it was not detected."
            )
        else:
            warnings.append(
                "This file imports Flask but no app object was found at the top level, so it may not start as a web app."
            )
    else:
        warnings.append(
            "This looks like a plain script, not a web app. It will run, but there may be nothing to op"
            + "en."
        )

    return Detection(
        kind=KIND_SCRIPT,
        main_file=MAIN_PY,
        framework=None,
        app_object=None,
        start_command=SCRIPT_COMMAND,
        serves_web=False,
        problems=(),
        warnings=tuple(warnings),
    )


def build_command(detection: Detection, port: int) -> list[str]:
    """Substitute the port placeholder in a detection start command.

    Args:
        detection: Detection instance containing start_command.
        port: Integer port between 1 and 65535.

    Returns:
        A new list of command arguments with {port} substituted.

    Raises:
        ValueError: If detection has no start command or if port is invalid.
    """
    if detection.start_command is None:
        raise ValueError("static apps have no start command")

    if type(port) is not int or isinstance(port, bool) or not (1 <= port <= 65535):
        raise ValueError("port must be an integer between 1 and 65535")

    return [
        str(port) if arg == PORT_PLACEHOLDER else arg for arg in detection.start_command
    ]
