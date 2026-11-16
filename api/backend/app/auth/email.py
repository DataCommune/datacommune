"""Transactional email sending via Resend or SMTP (verification + password reset).

Retry logic: each send is attempted up to 2 times. On definitive failure,
a structlog error is emitted and the failure counter is incremented.
The counter is exposed via email_failures_count() for /v1/health.
"""

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, cast

import resend

from app.config import settings

logger = logging.getLogger(__name__)

# Configure Resend SDK with API key
resend.api_key = settings.resend_api_key

# In-memory failure counter (reset on process restart)
_email_fail_count: int = 0


def _sender() -> str:
    """Display name and address every transactional email is sent from."""
    address = settings.email_sender or f"noreply@{settings.resend_domain}"
    return formataddr(("DataCommune", address))


def _send_smtp(params: dict[str, Any]) -> None:
    """Send one message through the configured SMTP server (blocking)."""
    msg = EmailMessage()
    msg["From"] = params["from"]
    msg["To"] = ", ".join(params["to"])
    msg["Subject"] = params["subject"]
    msg.set_content("Ce message est au format HTML.")
    msg.add_alternative(params["html"], subtype="html")

    timeout = 30
    conn: smtplib.SMTP
    if settings.smtp_port == 465:
        conn = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=timeout)
    else:
        conn = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout)
    with conn:
        if settings.smtp_port != 465:
            conn.starttls()
        conn.login(settings.smtp_username, settings.smtp_password)
        conn.send_message(msg)


def _deliver(params: dict[str, Any]) -> None:
    """Hand one message to the configured transport (blocking)."""
    if settings.email_transport == "smtp":
        _send_smtp(params)
    else:
        # Same keys as Resend.SendParams (from, to, subject, html), built above.
        resend.Emails.send(cast("resend.Emails.SendParams", params))


def email_failures_count() -> int:
    """Return the number of email send failures since process start."""
    return _email_fail_count


async def _send_with_retry(
    params: dict[str, Any],
    email_type: str,
    recipient: str,
    *,
    max_attempts: int = 2,
) -> None:
    """Send an email via Resend with retry and failure tracking.

    Best-effort: logs errors but does not raise (fire-and-forget pattern).
    """
    global _email_fail_count

    # Preproduction accounts are auto-verified by the auth router. This guard is
    # the final outbound boundary, including callers added outside that flow.
    if not settings.email_enabled:
        return

    for attempt in range(1, max_attempts + 1):
        try:
            await asyncio.to_thread(_deliver, params)
            return
        except Exception:
            if attempt < max_attempts:
                logger.warning(
                    "Email send attempt %d/%d failed for %s to %s, retrying...",
                    attempt,
                    max_attempts,
                    email_type,
                    recipient,
                )
                await asyncio.sleep(1)
            else:
                _email_fail_count += 1
                logger.exception(
                    "Failed to send %s email to %s after %d attempts",
                    email_type,
                    recipient,
                    max_attempts,
                )
                _try_alert(email_type, recipient)


def _try_alert(email_type: str, recipient: str) -> None:
    """Send an Apprise alert if alert_urls is configured."""
    if not settings.alert_urls:
        return
    try:
        import apprise

        ap = apprise.Apprise()
        for url in settings.alert_urls:
            ap.add(url)
        ap.notify(
            title="EMAIL SEND FAILURE",
            body=f"Type: {email_type}\nRecipient: {recipient}",
            notify_type=apprise.NotifyType.FAILURE,
        )
    except Exception:
        logger.exception("Failed to send email failure alert via Apprise")


async def send_verification_email(to: str, token: str) -> None:
    """Send an email verification link to a new user."""
    verify_url = f"{settings.dashboard_url}/verify?token={token}"
    sender = _sender()

    html = f"""
    <h2>Bienvenue sur DataCommune</h2>
    <p>Merci pour votre inscription. Cliquez sur le lien ci-dessous
    pour verifier votre adresse email :</p>
    <p><a href="{verify_url}">Verifier mon email</a></p>
    <p>Ce lien est valable 1 heure.</p>
    <p>Si vous n'avez pas cree de compte, ignorez cet email.</p>
    """

    await _send_with_retry(
        {
            "from": sender,
            "to": [to],
            "subject": "Verifiez votre email - DataCommune",
            "html": html,
        },
        "verification",
        to,
    )


async def send_reset_email(to: str, token: str) -> None:
    """Send a password reset link."""
    reset_url = f"{settings.dashboard_url}/reset-password?token={token}"
    sender = _sender()

    html = f"""
    <h2>Reinitialisation de mot de passe</h2>
    <p>Vous avez demande la reinitialisation de votre mot de passe
    DataCommune. Cliquez sur le lien ci-dessous :</p>
    <p><a href="{reset_url}">Reinitialiser mon mot de passe</a></p>
    <p>Ce lien est valable 30 minutes.</p>
    <p>Si vous n'avez pas fait cette demande, ignorez cet email.</p>
    """

    await _send_with_retry(
        {
            "from": sender,
            "to": [to],
            "subject": "Reinitialisation mot de passe - DataCommune",
            "html": html,
        },
        "reset",
        to,
    )


async def send_api_key_email(to: str, api_key: str) -> None:
    """Send an API key to a new quick-key user."""
    sender = _sender()

    html = (
        '<div style="font-family: -apple-system, BlinkMacSystemFont,'
        " 'Segoe UI', sans-serif; max-width: 560px;"
        ' margin: 0 auto; color: #2A1F14;">'
        '<h2 style="color: #A0744F;">'
        "Votre cle API DataCommune</h2>"
        "<p>Voici votre cle API :</p>"
        '<div style="background: #F5EEE3;'
        " border: 1px solid #D4C4AD;"
        ' border-radius: 8px; padding: 16px; margin: 16px 0;">'
        '<code style="font-size: 14px;'
        f' word-break: break-all; color: #2A1F14;">{api_key}</code>'
        "</div>"
        '<p style="color: #B85C3A; font-size: 13px;">'
        "<strong>Conservez cette cle precieusement"
        " — elle ne sera plus affichee.</strong></p>"
        '<h3 style="color: #A0744F; margin-top: 24px;">'
        "Quick start</h3>"
        '<div style="background: #1E1A15;'
        ' border-radius: 8px; padding: 16px; margin: 12px 0;">'
        '<code style="font-size: 13px; color: #E8D5C0;'
        ' white-space: pre-wrap;">'
        f'curl -H "X-API-Key: {api_key}" \\\n'
        f'  "{settings.api_base_url.rstrip("/")}/v1/datasets"'
        "</code></div>"
        '<p style="margin-top: 20px;">'
        "<strong>Plan Découverte</strong> : 500 requetes/mois<br>"
        '<a href="https://datacommune.fr/docs"'
        ' style="color: #A0744F;">Documentation complete</a>'
        "</p>"
        '<hr style="border: none;'
        ' border-top: 1px solid #D4C4AD; margin: 24px 0;">'
        '<p style="font-size: 12px; color: #6B5B4D;">'
        "Vous recevez cet email car une cle API a ete demandee"
        " avec cette adresse."
        " Si ce n'est pas vous, ignorez cet email."
        "</p></div>"
    )

    await _send_with_retry(
        {
            "from": sender,
            "to": [to],
            "subject": "Votre cle API - DataCommune",
            "html": html,
        },
        "api_key",
        to,
    )
