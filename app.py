from flask import Flask, render_template, request
import imaplib
import email
from email.header import decode_header
import re
import time
from config import EMAIL_ACCOUNT, EMAIL_PASSWORD, IMAP_SERVER, EXPECTED_FROM_EMAIL
from bs4 import BeautifulSoup
import ssl
import logging
import socket

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s:%(message)s')

# Log configuration values
logging.info(f"Configuration Loaded: IMAP_SERVER={IMAP_SERVER}, EMAIL_ACCOUNT={EMAIL_ACCOUNT}, EXPECTED_FROM_EMAIL={EXPECTED_FROM_EMAIL}")

def clean_text(text):
    """Clean text for use in filenames or other purposes."""
    return "".join(c if c.isalnum() else "_" for c in text)

def connect_to_email(retries=3, delay=5):
    """Connect to the IMAP server and log in with retry logic."""
    for attempt in range(1, retries + 1):
        try:
            logging.info(f"Attempt {attempt}: Connecting to IMAP server: {IMAP_SERVER}")
            context = ssl.create_default_context()
            mail = imaplib.IMAP4_SSL(IMAP_SERVER, 993, ssl_context=context)
            logging.info("Successfully connected to IMAP server.")
            
            logging.info("Logging in...")
            mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
            logging.info("Logged in successfully!")
            return mail
        except socket.gaierror as e:
            logging.error(f"Socket error (getaddrinfo failed): {e}")
        except imaplib.IMAP4.error as e:
            logging.error(f"IMAP4 error: {str(e)}")
        except Exception as e:
            logging.error(f"An unexpected error occurred: {str(e)}")
        
        if attempt < retries:
            logging.info(f"Retrying in {delay} seconds...")
            time.sleep(delay)
    
    raise Exception("Failed to connect to the IMAP server after multiple attempts.")

def search_email(mail, user_email):
    """
    Search for the latest forwarded email from user_email to EMAIL_ACCOUNT.
    Returns the email message object if found, else None.
    """
    mail.select("inbox")
    
    # Search for all emails from the user_email (do not limit to UNSEEN)
    status, messages = mail.search(None, f'(FROM "{user_email}")')
    
    if status != "OK":
        logging.error("Failed to search emails.")
        return None

    email_ids = messages[0].split()
    if not email_ids:
        logging.info("No emails found.")
        return None

    # Fetch the latest email ID from the user (the last one in the list)
    latest_email_id = email_ids[-1]
    status, msg_data = mail.fetch(latest_email_id, "(RFC822)")

    if status != "OK":
        logging.error("Failed to fetch the email.")
        return None

    msg = email.message_from_bytes(msg_data[0][1])

    # Decode the subject and check if the email is forwarded
    subject, encoding = decode_header(msg["Subject"])[0]
    if isinstance(subject, bytes):
        subject = subject.decode(encoding if encoding else "utf-8")

    if "Fwd:" not in subject:
        logging.info("The latest email is not a forwarded email.")
        return None

    logging.info(f"Email found: {subject}")

    # Extract the forwarded content
    forwarded_message = None
    if msg.is_multipart():
        # Check each part of the email for the forwarded content
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition"))

            if "message/rfc822" in content_type or "attachment" in disposition:
                # This part might be the forwarded message as an attachment
                try:
                    forwarded_message = email.message_from_bytes(part.get_payload(decode=True))
                    logging.info("Forwarded email detected as an attachment.")
                    break
                except Exception as e:
                    logging.error(f"Failed to parse forwarded email attachment: {e}")
            elif content_type == "text/plain":
                # Try to find forwarded message in the plain text body
                body = part.get_payload(decode=True).decode()
                if "Forwarded message" in body or "----- Forwarded message -----" in body:
                    logging.info("Forwarded message found in the plain text body.")
                    forwarded_message = body
                    break
    else:
        # Handle non-multipart emails
        body = msg.get_payload(decode=True).decode()
        if "Forwarded message" in body or "----- Forwarded message -----" in body:
            logging.info("Forwarded message found in a non-multipart email.")
            forwarded_message = body

    if forwarded_message:
        logging.info(f"Forwarded email content: {forwarded_message}")
        return msg  # Returning the original email object
    else:
        logging.info("No forwarded message content detected.")
        return None

def parse_email(msg):
    """
    Parse the email message to extract required information.
    Returns a dictionary with extracted data.
    """
    # Initialize variables
    from_email = to_email = booking_id = venue = date_time = event_name = quantity = None

    # Get the email body
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_payload(decode=True).decode(errors="ignore")
                break
    else:
        body = msg.get_payload(decode=True).decode(errors="ignore")

    logging.info("Email body extracted.")

    # Extract the forwarded email content
    forwarded_email = extract_forwarded_email(body)
    if not forwarded_email:
        logging.error("Forwarded email content not found.")
        return None

    # Clean soft line breaks
    forwarded_email = forwarded_email.replace("=\n", "")

    # Log forwarded_email content for debugging
    logging.debug(f"Forwarded email content after cleaning:\n{forwarded_email}")

    # Extract required fields using regex from the forwarded email
    from_email = extract_email_field(forwarded_email, "From:")
    to_email = extract_email_field(forwarded_email, "To:")
    
    booking_id_match = re.search(r"BOOKING ID[:\s]+(\w+)", forwarded_email, re.IGNORECASE)
    if booking_id_match:
        booking_id = booking_id_match.group(1).strip()
    else:
        logging.warning("Booking ID not found in the email.")

    # Updated Venue Extraction
    venue_match = re.search(
        r'Venue\s+Directions.*?<.*?>.*?\n\s*([^\n<]+)',
        forwarded_email,
        re.DOTALL | re.IGNORECASE
    )
    if venue_match:
        venue = venue_match.group(1).strip()
        logging.info(f"Venue extracted: {venue}")
    else:
        logging.warning("Venue not found in the email.")

    # Updated Date & Time Extraction
    datetime_match = re.search(
        r'Date\s*&\s*Time\s*\n\s*([^|]+\|[^<\n]+)',
        forwarded_email,
        re.IGNORECASE
    )
    if datetime_match:
        date_time = datetime_match.group(1).strip()
        logging.info(f"Date & Time extracted: {date_time}")
    else:
        logging.warning("Date & Time not found in the email.")

    # Extract Quantity
    quantity_match = re.search(
        r'Category\s+Quantity\s+Price.*?\n.*?\n(\d+)',
        forwarded_email,
        re.DOTALL | re.IGNORECASE
    )
    if quantity_match:
        quantity = quantity_match.group(1).strip()
        logging.info(f"Quantity extracted: {quantity}")
    else:
        logging.warning("Quantity not found in the email.")

    # Extract Event Name
    event_match = re.search(
        r'Subject:.*?Booking confirmed for\s+(.*?)\n',
        forwarded_email,
        re.IGNORECASE | re.DOTALL
    )
    if event_match:
        event_name = event_match.group(1).strip()
    else:
        logging.warning("Event name not found in the email subject.")

    return {
        "from_email": from_email,
        "to_email": to_email,
        "booking_id": booking_id,
        "venue": venue,
        "date_time": date_time,
        "event_name": event_name,
        "quantity": quantity
    }

def extract_forwarded_email(body):
    """
    Extract the forwarded email content from the outer email body.
    """
    markers = [
        "---------- Forwarded message ----------",
        "Begin forwarded message:",
        "-----Original Message-----"
    ]

    for marker in markers:
        if marker in body:
            parts = body.split(marker, 1)
            if len(parts) > 1:
                return marker + parts[1]

    logging.warning("No forwarded email marker found. Returning full body.")
    return body  # Return the whole body if no marker is found

def extract_email_field(text, field_name):
    """
    Extract email address from a specific field in the email header.
    """
    pattern = fr"{re.escape(field_name)}.*?([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{{2,}})"
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip().lower()
    logging.warning(f"Email address not found for field: {field_name}")
    return None

@app.route("/", methods=["GET", "POST"])
def index():
    verification_result = None
    user_email = None
    if request.method == "POST":
        user_email = request.form.get("user_email")
        logging.info(f"User Email Submitted: {user_email}")
    return render_template("index.html", user_email=user_email, 
                           verification_result=verification_result,
                           expected_from_email=EXPECTED_FROM_EMAIL,
                           your_email=EMAIL_ACCOUNT)

@app.route("/confirm", methods=["POST"])
def confirm_route():
    user_email = request.form.get("user_email")
    if not user_email:
        logging.warning("User email not provided.")
        return render_template("index.html", verification_result="User email not provided.",
                               expected_from_email=EXPECTED_FROM_EMAIL,
                               your_email=EMAIL_ACCOUNT)

    try:
        mail = connect_to_email()
    except Exception as e:
        logging.error(f"Failed to connect to email: {str(e)}")
        return render_template("index.html", user_email=user_email, 
                               verification_result=f"Failed to connect to email: {str(e)}",
                               expected_from_email=EXPECTED_FROM_EMAIL,
                               your_email=EMAIL_ACCOUNT)

    time.sleep(5)  # Wait for email to arrive

    msg = search_email(mail, user_email)
    if not msg:
        mail.logout()
        logging.warning(f"No forwarded email found for user: {user_email}")
        return render_template("index.html", user_email=user_email, 
                               verification_result="No forwarded email found. Please ensure you have forwarded the email correctly.",
                               expected_from_email=EXPECTED_FROM_EMAIL,
                               your_email=EMAIL_ACCOUNT)

    parsed_data = parse_email(msg)
    mail.logout()

    if not parsed_data:
        logging.error("Failed to parse the forwarded email.")
        return render_template("index.html", user_email=user_email, 
                               verification_result="Failed to parse the forwarded email.",
                               expected_from_email=EXPECTED_FROM_EMAIL,
                               your_email=EMAIL_ACCOUNT)

    errors = []
    if parsed_data["from_email"] != EXPECTED_FROM_EMAIL.lower():
        errors.append(f"From email does not match. Expected: {EXPECTED_FROM_EMAIL}, Found: {parsed_data['from_email']}")

    if parsed_data["to_email"] != user_email.lower():
        errors.append(f"To email does not match. Expected: {user_email}, Found: {parsed_data['to_email']}")

    for field in ["booking_id", "venue", "date_time", "event_name", "quantity"]:
        if not parsed_data[field]:
            errors.append(f"{field.replace('_', ' ').title()} not found in the email.")

    if errors:
        verification_result = "Verification failed:<br>" + "<br>".join(errors)
        logging.info("Verification failed.")
        return render_template("index.html", user_email=user_email, 
                               verification_result=verification_result,
                               expected_from_email=EXPECTED_FROM_EMAIL,
                               your_email=EMAIL_ACCOUNT)
    else:
        verification_result = "Verification successful! All details are valid."
        logging.info("Verification successful.")
        # Pass the validated details to the template
        return render_template("index.html", user_email=user_email, 
                               verification_result=verification_result,
                               validated_data=parsed_data,  # Pass the parsed data
                               expected_from_email=EXPECTED_FROM_EMAIL,
                               your_email=EMAIL_ACCOUNT)

@app.route("/test-email-connection", methods=["GET"])
def test_email_connection():
    try:
        mail = connect_to_email()
        mail.logout()
        return "Email connection successful!", 200
    except Exception as e:
        return f"Email connection failed: {e}", 500

if __name__ == "__main__":
    app.run(debug=True)
