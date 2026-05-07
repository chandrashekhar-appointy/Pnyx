try:
    from ..db import DatabaseManager
    from ..schemas.user import User
except (ImportError, ValueError):
    from db import DatabaseManager
    from schemas.user import User

import logging

logger = logging.getLogger(__name__)


class RBAC:
    def __init__(self, db: DatabaseManager):
        self.db = db

    async def can(self, user: User, action: str, meeting_id: str) -> bool:
        """
        Central Policy Check: Can `user` perform `action` on `meeting_id`?

        Default policy: Only meeting owner (or explicitly permitted user) has access.
        """
        if not user or not user.email:
            return False

        # Allow AI interaction with the current recording (not yet saved in DB)
        if meeting_id == "current-recording" and action == "ai_interact":
            return True

        try:
            async with self.db._get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT owner_id FROM meetings WHERE id = $1", meeting_id
                )
                if row and row.get("owner_id") == user.email:
                    return True

                # Optional: check meeting_permissions table if it exists
                try:
                    perm = await conn.fetchrow(
                        """
                        SELECT 1
                        FROM meeting_permissions
                        WHERE meeting_id = $1 AND user_id = $2
                        LIMIT 1
                        """,
                        meeting_id,
                        user.email,
                    )
                    if perm:
                        return True
                except Exception as e:
                    # Table may not exist; keep private by default
                    logger.debug(
                        f"RBAC: meeting_permissions check skipped: {e}", exc_info=True
                    )
        except Exception as e:
            logger.error(f"RBAC: Error checking permissions: {e}", exc_info=True)
            return False

        logger.info(
            f"RBAC Deny: {user.email} cannot {action} meeting {meeting_id}"
        )
        return False

    async def get_accessible_meetings(self, user: User):
        """
        Return a list of meeting_ids the user can access.

        Default policy: Return meetings owned by user or explicitly shared.
        """
        if not user or not user.email:
            return []

        async with self.db._get_connection() as conn:
            accessible_ids = set()

            # Owner access
            rows = await conn.fetch(
                "SELECT id FROM meetings WHERE owner_id = $1", user.email
            )
            accessible_ids.update([r["id"] for r in rows])

            # Optional: shared access
            try:
                rows = await conn.fetch(
                    """
                    SELECT meeting_id
                    FROM meeting_permissions
                    WHERE user_id = $1
                    """,
                    user.email,
                )
                accessible_ids.update([r["meeting_id"] for r in rows])
            except Exception as e:
                logger.debug(
                    f"RBAC: meeting_permissions list skipped: {e}", exc_info=True
                )

        return list(accessible_ids)
