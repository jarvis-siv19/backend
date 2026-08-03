"""Transactional email via Zoho ZeptoMail.

All sends are fire-and-forget (`send_in_background`) so API latency never
blocks a request, and failures are logged rather than raised. Disabled unless
ZEPTOMAIL_ENABLED=true and a token is configured.

ZeptoMail API: POST {ZEPTOMAIL_API_URL}/v1.1/email with header
``Authorization: Zoho-enczapikey <token>``.
"""

import asyncio
import html
import logging

import httpx

from app.config import settings
from app.services import fx_service

logger = logging.getLogger(__name__)

TAGLINE = "Just another expense splitting app."


def _enabled() -> bool:
    return bool(settings.ZEPTOMAIL_ENABLED and settings.ZEPTOMAIL_TOKEN)


def _symbol(currency: str) -> str:
    code = fx_service.normalize_currency(currency)
    return fx_service.SUPPORTED_CURRENCIES.get(code, {}).get("symbol", code)


def _render(title: str, body_html: str, cta_text: str, cta_url: str) -> str:
    return f"""
<div style="background:#0f1117;padding:32px 16px;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <div style="max-width:520px;margin:0 auto;background:#171a23;border:1px solid #262b38;border-radius:16px;overflow:hidden;">
    <div style="padding:28px 32px 20px;background:linear-gradient(135deg,#1d2333,#12151d);">
      <div style="font-size:22px;font-weight:700;color:#ffffff;">Split<span style="color:#7c6cff;">LLM</span></div>
      <div style="font-size:12px;color:#8b90a0;margin-top:4px;">{TAGLINE}</div>
    </div>
    <div style="padding:28px 32px;color:#d6d9e0;font-size:15px;line-height:1.6;">
      <div style="font-size:18px;font-weight:600;color:#ffffff;margin-bottom:12px;">{title}</div>
      {body_html}
      <a href="{cta_url}" style="display:inline-block;margin-top:20px;padding:12px 22px;background:#7c6cff;color:#ffffff;text-decoration:none;border-radius:10px;font-weight:600;">{cta_text}</a>
    </div>
    <div style="padding:16px 32px;border-top:1px solid #262b38;color:#6a6f7f;font-size:12px;">
      Sent by SplitLLM &middot; <a href="{settings.APP_BASE_URL}" style="color:#7c6cff;text-decoration:none;">splitllm.com</a>
    </div>
  </div>
</div>
"""


async def _send(to_email: str, to_name: str, subject: str, html_body: str) -> bool:
    if not _enabled():
        logger.debug("ZeptoMail disabled; skipping email to %s (%s)", to_email, subject)
        return False

    payload = {
        "from": {
            "address": settings.ZEPTOMAIL_FROM_ADDRESS,
            "name": settings.ZEPTOMAIL_FROM_NAME,
        },
        "to": [{"email_address": {"address": to_email, "name": to_name or to_email}}],
        "subject": subject,
        "htmlbody": html_body,
    }
    headers = {
        "Authorization": f"Zoho-enczapikey {settings.ZEPTOMAIL_TOKEN}",
        "Content-Type": "application/json",
    }
    url = f"{settings.ZEPTOMAIL_API_URL.rstrip('/')}/v1.1/email"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 300:
            logger.error(
                "ZeptoMail send failed (%s) to %s: %s",
                resp.status_code,
                to_email,
                resp.text[:500],
            )
            return False
        return True
    except Exception as e:
        logger.error("ZeptoMail send error to %s: %s", to_email, e)
        return False


def send_in_background(coro) -> None:
    """Schedule an email send without blocking the request."""
    task = asyncio.ensure_future(coro)

    def _log_result(t: asyncio.Task):
        exc = t.exception()
        if exc:
            logger.error("Background email task failed: %s", exc)

    task.add_done_callback(_log_result)


async def send_invite_email(to_email: str, to_name: str, inviter_name: str) -> bool:
    inviter = html.escape(inviter_name or "A friend")
    name = html.escape(to_name or "there")
    body = (
        f"<p>Hi {name},</p>"
        f"<p><b>{inviter}</b> added you to shared expenses on SplitLLM. "
        "Sign up with this email address and your expense history will be "
        "waiting for you.</p>"
    )
    return await _send(
        to_email,
        to_name,
        f"{inviter_name or 'A friend'} added you to shared expenses on SplitLLM",
        _render("You've been added to shared expenses", body, "Join SplitLLM", settings.APP_BASE_URL),
    )


async def send_expense_notification(
    to_email: str,
    to_name: str,
    creator_name: str,
    expense_name: str,
    amount: float,
    currency: str,
    event_name: str | None = None,
) -> bool:
    creator = html.escape(creator_name or "Someone")
    name = html.escape(to_name or "there")
    exp = html.escape(expense_name)
    where = f" in <b>{html.escape(event_name)}</b>" if event_name else ""
    body = (
        f"<p>Hi {name},</p>"
        f"<p><b>{creator}</b> added a new expense{where}:</p>"
        f"<p style='font-size:17px;'><b>{exp}</b> &mdash; "
        f"<b>{_symbol(currency)}{amount:,.2f}</b></p>"
        "<p>Open SplitLLM to see your share.</p>"
    )
    return await _send(
        to_email,
        to_name,
        f"New expense: {expense_name} ({_symbol(currency)}{amount:,.2f})",
        _render("New expense added", body, "View expense", settings.APP_BASE_URL),
    )


async def send_settleup_email(
    to_email: str,
    to_name: str,
    payer_name: str,
    payee_name: str = "",
    amount: float | None = None,
    currency: str | None = None,
    is_payer: bool = False,
) -> bool:
    """Confirmation that a settle-up is done. Sent to both sides.

    The wording flips with the recipient — the same event is "you paid" to one
    person and "you were paid" to the other, and a receipt that describes the
    wrong direction is worse than none.
    """
    payer = html.escape(payer_name or "Your friend")
    payee = html.escape(payee_name or "your friend")
    name = html.escape(to_name or "there")
    amount_html = (
        f"<p style='font-size:17px;'><b>{_symbol(currency or '')}{amount:,.2f}</b></p>"
        if amount
        else ""
    )
    if is_payer:
        line = f"<p>Your settle-up with <b>{payee}</b> is confirmed.</p>"
        subject = f"You settled up with {payee_name or 'your friend'}"
    else:
        line = f"<p><b>{payer}</b> settled up with you on SplitLLM.</p>"
        subject = f"{payer_name or 'Your friend'} settled up with you"
    body = (
        f"<p>Hi {name},</p>"
        f"{line}"
        f"{amount_html}"
        "<p>Your balance is now clear. Open the app to review it.</p>"
    )
    return await _send(
        to_email,
        to_name,
        subject,
        _render("Settled up", body, "View settlement", settings.APP_BASE_URL),
    )


async def send_settle_confirm_request_email(
    to_email: str,
    to_name: str,
    payer_name: str,
    amount: float,
    currency: str,
) -> bool:
    """Ask the person who is owed to confirm they actually received the money.

    Nothing has been recorded yet at this point — the balance only moves once
    they confirm, which is why this mail asks rather than informs.
    """
    payer = html.escape(payer_name or "Your friend")
    name = html.escape(to_name or "there")
    body = (
        f"<p>Hi {name},</p>"
        f"<p><b>{payer}</b> says they paid you "
        f"<b>{_symbol(currency)}{amount:,.2f}</b> to settle up.</p>"
        "<p>Confirm it in the app once you've received it — your balance stays "
        "as it is until you do.</p>"
    )
    return await _send(
        to_email,
        to_name,
        f"{payer_name or 'Your friend'} says they settled up — confirm it",
        _render(
            "Confirm a settle-up", body, "Review and confirm", settings.APP_BASE_URL
        ),
    )


async def send_card_reminder_email(
    to_email: str,
    to_name: str,
    card_name: str,
    due_date: str,
    days_until: int,
    amount: float | None = None,
    currency: str | None = None,
) -> bool:
    """Credit-card bill reminder, sent 10 and 5 days ahead of the due date."""
    name = html.escape(to_name or "there")
    card = html.escape(card_name or "your credit card")
    amount_html = (
        f"<p style='font-size:17px;'><b>{_symbol(currency or '')}{amount:,.2f}</b></p>"
        if amount
        else ""
    )
    when = "in 1 day" if days_until == 1 else f"in {days_until} days"
    body = (
        f"<p>Hi {name},</p>"
        f"<p>Your <b>{card}</b> bill is due {when}, on <b>{html.escape(due_date)}</b>.</p>"
        f"{amount_html}"
        "<p>This is a reminder from SplitLLM — we don't have access to your "
        "card or its balance, so check your issuer's statement for the exact "
        "amount.</p>"
    )
    return await _send(
        to_email,
        to_name,
        f"{card_name or 'Credit card'} bill due {when} ({due_date})",
        _render("Card bill due soon", body, "Open SplitLLM", settings.APP_BASE_URL),
    )


async def send_payment_request_email(
    to_email: str,
    to_name: str,
    requester_name: str,
    amount: float,
    currency: str,
    payment_link: str | None = None,
) -> bool:
    requester = html.escape(requester_name or "Your friend")
    name = html.escape(to_name or "there")
    body = (
        f"<p>Hi {name},</p>"
        f"<p><b>{requester}</b> requested a payment of "
        f"<b>{_symbol(currency)}{amount:,.2f}</b> on SplitLLM.</p>"
    )
    return await _send(
        to_email,
        to_name,
        f"{requester_name or 'Your friend'} requested {_symbol(currency)}{amount:,.2f}",
        _render(
            "Payment request",
            body,
            "Pay now" if payment_link else "Open SplitLLM",
            payment_link or settings.APP_BASE_URL,
        ),
    )
