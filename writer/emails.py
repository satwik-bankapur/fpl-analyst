"""Email templates (spec §3) — email-safe HTML plus a real plaintext alternative.

Lives in `writer/` because Layer 4 owns rendering (docs/CLAUDE.md repo shape:
"writer/ … email templates"). It renders text; it does not send. Sending is
`api/send.py`, and the provider call is `api/mail.py`. Keeping those three apart
is what makes "swap the provider" a one-file change.

## Plaintext is not an afterthought

Every template returns `(subject, html, text)`. The plaintext part is generated
from the same section data, not stripped from the HTML — a `<p>`-to-newline
conversion produces the kind of mangled text that gets a sender filed as spam,
and bulk-sender rules (Gmail/Yahoo, Feb 2024) treat a missing or degenerate text
part as a signal.

## No images, no external CSS, no web fonts

Inline styles only, single column, max-width 560. Not minimalism for its own sake:
remote assets in a bulk send are a tracking-pixel signal to filters, and half of
recipients block them anyway, so anything they carry is content that did not
arrive.
"""
from __future__ import annotations

import html as _html
import re

from graph.sections import SECTION_ORDER, SECTION_TITLES

# One place for the visual chrome, so a change lands in every template.
_WRAP_OPEN = (
    '<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;'
    'max-width:560px;margin:0 auto;padding:8px 4px;color:#111;'
    'font-size:15px;line-height:1.55">'
)
_WRAP_CLOSE = "</div>"
_MUTED = "color:#666;font-size:13px;line-height:1.5"
_RULE = '<hr style="border:0;border-top:1px solid #e5e5e5;margin:22px 0">'


def _esc(s: str) -> str:
    return _html.escape(s or "", quote=True)


def _md_to_html(md: str) -> str:
    """The narrow slice of markdown the writers actually emit.

    Bullets, paragraphs and `**bold**` — nothing else. A general markdown
    dependency would be a bigger attack surface than the feature: brief content
    derives from `players.news`, which is EXTERNAL text from the FPL API (see
    docs/AUDIT.md §M6). Everything here is escaped BEFORE any markup is added, so
    hostile input renders as visible characters rather than as HTML.
    """
    out: list[str] = []
    bullets: list[str] = []

    def flush():
        if bullets:
            items = "".join(f'<li style="margin:0 0 6px">{b}</li>' for b in bullets)
            out.append(f'<ul style="margin:0 0 14px;padding-left:20px">{items}</ul>')
            bullets.clear()

    for raw in (md or "").splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        safe = _esc(line.lstrip("-* ").strip() if line.startswith(("- ", "* ")) else line)
        safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
        if line.startswith(("- ", "* ")):
            bullets.append(safe)
        else:
            flush()
            out.append(f'<p style="margin:0 0 14px">{safe}</p>')
    flush()
    return "".join(out)


def _md_to_text(md: str) -> str:
    """Plaintext from the same source. Bullets keep a marker; bold loses its stars."""
    lines = []
    for raw in (md or "").splitlines():
        line = raw.strip()
        if not line:
            lines.append("")
            continue
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        lines.append(f"  - {line.lstrip('-* ').strip()}"
                     if line.startswith(("- ", "* ")) else line)
    return "\n".join(lines).strip()


def _hook(sections: dict[str, str], limit: int = 60) -> str:
    """First sentence of the verdict, truncated — the subject-line hook (§3)."""
    verdict = (sections.get("verdict") or "").strip()
    if not verdict:
        return "your team, read properly"
    first = re.split(r"(?<=[.!?])\s", verdict.replace("\n", " "))[0].strip()
    first = re.sub(r"\*\*(.+?)\*\*", r"\1", first).rstrip(".")
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def _footer_html(manage_url: str, unsubscribe_url: str) -> str:
    return (
        f"{_RULE}"
        f'<p style="{_MUTED}">'
        f'<a href="{_esc(manage_url)}">Open in your browser</a> · '
        f'<a href="{_esc(unsubscribe_url)}">Unsubscribe</a>'
        "<br>Not affiliated with the Premier League. Reply to this email any time."
        "</p>"
    )


def _footer_text(manage_url: str, unsubscribe_url: str) -> str:
    return (
        "\n\n---\n"
        f"Open in your browser: {manage_url}\n"
        f"Unsubscribe: {unsubscribe_url}\n"
        "Not affiliated with the Premier League. Reply to this email any time."
    )


# --- 1. weekly paid brief (§3 template 1) -----------------------------------

def paid_brief(*, gameweek: int, sections: dict[str, str], brief_url: str,
               unsubscribe_url: str, team_name: str | None = None
               ) -> tuple[str, str, str]:
    """The full brief. Sections render in SECTION_ORDER — never in dict order.

    Ordering from the canonical vocabulary rather than from `sections.keys()` means
    a writer that emits sections out of order still produces a correctly ordered
    email, and a section key that is not in the vocabulary is not rendered at all
    (the same allow-list property the paywall relies on).
    """
    subject = f"GW{gameweek}: {_hook(sections)}"
    who = f" — {team_name}" if team_name else ""

    body_html = [
        _WRAP_OPEN,
        f'<p style="{_MUTED};margin:0 0 4px">Gameweek {gameweek}{_esc(who)}</p>',
        '<h1 style="font-size:21px;margin:0 0 18px;line-height:1.3">'
        "Your scout report</h1>",
    ]
    body_text = [f"Gameweek {gameweek}{who}", "Your scout report", ""]

    for key in SECTION_ORDER:
        md = (sections.get(key) or "").strip()
        if not md:
            continue
        title = SECTION_TITLES.get(key, key)
        body_html.append(
            f'<h2 style="font-size:15px;margin:22px 0 8px;'
            f'text-transform:uppercase;letter-spacing:.04em;color:#444">'
            f"{_esc(title)}</h2>"
        )
        body_html.append(_md_to_html(md))
        body_text += [title.upper(), _md_to_text(md), ""]

    body_html.append(_footer_html(brief_url, unsubscribe_url))
    body_html.append(_WRAP_CLOSE)
    return subject, "".join(body_html), "\n".join(body_text).strip() + _footer_text(
        brief_url, unsubscribe_url)


# --- 2. weekly free preview (§3 template 2) ---------------------------------

def free_preview(*, gameweek: int, sections: dict[str, str], landing_url: str,
                 unsubscribe_url: str) -> tuple[str, str, str]:
    """The generic preview plus the upgrade CTA.

    Subject hook is the trap-of-the-week (§3). Falls back to the week-ahead
    section rather than inventing one — an empty hook produces a subject line
    ending in a colon, which reads broken.
    """
    trap = (sections.get("trap_of_the_week") or "").strip()
    hook = _hook({"verdict": trap or sections.get("the_week_ahead", "")}, limit=52)
    subject = f"GW{gameweek} preview: {hook}"

    from graph.sections import PREVIEW_ORDER, PREVIEW_TITLES

    body_html = [
        _WRAP_OPEN,
        f'<p style="{_MUTED};margin:0 0 4px">Gameweek {gameweek}</p>',
        '<h1 style="font-size:21px;margin:0 0 18px;line-height:1.3">'
        "The week ahead</h1>",
    ]
    body_text = [f"Gameweek {gameweek}", "The week ahead", ""]

    for key in PREVIEW_ORDER:
        md = (sections.get(key) or "").strip()
        if not md:
            continue
        title = PREVIEW_TITLES.get(key, key)
        body_html.append(
            f'<h2 style="font-size:15px;margin:22px 0 8px;'
            f'text-transform:uppercase;letter-spacing:.04em;color:#444">'
            f"{_esc(title)}</h2>"
        )
        body_html.append(_md_to_html(md))
        body_text += [title.upper(), _md_to_text(md), ""]

    body_html.append(
        f"{_RULE}"
        f'<p style="margin:0 0 14px"><strong>This is the league-wide view.</strong> '
        f"The full report reads <em>your</em> squad — the transfer, the captain, the "
        f"bench order.</p>"
        f'<p style="margin:0 0 4px">'
        f'<a href="{_esc(landing_url)}" style="display:inline-block;'
        f'background:#111;color:#fff;text-decoration:none;padding:11px 18px;'
        f'border-radius:6px;font-weight:600">Get this for my team →</a></p>'
    )
    body_text += ["---",
                  "This is the league-wide view. The full report reads YOUR squad —",
                  "the transfer, the captain, the bench order.",
                  f"Get this for my team: {landing_url}"]

    body_html.append(_footer_html(landing_url, unsubscribe_url))
    body_html.append(_WRAP_CLOSE)
    return subject, "".join(body_html), "\n".join(body_text).strip() + _footer_text(
        landing_url, unsubscribe_url)


# --- 3. welcome (§3 template 3) ---------------------------------------------

def welcome(*, brief_url: str | None, unsubscribe_url: str,
            paid: bool = False) -> tuple[str, str, str]:
    """Sent on signup and on checkout. One template, two openings."""
    subject = ("You're in — your first full brief is ready" if paid
               else "Your FPL Analyst brief is on the way")
    lead = (
        "Thanks for subscribing. Every gameweek, a few hours after the deadline, "
        "you'll get the full scout report for your team — the verdict, the "
        "transfer, the captain call, the bench order and the chip plan."
        if paid else
        "Thanks for signing up. Each gameweek you'll get a scout report for your "
        "team — the verdict, one transfer, and the captain call, free."
    )
    cta = (
        f'<p style="margin:0 0 14px">'
        f'<a href="{_esc(brief_url)}" style="display:inline-block;background:#111;'
        f'color:#fff;text-decoration:none;padding:11px 18px;border-radius:6px;'
        f'font-weight:600">Open your brief →</a></p>' if brief_url else ""
    )
    html = (
        f"{_WRAP_OPEN}"
        '<h1 style="font-size:21px;margin:0 0 14px">You\'re in.</h1>'
        f'<p style="margin:0 0 14px">{_esc(lead)}</p>'
        f"{cta}"
        f"{_footer_html(brief_url or unsubscribe_url, unsubscribe_url)}"
        f"{_WRAP_CLOSE}"
    )
    text = "You're in.\n\n" + lead
    if brief_url:
        text += f"\n\nOpen your brief: {brief_url}"
    text += _footer_text(brief_url or unsubscribe_url, unsubscribe_url)
    return subject, html, text
