import sqlite3
import datetime
from typing import List, Dict, Optional, Tuple, Any
from config import DB_PATH

def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(db_path: str = DB_PATH):
    """Initializes SQLite database schema for progress tracking, idempotency, and audit trails."""
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        
        # 1. Master Countries from Excel
        cur.execute("""
        CREATE TABLE IF NOT EXISTS countries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_name TEXT NOT NULL UNIQUE,
            official_emails TEXT,
            source_type TEXT DEFAULT 'Indian Embassy',
            row_order INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # 2. Normalized Email Lookup mapping to Country
        cur.execute("""
        CREATE TABLE IF NOT EXISTS email_lookup (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            normalized_email TEXT NOT NULL,
            country_name TEXT NOT NULL,
            is_ambiguous INTEGER DEFAULT 0,
            conflicting_countries TEXT,
            UNIQUE(normalized_email, country_name)
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_norm_email ON email_lookup(normalized_email)")

        # 2b. Mission Tag Lookup mapping to Country
        cur.execute("""
        CREATE TABLE IF NOT EXISTS mission_lookup (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_tag TEXT NOT NULL UNIQUE,
            country_name TEXT NOT NULL
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_mission_tag ON mission_lookup(mission_tag)")

        # 3. Scanned Gmail Messages
        cur.execute("""
        CREATE TABLE IF NOT EXISTS scanned_messages (
            message_id TEXT PRIMARY KEY,
            thread_id TEXT,
            sender_raw TEXT,
            sender_normalized TEXT,
            subject TEXT,
            date_received TEXT,
            matched_country TEXT,
            source_type TEXT DEFAULT 'Indian Embassy',
            is_ambiguous INTEGER DEFAULT 0,
            has_attachments INTEGER DEFAULT 0,
            status TEXT DEFAULT 'PENDING', -- PENDING, MATCHED, UNMATCHED, AMBIGUOUS, COMPLETED, FAILED
            error_details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # 4. Attachments & Drive Upload State
        cur.execute("""
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL,
            country_name TEXT NOT NULL,
            attachment_id TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            sanitized_filename TEXT NOT NULL,
            mime_type TEXT,
            size_bytes INTEGER,
            drive_file_id TEXT,
            drive_web_url TEXT,
            status TEXT DEFAULT 'PENDING', -- PENDING, UPLOADED, LINKED, COMPLETED, FAILED, SKIPPED
            error_details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(message_id, attachment_id),
            FOREIGN KEY (message_id) REFERENCES scanned_messages(message_id)
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_att_status ON attachments(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_att_country ON attachments(country_name)")

        # 5. Persistent Scan Checkpoints & Cutoffs
        cur.execute("""
        CREATE TABLE IF NOT EXISTS scan_checkpoints (
            checkpoint_key TEXT PRIMARY KEY,
            checkpoint_value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # 6. Structured Audit Log
        cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            level TEXT, -- INFO, WARNING, ERROR
            action TEXT,
            message_id TEXT,
            sender_email TEXT,
            country_name TEXT,
            filename TEXT,
            drive_file_id TEXT,
            details TEXT
        )
        """)
        conn.commit()

# --- Logging & Checkpoint Helpers ---

def log_audit(action: str, level: str = "INFO", message_id: str = None, 
              sender_email: str = None, country_name: str = None, 
              filename: str = None, drive_file_id: str = None, 
              details: str = None, db_path: str = DB_PATH):
    with get_connection(db_path) as conn:
        conn.execute("""
        INSERT INTO audit_logs (level, action, message_id, sender_email, country_name, filename, drive_file_id, details)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (level, action, message_id, sender_email, country_name, filename, drive_file_id, details))
        conn.commit()

def set_checkpoint(key: str, value: str, db_path: str = DB_PATH):
    with get_connection(db_path) as conn:
        conn.execute("""
        INSERT OR REPLACE INTO scan_checkpoints (checkpoint_key, checkpoint_value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        """, (key, value))
        conn.commit()

def get_checkpoint(key: str, db_path: str = DB_PATH) -> Optional[str]:
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT checkpoint_value FROM scan_checkpoints WHERE checkpoint_key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else None

# --- Message & Attachment State Queries ---

def is_message_scanned(message_id: str, db_path: str = DB_PATH) -> bool:
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM scanned_messages WHERE message_id = ? AND status IN ('MATCHED', 'COMPLETED')", (message_id,))
        return cur.fetchone() is not None

def record_scanned_message(message_data: Dict[str, Any], db_path: str = DB_PATH):
    with get_connection(db_path) as conn:
        conn.execute("""
        INSERT OR REPLACE INTO scanned_messages (
            message_id, thread_id, sender_raw, sender_normalized,
            subject, date_received, matched_country, source_type, is_ambiguous,
            has_attachments, status, error_details, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            message_data.get("message_id"),
            message_data.get("thread_id"),
            message_data.get("sender_raw"),
            message_data.get("sender_normalized"),
            message_data.get("subject"),
            message_data.get("date_received"),
            message_data.get("matched_country"),
            message_data.get("source_type", "Indian Embassy"),
            1 if message_data.get("is_ambiguous") else 0,
            1 if message_data.get("has_attachments") else 0,
            message_data.get("status", "PENDING"),
            message_data.get("error_details")
        ))
        conn.commit()

def record_attachment(att_data: Dict[str, Any], db_path: str = DB_PATH):
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM attachments WHERE message_id = ? AND original_filename = ?",
                    (att_data.get("message_id"), att_data.get("original_filename")))
        if cur.fetchone():
            return
        conn.execute("""
        INSERT INTO attachments (
            message_id, country_name, attachment_id, original_filename,
            sanitized_filename, mime_type, size_bytes, drive_file_id,
            drive_web_url, status, error_details
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            att_data.get("message_id"),
            att_data.get("country_name"),
            att_data.get("attachment_id"),
            att_data.get("original_filename"),
            att_data.get("sanitized_filename"),
            att_data.get("mime_type"),
            att_data.get("size_bytes"),
            att_data.get("drive_file_id"),
            att_data.get("drive_web_url"),
            att_data.get("status", "PENDING"),
            att_data.get("error_details")
        ))
        conn.commit()

def update_attachment_status(message_id: str, attachment_id: str, status: str, 
                             drive_file_id: str = None, drive_web_url: str = None, 
                             error_details: str = None, db_path: str = DB_PATH):
    with get_connection(db_path) as conn:
        conn.execute("""
        UPDATE attachments SET 
            status = ?, 
            drive_file_id = COALESCE(?, drive_file_id),
            drive_web_url = COALESCE(?, drive_web_url),
            error_details = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE message_id = ? AND attachment_id = ?
        """, (status, drive_file_id, drive_web_url, error_details, message_id, attachment_id))
        conn.commit()

def get_pending_attachments(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute("""
        SELECT a.*, m.sender_normalized 
        FROM attachments a 
        JOIN scanned_messages m ON a.message_id = m.message_id
        WHERE a.status = 'PENDING'
        ORDER BY a.id ASC
        """)
        return [dict(row) for row in cur.fetchall()]

def get_country_documents_summary(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Returns all 185 countries with their uploaded and linked documents."""
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute("""
        SELECT 
            c.country_name,
            COALESCE(c.source_type, 'Indian Embassy') as source_type,
            c.official_emails,
            c.row_order,
            COALESCE(GROUP_CONCAT(a.sanitized_filename || '|||' || a.drive_web_url, ';;;'), '') as documents_data,
            COUNT(a.id) as doc_count
        FROM countries c
        LEFT JOIN attachments a ON c.country_name = a.country_name AND a.status IN ('UPLOADED', 'LINKED', 'COMPLETED')
        GROUP BY c.country_name
        ORDER BY c.row_order ASC
        """)
        results = []
        for row in cur.fetchall():
            row_dict = dict(row)
            docs = []
            if row_dict["documents_data"]:
                for item in row_dict["documents_data"].split(";;;"):
                    if "|||" in item:
                        name, url = item.split("|||", 1)
                        docs.append({"filename": name, "url": url})
            row_dict["documents"] = docs
            results.append(row_dict)
        return results

def get_scan_statistics(db_path: str = DB_PATH) -> Dict[str, int]:
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM scanned_messages")
        total_messages = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM scanned_messages WHERE status = 'MATCHED' OR matched_country IS NOT NULL")
        matched_messages = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM scanned_messages WHERE is_ambiguous = 1")
        ambiguous_messages = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM attachments WHERE status IN ('UPLOADED', 'LINKED', 'COMPLETED')")
        uploaded_attachments = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM attachments WHERE status = 'FAILED'")
        failed_attachments = cur.fetchone()[0]

        cur.execute("SELECT COUNT(DISTINCT country_name) FROM attachments WHERE status IN ('UPLOADED', 'LINKED', 'COMPLETED')")
        countries_with_docs = cur.fetchone()[0]

        return {
            "total_messages_scanned": total_messages,
            "matched_messages": matched_messages,
            "ambiguous_messages": ambiguous_messages,
            "uploaded_attachments": uploaded_attachments,
            "failed_attachments": failed_attachments,
            "countries_with_docs": countries_with_docs
        }
