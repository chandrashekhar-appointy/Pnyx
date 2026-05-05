"""Root pytest configuration.

Backwards-compatible: the existing ``test_app``, ``async_client``, ``client``,
and ``test_user`` fixtures behave exactly as before.  New fixtures are added
for: external-API mocking (respx), real-DB integration (testcontainers),
local filesystem storage, eager Celery, and Google JWT helpers.
"""

import sys
import os
from pathlib import Path
from typing import AsyncIterator

import pytest
from fastapi import FastAPI

# Global Redis mock using fakeredis — must happen BEFORE app imports
import fakeredis.aioredis
import redis.asyncio
_fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
redis.asyncio.from_url = lambda *args, **kwargs: _fake_redis
redis.asyncio.Redis = lambda *args, **kwargs: _fake_redis

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# ---------------------------------------------------------------------------
# Process-wide test environment
# ---------------------------------------------------------------------------
from dotenv import load_dotenv
load_dotenv(BACKEND_ROOT / ".env")
os.environ.setdefault(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/meeting_copilot"
)
os.environ.setdefault("CALENDAR_REMINDER_AUTOMATION_ENABLED", "false")
os.environ.setdefault("AUDIO_SESSION_RECONCILER_ENABLED", "false")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client-id.apps.googleusercontent.com")
os.environ.setdefault("ALLOWED_DOMAINS", "")
os.environ.setdefault("ADMIN_EMAILS", "admin@example.com")
os.environ.setdefault("RATELIMIT_DEFAULT", "10000/minute")  # do not throttle test runs
os.environ.setdefault("STORAGE_TYPE", "local")

from app.api.deps import get_current_user  # noqa: E402
from app.api.routers import audio as audio_router  # noqa: E402
from app.api.routers import chat as chat_router  # noqa: E402
from app.api.routers import transcripts as transcripts_router  # noqa: E402
from app.schemas.user import User  # noqa: E402

from tests.fixtures.audio import (  # noqa: E402
    silence_pcm,
    speech_like_pcm,
    speech_then_silence,
)
from tests.fixtures.celery_helpers import configure_celery_eager  # noqa: E402
from tests.fixtures.database import (  # noqa: E402
    EphemeralPostgres,
    integration_db_enabled,
    truncate_all,
)
from tests.fixtures.external_apis import ExternalApiMocks  # noqa: E402
from tests.fixtures.jwt_helpers import get_test_key  # noqa: E402
from tests.fixtures.storage import configure_local_storage  # noqa: E402

# Eager Celery for the entire test session — applied at import time so any
# .delay() invocation during tests runs inline.
configure_celery_eager()


# ---------------------------------------------------------------------------
# Pytest CLI / markers
# ---------------------------------------------------------------------------


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: requires real Postgres/Redis (set INTEGRATION_DB=1)",
    )
    config.addinivalue_line(
        "markers", "load: long-running performance/load test"
    )
    config.addinivalue_line(
        "markers", "llm_eval: hits real LLM providers; nightly only"
    )
    config.addinivalue_line(
        "markers", "security: security regression test"
    )
    config.addinivalue_line(
        "markers", "chaos: failure-mode / fault-injection test"
    )
    config.addinivalue_line(
        "markers", "contract: contract / schema-drift test"
    )


# ---------------------------------------------------------------------------
# Existing fixtures (preserved verbatim) — keep changes additive
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_overrides():
    yield


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
def test_user() -> User:
    return User(email="test@appointy.com", name="Test User")


@pytest.fixture
def test_app(test_user: User):
    app = FastAPI()
    app.include_router(audio_router.router)
    app.include_router(chat_router.router)
    app.include_router(transcripts_router.router)

    @app.get("/health")
    async def _health():
        return {"status": "ok"}

    async def _fake_current_user():
        return test_user

    app.dependency_overrides[get_current_user] = _fake_current_user
    return app


@pytest.fixture
async def async_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def client(test_app):
    with TestClient(test_app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# New fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def jwt_key():
    """Fresh RSA keypair plus a JWKS payload — bound to the mocked Google JWKS."""
    return get_test_key()


@pytest.fixture
def issued_token(jwt_key) -> str:
    """A valid Google-shaped JWT that ``verify_google_token`` will accept once
    the JWKS endpoint is mocked via ``external_apis_mock``."""
    return jwt_key.issue(audience=os.environ["GOOGLE_CLIENT_ID"])


@pytest.fixture
def external_apis_mock():
    """respx-based mock for every outbound HTTP call.

    Yields the wrapper so individual tests can override specific routes for
    failure-mode coverage.  ``assert_all_called=False`` lets tests run without
    triggering every external dep — most tests only need a subset.
    """
    import respx

    with respx.mock(assert_all_called=False, assert_all_mocked=False) as router:
        mocks = ExternalApiMocks(router).install_defaults()
        yield mocks


@pytest.fixture
def temp_storage(tmp_path):
    """Per-test storage root.  Tests that hit StorageService go to disk under
    tmp_path instead of GCP."""
    configure_local_storage(tmp_path / "recordings")
    yield tmp_path / "recordings"


@pytest.fixture
def silence_chunk() -> bytes:
    return silence_pcm(0.5)


@pytest.fixture
def speech_chunk() -> bytes:
    return speech_like_pcm(1.0)


@pytest.fixture
def speech_then_silence_chunk() -> bytes:
    return speech_then_silence(1.0, 0.5)


# ---------------------------------------------------------------------------
# Real-DB fixture (opt-in via INTEGRATION_DB=1)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
async def real_db() -> AsyncIterator[str]:
    """Ephemeral Postgres for tests that need real SQL.

    Skips unless ``INTEGRATION_DB=1`` to keep the default suite fast and
    Docker-less.
    """
    if not integration_db_enabled():
        pytest.skip(
            "Set INTEGRATION_DB=1 to run real-database integration tests "
            "(spawns a testcontainers Postgres)."
        )

    pg = EphemeralPostgres()
    dsn = await pg.start()
    os.environ["DATABASE_URL"] = dsn
    try:
        yield dsn
    finally:
        await pg.stop()


@pytest.fixture
async def clean_db(real_db: str) -> AsyncIterator[str]:
    """Truncate every table before each test so tests are order-independent."""
    await truncate_all(real_db)
    yield real_db
