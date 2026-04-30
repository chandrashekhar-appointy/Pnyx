"""
Rate limiting via slowapi.

Keys requests by authenticated user (Bearer-token email-hash) when present,
falling back to client IP (with X-Forwarded-For awareness for nginx).

Default policy is permissive; expensive routes opt into tighter limits with
@limiter.limit("...") decorators.
"""

import hashlib
import logging
import os
from typing import Optional

from fastapi import Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    """Resolve client IP behind nginx. Trust XFF only if TRUST_PROXY=true."""
    if os.getenv("TRUST_PROXY", "true").lower() == "true":
        xff = request.headers.get("x-forwarded-for")
        if xff:
            # Leftmost is the original client per nginx convention
            return xff.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
    return get_remote_address(request)


def rate_limit_key(request: Request) -> str:
    """
    Per-user key when an Authorization Bearer token is present, otherwise
    per-IP. We hash the token (not store it) so logs don't leak it.
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
        if token:
            digest = hashlib.sha256(token.encode("utf-8", errors="ignore")).hexdigest()[:16]
            return f"u:{digest}"
    return f"ip:{_client_ip(request)}"


# Global default — generous, primarily anti-DDoS / anti-runaway.
# Tighter per-route limits are applied as decorators on expensive endpoints.
DEFAULT_LIMITS = os.getenv("RATELIMIT_DEFAULT", "300/minute").split(";")

limiter = Limiter(
    key_func=rate_limit_key,
    default_limits=DEFAULT_LIMITS,
    headers_enabled=True,
    storage_uri=os.getenv("RATELIMIT_STORAGE", "memory://"),
)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return JSON 429 with a Retry-After header."""
    response = JSONResponse(
        status_code=429,
        content={
            "detail": "Too many requests. Please slow down.",
            "limit": str(exc.detail),
        },
    )
    response.headers["Retry-After"] = "60"
    return response
