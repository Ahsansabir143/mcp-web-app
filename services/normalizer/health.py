from __future__ import annotations

from fastapi import APIRouter

from services.normalizer.consumer import ConsumerStats

router = APIRouter()

_stats: ConsumerStats | None = None


def set_stats(s: ConsumerStats) -> None:
    global _stats
    _stats = s


@router.get("/health/detail")
async def health_detail() -> dict:
    if _stats is None:
        return {"status": "starting", "consumer": {}}

    return {
        "status": "ok",
        "consumer": {
            "messages_processed": _stats.messages_processed,
            "messages_skipped": _stats.messages_skipped,
            "errors": _stats.errors,
            "uptime_s": round(_stats.uptime_s, 1),
            "last_message_at": _stats.last_message_at,
        },
    }
