"""Tests for app.code_store (T1.5: Code storage).

Verifies secure saving, validation, isolation, and removal of user code uploads.
Ensures zero side-effects on import, robust hostile filename rejection, atomic
failure rollbacks, and protection against directory traversal and symlink escapes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import app.code_store
from app.code_store import (
    MAX_FILE_BYTES,
    MAX_REQUIREMENTS_BYTES,
    CodeStoreError,
    DeploymentExistsError,
    FileTooLargeError,
    InvalidContentError,
    InvalidFilenameError,
    StoredDeployment,
    UnsafePathError,
    UnsupportedFileTypeError,
    default_root,
    remove_app_code,
    remove_deployment,
    save_deployment,
)

# ---------------------------------------------------------------------------
# 1. Happy Path: Python Deployment with Requirements
# ---------------------------------------------------------------------------


def test_save_deployment_happy_path(tmp_path: Path) -> None:
    """1. Save main.py with requirements; files exist at root/7/v1/main.py and
    requirements.txt with identical bytes; has_requirements True; no .tmp-* left over.
    """
    root = tmp_path / "apps"
    code = b"print('Hello, SmallCloud!')\n"
    reqs = b"fastapi>=0.100.0\nuvicorn\n"

    result = save_deployment(
        7,
        1,
        filename="main.py",
        content=code,
        requirements=reqs,
        root=root,
    )

    expected_dir = root / "7" / "v1"
    assert result == StoredDeployment(
        path=expected_dir,
        main_file="main.py",
        has_requirements=True,
        original_name="main.py",
        size_bytes=len(code),
    )

    main_file = expected_dir / "main.py"
    req_file = expected_dir / "requirements.txt"

    assert main_file.is_file()
    assert main_file.read_bytes() == code
    assert req_file.is_file()
    assert req_file.read_bytes() == reqs

    # Ensure no temporary folders remain
    app_dir = root / "7"
    leftovers = [p.name for p in app_dir.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == [], f"Found orphaned temp folders: {leftovers}"


# ---------------------------------------------------------------------------
# 2. Canonicalization: HTML Application
# ---------------------------------------------------------------------------


def test_save_deployment_html_canonicalization(tmp_path: Path) -> None:
    """2. 'Index Page.html' is stored as index.html and original_name is preserved."""
    root = tmp_path / "apps"
    html_code = b"<!DOCTYPE html><html><body><h1>SmallCloud</h1></body></html>"

    result = save_deployment(
        10,
        2,
        filename="Index Page.html",
        content=html_code,
        root=root,
    )

    expected_dir = root / "10" / "v2"
    assert result.path == expected_dir
    assert result.main_file == "index.html"
    assert result.has_requirements is False
    assert result.original_name == "Index Page.html"
    assert result.size_bytes == len(html_code)

    index_file = expected_dir / "index.html"
    assert index_file.is_file()
    assert index_file.read_bytes() == html_code
    assert not (expected_dir / "requirements.txt").exists()


# ---------------------------------------------------------------------------
# 3. Parametrized Hostile Filenames
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile_name",
    [
        "../../etc/passwd",
        r"..\..\windows\system32\x.py",
        "/etc/passwd",
        "C:\\x.py",
        "a/b.py",
        "a..py",
        ".hidden.py",
        "app.py.",
        "app.py ",
        " app.py",
        "",
        "x" * 101 + ".py",
        "a\x00.py",
        "a\n.py",
        "CON.py",
        "nul.html",
        "COM1.py",
        "lpt9.htm",
        "a\uff0fb.py",
    ],
)
def test_hostile_filenames_rejected_and_root_empty(
    tmp_path: Path, hostile_name: str
) -> None:
    """3. Parametrized hostile names each raise InvalidFilenameError and leave root EMPTY."""
    root = tmp_path / "apps"

    with pytest.raises(InvalidFilenameError) as exc_info:
        save_deployment(
            1,
            1,
            filename=hostile_name,
            content=b"print('should never be written')",
            root=root,
        )

    assert exc_info.value.message
    # Root must remain empty (or non-existent)
    if root.exists():
        assert list(root.rglob("*")) == []


# ---------------------------------------------------------------------------
# 4. Unsupported File Types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "unsupported_name",
    [
        "run.exe",
        "run.sh",
        "archive.zip",
        "noextension",
        "main.py.exe",
    ],
)
def test_unsupported_file_types_rejected(tmp_path: Path, unsupported_name: str) -> None:
    """4. Unsupported types raise UnsupportedFileTypeError and write nothing."""
    root = tmp_path / "apps"

    with pytest.raises(UnsupportedFileTypeError) as exc_info:
        save_deployment(
            1,
            1,
            filename=unsupported_name,
            content=b"binary-or-unsupported-data",
            root=root,
        )

    assert "Only .py and .html files are supported for now." in exc_info.value.message
    if root.exists():
        assert list(root.rglob("*")) == []


# ---------------------------------------------------------------------------
# 5. Size Boundaries
# ---------------------------------------------------------------------------


def test_file_size_limits(tmp_path: Path) -> None:
    """5. Exactly MAX_FILE_BYTES is accepted; MAX_FILE_BYTES + 1 raises FileTooLargeError;
    empty raises InvalidContentError; requirements over limit raises FileTooLargeError.
    """
    root = tmp_path / "apps"

    # Exactly MAX_FILE_BYTES is accepted
    exact_code = b"a" * MAX_FILE_BYTES
    res = save_deployment(
        1,
        1,
        filename="main.py",
        content=exact_code,
        root=root,
    )
    assert res.size_bytes == MAX_FILE_BYTES

    # MAX_FILE_BYTES + 1 raises FileTooLargeError
    too_large_code = b"a" * (MAX_FILE_BYTES + 1)
    with pytest.raises(FileTooLargeError):
        save_deployment(
            1,
            2,
            filename="main.py",
            content=too_large_code,
            root=root,
        )
    assert not (root / "1" / "v2").exists()

    # Empty content raises InvalidContentError ("The file is empty.")
    with pytest.raises(InvalidContentError) as exc_info:
        save_deployment(
            1,
            3,
            filename="main.py",
            content=b"",
            root=root,
        )
    assert exc_info.value.message == "The file is empty."
    assert not (root / "1" / "v3").exists()

    # Requirements: exact limit accepted
    exact_reqs = b"r" * MAX_REQUIREMENTS_BYTES
    res_req = save_deployment(
        1,
        4,
        filename="main.py",
        content=b"print(1)",
        requirements=exact_reqs,
        root=root,
    )
    assert res_req.has_requirements is True

    # Requirements: over limit raises FileTooLargeError
    too_large_reqs = b"r" * (MAX_REQUIREMENTS_BYTES + 1)
    with pytest.raises(FileTooLargeError):
        save_deployment(
            1,
            5,
            filename="main.py",
            content=b"print(1)",
            requirements=too_large_reqs,
            root=root,
        )
    assert not (root / "1" / "v5").exists()


# ---------------------------------------------------------------------------
# 6. Content Validation (NUL byte, UTF-8, BOM)
# ---------------------------------------------------------------------------


def test_content_validation_rules(tmp_path: Path) -> None:
    """6. NUL byte raises InvalidContentError; invalid UTF-8 raises InvalidContentError;
    UTF-8 with BOM is accepted and stored unchanged. Empty requirements means none provided.
    """
    root = tmp_path / "apps"

    # NUL byte in content raises InvalidContentError
    with pytest.raises(InvalidContentError):
        save_deployment(
            2,
            1,
            filename="main.py",
            content=b"print('hello')\x00print('world')",
            root=root,
        )

    # Invalid UTF-8 raises InvalidContentError
    with pytest.raises(InvalidContentError):
        save_deployment(
            2,
            1,
            filename="main.py",
            content=b"\xff\xfe\x00\x80",
            root=root,
        )

    # UTF-8 with BOM is accepted and stored unchanged
    bom_content = b"\xef\xbb\xbfprint('BOM content')\n"
    res = save_deployment(
        2,
        1,
        filename="main.py",
        content=bom_content,
        root=root,
    )
    saved_file = res.path / "main.py"
    assert saved_file.read_bytes() == bom_content

    # Empty requirements (b"") means "none provided"
    res2 = save_deployment(
        2,
        2,
        filename="main.py",
        content=b"print('no reqs')",
        requirements=b"",
        root=root,
    )
    assert res2.has_requirements is False
    assert not (res2.path / "requirements.txt").exists()


# ---------------------------------------------------------------------------
# 7. ID Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_id", [0, -1, True, "1", 1.5, None])
def test_id_validation_rejects_non_positive_integers(
    tmp_path: Path, bad_id: object
) -> None:
    """7. app_id or version of 0, -1, True, '1', 1.5, None each raise ValueError."""
    root = tmp_path / "apps"
    content = b"print('test')"

    # Bad app_id
    with pytest.raises(ValueError):
        save_deployment(
            bad_id,  # type: ignore[arg-type]
            1,
            filename="main.py",
            content=content,
            root=root,
        )

    # Bad version
    with pytest.raises(ValueError):
        save_deployment(
            1,
            bad_id,  # type: ignore[arg-type]
            filename="main.py",
            content=content,
            root=root,
        )


# ---------------------------------------------------------------------------
# 8. Immutability
# ---------------------------------------------------------------------------


def test_deployment_immutability(tmp_path: Path) -> None:
    """8. Saving the same (app_id, version) twice raises DeploymentExistsError and
    the first file's content is unchanged.
    """
    root = tmp_path / "apps"
    initial_content = b"print('v1 initial')"
    second_content = b"print('v1 overwrite attempt')"

    res1 = save_deployment(
        3,
        1,
        filename="main.py",
        content=initial_content,
        root=root,
    )
    main_file = res1.path / "main.py"
    assert main_file.read_bytes() == initial_content

    with pytest.raises(DeploymentExistsError):
        save_deployment(
            3,
            1,
            filename="main.py",
            content=second_content,
            root=root,
        )

    # Content must remain unchanged
    assert main_file.read_bytes() == initial_content


# ---------------------------------------------------------------------------
# 9. Symlink Safety
# ---------------------------------------------------------------------------


def test_symlink_safety_outside_root(tmp_path: Path) -> None:
    """9. If root/7 is a symlink to a directory outside root, save_deployment
    raises UnsafePathError and the outside directory stays empty.
    """
    root = tmp_path / "apps"
    root.mkdir(parents=True)
    outside_dir = tmp_path / "outside_target"
    outside_dir.mkdir()

    symlink_path = root / "7"
    try:
        symlink_path.symlink_to(outside_dir, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlinks not supported in this environment: {exc}")

    with pytest.raises(UnsafePathError):
        save_deployment(
            7,
            1,
            filename="main.py",
            content=b"print('outside leak attempt')",
            root=root,
        )

    # Outside directory must remain completely empty
    assert list(outside_dir.rglob("*")) == []


# ---------------------------------------------------------------------------
# 10. Atomic Failure Rollback
# ---------------------------------------------------------------------------


def test_atomic_failure_cleans_temp_and_partial_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """10. Monkeypatch app.code_store.os.rename to raise OSError('boom'); the
    OSError propagates, and no v1 folder and no .tmp-* folder remain.
    """
    root = tmp_path / "apps"

    def fail_rename(src: object, dst: object) -> None:
        raise OSError("boom")

    monkeypatch.setattr(app.code_store.os, "rename", fail_rename)

    with pytest.raises(OSError, match="boom"):
        save_deployment(
            4,
            1,
            filename="main.py",
            content=b"print('fail rename')",
            root=root,
        )

    app_dir = root / "4"
    if app_dir.exists():
        assert not (app_dir / "v1").exists()
        leftovers = [p.name for p in app_dir.iterdir() if p.name.startswith(".tmp-")]
        assert leftovers == []


# ---------------------------------------------------------------------------
# 11. Removal Functions
# ---------------------------------------------------------------------------


def test_remove_deployment_and_remove_app_code(tmp_path: Path) -> None:
    """11. remove_deployment and remove_app_code delete what they should, are safe
    to call twice, and never delete the root; removing a symlinked folder leaves
    its target intact.
    """
    root = tmp_path / "apps"

    # Create v1 and v2
    save_deployment(5, 1, filename="main.py", content=b"print('v1')", root=root)
    save_deployment(5, 2, filename="main.py", content=b"print('v2')", root=root)

    app_dir = root / "5"
    assert (app_dir / "v1").exists()
    assert (app_dir / "v2").exists()

    # Remove version 1
    remove_deployment(5, 1, root=root)
    assert not (app_dir / "v1").exists()
    assert (app_dir / "v2").exists()

    # Calling twice is safe
    remove_deployment(5, 1, root=root)
    assert not (app_dir / "v1").exists()

    # Remove app 5 entirely
    remove_app_code(5, root=root)
    assert not app_dir.exists()

    # Calling twice is safe
    remove_app_code(5, root=root)
    assert not app_dir.exists()

    # Root itself is never deleted
    assert root.exists()

    # Symlink safety test on removal
    symlink_target = tmp_path / "do_not_delete"
    symlink_target.mkdir()
    (symlink_target / "keep.txt").write_text("precious")

    symlink_app = root / "6"
    try:
        symlink_app.symlink_to(symlink_target, target_is_directory=True)
    except (OSError, NotImplementedError):
        return  # Symlinks unavailable; rest of test passed

    # Removing symlinked app folder deletes only the link, leaving target intact
    remove_app_code(6, root=root)
    assert not symlink_app.exists()
    assert symlink_target.exists()
    assert (symlink_target / "keep.txt").read_text() == "precious"


# ---------------------------------------------------------------------------
# 12. default_root Resolution & Lazy Initialization
# ---------------------------------------------------------------------------


def test_default_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """12. With env DATA_DIR=tmp_path and SECRET_KEY set, and get_settings.cache_clear()
    before and after, default_root() == tmp_path.resolve() / 'apps', and calling it
    creates nothing.
    """
    from app.config import get_settings

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-default-root")

    get_settings.cache_clear()
    try:
        expected = tmp_path.resolve() / "apps"
        computed = default_root()
        assert computed == expected
        # Calling default_root creates nothing
        assert not computed.exists()
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 13. Safe, User-Friendly Exception Messages
# ---------------------------------------------------------------------------


def test_exception_messages_are_safe_and_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """13. For each exception type, trigger it and assert exc.message is non-empty,
    does not contain str(tmp_path), 'Traceback' or a backslash path.
    """
    root = tmp_path / "apps"
    exceptions: list[CodeStoreError] = []

    # 1. InvalidFilenameError
    try:
        save_deployment(1, 1, filename="../../etc/passwd", content=b"code", root=root)
    except InvalidFilenameError as e:
        exceptions.append(e)

    # 2. UnsupportedFileTypeError
    try:
        save_deployment(1, 1, filename="run.exe", content=b"code", root=root)
    except UnsupportedFileTypeError as e:
        exceptions.append(e)

    # 3. FileTooLargeError
    try:
        save_deployment(
            1, 1, filename="main.py", content=b"a" * (MAX_FILE_BYTES + 1), root=root
        )
    except FileTooLargeError as e:
        exceptions.append(e)

    # 4. InvalidContentError
    try:
        save_deployment(1, 1, filename="main.py", content=b"", root=root)
    except InvalidContentError as e:
        exceptions.append(e)

    # 5. DeploymentExistsError
    save_deployment(1, 1, filename="main.py", content=b"print('first')", root=root)
    try:
        save_deployment(1, 1, filename="main.py", content=b"print('second')", root=root)
    except DeploymentExistsError as e:
        exceptions.append(e)

    # 6. UnsafePathError
    monkeypatch.setattr(Path, "is_symlink", lambda self: True)
    try:
        save_deployment(
            9,
            1,
            filename="main.py",
            content=b"print('escape')",
            root=root,
        )
    except UnsafePathError as e:
        exceptions.append(e)

    assert len(exceptions) == 6, f"Expected 6 exceptions, got {len(exceptions)}"

    for exc in exceptions:
        msg = exc.message
        assert msg and isinstance(msg, str), f"Empty message in {type(exc).__name__}"
        assert str(tmp_path) not in msg, (
            f"Leaked tmp_path in {type(exc).__name__}: {msg}"
        )
        assert "Traceback" not in msg, (
            f"Leaked Traceback in {type(exc).__name__}: {msg}"
        )
        assert "\\" not in msg, f"Leaked backslash path in {type(exc).__name__}: {msg}"


# ---------------------------------------------------------------------------
# 14. Import Safety (No Side Effects)
# ---------------------------------------------------------------------------


def test_import_has_no_side_effects() -> None:
    """14. In a fresh subprocess with SECRET_KEY removed from the environment,
    'import app.code_store' succeeds and 'app.config' is not in sys.modules.
    """
    env = os.environ.copy()
    env.pop("SECRET_KEY", None)

    code = (
        "import sys\n"
        "import app.code_store\n"
        "assert 'app.config' not in sys.modules, 'app.config was imported unexpectedly'\n"
        "assert 'app.db' not in sys.modules, 'app.db was imported unexpectedly'\n"
        "assert 'app.models' not in sys.modules, 'app.models was imported unexpectedly'\n"
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
