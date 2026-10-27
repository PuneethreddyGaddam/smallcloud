"""Endpoints for creating and inspecting deployed applications."""

from __future__ import annotations

import re
import secrets
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import App, AppKind, Deployment, User

router = APIRouter(prefix="/api", tags=["apps"])
MAX_DEPLOYMENT_BYTES = 5 * 1024 * 1024
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


async def _read_limited(upload: UploadFile, *, remaining: int) -> bytes:
    """Read an upload while enforcing the combined deployment size limit."""
    content = await upload.read(remaining + 1)
    if len(content) > remaining:
        raise HTTPException(
            status_code=413,
            detail="The app and its requirements file must be 5 MB or smaller.",
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


def _new_slug(name: str) -> str:
    """Create a readable, collision-resistant slug within the DNS label limit."""
    stem = slugify(name, max_length=48) or "app"
    return f"{stem}-{secrets.token_hex(3)}"


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
    filename = Path(file.filename or "").name
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

    source = await _read_limited(file, remaining=MAX_DEPLOYMENT_BYTES)
    if not source.strip():
        raise HTTPException(status_code=422, detail="The uploaded app file is empty.")

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
            requirements, remaining=MAX_DEPLOYMENT_BYTES - len(source)
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

    slug = _new_slug(app_name)
    while session.scalar(select(App.id).where(App.slug == slug)) is not None:
        slug = _new_slug(app_name)

    settings = get_settings()
    app_dir = settings.data_dir.resolve() / "apps" / slug / "deployments" / "1"
    created_app_dir = app_dir.parents[1]
    directory_created = False
    try:
        app_dir.mkdir(parents=True, exist_ok=False)
        directory_created = True
        source_path = app_dir / ("index.html" if suffix == ".html" else "app.py")
        source_path.write_bytes(source)
        if requirements_bytes is not None:
            (app_dir / "requirements.txt").write_bytes(requirements_bytes)

        app_record = App(
            owner_id=user.id,
            slug=slug,
            name=app_name,
            kind=kind.value,
        )
        session.add(app_record)
        session.flush()
        session.add(
            Deployment(
                app_id=app_record.id,
                version=1,
                code_path=str(source_path.resolve()),
                has_requirements=requirements_bytes is not None,
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        if directory_created:
            shutil.rmtree(created_app_dir, ignore_errors=True)
        raise

    settings_domain = settings.apps_domain.strip(".")
    port = request.url.port or 8000
    url = f"{request.url.scheme}://{slug}.{settings_domain}:{port}"
    return {
        "id": app_record.id,
        "slug": slug,
        "name": app_record.name,
        "kind": app_record.kind,
        "status": app_record.status,
        "url": url,
        "has_requirements": requirements_bytes is not None,
    }
