"""Transactional email abstraction for BookLoop.

The rest of the app calls `get_email_provider().send(...)` and never touches a provider SDK
directly, so switching providers later (Postmark, SES, SMTP, ...) is a one-file change here.

Current provider: Resend (https://resend.com), called over its plain REST API with `httpx` (already
a dependency) — no provider-specific SDK/package needed. Configure via environment variables:

    RESEND_API_KEY=re_...          required to actually send
    EMAIL_FROM=BookLoop <you@yourdomain.com>   optional, defaults to Resend's shared test address

Never hard-code a key here, never log a key or a full email body, never expose these env vars to the
frontend (they are read only in this backend process). If no provider is configured, `send()` raises
`EmailNotConfiguredError` with an actionable message — callers must not treat that as success.
"""
from __future__ import annotations

import os
import logging
from typing import Optional, Protocol

import httpx

logger = logging.getLogger("bookloop.email")

DEFAULT_FROM = "BookLoop <onboarding@resend.dev>"


class EmailNotConfiguredError(RuntimeError):
    """Raised when an email send is attempted with no provider configured."""


class EmailSendError(RuntimeError):
    """Raised when the provider was configured but the send itself failed."""


class EmailProvider(Protocol):
    async def send(self, *, to: str, subject: str, html: str, text: str) -> None: ...


class ResendEmailProvider:
    """Sends mail through Resend's REST API (https://resend.com/docs/api-reference/emails/send-email)."""

    def __init__(self, api_key: str, from_address: str):
        self._api_key = api_key
        self._from = from_address

    async def send(self, *, to: str, subject: str, html: str, text: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={"from": self._from, "to": [to], "subject": subject, "html": html, "text": text},
                )
        except httpx.HTTPError as e:
            logger.warning(f"Resend send failed (network): {e!r}")
            raise EmailSendError("Could not reach the email provider") from e
        if resp.status_code >= 400:
            # Never log the email body/recipient details beyond what's needed to diagnose a config issue.
            logger.warning(f"Resend send failed: {resp.status_code} {resp.text[:300]}")
            raise EmailSendError(f"Email provider rejected the send ({resp.status_code})")


class NullEmailProvider:
    """Used when no provider is configured. Fails loudly and clearly instead of pretending to send."""

    async def send(self, *, to: str, subject: str, html: str, text: str) -> None:
        raise EmailNotConfiguredError(
            "No email provider configured. Set RESEND_API_KEY (and optionally EMAIL_FROM) in "
            "backend/.env, or enable OTP_DEBUG_MODE=true for local development/testing."
        )


_provider: Optional[EmailProvider] = None


def get_email_provider() -> EmailProvider:
    """Reads config once and caches the provider. Missing RESEND_API_KEY -> NullEmailProvider,
    which fails clearly on send rather than silently succeeding."""
    global _provider
    if _provider is None:
        api_key = (os.environ.get("RESEND_API_KEY") or "").strip()
        if api_key:
            from_address = (os.environ.get("EMAIL_FROM") or DEFAULT_FROM).strip()
            _provider = ResendEmailProvider(api_key, from_address)
        else:
            _provider = NullEmailProvider()
    return _provider


def otp_email_body(name: str, code: str) -> tuple[str, str, str]:
    """Returns (subject, html, text) for a registration OTP email."""
    subject = "Your BookLoop verification code"
    text = (
        f"Hi {name},\n\n"
        f"Your BookLoop verification code is: {code}\n\n"
        "This code expires in 10 minutes and can only be used once. "
        "If you didn't request this, you can safely ignore this email.\n\n"
        "— BookLoop"
    )
    html = f"""
    <div style="font-family: -apple-system, Helvetica, Arial, sans-serif; max-width: 480px; margin: 0 auto;
                background: #F7F4EE; padding: 32px 28px; border-radius: 16px;">
      <p style="color:#17211F; font-size:16px; margin:0 0 16px;">Hi {name},</p>
      <p style="color:#17211F; font-size:15px; line-height:1.5; margin:0 0 24px;">
        Your BookLoop verification code is:
      </p>
      <div style="font-size:32px; font-weight:700; letter-spacing:8px; color:#D96C4A;
                  text-align:center; margin:0 0 24px;">{code}</div>
      <p style="color:#707775; font-size:13px; line-height:1.5; margin:0;">
        This code expires in 10 minutes and can only be used once.
        If you didn't request this, you can safely ignore this email.
      </p>
      <p style="color:#707775; font-size:13px; margin:24px 0 0;">— BookLoop</p>
    </div>
    """
    return subject, html, text
