"""Endpoints for creating and inspecting deployed applications."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.code_store import (
    CodeStoreError,
    DeploymentExistsError,
    FileTooLargeError,
    InvalidContentError,
    InvalidFilenameError,
    MAX_FILE_BYTES,
    MAX_REQUIREMENTS_BYTES,
    UnsupportedFileTypeError,
    remove_deployment,
    save_deployment,
)
from app.config import get_settings
from app.db import get_session
from app.models import App, AppKind, Deployment, User
from app.slugs import new_unique_slug

router = APIRouter(prefix="/api", tags=["apps"])
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


async def _read_limited(upload: UploadFile, *, limit: int) -> bytes:
    """Read an upload while enforcing its configured size limit."""
    content = await upload.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(
            status_code=413,
            detail="The uploaded file is larger than the allowed limit.",
        )
    return content


def _detect_kind(filename: str, source: bytes) -> AppKind:
    """Infer the runtime kind from the file extension and Python imports."""
    if Path(filename).suffix.lower() == ".html":
        return AppKind.static

    source_text = source.decode("utf-8-sig", errors="ignore")
    if re.search(r"^\s*(?:from\s+streamlit\b|import\s+streamlit\b)", source_text, re.M):
        return AppKind.streamlit
    if re.search(r"^\s*(?:from\s+flask\b|import\s+flask\b)", source_text, re.M):
        return AppKind.flask
    if re.search(r"^\s*(?:from\s+fastapi\b|import\s+fastapi\b)", source_text, re.M):
        return AppKind.fastapi
    return AppKind.script


@router.post("/deploy", status_code=201)
async def deploy_app(
    request: Request,
    file: UploadFile = File(...),
    owner_email: str = Form(...),
    name: str | None = Form(default=None),
    requirements: UploadFile | None = File(default=None),
    session: Session = Depends(get_session),
) -> dict[str, str | int | bool]:
    """Store a Python or HTML deployment and create its app records.

    M1 runs without login, so the caller supplies the owner email. Runtime
    startup is handled by the runtime integration once its shared interface
    is in place; this endpoint records the deployment and returns its route.
    """
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in {".py", ".html"}:
        raise HTTPException(status_code=415, detail="Upload a .py or .html file.")

    email = owner_email.strip().lower()
    if len(email) > 320 or not _EMAIL_RE.fullmatch(email):
        raise HTTPException(status_code=422, detail="Enter a valid owner email address.")

    app_name = (name or Path(filename).stem).strip()
    if not app_name or len(app_name) > 100:
        raise HTTPException(
            status_code=422, detail="The app name must be between 1 and 100 characters."
        )

    source = await _read_limited(file, limit=MAX_FILE_BYTES)

    requirements_bytes: bytes | None = None
    if requirements is not None and requirements.filename:
        if suffix != ".py":
            raise HTTPException(
                status_code=422,
                detail="A requirements.txt file can only be uploaded with a Python app.",
            )
        if Path(requirements.filename).name.lower() != "requirements.txt":
            raise HTTPException(
                status_code=422,
                detail="The optional dependency file must be named requirements.txt.",
            )
        requirements_bytes = await _read_limited(
            requirements, limit=MAX_REQUIREMENTS_BYTES
        )
        try:
            requirements_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=422, detail="requirements.txt must be a UTF-8 text file."
            ) from exc

    kind = _detect_kind(filename, source)
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email)
        session.add(user)
        session.flush()

    app_record = App(
        owner_id=user.id,
        slug=new_unique_slug(session),
        name=app_name,
        kind=kind.value,
    )
    session.add(app_record)
    session.flush()

    stored_path: Path | None = None
    try:
        stored = save_deployment(
            app_record.id,
            1,
            filename=filename,
            content=source,
            requirements=requirements_bytes,
        )
        stored_path = stored.path
        session.add(
            Deployment(
                app_id=app_record.id,
                version=1,
                code_path=str((stored.path / stored.main_file).resolve()),
                has_requirements=stored.has_requirements,
            )
        )
        session.commit()
    except CodeStoreError as exc:
        session.rollback()
        if isinstance(exc, FileTooLargeError):
            status_code = 413
        elif isinstance(exc, DeploymentExistsError):
            status_code = 409
        elif isinstance(
            exc,
            (InvalidContentError, InvalidFilenameError, UnsupportedFileTypeError),
        ):
            status_code = 422
        else:
            status_code = 500
        raise HTTPException(status_code=status_code, detail=exc.message) from exc
    except Exception:
        session.rollback()
        if stored_path is not None:
            remove_deployment(app_record.id, 1)
        raise

    settings_domain = (get_settings().apps_domain or "localhost").strip(".")
    port = request.url.port or 8000
    url = f"{request.url.scheme}://{app_record.slug}.{settings_domain}:{port}"
    return {
        "id": app_record.id,
        "slug": app_record.slug,
        "name": app_record.name,
        "kind": app_record.kind,
        "status": app_record.status,
        "url": url,
        "has_requirements": requirements_bytes is not None,
    }
