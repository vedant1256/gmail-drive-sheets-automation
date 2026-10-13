import os
import threading
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from google_auth_oauthlib.flow import Flow

from config import SPREADSHEET_ID, DRIVE_FOLDER_ID, CREDENTIALS_FILE, SCOPES, TOKEN_FILE, USER_EMAIL
from models import (
    get_connection, get_scan_statistics, get_country_documents_summary,
    get_checkpoint, set_checkpoint, log_audit
)
from google_client import google_auth
from scanner import GmailHistoricalScanner
from drive_syncer import DriveSheetsSyncer
from incremental import GmailIncrementalScanner
from database_loader import load_master_excel

app = FastAPI(title="Gmail Document Registry API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory storage for active flows and verifiers
active_oauth_flows: Dict[str, Any] = {}
active_verifiers: Dict[str, str] = {}

# In-memory scan state for live tracking
active_scan_state = {
    "is_running": False,
    "scan_type": None,
    "progress_percent": 0,
    "status_message": "Idle",
    "current_sender": "",
    "current_country": "",
    "evaluated_count": 0,
    "matched_count": 0,
    "attachments_count": 0,
    "last_error": None
}

@app.get("/api/status")
def get_status():
    is_authed = google_auth.is_authenticated()
    stats = get_scan_statistics()
    cutoff = get_checkpoint("HISTORICAL_CUTOFF_ISO") or "None"
    is_completed = get_checkpoint("HISTORICAL_SCAN_COMPLETED") == "TRUE"
    last_inc = get_checkpoint("LAST_INCREMENTAL_SYNC_ISO") or "Never"
    sheet_id = get_checkpoint("REGISTRY_SPREADSHEET_ID") or SPREADSHEET_ID

    return {
        "is_authenticated": is_authed,
        "logged_in_email": USER_EMAIL if (is_authed and USER_EMAIL) else ("Connected" if is_authed else None),
        "historical_cutoff": cutoff,
        "historical_completed": is_completed,
        "last_incremental_sync": last_inc,
        "spreadsheet_id": sheet_id,
        "drive_folder_id": DRIVE_FOLDER_ID,
        "stats": stats,
        "scan_state": active_scan_state
    }

@app.get("/api/auth/url")
def get_auth_url():
    """Generates a direct Google OAuth URL targeted specifically to the configured user email."""
    try:
        if not Path(CREDENTIALS_FILE).exists():
            return JSONResponse({"status": "error", "message": f"credentials.json not found at {CREDENTIALS_FILE}"}, status_code=400)

        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

        flow = Flow.from_client_secrets_file(
            CREDENTIALS_FILE,
            scopes=SCOPES,
            redirect_uri="http://localhost:8000/api/auth/callback"
        )
        auth_url, state = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            login_hint=USER_EMAIL or None
        )
        active_oauth_flows[state] = flow
        if hasattr(flow, "code_verifier") and flow.code_verifier:
            active_verifiers[state] = flow.code_verifier

        return {"status": "success", "auth_url": auth_url}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.get("/api/auth/callback")
def auth_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None):
    """Handles OAuth 2.0 redirect, saves token.json permanently, and returns to frontend."""
    if not code:
        return RedirectResponse("http://localhost:3000?auth_error=missing_code")

    try:
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
        
        flow = active_oauth_flows.pop(state, None)
        if not flow:
            flow = Flow.from_client_secrets_file(
                CREDENTIALS_FILE,
                scopes=SCOPES,
                state=state,
                redirect_uri="http://localhost:8000/api/auth/callback"
            )
            verifier = active_verifiers.pop(state, None)
            if verifier:
                flow.code_verifier = verifier

        flow.fetch_token(code=code)
        creds = flow.credentials

        # Save token permanently to disk
        with open(TOKEN_FILE, "w") as tf:
            tf.write(creds.to_json())

        # Also copy to root directory for redundancy
        with open(Path(TOKEN_FILE).parent.parent / "token.json", "w") as tf:
            tf.write(creds.to_json())

        # Update in-memory Google client credentials
        google_auth._creds = creds
        google_auth._gmail_service = None
        google_auth._drive_service = None
        google_auth._sheets_service = None

        log_audit("OAUTH_LOGIN_SUCCESS", level="INFO", 
                  sender_email=USER_EMAIL or "authorized_user",
                  details=f"User {USER_EMAIL or 'account'} authenticated permanently.")

        return RedirectResponse("http://localhost:3000?authenticated=true")
    except Exception as e:
        log_audit("OAUTH_CALLBACK_ERROR", level="ERROR", details=str(e))
        return RedirectResponse(f"http://localhost:3000?auth_error={str(e)}")

def _run_historical_task():
    global active_scan_state
    active_scan_state["is_running"] = True
    active_scan_state["scan_type"] = "historical"
    active_scan_state["progress_percent"] = 5
    active_scan_state["status_message"] = "Scanning Gmail historical messages..."
    active_scan_state["last_error"] = None

    try:
        scanner = GmailHistoricalScanner()
        def on_progress(p):
            active_scan_state["current_sender"] = p.get("sender", "")
            active_scan_state["current_country"] = p.get("matched_country", "") or "Unmatched"
            active_scan_state["evaluated_count"] = p.get("total_evaluated", 0)
            active_scan_state["attachments_count"] += p.get("attachments_found", 0)
            if p.get("matched_country"):
                active_scan_state["matched_count"] += 1
            active_scan_state["status_message"] = f"Evaluating {p.get('sender')} -> {active_scan_state['current_country']}"

        scan_res = scanner.run_historical_scan(force_rescan=True, progress_callback=on_progress)
        
        active_scan_state["progress_percent"] = 55
        active_scan_state["status_message"] = "Uploading attachments to Google Drive..."
        syncer = DriveSheetsSyncer()
        upload_res = syncer.sync_pending_uploads()

        active_scan_state["progress_percent"] = 85
        active_scan_state["status_message"] = "Updating Google Sheets Document Registry..."
        syncer.update_country_registry_sheet()

        active_scan_state["progress_percent"] = 100
        active_scan_state["status_message"] = f"Historical scan complete! Uploaded {upload_res.get('uploaded', 0)} attachments."
    except Exception as e:
        active_scan_state["last_error"] = str(e)
        active_scan_state["status_message"] = f"Error: {e}"
    finally:
        active_scan_state["is_running"] = False

@app.post("/api/scan/historical")
def start_historical_scan(background_tasks: BackgroundTasks):
    if active_scan_state["is_running"]:
        return {"status": "already_running", "message": "A scan is already in progress"}
    
    background_tasks.add_task(_run_historical_task)
    return {"status": "started", "message": "Historical scan initiated"}

def _run_incremental_task():
    global active_scan_state
    active_scan_state["is_running"] = True
    active_scan_state["scan_type"] = "incremental"
    active_scan_state["progress_percent"] = 20
    active_scan_state["status_message"] = "Scanning new incoming messages..."
    active_scan_state["last_error"] = None

    try:
        inc = GmailIncrementalScanner()
        res = inc.run_incremental_scan()
        active_scan_state["progress_percent"] = 100
        active_scan_state["status_message"] = f"Incremental scan completed. Uploaded {res['upload_stats']['uploaded']} attachments."
    except Exception as e:
        active_scan_state["last_error"] = str(e)
        active_scan_state["status_message"] = f"Error: {e}"
    finally:
        active_scan_state["is_running"] = False

@app.post("/api/scan/incremental")
def start_incremental_scan(background_tasks: BackgroundTasks):
    if active_scan_state["is_running"]:
        return {"status": "already_running", "message": "A scan is already in progress"}
    
    background_tasks.add_task(_run_incremental_task)
    return {"status": "started", "message": "Incremental scan initiated"}

def _run_date_scan_task(query: str, date_label: str):
    global active_scan_state
    active_scan_state["is_running"] = True
    active_scan_state["scan_type"] = f"date ({date_label})"
    active_scan_state["progress_percent"] = 5
    active_scan_state["status_message"] = f"Scanning emails for {date_label}..."
    active_scan_state["last_error"] = None
    active_scan_state["evaluated_count"] = 0
    active_scan_state["matched_count"] = 0
    active_scan_state["attachments_count"] = 0

    try:
        scanner = GmailHistoricalScanner(search_query=query)
        def on_progress(p):
            active_scan_state["current_sender"] = p.get("sender", "")
            active_scan_state["current_country"] = p.get("matched_country", "") or "Unmatched"
            active_scan_state["evaluated_count"] = p.get("total_evaluated", 0)
            active_scan_state["attachments_count"] += p.get("attachments_found", 0)
            if p.get("matched_country"):
                active_scan_state["matched_count"] += 1
            active_scan_state["status_message"] = f"Evaluating {p.get('sender')} -> {active_scan_state['current_country']}"

        scan_res = scanner.run_historical_scan(force_rescan=True, progress_callback=on_progress)
        
        active_scan_state["progress_percent"] = 55
        active_scan_state["status_message"] = "Uploading attachments to Google Drive..."
        syncer = DriveSheetsSyncer()
        upload_res = syncer.sync_pending_uploads()

        active_scan_state["progress_percent"] = 85
        active_scan_state["status_message"] = "Updating Google Sheets Document Registry..."
        syncer.update_country_registry_sheet()

        active_scan_state["progress_percent"] = 100
        active_scan_state["status_message"] = f"Date scan complete! Uploaded {upload_res.get('uploaded', 0)} attachments for {date_label}."
    except Exception as e:
        active_scan_state["last_error"] = str(e)
        active_scan_state["status_message"] = f"Error: {e}"
    finally:
        active_scan_state["is_running"] = False

@app.post("/api/scan/date")
async def start_date_scan(request: Request, background_tasks: BackgroundTasks):
    if active_scan_state["is_running"]:
        return JSONResponse({"status": "already_running", "message": "A scan is already in progress"}, status_code=400)
    
    try:
        body = await request.json()
    except Exception:
        body = {}

    date_str = body.get("date")
    mode = body.get("mode", "single") # "single", "from_date", "range"
    end_date_str = body.get("end_date")

    if not date_str:
        return JSONResponse({"status": "error", "message": "Date parameter is required (YYYY-MM-DD)"}, status_code=400)

    try:
        import datetime
        dt_start = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid date format. Use YYYY-MM-DD."}, status_code=400)

    base_query = "has:attachment -in:spam -in:trash"
    
    if mode == "single":
        day_before = (dt_start - datetime.timedelta(days=1)).strftime("%Y/%m/%d")
        day_after = (dt_start + datetime.timedelta(days=1)).strftime("%Y/%m/%d")
        query = f"{base_query} after:{day_before} before:{day_after}"
        date_label = date_str
    elif mode == "from_date":
        day_before = (dt_start - datetime.timedelta(days=1)).strftime("%Y/%m/%d")
        query = f"{base_query} after:{day_before}"
        date_label = f"from {date_str} onwards"
    elif mode == "range":
        if not end_date_str:
            return JSONResponse({"status": "error", "message": "end_date is required for range mode"}, status_code=400)
        try:
            dt_end = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except Exception:
            return JSONResponse({"status": "error", "message": "Invalid end_date format. Use YYYY-MM-DD."}, status_code=400)
        start_prev = (dt_start - datetime.timedelta(days=1)).strftime("%Y/%m/%d")
        end_next = (dt_end + datetime.timedelta(days=1)).strftime("%Y/%m/%d")
        query = f"{base_query} after:{start_prev} before:{end_next}"
        date_label = f"{date_str} to {end_date_str}"
    else:
        return JSONResponse({"status": "error", "message": f"Unknown mode: {mode}"}, status_code=400)

    background_tasks.add_task(_run_date_scan_task, query, date_label)
    return {"status": "started", "message": f"Scan initiated for {date_label}", "query": query}

@app.get("/api/countries")
def get_countries():
    data = get_country_documents_summary()
    return {"countries": data, "total": len(data)}

@app.get("/api/logs")
def get_logs():
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
        SELECT timestamp, level, action, message_id, sender_email, country_name, filename, details
        FROM audit_logs
        ORDER BY id DESC
        LIMIT 100
        """)
        rows = [dict(r) for r in cur.fetchall()]
        return {"logs": rows}

@app.get("/api/unmatched")
def get_unmatched():
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
        SELECT message_id, sender_normalized, subject, date_received, status, error_details
        FROM scanned_messages
        WHERE status IN ('UNMATCHED', 'AMBIGUOUS')
        ORDER BY created_at DESC
        """)
        rows = [dict(r) for r in cur.fetchall()]
        return {"unmatched": rows}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
