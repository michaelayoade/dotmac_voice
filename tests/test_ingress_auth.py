from fastapi import Depends
from app.services.ingress_auth import require_ingress


def _mount(app):
    from fastapi import APIRouter
    r = APIRouter()
    @r.get("/_ingress_ping")
    def ping(_=Depends(require_ingress)): return {"ok": True}
    app.include_router(r)


def test_missing_key_401(client):
    _mount(client.app)
    assert client.get("/_ingress_ping").status_code == 401


def test_valid_key_200(client):
    _mount(client.app)
    assert client.get("/_ingress_ping", headers={"X-API-Key": "test-ingress-key"}).status_code == 200
