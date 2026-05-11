"""
/health/deep — checks all critical dependencies and returns per-component
status. Use this from external monitoring (UptimeRobot, GCP, k8s readiness)
to detect partial outages that the basic /health endpoint hides.

The endpoint always returns JSON. Status code is 200 when everything is up
or only optional services are degraded, 503 when a critical service is down.
"""

import asyncio
import logging
import os
import time
from typing import Any, Dict

import httpx
from fastapi import APIRouter, Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])


async def _check_database() -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        try:
            from ...db.manager import DatabaseManager
        except (ImportError, ValueError):
            from db.manager import DatabaseManager

        async with DatabaseManager()._get_connection() as conn:
            await conn.fetchval("SELECT 1")
        return {
            "status": "ok",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except Exception as e:
        return {"status": "down", "error": str(e)[:200]}


async def _check_redis() -> Dict[str, Any]:
    url = os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL")
    if not url:
        return {"status": "skipped", "reason": "REDIS_URL not configured"}
    try:
        import redis.asyncio as aioredis  # type: ignore

        client = aioredis.from_url(url, socket_connect_timeout=2.0)
        try:
            await client.ping()
            return {"status": "ok"}
        finally:
            await client.aclose()
    except Exception as e:
        return {"status": "down", "error": str(e)[:200]}


async def _check_gcp_bucket() -> Dict[str, Any]:
    if os.getenv("STORAGE_TYPE", "local").lower() != "gcp":
        return {"status": "skipped", "reason": "STORAGE_TYPE != gcp"}
    try:
        try:
            from ...services.storage import get_gcp_bucket
        except (ImportError, ValueError):
            from services.storage import get_gcp_bucket

        loop = asyncio.get_running_loop()

        def _ping():
            bucket = get_gcp_bucket()
            if not bucket:
                return False
            return bucket.exists()

        ok = await loop.run_in_executor(None, _ping)
        return {"status": "ok" if ok else "down"}
    except Exception as e:
        return {"status": "down", "error": str(e)[:200]}


async def _check_groq() -> Dict[str, Any]:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return {"status": "skipped", "reason": "GROQ_API_KEY not set"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {key}"},
            )
        if resp.status_code == 200:
            return {"status": "ok"}
        if resp.status_code == 401:
            return {"status": "auth_failed"}
        return {"status": "degraded", "http_status": resp.status_code}
    except Exception as e:
        return {"status": "down", "error": str(e)[:200]}


async def _check_elevenlabs() -> Dict[str, Any]:
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        return {"status": "skipped", "reason": "ELEVENLABS_API_KEY not set"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://api.elevenlabs.io/v1/user/subscription",
                headers={"xi-api-key": key},
            )
        if resp.status_code == 200:
            return {"status": "ok"}
        if resp.status_code == 401:
            return {"status": "auth_failed"}
        return {"status": "degraded", "http_status": resp.status_code}
    except Exception as e:
        return {"status": "down", "error": str(e)[:200]}


CRITICAL_COMPONENTS = {"database"}


@router.get("/deep")
async def health_deep(response: Response) -> Dict[str, Any]:
    components_results = await asyncio.gather(
        _check_database(),
        _check_redis(),
        _check_gcp_bucket(),
        _check_groq(),
        _check_elevenlabs(),
        return_exceptions=False,
    )
    components = {
        "database": components_results[0],
        "redis": components_results[1],
        "gcp_bucket": components_results[2],
        "groq": components_results[3],
        "elevenlabs": components_results[4],
    }

    overall = "ok"
    for name, result in components.items():
        status = result.get("status")
        if status in ("down", "auth_failed"):
            if name in CRITICAL_COMPONENTS:
                overall = "down"
                break
            overall = "degraded"

    if overall == "down":
        response.status_code = 503

    return {
        "overall": overall,
        "version": "1.0.0",
        "environment": os.getenv("ENVIRONMENT", "development"),
        "components": components,
    }
