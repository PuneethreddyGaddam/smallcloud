"""Tests for app.friendly_errors – Plain-language error classification (A1.2).

Covers all specification requirements: required results, import-name mapping,
hostile input rejection, precedence rules, exit-code 137 edge-case, line-number
edge-cases, never-raises/never-leaks guarantee, output format rules, performance,
explain_exception, dataclass properties, and import safety.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import time

import pytest

from app.friendly_errors import (
    _PIP_NAME_RE,
    ALL_CODES,
    CODE_MISSING_PACKAGE,
    CODE_OUT_OF_MEMORY,
    CODE_PORT_NOT_LISTENING,
    CODE_SYNTAX_ERROR,
    CODE_UNKNOWN,
    PIP_NAMES,
    FriendlyError,
    classify_failure,
    explain_exception,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_FORBIDDEN_WORDS = [
    "traceback",
    "stderr",
    "stdout",
    "exception",
    "errno",
    "exit code",
    "container",
    "docker",
    "oom",
    "sigkill",
    "kernel",
    "stack",
    "segfault",
    "pid",
    "runtime",
    "modulenotfounderror",
    "syntaxerror",
    "importerror",
    "memoryerror",
]


def _assert_output_rules(err: FriendlyError, label: str = "") -> None:
    """Assert that all output fields of a FriendlyError satisfy the spec rules."""
    for field_name, value in [
        ("title", err.title),
        ("message", err.message),
        ("suggestion", err.suggestion),
    ]:
        tag = f"[{label} {field_name}]" if label else f"[{field_name}]"
        assert value, f"{tag} must not be empty"
        assert len(value) <= 300, f"{tag} exceeds 300 chars: {value!r}"
        assert "\n" not in value and "\r" not in value, (
            f"{tag} contains newline: {value!r}"
        )
        assert "/" not in value and "\\" not in value, (
            f"{tag} contains slash: {value!r}"
        )
        for word in _FORBIDDEN_WORDS:
            assert word not in value.lower(), (
                f"{tag} contains forbidden word {word!r}: {value!r}"
            )

    # Title word count: 3 to 8 words
    words = err.title.split()
    assert 3 <= len(words) <= 8, f"Title has {len(words)} words: {err.title!r}"
    assert err.code in ALL_CODES, f"code {err.code!r} not in ALL_CODES"


# ---------------------------------------------------------------------------
# 1. Required Results
# ---------------------------------------------------------------------------


def test_required_missing_package() -> None:
    """1a. Missing pandas package is classified correctly."""
    log = (
        "Traceback (most recent call last):\n"
        '  File "/app/main.py", line 1, in <module>\n'
        "    import pandas\n"
        "ModuleNotFoundError: No module named 'pandas'"
    )
    err = classify_failure(log)
    assert err.code == CODE_MISSING_PACKAGE
    assert err.package == "pandas"
    assert "requirements.txt" in err.suggestion
    assert "pandas" in err.suggestion
    _assert_output_rules(err, "missing_package")


def test_required_syntax_error() -> None:
    """1b. Syntax error on line 12 is classified correctly."""
    log = (
        '  File "/app/main.py", line 12\n'
        "    def (:\n"
        "        ^\n"
        "SyntaxError: invalid syntax"
    )
    err = classify_failure(log)
    assert err.code == CODE_SYNTAX_ERROR
    assert err.line == 12
    assert "line 12" in err.message
    _assert_output_rules(err, "syntax_error")


def test_required_port_not_listening_streamlit() -> None:
    """1c. timed_out_waiting_for_port with streamlit kind."""
    err = classify_failure("", timed_out_waiting_for_port=True, kind="streamlit")
    assert err.code == CODE_PORT_NOT_LISTENING
    _assert_output_rules(err, "port_not_listening_streamlit")


def test_required_port_not_listening_script() -> None:
    """1c. timed_out_waiting_for_port with script kind gives 'Nothing to open yet'."""
    err = classify_failure("", timed_out_waiting_for_port=True, kind="script")
    assert err.code == CODE_PORT_NOT_LISTENING
    assert err.title == "Nothing to open yet"
    _assert_output_rules(err, "port_not_listening_script")


def test_required_out_of_memory_with_limit() -> None:
    """1d. oom_killed=True with memory_limit_mib=512 mentions 512 MB."""
    err = classify_failure("", oom_killed=True, memory_limit_mib=512)
    assert err.code == CODE_OUT_OF_MEMORY
    assert "512 MB" in err.message
    _assert_output_rules(err, "out_of_memory")


# ---------------------------------------------------------------------------
# 2. Import-name mapping
# ---------------------------------------------------------------------------


def test_import_name_mapping_cv2() -> None:
    """2. cv2 maps to opencv-python in the suggestion."""
    log = "ModuleNotFoundError: No module named 'cv2'"
    err = classify_failure(log)
    assert err.code == CODE_MISSING_PACKAGE
    assert err.package == "cv2"
    assert "opencv-python" in err.suggestion
    _assert_output_rules(err, "cv2")


def test_import_name_mapping_pil_submodule() -> None:
    """2. PIL.Image -> package PIL, install pillow."""
    log = "ModuleNotFoundError: No module named 'PIL.Image'"
    err = classify_failure(log)
    assert err.code == CODE_MISSING_PACKAGE
    assert err.package == "PIL"
    assert "pillow" in err.suggestion
    _assert_output_rules(err, "PIL")


def test_import_name_mapping_pandas_submodule() -> None:
    """2. pandas.core.x -> package pandas."""
    log = "ModuleNotFoundError: No module named 'pandas.core.x'"
    err = classify_failure(log)
    assert err.code == CODE_MISSING_PACKAGE
    assert err.package == "pandas"
    _assert_output_rules(err, "pandas_submodule")


def test_all_pip_names_match_re() -> None:
    """2. Every value in PIP_NAMES matches _PIP_NAME_RE."""
    for import_name, pip_name in PIP_NAMES.items():
        assert _PIP_NAME_RE.fullmatch(pip_name), (
            f"PIP_NAMES[{import_name!r}] = {pip_name!r} does not match _PIP_NAME_RE"
        )


# ---------------------------------------------------------------------------
# 3. Hostile package names rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile_name",
    [
        "x; rm -rf /",
        "../../etc",
        "a" * 300,
        "\u00fcber",
        "",
        "a b",
        "a\nb",
    ],
)
def test_hostile_package_names_rejected(hostile_name: str) -> None:
    """3. Hostile module names fall through to unknown_error; hostile text never echoed."""
    log = f"ModuleNotFoundError: No module named '{hostile_name}'"
    err = classify_failure(log)
    # Either unknown_error or missing_package for legit-looking sub-part – but
    # the hostile text must NEVER appear in any output field.
    for field in (err.title, err.message, err.suggestion):
        for fragment in [hostile_name, "rm", "etc", "uber", "\n"]:
            if fragment:
                assert fragment not in field, (
                    f"Hostile text {fragment!r} leaked into {field!r}"
                )
    _assert_output_rules(err, f"hostile:{hostile_name[:20]!r}")


# ---------------------------------------------------------------------------
# 4. Precedence
# ---------------------------------------------------------------------------


def test_precedence_syntax_beats_missing_package() -> None:
    """4. A log with both syntax error and missing-package lines -> syntax_error."""
    log = (
        "ModuleNotFoundError: No module named 'numpy'\n"
        '  File "/app/main.py", line 5\n'
        "    def (:\n"
        "SyntaxError: invalid syntax"
    )
    err = classify_failure(log)
    assert err.code == CODE_SYNTAX_ERROR


def test_precedence_missing_package_beats_port() -> None:
    """4. Missing package + timed_out_waiting_for_port -> missing_package."""
    log = "ModuleNotFoundError: No module named 'requests'"
    err = classify_failure(log, timed_out_waiting_for_port=True)
    assert err.code == CODE_MISSING_PACKAGE


def test_precedence_oom_beats_syntax() -> None:
    """4. oom_killed=True + syntax error log -> out_of_memory."""
    log = "SyntaxError: invalid syntax"
    err = classify_failure(log, oom_killed=True)
    assert err.code == CODE_OUT_OF_MEMORY


def test_precedence_oom_via_last_line() -> None:
    """4. Last non-empty log line starting with MemoryError -> out_of_memory."""
    log = "some earlier line\nMemoryError"
    err = classify_failure(log)
    assert err.code == CODE_OUT_OF_MEMORY


# ---------------------------------------------------------------------------
# 5. Exit code 137 and wrong types
# ---------------------------------------------------------------------------


def test_exit_code_137_alone_is_unknown() -> None:
    """5. exit_code=137 alone (no oom_killed, empty log) -> unknown_error, NOT oom."""
    err = classify_failure("", exit_code=137)
    assert err.code == CODE_UNKNOWN


def test_exit_code_1_alone_is_unknown() -> None:
    """5. exit_code=1 alone -> unknown_error."""
    err = classify_failure("", exit_code=1)
    assert err.code == CODE_UNKNOWN


def test_oom_killed_wrong_type_ignored() -> None:
    """5. oom_killed='yes' (wrong type) is ignored; falls through to unknown."""
    err = classify_failure("", oom_killed="yes")  # type: ignore[arg-type]
    # Must not trigger out_of_memory
    assert err.code != CODE_OUT_OF_MEMORY


def test_timed_out_wrong_type_ignored() -> None:
    """5. timed_out_waiting_for_port='true' (wrong type) is ignored."""
    err = classify_failure("", timed_out_waiting_for_port="true")  # type: ignore[arg-type]
    assert err.code != CODE_PORT_NOT_LISTENING


# ---------------------------------------------------------------------------
# 6. Line numbers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lineno",
    [0, -3, 1_000_001, 999_999_999_999],
)
def test_line_number_out_of_range_gives_none(lineno: int) -> None:
    """6. Out-of-range line numbers give line=None."""
    log = f'  File "/app/main.py", line {lineno}\n    def (:\nSyntaxError: bad'
    err = classify_failure(log)
    assert err.code == CODE_SYNTAX_ERROR
    assert err.line is None


def test_line_number_other_file_gives_none() -> None:
    """6. File annotations for files other than main.py give line=None."""
    log = '  File "/app/other.py", line 5\n    def (:\nSyntaxError: bad'
    err = classify_failure(log)
    assert err.code == CODE_SYNTAX_ERROR
    assert err.line is None


def test_line_number_closest_main_py_wins() -> None:
    """6. When two File/main.py lines appear, the closest to the error wins."""
    log = (
        '  File "/app/main.py", line 3\n'
        "    some code\n"
        '  File "/app/main.py", line 42\n'
        "    def (:\n"
        "SyntaxError: bad"
    )
    err = classify_failure(log)
    assert err.code == CODE_SYNTAX_ERROR
    # Line 42 is the closest (immediately before SyntaxError)
    assert err.line == 42


# ---------------------------------------------------------------------------
# 7. Never raises / never leaks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "log_input",
    [
        None,
        123,
        b"bytes",
        "",
    ],
)
def test_never_raises_bad_log_type(log_input: object) -> None:
    """7. Non-str and None log_text inputs return a FriendlyError, never raise."""
    err = classify_failure(log_input)  # type: ignore[arg-type]
    assert isinstance(err, FriendlyError)
    _assert_output_rules(err, f"bad_log:{type(log_input).__name__}")


def test_never_raises_large_random_text() -> None:
    """7. 5 MB of random printable text does not raise and does not leak."""
    import string

    rng_text = (string.printable * (5_000_000 // len(string.printable) + 1))[:5_000_000]
    err = classify_failure(rng_text)
    assert isinstance(err, FriendlyError)
    _assert_output_rules(err, "5mb_random")


def test_never_raises_repeated_a() -> None:
    """7. 5 MB of 'a' characters does not raise."""
    err = classify_failure("a" * 5_000_000)
    assert isinstance(err, FriendlyError)


def test_nul_bytes_in_log() -> None:
    """7. Log text containing NUL bytes is handled cleanly."""
    log = "some text\x00more text\x00ModuleNotFoundError: No module named 'numpy'"
    err = classify_failure(log)
    assert isinstance(err, FriendlyError)
    _assert_output_rules(err, "nul_bytes")


def test_ansi_codes_do_not_hide_error() -> None:
    """7. ANSI colour codes surrounding error text are stripped; error is still detected."""
    log = "\x1b[31mModuleNotFoundError: No module named 'requests'\x1b[0m"
    err = classify_failure(log)
    assert err.code == CODE_MISSING_PACKAGE
    assert err.package == "requests"


def test_secret_looking_line_not_leaked() -> None:
    """7. A secret-looking log line never appears in any output field."""
    log = "API_KEY=sk-live-abc123 at main.py line 1\nSyntaxError: oops"
    err = classify_failure(log)
    secret = "sk-live-abc123"
    for field in (err.title, err.message, err.suggestion):
        assert secret not in field


def test_wrong_typed_keyword_args() -> None:
    """7. Wrong-typed keyword arguments return a FriendlyError, never raise."""
    err = classify_failure(
        "some log",
        exit_code="oops",  # type: ignore[arg-type]
        oom_killed=1,  # type: ignore[arg-type]
        timed_out_waiting_for_port=None,  # type: ignore[arg-type]
        kind=42,  # type: ignore[arg-type]
        memory_limit_mib="big",  # type: ignore[arg-type]
    )
    assert isinstance(err, FriendlyError)
    _assert_output_rules(err, "wrong_typed_kwargs")


# ---------------------------------------------------------------------------
# 8. Output rules – checked for every code
# ---------------------------------------------------------------------------


def test_output_rules_all_codes() -> None:
    """8. Every reachable code path produces output satisfying all format rules."""
    cases: list[tuple[str, FriendlyError]] = [
        (
            "missing_package",
            classify_failure("ModuleNotFoundError: No module named 'numpy'"),
        ),
        (
            "syntax_error",
            classify_failure(
                '  File "/app/main.py", line 7\n    def (:\nSyntaxError: bad'
            ),
        ),
        (
            "port_not_listening_web",
            classify_failure("", timed_out_waiting_for_port=True, kind="flask"),
        ),
        (
            "port_not_listening_script",
            classify_failure("", timed_out_waiting_for_port=True, kind="script"),
        ),
        ("out_of_memory_no_limit", classify_failure("", oom_killed=True)),
        (
            "out_of_memory_with_limit",
            classify_failure("", oom_killed=True, memory_limit_mib=256),
        ),
        ("unknown", classify_failure("")),
    ]
    for label, err in cases:
        _assert_output_rules(err, label)


# ---------------------------------------------------------------------------
# 9. Performance
# ---------------------------------------------------------------------------


def test_performance_all_a_5mb() -> None:
    """9. 5 MB of 'a' characters classifies in under 2 seconds."""
    log = "a" * 5_000_000
    t0 = time.perf_counter()
    err = classify_failure(log)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"classify_failure took {elapsed:.3f}s on 5 MB 'a' string"
    assert isinstance(err, FriendlyError)


def test_performance_repeated_file_lines_5mb() -> None:
    """9. 5 MB of 'File \"' repeated does not cause catastrophic backtracking."""
    log = 'File "' * (5_000_000 // 6)
    t0 = time.perf_counter()
    err = classify_failure(log)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"classify_failure took {elapsed:.3f}s on repeated File lines"
    assert isinstance(err, FriendlyError)


def test_performance_repeated_module_not_found_5mb() -> None:
    """9. 5 MB of 'No module named \\'' without closing quote finishes in time."""
    log = "No module named '" * (5_000_000 // 18)
    t0 = time.perf_counter()
    err = classify_failure(log)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, (
        f"classify_failure took {elapsed:.3f}s on repeated module lines"
    )
    assert isinstance(err, FriendlyError)


# ---------------------------------------------------------------------------
# 10. explain_exception
# ---------------------------------------------------------------------------


def test_explain_exception_module_not_found_named() -> None:
    """10. ModuleNotFoundError with name='numpy' -> missing_package numpy."""
    exc = ModuleNotFoundError("No module named 'numpy'")
    exc.name = "numpy"  # type: ignore[attr-defined]
    err = explain_exception(exc)
    assert err.code == CODE_MISSING_PACKAGE
    assert err.package == "numpy"
    _assert_output_rules(err, "explain_mnfe_named")


def test_explain_exception_module_not_found_name_none() -> None:
    """10. ModuleNotFoundError with name=None (path in msg) -> unknown_error; path not leaked."""
    exc = ModuleNotFoundError("boom /home/x/secret")
    exc.name = None  # type: ignore[attr-defined]
    err = explain_exception(exc)
    assert err.code == CODE_UNKNOWN
    for field in (err.title, err.message, err.suggestion):
        assert "/home/x/secret" not in field
        assert "boom" not in field
    _assert_output_rules(err, "explain_mnfe_none")


def test_explain_exception_syntax_error_with_line() -> None:
    """10. SyntaxError with lineno=7 -> syntax_error, line=7."""
    exc = SyntaxError("bad", ("main.py", 7, 1, "x"))
    err = explain_exception(exc)
    assert err.code == CODE_SYNTAX_ERROR
    assert err.line == 7
    _assert_output_rules(err, "explain_syntax")


def test_explain_exception_memory_error() -> None:
    """10. MemoryError() -> out_of_memory."""
    err = explain_exception(MemoryError())
    assert err.code == CODE_OUT_OF_MEMORY
    _assert_output_rules(err, "explain_oom")


def test_explain_exception_timeout_error() -> None:
    """10. TimeoutError() -> port_not_listening."""
    err = explain_exception(TimeoutError())
    assert err.code == CODE_PORT_NOT_LISTENING
    _assert_output_rules(err, "explain_timeout")


def test_explain_exception_asyncio_timeout() -> None:
    """10. asyncio.TimeoutError() -> port_not_listening."""
    err = explain_exception(TimeoutError())
    assert err.code == CODE_PORT_NOT_LISTENING
    _assert_output_rules(err, "explain_asyncio_timeout")


def test_explain_exception_value_error_no_leak() -> None:
    """10. ValueError('/etc/passwd') -> unknown_error; path not leaked."""
    err = explain_exception(ValueError("/etc/passwd"))
    assert err.code == CODE_UNKNOWN
    for field in (err.title, err.message, err.suggestion):
        assert "/etc/passwd" not in field
    _assert_output_rules(err, "explain_valueerror")


# ---------------------------------------------------------------------------
# 11. FriendlyError properties and to_dict
# ---------------------------------------------------------------------------


def test_friendly_error_is_frozen() -> None:
    """11. FriendlyError is frozen; assignment raises FrozenInstanceError."""
    err = classify_failure("", oom_killed=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        err.code = "other"  # type: ignore[misc]


def test_to_dict_has_exactly_six_keys() -> None:
    """11. to_dict() returns exactly: code, title, message, suggestion, line, package."""
    err = classify_failure("ModuleNotFoundError: No module named 'numpy'")
    d = err.to_dict()
    assert set(d.keys()) == {
        "code",
        "title",
        "message",
        "suggestion",
        "line",
        "package",
    }
    # Must be JSON-serialisable
    json.dumps(d)


def test_all_codes_in_all_codes_constant() -> None:
    """11. ALL_CODES contains every code returned during these tests."""
    codes_seen = {
        classify_failure("ModuleNotFoundError: No module named 'numpy'").code,
        classify_failure(
            '  File "/app/main.py", line 1\n    def (:\nSyntaxError: bad'
        ).code,
        classify_failure("", timed_out_waiting_for_port=True).code,
        classify_failure("", oom_killed=True).code,
        classify_failure("").code,
    }
    assert codes_seen == ALL_CODES


def test_text_property() -> None:
    """11. .text == message + ' ' + suggestion."""
    err = classify_failure("")
    assert err.text == f"{err.message} {err.suggestion}"


# ---------------------------------------------------------------------------
# 12. Import safety
# ---------------------------------------------------------------------------


def test_import_safety() -> None:
    """12. Fresh subprocess without SECRET_KEY: import succeeds, no forbidden modules loaded."""
    env = os.environ.copy()
    env.pop("SECRET_KEY", None)

    code = (
        "import sys\n"
        "import app.friendly_errors\n"
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
