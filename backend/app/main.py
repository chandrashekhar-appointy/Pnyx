import logging
import os
import time
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from typing import Optional
from dotenv import load_dotenv
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from urllib.parse import quote

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d - %(funcName)s()] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
calendar_scheduler = None
audio_reconciler = None

# Initialize Sentry early (no-op if SENTRY_DSN unset)
_SENTRY_DSN = os.getenv("SENTRY_DSN")
if _SENTRY_DSN:
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            environment=os.getenv("ENVIRONMENT", "development"),
            release=os.getenv("RELEASE_VERSION"),
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05")),
            send_default_pii=False,
        )
        logger.info("[Sentry] Initialized for env=%s", os.getenv("ENVIRONMENT"))
    except Exception as e:  # noqa: BLE001
        logger.warning("[Sentry] Init failed: %s", e)

# Import Routers
try:
    from app.api.routers import (
        meetings,
        transcripts,
        chat,
        audio,
        diarization,
        settings,
        calendar,
        admin,
        feedback,
        sharing,
        credits,
        payments,
        bot,
        health_deep,
    )
except ImportError:
    from api.routers import (
        meetings,
        transcripts,
        chat,
        audio,
        diarization,
        settings,
        calendar,
        admin,
        feedback,
        sharing,
        credits,
        payments,
        bot,
        health_deep,
    )

app = FastAPI(
    title="Meeting Summarizer API",
    description="API for processing and summarizing meeting transcripts",
    version="1.0.0",
)

# --- Rate limiting -----------------------------------------------------------
try:
    from app.core.rate_limit import limiter, rate_limit_exceeded_handler
except ImportError:
    from core.rate_limit import limiter, rate_limit_exceeded_handler

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# --- Security headers --------------------------------------------------------
try:
    from app.core.security_headers import SecurityHeadersMiddleware
except ImportError:
    from core.security_headers import SecurityHeadersMiddleware

app.add_middleware(SecurityHeadersMiddleware)

# --- CORS --------------------------------------------------------------------
# Origins are env-driven so prod doesn't ship localhost in the allowlist.
_ENV = os.getenv("ENVIRONMENT", "development").lower()
_default_origins = (
    "http://localhost:3118,http://localhost:3000"
    if _ENV != "production"
    else "https://pnyxx.vercel.app,https://meet.quexio.com"
)
origins = [
    o.strip()
    for o in os.getenv("CORS_ALLOWED_ORIGINS", _default_origins).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Requested-With",
        "X-CSRF-Token",
        "Accept",
        "Origin",
    ],
    expose_headers=["X-Request-Id", "Retry-After"],
    max_age=3600,
)
logger.info("[CORS] Allowed origins: %s", origins)

# Global Request Logging Middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    logger.info(
        f"🌐 [REQUEST] {request.method} {request.url.path} - "
        f"Status: {response.status_code} - Duration: {duration:.3f}s"
    )
    return response

# Include Routers
app.include_router(meetings.router, tags=["Meetings"])
app.include_router(transcripts.router, tags=["Transcripts"])
app.include_router(chat.router, tags=["Chat"])
app.include_router(audio.router, tags=["Audio"])
app.include_router(diarization.router, tags=["Diarization"])
app.include_router(settings.router, tags=["Settings"])
app.include_router(calendar.router, tags=["Calendar"])
app.include_router(admin.router, tags=["Admin"])
app.include_router(feedback.router, prefix="/feedback", tags=["Feedback"])
app.include_router(sharing.router)
app.include_router(credits.router)
app.include_router(payments.router)
app.include_router(bot.router)
app.include_router(health_deep.router)


@app.on_event("startup")
async def startup_event():
    # Initialize database connection pool
    try:
        from app.db.manager import DatabaseManager
    except ImportError:
        from db.manager import DatabaseManager

    db_url = os.getenv("DATABASE_URL")
    if db_url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(db_url)
            logger.info(f"🎯 DATABASE_URL check: scheme={parsed.scheme}, host={parsed.hostname}, port={parsed.port}")
        except Exception as e:
            logger.warning(f"⚠️ Could not parse DATABASE_URL for debug: {e}")
        await DatabaseManager.init_pool(db_url)

    # Validate GCP bucket exists at startup so a name typo fails loudly with a
    # clear log line rather than silently 404'ing every recording download.
    if os.getenv("STORAGE_TYPE", "local").lower() == "gcp":
        try:
            try:
                from app.services.storage import get_gcp_bucket
            except ImportError:
                from services.storage import get_gcp_bucket
            import asyncio as _asyncio

            bucket = await _asyncio.get_running_loop().run_in_executor(None, get_gcp_bucket)
            if not bucket:
                logger.error("[Storage] GCP enabled but bucket client could not initialize")
            else:
                exists = await _asyncio.get_running_loop().run_in_executor(None, bucket.exists)
                if exists:
                    logger.info("[Storage] GCP bucket OK: %s", bucket.name)
                else:
                    logger.error(
                        "[Storage] Configured GCP_BUCKET_NAME=%s does NOT exist or is not "
                        "accessible by the service account. Recording features will fail.",
                        os.getenv("GCP_BUCKET_NAME"),
                    )
        except Exception as e:  # noqa: BLE001
            logger.error("[Storage] Bucket startup probe failed: %s", e)

    global calendar_scheduler, audio_reconciler
    try:
        from app.services.calendar.reminder_scheduler import CalendarReminderScheduler
    except ImportError:
        from services.calendar.reminder_scheduler import CalendarReminderScheduler

    if os.getenv("CALENDAR_REMINDER_AUTOMATION_ENABLED", "true").lower() != "true":
        logger.info("[CalendarReminder] Automation disabled by env")
        return

    calendar_scheduler = CalendarReminderScheduler()
    calendar_scheduler.start()
    logger.info("[CalendarReminder] Automation worker initialized")

    if os.getenv("AUDIO_SESSION_RECONCILER_ENABLED", "true").lower() == "true":
        try:
            from app.services.audio.session_reconciler import AudioSessionReconciler
        except ImportError:
            from services.audio.session_reconciler import AudioSessionReconciler
        audio_reconciler = AudioSessionReconciler()
        audio_reconciler.start()
        logger.info("[AudioReconciler] Session reconciler initialized")


@app.on_event("shutdown")
async def shutdown_event():
    global calendar_scheduler, audio_reconciler
    if calendar_scheduler:
        await calendar_scheduler.stop()
        calendar_scheduler = None
    if audio_reconciler:
        await audio_reconciler.stop()
        audio_reconciler = None

    # Close database pool
    try:
        from app.db.manager import DatabaseManager
    except ImportError:
        from db.manager import DatabaseManager
    await DatabaseManager.close_pool()


@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "1.0.0"}


# --- Local audio serving (signed URL) ---------------------------------------
# Used when STORAGE_TYPE=local — the recording-url endpoint mints a HMAC-signed
# token and the URL points here. GCP deployments use native signed URLs and
# never hit this route.
try:
    from app.services.audio.signed_urls import verify_signed_token
except ImportError:
    from services.audio.signed_urls import verify_signed_token

_RECORDINGS_BASE = Path(os.getenv("LOCAL_RECORDINGS_DIR", "./data/recordings")).resolve()


@app.get("/audio/signed/{token}")
async def serve_signed_audio(token: str, download: Optional[str] = None):
    decoded = verify_signed_token(token)
    if not decoded:
        raise HTTPException(status_code=403, detail="Invalid or expired token")
    _meeting_id, rel_path = decoded

    # Resolve and confirm the file is inside the recordings root (no traversal).
    target = (_RECORDINGS_BASE / rel_path).resolve()
    try:
        target.relative_to(_RECORDINGS_BASE)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path outside recordings root")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Recording not found")

    headers = {}
    if download:
        # RFC 6266 — escape filename for safety
        headers["Content-Disposition"] = f'attachment; filename="{quote(download)}"'

    return FileResponse(target, headers=headers)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5167, reload=True)
