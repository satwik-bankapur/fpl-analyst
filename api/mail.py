"""Transactional email via Resend (§3), isolated to one module so swapping
providers is a one-file change.

Stage A ships only the Welcome template, as inline email-safe HTML (React Email is
a Stage B weekly-template concern). When RESEND_API_KEY is empty the send is a
logged no-op — that keeps tests and local dev zero-spend and offline; tests assert
the trigger by monkeypatching `send_welcome` on this module.

Named `mail`, not top-level `email/`, to avoid shadowing the stdlib `email`
package (see docs/layer5 drift note).
"""
from __future__ import annotations

import logging

import httpx

from .config import settings

log = logging.getLogger("api.mail")

_RESEND_URL = "https://api.resend.com/emails"


def _welcome_html(brief_url: str | None) -> str:
    cta = (
        f'<p><a href="{brief_url}">Open your brief →</a></p>' if brief_url else ""
    )
    return (
        "<div style=\"font-family:system-ui,sans-serif;max-width:520px\">"
        "<h2>You're in.</h2>"
        "<p>Thanks for signing up to FPL Analyst. Each gameweek you'll get a "
        "scout report for your team — the verdict, one transfer, and the captain "
        "call, free.</p>"
        f"{cta}"
        "<p style=\"color:#666;font-size:13px\">Not affiliated with the Premier "
        "League. Reply to this email any time.</p>"
        "</div>"
    )


def send_welcome(email: str, *, brief_url: str | None = None) -> bool:
    """Send the welcome email. Returns True if dispatched, False if skipped/no-op.

    Never raises into the request path — a failed welcome must not fail signup.
    """
    if not settings.resend_api_key:
        log.info("mail: RESEND_API_KEY unset — welcome email to %s skipped (no-op)", email)
        return False
    payload = {
        "from": settings.send_from,
        "to": [email],
        "subject": "Your FPL Analyst brief is on the way",
        "html": _welcome_html(brief_url),
    }
    try:
        resp = httpx.post(
            _RESEND_URL,
            json=payload,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            timeout=15.0,
        )
        resp.raise_for_status()
        log.info("mail: welcome email sent to %s", email)
        return True
    except httpx.HTTPError as exc:  # log and swallow — signup already succeeded
        log.warning("mail: welcome email to %s failed: %s", email, exc)
        return False
