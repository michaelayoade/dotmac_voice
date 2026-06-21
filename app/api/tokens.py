from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.services import tokens as token_service
from app.services.ingress_auth import require_ingress

router = APIRouter(prefix="/tokens", tags=["tokens"], dependencies=[Depends(require_ingress)])


class TokenRequest(BaseModel):
    subject: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    ttl_seconds: int = 60


@router.post("", status_code=status.HTTP_201_CREATED)
def create_token(payload: TokenRequest):
    return token_service.mint_token(payload.subject, payload.scope, payload.ttl_seconds)
