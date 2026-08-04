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

import hashlib
import logging
from urllib.parse import quote

import httpx

from .config import settings

log = logging.getLogger("api.mail")

_RESEND_URL = "https://api.resend.com/emails"


def mask(email: str) -> str:
    """A stable, non-reversible handle for logs. NEVER log a full address.

    docs/AUDIT.md §D4.3: at send-batch volume, full addresses in logs are a copy of
    the mailing list sitting in a log provider, retained on their schedule and
    readable by anyone with dashboard access. The hash is stable, so you can still
    correlate "this recipient failed twice" across runs; it just isn't a list.

    The domain is kept in the clear — it is not personal, and 'every failure is
    @yahoo.com' is exactly the diagnosis you need at 7am.
    """
    email = (email or "").strip().lower()
    digest = hashlib.sha256(email.encode()).hexdigest()[:12]
    domain = email.rpartition("@")[2]
    return f"{digest}@{domain}" if domain else digest


class MailError(Exception):
    """A send failed. Carries a short reason for `send_log.error`, never a body."""


def send(*, to: str, subject: str, html: str, text: str,
         unsubscribe_url: str | None = None, post=None) -> bool:
    """Send one email. Returns True if dispatched, raises MailError on failure.

    Unlike `send_welcome`, this RAISES: the send batch needs to record a per-
    recipient failure and decide about a retry, and a swallowed exception would
    make every failure look like a success in `send_log`.

    `List-Unsubscribe` + `List-Unsubscribe-Post` are set on every bulk send —
    Gmail/Yahoo bulk-sender rules make one-click unsubscribe non-optional, and a
    missing header costs inbox placement for the whole domain, not just this
    message.
    """
    if not settings.resend_api_key:
        log.info("mail: RESEND_API_KEY unset — send to %s skipped (no-op)", mask(to))
        return False

    headers = {"Authorization": f"Bearer {settings.resend_api_key}"}
    payload: dict = {
        "from": settings.send_from,
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
    }
    if unsubscribe_url:
        payload["headers"] = {
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }

    post = post or httpx.post
    try:
        resp = post(_RESEND_URL, json=payload, headers=headers, timeout=20.0)
        resp.raise_for_status()
    except Exception as exc:
        raise MailError(f"{type(exc).__name__}: {exc}"[:300]) from exc
    log.info("mail: sent %r to %s", subject[:40], mask(to))
    return True


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


def send_magic_link(email: str, token: str, *, next_path: str = "/") -> bool:
    """Mail a sign-in link. Never raises into the request path.

    Swallowing the failure is deliberate and is the same rule `send_welcome`
    follows: `POST /api/auth/request-link` must answer identically for every
    address (no enumeration oracle), and an exception escaping here would make a
    provider hiccup on one address look different from success on another.

    NO `List-Unsubscribe` HEADER. This is transactional mail the recipient asked
    for seconds ago, not bulk — putting an unsubscribe on it invites someone to
    unsubscribe from their own ability to sign in.

    The link points at the API, not the web app: it must be consumed by the route
    that sets the session cookie, and bouncing through Next.js first would mean
    two hops with a single-use token in the middle of them.
    """
    api = settings.public_api_url.rstrip("/")
    url = f"{api}/api/auth/verify?token={token}&next={quote(next_path, safe='/')}"
    minutes = settings.magic_link_ttl_minutes
    html = (
        "<div style=\"font-family:system-ui,sans-serif;max-width:520px\">"
        "<h2>Sign in to FPL Analyst</h2>"
        f'<p><a href="{url}">Click here to sign in →</a></p>'
        f"<p style=\"color:#666;font-size:13px\">This link works once and expires "
        f"in {minutes} minutes. If you didn't ask for it, ignore this email — "
        "nobody can sign in without clicking it.</p>"
        "</div>"
    )
    text = (
        "Sign in to FPL Analyst\n\n"
        f"{url}\n\n"
        f"This link works once and expires in {minutes} minutes.\n"
        "If you didn't ask for it, ignore this email."
    )
    try:
        return send(to=email, subject="Your FPL Analyst sign-in link",
                    html=html, text=text)
    except MailError as exc:
        log.warning("mail: magic link to %s failed: %s", mask(email), exc)
        return False


def send_welcome(email: str, *, brief_url: str | None = None) -> bool:
    """Send the welcome email. Returns True if dispatched, False if skipped/no-op.

    Never raises into the request path — a failed welcome must not fail signup.
    """
    if not settings.resend_api_key:
        log.info("mail: RESEND_API_KEY unset — welcome email to %s skipped (no-op)",
                 mask(email))
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
        log.info("mail: welcome email sent to %s", mask(email))
        return True
    except httpx.HTTPError as exc:  # log and swallow — signup already succeeded
        log.warning("mail: welcome email to %s failed: %s", mask(email), exc)
        return False
