# config.py

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

EMAIL_ACCOUNT = os.getenv("EMAIL_ACCOUNT")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
IMAP_SERVER = os.getenv("IMAP_SERVER")

# Platform-specific email configurations
PLATFORM_EMAILS = {
    'bookmyshow': 'tickets@bookmyshow.email',
    'zomato': 'eventsupport@zomato.com',
    'paytminsider': 'purchases@insider.in'
}

def get_platform_email(platform):
    return PLATFORM_EMAILS.get(platform)