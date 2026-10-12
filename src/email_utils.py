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

    # Never email a blank body.  This happened when a free-tier model
    # returned an empty completion, which made the verdict string empty and
    # reduced the report to a bare heading.  Send an explicit notice instead
    # so the recipient knows why there is nothing to read.
    if not (html_content or "").strip():
        logger.warning("Empty email body for %s (subject=%r) – sending notice", recipient, subject)
        html_content = (
            f"<h2>{subject}</h2>"
            "<p><b>No analysis content was produced for this run.</b></p>"
            "<p>The analysis model returned an empty response, so there is "
            "nothing to report. Please check the workflow logs for the model "
            "failure and re-run.</p>"
        )

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
