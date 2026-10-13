# Gmail → Google Drive → Google Sheets Automation

Production-grade automation system that scans unread emails, uploads eligible attachments to a designated Google Drive folder, updates corresponding country rows in Google Sheets, and dynamically creates sequential document columns (`Document 1`, `Document 2`, etc.).

---

## What We Need From You (Only 2 Things!)

You **do not need any paid API keys** or billing accounts for the recommended Google Apps Script implementation. You only need:

1. **Google Drive Folder ID**  
   - Create a folder in Google Drive (e.g. `Country Documents`).
   - Copy the ID from the folder URL: `https://drive.google.com/drive/folders/{FOLDER_ID}`.
2. **Google Spreadsheet ID or URL**  
   - The spreadsheet containing your `Database` sheet (`Country` in Col A, `Email` in Col B).
   - If you attach this script directly to your Sheet (**Extensions > Apps Script**), you don't even need the Spreadsheet ID!

---

## File Structure

```
gmail-drive-sheets-automation/
├── appsscript.json        # Apps Script manifest with explicit OAuth scopes
├── Code.js                # Complete Apps Script production engine
├── README.md              # Quick setup and deployment instructions
└── python_version/        # Optional standalone Python CLI / Docker alternative
    ├── main.py            # Python implementation using Google Client Libraries
    ├── requirements.txt   # Dependencies (google-api-python-client, etc.)
    └── .env.example       # Environment template
```

---

## Quick Deployment (3 Minutes)

1. Open your target Google Spreadsheet.
2. Ensure sheet tab is named **`Database`** with headers:
   - `A1`: `Country`
   - `B1`: `Email`
   - `C1`: `Document 1`
   - `D1`: `Document 2`
   - `E1`: `Document 3`
3. Click **Extensions > Apps Script**.
4. In Project Settings, enable **Show "appsscript.json" manifest file in editor**.
5. Copy the contents of `appsscript.json` into the editor's `appsscript.json`.
6. Copy the contents of `Code.js` into the editor's `Code.gs`.
7. Paste your `DRIVE_FOLDER_ID` into line 21 of `Code.js`.
8. Select `setupAutomation` from the function dropdown and click **Run**.
9. Grant the one-time Google permissions.

That's it! The script will automatically verify your Drive folder, create the audit log tab, create Gmail tracking labels, and schedule a trigger running every 10 minutes.
