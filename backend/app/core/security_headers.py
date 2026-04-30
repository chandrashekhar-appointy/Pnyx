"""
Security headers middleware.

Applies a baseline set of OWASP-recommended response headers to all backend
responses. Frontend (Next.js) sets its own — see frontend/next.config.js.
"""

import os
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


_DEFAULT_CSP = (
    "default-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)

        # MIME-sniffing protection
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # Clickjacking protection (defense in depth alongside CSP frame-ancestors)
        response.headers.setdefault("X-Frame-Options", "DENY")
        # Limit referer leakage
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # Disable powerful permissions by default (opt back in per-feature)
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(self), geolocation=(), payment=(), usb=()",
        )
        # Force HTTPS in production deployments
        if os.getenv("ENVIRONMENT", "development").lower() == "production":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains; preload",
            )

        # API responses don't render HTML, so a strict CSP is safe — frontend
        # is on a different origin (Vercel) and sets its own CSP.
        if "content-security-policy" not in {k.lower() for k in response.headers.keys()}:
            response.headers["Content-Security-Policy"] = os.getenv(
                "BACKEND_CSP", _DEFAULT_CSP
            )

        return response
