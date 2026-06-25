from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditActorType, AuditEvent
from app.schemas.audit import AuditEventCreate
from app.services.common import coerce_uuid
from app.services.exceptions import BadRequestError, NotFoundError
from app.services.query_utils import apply_ordering, apply_pagination
from app.services.response import ListResponseMixin


class AuditEvents(ListResponseMixin):
    @staticmethod
    def parse_actor_type(value: str | None) -> AuditActorType | None:
        if value is None:
            return None
        try:
            return AuditActorType(value)
        except ValueError as exc:
            allowed = ", ".join(sorted(a.value for a in AuditActorType))
            raise BadRequestError(f"Invalid actor_type. Allowed: {allowed}") from exc

    @staticmethod
    def create(db: Session, payload: AuditEventCreate):
        data = payload.model_dump()
        if payload.occurred_at is None:
            data.pop("occurred_at", None)
        event = AuditEvent(**data)
        db.add(event)
        db.flush()
        db.refresh(event)
        return event

    @staticmethod
    def get(db: Session, event_id: str):
        event = db.get(AuditEvent, coerce_uuid(event_id))
        if not event:
            raise NotFoundError("Audit event not found")
        return event

    @staticmethod
    def list(
        db: Session,
        actor_id: str | None,
        actor_type: AuditActorType | None,
        action: str | None,
        entity_type: str | None,
        request_id: str | None,
        is_success: bool | None,
        status_code: int | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = select(AuditEvent)
        if actor_id:
            query = query.where(AuditEvent.actor_id == actor_id)
        if actor_type:
            query = query.where(AuditEvent.actor_type == actor_type)
        if action:
            query = query.where(AuditEvent.action == action)
        if entity_type:
            query = query.where(AuditEvent.entity_type == entity_type)
        if request_id:
            query = query.where(AuditEvent.request_id == request_id)
        if is_success is not None:
            query = query.where(AuditEvent.is_success == is_success)
        if status_code is not None:
            query = query.where(AuditEvent.status_code == status_code)
        if is_active is None:
            query = query.where(AuditEvent.is_active.is_(True))
        else:
            query = query.where(AuditEvent.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "occurred_at": AuditEvent.occurred_at,
                "action": AuditEvent.action,
                "entity_type": AuditEvent.entity_type,
                "status_code": AuditEvent.status_code,
            },
        )
        return db.scalars(apply_pagination(query, limit, offset)).all()

    @staticmethod
    def log_request(db: Session, request: Request, response: Response):
        actor_type = request.headers.get("x-actor-type", AuditActorType.system.value)
        actor_id = request.headers.get("x-actor-id")
        request_id = request.headers.get("x-request-id")
        entity_id = request.headers.get("x-entity-id")
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        try:
            resolved_actor_type = AuditActorType(actor_type)
        except ValueError:
            resolved_actor_type = AuditActorType.system
        try:
            query_params = dict(request.query_params)
        except KeyError:
            query_params = {}
        payload = AuditEventCreate(
            actor_type=resolved_actor_type,
            actor_id=actor_id,
            action=request.method,
            entity_type=request.url.path,
            entity_id=entity_id,
            status_code=response.status_code,
            is_success=response.status_code < 400,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
            metadata_={
                "path": request.url.path,
                "query": query_params,
            },
        )
        event = AuditEvent(**payload.model_dump())
        db.add(event)
        db.flush()

    @staticmethod
    def delete(db: Session, event_id: str):
        event = db.get(AuditEvent, coerce_uuid(event_id))
        if not event:
            raise NotFoundError("Audit event not found")
        event.is_active = False
        db.flush()


audit_events = AuditEvents()
