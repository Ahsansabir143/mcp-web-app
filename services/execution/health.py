from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()

_consumer = None
_recon_consumer = None
_recon_loop = None


def set_consumer(consumer) -> None:
    global _consumer
    _consumer = consumer


def set_recon_components(recon_consumer, recon_loop) -> None:
    global _recon_consumer, _recon_loop
    _recon_consumer = recon_consumer
    _recon_loop = recon_loop


@router.get("/health")
async def health():
    return {"status": "ok", "service": "execution"}


@router.get("/health/detail")
async def health_detail():
    if _consumer is None:
        return {"status": "starting", "service": "execution"}

    detail = {
        "status": "ok",
        "service": "execution",
        "adapter": _consumer._adapter.adapter_name(),
        "jobs_processed": _consumer.jobs_processed,
        "jobs_blocked": _consumer.jobs_blocked,
        "reconciliation": {
            "fills_processed": _recon_consumer.fills_processed if _recon_consumer else 0,
            "orphans_seen": _recon_consumer.orphans_seen if _recon_consumer else 0,
            "total_scans": _recon_loop.total_scans if _recon_loop else 0,
            "total_stale_detected": _recon_loop.total_stale_detected if _recon_loop else 0,
        },
    }
    return detail
