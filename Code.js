/**
 * ==============================================================================
 * GMAIL -> GOOGLE DRIVE -> GOOGLE SHEETS ATTACHMENT AUTOMATION
 * ==============================================================================
 * Repository: C:/Users/vedan/Documents/gmail-drive-sheets-automation
 * ==============================================================================
 */

/**
 * Global Configuration
 * Update DRIVE_FOLDER_ID and (optionally) SPREADSHEET_ID below.
 */
const CONFIG = {
  // If your script is container-bound (opened from Extensions > Apps Script in your Sheet),
  // leave SPREADSHEET_ID as "" (empty string). It will automatically detect the active sheet.
  // Google Spreadsheet ID (leave empty if bound to sheet, or paste spreadsheet ID)
  SPREADSHEET_ID: "",

  // Target Google Drive Folder ID
  DRIVE_FOLDER_ID: "PASTE_YOUR_DRIVE_FOLDER_ID_HERE",

  // Sheet tab names
  SHEET_NAME: "Database",
  LOG_SHEET_NAME: "Automation_Logs",

  // Gmail tracking labels
  PROCESSED_LABEL: "Automation/Processed",
  ERROR_LABEL: "Automation/Error",

  // Batching & limits
  BATCH_SIZE: 25,
  MAX_ATTACHMENT_SIZE_BYTES: 25 * 1024 * 1024, // 25 MB
  IGNORE_INLINE_IMAGES: true,
  ALLOWED_EXTENSIONS: [
    "pdf", "docx", "doc", "xlsx", "xls", "csv", 
    "png", "jpg", "jpeg", "zip", "tar", "gz", "txt"
  ],

  // Execution & Concurrency
  LOCK_TIMEOUT_MS: 30000,
  MAX_RETRY_ATTEMPTS: 3,
  RETRY_BASE_DELAY_MS: 1000
};

/**
 * Step 1: Run this function once after inserting your DRIVE_FOLDER_ID.
 * It validates access, ensures tabs and headers exist, creates labels, and installs the 10-minute trigger.
 */
function setupAutomation() {
  console.log("=== Starting Automation Setup ===");
  
  if (CONFIG.DRIVE_FOLDER_ID === "PASTE_YOUR_DRIVE_FOLDER_ID_HERE" || !CONFIG.DRIVE_FOLDER_ID.trim()) {
    throw new Error("Action Required: Please update CONFIG.DRIVE_FOLDER_ID with your real Google Drive Folder ID before running setup.");
  }

  // 1. Validate Spreadsheet & Tabs
  const ss = getSpreadsheetInstance();
  let dbSheet = getDatabaseSheet(ss);
  if (!dbSheet) {
    dbSheet = ss.insertSheet(CONFIG.SHEET_NAME, 0);
    dbSheet.appendRow(["Country", "Email", "Document 1", "Document 2", "Document 3"]);
    dbSheet.setFrozenRows(1);
    console.log(`Created missing database sheet: "${CONFIG.SHEET_NAME}" with initial headers.`);
  } else {
    console.log(`Using database sheet: "${dbSheet.getName()}"`);
    validateDatabaseHeaders(dbSheet);
  }

  let logSheet = ss.getSheetByName(CONFIG.LOG_SHEET_NAME);
  if (!logSheet) {
    logSheet = ss.insertSheet(CONFIG.LOG_SHEET_NAME);
    logSheet.appendRow([
      "Timestamp", "Gmail Message ID", "Sender Email", "Matched Row",
      "Attachment Filename", "MIME Type", "Size (Bytes)", "Drive File ID",
      "Destination Cell", "Status", "Details / Error"
    ]);
    logSheet.setFrozenRows(1);
    console.log(`Created audit log sheet: "${CONFIG.LOG_SHEET_NAME}".`);
  }

  // 2. Validate Drive Folder Access
  try {
    const folder = DriveApp.getFolderById(CONFIG.DRIVE_FOLDER_ID.trim());
    console.log(`Successfully verified target Drive folder: "${folder.getName()}" (${folder.getId()})`);
  } catch (err) {
    throw new Error(`Drive folder verification failed for ID "${CONFIG.DRIVE_FOLDER_ID}". Verify folder ID and edit permissions. Details: ${err.message}`);
  }

  // 3. Create Required Gmail Labels
  createRequiredLabels();

  // 4. Create Time-Driven Trigger (runs every 10 minutes)
  createTimeDrivenTrigger(10);

  console.log("=== Setup Complete. Automation is active and scheduled. ===");
}

/**
 * Creates the required Gmail labels if they do not already exist.
 */
function createRequiredLabels() {
  const labelsToEnsure = [CONFIG.PROCESSED_LABEL, CONFIG.ERROR_LABEL];
  labelsToEnsure.forEach(labelName => {
    let label = GmailApp.getUserLabelByName(labelName);
    if (!label) {
      GmailApp.createLabel(labelName);
      console.log(`Created Gmail label: "${labelName}"`);
    } else {
      console.log(`Gmail label exists: "${labelName}"`);
    }
  });
}

/**
 * Creates a recurring time-driven installable trigger.
 * Removes existing duplicate triggers to ensure single-trigger scheduling.
 * @param {number} intervalMinutes Frequency in minutes (1, 5, 10, 15, or 30).
 */
function createTimeDrivenTrigger(intervalMinutes = 10) {
  const functionName = "runEmailAttachmentAutomation";
  const triggers = ScriptApp.getProjectTriggers();
  
  triggers.forEach(trigger => {
    if (trigger.getHandlerFunction() === functionName) {
      ScriptApp.deleteTrigger(trigger);
      console.log(`Removed existing trigger ID: ${trigger.getUniqueId()}`);
    }
  });

  ScriptApp.newTrigger(functionName)
    .timeBased()
    .everyMinutes(intervalMinutes)
    .create();
  console.log(`Installed new trigger for "${functionName}" running every ${intervalMinutes} minutes.`);
}

/**
 * Primary Automation Entry Point.
 * Executed automatically by the time-driven trigger or manually from the IDE.
 */
function runEmailAttachmentAutomation() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(CONFIG.LOCK_TIMEOUT_MS)) {
    console.warn("Concurrent execution detected: Could not acquire script lock within timeout. Exiting safely.");
    return;
  }

  console.log(`[${new Date().toISOString()}] Automation cycle started.`);

  try {
    const ss = getSpreadsheetInstance();
    const dbSheet = getDatabaseSheet(ss);
    const logSheet = ss.getSheetByName(CONFIG.LOG_SHEET_NAME);

    if (!dbSheet) throw new Error(`Database sheet not found. Ensure a sheet exists with "Country" and "Email" columns.`);
    if (!logSheet) throw new Error(`Log sheet "${CONFIG.LOG_SHEET_NAME}" not found. Please run setupAutomation() first.`);

    // Step 1: Pre-load and index Database rows by sender email
    const dbIndex = loadDatabaseIndex(dbSheet);
    console.log(`Indexed ${dbIndex.emailMap.size} database emails. Found ${dbIndex.duplicateEmails.size} duplicate entries.`);

    // Step 2: Fetch unread candidate messages containing attachments
    const candidateMessages = scanUnreadCandidateMessages(CONFIG.BATCH_SIZE);
    console.log(`Found ${candidateMessages.length} candidate messages requiring evaluation.`);

    if (candidateMessages.length === 0) {
      console.log("No messages to process. Ending cycle.");
      return;
    }

    // Step 3: Fetch Target Drive Folder and labels
    const driveFolder = DriveApp.getFolderById(CONFIG.DRIVE_FOLDER_ID.trim());
    const processedLabel = GmailApp.getUserLabelByName(CONFIG.PROCESSED_LABEL);
    const errorLabel = GmailApp.getUserLabelByName(CONFIG.ERROR_LABEL);

    // Step 4: Process Each Message Individually
    for (const msg of candidateMessages) {
      processSingleMessage(msg, dbSheet, logSheet, dbIndex, driveFolder, processedLabel, errorLabel);
    }

  } catch (globalError) {
    console.error(`Fatal execution error: ${globalError.message}\nStack: ${globalError.stack}`);
  } finally {
    lock.releaseLock();
    console.log(`[${new Date().toISOString()}] Automation cycle ended. Lock released.`);
  }
}

/**
 * Processes an individual Gmail message with error isolation and idempotency.
 */
function processSingleMessage(message, dbSheet, logSheet, dbIndex, driveFolder, processedLabel, errorLabel) {
  const messageId = message.getId();
  const rawSender = message.getFrom();
  const normalizedSender = extractSenderEmail(rawSender);

  console.log(`Evaluating Message ID: ${messageId} | Sender: "${rawSender}" (Normalized: "${normalizedSender}")`);

  // Check if sender exists in Database
  if (!dbIndex.emailMap.has(normalizedSender)) {
    if (dbIndex.duplicateEmails.has(normalizedSender)) {
      const duplicateRowNumbers = dbIndex.duplicateEmails.get(normalizedSender).join(", ");
      const errorMsg = `Ambiguous sender: "${normalizedSender}" appears multiple times in Database (Rows: ${duplicateRowNumbers}). Processing halted to prevent incorrect row updates.`;
      console.warn(errorMsg);
      message.getThread().addLabel(errorLabel);
      logAudit(logSheet, {
        messageId: messageId,
        senderEmail: normalizedSender,
        matchedRow: "AMBIGUOUS",
        filename: "N/A",
        mimeType: "N/A",
        sizeBytes: 0,
        driveFileId: "N/A",
        cellNotation: "N/A",
        status: "ERROR",
        details: errorMsg
      });
      return;
    }

    // Sender is not in database: Ignore silently per specification
    console.log(`Sender "${normalizedSender}" not found in database. Skipping without downloading attachments.`);
    return;
  }

  const matchedRow = dbIndex.emailMap.get(normalizedSender);

  // Extract eligible attachments
  const allAttachments = message.getAttachments({ includeInlineImages: !CONFIG.IGNORE_INLINE_IMAGES });
  const eligibleAttachments = filterEligibleAttachments(allAttachments);

  if (eligibleAttachments.length === 0) {
    console.log(`Message ${messageId} from "${normalizedSender}" contains no eligible attachments. Marking processed.`);
    message.getThread().addLabel(processedLabel);
    message.markRead();
    logAudit(logSheet, {
      messageId: messageId,
      senderEmail: normalizedSender,
      matchedRow: matchedRow,
      filename: "NONE",
      mimeType: "NONE",
      sizeBytes: 0,
      driveFileId: "N/A",
      cellNotation: "N/A",
      status: "SKIPPED",
      details: "Email matched sender but contained no eligible attachments."
    });
    return;
  }

  // Idempotency check: Retrieve previously completed uploads for this message
  const scriptProperties = PropertiesService.getScriptProperties();
  const trackingPropKey = `MSG_ATTACHMENTS_${messageId}`;
  let uploadedMap = {};
  const storedJson = scriptProperties.getProperty(trackingPropKey);
  if (storedJson) {
    try {
      uploadedMap = JSON.parse(storedJson);
    } catch (e) {
      uploadedMap = {};
    }
  }

  const uploadedFilesData = [];
  let hadUploadFailure = false;

  // Step A: Upload eligible attachments to Google Drive
  for (const attachment of eligibleAttachments) {
    const rawFilename = attachment.getName();

    if (uploadedMap[rawFilename]) {
      const existingFileId = uploadedMap[rawFilename];
      try {
        const existingFile = DriveApp.getFileById(existingFileId);
        console.log(`Attachment "${rawFilename}" was previously uploaded (${existingFileId}). Reusing file.`);
        uploadedFilesData.push({
          fileId: existingFileId,
          fileName: existingFile.getName(),
          fileUrl: existingFile.getUrl(),
          mimeType: existingFile.getMimeType(),
          sizeBytes: existingFile.getSize()
        });
        continue;
      } catch (checkErr) {
        console.warn(`Stored file ID ${existingFileId} invalid or inaccessible. Re-uploading.`);
      }
    }

    try {
      const uploadResult = executeWithBackoff(() => {
        return uploadAttachmentToDrive(driveFolder, attachment, messageId);
      });

      uploadedFilesData.push(uploadResult);
      uploadedMap[rawFilename] = uploadResult.fileId;
      scriptProperties.setProperty(trackingPropKey, JSON.stringify(uploadedMap));
      console.log(`Uploaded "${uploadResult.fileName}" to Drive -> ID: ${uploadResult.fileId}`);
    } catch (uploadError) {
      hadUploadFailure = true;
      const errorMsg = `Failed to upload attachment "${rawFilename}": ${uploadError.message}`;
      console.error(errorMsg);
      logAudit(logSheet, {
        messageId: messageId,
        senderEmail: normalizedSender,
        matchedRow: matchedRow,
        filename: rawFilename,
        mimeType: attachment.getContentType(),
        sizeBytes: attachment.getSize(),
        driveFileId: "FAILED",
        cellNotation: "N/A",
        status: "ERROR",
        details: errorMsg
      });
      break;
    }
  }

  if (hadUploadFailure || uploadedFilesData.length < eligibleAttachments.length) {
    message.getThread().addLabel(errorLabel);
    console.warn(`Message ${messageId} encountered partial upload failure. Preserved for subsequent retry.`);
    return;
  }

  // Step B: Write Drive Hyperlinks dynamically to matched row
  try {
    const writtenCells = writeDriveLinksToSheet(dbSheet, matchedRow, uploadedFilesData);

    // Step C: Log successful results to audit trail
    for (let i = 0; i < uploadedFilesData.length; i++) {
      const fileData = uploadedFilesData[i];
      const cellNotation = writtenCells[i] || "UNKNOWN";
      logAudit(logSheet, {
        messageId: messageId,
        senderEmail: normalizedSender,
        matchedRow: matchedRow,
        filename: fileData.fileName,
        mimeType: fileData.mimeType,
        sizeBytes: fileData.sizeBytes,
        driveFileId: fileData.fileId,
        cellNotation: cellNotation,
        status: "SUCCESS",
        details: `Successfully uploaded and linked in cell ${cellNotation}`
      });
    }

    // Step D: Finalize message state
    message.getThread().addLabel(processedLabel);
    message.getThread().removeLabel(errorLabel);
    message.markRead();

    // Clean up temporary tracking property upon 100% completion
    scriptProperties.deleteProperty(trackingPropKey);
    console.log(`Successfully completed all operations for Message ID: ${messageId}`);

  } catch (sheetWriteError) {
    const errorMsg = `Failed writing hyperlinks to Database for row ${matchedRow}: ${sheetWriteError.message}`;
    console.error(errorMsg);
    message.getThread().addLabel(errorLabel);
    logAudit(logSheet, {
      messageId: messageId,
      senderEmail: normalizedSender,
      matchedRow: matchedRow,
      filename: "N/A",
      mimeType: "N/A",
      sizeBytes: 0,
      driveFileId: "N/A",
      cellNotation: "N/A",
      status: "ERROR",
      details: errorMsg
    });
  }
}

/**
 * Searches Gmail for candidate unread messages containing attachments.
 * @param {number} batchSize
 * @returns {Array<GoogleAppsScript.Gmail.GmailMessage>}
 */
function scanUnreadCandidateMessages(batchSize) {
  const query = `is:unread has:attachment -label:"${CONFIG.PROCESSED_LABEL}" -label:"${CONFIG.ERROR_LABEL}" -in:spam -in:trash`;
  const threads = GmailApp.search(query, 0, batchSize);
  const eligibleMessages = [];

  for (const thread of threads) {
    const messages = thread.getMessages();
    for (const message of messages) {
      if (message.isUnread()) {
        eligibleMessages.push(message);
      }
    }
  }

  return eligibleMessages;
}

/**
 * Parses and normalizes an email address from a raw "From" header.
 * @param {string} fromHeader
 * @returns {string} Normalized lowercase email address.
 */
function extractSenderEmail(fromHeader) {
  if (!fromHeader) return "";
  
  const angleMatch = fromHeader.match(/<([^>]+)>/);
  if (angleMatch && angleMatch[1]) {
    return angleMatch[1].trim().toLowerCase();
  }

  const rawEmailMatch = fromHeader.match(/[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+/);
  if (rawEmailMatch) {
    return rawEmailMatch[0].trim().toLowerCase();
  }

  return fromHeader.trim().toLowerCase();
}

/**
 * Loads and indexes the database sheet in-memory.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} dbSheet
 * @returns {{ emailMap: Map<string, number>, duplicateEmails: Map<string, Array<number>> }}
 */
function loadDatabaseIndex(dbSheet) {
  const lastRow = dbSheet.getLastRow();
  const emailMap = new Map();
  const duplicateEmails = new Map();

  if (lastRow < 2) {
    return { emailMap, duplicateEmails };
  }

  const emailRangeValues = dbSheet.getRange(2, 2, lastRow - 1, 1).getValues();

  for (let i = 0; i < emailRangeValues.length; i++) {
    const rowNumber = i + 2;
    const rawVal = emailRangeValues[i][0];
    const normalizedEmail = (rawVal !== null && rawVal !== undefined) ? String(rawVal).trim().toLowerCase() : "";

    if (!normalizedEmail) continue;

    if (emailMap.has(normalizedEmail)) {
      if (!duplicateEmails.has(normalizedEmail)) {
        duplicateEmails.set(normalizedEmail, [emailMap.get(normalizedEmail)]);
      }
      duplicateEmails.get(normalizedEmail).push(rowNumber);
    } else {
      emailMap.set(normalizedEmail, rowNumber);
    }
  }

  duplicateEmails.forEach((rows, email) => {
    emailMap.delete(email);
  });

  return { emailMap, duplicateEmails };
}

/**
 * Filters message attachments against allowed extensions, size, and inline status.
 * @param {Array<GoogleAppsScript.Gmail.GmailAttachment>} attachments
 * @returns {Array<GoogleAppsScript.Gmail.GmailAttachment>}
 */
function filterEligibleAttachments(attachments) {
  if (!attachments || attachments.length === 0) return [];

  return attachments.filter(att => {
    if (CONFIG.IGNORE_INLINE_IMAGES) {
      if (typeof att.isInline === "function" && att.isInline()) {
        return false;
      }
      const mime = att.getContentType().toLowerCase();
      if (mime.startsWith("image/") && att.getSize() < 20480) {
        const name = att.getName().toLowerCase();
        if (name.includes("image00") || name.includes("sig") || name.includes("logo") || name.includes("icon")) {
          return false;
        }
      }
    }

    if (att.getSize() > CONFIG.MAX_ATTACHMENT_SIZE_BYTES) {
      console.warn(`Attachment "${att.getName()}" (${att.getSize()} bytes) exceeds limit. Skipping.`);
      return false;
    }

    if (CONFIG.ALLOWED_EXTENSIONS && CONFIG.ALLOWED_EXTENSIONS.length > 0) {
      const fileName = att.getName();
      const dotIndex = fileName.lastIndexOf(".");
      if (dotIndex === -1) return false;
      const ext = fileName.substring(dotIndex + 1).toLowerCase();
      if (!CONFIG.ALLOWED_EXTENSIONS.includes(ext)) {
        console.log(`Attachment "${fileName}" extension ".${ext}" is not in allowed list. Skipping.`);
        return false;
      }
    }

    return true;
  });
}

/**
 * Uploads an attachment to Google Drive, ensuring unique names.
 * @param {GoogleAppsScript.Drive.Folder} folder
 * @param {GoogleAppsScript.Gmail.GmailAttachment} attachment
 * @param {string} messageId
 * @returns {{ fileId: string, fileName: string, fileUrl: string, mimeType: string, sizeBytes: number }}
 */
function uploadAttachmentToDrive(folder, attachment, messageId) {
  const originalName = attachment.getName() || "untitled_attachment";
  const uniqueName = generateUniqueFileName(folder, originalName);
  
  const blob = attachment.copyBlob().setName(uniqueName);
  const createdFile = folder.createFile(blob);

  createdFile.setDescription(`Uploaded by Gmail Automation. Message ID: ${messageId} | Original Filename: ${originalName}`);

  return {
    fileId: createdFile.getId(),
    fileName: uniqueName,
    fileUrl: createdFile.getUrl(),
    mimeType: createdFile.getMimeType(),
    sizeBytes: createdFile.getSize()
  };
}

/**
 * Generates a unique filename within the destination folder to prevent collisions.
 * @param {GoogleAppsScript.Drive.Folder} folder
 * @param {string} filename
 * @returns {string}
 */
function generateUniqueFileName(folder, filename) {
  let safeName = filename;
  let counter = 1;

  const dotIndex = filename.lastIndexOf(".");
  const baseName = dotIndex !== -1 ? filename.substring(0, dotIndex) : filename;
  const extension = dotIndex !== -1 ? filename.substring(dotIndex) : "";

  while (folder.getFilesByName(safeName).hasNext()) {
    safeName = `${baseName} (${counter})${extension}`;
    counter++;
  }

  return safeName;
}

/**
 * Writes Drive hyperlinks into the matched row's document columns.
 * Fills available empty document cells first, and dynamically appends columns if needed.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} dbSheet
 * @param {number} matchedRow
 * @param {Array<Object>} uploadedFiles
 * @returns {Array<string>}
 */
function writeDriveLinksToSheet(dbSheet, matchedRow, uploadedFiles) {
  if (!uploadedFiles || uploadedFiles.length === 0) return [];

  const neededSlots = uploadedFiles.length;
  const currentMaxCols = dbSheet.getMaxColumns();

  const numColsToRead = Math.max(1, currentMaxCols - 2);
  const rowRange = dbSheet.getRange(matchedRow, 3, 1, numColsToRead);
  const rowValues = rowRange.getValues()[0];

  const availableColIndices = [];
  for (let i = 0; i < rowValues.length; i++) {
    const val = rowValues[i];
    if (val === "" || val === null || val === undefined) {
      availableColIndices.push(i + 3);
    }
  }

  const slotsAlreadyAvailable = availableColIndices.length;
  const additionalColumnsNeeded = Math.max(0, neededSlots - slotsAlreadyAvailable);

  if (additionalColumnsNeeded > 0) {
    ensureDocumentColumns(dbSheet, currentMaxCols + additionalColumnsNeeded);
    for (let c = 1; c <= additionalColumnsNeeded; c++) {
      availableColIndices.push(currentMaxCols + c);
    }
  }

  const targetColIndices = availableColIndices.slice(0, neededSlots);
  const updatedCellNotations = [];

  for (let i = 0; i < targetColIndices.length; i++) {
    const targetCol = targetColIndices[i];
    const file = uploadedFiles[i];
    const cellRange = dbSheet.getRange(matchedRow, targetCol);

    const escapedFileName = file.fileName.replace(/"/g, '""');
    const formula = `=HYPERLINK("${file.fileUrl}", "${escapedFileName}")`;
    
    cellRange.setFormula(formula);
    updatedCellNotations.push(cellRange.getA1Notation());
  }

  return updatedCellNotations;
}

/**
 * Ensures the sheet has at least targetTotalColumns and verifies headers.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @param {number} targetTotalColumns
 */
function ensureDocumentColumns(sheet, targetTotalColumns) {
  const currentMax = sheet.getMaxColumns();
  if (targetTotalColumns <= currentMax) {
    verifyAndFillHeaders(sheet, targetTotalColumns);
    return;
  }

  const columnsToAdd = targetTotalColumns - currentMax;
  sheet.insertColumnsAfter(currentMax, columnsToAdd);
  console.log(`Dynamically inserted ${columnsToAdd} new columns. New max columns: ${targetTotalColumns}`);

  verifyAndFillHeaders(sheet, targetTotalColumns);
}

/**
 * Checks row 1 headers from Column C onwards and populates missing headers with "Document N".
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @param {number} totalColumnsToCheck
 */
function verifyAndFillHeaders(sheet, totalColumnsToCheck) {
  if (totalColumnsToCheck < 3) return;

  const headerRange = sheet.getRange(1, 3, 1, totalColumnsToCheck - 2);
  const headers = headerRange.getValues()[0];
  let headersModified = false;

  for (let i = 0; i < headers.length; i++) {
    const docNumber = i + 1;
    if (!headers[i] || String(headers[i]).trim() === "") {
      headers[i] = `Document ${docNumber}`;
      headersModified = true;
    }
  }

  if (headersModified) {
    headerRange.setValues([headers]);
    console.log("Updated document column headers sequentially.");
  }
}

/**
 * Appends a structured audit entry to the log sheet.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} logSheet
 * @param {Object} logData
 */
function logAudit(logSheet, logData) {
  try {
    logSheet.appendRow([
      new Date().toISOString(),
      logData.messageId || "N/A",
      logData.senderEmail || "N/A",
      logData.matchedRow || "N/A",
      logData.filename || "N/A",
      logData.mimeType || "N/A",
      logData.sizeBytes !== undefined ? logData.sizeBytes : 0,
      logData.driveFileId || "N/A",
      logData.cellNotation || "N/A",
      logData.status || "INFO",
      logData.details || ""
    ]);
  } catch (err) {
    console.error(`Failed to write entry to audit log sheet: ${err.message}`);
  }
}

/**
 * Executes an operation with exponential backoff.
 * @param {Function} operation
 * @returns {*}
 */
function executeWithBackoff(operation) {
  let attempts = 0;
  while (attempts < CONFIG.MAX_RETRY_ATTEMPTS) {
    try {
      return operation();
    } catch (error) {
      attempts++;
      if (attempts >= CONFIG.MAX_RETRY_ATTEMPTS) {
        throw error;
      }
      const delay = CONFIG.RETRY_BASE_DELAY_MS * Math.pow(2, attempts - 1) + Math.random() * 500;
      console.warn(`Transient error: "${error.message}". Retrying attempt ${attempts}/${CONFIG.MAX_RETRY_ATTEMPTS} in ${Math.round(delay)}ms...`);
      Utilities.sleep(delay);
    }
  }
}

/**
 * Resolves the Google Spreadsheet instance.
 * @returns {GoogleAppsScript.Spreadsheet.Spreadsheet}
 */
function getSpreadsheetInstance() {
  if (CONFIG.SPREADSHEET_ID && CONFIG.SPREADSHEET_ID.trim() !== "") {
    return SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID.trim());
  }
  return SpreadsheetApp.getActiveSpreadsheet();
}

/**
 * Validates that row 1 headers in Columns A and B match Country and Email.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 */
function validateDatabaseHeaders(sheet) {
  const headers = sheet.getRange(1, 1, 1, 2).getValues()[0];
  const colA = String(headers[0]).trim().toLowerCase();
  const colB = String(headers[1]).trim().toLowerCase();

  if (colA !== "country" || colB !== "email") {
    console.warn(`Warning: Database sheet headers in row 1 are ["${headers[0]}", "${headers[1]}"]. Expected ["Country", "Email"].`);
  }
}

/**
 * Finds the database sheet: first looks for CONFIG.SHEET_NAME,
 * otherwise picks the first sheet that is not the log sheet.
 * @param {GoogleAppsScript.Spreadsheet.Spreadsheet} ss
 * @returns {GoogleAppsScript.Spreadsheet.Sheet|null}
 */
function getDatabaseSheet(ss) {
  let dbSheet = ss.getSheetByName(CONFIG.SHEET_NAME);
  if (dbSheet) return dbSheet;
  
  const sheets = ss.getSheets();
  for (const s of sheets) {
    if (s.getName() !== CONFIG.LOG_SHEET_NAME) {
      return s;
    }
  }
  return null;
}
