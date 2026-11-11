import smtplib
from email.message import EmailMessage

msg = EmailMessage()

msg["From"] = "sender@example.com"
msg["To"] = "2eb15ba3299965a5@invismail.test1"
msg["Subject"] = "Test Email"

msg.set_content("This is a test email.")

with smtplib.SMTP("127.0.0.1", 2525) as smtp_server:
    # smtp_server.set_debuglevel(1)  # Enable debug output for the SMTP session
    smtp_server.send_message(msg)

print("Test email sent successfully.")
