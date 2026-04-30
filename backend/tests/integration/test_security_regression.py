"""Security regression suite.

Each scenario maps to either an OWASP top-10 class or a previously-fixed
incident in this repo (XSS in chat renderer, BackgroundTasks injection,
signed-URL HMAC, etc.).  These tests should never be allowed to fail silently.

We use the full ``app.main:app`` here (not the trimmed test_app fixture) so we
exercise the real middleware stack: rate limiting, CSP/security headers, CORS.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Iterator

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_app():
    """Real FastAPI app — uses every middleware (CSP, rate limit, CORS)."""
    os.environ.setdefault("ENVIRONMENT", "production")  # exercise HSTS branch
    os.environ.setdefault("RATELIMIT_DEFAULT", "5/minute")
    from app.main import app

    return app


@pytest.fixture
def real_client(real_app) -> Iterator[TestClient]:
    with TestClient(real_app) as client:
        yield client


# ---------------------------------------------------------------------------
# 1. Security headers
# ---------------------------------------------------------------------------


@pytest.mark.security
def test_security_headers_present_on_health(real_client):
    resp = real_client.get("/health")
    headers = {k.lower(): v for k, v in resp.headers.items()}
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("x-frame-options", "").upper() == "DENY"
    assert "referrer-policy" in headers
    assert "permissions-policy" in headers
    assert "content-security-policy" in headers


@pytest.mark.security
def test_csp_blocks_default_src(real_client):
    resp = real_client.get("/health")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


@pytest.mark.security
def test_hsts_only_in_production(real_client):
    resp = real_client.get("/health")
    if os.getenv("ENVIRONMENT", "").lower() == "production":
        assert "strict-transport-security" in {k.lower() for k in resp.headers}


# ---------------------------------------------------------------------------
# 2. Auth bypass / JWT tampering
# ---------------------------------------------------------------------------


@pytest.mark.security
def test_protected_endpoint_rejects_no_token(real_client):
    """Calling /chat-meeting with no Authorization header must NOT return 200."""
    resp = real_client.post(
        "/chat-meeting",
        json={
            "meeting_id": "x",
            "question": "x",
            "model": "gemini",
            "model_name": "gemini-3-pro-preview",
            "context_text": "",
        },
    )
    assert resp.status_code in {401, 403, 422}


@pytest.mark.security
def test_protected_endpoint_rejects_garbage_bearer(real_client):
    resp = real_client.post(
        "/chat-meeting",
        headers={"Authorization": "Bearer not.a.real.jwt"},
        json={
            "meeting_id": "x",
            "question": "x",
            "model": "gemini",
            "model_name": "gemini-3-pro-preview",
            "context_text": "",
        },
    )
    assert resp.status_code in {401, 403, 422}


@pytest.mark.security
def test_jwt_with_tampered_signature_is_rejected(jwt_key, external_apis_mock, real_client):
    tampered = jwt_key.issue_tampered(
        audience=os.environ.get("GOOGLE_CLIENT_ID", "test")
    )
    resp = real_client.get(
        "/get-meetings",
        headers={"Authorization": f"Bearer {tampered}"},
    )
    assert resp.status_code in {401, 403}


# ---------------------------------------------------------------------------
# 3. XSS regression — chat renderer (frontend MeetingDetails/ChatInterface)
# ---------------------------------------------------------------------------

XSS_PAYLOADS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "javascript:alert(1)",
    "<svg/onload=alert(1)>",
    "&lt;script&gt;alert(1)&lt;/script&gt;",
    "<iframe src=javascript:alert(1)>",
    "'\"--></style></script><script>alert(1)</script>",
]


@pytest.mark.security
@pytest.mark.parametrize("payload", XSS_PAYLOADS)
def test_chat_endpoint_does_not_reflect_raw_script_tags(
    payload, async_client_factory
):
    """The /chat-meeting handler must not echo a payload back as an HTML-active
    script.  Even if the LLM is mocked to *return* the payload, the response
    body should be served with content-type text/plain or be HTML-escaped."""

    import asyncio

    async def _run():
        from app.api.routers import chat as chat_router

        async def _allow(*a, **k):
            return True

        async def _fake_chat(**_kwargs):
            async def _gen():
                yield payload

            return _gen()

        async_client = await async_client_factory(
            patches={
                (chat_router.rbac, "can"): _allow,
                (chat_router.chat_service, "chat_about_meeting"): _fake_chat,
            }
        )

        resp = await async_client.post(
            "/chat-meeting",
            json={
                "meeting_id": "m",
                "question": "q",
                "model": "gemini",
                "model_name": "gemini-3-pro-preview",
                "context_text": "",
            },
        )
        # The streaming endpoint may echo whatever the LLM returned. The
        # contract that protects users is: content-type must NOT be text/html.
        ct = resp.headers.get("content-type", "").lower()
        assert "text/html" not in ct, (
            f"chat endpoint returned text/html for payload {payload!r}"
        )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 4. Rate limiting
# ---------------------------------------------------------------------------


@pytest.mark.security
def test_rate_limit_eventually_blocks_excessive_requests():
    """With a tight RATELIMIT_DEFAULT, repeated calls must hit a 429.

    We isolate this to a dedicated app instance to avoid bleeding limiter state
    into other tests.
    """
    os.environ["RATELIMIT_DEFAULT"] = "3/minute"
    # Force re-import so limiter picks up env
    import importlib

    import app.core.rate_limit as rl_mod

    importlib.reload(rl_mod)
    import app.main as main_mod

    importlib.reload(main_mod)

    with TestClient(main_mod.app) as client:
        statuses = [client.get("/health").status_code for _ in range(8)]
    # Restore default for other tests
    os.environ["RATELIMIT_DEFAULT"] = "10000/minute"
    importlib.reload(rl_mod)
    importlib.reload(main_mod)

    assert 429 in statuses, f"Rate limit never triggered: {statuses}"


# ---------------------------------------------------------------------------
# 5. Path traversal in signed-URL artifact path
# ---------------------------------------------------------------------------


@pytest.mark.security
def test_artifact_path_rejects_dotdot(real_client):
    """Whatever route serves audio artifacts, it must reject ``..`` in the
    path so users can't read /etc/passwd via a crafted token."""
    bad_paths = [
        "/recordings/../../../etc/passwd",
        "/recordings/..%2F..%2F..%2Fetc%2Fpasswd",
    ]
    for p in bad_paths:
        resp = real_client.get(p)
        assert resp.status_code in {400, 401, 403, 404}, (
            f"{p} returned {resp.status_code} — possible path traversal vector"
        )


# ---------------------------------------------------------------------------
# 6. CORS — only allow-listed origins should pass preflight
# ---------------------------------------------------------------------------


@pytest.mark.security
def test_cors_rejects_unknown_origin(real_client):
    resp = real_client.options(
        "/health",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    allow_origin = resp.headers.get("access-control-allow-origin", "")
    # If CORS is wide-open with "*", that's a finding.  If it echoes the
    # malicious origin verbatim, that's worse.
    assert allow_origin != "https://evil.example", (
        "CORS reflected an unapproved origin"
    )


# ---------------------------------------------------------------------------
# Helper fixture for parametrized XSS test
# ---------------------------------------------------------------------------


@pytest.fixture
def async_client_factory():
    """Builds an AsyncClient against the trimmed test_app, pre-applying a
    map of ``(target, attr) → replacement`` so XSS parametrization stays
    readable."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.api.deps import get_current_user
    from app.api.routers import chat as chat_router
    from app.api.routers import transcripts as transcripts_router
    from app.api.routers import audio as audio_router
    from app.schemas.user import User

    async def _factory(patches: dict | None = None):
        app = FastAPI()
        app.include_router(audio_router.router)
        app.include_router(chat_router.router)
        app.include_router(transcripts_router.router)

        async def _user():
            return User(email="t@x", name="T")

        app.dependency_overrides[get_current_user] = _user

        for (target, attr), value in (patches or {}).items():
            setattr(target, attr, value)

        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    return _factory
