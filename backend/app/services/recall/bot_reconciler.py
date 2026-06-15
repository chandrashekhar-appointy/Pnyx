"""
Bot Reconciler — background watchdog for Recall.ai bot sessions.

Recall enforces the per-bot `automatic_leave` timeouts server-side, so a bot
will always leave a meeting on its own. But if the corresponding webhook is
dropped, our DB can be left believing a bot is still active forever — which is
how a bot appears to "sit in a meeting for 24 hours."

This loop periodically asks Recall what actually happened to any bot we still
think is active and finalizes / force-leaves accordingly. Mirrors the design of
AudioSessionReconciler.
"""

import asyncio
import logging
import os
from typing import Optional

try:
    from .manager import RecallManager
except (ImportError, ValueError):
    from services.recall.manager import RecallManager

logger = logging.getLogger(__name__)


class BotReconciler:
    def __init__(self):
        self.enabled = os.getenv("RECALL_BOT_RECONCILER_ENABLED", "true").lower() == "true"
        self.interval_seconds = int(
            os.getenv("RECALL_BOT_RECONCILER_INTERVAL_SECONDS", "120")
        )
        # Only inspect bots that have been "active" at least this long, so we
        # don't fight with the normal join flow.
        self.stuck_after_minutes = int(
            os.getenv("RECALL_BOT_STUCK_AFTER_MINUTES", "15")
        )
        self._manager: Optional[RecallManager] = None
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    def start(self):
        if not self.enabled:
            logger.info("[BotReconciler] Disabled by env")
            return
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())
        logger.info(
            "[BotReconciler] Started (interval=%ss, stuck_after=%smin)",
            self.interval_seconds, self.stuck_after_minutes,
        )

    async def stop(self):
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("[BotReconciler] Stopped")

    async def _run(self):
        # Lazily construct the manager so a missing Redis/DB at import time
        # never crashes startup.
        try:
            self._manager = RecallManager()
        except Exception as e:
            logger.error("[BotReconciler] Could not init RecallManager: %s", e)
            return

        while not self._stop_event.is_set():
            try:
                result = await self._manager.reconcile_stuck_bots(
                    stuck_after_minutes=self.stuck_after_minutes
                )
                if result.get("reconciled"):
                    logger.info("[BotReconciler] %s", result)
            except Exception as e:
                logger.error("[BotReconciler] Loop error: %s", e, exc_info=True)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.interval_seconds
                )
            except asyncio.TimeoutError:
                pass
