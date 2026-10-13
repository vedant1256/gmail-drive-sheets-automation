import streamlit as st
import pandas as pd
import datetime
from pathlib import Path

from config import (
    EXCEL_PATH, DRIVE_FOLDER_ID, SPREADSHEET_ID,
    SPREADSHEET_TITLE, SHEET_NAME, CREDENTIALS_FILE
)
from database_loader import load_master_excel
from google_client import google_auth
from models import (
    get_connection, get_scan_statistics, get_country_documents_summary,
    get_checkpoint, set_checkpoint
)
from scanner import GmailHistoricalScanner
from drive_syncer import DriveSheetsSyncer
from incremental import GmailIncrementalScanner

st.set_page_config(
    page_title="Gmail Country Document Registry",
    page_icon="🌍",
    layout="wide"
)

st.title("🌍 Gmail Historical Scanner & Country Document Registry")
st.markdown("Automated ingestion of historical & incoming Gmail attachments organized country-wise by Indian Embassies master database.")

# --- Sidebar Configuration & Auth ---
with st.sidebar:
    st.header("⚙️ System Configuration")
    
    # Auth status
    is_authed = google_auth.is_authenticated()
    if is_authed:
        st.success(" Google Account Authenticated")
    else:
        st.warning("⚠️ Not Authenticated")
        if st.button("🔐 Authenticate with Google"):
            try:
                google_auth.get_credentials()
                st.success("Authentication successful! Refreshing...")
                st.rerun()
            except Exception as e:
                st.error(f"Auth error: {e}")

    st.divider()
    excel_path_input = st.text_input("Master Excel File", value=EXCEL_PATH)
    drive_folder_input = st.text_input("Drive Destination Folder ID", value=DRIVE_FOLDER_ID)
    sheet_id_input = st.text_input("Results Spreadsheet ID", value=SPREADSHEET_ID)

    if st.button("🔄 Reload Master Excel Database"):
        try:
            res = load_master_excel(excel_path_input)
            st.success(f"Loaded {res['total_countries']} countries ({res['ambiguous_emails_count']} ambiguous).")
        except Exception as e:
            st.error(f"Error loading Excel: {e}")

    st.divider()
    st.markdown("### 🔗 Quick Links")
    st.markdown(f"- [📁 Destination Google Drive Folder](https://drive.google.com/drive/folders/{drive_folder_input})")
    current_sheet_id = get_checkpoint("REGISTRY_SPREADSHEET_ID") or sheet_id_input
    st.markdown(f"- [📊 Results Google Spreadsheet](https://docs.google.com/spreadsheets/d/{current_sheet_id}/edit)")

# --- Top Key Metrics ---
stats = get_scan_statistics()
cutoff = get_checkpoint("HISTORICAL_CUTOFF_ISO") or "None"
is_completed = get_checkpoint("HISTORICAL_SCAN_COMPLETED") == "TRUE"

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Messages Scanned", stats["total_messages_scanned"])
col2.metric("Matched Countries", stats["matched_messages"])
col3.metric("Attachments Uploaded", stats["uploaded_attachments"])
col4.metric("Countries With Docs", stats["countries_with_docs"])
col5.metric("Ambiguous Senders", stats["ambiguous_messages"])

st.divider()

# --- Main Tabs ---
tab_scan, tab_registry, tab_unmatched, tab_audit = st.tabs([
    "🚀 Scan Engine", 
    "📋 Country Document Registry", 
    "❓ Unmatched / Ambiguous Senders", 
    "📝 Audit Trail & Logs"
])

# TAB 1: SCAN ENGINE
with tab_scan:
    st.subheader("Historical & Incremental Scan Controls")
    
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("#### 1. Historical Scan")
        st.caption("Scans ALL historical messages (read & unread) up to the scan cutoff timestamp.")
        
        status_text = " Completed" if is_completed else ("⏳ In Progress / Paused" if cutoff != "None" else "⚪ Not Started")
        st.write(f"**Status:** {status_text} | **Cutoff:** `{cutoff}`")

        if st.button("▶️ Start / Resume Historical Scan", type="primary", use_container_width=True):
            if not is_authed:
                st.error("Please authenticate with Google first via the sidebar!")
            else:
                progress_bar = st.progress(0, text="Initializing scan...")
                status_box = st.empty()
                
                try:
                    status_box.info("Scanning Gmail messages...")
                    scanner = GmailHistoricalScanner()
                    scan_res = scanner.run_historical_scan(
                        progress_callback=lambda p: status_box.write(
                            f"Scanning: Message `{p['current_message_id']}` from `{p['sender']}` -> "
                            f"**{p['matched_country'] or 'Unmatched'}** ({p['attachments_found']} docs)"
                        )
                    )
                    
                    status_box.info("Uploading attachments to Google Drive...")
                    progress_bar.progress(60, text="Uploading attachments to Drive...")
                    syncer = DriveSheetsSyncer(folder_id=drive_folder_input)
                    upload_res = syncer.sync_pending_uploads()

                    status_box.info("Updating Google Sheets Document Registry (One row per country)...")
                    progress_bar.progress(90, text="Writing to Google Sheets...")
                    sheet_res = syncer.update_country_registry_sheet()

                    progress_bar.progress(100, text="Completed!")
                    st.success(f"Historical Scan complete! Uploaded {upload_res['uploaded']} attachments.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Execution Error: {e}")

    with col_b:
        st.markdown("#### 2. Incremental Scan")
        st.caption("Detects new incoming emails received AFTER the historical cutoff.")
        
        last_inc = get_checkpoint("LAST_INCREMENTAL_SYNC_ISO") or "Never"
        st.write(f"**Last Sync:** `{last_inc}`")

        if st.button("🔄 Run Incremental Scan", use_container_width=True):
            if not is_authed:
                st.error("Please authenticate with Google first via the sidebar!")
            else:
                with st.spinner("Running incremental scan..."):
                    try:
                        inc = GmailIncrementalScanner()
                        res = inc.run_incremental_scan()
                        st.success(f"Incremental scan complete! Discovered: {res['scan_stats']['attachments_discovered']}, Uploaded: {res['upload_stats']['uploaded']}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error during incremental scan: {e}")

# TAB 2: COUNTRY DOCUMENT REGISTRY
with tab_registry:
    st.subheader("Master Country Document Registry (One Row Per Country)")
    countries_data = get_country_documents_summary()
    
    if countries_data:
        display_rows = []
        for c in countries_data:
            doc_items = c.get("documents", [])
            doc_str = "\n".join([f"• {d['filename']}" for d in doc_items]) if doc_items else "No documents found"
            display_rows.append({
                "Row #": c["row_order"],
                "Country Name": c["country_name"],
                "Email Address": c["official_emails"],
                "Document Count": len(doc_items),
                "Documents": doc_str
            })
        
        df_registry = pd.DataFrame(display_rows)
        st.dataframe(df_registry, use_container_width=True, height=500)
    else:
        st.info("No master country records loaded. Click 'Reload Master Excel Database' in the sidebar.")

# TAB 3: UNMATCHED / AMBIGUOUS SENDERS
with tab_audit:
    pass

with tab_unmatched:
    st.subheader("Unmatched & Ambiguous Email Addresses")
    with get_connection() as conn:
        df_unmatched = pd.read_sql_query("""
        SELECT message_id, sender_normalized, subject, date_received, status, error_details
        FROM scanned_messages
        WHERE status IN ('UNMATCHED', 'AMBIGUOUS')
        ORDER BY created_at DESC
        """, conn)
    
    if not df_unmatched.empty:
        st.dataframe(df_unmatched, use_container_width=True)
    else:
        st.info("No unmatched or ambiguous senders recorded yet.")

# TAB 4: AUDIT TRAIL & LOGS
with tab_audit:
    st.subheader("Structured Execution Audit Logs")
    with get_connection() as conn:
        df_logs = pd.read_sql_query("""
        SELECT timestamp, level, action, message_id, sender_email, country_name, filename, details
        FROM audit_logs
        ORDER BY id DESC
        LIMIT 200
        """, conn)
    
    if not df_logs.empty:
        st.dataframe(df_logs, use_container_width=True)
    else:
        st.info("No audit logs recorded yet.")
