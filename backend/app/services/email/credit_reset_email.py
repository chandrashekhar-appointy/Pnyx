import html
import os
from typing import Dict, Optional

from ..email.smtp_sender import SmtpEmailSender


class CreditResetEmailService:
    """Sends email notifications when a user's weekly credits are replenished."""

    def __init__(self, sender: Optional[SmtpEmailSender] = None):
        self.sender = sender or SmtpEmailSender()

    def _app_url(self) -> str:
        return os.getenv("APP_BASE_URL", "https://meet.quexio.com")

    def _build_reset_email_content(
        self,
        user_email: str,
        weekly_credits: int = 10000,
    ) -> Dict[str, str]:
        safe_email = html.escape(user_email)
        safe_app_url = html.escape(self._app_url(), quote=True)
        credits_formatted = f"{weekly_credits:,}"

        subject = f"Your {credits_formatted} weekly credits have been replenished!"

        # --- Plain-text fallback ---
        text_body = "\n".join([
            f"Hi {user_email},",
            "",
            f"Great news! Your {credits_formatted} weekly meeting credits have been replenished.",
            "",
            "You're all set to record and transcribe your meetings this week.",
            "",
            f"Start a meeting: {self._app_url()}",
            "",
            "Tips for better meetings:",
            "- Set a clear agenda before you start",
            "- Use the AI assistant for real-time insights",
            "- Review your meeting notes afterwards",
            "",
            "— The Pnyx Team",
        ])

        # --- HTML email ---
        html_body = f"""
        <div style="font-family:'Segoe UI',Arial,sans-serif;max-width:680px;margin:0 auto;padding:22px;color:#111827;background:#f8fafc;">
          <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:24px;">
            <p style="margin:0;color:#64748b;font-size:12px;letter-spacing:.06em;text-transform:uppercase;">Pnyx Weekly Credits</p>

            <h2 style="margin:10px 0 6px;font-size:24px;line-height:1.3;color:#0f172a;">
              Your credits are back! \U0001f389
            </h2>

            <p style="margin:0 0 16px;color:#334155;font-size:15px;line-height:1.6;">
              Hi {safe_email}, your <strong style="color:#0f172a;">{credits_formatted} weekly meeting credits</strong>
              have been replenished. You&rsquo;re all set for another week of productive meetings.
            </p>

            <div style="margin:16px 0;padding:16px;background:linear-gradient(135deg,#f0f9ff 0%,#e0f2fe 100%);border:1px solid #bae6fd;border-radius:10px;text-align:center;">
              <p style="margin:0 0 4px;color:#0369a1;font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;">Available Credits</p>
              <p style="margin:0;font-size:32px;font-weight:700;color:#0c4a6e;">{credits_formatted}</p>
              <p style="margin:4px 0 0;color:#0369a1;font-size:13px;">Resets again next Monday</p>
            </div>

            <div style="text-align:center;margin:20px 0 8px;">
              <a href="{safe_app_url}"
                 style="display:inline-block;background:#0f172a;color:#ffffff;text-decoration:none;padding:13px 28px;border-radius:8px;font-weight:600;font-size:15px;">
                Catch Up on Your Meetings Today
              </a>
            </div>

            <div style="margin-top:18px;padding:12px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;">
              <p style="margin:0 0 8px;font-weight:600;color:#0f172a;font-size:14px;">Make the most of your credits</p>
              <ul style="margin:0 0 0 18px;padding:0;color:#334155;font-size:13px;line-height:1.8;">
                <li>Set a clear agenda before each meeting</li>
                <li>Use AI assistant for real-time insights</li>
                <li>Review &amp; share meeting notes with your team</li>
              </ul>
            </div>

            <p style="margin:20px 0 0;color:#94a3b8;font-size:12px;text-align:center;">
              This email was sent by Pnyx Meeting Co-Pilot.
            </p>
          </div>
        </div>
        """

        return {"subject": subject, "text_body": text_body, "html_body": html_body}

    async def send_credit_reset_notification(
        self,
        user_email: str,
        weekly_credits: int = 10000,
    ) -> Dict:
        """Send a notification email when weekly credits are replenished."""
        content = self._build_reset_email_content(
            user_email=user_email,
            weekly_credits=weekly_credits,
        )

        await self.sender.send(
            recipients=[user_email],
            subject=content["subject"],
            text_body=content["text_body"],
            html_body=content["html_body"],
        )

        return {
            "sent": True,
            "recipient": user_email,
            "weekly_credits": weekly_credits,
        }
