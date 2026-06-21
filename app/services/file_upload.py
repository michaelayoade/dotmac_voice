"""File upload service — CRUD + storage integration."""

from __future__ import annotations

import logging
import re
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.file_upload import FileUpload, FileUploadStatus
from app.models.rbac import PersonRole, Role
from app.schemas.common import ListResponse
from app.schemas.file_upload import FileUploadRead
from app.services.common import coerce_uuid
from app.services.exceptions import BadRequestError, NotFoundError
from app.services.storage import StorageBackend, get_storage_backend

logger = logging.getLogger(__name__)

_DANGEROUS_TEXT_PATTERNS = (
    re.compile(rb"(?is)<\s*(?:!doctype|html|head|body|script|iframe|object|embed)\b"),
    re.compile(rb"(?is)\bjavascript\s*:"),
)
_UPLOAD_CHUNK_SIZE = 1024 * 1024


def _allowed_types() -> set[str]:
    return {item.strip() for item in settings.upload_allowed_types.split(",")}


def _sniff_binary_content_type(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content.startswith(b"RIFF") and len(content) >= 12 and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def _is_safe_text(content: bytes) -> bool:
    sample = content[:4096]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return not any(pattern.search(sample) for pattern in _DANGEROUS_TEXT_PATTERNS)


def _validate_content(content: bytes, declared_type: str) -> str:
    allowed = _allowed_types()
    if declared_type not in allowed:
        raise BadRequestError(f"File type '{declared_type}' not allowed")

    sniffed = _sniff_binary_content_type(content)
    if sniffed:
        if sniffed != declared_type:
            raise BadRequestError("Uploaded file content does not match file type")
        return sniffed

    if declared_type in {"text/plain", "text/csv"} and _is_safe_text(content):
        return declared_type

    raise BadRequestError("Could not verify uploaded file type")


async def read_upload_file_limited(
    file: UploadFile,
    *,
    max_size_bytes: int | None = None,
) -> bytes:
    limit = max_size_bytes or settings.upload_max_size_bytes
    content = bytearray()
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > limit:
            max_mb = limit // (1024 * 1024)
            raise BadRequestError(f"File too large. Maximum size: {max_mb}MB")
    return bytes(content)


class FileUploadService:
    """Manages file upload records and storage."""

    def __init__(self, db: Session, storage: StorageBackend | None = None) -> None:
        self.db = db
        self.storage = storage or get_storage_backend()

    def upload(
        self,
        content: bytes,
        filename: str,
        content_type: str,
        uploaded_by: UUID | None = None,
        category: str = "document",
        entity_type: str | None = None,
        entity_id: str | None = None,
        metadata_: dict | None = None,
    ) -> FileUpload:
        """Upload a file and create a database record."""
        if len(content) > settings.upload_max_size_bytes:
            max_mb = settings.upload_max_size_bytes // (1024 * 1024)
            raise BadRequestError(f"File too large. Maximum size: {max_mb}MB")
        verified_content_type = _validate_content(content, content_type)

        storage_key = self.storage.save(content, filename, verified_content_type)
        url = self.storage.get_url(storage_key)

        record = FileUpload(
            uploaded_by=uploaded_by,
            original_filename=filename,
            content_type=verified_content_type,
            file_size=len(content),
            storage_backend=settings.storage_backend,
            storage_key=storage_key,
            url=url,
            category=category,
            entity_type=entity_type,
            entity_id=entity_id,
            status=FileUploadStatus.active,
            metadata_=metadata_,
        )
        self.db.add(record)
        self.db.flush()
        logger.info("Uploaded file: %s (id=%s)", filename, record.id)
        return record

    def upload_for_actor(
        self,
        *,
        actor_id: UUID,
        content: bytes,
        filename: str,
        content_type: str,
        category: str = "document",
        entity_type: str | None = None,
        entity_id: str | None = None,
    ) -> FileUpload:
        return self.upload(
            content=content,
            filename=filename,
            content_type=content_type,
            uploaded_by=actor_id,
            category=category,
            entity_type=entity_type,
            entity_id=entity_id,
        )

    def get_by_id(self, file_id: UUID) -> FileUpload | None:
        """Get a file upload by ID."""
        return self.db.get(FileUpload, file_id)

    def _is_admin(self, person_id: UUID) -> bool:
        return (
            self.db.scalars(
                select(PersonRole)
                .join(Role, PersonRole.role_id == Role.id)
                .where(PersonRole.person_id == person_id)
                .where(Role.name == "admin")
                .where(Role.is_active.is_(True))
                .limit(1)
            ).first()
            is not None
        )

    def _visible_upload_or_404(self, file_id: UUID, actor_id: UUID) -> FileUpload:
        record = self.get_by_id(file_id)
        if (
            not record
            or not record.is_active
            or (not self._is_admin(actor_id) and record.uploaded_by != actor_id)
        ):
            raise NotFoundError("File upload not found")
        return record

    def get_for_actor(self, file_id: UUID, actor_id: UUID) -> FileUpload:
        return self._visible_upload_or_404(file_id, actor_id)

    def list_response_for_actor(
        self,
        *,
        actor_id: UUID,
        category: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ListResponse[FileUploadRead]:
        uploaded_by = None if self._is_admin(actor_id) else actor_id
        items = self.list_uploads(
            uploaded_by=uploaded_by,
            category=category,
            entity_type=entity_type,
            entity_id=entity_id,
            limit=limit,
            offset=offset,
        )
        total = self.count(
            uploaded_by=uploaded_by,
            category=category,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        return ListResponse(
            items=[FileUploadRead.model_validate(i) for i in items],
            count=len(items),
            limit=limit,
            offset=offset,
            total=total,
        )

    def list_uploads(
        self,
        *,
        uploaded_by: UUID | None = None,
        category: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[FileUpload]:
        """List file uploads with optional filters."""
        stmt = select(FileUpload).where(
            FileUpload.is_active.is_(True),
            FileUpload.status == FileUploadStatus.active,
        )
        if uploaded_by is not None:
            stmt = stmt.where(FileUpload.uploaded_by == uploaded_by)
        if category is not None:
            stmt = stmt.where(FileUpload.category == category)
        if entity_type is not None:
            stmt = stmt.where(FileUpload.entity_type == entity_type)
        if entity_id is not None:
            stmt = stmt.where(FileUpload.entity_id == entity_id)
        stmt = stmt.order_by(FileUpload.created_at.desc()).limit(limit).offset(offset)
        return list(self.db.scalars(stmt).all())

    def count(
        self,
        *,
        uploaded_by: UUID | None = None,
        category: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
    ) -> int:
        """Count active file uploads."""
        from sqlalchemy import func

        stmt = (
            select(func.count())
            .select_from(FileUpload)
            .where(
                FileUpload.is_active.is_(True),
                FileUpload.status == FileUploadStatus.active,
            )
        )
        if uploaded_by is not None:
            stmt = stmt.where(FileUpload.uploaded_by == uploaded_by)
        if category is not None:
            stmt = stmt.where(FileUpload.category == category)
        if entity_type is not None:
            stmt = stmt.where(FileUpload.entity_type == entity_type)
        if entity_id is not None:
            stmt = stmt.where(FileUpload.entity_id == entity_id)
        result = self.db.execute(stmt).scalar()
        return result or 0

    def delete(self, file_id: UUID) -> None:
        """Soft-delete a file upload and remove from storage."""
        record = self.db.get(FileUpload, file_id)
        if not record:
            raise NotFoundError("File upload not found")
        try:
            self.storage.delete(record.storage_key)
        except Exception:
            logger.exception(
                "Failed to delete file from storage: %s", record.storage_key
            )
        record.status = FileUploadStatus.deleted
        record.is_active = False
        self.db.flush()
        logger.info("Deleted file upload: %s", file_id)

    def delete_for_actor(self, file_id: UUID, actor_id: UUID) -> None:
        self._visible_upload_or_404(file_id, actor_id)
        self.delete(file_id)


def current_person_id(auth: dict) -> UUID:
    person_id = coerce_uuid(auth["person_id"])
    if person_id is None:
        raise NotFoundError("User not found")
    return person_id
