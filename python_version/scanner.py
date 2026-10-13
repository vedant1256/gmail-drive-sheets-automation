import time
import base64
import datetime
from typing import Dict, List, Any, Optional, Tuple, Callable
from googleapiclient.errors import HttpError
import re

from config import (
    HISTORICAL_SEARCH_QUERY, BATCH_SIZE, 
    MAX_ATTACHMENT_SIZE_BYTES, IGNORE_INLINE_IMAGES, ALLOWED_EXTENSIONS
)
from models import (
    is_message_scanned, record_scanned_message, record_attachment,
    set_checkpoint, get_checkpoint, log_audit, get_connection
)
from database_loader import (
    lookup_sender, normalize_email, EMAIL_REGEX,
    get_all_country_names, register_export_source
)
from google_client import google_auth

class GmailHistoricalScanner:
    """
    Performs resilient, paginated historical scanning of Gmail messages,
    matching senders against the master embassy database and export inquiry rules,
    and extracting eligible attachments.
    """

    def __init__(self, search_query: str = HISTORICAL_SEARCH_QUERY):
        self.search_query = search_query
        self.service = google_auth.get_gmail_service()

    def run_historical_scan(
        self, 
        force_rescan: bool = False, 
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> Dict[str, Any]:
        """
        Executes or resumes a historical scan across all messages.
        Tracks page tokens and message IDs in SQLite to ensure safe restarts.
        """
        # Determine or restore scan cutoff
        scan_cutoff = get_checkpoint("HISTORICAL_CUTOFF_ISO")
        if not scan_cutoff:
            scan_cutoff = datetime.datetime.now(datetime.timezone.utc).isoformat()
            set_checkpoint("HISTORICAL_CUTOFF_ISO", scan_cutoff)
            log_audit("SCAN_INIT", level="INFO", details=f"Initialized historical scan with cutoff: {scan_cutoff}")

        if force_rescan:
            set_checkpoint("LAST_PAGE_TOKEN", "")
            set_checkpoint("HISTORICAL_SCAN_COMPLETED", "FALSE")
            with get_connection() as conn:
                conn.execute("DELETE FROM scanned_messages WHERE status = 'UNMATCHED'")
                conn.commit()
            log_audit("SCAN_RESET", level="INFO", details="Reset checkpoints and unmatched messages for full rescan.")

        stats = {
            "messages_evaluated": 0,
            "messages_matched": 0,
            "messages_unmatched": 0,
            "messages_ambiguous": 0,
            "attachments_discovered": 0,
            "attachments_skipped": 0,
            "is_complete": False
        }

        page_token = get_checkpoint("LAST_PAGE_TOKEN")
        query = self.search_query

        log_audit("SCAN_START", level="INFO", details=f"Starting Gmail query: '{query}' (Resume page_token: {page_token})")

        while True:
            try:
                # 1. Fetch page of message stubs
                request = self.service.users().messages().list(
                    userId="me",
                    q=query,
                    maxResults=BATCH_SIZE,
                    pageToken=page_token
                )
                response = request.execute()
                messages_stubs = response.get("messages", [])

                if not messages_stubs:
                    stats["is_complete"] = True
                    set_checkpoint("HISTORICAL_SCAN_COMPLETED", "TRUE")
                    set_checkpoint("LAST_PAGE_TOKEN", "")
                    log_audit("SCAN_COMPLETE", level="INFO", details=f"Historical scan complete. Evaluated {stats['messages_evaluated']} messages.")
                    break

                for stub in messages_stubs:
                    msg_id = stub["id"]

                    # Check if message was already scanned in a prior run
                    if not force_rescan and is_message_scanned(msg_id):
                        stats["messages_evaluated"] += 1
                        continue

                    # Fetch full message details
                    msg_detail = self._fetch_message_with_retry(msg_id)
                    if not msg_detail:
                        continue

                    stats["messages_evaluated"] += 1
                    eval_result = self._evaluate_message(msg_detail)

                    if eval_result["matched_country"]:
                        stats["messages_matched"] += 1
                    elif eval_result["is_ambiguous"]:
                        stats["messages_ambiguous"] += 1
                    else:
                        stats["messages_unmatched"] += 1

                    stats["attachments_discovered"] += eval_result["attachments_count"]

                    if progress_callback:
                        progress_callback({
                            "current_message_id": msg_id,
                            "sender": eval_result["sender_normalized"],
                            "matched_country": eval_result["matched_country"],
                            "source_type": eval_result["source_type"],
                            "attachments_found": eval_result["attachments_count"],
                            "total_evaluated": stats["messages_evaluated"]
                        })

                # Save pagination checkpoint
                page_token = response.get("nextPageToken")
                set_checkpoint("LAST_PAGE_TOKEN", page_token or "")

                if not page_token:
                    stats["is_complete"] = True
                    set_checkpoint("HISTORICAL_SCAN_COMPLETED", "TRUE")
                    log_audit("SCAN_COMPLETE", level="INFO", details=f"No further pages. Evaluated {stats['messages_evaluated']} messages.")
                    break

            except HttpError as http_err:
                log_audit("SCAN_HTTP_ERROR", level="ERROR", details=f"Gmail API HTTP Error: {http_err}")
                time.sleep(5) # Backoff before retry
                break
            except Exception as e:
                log_audit("SCAN_FATAL_ERROR", level="ERROR", details=f"Fatal error during scan: {e}")
                raise

        return stats

    def _fetch_message_with_retry(self, message_id: str, max_retries: int = 3) -> Optional[Dict[str, Any]]:
        for attempt in range(max_retries):
            try:
                return self.service.users().messages().get(
                    userId="me", id=message_id, format="full"
                ).execute()
            except HttpError as e:
                if e.resp.status in [429, 500, 503]:
                    delay = (2 ** attempt) + 1
                    time.sleep(delay)
                else:
                    log_audit("MSG_FETCH_ERROR", level="ERROR", message_id=message_id, details=str(e))
                    return None
            except Exception as e:
                log_audit("MSG_FETCH_FAIL", level="ERROR", message_id=message_id, details=str(e))
                return None
        return None

    def _evaluate_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluates headers, matches sender against master Excel and export rules, and registers attachments."""
        msg_id = message["id"]
        thread_id = message.get("threadId", "")
        payload = message.get("payload", {})
        headers = payload.get("headers", [])
        snippet = message.get("snippet", "")

        # Extract headers
        sender_raw = ""
        subject = ""
        date_raw = ""

        for h in headers:
            name = h.get("name", "").lower()
            if name == "from":
                sender_raw = h.get("value", "")
            elif name == "subject":
                subject = h.get("value", "")
            elif name == "date":
                date_raw = h.get("value", "")

        # Extract and normalize sender email
        em_match = EMAIL_REGEX.search(sender_raw)
        sender_normalized = normalize_email(em_match.group(0)) if em_match else sender_raw.strip().lower()

        # Discover attachments in message payload
        discovered_attachments = self._extract_eligible_attachments(payload)
        has_attachments = len(discovered_attachments) > 0

        # Intelligent matching across embassies and export inquiries
        matched_country, source_type, is_ambiguous, error_notes = lookup_sender(
            sender_raw=sender_raw,
            subject=subject,
            body_snippet=snippet,
            has_attachments=has_attachments
        )

        msg_status = "PENDING"
        if is_ambiguous:
            msg_status = "AMBIGUOUS"
            log_audit("AMBIGUOUS_SENDER", level="WARNING", message_id=msg_id, sender_email=sender_normalized, details=error_notes)
        elif matched_country:
            msg_status = "MATCHED"
        else:
            msg_status = "UNMATCHED"

        # Record message in database
        record_scanned_message({
            "message_id": msg_id,
            "thread_id": thread_id,
            "sender_raw": sender_raw,
            "sender_normalized": sender_normalized,
            "subject": subject,
            "date_received": date_raw,
            "matched_country": matched_country,
            "source_type": source_type or "Indian Embassy",
            "is_ambiguous": is_ambiguous,
            "has_attachments": has_attachments,
            "status": msg_status,
            "error_details": error_notes
        })

        # If matched to a valid entity and has attachments, record attachments as PENDING
        if matched_country and has_attachments:
            for att in discovered_attachments:
                record_attachment({
                    "message_id": msg_id,
                    "country_name": matched_country,
                    "attachment_id": att["attachment_id"],
                    "original_filename": att["filename"],
                    "sanitized_filename": att["filename"],
                    "mime_type": att["mime_type"],
                    "size_bytes": att["size_bytes"],
                    "status": "PENDING"
                })

        return {
            "sender_normalized": sender_normalized,
            "matched_country": matched_country,
            "source_type": source_type,
            "is_ambiguous": is_ambiguous,
            "attachments_count": len(discovered_attachments) if matched_country else 0
        }

    def _extract_eligible_attachments(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Recursively traverses MIME parts to find eligible file attachments."""
        eligible = []

        def traverse(parts):
            for part in parts:
                filename = part.get("filename", "")
                body = part.get("body", {})
                att_id = body.get("attachmentId")
                size = body.get("size", 0)
                mime = part.get("mimeType", "")
                headers = part.get("headers", [])

                # Check if this part is an attachment
                if filename and att_id:
                    # Filter: ignore tiny inline images if configured
                    if IGNORE_INLINE_IMAGES:
                        is_image = mime.startswith("image/") or any(filename.lower().endswith(f".{ext}") for ext in ["png", "jpg", "jpeg", "gif", "ico", "bmp", "svg"])
                        if is_image:
                            # Signature icons, logos, tracking pixels (< 25KB)
                            if size < 25600:
                                continue
                            is_disposition_inline = False
                            for h in headers:
                                h_name = h.get("name", "").lower()
                                h_val = h.get("value", "").lower()
                                if h_name == "content-disposition" and "inline" in h_val and "attachment" not in h_val:
                                    is_disposition_inline = True
                            if is_disposition_inline and size < 51200:
                                continue

                    # Filter: size limit
                    if size > MAX_ATTACHMENT_SIZE_BYTES:
                        continue

                    # Filter: allowed extension
                    if ALLOWED_EXTENSIONS:
                        dot_idx = filename.rfind(".")
                        if dot_idx == -1:
                            continue
                        ext = filename[dot_idx + 1:].lower()
                        if ext not in ALLOWED_EXTENSIONS:
                            continue

                    eligible.append({
                        "filename": filename,
                        "attachment_id": att_id,
                        "size_bytes": size,
                        "mime_type": mime
                    })

                # Recurse through multipart children
                if "parts" in part:
                    traverse(part["parts"])

        if "parts" in payload:
            traverse(payload["parts"])

        return eligible

if __name__ == "__main__":
    scanner = GmailHistoricalScanner()
    print("GmailHistoricalScanner ready.")
