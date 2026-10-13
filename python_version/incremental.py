import datetime
from typing import Dict, Any, Optional
from models import get_checkpoint, set_checkpoint, log_audit
from scanner import GmailHistoricalScanner
from drive_syncer import DriveSheetsSyncer

class GmailIncrementalScanner:
    """
    Scans for new incoming emails received after the historical cutoff timestamp.
    Appends new attachments to existing country rows in the Google Sheet without duplication.
    """

    def __init__(self):
        self.cutoff_iso = get_checkpoint("HISTORICAL_CUTOFF_ISO")
        self.syncer = DriveSheetsSyncer()

    def run_incremental_scan(self) -> Dict[str, Any]:
        """Runs an incremental scan from the last recorded scan timestamp."""
        # Convert cutoff ISO to Unix timestamp or date query
        last_sync = get_checkpoint("LAST_INCREMENTAL_SYNC_ISO") or self.cutoff_iso
        current_time_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Build query for messages newer than last_sync
        query = "has:attachment -in:spam -in:trash"
        if last_sync:
            try:
                dt = datetime.datetime.fromisoformat(last_sync)
                epoch = int(dt.timestamp())
                query += f" after:{epoch}"
            except Exception:
                pass

        log_audit("INCREMENTAL_START", level="INFO", details=f"Starting incremental scan with query: '{query}'")

        # 1. Scan new candidate messages
        scanner = GmailHistoricalScanner(search_query=query)
        scan_stats = scanner.run_historical_scan()

        # 2. Upload any newly discovered attachments to Drive
        upload_stats = self.syncer.sync_pending_uploads()

        # 3. Update the Results Spreadsheet
        sheet_result = self.syncer.update_country_registry_sheet()

        # Update last sync timestamp
        set_checkpoint("LAST_INCREMENTAL_SYNC_ISO", current_time_iso)
        log_audit("INCREMENTAL_COMPLETE", level="INFO", 
                  details=f"Incremental scan completed. Discovered: {scan_stats['attachments_discovered']}, Uploaded: {upload_stats['uploaded']}.")

        return {
            "scan_stats": scan_stats,
            "upload_stats": upload_stats,
            "spreadsheet_result": sheet_result,
            "synced_at": current_time_iso
        }

if __name__ == "__main__":
    inc = GmailIncrementalScanner()
    print("GmailIncrementalScanner ready.")
