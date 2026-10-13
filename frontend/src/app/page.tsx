"use client";

import React, { useState, useEffect } from "react";
import {
  Globe,
  Mail,
  FileText,
  Play,
  RotateCw,
  ExternalLink,
  ShieldCheck,
  AlertTriangle,
  FolderOpen,
  TableProperties,
  Search,
  CheckCircle2,
  Clock,
  RefreshCw,
  Calendar,
  Sparkles,
} from "lucide-react";

interface ScanStats {
  total_messages_scanned: number;
  matched_messages: number;
  ambiguous_messages: number;
  uploaded_attachments: number;
  failed_attachments: number;
  countries_with_docs: number;
}

interface ScanState {
  is_running: boolean;
  scan_type: string | null;
  progress_percent: number;
  status_message: string;
  current_sender: string;
  current_country: string;
  evaluated_count: number;
  matched_count: number;
  attachments_count: number;
  last_error: string | null;
}

interface SystemStatus {
  is_authenticated: boolean;
  historical_cutoff: string;
  historical_completed: boolean;
  last_incremental_sync: string;
  spreadsheet_id: string;
  drive_folder_id: string;
  stats: ScanStats;
  scan_state: ScanState;
}

interface DocumentItem {
  filename: string;
  url: string;
}

interface CountryRecord {
  country_name: string;
  source_type?: string;
  official_emails: string;
  row_order: number;
  doc_count: number;
  documents: DocumentItem[];
}

interface AuditLog {
  timestamp: string;
  level: string;
  action: string;
  message_id: string;
  sender_email: string;
  country_name: string;
  filename: string;
  details: string;
}

interface UnmatchedMessage {
  message_id: string;
  sender_normalized: string;
  subject: string;
  date_received: string;
  status: string;
  error_details: string;
}

const API_BASE = "http://127.0.0.1:8000";

export default function Dashboard() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [countries, setCountries] = useState<CountryRecord[]>([]);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [unmatched, setUnmatched] = useState<UnmatchedMessage[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [activeTab, setActiveTab] = useState<"registry" | "unmatched" | "logs">("registry");
  const [loading, setLoading] = useState(false);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // Particular Date scan controls
  const [selectedScanDate, setSelectedScanDate] = useState(() => {
    return new Date().toISOString().split("T")[0];
  });
  const [dateScanMode, setDateScanMode] = useState<"single" | "from_date" | "range">("single");
  const [selectedEndDate, setSelectedEndDate] = useState(() => {
    return new Date().toISOString().split("T")[0];
  });

  const showToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 4000);
  };

  const fetchStatus = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/status`);
      if (res.ok) {
        const data = await res.json();
        setStatus(data);
      }
    } catch (e) {
      console.error("Status fetch error", e);
    }
  };

  const fetchCountries = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/countries`);
      if (res.ok) {
        const data = await res.json();
        setCountries(data.countries || []);
      }
    } catch (e) {
      console.error("Countries fetch error", e);
    }
  };

  const fetchLogs = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/logs`);
      if (res.ok) {
        const data = await res.json();
        setLogs(data.logs || []);
      }
    } catch (e) {
      console.error("Logs fetch error", e);
    }
  };

  const fetchUnmatched = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/unmatched`);
      if (res.ok) {
        const data = await res.json();
        setUnmatched(data.unmatched || []);
      }
    } catch (e) {
      console.error("Unmatched fetch error", e);
    }
  };

  const refreshAll = () => {
    fetchStatus();
    fetchCountries();
    fetchLogs();
    fetchUnmatched();
  };

  useEffect(() => {
    refreshAll();
    const interval = setInterval(() => {
      fetchStatus();
    }, 2500);
    return () => clearInterval(interval);
  }, []);

  const handleAuthenticate = async () => {
    setLoading(true);
    showToast(status?.logged_in_email ? `Opening Google Sign-in for ${status.logged_in_email}...` : "Opening Google Sign-in...");
    try {
      const res = await fetch(`${API_BASE}/api/auth/url`);
      const data = await res.json();
      if (data.status === "success" && data.auth_url) {
        window.location.href = data.auth_url;
      } else {
        showToast(`Auth error: ${data.message}`);
      }
    } catch (err: any) {
      showToast(`Auth request failed: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleStartHistoricalScan = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/scan/historical`, { method: "POST" });
      const data = await res.json();
      showToast(data.message);
      fetchStatus();
    } catch (err: any) {
      showToast(`Scan failed: ${err.message}`);
    }
  };

  const handleStartIncrementalScan = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/scan/incremental`, { method: "POST" });
      const data = await res.json();
      showToast(data.message);
      fetchStatus();
    } catch (err: any) {
      showToast(`Incremental scan failed: ${err.message}`);
    }
  };

  const handleStartDateScan = async () => {
    if (!selectedScanDate) {
      showToast("Please pick a date first.");
      return;
    }
    try {
      const payload: any = {
        date: selectedScanDate,
        mode: dateScanMode,
      };
      if (dateScanMode === "range") {
        if (!selectedEndDate) {
          showToast("Please choose an end date for range mode.");
          return;
        }
        payload.end_date = selectedEndDate;
      }
      const res = await fetch(`${API_BASE}/api/scan/date`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      showToast(data.message);
      fetchStatus();
    } catch (err: any) {
      showToast(`Date scan failed: ${err.message}`);
    }
  };

  const filteredCountries = countries.filter(
    (c) =>
      c.country_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (c.official_emails && c.official_emails.toLowerCase().includes(searchQuery.toLowerCase()))
  );

  return (
    <div className="min-h-screen bg-[#f8fafc] text-slate-800 font-sans pb-16">
      {/* Toast Notification */}
      {toastMessage && (
        <div className="fixed top-6 right-6 z-50 bg-slate-900 text-white px-5 py-3 rounded-2xl shadow-xl flex items-center gap-2 border border-slate-700 animate-in fade-in slide-in-from-top-2">
          <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          <span className="text-xs font-medium">{toastMessage}</span>
        </div>
      )}

      {/* Top Navbar Header */}
      <header className="bg-white border-b border-slate-200/80 sticky top-0 z-30 shadow-xs backdrop-blur-md bg-white/95">
        <div className="max-w-7xl mx-auto px-6 py-4 flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
          <div className="flex items-center gap-3.5">
            <div className="p-2.5 bg-blue-50 text-blue-600 rounded-xl border border-blue-100 shadow-xs">
              <Globe className="w-6 h-6" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg md:text-xl font-bold tracking-tight text-slate-900">
                  Gmail Document Registry
                </h1>
                <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-blue-50 text-blue-700 border border-blue-100">
                  Export Edition
                </span>
              </div>
              <p className="text-xs text-slate-500 font-normal mt-0.5">
                Automated Historical & Incoming Email Ingestion with Country-Wise Matching
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2.5 w-full md:w-auto justify-end">
            {status?.is_authenticated ? (
              <div className="flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-emerald-50 border border-emerald-200/80 text-emerald-700 text-xs font-semibold shadow-xs">
                <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                <ShieldCheck className="w-3.5 h-3.5" />
                <span>Connected: {status?.logged_in_email || "Authorized"}</span>
              </div>
            ) : (
              <button
                onClick={handleAuthenticate}
                disabled={loading}
                className="flex items-center gap-2 px-4 py-2 rounded-xl bg-amber-500 hover:bg-amber-600 text-white font-semibold text-xs shadow-xs transition"
              >
                <Mail className="w-4 h-4" />
                <span>Connect {status?.logged_in_email || "Google Account"}</span>
              </button>
            )}

            <a
              href={`https://drive.google.com/drive/folders/${status?.drive_folder_id || ""}`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-white hover:bg-slate-50 border border-slate-200 text-slate-700 font-medium text-xs shadow-xs transition hover:border-slate-300"
            >
              <FolderOpen className="w-3.5 h-3.5 text-amber-500" />
              <span>Drive Folder</span>
              <ExternalLink className="w-3 h-3 text-slate-400" />
            </a>

            <a
              href={`https://docs.google.com/spreadsheets/d/${status?.spreadsheet_id || ""}/edit`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-emerald-50/70 hover:bg-emerald-100/80 border border-emerald-200 text-emerald-800 font-medium text-xs shadow-xs transition"
            >
              <TableProperties className="w-3.5 h-3.5 text-emerald-600" />
              <span>Google Sheet</span>
              <ExternalLink className="w-3 h-3 text-emerald-600" />
            </a>

            <button
              onClick={refreshAll}
              className="p-2 rounded-xl bg-white hover:bg-slate-50 border border-slate-200 text-slate-500 hover:text-slate-800 shadow-xs transition hover:border-slate-300"
              title="Refresh Data"
            >
              <RefreshCw className="w-4 h-4" />
            </button>
          </div>
        </div>
      </header>

      {/* Main Container */}
      <main className="max-w-7xl mx-auto px-6 mt-8 space-y-6">
        {/* Metric Cards Grid */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="p-5 rounded-2xl bg-white border border-slate-200/90 shadow-xs flex items-center justify-between hover:border-slate-300 transition">
            <div>
              <span className="text-xs font-medium text-slate-500">Total Scanned</span>
              <div className="text-2xl lg:text-3xl font-extrabold text-slate-900 mt-1">
                {status?.stats.total_messages_scanned ?? 0}
              </div>
              <span className="text-[11px] text-slate-400 font-medium mt-0.5 block">Historical & new emails</span>
            </div>
            <div className="p-3 bg-blue-50 text-blue-600 rounded-2xl border border-blue-100">
              <Mail className="w-5 h-5" />
            </div>
          </div>

          <div className="p-5 rounded-2xl bg-white border border-slate-200/90 shadow-xs flex items-center justify-between hover:border-slate-300 transition">
            <div>
              <span className="text-xs font-medium text-slate-500">Matched Countries</span>
              <div className="text-2xl lg:text-3xl font-extrabold text-indigo-600 mt-1">
                {status?.stats.countries_with_docs ?? 0}{" "}
                <span className="text-xs font-semibold text-slate-400">/ 185</span>
              </div>
              <span className="text-[11px] text-slate-400 font-medium mt-0.5 block">Countries with files</span>
            </div>
            <div className="p-3 bg-indigo-50 text-indigo-600 rounded-2xl border border-indigo-100">
              <Globe className="w-5 h-5" />
            </div>
          </div>

          <div className="p-5 rounded-2xl bg-white border border-slate-200/90 shadow-xs flex items-center justify-between hover:border-slate-300 transition">
            <div>
              <span className="text-xs font-medium text-slate-500">Attachments Uploaded</span>
              <div className="text-2xl lg:text-3xl font-extrabold text-emerald-600 mt-1">
                {status?.stats.uploaded_attachments ?? 0}
              </div>
              <span className="text-[11px] text-slate-400 font-medium mt-0.5 block">Stored in Google Drive</span>
            </div>
            <div className="p-3 bg-emerald-50 text-emerald-600 rounded-2xl border border-emerald-100">
              <FileText className="w-5 h-5" />
            </div>
          </div>

          <div className="p-5 rounded-2xl bg-white border border-slate-200/90 shadow-xs flex items-center justify-between hover:border-slate-300 transition">
            <div>
              <span className="text-xs font-medium text-slate-500">Ambiguous Senders</span>
              <div className="text-2xl lg:text-3xl font-extrabold text-slate-800 mt-1">
                {status?.stats.ambiguous_messages ?? 0}
              </div>
              <span className="text-[11px] text-slate-400 font-medium mt-0.5 block">Shared missions flagged</span>
            </div>
            <div className="p-3 bg-amber-50 text-amber-600 rounded-2xl border border-amber-100">
              <AlertTriangle className="w-5 h-5" />
            </div>
          </div>
        </div>

        {/* Execution Control Center Card */}
        <div className="p-6 rounded-2xl bg-white border border-slate-200 shadow-xs space-y-5">
          <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
            <div>
              <h2 className="text-base font-bold text-slate-900 flex items-center gap-2">
                Execution Control Center
                <Sparkles className="w-4 h-4 text-blue-500" />
              </h2>
              <p className="text-xs text-slate-500 mt-0.5">
                Trigger full historical sweep, incremental sync, or scan emails of a specific date
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2.5">
              <button
                onClick={handleStartHistoricalScan}
                disabled={status?.scan_state.is_running || !status?.is_authenticated}
                className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-semibold text-xs shadow-xs transition active:scale-95"
              >
                <Play className="w-3.5 h-3.5 fill-current" />
                <span>Start Full Historical Scan</span>
              </button>

              <button
                onClick={handleStartIncrementalScan}
                disabled={status?.scan_state.is_running || !status?.is_authenticated}
                className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-white hover:bg-slate-50 disabled:opacity-50 text-slate-700 border border-slate-200 font-semibold text-xs shadow-xs transition hover:border-slate-300 active:scale-95"
              >
                <RotateCw className="w-3.5 h-3.5 text-slate-500" />
                <span>Run Incremental Scan</span>
              </button>
            </div>
          </div>

          {/* Quick Date-Specific Scanner Bar */}
          <div className="p-4 rounded-xl bg-amber-50/60 border border-amber-200/70 flex flex-col lg:flex-row items-start lg:items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="p-2.5 bg-amber-100 text-amber-800 rounded-xl border border-amber-200 shadow-2xs">
                <Calendar className="w-4 h-4" />
              </div>
              <div>
                <h3 className="text-xs font-bold text-slate-900 flex items-center gap-2">
                  Scan Mails of a Particular Date
                  <span className="px-2 py-0.5 rounded-full text-[10px] bg-amber-100 text-amber-800 font-semibold border border-amber-200/60">
                    Fast Mode
                  </span>
                </h3>
                <p className="text-[11px] text-slate-600 mt-0.5">
                  Target only emails received on a specific date without re-scanning your entire historical inbox
                </p>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2.5 w-full lg:w-auto justify-end">
              <select
                value={dateScanMode}
                onChange={(e) => setDateScanMode(e.target.value as any)}
                disabled={status?.scan_state.is_running || !status?.is_authenticated}
                className="bg-white border border-slate-300 rounded-lg px-3 py-1.5 text-xs text-slate-800 font-medium focus:outline-none focus:ring-2 focus:ring-amber-500/20 focus:border-amber-500 shadow-2xs"
              >
                <option value="single">Single Date (Specific Day)</option>
                <option value="from_date">From Date Onwards</option>
                <option value="range">Custom Date Range</option>
              </select>

              <div className="flex items-center gap-2">
                <input
                  type="date"
                  value={selectedScanDate}
                  onChange={(e) => setSelectedScanDate(e.target.value)}
                  disabled={status?.scan_state.is_running || !status?.is_authenticated}
                  className="bg-white border border-slate-300 rounded-lg px-3 py-1.5 text-xs text-slate-800 font-medium focus:outline-none focus:ring-2 focus:ring-amber-500/20 focus:border-amber-500 shadow-2xs"
                />

                {dateScanMode === "range" && (
                  <>
                    <span className="text-xs text-slate-500 font-medium">to</span>
                    <input
                      type="date"
                      value={selectedEndDate}
                      onChange={(e) => setSelectedEndDate(e.target.value)}
                      disabled={status?.scan_state.is_running || !status?.is_authenticated}
                      className="bg-white border border-slate-300 rounded-lg px-3 py-1.5 text-xs text-slate-800 font-medium focus:outline-none focus:ring-2 focus:ring-amber-500/20 focus:border-amber-500 shadow-2xs"
                    />
                  </>
                )}
              </div>

              <button
                onClick={handleStartDateScan}
                disabled={status?.scan_state.is_running || !status?.is_authenticated || !selectedScanDate}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-700 disabled:opacity-50 text-white font-semibold text-xs shadow-xs transition active:scale-95"
              >
                <Calendar className="w-3.5 h-3.5" />
                <span>Scan Selected Date</span>
              </button>
            </div>
          </div>

          {/* Active Progress Bar or Status */}
          {status?.scan_state.is_running ? (
            <div className="pt-2 space-y-2 border-t border-slate-100">
              <div className="flex items-center justify-between text-xs">
                <span className="text-blue-700 font-semibold flex items-center gap-1.5 animate-pulse">
                  <span>⚡</span> {status.scan_state.status_message}
                </span>
                <span className="text-slate-700 font-bold">
                  {status.scan_state.progress_percent}%
                </span>
              </div>
              <div className="w-full bg-slate-100 h-2.5 rounded-full overflow-hidden border border-slate-200/60">
                <div
                  className="bg-blue-600 h-full rounded-full transition-all duration-300"
                  style={{ width: `${Math.max(5, status.scan_state.progress_percent)}%` }}
                />
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500 pt-3 border-t border-slate-100">
              <div className="flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5 text-slate-400" />
                <span>Historical Cutoff:</span>
                <code className="text-slate-700 bg-slate-100 px-1.5 py-0.5 rounded font-mono text-[11px]">
                  {status?.historical_cutoff ? status.historical_cutoff.slice(0, 19).replace("T", " ") : "Not initialized"}
                </code>
              </div>
              <span className="text-slate-300">•</span>
              <div className="flex items-center gap-1.5">
                <span>System Status:</span>
                <span
                  className={`font-semibold px-2 py-0.5 rounded-full text-[11px] ${
                    status?.historical_completed
                      ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                      : "bg-slate-100 text-slate-600"
                  }`}
                >
                  {status?.historical_completed ? "Ready / Synced" : "Pending First Scan"}
                </span>
              </div>
            </div>
          )}
        </div>

        {/* Tab Navigation and Search Bar */}
        <div className="space-y-4">
          <div className="flex flex-col md:flex-row md:items-center justify-between border-b border-slate-200 gap-3">
            <div className="flex items-center gap-2">
              <button
                onClick={() => setActiveTab("registry")}
                className={`pb-3 px-3 text-xs font-bold border-b-2 transition flex items-center gap-2 ${
                  activeTab === "registry"
                    ? "border-blue-600 text-blue-600"
                    : "border-transparent text-slate-500 hover:text-slate-900"
                }`}
              >
                <span>Country Registry</span>
                <span className="px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 text-[11px] font-semibold">
                  {countries.length}
                </span>
              </button>

              <button
                onClick={() => setActiveTab("unmatched")}
                className={`pb-3 px-3 text-xs font-bold border-b-2 transition flex items-center gap-2 ${
                  activeTab === "unmatched"
                    ? "border-blue-600 text-blue-600"
                    : "border-transparent text-slate-500 hover:text-slate-900"
                }`}
              >
                <span>Ambiguous / Unmatched</span>
                <span className="px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 text-[11px] font-semibold">
                  {unmatched.length}
                </span>
              </button>

              <button
                onClick={() => setActiveTab("logs")}
                className={`pb-3 px-3 text-xs font-bold border-b-2 transition flex items-center gap-2 ${
                  activeTab === "logs"
                    ? "border-blue-600 text-blue-600"
                    : "border-transparent text-slate-500 hover:text-slate-900"
                }`}
              >
                <span>Audit Logs</span>
                <span className="px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 text-[11px] font-semibold">
                  {logs.length}
                </span>
              </button>
            </div>

            {activeTab === "registry" && (
              <div className="relative pb-2 md:pb-0 w-full md:w-80">
                <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-2.5" />
                <input
                  type="text"
                  placeholder="Search country or email address..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-9 pr-3 py-1.5 bg-white border border-slate-200 rounded-xl text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 shadow-2xs transition"
                />
              </div>
            )}
          </div>

          {/* TAB 1: COUNTRY REGISTRY */}
          {activeTab === "registry" && (
            <div className="space-y-4">
              {(() => {
                const maxDocsCount = Math.max(
                  3,
                  ...countries.map((c) => (c.documents ? c.documents.length : 0))
                );

                return (
                  <div className="rounded-2xl border border-slate-200 overflow-x-auto bg-white shadow-xs">
                    <table className="w-full text-left text-xs whitespace-nowrap">
                      <thead className="bg-slate-50/90 border-b border-slate-200 text-slate-600 uppercase tracking-wider font-bold text-[11px]">
                        <tr>
                          <th className="py-3 px-4 w-12 text-slate-400">#</th>
                          <th className="py-3 px-4 w-48 text-slate-700">Country Name</th>
                          <th className="py-3 px-4 w-40 text-slate-700">Source Type</th>
                          <th className="py-3 px-4 w-64 text-slate-700">Email Address</th>
                          {Array.from({ length: maxDocsCount }, (_, i) => (
                            <th key={i} className="py-3 px-4 min-w-[220px] text-slate-700">
                              Document {i + 1}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {filteredCountries.length > 0 ? (
                          filteredCountries.map((c) => (
                            <tr key={c.country_name} className="hover:bg-slate-50/70 transition-colors">
                              <td className="py-3 px-4 text-slate-400 font-mono text-[11px]">{c.row_order}</td>
                              <td className="py-3 px-4 font-bold text-slate-900">{c.country_name}</td>
                              <td className="py-3 px-4">
                                <span
                                  className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold border ${
                                    c.source_type === "Export Inquiry"
                                      ? "bg-amber-50 text-amber-800 border-amber-200/80"
                                      : "bg-blue-50 text-blue-700 border-blue-200/80"
                                  }`}
                                >
                                  {c.source_type === "Export Inquiry" ? "🚢 Export Inquiry" : "🏛️ Indian Embassy"}
                                </span>
                              </td>
                              <td className="py-3 px-4 text-slate-600 font-mono text-xs break-all">
                                {c.official_emails || "—"}
                              </td>
                              {Array.from({ length: maxDocsCount }, (_, i) => {
                                const doc = c.documents && c.documents[i];
                                return (
                                  <td key={i} className="py-3 px-4">
                                    {doc ? (
                                      <a
                                        href={doc.url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-blue-50 hover:bg-blue-100/80 border border-blue-200 text-blue-700 transition text-[11px] font-medium max-w-[240px] shadow-2xs"
                                        title={doc.filename}
                                      >
                                        <FileText className="w-3.5 h-3.5 text-blue-600 flex-shrink-0" />
                                        <span className="truncate">{doc.filename}</span>
                                        <ExternalLink className="w-3 h-3 text-blue-500 flex-shrink-0" />
                                      </a>
                                    ) : (
                                      <span className="text-slate-300 font-mono text-xs">—</span>
                                    )}
                                  </td>
                                );
                              })}
                            </tr>
                          ))
                        ) : (
                          <tr>
                            <td colSpan={4 + maxDocsCount} className="py-12 text-center text-slate-400">
                              No countries matching &quot;{searchQuery}&quot;
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                );
              })()}
            </div>
          )}

          {/* TAB 2: AMBIGUOUS / UNMATCHED */}
          {activeTab === "unmatched" && (
            <div className="rounded-2xl border border-slate-200 overflow-hidden bg-white shadow-xs">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50/90 border-b border-slate-200 text-slate-600 uppercase tracking-wider font-bold text-[11px]">
                  <tr>
                    <th className="py-3 px-4">Sender Email</th>
                    <th className="py-3 px-4">Subject</th>
                    <th className="py-3 px-4">Date</th>
                    <th className="py-3 px-4">Status</th>
                    <th className="py-3 px-4">Details</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {unmatched.length > 0 ? (
                    unmatched.map((m) => (
                      <tr key={m.message_id} className="hover:bg-slate-50/70 transition-colors">
                        <td className="py-3 px-4 font-mono font-medium text-slate-900">{m.sender_normalized}</td>
                        <td className="py-3 px-4 text-slate-700">{m.subject || "(No Subject)"}</td>
                        <td className="py-3 px-4 text-slate-500">{m.date_received}</td>
                        <td className="py-3 px-4">
                          <span
                            className={`px-2 py-0.5 rounded text-[10px] font-semibold border ${
                              m.status === "AMBIGUOUS"
                                ? "bg-amber-50 text-amber-700 border-amber-200"
                                : "bg-slate-100 text-slate-600 border-slate-200"
                            }`}
                          >
                            {m.status}
                          </span>
                        </td>
                        <td className="py-3 px-4 text-slate-500">{m.error_details || "—"}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={5} className="py-12 text-center text-slate-400">
                        No ambiguous or unmatched senders recorded. All eligible emails have been matched!
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}

          {/* TAB 3: AUDIT LOGS */}
          {activeTab === "logs" && (
            <div className="rounded-2xl border border-slate-200 overflow-hidden bg-white shadow-xs">
              <table className="w-full text-left text-xs font-mono">
                <thead className="bg-slate-50/90 border-b border-slate-200 text-slate-600 uppercase tracking-wider font-bold font-sans text-[11px]">
                  <tr>
                    <th className="py-3 px-4 w-44">Timestamp</th>
                    <th className="py-3 px-4 w-20">Level</th>
                    <th className="py-3 px-4 w-48">Action</th>
                    <th className="py-3 px-4">Details</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {logs.length > 0 ? (
                    logs.map((l, i) => (
                      <tr key={i} className="hover:bg-slate-50/70 transition-colors">
                        <td className="py-2.5 px-4 text-slate-500 text-[11px]">{l.timestamp}</td>
                        <td className="py-2.5 px-4">
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] font-bold border ${
                              l.level === "ERROR"
                                ? "bg-rose-50 text-rose-700 border-rose-200"
                                : l.level === "WARNING"
                                ? "bg-amber-50 text-amber-700 border-amber-200"
                                : "bg-slate-100 text-slate-700 border-slate-200"
                            }`}
                          >
                            {l.level}
                          </span>
                        </td>
                        <td className="py-2.5 px-4 text-blue-700 font-semibold">{l.action}</td>
                        <td className="py-2.5 px-4 text-slate-700 break-all">{l.details}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={4} className="py-12 text-center text-slate-400 font-sans">
                        No audit logs recorded yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
