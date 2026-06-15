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
bot_reconciler = None

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
        # diarization,  # v1: disabled — not on core journey, re-enable in v2
        settings,
        calendar,
        admin,
        analytics,
        feedback,
        # sharing,  # v1: Share Notes disabled — re-enable to restore sharing
        credits,
        # payments,  # v1: disabled — Razorpay not configured, re-enable when billing is needed
        bot,
        health_deep,
    )
except ImportError:
    from api.routers import (
        meetings,
        transcripts,
        chat,
        audio,
        # diarization,  # v1: disabled — not on core journey, re-enable in v2
        settings,
        calendar,
        admin,
        analytics,
        feedback,
        # sharing,  # v1: Share Notes disabled — re-enable to restore sharing
        credits,
        # payments,  # v1: disabled — Razorpay not configured, re-enable when billing is needed
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
# app.include_router(diarization.router, tags=["Diarization"])  # v1: disabled
app.include_router(settings.router, tags=["Settings"])
app.include_router(calendar.router, tags=["Calendar"])
app.include_router(admin.router, tags=["Admin"])
app.include_router(analytics.router, tags=["Analytics"])
app.include_router(feedback.router, prefix="/feedback", tags=["Feedback"])
# app.include_router(sharing.router)  # v1: Share Notes disabled (kept dormant)
app.include_router(credits.router)
# app.include_router(payments.router)  # v1: disabled
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

    global calendar_scheduler, audio_reconciler, bot_reconciler

    # Recall.ai bot watchdog — recovers bots stuck due to missed webhooks so
    # they never sit in a meeting indefinitely.
    if os.getenv("RECALL_BOT_RECONCILER_ENABLED", "true").lower() == "true":
        try:
            try:
                from app.services.recall.bot_reconciler import BotReconciler
            except ImportError:
                from services.recall.bot_reconciler import BotReconciler
            bot_reconciler = BotReconciler()
            bot_reconciler.start()
            logger.info("[BotReconciler] Bot watchdog initialized")
        except Exception as e:  # noqa: BLE001
            logger.warning("[BotReconciler] Could not start: %s", e)

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

    # Reset any summary_processes rows that were left in a non-terminal state by a
    # previous crash or server restart.  PENDING / finalizing_audio rows older than
    # 15 minutes can never complete now — surface them as failures so the UI stops
    # showing "Generating notes..." indefinitely.
    try:
        from app.db.manager import DatabaseManager
    except ImportError:
        from db.manager import DatabaseManager

    try:
        async with DatabaseManager()._get_connection() as conn:
            updated = await conn.execute(
                """
                UPDATE summary_processes
                SET status      = 'failed',
                    error       = 'Server restarted while notes were being generated. Please regenerate.',
                    end_time    = CURRENT_TIMESTAMP,
                    updated_at  = CURRENT_TIMESTAMP
                WHERE status IN ('PENDING', 'finalizing_audio')
                  AND start_time < NOW() - INTERVAL '15 minutes'
                """,
            )
        if updated and updated != "UPDATE 0":
            logger.warning("[Startup] Reset stale summary_processes rows: %s", updated)
    except Exception as e:
        logger.warning("[Startup] Could not reset stale summary_processes: %s", e)


@app.on_event("shutdown")
async def shutdown_event():
    global calendar_scheduler, audio_reconciler, bot_reconciler
    if calendar_scheduler:
        await calendar_scheduler.stop()
        calendar_scheduler = None
    if audio_reconciler:
        await audio_reconciler.stop()
        audio_reconciler = None
    if bot_reconciler:
        await bot_reconciler.stop()
        bot_reconciler = None

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
# Auth-free audio serving via HMAC-signed tokens.
# Used for BOTH local and GCP storage:
#   - Local: file is read from disk directly.
#   - GCP: when a native GCP signed URL cannot be generated (e.g. Cloud Run
#     workload identity), the recording-url endpoint mints a token here and
#     this handler downloads the bytes from GCS and streams them to the browser.
#   The <audio src="..."> element cannot send Authorization headers, so any
#   fallback URL must be auth-free.
try:
    from app.services.audio.signed_urls import verify_signed_token
    from app.services.storage import StorageService as _StorageService
except ImportError:
    from services.audio.signed_urls import verify_signed_token
    from services.storage import StorageService as _StorageService

_RECORDINGS_BASE = Path(os.getenv("LOCAL_RECORDINGS_DIR", "./data/recordings")).resolve()
_STORAGE_TYPE = os.getenv("STORAGE_TYPE", "local").lower()


@app.get("/audio/signed/{token}")
async def serve_signed_audio(token: str, download: Optional[str] = None):
    decoded = verify_signed_token(token)
    if not decoded:
        raise HTTPException(status_code=403, detail="Invalid or expired token")
    _meeting_id, rel_path = decoded

    headers: dict = {}
    if download:
        # RFC 6266 — escape filename for safety
        headers["Content-Disposition"] = f'attachment; filename="{quote(download)}"'

    # --- Try local disk first (works for both local and GCP with local fallback) ---
    target = (_RECORDINGS_BASE / rel_path).resolve()
    try:
        target.relative_to(_RECORDINGS_BASE)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path outside recordings root")

    if target.is_file():
        return FileResponse(target, headers=headers)

    # --- GCP fallback: download from cloud storage and stream ---
    if _STORAGE_TYPE == "gcp":
        from fastapi.responses import Response as _Response
        try:
            audio_bytes = await _StorageService.download_bytes(rel_path)
        except Exception as e:
            logger.warning("[serve_signed_audio] GCP download failed for %s: %s", rel_path, e)
            raise HTTPException(status_code=404, detail="Recording not found")
        if not audio_bytes:
            raise HTTPException(status_code=404, detail="Recording not found")
        # Infer MIME type from extension
        ext = rel_path.rsplit(".", 1)[-1].lower()
        mime_map = {"wav": "audio/wav", "opus": "audio/ogg", "m4a": "audio/mp4", "mp3": "audio/mpeg"}
        mime = mime_map.get(ext, "audio/octet-stream")
        return _Response(content=audio_bytes, media_type=mime, headers=headers)

    raise HTTPException(status_code=404, detail="Recording not found")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5167, reload=True)
