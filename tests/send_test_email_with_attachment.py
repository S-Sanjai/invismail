
import smtplib
from email.message import EmailMessage
from pathlib import Path

# Replace with an actual active inbox created in InvisMail.
SENDER = "sender@example.com"
RECIPIENT = "a02d6e69f18cfbc4@invismail.test"

SMTP_HOST = "127.0.0.1"
SMTP_PORT = 2525

SCRIPT_DIR = Path(__file__).resolve().parent
attachment_path = SCRIPT_DIR / "test.pdf"

msg = EmailMessage()
msg["From"] = SENDER
msg["To"] = RECIPIENT
msg["Subject"] = "InvisMail Attachment Test"

msg.set_content("""
Hello!

This is a test email sent to InvisMail.

Please verify that the attachment is received and stored correctly.
""")

# Attach the file.
with attachment_path.open("rb") as file:
    msg.add_attachment(
        file.read(),
        maintype="text",
        subtype="plain",
        filename=attachment_path.name,
    )

# Send the email.
with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
    smtp.send_message(msg)

print("Test email with attachment sent successfully!")
