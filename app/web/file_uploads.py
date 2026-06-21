"""Admin web routes for File Upload management."""

from __future__ import annotations

import logging
from urllib.parse import quote_plus
from uuid import UUID

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.file_upload import FileUpload, FileUploadStatus
from app.services.branding_context import load_branding_context
from app.services.file_upload import FileUploadService, read_upload_file_limited
from app.templates import templates
from app.web.deps import require_web_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/file-uploads", tags=["web-file-uploads"])

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
def list_file_uploads(
    request: Request,
    page: int = 1,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    """List file uploads with pagination."""
    page = max(1, page)
    offset = (page - 1) * PAGE_SIZE

    query = (
        select(FileUpload)
        .where(
            FileUpload.is_active.is_(True),
            FileUpload.status == FileUploadStatus.active,
        )
        .order_by(FileUpload.created_at.desc())
    )
    total = (
        db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
        or 0
    )
    items = list(db.scalars(query.limit(PAGE_SIZE).offset(offset)).all())
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    ctx = _base_context(
        request, db, auth, title="File Uploads", page_title="File Uploads"
    )
    ctx.update(
        {
            "uploads": items,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "success": request.query_params.get("success"),
            "error": request.query_params.get("error"),
        }
    )
    return templates.TemplateResponse("admin/file_uploads/list.html", ctx)


@router.get("/upload", response_class=HTMLResponse)
def upload_form(
    request: Request,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    """Render the file upload form."""
    ctx = _base_context(
        request, db, auth, title="Upload File", page_title="Upload File"
    )
    return templates.TemplateResponse("admin/file_uploads/upload.html", ctx)


@router.post("/upload", response_model=None)
async def upload_submit(
    request: Request,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_web_auth),
) -> RedirectResponse | HTMLResponse:
    """Handle file upload form submission."""
    form = await request.form()
    uploaded_file: UploadFile | None = form.get("file")  # type: ignore[assignment]
    category = str(form.get("category", "document"))
    person = auth["person"]

    if not uploaded_file or not uploaded_file.filename:
        ctx = _base_context(
            request, db, auth, title="Upload File", page_title="Upload File"
        )
        ctx["error"] = "Please select a file to upload"
        return templates.TemplateResponse("admin/file_uploads/upload.html", ctx)

    try:
        content = await read_upload_file_limited(uploaded_file)
        svc = FileUploadService(db)
        svc.upload(
            content=content,
            filename=uploaded_file.filename,
            content_type=uploaded_file.content_type or "application/octet-stream",
            uploaded_by=person.id,
            category=category,
        )
        _commit(db)
        logger.info(
            "Uploaded file via web: %s by %s", uploaded_file.filename, person.id
        )
        return RedirectResponse(
            url="/admin/file-uploads?success=File+uploaded+successfully",
            status_code=302,
        )
    except ValueError as exc:
        logger.warning("File upload validation failed: %s", exc)
        ctx = _base_context(
            request, db, auth, title="Upload File", page_title="Upload File"
        )
        ctx["error"] = str(exc)
        return templates.TemplateResponse("admin/file_uploads/upload.html", ctx)
    except Exception as exc:
        logger.exception("File upload failed: %s", exc)
        db.rollback()
        ctx = _base_context(
            request, db, auth, title="Upload File", page_title="Upload File"
        )
        ctx["error"] = str(exc)
        return templates.TemplateResponse("admin/file_uploads/upload.html", ctx)


@router.post("/{file_id}/delete", response_model=None)
async def delete_file_upload(
    request: Request,
    file_id: UUID,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_web_auth),
) -> RedirectResponse:
    """Handle file upload deletion (soft delete)."""
    form = await request.form()
    _ = form.get("csrf_token")

    try:
        svc = FileUploadService(db)
        svc.delete(file_id)
        _commit(db)
        logger.info("Deleted file upload via web: %s", file_id)
        return RedirectResponse(
            url="/admin/file-uploads?success=File+deleted+successfully",
            status_code=302,
        )
    except ValueError as exc:
        logger.warning("Failed to delete file upload %s: %s", file_id, exc)
        return RedirectResponse(
            url=f"/admin/file-uploads?error={quote_plus(str(exc))}",
            status_code=302,
        )
    except Exception as exc:
        logger.exception("Failed to delete file upload %s: %s", file_id, exc)
        db.rollback()
        return RedirectResponse(
            url=f"/admin/file-uploads?error={quote_plus(str(exc))}",
            status_code=302,
        )
