"""Resend-backed transactional email. Falls back to console.log when RESEND_API_KEY is not set."""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_FROM = os.getenv("RESEND_FROM_EMAIL", "Shortlistly <onboarding@resend.dev>")
APP_URL = os.getenv("APP_URL", "https://shortlistly.co.uk").rstrip("/")
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "gptc2903@gmail.com")
RESEND_URL = "https://api.resend.com/emails"


def _send(to: str, subject: str, html: str, text: str) -> bool:
    """Send via Resend; if no key, log the message to stdout so dev flows still work."""
    if not RESEND_API_KEY:
        logger.warning(
            "RESEND_API_KEY not set — printing email to console instead of sending.\n"
            "  To: %s\n  Subject: %s\n  Body:\n%s",
            to, subject, text,
        )
        return True

    try:
        resp = httpx.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": RESEND_FROM, "to": [to], "subject": subject, "html": html, "text": text},
            timeout=10.0,
        )
        if resp.status_code >= 400:
            logger.error("Resend API error %s: %s", resp.status_code, resp.text)
            return False
        return True
    except Exception as exc:
        logger.error("Could not send email via Resend: %s", exc)
        return False


# ── HTML wrapper for branded emails ───────────────────────────────────────────

def _wrap_html(title: str, body_html: str) -> str:
    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#0f1115;font-family:'Plus Jakarta Sans',system-ui,-apple-system,sans-serif;color:#e8edf5;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0f1115;padding:40px 16px;">
  <tr><td align="center">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#15181f;border-radius:14px;border:1px solid rgba(255,255,255,0.06);padding:36px;">
      <tr><td>
        <p style="margin:0 0 8px;font-size:0.74rem;font-weight:700;letter-spacing:0.16em;text-transform:uppercase;color:#5ee4ff;">Shortlistly</p>
        <h1 style="margin:0 0 18px;font-size:1.6rem;line-height:1.25;color:#f4f6fb;font-weight:800;">{title}</h1>
        {body_html}
        <p style="margin:32px 0 0;font-size:0.78rem;color:rgba(184,192,212,0.5);line-height:1.5;">
          Sent by Shortlistly. Questions? Reply to this email or write to
          <a href="mailto:{SUPPORT_EMAIL}" style="color:#5ee4ff;text-decoration:none;">{SUPPORT_EMAIL}</a>.
        </p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""


def send_verification_email(to_email: str, token: str) -> bool:
    verify_url = f"{APP_URL}/verify?token={token}"
    subject = "Verify your Shortlistly email"
    text = (
        f"Welcome to Shortlistly.\n\n"
        f"Please verify your email address by opening this link:\n{verify_url}\n\n"
        f"If you didn't sign up, you can safely ignore this email."
    )
    body = f"""
      <p style="margin:0 0 18px;font-size:0.98rem;line-height:1.6;color:rgba(232,237,245,0.88);">
        Welcome aboard. Please verify your email address so you don't lose access to your
        scan history and account.
      </p>
      <p style="margin:0 0 24px;">
        <a href="{verify_url}" style="display:inline-block;background:linear-gradient(135deg,#5ee4ff,#a78bfa);color:#0f1115;text-decoration:none;font-weight:700;padding:13px 26px;border-radius:999px;font-size:0.95rem;">Verify my email</a>
      </p>
      <p style="margin:0 0 6px;font-size:0.84rem;color:rgba(184,192,212,0.65);">Or copy this link into your browser:</p>
      <p style="margin:0 0 12px;font-size:0.82rem;word-break:break-all;"><a href="{verify_url}" style="color:#5ee4ff;text-decoration:none;">{verify_url}</a></p>
      <p style="margin:0;font-size:0.84rem;color:rgba(184,192,212,0.55);">If you didn't sign up for Shortlistly, you can ignore this email.</p>
    """
    return _send(to_email, subject, _wrap_html("Verify your email", body), text)


def send_password_reset_email(to_email: str, token: str) -> bool:
    reset_url = f"{APP_URL}/reset-password?token={token}"
    subject = "Reset your Shortlistly password"
    text = (
        f"We received a request to reset your Shortlistly password.\n\n"
        f"Open this link to choose a new one (expires in 1 hour):\n{reset_url}\n\n"
        f"If you didn't request this, you can safely ignore this email — your password won't change."
    )
    body = f"""
      <p style="margin:0 0 18px;font-size:0.98rem;line-height:1.6;color:rgba(232,237,245,0.88);">
        We received a request to reset your Shortlistly password. Click the button below to
        choose a new one. This link expires in <strong>1 hour</strong>.
      </p>
      <p style="margin:0 0 24px;">
        <a href="{reset_url}" style="display:inline-block;background:linear-gradient(135deg,#5ee4ff,#a78bfa);color:#0f1115;text-decoration:none;font-weight:700;padding:13px 26px;border-radius:999px;font-size:0.95rem;">Reset my password</a>
      </p>
      <p style="margin:0 0 6px;font-size:0.84rem;color:rgba(184,192,212,0.65);">Or copy this link into your browser:</p>
      <p style="margin:0 0 12px;font-size:0.82rem;word-break:break-all;"><a href="{reset_url}" style="color:#5ee4ff;text-decoration:none;">{reset_url}</a></p>
      <p style="margin:0;font-size:0.84rem;color:rgba(184,192,212,0.55);">If you didn't request this, ignore this email — your password won't be changed.</p>
    """
    return _send(to_email, subject, _wrap_html("Reset your password", body), text)
