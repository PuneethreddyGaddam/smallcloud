"""Server-rendered dashboard for the local M1 workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import App

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parents[1] / "templates")
)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    """List deployed apps with their status and local URL."""
    settings = get_settings()
    domain = (settings.apps_domain or "localhost").strip(".")
    port = request.url.port or 8000

    app_rows = _list_apps(session, request, domain, port)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"apps": app_rows},
    )


@router.get("/dashboard/apps", response_class=HTMLResponse)
async def dashboard_apps(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    """Return the app-list fragment for HTMX refreshes."""
    settings = get_settings()
    domain = (settings.apps_domain or "localhost").strip(".")
    port = request.url.port or 8000
    app_rows = _list_apps(session, request, domain, port)

    return templates.TemplateResponse(
        request=request,
        name="app_list.html",
        context={"apps": app_rows},
    )


def _list_apps(
    session: Session,
    request: Request,
    domain: str,
    port: int,
) -> list[dict[str, str]]:
    """Load app summary rows and construct their local links."""
    apps = session.scalars(
        select(App).order_by(App.created_at.desc(), App.id.desc())
    ).all()
    return [
        {
            "name": app.name,
            "kind": app.kind,
            "status": app.status,
            "url": f"{request.url.scheme}://{app.slug}.{domain}:{port}",
        }
        for app in apps
    ]
