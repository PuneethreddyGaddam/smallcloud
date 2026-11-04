"""Tests for app.detect (T1.6: App type detection).

Verifies AST-based app kind detection, start command building, precedence rules,
safe error handling, and zero side-effects.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from app.detect import (
    _IDENTIFIER_RE,
    INDEX_HTML,
    KIND_FASTAPI,
    KIND_FLASK,
    KIND_SCRIPT,
    KIND_STATIC,
    KIND_STREAMLIT,
    MAIN_PY,
    PORT_PLACEHOLDER,
    build_command,
    detect_app,
)

# ---------------------------------------------------------------------------
# 1. The Required Result: One Sample per Type
# ---------------------------------------------------------------------------


def test_required_result_one_sample_per_type() -> None:
    """1. Assert kind, main_file, framework, app_object, start_command, and serves_web
    for HTML, Streamlit, Flask, FastAPI, and Script.
    """
    # 1. HTML (static)
    d_html = detect_app("index.html", "<h1>Hello</h1>")
    assert d_html.kind == KIND_STATIC
    assert d_html.main_file == INDEX_HTML
    assert d_html.framework is None
    assert d_html.app_object is None
    assert d_html.start_command is None
    assert d_html.serves_web is True
    assert d_html.problems == ()
    assert d_html.warnings == ()

    # 2. Streamlit
    d_st = detect_app("main.py", "import streamlit as st\nst.write('hi')")
    assert d_st.kind == KIND_STREAMLIT
    assert d_st.main_file == MAIN_PY
    assert d_st.framework == "streamlit"
    assert d_st.app_object is None
    assert d_st.start_command == (
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
    assert d_st.serves_web is True
    assert d_st.problems == ()
    assert d_st.warnings == ()

    # 3. Flask
    d_flask = detect_app("main.py", "from flask import Flask\napp = Flask(__name__)")
    assert d_flask.kind == KIND_FLASK
    assert d_flask.main_file == MAIN_PY
    assert d_flask.framework == "flask"
    assert d_flask.app_object == "app"
    assert d_flask.start_command == (
        "python",
        "-m",
        "flask",
        "--app",
        "main:app",
        "run",
        "--host",
        "0.0.0.0",
        "--port",
        "{port}",
    )
    assert d_flask.serves_web is True
    assert d_flask.problems == ()
    assert d_flask.warnings == ()

    # 4. FastAPI
    d_fastapi = detect_app("main.py", "from fastapi import FastAPI\napp = FastAPI()")
    assert d_fastapi.kind == KIND_FASTAPI
    assert d_fastapi.main_file == MAIN_PY
    assert d_fastapi.framework == "fastapi"
    assert d_fastapi.app_object == "app"
    assert d_fastapi.start_command == (
        "python",
        "-m",
        "uvicorn",
        "main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "{port}",
    )
    assert d_fastapi.serves_web is True
    assert d_fastapi.problems == ()
    assert d_fastapi.warnings == ()

    # 5. Plain Script
    d_script = detect_app("main.py", "print('hi')")
    assert d_script.kind == KIND_SCRIPT
    assert d_script.main_file == MAIN_PY
    assert d_script.framework is None
    assert d_script.app_object is None
    assert d_script.start_command == ("python", "main.py")
    assert d_script.serves_web is False
    assert d_script.problems == ()
    assert d_script.warnings == (
        "This looks like a plain script, not a web app. It will run, but there may be nothing to open.",
    )


# ---------------------------------------------------------------------------
# 2. Streamlit Variants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        "import streamlit as st\nst.write(1)",
        "from streamlit import write\nwrite('hello')",
        "def main():\n    import streamlit\nmain()",
        "import streamlit.web.cli\nprint('ready')",
    ],
)
def test_streamlit_variants(src: str) -> None:
    """2. Streamlit variants detect correctly."""
    d = detect_app("main.py", src)
    assert d.kind == KIND_STREAMLIT
    assert d.framework == "streamlit"
    assert d.serves_web is True


# ---------------------------------------------------------------------------
# 3. Not Fooled by Strings, Comments, or Unrelated Imports
# ---------------------------------------------------------------------------


def test_not_fooled_by_strings_comments_or_similar_modules() -> None:
    """3. Strings, comments, docstrings, similar module names, and relative imports do not trigger frameworks."""
    # Mentions in comments/docstring/string
    src_comment = (
        '"""This module mentions streamlit, flask and fastapi in docstring."""\n'
        "# import streamlit\n"
        'msg = "from flask import Flask"\n'
        'endpoint = "http://localhost:8000/fastapi"\n'
        "print(msg)\n"
    )
    d1 = detect_app("main.py", src_comment)
    assert d1.kind == KIND_SCRIPT
    assert d1.framework is None
    assert d1.serves_web is False

    # streamlit_extras
    d2 = detect_app("main.py", "import streamlit_extras\nprint('extra')")
    assert d2.kind == KIND_SCRIPT
    assert d2.framework is None

    # flask_cors
    d3 = detect_app("main.py", "import flask_cors\nprint('cors')")
    assert d3.kind == KIND_SCRIPT
    assert d3.framework is None

    # relative import
    d4 = detect_app("main.py", "from . import streamlit\nprint('rel')")
    assert d4.kind == KIND_SCRIPT
    assert d4.framework is None


# ---------------------------------------------------------------------------
# 4. Five Instance Forms for Flask and FastAPI
# ---------------------------------------------------------------------------


def test_flask_instance_forms() -> None:
    """4a. Five instance forms for Flask."""
    # 1. Plain
    d1 = detect_app("main.py", "from flask import Flask\napp = Flask(__name__)")
    assert d1.kind == KIND_FLASK
    assert d1.app_object == "app"
    assert d1.start_command == (
        "python",
        "-m",
        "flask",
        "--app",
        "main:app",
        "run",
        "--host",
        "0.0.0.0",
        "--port",
        "{port}",
    )

    # 2. Custom name
    d2 = detect_app("main.py", "from flask import Flask\nserver = Flask(__name__)")
    assert d2.kind == KIND_FLASK
    assert d2.app_object == "server"
    assert d2.start_command == (
        "python",
        "-m",
        "flask",
        "--app",
        "main:server",
        "run",
        "--host",
        "0.0.0.0",
        "--port",
        "{port}",
    )

    # 3. Module alias
    d3 = detect_app("main.py", "import flask as f\nx = f.Flask(__name__)")
    assert d3.kind == KIND_FLASK
    assert d3.app_object == "x"
    assert d3.start_command == (
        "python",
        "-m",
        "flask",
        "--app",
        "main:x",
        "run",
        "--host",
        "0.0.0.0",
        "--port",
        "{port}",
    )

    # 4. Class alias
    d4 = detect_app("main.py", "from flask import Flask as F\nweb = F(__name__)")
    assert d4.kind == KIND_FLASK
    assert d4.app_object == "web"
    assert d4.start_command == (
        "python",
        "-m",
        "flask",
        "--app",
        "main:web",
        "run",
        "--host",
        "0.0.0.0",
        "--port",
        "{port}",
    )

    # 5. Annotated
    d5 = detect_app("main.py", "from flask import Flask\napp: Flask = Flask(__name__)")
    assert d5.kind == KIND_FLASK
    assert d5.app_object == "app"
    assert d5.start_command == (
        "python",
        "-m",
        "flask",
        "--app",
        "main:app",
        "run",
        "--host",
        "0.0.0.0",
        "--port",
        "{port}",
    )


def test_fastapi_instance_forms() -> None:
    """4b. Five instance forms for FastAPI."""
    # 1. Plain
    d1 = detect_app("main.py", "from fastapi import FastAPI\napp = FastAPI()")
    assert d1.kind == KIND_FASTAPI
    assert d1.app_object == "app"
    assert d1.start_command == (
        "python",
        "-m",
        "uvicorn",
        "main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "{port}",
    )

    # 2. Custom name
    d2 = detect_app("main.py", "from fastapi import FastAPI\napi = FastAPI()")
    assert d2.kind == KIND_FASTAPI
    assert d2.app_object == "api"
    assert d2.start_command == (
        "python",
        "-m",
        "uvicorn",
        "main:api",
        "--host",
        "0.0.0.0",
        "--port",
        "{port}",
    )

    # 3. Module alias
    d3 = detect_app("main.py", "import fastapi as fa\nx = fa.FastAPI()")
    assert d3.kind == KIND_FASTAPI
    assert d3.app_object == "x"
    assert d3.start_command == (
        "python",
        "-m",
        "uvicorn",
        "main:x",
        "--host",
        "0.0.0.0",
        "--port",
        "{port}",
    )

    # 4. Class alias
    d4 = detect_app("main.py", "from fastapi import FastAPI as FA\nweb = FA()")
    assert d4.kind == KIND_FASTAPI
    assert d4.app_object == "web"
    assert d4.start_command == (
        "python",
        "-m",
        "uvicorn",
        "main:web",
        "--host",
        "0.0.0.0",
        "--port",
        "{port}",
    )

    # 5. Annotated
    d5 = detect_app("main.py", "from fastapi import FastAPI\napp: FastAPI = FastAPI()")
    assert d5.kind == KIND_FASTAPI
    assert d5.app_object == "app"
    assert d5.start_command == (
        "python",
        "-m",
        "uvicorn",
        "main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "{port}",
    )


# ---------------------------------------------------------------------------
# 5. Imported but No Instance Found
# ---------------------------------------------------------------------------


def test_imported_but_no_instance() -> None:
    """5. Framework imported without top-level instance yields script with warning."""
    # Flask with jsonify only
    d1 = detect_app("main.py", "from flask import jsonify\nprint('no instance')")
    assert d1.kind == KIND_SCRIPT
    assert d1.serves_web is False
    assert d1.warnings == (
        "This file imports Flask but no app object was found at the top level, so it may not start as a web app.",
    )

    # Flask inside create_app() function
    d2 = detect_app(
        "main.py",
        "from flask import Flask\ndef create_app():\n    app = Flask(__name__)\n    return app\n",
    )
    assert d2.kind == KIND_SCRIPT
    assert d2.serves_web is False
    assert d2.warnings == (
        "This file imports Flask but no app object was found at the top level, so it may not start as a web app.",
    )

    # FastAPI without instance
    d3 = detect_app("main.py", "from fastapi import Request\nprint('no instance')")
    assert d3.kind == KIND_SCRIPT
    assert d3.serves_web is False
    assert d3.warnings == (
        "This file imports FastAPI but no app object was found at the top level, so it may not start as a web app.",
    )


# ---------------------------------------------------------------------------
# 6. Precedence Rules
# ---------------------------------------------------------------------------


def test_precedence() -> None:
    """6. Precedence: streamlit+flask -> streamlit; fastapi+flask -> fastapi."""
    # streamlit + flask
    src_st_flask = (
        "import streamlit as st\n"
        "from flask import Flask\n"
        "app = Flask(__name__)\n"
        "st.write('both')\n"
    )
    d1 = detect_app("main.py", src_st_flask)
    assert d1.kind == KIND_STREAMLIT
    assert d1.framework == "streamlit"

    # fastapi + flask (both instances)
    src_fast_flask = (
        "from fastapi import FastAPI\n"
        "from flask import Flask\n"
        "api = FastAPI()\n"
        "server = Flask(__name__)\n"
    )
    d2 = detect_app("main.py", src_fast_flask)
    assert d2.kind == KIND_FASTAPI
    assert d2.framework == "fastapi"
    assert d2.app_object == "api"


# ---------------------------------------------------------------------------
# 7. Look-alike Names & Non-ASCII Identifiers
# ---------------------------------------------------------------------------


def test_look_alike_identifiers() -> None:
    """7. Look-alike identifiers never produce non-ASCII app_object; app_object must match _IDENTIFIER_RE."""
    # Full-width letter 'ａpp'
    src_full_width = "from flask import Flask\n\uff41pp = Flask(__name__)"
    d_fw = detect_app("main.py", src_full_width)
    if d_fw.app_object is not None:
        assert d_fw.app_object == "app"
        assert _IDENTIFIER_RE.fullmatch(d_fw.app_object)
    else:
        assert d_fw.kind == KIND_SCRIPT
        assert d_fw.warnings == (
            "The app object has an unusual name, so it was not detected.",
        )

    # Greek alpha 'α' (non-ASCII, cannot normalize to ASCII identifier)
    src_greek = "from flask import Flask\nα = Flask(__name__)"
    d_greek = detect_app("main.py", src_greek)
    assert d_greek.app_object is None
    assert d_greek.kind == KIND_SCRIPT
    assert d_greek.warnings == (
        "The app object has an unusual name, so it was not detected.",
    )


# ---------------------------------------------------------------------------
# 8. Bad Input Handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_input",
    [
        'print("unterminated',
        "  print(1)",
        "a\x00b",
        b"\xff\xfe\x00\x80",
        "",
        "[" * 1000,
    ],
)
def test_bad_input_never_raises_and_never_leaks(bad_input: str | bytes) -> None:
    """8. Bad input returns a script Detection with problems and no paths or tracebacks."""
    d = detect_app("main.py", bad_input)
    assert d.kind == KIND_SCRIPT
    assert d.start_command == ("python", "main.py")
    assert d.serves_web is False
    assert len(d.problems) > 0

    for p in d.problems:
        assert "Traceback" not in p
        assert "\\" not in p
        assert "/" not in p


def test_pathological_expression_subprocess() -> None:
    """8b. Pathological deep expression in subprocess exits cleanly within 20s."""
    script = (
        "from app.detect import detect_app\n"
        "bad = 'x = ' + '-' * 100000 + '1'\n"
        "d = detect_app('main.py', bad)\n"
        "assert d.kind == 'script'\n"
        "assert len(d.problems) > 0\n"
        "print('ok')\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert res.returncode == 0
    assert "ok" in res.stdout


# ---------------------------------------------------------------------------
# 9. UTF-8 BOM
# ---------------------------------------------------------------------------


def test_utf8_bom_handling() -> None:
    """9. UTF-8 BOM is stripped and code parses normally."""
    bom_content = b"\xef\xbb\xbfimport streamlit as st\nst.title('BOM App')"
    d = detect_app("main.py", bom_content)
    assert d.kind == KIND_STREAMLIT
    assert d.framework == "streamlit"
    assert d.serves_web is True


# ---------------------------------------------------------------------------
# 10. Extensions
# ---------------------------------------------------------------------------


def test_extension_validation() -> None:
    """10. Extension validation accepts .html, .htm, .py (case-insensitive) and rejects others."""
    assert detect_app("page.HTML", "<h1>Hi</h1>").kind == KIND_STATIC
    assert detect_app("page.htm", "<h1>Hi</h1>").kind == KIND_STATIC
    assert detect_app("MAIN.PY", "print(1)").kind == KIND_SCRIPT

    for invalid in ["run.exe", "noext", "x.py.exe", ""]:
        with pytest.raises(ValueError, match="unsupported file type"):
            detect_app(invalid, "print(1)")


# ---------------------------------------------------------------------------
# 11. build_command
# ---------------------------------------------------------------------------


def test_build_command() -> None:
    """11. build_command substitutes port, rejects static/invalid ports, preserves original."""
    d_static = detect_app("index.html", "<h1>Hi</h1>")
    with pytest.raises(ValueError, match="static apps have no start command"):
        build_command(d_static, 8000)

    d_st = detect_app("main.py", "import streamlit as st")
    cmd = build_command(d_st, 8501)
    assert isinstance(cmd, list)
    assert "8501" in cmd
    assert PORT_PLACEHOLDER not in cmd
    assert all(isinstance(x, str) for x in cmd)

    # Immutability check
    assert PORT_PLACEHOLDER in d_st.start_command  # type: ignore[operator]

    # Invalid ports
    for bad_port in [0, 65536, -1, True, "80", 80.0, None]:
        with pytest.raises(ValueError):
            build_command(d_st, bad_port)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 12. System Consistency
# ---------------------------------------------------------------------------


def test_system_consistency() -> None:
    """12. Constants match app.models.AppKind and app.code_store.ALLOWED_EXTENSIONS."""
    from app.code_store import ALLOWED_EXTENSIONS
    from app.models import AppKind

    expected_kinds = {k.value for k in AppKind}
    detected_kinds = {
        KIND_STATIC,
        KIND_STREAMLIT,
        KIND_FLASK,
        KIND_FASTAPI,
        KIND_SCRIPT,
    }
    assert detected_kinds == expected_kinds

    assert MAIN_PY in ALLOWED_EXTENSIONS.values()
    assert INDEX_HTML in ALLOWED_EXTENSIONS.values()


# ---------------------------------------------------------------------------
# 13. No Execution Proof
# ---------------------------------------------------------------------------


def test_no_execution_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    """13. detect_app parses AST only; code with side-effects or SystemExit is never executed."""
    monkeypatch.delenv("T16_EXECUTED", raising=False)

    code_side_effect = "import os\nos.environ['T16_EXECUTED'] = '1'"
    detect_app("main.py", code_side_effect)
    assert "T16_EXECUTED" not in os.environ

    code_exit = "raise SystemExit('should not exit')"
    # Must not exit the test process
    d = detect_app("main.py", code_exit)
    assert d.kind == KIND_SCRIPT


# ---------------------------------------------------------------------------
# 14. Import Safety
# ---------------------------------------------------------------------------


def test_import_has_no_side_effects() -> None:
    """14. In a fresh subprocess without SECRET_KEY, import app.detect succeeds and imports neither config nor models."""
    env = os.environ.copy()
    env.pop("SECRET_KEY", None)

    code = (
        "import sys\n"
        "import app.detect\n"
        "assert 'app.config' not in sys.modules, 'app.config was imported unexpectedly'\n"
        "assert 'app.models' not in sys.modules, 'app.models was imported unexpectedly'\n"
        "assert 'app.db' not in sys.modules, 'app.db was imported unexpectedly'\n"
    )

    res = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, (
        f"Subprocess failed:\nstdout: {res.stdout}\nstderr: {res.stderr}"
    )
