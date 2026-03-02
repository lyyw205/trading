"""Prometheus metrics endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.dependencies import require_admin

router = APIRouter(tags=["monitoring"])


@router.get("/metrics")
async def metrics(_admin: dict = Depends(require_admin)):
    """Expose Prometheus metrics (admin only)."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
