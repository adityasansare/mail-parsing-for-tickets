# test_imap.py

import imaplib
import email
from email.header import decode_header
from config import EMAIL_ACCOUNT, EMAIL_PASSWORD, IMAP_SERVER
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s')

def test_imap_connection():
    try:
        logging.info(f"Connecting to IMAP server: {IMAP_SERVER}")
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, 993)
        logging.info("Logging in...")
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        logging.info("Logged in successfully!")

        # List recent emails for debugging
        mail.select("inbox")
        status, messages = mail.search(None, 'ALL')
        if status != "OK":
            logging.error("Failed to retrieve emails.")
            mail.logout()
            return

        email_ids = messages[0].split()
        recent_email_ids = email_ids[-5:]  # Get last 5 emails
        logging.info("Listing recent emails:")
        for eid in recent_email_ids:
            status, msg_data = mail.fetch(eid, "(RFC822)")
            if status != "OK":
                logging.error(f"Failed to fetch email ID {eid.decode()}.")
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding if encoding else "utf-8")
            from_ = msg.get("From")
            to_ = msg.get("To")
            logging.info(f"ID: {eid.decode()}, From: {from_}, Subject: {subject}")

        mail.logout()
    except imaplib.IMAP4.error as e:
        logging.error(f"IMAP4 error: {str(e)}")
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    test_imap_connection()
