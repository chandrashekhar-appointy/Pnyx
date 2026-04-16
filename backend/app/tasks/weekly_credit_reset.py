"""
Weekly Credit Reset Task — Celery beat task that runs Monday 00:00 UTC.

Resets all users' weekly free credits to 10,000 and logs to the credit ledger.
"""

import logging

try:
    from ..celery_app import celery_app
except (ImportError, ValueError):
    from celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.weekly_credit_reset", bind=True, max_retries=3)
def weekly_credit_reset(self):
    """
    Reset weekly credits for all users.

    This is a sync Celery task that wraps async calls via asyncio.run().
    Scheduled via Celery Beat to run every Monday at 00:00 UTC.

    Add to your celery beat schedule in celery_app.py or settings:
        celery_app.conf.beat_schedule = {
            'weekly-credit-reset': {
                'task': 'tasks.weekly_credit_reset',
                'schedule': crontab(hour=0, minute=0, day_of_week=1),
            },
        }
    """
    import asyncio

    async def _run_reset():
        try:
            from ..db import DatabaseManager
        except (ImportError, ValueError):
            from db import DatabaseManager

        try:
            from ..services.credit_manager import CreditManager, WEEKLY_FREE_CREDITS
        except (ImportError, ValueError):
            from services.credit_manager import CreditManager, WEEKLY_FREE_CREDITS

        try:
            from ..services.email.credit_reset_email import CreditResetEmailService
        except (ImportError, ValueError):
            from services.email.credit_reset_email import CreditResetEmailService

        db = DatabaseManager()
        credit_mgr = CreditManager(db)
        email_service = CreditResetEmailService()

        # Get all user emails from user_credits table
        async with db._get_connection() as conn:
            rows = await conn.fetch(
                "SELECT user_email FROM user_credits WHERE is_unlimited = FALSE"
            )

        user_emails = [row["user_email"] for row in rows]
        logger.info(
            f"[WeeklyReset] Starting weekly credit reset for {len(user_emails)} users"
        )

        success_count = 0
        error_count = 0
        email_sent_count = 0
        email_error_count = 0

        for email in user_emails:
            try:
                await credit_mgr.reset_weekly_credits(email)
                success_count += 1

                # Send notification email (best-effort, don't block reset)
                try:
                    await email_service.send_credit_reset_notification(
                        user_email=email,
                        weekly_credits=WEEKLY_FREE_CREDITS,
                    )
                    email_sent_count += 1
                except Exception as email_err:
                    email_error_count += 1
                    logger.warning(
                        "[WeeklyReset] Email notification failed for %s: %s",
                        email,
                        email_err,
                    )

            except Exception as e:
                error_count += 1
                logger.error(
                    f"[WeeklyReset] Failed to reset credits for {email}: {e}",
                    exc_info=True,
                )

        logger.info(
            "[WeeklyReset] Complete: %s/%s credits reset, %s/%s emails sent",
            success_count,
            len(user_emails),
            email_sent_count,
            success_count,
        )

        return {
            "total_users": len(user_emails),
            "success": success_count,
            "errors": error_count,
            "emails_sent": email_sent_count,
            "emails_failed": email_error_count,
        }

    return asyncio.run(_run_reset())
