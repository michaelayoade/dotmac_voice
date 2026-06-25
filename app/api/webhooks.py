"""Webhook endpoint registration and management API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.webhook import WebhookEndpoint
from app.services.ingress_auth import require_ingress

router = APIRouter(
    prefix="/webhooks",
    tags=["webhooks"],
    dependencies=[Depends(require_ingress)],
)


def _commit(db: Session) -> None:
    db.commit()


# ─── Pydantic schemas ────────────────────────────────────────────────────────


class WebhookEndpointCreate(BaseModel):
    url: str
    secret: str
    event_types: list[str]
    active: bool = True


class WebhookEndpointRead(BaseModel):
    id: uuid.UUID
    url: str
    event_types: list[str]
    active: bool

    model_config = {"from_attributes": True}


# ─── Routes ──────────────────────────────────────────────────────────────────


@router.post("/endpoints", response_model=WebhookEndpointRead, status_code=201)
def create_endpoint(
    body: WebhookEndpointCreate,
    db: Session = Depends(get_db),
) -> WebhookEndpointRead:
    """Register a new webhook endpoint."""
    ep = WebhookEndpoint(
        url=body.url,
        secret=body.secret,
        event_types=body.event_types,
        active=body.active,
    )
    db.add(ep)
    _commit(db)
    db.refresh(ep)
    return WebhookEndpointRead.model_validate(ep)


@router.get("/endpoints", response_model=list[WebhookEndpointRead])
def list_endpoints(db: Session = Depends(get_db)) -> list[WebhookEndpointRead]:
    """List all registered webhook endpoints."""
    rows = list(db.scalars(select(WebhookEndpoint)).all())
    return [WebhookEndpointRead.model_validate(row) for row in rows]


@router.delete("/endpoints/{endpoint_id}", status_code=204, response_model=None)
def delete_endpoint(
    endpoint_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> None:
    """Delete a registered webhook endpoint."""
    ep = db.get(WebhookEndpoint, endpoint_id)
    if ep is None:
        raise HTTPException(status_code=404, detail="endpoint not found")
    db.delete(ep)
    _commit(db)
