import smtplib
from email.message import EmailMessage

msg = EmailMessage()

msg["From"] = "sender@example.com"
msg["To"] = "12e034a3636200a1@invismail.test"
msg["Subject"] = "Test Email With Attachment"


msg.set_content("Hello! This email was processed by InvisMail. to checke if the mail is sent to the newly generated email address.")

with smtplib.SMTP("127.0.0.1", 2525) as smtp_server:
    # smtp_server.set_debuglevel(1)  # Enable debug output for the SMTP session
    smtp_server.send_message(msg)

print("Test email sent successfully.")
