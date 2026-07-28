"""Sending the sign-in links.

Three providers, chosen with MAIL_PROVIDER:
  console  - prints the link to the server log (the default; great for testing)
  resend   - Resend's HTTP API, needs RESEND_API_KEY (free tier is plenty)
  smtp     - any SMTP server, needs SMTP_HOST/SMTP_USER/SMTP_PASSWORD

Sending never raises. If mail is broken the sign-in page still works and the
link is written to the log, so you are never locked out of your own site.
"""

import json
import logging
import os
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage

log = logging.getLogger("mailer")


def provider():
    return os.environ.get("MAIL_PROVIDER", "console").strip().lower()


def from_address():
    return os.environ.get("MAIL_FROM", "Valentine Cookbook <onboarding@resend.dev>")


def send(to_address, subject, body):
    """Returns True if the message was handed off to a real mail service."""
    choice = provider()
    try:
        if choice == "resend":
            return _send_resend(to_address, subject, body)
        if choice == "smtp":
            return _send_smtp(to_address, subject, body)
    except Exception as exc:                    # noqa: BLE001 - never break sign-in
        log.error("email send failed (%s): %s", choice, exc)
        _log_only(to_address, subject, body)
        return False
    _log_only(to_address, subject, body)
    return False


def _log_only(to_address, subject, body):
    log.warning(
        "\n===== EMAIL (not sent, provider=%s) =====\nTo: %s\nSubject: %s\n\n%s\n"
        "========================================",
        provider(), to_address, subject, body,
    )


def _send_resend(to_address, subject, body):
    key = os.environ.get("RESEND_API_KEY")
    if not key:
        raise RuntimeError("RESEND_API_KEY is not set")
    payload = json.dumps({
        "from": from_address(),
        "to": [to_address],
        "subject": subject,
        "text": body,
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "content-type": "application/json",
            "authorization": "Bearer %s" % key,
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status >= 300:
            raise RuntimeError("resend returned %s" % response.status)
    return True


def _send_smtp(to_address, subject, body):
    host = os.environ.get("SMTP_HOST")
    if not host:
        raise RuntimeError("SMTP_HOST is not set")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")

    message = EmailMessage()
    message["From"] = from_address()
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
    try:
        if port != 465:
            server.starttls()
        if user:
            server.login(user, password)
        server.send_message(message)
    finally:
        server.quit()
    return True
