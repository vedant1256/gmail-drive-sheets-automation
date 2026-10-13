import sys
import argparse
from typing import Dict, Any

# Ensure Windows terminal doesn't crash on unicode
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from database_loader import load_master_excel
from scanner import GmailHistoricalScanner
from drive_syncer import DriveSheetsSyncer
from incremental import GmailIncrementalScanner
from models import get_scan_statistics, get_country_documents_summary, get_checkpoint

def run_historical_pipeline():
    print("=" * 65)
    print("🚀 STARTING HISTORICAL GMAIL SCAN & DOCUMENT PIPELINE")
    print("=" * 65)

    # 1. Master Database
    print("\n[Step 1/4] Verifying Master Excel Database...")
    db_info = load_master_excel()
    print(f"✓ Master database loaded: {db_info['total_countries']} countries, "
          f"{db_info['total_unique_emails']} unique emails, {db_info['ambiguous_emails_count']} ambiguous.")

    # 2. Gmail Scanner
    print("\n[Step 2/4] Scanning Historical Gmail Messages (Read & Unread)...")
    scanner = GmailHistoricalScanner()
    scan_stats = scanner.run_historical_scan(
        progress_callback=lambda p: print(f"  • Message {p['current_message_id']} from '{p['sender']}' -> "
                                          f"Country: {p['matched_country'] or 'Unmatched'} (Attachments: {p['attachments_found']})")
    )
    print(f"✓ Scan completed: {scan_stats['messages_evaluated']} evaluated, "
          f"{scan_stats['messages_matched']} matched, {scan_stats['attachments_discovered']} attachments found.")

    # 3. Google Drive Uploads
    print("\n[Step 3/4] Uploading Discovered Attachments to Google Drive...")
    syncer = DriveSheetsSyncer()
    upload_stats = syncer.sync_pending_uploads()
    print(f"✓ Drive upload sync complete: {upload_stats['uploaded']} uploaded, {upload_stats['failed']} failed.")

    # 4. Google Sheets Registry Update
    print("\n[Step 4/4] Updating Country Document Registry in Google Sheets...")
    sheet_info = syncer.update_country_registry_sheet()
    print(f"✓ Registry successfully updated: Spreadsheet ID: {sheet_info['spreadsheet_id']}")
    print(f"✓ Formatted exactly ONE row per country across all {sheet_info['total_countries_written']} countries.")

    print("\n" + "=" * 65)
    print("🎉 PIPELINE COMPLETED SUCCESSFULLY!")
    print("=" * 65)

def run_incremental_pipeline():
    print("=" * 65)
    print("🔄 STARTING INCREMENTAL SCAN FOR NEW MESSAGES")
    print("=" * 65)
    inc = GmailIncrementalScanner()
    res = inc.run_incremental_scan()
    print(f"✓ Incremental scan complete. Discovered: {res['scan_stats']['attachments_discovered']}, "
          f"Uploaded: {res['upload_stats']['uploaded']}")

def print_status():
    print("=" * 65)
    print("📊 AUTOMATION SYSTEM STATUS & METRICS")
    print("=" * 65)
    stats = get_scan_statistics()
    cutoff = get_checkpoint("HISTORICAL_CUTOFF_ISO") or "Not started"
    completed = get_checkpoint("HISTORICAL_SCAN_COMPLETED") or "False"
    sheet_id = get_checkpoint("REGISTRY_SPREADSHEET_ID") or "Default"

    print(f"Historical Cutoff Timestamp:  {cutoff}")
    print(f"Historical Scan Completed:    {completed}")
    print(f"Registry Spreadsheet ID:      {sheet_id}")
    print("-" * 65)
    print(f"Total Messages Scanned:       {stats['total_messages_scanned']}")
    print(f"Messages Matched to Country:  {stats['matched_messages']}")
    print(f"Ambiguous Sender Messages:    {stats['ambiguous_messages']}")
    print(f"Attachments Uploaded to Drive:{stats['uploaded_attachments']}")
    print(f"Countries With Documents:     {stats['countries_with_docs']}")
    print(f"Failed Attachments:           {stats['failed_attachments']}")
    print("=" * 65)

def main():
    parser = argparse.ArgumentParser(description="Gmail Historical Scanner & Country Document Automation")
    parser.add_argument("--scan", choices=["historical", "incremental"], help="Execute historical or incremental scan")
    parser.add_argument("--load-db", action="store_true", help="Reload master database from Excel")
    parser.add_argument("--sync-drive", action="store_true", help="Sync pending attachments to Google Drive")
    parser.add_argument("--sync-sheet", action="store_true", help="Update Google Sheets Document Registry")
    parser.add_argument("--status", action="store_true", help="Display system statistics and metrics")

    args = parser.parse_args()

    if args.load_db:
        load_master_excel()
        print("✓ Master database reloaded.")
    elif args.scan == "historical":
        run_historical_pipeline()
    elif args.scan == "incremental":
        run_incremental_pipeline()
    elif args.sync_drive:
        syncer = DriveSheetsSyncer()
        syncer.sync_pending_uploads()
    elif args.sync_sheet:
        syncer = DriveSheetsSyncer()
        syncer.update_country_registry_sheet()
    elif args.status or len(sys.argv) == 1:
        print_status()

if __name__ == "__main__":
    main()
