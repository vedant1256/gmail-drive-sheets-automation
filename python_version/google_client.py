import os
from pathlib import Path
from typing import Optional, Dict, Any
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource

from config import SCOPES, CREDENTIALS_FILE, TOKEN_FILE
from models import log_audit

class GoogleAuthManager:
    """Manages Google OAuth 2.0 lifecycle across Gmail, Google Drive, and Google Sheets."""

    def __init__(self, credentials_path: str = CREDENTIALS_FILE, token_path: str = TOKEN_FILE):
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)

        # Restore secrets from environment variables in cloud environments (e.g. Render)
        token_env = os.getenv("GOOGLE_TOKEN_JSON") or os.getenv("TOKEN_JSON_CONTENT")
        if token_env and not self.token_path.exists():
            try:
                self.token_path.parent.mkdir(parents=True, exist_ok=True)
                self.token_path.write_text(token_env.strip(), encoding="utf-8")
            except Exception as e:
                log_audit("AUTH_ENV_RESTORE_FAIL", level="WARNING", details=f"Failed to restore token from env: {e}")

        creds_env = os.getenv("GOOGLE_CREDENTIALS_JSON") or os.getenv("CREDENTIALS_JSON_CONTENT")
        if creds_env and not self.credentials_path.exists():
            try:
                self.credentials_path.parent.mkdir(parents=True, exist_ok=True)
                self.credentials_path.write_text(creds_env.strip(), encoding="utf-8")
            except Exception as e:
                log_audit("AUTH_ENV_RESTORE_FAIL", level="WARNING", details=f"Failed to restore credentials from env: {e}")

        self._creds: Optional[Credentials] = None
        self._gmail_service: Optional[Resource] = None
        self._drive_service: Optional[Resource] = None
        self._sheets_service: Optional[Resource] = None

    def get_credentials(self) -> Credentials:
        """Retrieves valid OAuth credentials or triggers browser-based authorization flow."""
        if self._creds and self._creds.valid:
            return self._creds

        creds = None
        if self.token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
            except Exception as e:
                log_audit("AUTH_ERROR", level="WARNING", details=f"Existing token could not be loaded: {e}")

        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    log_audit("AUTH_REFRESH", level="INFO", details="Successfully refreshed OAuth token.")
                except Exception as refresh_err:
                    log_audit("AUTH_REFRESH_FAIL", level="WARNING", details=f"Token refresh failed: {refresh_err}. Re-authenticating.")
                    creds = None

            if not creds:
                if not self.credentials_path.exists():
                    raise FileNotFoundError(
                        f"Google OAuth client secrets file not found at: {self.credentials_path}\n"
                        f"Please place your Google Cloud 'credentials.json' (Desktop App type) in the project folder."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), SCOPES)
                creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
                log_audit("AUTH_NEW_LOGIN", level="INFO", details="New OAuth 2.0 authorization completed.")

            # Save the credentials for the next run
            with open(self.token_path, "w") as token_file:
                token_file.write(creds.to_json())

        self._creds = creds
        return self._creds

    def is_authenticated(self) -> bool:
        """Checks if valid credentials already exist without launching a browser prompt."""
        if self.token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
                return creds.valid or (creds.expired and creds.refresh_token is not None)
            except Exception:
                return False
        return False

    def get_gmail_service(self) -> Resource:
        if not self._gmail_service:
            creds = self.get_credentials()
            self._gmail_service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._gmail_service

    def get_drive_service(self) -> Resource:
        if not self._drive_service:
            creds = self.get_credentials()
            self._drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._drive_service

    def get_sheets_service(self) -> Resource:
        if not self._sheets_service:
            creds = self.get_credentials()
            self._sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        return self._sheets_service

# Global singleton instance
google_auth = GoogleAuthManager()
