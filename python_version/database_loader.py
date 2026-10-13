import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
import openpyxl
from config import EXCEL_PATH, DB_PATH, USER_EMAIL
from models import init_db, get_connection, log_audit

EMAIL_REGEX = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')

STOP_WORDS = {
    'the', 'and', 'for', 'rep', 'gov', 'emb', 'com', 'comm', 'hoc', 'amb', 'cons', 
    'trade', 'info', 'mail', 'email', 'eoi', 'hci', 'india', 'indian', 'office', 
    'wing', 'section', 'asst', 'assistant', 'officer', 'general', 'high', 'commission', 
    'embassy', 'new', 'delhi', 'contactamb', 'commerce', 'commercial', 'query', 
    'contact', 'project', 'newdelhi', 'events', 'dcm', 'sscom', 'socialsecy', 
    'cr', 'mktasst', 'lsproj', 'eco', 'indemb', 'admin', 'frontdesk', 'pic', 'dhc',
    'comwing', 'com1', 'com2', 'com3', 'com4', 'com5', 'com6', 'comsec', 'republic'
}

ADDITIONAL_MISSIONS = {
    'niamey': 'Niger', 'windhoek': 'Namibia', 'maputo': 'Mozambique', 'lisbon': 'Portugal',
    'warsaw': 'Poland', 'nairobi': 'Kenya', 'vientianne': 'Laos', 'vientiane': 'Laos',
    'male': 'Maldives', 'mexico': 'Mexico', 'athe': 'Greece', 'athens': 'Greece',
    'belgrade': 'Serbia', 'jkt': 'Indonesia', 'jakarta': 'Indonesia', 'baghdad': 'Iraq',
    'berlin': 'Germany', 'georgetown': 'Antigua', 'tallinn': 'Estonia', 'nicosia': 'Cyprus',
    'zagreb': 'Croatia', 'canberra': 'Australia', 'brunei': 'Brunei', 'minsk': 'Belarus',
    'bahrain': 'Bahrain', 'amman': 'Jordan', 'astana': 'Kazakhstan', 'lima': 'Peru',
    'telaviv': 'Israel'
}

EXPORT_KEYWORDS = [
    "export", "exporter", "exporters", "importer", "importers", "import", "imports", "buyer", "buyers", 
    "trade", "shipment", "inquiry", "enquiry", "tender", "quotation", "rfq", "supplier", 
    "suppliers", "consignee", "logistics", "customs", "spices", "agro", "food", "commodity", 
    "purchase order", "packing list", "cargo", "freight", "b2b", "procurement", "distributor",
    "raisins", "dry fruits", "fruits", "nuts", "supermarket", "chambers of commerce", "iec", "dgft"
]

def normalize_email(email_str: str) -> str:
    """Normalizes an email address by trimming, lowercasing, and removing trailing punctuation."""
    if not email_str:
        return ""
    cleaned = email_str.strip().lower()
    cleaned = cleaned.rstrip('.,;:')
    return cleaned

def load_master_excel(excel_path: str = EXCEL_PATH, db_path: str = DB_PATH) -> Dict[str, Any]:
    """
    Parses Indian Embassies.xlsx and populates SQLite master tables:
    countries, email_lookup, and mission_lookup.
    """
    p = Path(excel_path)
    if not p.exists():
        raise FileNotFoundError(f"Master Excel file not found at: {excel_path}")

    init_db(db_path)
    wb = openpyxl.load_workbook(str(p), data_only=True)
    
    # Select worksheet: choose active or sheet with data
    ws = None
    for sname in wb.sheetnames:
        sheet = wb[sname]
        if sheet.max_row > 5:
            ws = sheet
            break
    if ws is None:
        ws = wb.active

    # Detect dynamic column indices from row 1
    headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    country_col_idx = None
    email_col_idx = None
    website_col_idx = None

    for idx, h in enumerate(headers, start=1):
        if not h:
            continue
        h_str = str(h).strip().upper()
        if "COUNTRI" in h_str or "COUNTRY" in h_str:
            country_col_idx = idx
        elif any(k in h_str for k in ["E. MALE", "EMAIL", "E-MAIL", "MAIL"]):
            email_col_idx = idx
        elif any(k in h_str for k in ["WEB", "SITE", "URL"]):
            website_col_idx = idx

    if not country_col_idx or not email_col_idx:
        raise ValueError(
            f"Could not identify required columns in {p.name}. Found headers: {headers}. "
            f"Need 'COUNTRIES NAME' and 'E. MALE' (or equivalent)."
        )

    countries_data = [] # (country_name, official_emails_str, row_order)
    email_to_countries: Dict[str, List[str]] = {}
    mission_tags: Dict[str, str] = {} # mission_tag -> country_name

    row_order = 1
    for r in range(2, ws.max_row + 1):
        c_val = ws.cell(r, country_col_idx).value
        e_val = ws.cell(r, email_col_idx).value
        w_val = ws.cell(r, website_col_idx).value if website_col_idx else ""

        if c_val is None and e_val is None:
            continue

        country = str(c_val).strip() if c_val is not None else ""
        raw_email = str(e_val).strip() if e_val is not None else ""
        raw_website = str(w_val).strip() if w_val is not None else ""

        if not country or country.lower() == "none":
            continue

        # Extract all valid emails from this cell (handles space or newline separated)
        found_emails = EMAIL_REGEX.findall(raw_email)
        normalized_emails = [normalize_email(e) for e in found_emails if e]
        # Remove duplicates while preserving order
        unique_emails = list(dict.fromkeys(normalized_emails))

        official_emails_str = ", ".join(unique_emails) if unique_emails else raw_email
        countries_data.append((country, official_emails_str, row_order))
        row_order += 1

        for em in unique_emails:
            if em not in email_to_countries:
                email_to_countries[em] = []
            if country not in email_to_countries[em]:
                email_to_countries[em].append(country)

            # Extract mission code from MEA email: e.g. hoc.niamey@mea.gov.in -> niamey
            if "@mea.gov.in" in em:
                user_part = em.split("@")[0]
                for part in user_part.split("."):
                    clean_part = re.sub(r'\d+$', '', part).strip()
                    if len(clean_part) >= 3 and clean_part not in STOP_WORDS:
                        mission_tags[clean_part] = country

        # Extract mission code from website URL: e.g. https://indembniamey.gov.in -> niamey
        if raw_website:
            for url in re.findall(r'https?://(?:www\.)?([\w.-]+)', raw_website.lower()):
                host = url.split(".")[0]
                for pfx in ['indembassy', 'indianembassy', 'indemb', 'embassy', 'eoi', 'hci', 'highcommission']:
                    if host.startswith(pfx):
                        host = host[len(pfx):]
                        break
                if len(host) >= 3 and host not in STOP_WORDS:
                    mission_tags[host] = country

    # Supplement with curated mission/city mappings
    mission_tags.update(ADDITIONAL_MISSIONS)

    # Persist to SQLite
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM countries")
        cur.execute("DELETE FROM email_lookup")
        cur.execute("DELETE FROM mission_lookup")

        for country, emails_str, order in countries_data:
            cur.execute("""
            INSERT OR REPLACE INTO countries (country_name, official_emails, row_order, source_type)
            VALUES (?, ?, ?, 'Indian Embassy')
            """, (country, emails_str, order))

        ambiguous_count = 0
        for email, clist in email_to_countries.items():
            is_amb = 1 if len(clist) > 1 else 0
            if is_amb:
                ambiguous_count += 1
            conflicting_str = ", ".join(clist) if is_amb else None
            for country in clist:
                cur.execute("""
                INSERT OR REPLACE INTO email_lookup (normalized_email, country_name, is_ambiguous, conflicting_countries)
                VALUES (?, ?, ?, ?)
                """, (email, country, is_amb, conflicting_str))

        for tag, country in mission_tags.items():
            cur.execute("""
            INSERT OR REPLACE INTO mission_lookup (mission_tag, country_name)
            VALUES (?, ?)
            """, (tag, country))

        conn.commit()

    log_audit("LOAD_MASTER_DATABASE", level="INFO", 
              details=f"Loaded {len(countries_data)} countries, {len(email_to_countries)} emails, and {len(mission_tags)} mission tags.",
              db_path=db_path)

    return {
        "total_countries": len(countries_data),
        "total_unique_emails": len(email_to_countries),
        "total_mission_tags": len(mission_tags),
        "ambiguous_emails_count": ambiguous_count
    }

def lookup_sender(
    sender_raw: str,
    subject: str = "",
    body_snippet: str = "",
    has_attachments: bool = False,
    db_path: str = DB_PATH
) -> Tuple[Optional[str], Optional[str], bool, Optional[str]]:
    """
    Intelligently identifies if an email belongs to an Indian Embassy or an Export Inquiry.
    Returns: (matched_country, source_type, is_ambiguous, error_notes)
    - source_type: 'Indian Embassy' or 'Export Inquiry' or None
    """
    if not sender_raw:
        return (None, None, False, "Empty sender header")

    # Extract email from "Display Name <email@domain.com>"
    match = EMAIL_REGEX.search(sender_raw)
    norm_email = normalize_email(match.group(0)) if match else sender_raw.strip().lower()
    sender_lower = sender_raw.lower()
    subj_lower = subject.lower()
    snippet_lower = body_snippet.lower()
    corpus = f"{sender_lower} {subj_lower} {snippet_lower}"

    # 1. Ignore delivery failure notices / daemon bounces
    if any(k in norm_email for k in ["mailer-daemon", "postmaster", "googlemail.com"]) or \
       any(k in subj_lower for k in ["delivery status notification", "failure", "undelivered"]):
        return (None, None, False, "Delivery status failure notification (ignored)")

    # 2. Ignore visa autoresponder
    if "newdelhi.visa@eda.admin.ch" in norm_email:
        return (None, None, False, "Swiss visa auto-response (ignored)")

    with get_connection(db_path) as conn:
        cur = conn.cursor()

        # Step 1: Direct official email match in email_lookup
        cur.execute("""
        SELECT country_name, is_ambiguous, conflicting_countries 
        FROM email_lookup 
        WHERE normalized_email = ?
        """, (norm_email,))
        rows = cur.fetchall()

        if rows:
            first_row = rows[0]
            if first_row["is_ambiguous"] == 1 or len(rows) > 1:
                countries = [r["country_name"] for r in rows]
                conflict_msg = f"Ambiguous sender '{norm_email}' matches multiple countries: {', '.join(countries)}"
                return (None, None, True, conflict_msg)
            return (first_row["country_name"], "Indian Embassy", False, None)

        # Step 2: MEA Domain mission matching: e.g. <role>.<mission>@mea.gov.in
        if "@mea.gov.in" in norm_email:
            user_part = norm_email.split("@")[0]
            for part in user_part.split("."):
                clean_part = re.sub(r'\d+$', '', part).strip()
                if clean_part and clean_part not in STOP_WORDS:
                    cur.execute("SELECT country_name FROM mission_lookup WHERE mission_tag = ?", (clean_part,))
                    tag_row = cur.fetchone()
                    if tag_row:
                        # Append this sender to country official emails if not present
                        _append_official_email(tag_row["country_name"], norm_email, conn)
                        return (tag_row["country_name"], "Indian Embassy", False, None)

        # Step 3: Known embassy mission tags in sender email or sender display name
        # e.g. com2.indemb.astana@gmail.com, tradequery.eoi.jkt@gmail.com, commercewingembassyofindiaathe@gmail.com
        cur.execute("SELECT mission_tag, country_name FROM mission_lookup ORDER BY LENGTH(mission_tag) DESC")
        for tag_row in cur.fetchall():
            tag = tag_row["mission_tag"]
            c_name = tag_row["country_name"]
            # Look for whole word in sender raw or substring in email user part
            if re.search(r'\b' + re.escape(tag) + r'\b', sender_lower) or tag in norm_email.split('@')[0]:
                _append_official_email(c_name, norm_email, conn)
                return (c_name, "Indian Embassy", False, None)

        # Step 4: Subject or sender mentions "Embassy of India in <Country>" / "High Commission of India in <Country>"
        if any(k in corpus for k in ["embassy", "high commission", "eoi", "hci", "consulate"]):
            cur.execute("SELECT country_name FROM countries WHERE source_type = 'Indian Embassy' AND country_name != 'India' ORDER BY LENGTH(country_name) DESC")
            for c_row in cur.fetchall():
                c_name = c_row["country_name"]
                if re.search(r'\b' + re.escape(c_name.lower()) + r'\b', corpus):
                    _append_official_email(c_name, norm_email, conn)
                    return (c_name, "Indian Embassy", False, None)

        # Step 5: Export / Trade Inquiries
        is_export = any(kw in corpus for kw in EXPORT_KEYWORDS)
        if is_export or has_attachments:
            if "dgft" in corpus or "e-iec" in corpus:
                matched_entity = "India (DGFT)"
            elif USER_EMAIL and USER_EMAIL.split("@")[0].lower() in norm_email:
                matched_entity = "Company Internal / Outgoing"
            elif "being exporter" in corpus or "socialmedia" in norm_email:
                matched_entity = "Export Marketing"
            else:
                # Check if a specific country is mentioned in the export inquiry
                cur.execute("SELECT country_name FROM countries WHERE source_type = 'Indian Embassy' AND country_name != 'India' ORDER BY LENGTH(country_name) DESC")
                detected = None
                for c_row in cur.fetchall():
                    c_name = c_row["country_name"]
                    if re.search(r'\b' + re.escape(c_name.lower()) + r'\b', corpus):
                        detected = c_name
                        break
                if detected:
                    matched_entity = detected
                else:
                    display_name = sender_raw.split('<')[0].strip(' "\'') if '<' in sender_raw else norm_email.split('@')[0].replace('.', ' ').title()
                    matched_entity = f"Export Inquiry ({display_name})"

            # Register as export source in database
            register_export_source(matched_entity, norm_email, source_type="Export Inquiry", db_path=db_path)
            return (matched_entity, "Export Inquiry", False, None)

    return (None, None, False, f"Sender '{norm_email}' not matched")

def _append_official_email(country_name: str, new_email: str, conn: Any):
    """Appends incoming sender email to country's official_emails if not already recorded."""
    try:
        cur = conn.cursor()
        cur.execute("SELECT official_emails FROM countries WHERE country_name = ?", (country_name,))
        row = cur.fetchone()
        if row:
            curr = row[0] or ""
            if new_email.lower() not in curr.lower():
                updated = f"{curr}, {new_email}" if curr else new_email
                cur.execute("UPDATE countries SET official_emails = ? WHERE country_name = ?", (updated, country_name))
                conn.commit()
    except Exception:
        pass

def get_all_country_names(db_path: str = DB_PATH) -> List[str]:
    """Returns list of country names sorted by length descending for accurate greedy matching."""
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT country_name FROM countries WHERE source_type = 'Indian Embassy' ORDER BY LENGTH(country_name) DESC")
        return [row[0] for row in cur.fetchall()]

def register_export_source(source_name: str, sender_email: str, source_type: str = "Export Inquiry", db_path: str = DB_PATH) -> str:
    """Registers or retrieves an export inquiry sender as a country entry."""
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT country_name, official_emails FROM countries WHERE country_name = ?", (source_name,))
        row = cur.fetchone()
        if not row:
            cur.execute("SELECT COALESCE(MAX(row_order), 0) + 1 FROM countries")
            next_order = cur.fetchone()[0]
            cur.execute("""
            INSERT INTO countries (country_name, official_emails, row_order, source_type)
            VALUES (?, ?, ?, ?)
            """, (source_name, sender_email, next_order, source_type))
            conn.commit()
        else:
            curr_emails = row[1] or ""
            if sender_email and sender_email.lower() not in curr_emails.lower():
                updated = f"{curr_emails}, {sender_email}" if curr_emails else sender_email
                cur.execute("UPDATE countries SET official_emails = ? WHERE country_name = ?", (updated, source_name))
                conn.commit()
        return source_name

if __name__ == "__main__":
    result = load_master_excel()
    print("Master database successfully loaded:")
    print(result)
