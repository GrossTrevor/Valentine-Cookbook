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

# urllib introduces itself as "Python-urllib/3.x", and Cloudflare -- which sits
# in front of Resend's API -- rejects that signature outright with "error code:
# 1010" before the request ever reaches Resend. Any ordinary User-Agent gets
# through. Without this, every email fails and nothing appears in Resend's own
# logs, because from their side the request never happened.
USER_AGENT = "ValentineCookbook/1.0 (+https://valentinecookbook.com)"


def provider():
    return os.environ.get("MAIL_PROVIDER", "console").strip().lower()


def from_address():
    return os.environ.get("MAIL_FROM", "Valentine Cookbook <onboarding@resend.dev>")


def describe():
    """One line saying how mail is set up, written to the log on startup.

    Without this, a misconfigured provider looks exactly like a working one
    until somebody notices no email ever arrived.
    """
    choice = provider()
    if choice == "resend":
        return "provider=resend from=%r api_key=%s" % (
            from_address(),
            "set" if os.environ.get("RESEND_API_KEY") else "MISSING",
        )
    if choice == "smtp":
        return "provider=smtp host=%s from=%r" % (
            os.environ.get("SMTP_HOST") or "MISSING", from_address()
        )
    return "provider=console -- no email is sent, links are written to this log"


def send(to_address, subject, body):
    """Returns True if the message was handed off to a real mail service.

    Every outcome is logged, including success. A silent success is worse than
    useless when someone is standing in front of an empty inbox trying to work
    out whether the site even tried.
    """
    choice = provider()
    try:
        if choice == "resend":
            message_id = _send_resend(to_address, subject, body)
            log.info(
                "sign-in email accepted by Resend for %s (id=%s). Accepted is "
                "not delivered -- check the Resend dashboard for the outcome.",
                to_address, message_id,
            )
            return True
        if choice == "smtp":
            _send_smtp(to_address, subject, body)
            log.info("sign-in email handed to SMTP server for %s", to_address)
            return True
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
            "user-agent": USER_AGENT,
            "accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status >= 300:
                raise RuntimeError("Resend returned %s" % response.status)
            try:
                # Resend's message id makes a send traceable in their dashboard.
                return json.loads(response.read().decode("utf-8")).get("id", "?")
            except (ValueError, UnicodeDecodeError):
                return "?"
    except urllib.error.HTTPError as exc:
        # The status code alone is not actionable: a 403 could be an unverified
        # domain, a restricted key, or a from-address that is not allowed, and
        # Resend says which in the response body. Losing that turns a
        # thirty-second fix into a guessing game.
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace").strip()[:400]
        except Exception:                       # noqa: BLE001 - best effort only
            pass
        raise RuntimeError(
            "Resend rejected the message with HTTP %s: %s" % (exc.code, detail or "(no detail)")
        )


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
