from flask import Flask, render_template, request
import imaplib
import email
from email.header import decode_header
import re
import time
from config import EMAIL_ACCOUNT, EMAIL_PASSWORD, IMAP_SERVER, get_platform_email
from bs4 import BeautifulSoup
import ssl
import logging
import socket

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s:%(message)s')

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
    
    # Search for all emails from the user_email
    status, messages = mail.search(None, f'(FROM "{user_email}")')
    
    if status != "OK":
        logging.error("Failed to search emails.")
        return None

    email_ids = messages[0].split()
    if not email_ids:
        logging.info("No emails found.")
        return None

    # Fetch the latest email ID
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
    return msg

def extract_forwarded_email(body):
    """Extract the forwarded email content from the outer email body."""
    markers = [
        "---------- Forwarded message ----------",
        "Begin forwarded message:",
        "----- Original Message -----"
    ]

    for marker in markers:
        if marker in body:
            parts = body.split(marker, 1)
            if len(parts) > 1:
                return marker + parts[1]

    logging.warning("No forwarded email marker found. Returning full body.")
    return body

def extract_email_field(text, field_name):
    """Extract email address from a specific field in the email header."""
    pattern = fr"{re.escape(field_name)}.*?([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{{2,}})"
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip().lower()
    logging.warning(f"Email address not found for field: {field_name}")
    return None

def parse_bookmyshow_email(msg):
    """Parse BookMyShow email format."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_payload(decode=True).decode(errors="ignore")
                break
    else:
        body = msg.get_payload(decode=True).decode(errors="ignore")

    forwarded_email = extract_forwarded_email(body)
    if not forwarded_email:
        return None

    # Clean soft line breaks
    forwarded_email = forwarded_email.replace("=\n", "")

    # Extract required fields
    from_email = extract_email_field(forwarded_email, "From:")
    to_email = extract_email_field(forwarded_email, "To:")
    
    # Extract BookMyShow specific fields
    booking_id_match = re.search(r"BOOKING ID[:\s]+(\w+)", forwarded_email, re.IGNORECASE)
    venue_match = re.search(
        r'Venue\s+Directions.*?<.*?>.*?\n\s*([^\n<]+)',
        forwarded_email,
        re.DOTALL | re.IGNORECASE
    )
    datetime_match = re.search(
        r'Date\s*&\s*Time\s*\n\s*([^|]+\|[^<\n]+)',
        forwarded_email,
        re.IGNORECASE
    )
    quantity_match = re.search(
        r'Category\s+Quantity\s+Price.*?\n.*?\n(\d+)',
        forwarded_email,
        re.DOTALL | re.IGNORECASE
    )
    event_match = re.search(
        r'Subject:.*?Booking confirmed for\s+(.*?)\n',
        forwarded_email,
        re.IGNORECASE | re.DOTALL
    )

    return {
        "from_email": from_email,
        "to_email": to_email,
        "booking_id": booking_id_match.group(1).strip() if booking_id_match else None,
        "venue": venue_match.group(1).strip() if venue_match else None,
        "date_time": datetime_match.group(1).strip() if datetime_match else None,
        "event_name": event_match.group(1).strip() if event_match else None,
        "quantity": quantity_match.group(1).strip() if quantity_match else None
    }

def parse_zomato_email(msg):
    """Parse Zomato email format."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_payload(decode=True).decode(errors="ignore")
                break
    else:
        body = msg.get_payload(decode=True).decode(errors="ignore")

    forwarded_email = extract_forwarded_email(body)
    if not forwarded_email:
        return None

    # Clean soft line breaks
    forwarded_email = forwarded_email.replace("=\n", "")

    # Extract required fields
    from_email = extract_email_field(forwarded_email, "From:")
    to_email = extract_email_field(forwarded_email, "To:")
    
    # Extract Zomato specific fields
    booking_id_match = re.search(r"Ticket ID[:\s]+(\w+)", forwarded_email, re.IGNORECASE)
    event_match = re.search(r"You just scored tickets to\s+(.*?)(?:\n|$)", forwarded_email, re.IGNORECASE)
    date_match = re.search(r"(\w+,\s+\w+\s+\d+,\s+\d{4})", forwarded_email)
    quantity_match = re.search(r"RSVP x\s*(\d+)", forwarded_email, re.IGNORECASE)

    # Note: Venue might not be directly available in the email template shown
    venue = "Venue information not available in email"

    return {
        "from_email": from_email,
        "to_email": to_email,
        "booking_id": booking_id_match.group(1).strip() if booking_id_match else None,
        "venue": venue,
        "date_time": date_match.group(1).strip() if date_match else None,
        "event_name": event_match.group(1).strip() if event_match else None,
        "quantity": quantity_match.group(1).strip() if quantity_match else None
    }

def parse_paytminsider_email(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_payload(decode=True).decode(errors="ignore")
                break
    else:
        body = msg.get_payload(decode=True).decode(errors="ignore")

    # Log the raw body for debugging purposes
    logging.debug(f"Raw email body: {body}")

    # Clean soft line breaks that may appear in the email
    body = body.replace("=\n", "")

    # Extract email fields
    from_email = extract_email_field(body, "From:")
    to_email = extract_email_field(body, "To:")
    logging.info(f"Extracted from_email: {from_email}")
    logging.info(f"Extracted to_email: {to_email}")

    # Extract Booking ID
    booking_id_match = re.search(r"transaction reference\s*(\w+)", body, re.IGNORECASE)
    booking_id = booking_id_match.group(1).strip() if booking_id_match else None
    logging.info(f"Extracted booking_id: {booking_id}")

    # Refined venue extraction: Capture text after "Venue" up to "Get Directions"
    venue_match = re.search(
        r"Venue\s*\n([^\n]+(?:\n[^\n]+)*)\nGet Directions", body, re.IGNORECASE
    )
    if venue_match:
        # Capture the matched groups, join them with commas, and remove leading/trailing commas
        venue = ", ".join([line.strip() for line in venue_match.group(1).splitlines() if line]).strip(", ")
    else:
        venue = None
    logging.info(f"Extracted venue: {venue}")

    # Extract Date and Time
    date_match = re.search(r"Date\s*\n([^\n]+)", body, re.IGNORECASE)
    time_match = re.search(r"Time\s*\n([^\n]+)", body, re.IGNORECASE)
    date_time = f"{date_match.group(1).strip()} {time_match.group(1).strip()}" if date_match and time_match else None
    logging.info(f"Extracted date_time: {date_time}")

    # Extract Event Name
    event_match = re.search(r"Music\s*\n([^\n]+)", body, re.IGNORECASE)
    event_name = event_match.group(1).strip() if event_match else None
    logging.info(f"Extracted event_name: {event_name}")

    # Extract Quantity
    quantity_match = re.search(r"(\d+)\s*Ticket", body, re.IGNORECASE)
    quantity = quantity_match.group(1).strip() if quantity_match else "1"
    logging.info(f"Extracted quantity: {quantity}")

    return {
        "from_email": from_email,
        "to_email": to_email,
        "booking_id": booking_id,
        "venue": venue,
        "date_time": date_time,
        "event_name": event_name,
        "quantity": quantity
    }


def parse_email(msg, platform):
    """
    Parse the email message based on the selected platform.
    Returns a dictionary with extracted data.
    """
    if platform == 'bookmyshow':
        return parse_bookmyshow_email(msg)
    elif platform == 'zomato':
        return parse_zomato_email(msg)
    elif platform == 'paytminsider':
        return parse_paytminsider_email(msg)
    else:
        logging.error(f"Unsupported platform: {platform}")
        return None

@app.route("/", methods=["GET", "POST"])
def index():
    verification_result = None
    user_email = None
    platform = None
    expected_from_email = None

    if request.method == "POST":
        user_email = request.form.get("user_email")
        platform = request.form.get("platform")
        if platform:
            expected_from_email = get_platform_email(platform)
        logging.info(f"User Email Submitted: {user_email}, Platform: {platform}")

    return render_template("index.html", 
                         user_email=user_email,
                         platform=platform,
                         verification_result=verification_result,
                         expected_from_email=expected_from_email,
                         your_email=EMAIL_ACCOUNT)

@app.route("/confirm", methods=["POST"])
def confirm_route():
    user_email = request.form.get("user_email")
    platform = request.form.get("platform")
    
    if not user_email or not platform:
        logging.warning("User email or platform not provided.")
        return render_template("index.html", 
                             verification_result="User email and platform must be provided.",
                             expected_from_email=None,
                             your_email=EMAIL_ACCOUNT)

    expected_from_email = get_platform_email(platform)
    if not expected_from_email:
        logging.warning(f"Invalid platform selected: {platform}")
        return render_template("index.html", 
                             user_email=user_email,
                             platform=platform,
                             verification_result="Invalid platform selected.",
                             expected_from_email=None,
                             your_email=EMAIL_ACCOUNT)

    try:
        mail = connect_to_email()
    except Exception as e:
        logging.error(f"Failed to connect to email: {str(e)}")
        return render_template("index.html", 
                             user_email=user_email,
                             platform=platform,
                             verification_result=f"Failed to connect to email: {str(e)}",
                             expected_from_email=expected_from_email,
                             your_email=EMAIL_ACCOUNT)

    time.sleep(5)  # Wait for email to arrive

    msg = search_email(mail, user_email)
    if not msg:
        mail.logout()
        logging.warning(f"No forwarded email found for user: {user_email}")
        return render_template("index.html", 
                             user_email=user_email,
                             platform=platform,
                             verification_result="No forwarded email found. Please ensure you have forwarded the email correctly.",
                             expected_from_email=expected_from_email,
                             your_email=EMAIL_ACCOUNT)

    parsed_data = parse_email(msg, platform)
    mail.logout()

    if not parsed_data:
        logging.error("Failed to parse the forwarded email.")
        return render_template("index.html", 
                             user_email=user_email,
                             platform=platform,
                             verification_result="Failed to parse the forwarded email.",
                             expected_from_email=expected_from_email,
                             your_email=EMAIL_ACCOUNT)

    errors = []
    if parsed_data["from_email"] != expected_from_email.lower():
        errors.append(f"From email does not match. Expected: {expected_from_email}, Found: {parsed_data['from_email']}")

    if parsed_data["to_email"] != user_email.lower():
        errors.append(f"To email does not match. Expected: {user_email}, Found: {parsed_data['to_email']}")

    for field in ["booking_id", "venue", "date_time", "event_name", "quantity"]:
        if not parsed_data[field]:
            errors.append(f"{field.replace('_', ' ').title()} not found in the email.")

    if errors:
        verification_result = "Verification failed:<br>" + "<br>".join(errors)
        logging.info("Verification failed.")
        return render_template("index.html", 
                             user_email=user_email,
                             platform=platform,
                             verification_result=verification_result,
                             expected_from_email=expected_from_email,
                             your_email=EMAIL_ACCOUNT)
    else:
        verification_result = "Verification successful! All details are valid."
        logging.info("Verification successful.")
        return render_template("index.html", 
                             user_email=user_email,
                             platform=platform,
                             verification_result=verification_result,
                             validated_data=parsed_data,
                             expected_from_email=expected_from_email,
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