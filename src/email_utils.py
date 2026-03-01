import threading
import resend
from src.core.logger import get_logger

logger = get_logger(__name__)

_send_lock = threading.Lock()


def send_email_report(subject: str, html_content: str, recipient: str, api_key: str):
    """Send an HTML email via Resend.

    Uses a lock around ``resend.api_key`` assignment to prevent race
    conditions when multiple emails are dispatched concurrently.
    """
    if not api_key:
        logger.warning("No API key for %s – email skipped", recipient)
        return None

    with _send_lock:
        try:
            resend.api_key = api_key

            params = {
                "from": "PrimoGreedy <onboarding@resend.dev>",
                "to": [recipient],
                "subject": subject,
                "html": html_content,
            }

            result = resend.Emails.send(params)
            logger.info("Email sent to %s (ID: %s)", recipient, result.get("id"))
            return result

        except Exception as exc:
            logger.error("Failed to send email to %s: %s", recipient, exc)
            return None
