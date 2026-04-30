from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Dict, Any, List
from pydantic import BaseModel
import json
import logging
import re

# Validation pattern for user_filter when an explicit user is requested.
# Restricts to email-shaped strings so the value is safe for parameterization
# AND prevents abuse of the dropdown by passing arbitrary content.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_USER_FILTER_KEYWORDS = {"all", "exclude_admin"}

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["Analytics"])

try:
    from ..deps import get_current_user, get_admin_user
    from ...db import DatabaseManager
except (ImportError, ValueError):
    from api.deps import get_current_user, get_admin_user
    from db import DatabaseManager

db = DatabaseManager()


class TrackEventRequest(BaseModel):
    event_name: str
    properties: Dict[str, Any] = {}
    session_id: str | None = None
    user_id: str | None = None
    timestamp: str | None = None


_EVENT_NAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,80}$")
_MAX_PROPERTIES_BYTES = 8 * 1024  # 8 KB per event

try:
    from ...core.security import verify_google_token
except (ImportError, ValueError):
    from core.security import verify_google_token


@router.post("/track")
async def track_event(request: TrackEventRequest, req: Request):
    """Ingest analytics events from frontend.

    Anonymous events are accepted (pre-login analytics), but:
    - event_name must match an alphanumeric allowlist
    - properties payload is size-capped
    - user_id from the request is *only* trusted if a valid Bearer token
      verifies the same email — otherwise we record as anonymous.
    Rate limiting is enforced globally via SlowAPI middleware.
    """
    # Validate event name to prevent log/db noise & analytics pollution
    if not _EVENT_NAME_RE.match(request.event_name or ""):
        raise HTTPException(status_code=400, detail="Invalid event_name")

    # Cap payload size (frontend sometimes attaches debug objects)
    serialized_props = json.dumps(request.properties or {})
    if len(serialized_props) > _MAX_PROPERTIES_BYTES:
        raise HTTPException(status_code=413, detail="properties too large")

    # Verify user_id only if token is present and valid; never trust the
    # request body for identity.
    verified_email: str | None = None
    auth_header = req.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        try:
            payload = await verify_google_token(token)
            verified_email = payload.get("email")
        except Exception:
            verified_email = None

    user_email = verified_email  # ignore client-supplied user_id entirely

    query = """
    INSERT INTO analytics_events (session_id, user_id, event_name, properties, timestamp)
    VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP)
    """

    try:
        async with db._get_connection() as conn:
            await conn.execute(
                query,
                request.session_id,
                user_email,
                request.event_name,
                serialized_props,
            )
    except Exception as e:
        logger.error(f"Failed to insert analytics event: {e}")
        # Swallow DB errors so analytics failures don't break frontend UX

    return {"status": "success"}


@router.get("/dashboard/metrics")
async def get_dashboard_metrics(
    user_filter: str | None = None, user=Depends(get_admin_user)
):
    """Fetch dashboard metrics, restricted to admin."""
    # Validate user_filter so it can never contribute SQL fragments —
    # only a fixed keyword set, an email-shaped string, or empty.
    if user_filter and user_filter not in _USER_FILTER_KEYWORDS:
        if not _EMAIL_RE.match(user_filter):
            raise HTTPException(
                status_code=400, detail="Invalid user_filter value"
            )
    try:
        async with db._get_connection() as conn:
            # Fetch all unique users to populate the dropdown
            unique_users_list_rows = await conn.fetch(
                "SELECT DISTINCT user_id FROM analytics_events WHERE user_id IS NOT NULL AND user_id != ''"
            )
            unique_users_list = [row["user_id"] for row in unique_users_list_rows]

            base_where = "user_id NOT LIKE 'localhost%'"
            args = []

            if user_filter == "exclude_admin":
                import os

                admin_emails = [
                    e.strip()
                    for e in os.getenv("ADMIN_EMAILS", "").split(",")
                    if e.strip()
                ]
                if admin_emails:
                    placeholders = ", ".join(
                        [f"${i + 1}" for i in range(len(admin_emails))]
                    )
                    base_where += f" AND user_id NOT IN ({placeholders})"
                    args.extend(admin_emails)
            elif user_filter and user_filter != "all":
                args.append(user_filter)
                base_where += f" AND user_id = ${len(args)}"

            # Top-level KPIs
            total_events = await conn.fetchval(
                f"SELECT COUNT(*) FROM analytics_events WHERE {base_where}", *args
            )
            unique_users = await conn.fetchval(
                f"SELECT COUNT(DISTINCT user_id) FROM analytics_events WHERE user_id IS NOT NULL AND {base_where}",
                *args,
            )

            # Breakdown by feature
            feature_breakdown_rows = await conn.fetch(
                f"""
                SELECT event_name, COUNT(*) as count 
                FROM analytics_events 
                WHERE {base_where}
                GROUP BY event_name 
                ORDER BY count DESC
                LIMIT 15
            """,
                *args,
            )
            feature_breakdown = [
                {"name": row["event_name"], "value": row["count"]}
                for row in feature_breakdown_rows
            ]

            # Template popularity (for notes_generated OR notes_template_switched)
            template_popularity_rows = await conn.fetch(
                f"""
                SELECT properties->>'template_name' as template_name, COUNT(*) as count
                FROM analytics_events
                WHERE event_name IN ('notes_generated', 'notes_template_switched') 
                  AND properties->>'template_name' IS NOT NULL
                  AND {base_where}
                GROUP BY properties->>'template_name'
                ORDER BY count DESC
            """,
                *args,
            )
            template_popularity = [
                {"name": row["template_name"], "value": row["count"]}
                for row in template_popularity_rows
            ]

            # Daily active usage (last 7 days)
            daily_usage_rows = await conn.fetch(
                f"""
                SELECT date_trunc('day', timestamp) as day, COUNT(*) as count
                FROM analytics_events
                WHERE timestamp >= CURRENT_DATE - INTERVAL '7 days'
                  AND {base_where}
                GROUP BY day
                ORDER BY day
            """,
                *args,
            )
            daily_usage = [
                {
                    "date": row["day"].strftime("%Y-%m-%d") if row["day"] else "",
                    "events": row["count"],
                }
                for row in daily_usage_rows
            ]

        return {
            "kpis": {
                "totalEvents": total_events or 0,
                "uniqueUsers": unique_users or 0,
            },
            "featureBreakdown": feature_breakdown,
            "templatePopularity": template_popularity,
            "dailyUsage": daily_usage,
            "uniqueUsersList": unique_users_list,
        }
    except Exception as e:
        logger.error(f"Failed to fetch metrics: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch dashboard metrics")
