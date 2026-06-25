"""Admin web routes for Role management."""

from __future__ import annotations

import logging
from urllib.parse import quote_plus
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.rbac import Permission, Role, RolePermission
from app.schemas.rbac import RoleCreate, RoleUpdate
from app.services.branding_context import load_branding_context
from app.services.rbac import Roles
from app.templates import templates
from app.web.deps import require_web_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/roles", tags=["web-roles"])

PAGE_SIZE = 25


def _commit(db: Session) -> None:
    db.commit()


def _base_context(
    request: Request,
    db: Session,
    auth: dict,
    *,
    title: str,
    page_title: str,
) -> dict:
    branding = load_branding_context(db)
    person = auth["person"]
    return {
        "request": request,
        "title": title,
        "page_title": page_title,
        "current_user": person,
        "brand": branding["brand"],
        "org_branding": branding["org_branding"],
        "brand_mark": branding["brand"].get("mark", "A"),
    }


@router.get("", response_class=HTMLResponse)
def list_roles(
    request: Request,
    page: int = 1,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    """List roles with pagination."""
    page = max(1, page)
    offset = (page - 1) * PAGE_SIZE

    query = select(Role).where(Role.is_active.is_(True)).order_by(Role.name.asc())
    total = (
        db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
        or 0
    )
    items = list(db.scalars(query.limit(PAGE_SIZE).offset(offset)).all())
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    ctx = _base_context(request, db, auth, title="Roles", page_title="Roles")
    ctx.update(
        {
            "roles": items,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "success": request.query_params.get("success"),
            "error": request.query_params.get("error"),
        }
    )
    return templates.TemplateResponse("admin/roles/list.html", ctx)


@router.get("/create", response_class=HTMLResponse)
def create_role_form(
    request: Request,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    """Render the create role form with permission checkboxes."""
    all_permissions = list(
        db.scalars(
            select(Permission)
            .where(Permission.is_active.is_(True))
            .order_by(Permission.key.asc())
        ).all()
    )
    ctx = _base_context(
        request, db, auth, title="Create Role", page_title="Create Role"
    )
    ctx["all_permissions"] = all_permissions
    return templates.TemplateResponse("admin/roles/create.html", ctx)


@router.post("/create", response_model=None)
async def create_role_submit(
    request: Request,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_web_auth),
) -> RedirectResponse | HTMLResponse:
    """Handle role creation with permission assignments."""
    form = await request.form()
    data = dict(form)
    data.pop("csrf_token", None)

    # Extract selected permission IDs from multi-select checkboxes
    permission_ids = form.getlist("permission_ids")

    try:
        payload = RoleCreate(
            name=str(data.get("name", "")),
            description=str(data["description"]) if data.get("description") else None,
            is_active=data.get("is_active") == "on",
        )
        Roles(db).create_with_permissions(
            payload, [str(item) for item in permission_ids]
        )
        _commit(db)
        logger.info("Created role via web: %s", payload.name)
        return RedirectResponse(
            url="/admin/roles?success=Role+created+successfully",
            status_code=302,
        )
    except Exception as exc:
        logger.warning("Failed to create role: %s", exc)
        all_permissions = list(
            db.scalars(
                select(Permission)
                .where(Permission.is_active.is_(True))
                .order_by(Permission.key.asc())
            ).all()
        )
        ctx = _base_context(
            request, db, auth, title="Create Role", page_title="Create Role"
        )
        ctx["error"] = str(exc)
        ctx["form_data"] = data
        ctx["all_permissions"] = all_permissions
        return templates.TemplateResponse("admin/roles/create.html", ctx)


@router.get("/{role_id}/edit", response_class=HTMLResponse)
def edit_role_form(
    request: Request,
    role_id: UUID,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    """Render the edit role form with permission checkboxes."""
    role = Roles(db).get(str(role_id))
    all_permissions = list(
        db.scalars(
            select(Permission)
            .where(Permission.is_active.is_(True))
            .order_by(Permission.key.asc())
        ).all()
    )
    # Get current permission IDs for this role
    current_permission_ids = {
        str(rp.permission_id)
        for rp in db.scalars(
            select(RolePermission).where(RolePermission.role_id == role_id)
        ).all()
    }
    ctx = _base_context(request, db, auth, title="Edit Role", page_title="Edit Role")
    ctx["role"] = role
    ctx["all_permissions"] = all_permissions
    ctx["current_permission_ids"] = current_permission_ids
    return templates.TemplateResponse("admin/roles/edit.html", ctx)


@router.post("/{role_id}/edit", response_model=None)
async def edit_role_submit(
    request: Request,
    role_id: UUID,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_web_auth),
) -> RedirectResponse | HTMLResponse:
    """Handle role edit with permission reassignment."""
    form = await request.form()
    data = dict(form)
    data.pop("csrf_token", None)
    permission_ids = form.getlist("permission_ids")

    try:
        payload = RoleUpdate(
            name=str(data["name"]) if data.get("name") else None,
            description=str(data["description"]) if data.get("description") else None,
            is_active="is_active" in data,
        )
        Roles(db).update_with_permissions(
            str(role_id), payload, [str(item) for item in permission_ids]
        )
        _commit(db)
        logger.info("Updated role via web: %s", role_id)
        return RedirectResponse(
            url="/admin/roles?success=Role+updated+successfully",
            status_code=302,
        )
    except Exception as exc:
        logger.warning("Failed to update role %s: %s", role_id, exc)
        role = db.get(Role, role_id)
        all_permissions = list(
            db.scalars(
                select(Permission)
                .where(Permission.is_active.is_(True))
                .order_by(Permission.key.asc())
            ).all()
        )
        current_permission_ids = {
            str(rp.permission_id)
            for rp in db.scalars(
                select(RolePermission).where(RolePermission.role_id == role_id)
            ).all()
        }
        ctx = _base_context(
            request, db, auth, title="Edit Role", page_title="Edit Role"
        )
        ctx["role"] = role
        ctx["all_permissions"] = all_permissions
        ctx["current_permission_ids"] = current_permission_ids
        ctx["error"] = str(exc)
        return templates.TemplateResponse("admin/roles/edit.html", ctx)


@router.post("/{role_id}/delete", response_model=None)
async def delete_role(
    request: Request,
    role_id: UUID,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_web_auth),
) -> RedirectResponse:
    """Handle role deletion (soft delete via is_active=False)."""
    form = await request.form()
    _ = form.get("csrf_token")

    try:
        Roles(db).delete(str(role_id))
        _commit(db)
        logger.info("Deleted role via web: %s", role_id)
        return RedirectResponse(
            url="/admin/roles?success=Role+deleted+successfully",
            status_code=302,
        )
    except Exception as exc:
        logger.warning("Failed to delete role %s: %s", role_id, exc)
        return RedirectResponse(
            url=f"/admin/roles?error={quote_plus(str(exc))}",
            status_code=302,
        )
