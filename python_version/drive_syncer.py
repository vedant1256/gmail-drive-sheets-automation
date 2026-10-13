import io
import base64
import time
from typing import Dict, List, Any, Optional
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError

from config import DRIVE_FOLDER_ID, SPREADSHEET_TITLE, SHEET_NAME, SPREADSHEET_ID
from models import (
    get_pending_attachments, update_attachment_status,
    get_country_documents_summary, log_audit, set_checkpoint, get_checkpoint
)
from google_client import google_auth

class DriveSheetsSyncer:
    """
    Uploads pending attachments to Google Drive and builds/updates the
    one-row-per-country Document Registry in Google Sheets.
    """

    def __init__(self, folder_id: str = DRIVE_FOLDER_ID):
        self.folder_id = folder_id
        self.drive_service = google_auth.get_drive_service()
        self.gmail_service = google_auth.get_gmail_service()
        self.sheets_service = google_auth.get_sheets_service()

    def sync_pending_uploads(self) -> Dict[str, int]:
        """Uploads all PENDING attachments from Gmail to Google Drive."""
        pending_list = get_pending_attachments()
        stats = {"uploaded": 0, "failed": 0, "skipped": 0}

        if not pending_list:
            return stats

        # Pre-fetch existing filenames in destination folder once
        existing_names = set()
        try:
            pt = None
            while True:
                res = self.drive_service.files().list(
                    q=f"'{self.folder_id}' in parents and trashed = false",
                    spaces="drive",
                    fields="nextPageToken, files(name)",
                    pageSize=1000,
                    pageToken=pt
                ).execute()
                for f in res.get("files", []):
                    existing_names.add(f.get("name"))
                pt = res.get("nextPageToken")
                if not pt:
                    break
        except Exception as e:
            log_audit("DRIVE_LIST_WARN", level="WARNING", details=str(e))

        for att in pending_list:
            msg_id = att["message_id"]
            att_id = att["attachment_id"]
            orig_filename = att["original_filename"]
            mime_type = att["mime_type"] or "application/octet-stream"

            try:
                # 1. Fetch attachment binary data from Gmail
                raw_data = self._download_attachment_data(msg_id, att_id)
                if not raw_data:
                    update_attachment_status(msg_id, att_id, "FAILED", error_details="Empty data received from Gmail")
                    stats["failed"] += 1
                    continue

                # 2. Generate safe non-colliding filename in destination folder
                safe_name = self._generate_unique_drive_filename(orig_filename, existing_names)

                # 3. Upload to Google Drive
                drive_file = self._upload_to_drive(safe_name, raw_data, mime_type, msg_id)
                file_id = drive_file["id"]
                web_url = drive_file.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"

                # 4. Mark status as UPLOADED in SQLite
                update_attachment_status(
                    msg_id, att_id, "UPLOADED",
                    drive_file_id=file_id, drive_web_url=web_url
                )
                log_audit("DRIVE_UPLOAD_SUCCESS", level="INFO", message_id=msg_id,
                          country_name=att["country_name"], filename=safe_name,
                          drive_file_id=file_id, details=f"Uploaded to Drive: {web_url}")
                stats["uploaded"] += 1

            except Exception as e:
                err_msg = f"Failed to upload {orig_filename}: {e}"
                update_attachment_status(msg_id, att_id, "FAILED", error_details=err_msg)
                log_audit("DRIVE_UPLOAD_FAIL", level="ERROR", message_id=msg_id,
                          filename=orig_filename, details=err_msg)
                stats["failed"] += 1

        return stats

    def _download_attachment_data(self, message_id: str, attachment_id: str) -> Optional[bytes]:
        res = self.gmail_service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id
        ).execute()
        data = res.get("data")
        if data:
            return base64.urlsafe_b64decode(data)
        return None

    def _generate_unique_drive_filename(self, filename: str, existing_names: set) -> str:
        """Checks in-memory set of existing filenames and appends counter if needed."""
        safe_name = filename
        counter = 1

        dot_idx = filename.rfind(".")
        base_name = filename[:dot_idx] if dot_idx != -1 else filename
        extension = filename[dot_idx:] if dot_idx != -1 else ""

        while safe_name in existing_names:
            safe_name = f"{base_name} ({counter}){extension}"
            counter += 1

        existing_names.add(safe_name)
        return safe_name

    def _upload_to_drive(self, filename: str, data: bytes, mime_type: str, message_id: str) -> Dict[str, Any]:
        file_metadata = {
            "name": filename,
            "parents": [self.folder_id],
            "description": f"Uploaded by Gmail Automation. Source Message ID: {message_id}"
        }
        is_resumable = len(data) > 5 * 1024 * 1024
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=is_resumable)
        return self.drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, webViewLink, size"
        ).execute()

    # --- Google Sheets Registry Builder ---

    def ensure_results_spreadsheet(self) -> str:
        """Finds existing spreadsheet by ID, or creates a new spreadsheet named 'Indian Embassies - Gmail Document Registry'."""
        spreadsheet_id = get_checkpoint("REGISTRY_SPREADSHEET_ID") or SPREADSHEET_ID

        if spreadsheet_id and spreadsheet_id.strip():
            try:
                sheet_meta = self.sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
                return spreadsheet_id
            except Exception:
                pass

        # Create new spreadsheet
        spreadsheet_body = {
            "properties": {
                "title": SPREADSHEET_TITLE
            },
            "sheets": [
                {
                    "properties": {
                        "title": SHEET_NAME,
                        "gridProperties": {
                            "frozenRowCount": 1
                        }
                    }
                }
            ]
        }
        new_sheet = self.sheets_service.spreadsheets().create(
            body=spreadsheet_body, fields="spreadsheetId"
        ).execute()
        new_id = new_sheet["spreadsheetId"]
        set_checkpoint("REGISTRY_SPREADSHEET_ID", new_id)
        log_audit("SPREADSHEET_CREATED", level="INFO", details=f"Created new registry spreadsheet: {new_id}")
        return new_id

    def update_country_registry_sheet(self) -> Dict[str, Any]:
        """
        Updates the Google Sheet with exactly ONE row per country.
        Columns: Country Name | Email Address | Documents
        Formats multiple documents as clickable rich-text hyperlinks inside the Documents cell.
        """
        spreadsheet_id = self.ensure_results_spreadsheet()
        country_data = get_country_documents_summary()

        # Calculate maximum number of documents across all countries (at least 3 columns)
        max_docs = max([len(c.get("documents", [])) for c in country_data] + [3])

        # Dynamic Headers: Country Name | Source Type | Email Address | Document 1 | Document 2 | ...
        headers = ["Country Name", "Source Type", "Email Address"] + [f"Document {i}" for i in range(1, max_docs + 1)]
        total_cols = len(headers)

        # Prepare cell data rows
        rows_data = []

        # 1. Header Row
        header_row_values = []
        for h in headers:
            header_row_values.append({
                "userEnteredValue": {"stringValue": h},
                "userEnteredFormat": {
                    "textFormat": {"bold": True},
                    "backgroundColor": {"red": 0.9, "green": 0.93, "blue": 0.98}
                }
            })
        rows_data.append({"values": header_row_values})

        # 2. Data Rows (One per country, with individual Document columns)
        for c in country_data:
            c_name = c["country_name"]
            c_source = c.get("source_type") or "Indian Embassy"
            c_emails = c["official_emails"] or ""
            docs = c.get("documents", [])

            row_values = [
                {"userEnteredValue": {"stringValue": c_name}, "userEnteredFormat": {"textFormat": {"bold": True}}},
                {"userEnteredValue": {"stringValue": c_source}, "userEnteredFormat": {"textFormat": {"italic": True}}},
                {"userEnteredValue": {"stringValue": c_emails}}
            ]

            # Populate each Document column
            for doc_idx in range(max_docs):
                if doc_idx < len(docs):
                    doc = docs[doc_idx]
                    escaped_filename = doc["filename"].replace('"', '""')
                    formula_val = f'=HYPERLINK("{doc["url"]}", "{escaped_filename}")'
                    row_values.append({
                        "userEnteredValue": {"formulaValue": formula_val},
                        "userEnteredFormat": {
                            "textFormat": {
                                "underline": True,
                                "foregroundColor": {"red": 0.06, "green": 0.33, "blue": 0.8}
                            }
                        }
                    })
                else:
                    row_values.append({
                        "userEnteredValue": {"stringValue": ""}
                    })

            rows_data.append({"values": row_values})

        # Find target sheet metadata
        sheet_meta = self.sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        target_sheet_id = 0
        current_cols = 26
        for s in sheet_meta.get("sheets", []):
            if s["properties"]["title"] == SHEET_NAME:
                target_sheet_id = s["properties"]["sheetId"]
                current_cols = s["properties"].get("gridProperties", {}).get("columnCount", 26)
                break

        # If more columns are needed than current grid dimensions, expand sheet columns first
        if total_cols > current_cols:
            self.sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [{
                    "appendDimension": {
                        "sheetId": target_sheet_id,
                        "dimension": "COLUMNS",
                        "length": total_cols - current_cols
                    }
                }]}
            ).execute()

        batch_requests = []

        # Update cells with dynamic Document columns
        batch_requests.append({
            "updateCells": {
                "range": {
                    "sheetId": target_sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": len(rows_data),
                    "startColumnIndex": 0,
                    "endColumnIndex": total_cols
                },
                "rows": rows_data,
                "fields": "userEnteredValue,userEnteredFormat"
            }
        })

        # Auto-resize columns
        batch_requests.append({
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": target_sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": total_cols
                }
            }
        })

        self.sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": batch_requests}
        ).execute()

        # Update SQLite attachment status from UPLOADED to LINKED / COMPLETED
        with models_get_conn() as conn:
            conn.execute("UPDATE attachments SET status = 'COMPLETED' WHERE status = 'UPLOADED'")
            conn.commit()

        log_audit("SHEET_SYNC_SUCCESS", level="INFO", 
                  details=f"Synchronized {len(country_data)} country rows to spreadsheet {spreadsheet_id}")

        return {
            "spreadsheet_id": spreadsheet_id,
            "total_countries_written": len(country_data)
        }

def models_get_conn():
    from models import get_connection
    return get_connection()

if __name__ == "__main__":
    syncer = DriveSheetsSyncer()
    print("DriveSheetsSyncer initialized.")
