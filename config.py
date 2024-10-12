# config.py

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

EMAIL_ACCOUNT = os.getenv("EMAIL_ACCOUNT")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
IMAP_SERVER = os.getenv("IMAP_SERVER")
EXPECTED_FROM_EMAIL = os.getenv("EXPECTED_FROM_EMAIL")
