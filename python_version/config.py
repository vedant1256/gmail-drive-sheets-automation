import os
from pathlib import Path
from dotenv import load_dotenv

# Base paths
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
ENV_PATH = BASE_DIR / ".env"

if ENV_PATH.exists():
    load_dotenv(ENV_PATH)
else:
    load_dotenv()

# Master Excel File Path
EXCEL_PATH = os.getenv("EXCEL_PATH", str(PROJECT_DIR / "Indian Embassies.xlsx"))

# Google Drive Destination Folder (Set in .env)
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "")

# Google Spreadsheet ID (Set in .env)
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")
SPREADSHEET_TITLE = os.getenv("SPREADSHEET_TITLE", "Indian Embassies - Gmail Document Registry")
SHEET_NAME = os.getenv("SHEET_NAME", "Database")

# Target User Email Address (Set in .env)
USER_EMAIL = os.getenv("USER_EMAIL", "")

# OAuth Credentials & Token Paths
CREDENTIALS_FILE = os.getenv("CREDENTIALS_FILE", str(BASE_DIR / "credentials.json"))
TOKEN_FILE = os.getenv("TOKEN_FILE", str(BASE_DIR / "token.json"))

# SQLite Database Path for Idempotency, Progress, and Audit
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "automation.db"))

# Gmail Search Configurations
# Initial scan includes read & unread, inbox & archived; excludes spam and trash
HISTORICAL_SEARCH_QUERY = os.getenv("HISTORICAL_SEARCH_QUERY", "has:attachment -in:spam -in:trash")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))

# Attachment Filters
MAX_ATTACHMENT_SIZE_BYTES = int(os.getenv("MAX_ATTACHMENT_SIZE_MB", "25")) * 1024 * 1024
IGNORE_INLINE_IMAGES = os.getenv("IGNORE_INLINE_IMAGES", "True").lower() in ("true", "1", "t")
ALLOWED_EXTENSIONS = [
    "pdf", "doc", "docx", "xls", "xlsx", "csv", "odt", "ods", "odp",
    "ppt", "pptx", "txt", "rtf", "zip", "rar", "7z", "tar", "gz",
    "jpg", "jpeg", "png"
]

# OAuth Scopes: Least privilege; read-only for Gmail
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets"
]
