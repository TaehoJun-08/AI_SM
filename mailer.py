"""Sending email through Gmail (or any SMTP server). Needs MAIL_USERNAME and MAIL_APP_PASSWORD in .env -- a
Gmail App Password, not your normal password. Without them nothing is sent; in debug mode the email is printed
in the terminal instead, so a teammate without the password can still try verification locally."""
import os
import smtplib
from email.message import EmailMessage
from flask import current_app


def send(to, subject, body):
    """True if the email went out (or was printed, in debug mode without a mail password)."""
    user, password = os.getenv("MAIL_USERNAME"), os.getenv("MAIL_APP_PASSWORD")
    if not (user and password):
        if current_app.debug:
            print(f"\n----- email to {to} (not sent: no MAIL_USERNAME / MAIL_APP_PASSWORD in .env) -----\n"
                  f"{subject}\n\n{body}\n-----\n", flush=True)
            return True
        return False
    msg = EmailMessage()
    msg["From"] = f"Matchmaker <{user}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL(os.getenv("MAIL_SERVER", "smtp.gmail.com"), int(os.getenv("MAIL_PORT", "465")), timeout=15) as smtp:
            smtp.login(user, password.replace(" ", ""))      # Gmail shows app passwords in groups of four
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as e:
        current_app.logger.warning("Email to %s failed: %s", to, e)
        return False
