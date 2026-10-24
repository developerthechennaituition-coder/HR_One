import { HRONE_API_BASE } from "../config/api.js";
import { useCallback, useEffect, useState } from "react";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || HRONE_API_BASE;
const ESSL_API_URL = `${API_BASE_URL}/api/attendance/essl`;

const todayInIndia = () => {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(
    parts.map(({ type, value }) => [type, value]),
  );
  return `${values.year}-${values.month}-${values.day}`;
};

const emptyConfig = {
  configured: false,
  api_url: "",
  method: "GET",
  auth_type: "none",
  api_key_header: "X-API-Key",
  records_path: "",
  employee_id_path: "",
  timestamp_path: "",
  punch_id_path: "",
  device_id_path: "",
  from_date_parameter: "",
  to_date_parameter: "",
  timestamp_format: "",
};

function formatDateTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
  });
}

function EsslAdminSettings({ session }) {
  const token = session?.token;
  const [config, setConfig] = useState(emptyConfig);
  const [employees, setEmployees] = useState([]);
  const [history, setHistory] = useState([]);
  const [issues, setIssues] = useState([]);
  const [fromDate, setFromDate] = useState(todayInIndia);
  const [toDate, setToDate] = useState(todayInIndia);
  const [requestParamsText, setRequestParamsText] = useState("");
  const [requestBodyText, setRequestBodyText] = useState("");
  const [requestHeadersText, setRequestHeadersText] = useState("");
  const [authSecret, setAuthSecret] = useState("");
  const [authUsername, setAuthUsername] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [importFile, setImportFile] = useState(null);
  const [importing, setImporting] = useState(false);
  const [importSummary, setImportSummary] = useState(null);
  const [savingMappingId, setSavingMappingId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [syncSummary, setSyncSummary] = useState(null);
  const lastSuccessfulSync = history.find(
    (item) => item.status === "COMPLETED",
  );

  const headers = useCallback(() => {
    if (!token) throw new Error("Login session not found. Please login again.");
    return {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    };
  }, [token]);

  const request = useCallback(
    async (path, options = {}) => {
      const response = await fetch(`${ESSL_API_URL}${path}`, {
        ...options,
        headers: { ...headers(), ...options.headers },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data?.detail || "ESSL request failed.");
      }
      return data;
    },
    [headers],
  );

  const loadAdminData = useCallback(async () => {
    try {
      const [savedConfig, savedEmployees, savedHistory, savedIssues] =
        await Promise.all([
          request("/config"),
          request("/employees"),
          request("/sync-history"),
          request("/issues"),
        ]);
      setError("");
      setConfig({ ...emptyConfig, ...savedConfig });
      setEmployees(savedEmployees);
      setHistory(savedHistory);
      setIssues(savedIssues);
    } catch (loadError) {
      setError(loadError.message || "Unable to load ESSL settings.");
    } finally {
      setLoading(false);
    }
  }, [request]);

  useEffect(() => {
    loadAdminData();
  }, [loadAdminData]);

  const saveConfig = async (event) => {
    event.preventDefault();
    try {
      setSaving(true);
      setError("");
      setNotice("");
      const payload = {
        api_url: config.api_url,
        method: config.method,
        auth_type: config.auth_type,
        api_key_header: config.api_key_header,
        records_path: config.records_path,
        employee_id_path: config.employee_id_path,
        timestamp_path: config.timestamp_path,
        punch_id_path: config.punch_id_path || null,
        device_id_path: config.device_id_path || null,
        from_date_parameter: config.from_date_parameter || null,
        to_date_parameter: config.to_date_parameter || null,
        timestamp_format: config.timestamp_format || null,
      };

      if (requestParamsText.trim()) {
        payload.request_params = JSON.parse(requestParamsText);
      } else if (!config.has_request_parameters) {
        payload.request_params = {};
      }
      if (requestBodyText.trim()) {
        payload.request_body = JSON.parse(requestBodyText);
      } else if (!config.has_request_body) {
        payload.request_body = {};
      }
      if (requestHeadersText.trim()) {
        payload.request_headers = JSON.parse(requestHeadersText);
      } else if (!config.has_request_headers) {
        payload.request_headers = {};
      }
      if (authSecret) payload.auth_secret = authSecret;
      if (authUsername) payload.auth_username = authUsername;
      if (authPassword) payload.auth_password = authPassword;

      const saved = await request("/config", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      setConfig({ ...emptyConfig, ...saved });
      setAuthSecret("");
      setAuthUsername("");
      setAuthPassword("");
      setRequestParamsText("");
      setRequestBodyText("");
      setRequestHeadersText("");
      setNotice("ESSL API configuration saved securely.");
    } catch (saveError) {
      setError(
        saveError instanceof SyntaxError
          ? "Request parameters and body must be valid JSON objects."
          : saveError.message || "Unable to save ESSL configuration.",
      );
    } finally {
      setSaving(false);
    }
  };

  const testConnection = async () => {
    try {
      setTesting(true);
      setError("");
      setNotice("");
      const result = await request("/test-connection", { method: "POST" });
      setNotice(
        `${result.message} ${result.records_returned} punch records were returned; no attendance was changed.`,
      );
    } catch (testError) {
      setError(testError.message || "ESSL connection failed.");
    } finally {
      setTesting(false);
    }
  };

  const syncAttendance = async () => {
    try {
      setSyncing(true);
      setError("");
      setNotice("");
      setSyncSummary(null);
      const summary = await request("/sync", {
        method: "POST",
        body: JSON.stringify({ from_date: fromDate, to_date: toDate }),
      });
      setSyncSummary(summary);
      setNotice("ESSL attendance synchronized successfully.");
      await loadAdminData();
    } catch (syncError) {
      await loadAdminData();
      setError(syncError.message || "ESSL synchronization failed.");
    } finally {
      setSyncing(false);
    }
  };

  const saveMapping = async (employee) => {
    try {
      setSavingMappingId(employee.user_id);
      setError("");
      setNotice("");
      await request(`/employees/${employee.user_id}`, {
        method: "PUT",
        body: JSON.stringify({
          essl_employee_id: employee.essl_employee_id || null,
        }),
      });
      setNotice(`ESSL mapping saved for ${employee.name}.`);
      await loadAdminData();
    } catch (mappingError) {
      setError(mappingError.message || "Unable to save employee mapping.");
    } finally {
      setSavingMappingId("");
    }
  };

  if (loading) {
    return (
      <div className="card border-0 shadow-sm mb-4">
        <div className="card-body text-muted">Loading ESSL configuration...</div>
      </div>
    );
  }

  return (
    <section className="card border-0 shadow-sm mb-4">
      <div className="card-body">
        <div className="d-flex flex-wrap justify-content-between align-items-start gap-2 mb-3">
          <div>
            <h4 className="mb-1">ESSL Biometric Attendance</h4>
            <p className="text-muted mb-0">
              Configure the company API and map its employee IDs to HRMS users.
              API credentials and request values are encrypted on the server.
            </p>
          </div>
          <span className={`badge ${config.configured ? "text-bg-success" : "text-bg-secondary"}`}>
            {config.configured ? "Configured" : "Not configured"}
          </span>
        </div>
        <p className="small text-muted">
          Last successful synchronization: {formatDateTime(lastSuccessfulSync?.completed_at)}
        </p>

        {error && <div className="alert alert-danger" role="alert">{error}</div>}
        {notice && <div className="alert alert-success" role="status">{notice}</div>}

        <form onSubmit={saveConfig} className="border rounded p-3 mb-4">
          <h5>API Configuration</h5>
          <div className="row g-3">
            <div className="col-lg-7">
              <label className="form-label">API URL / endpoint</label>
              <input
                className="form-control"
                type="url"
                required
                value={config.api_url || ""}
                onChange={(event) => setConfig({ ...config, api_url: event.target.value })}
                placeholder="Company ESSL endpoint URL"
              />
            </div>
            <div className="col-sm-4 col-lg-2">
              <label className="form-label">Method</label>
              <select
                className="form-select"
                value={config.method}
                onChange={(event) => setConfig({ ...config, method: event.target.value })}
              >
                <option value="GET">GET</option>
                <option value="POST">POST</option>
              </select>
            </div>
            <div className="col-sm-8 col-lg-3">
              <label className="form-label">Authentication</label>
              <select
                className="form-select"
                value={config.auth_type}
                onChange={(event) => setConfig({ ...config, auth_type: event.target.value })}
              >
                <option value="none">None</option>
                <option value="bearer">Bearer token</option>
                <option value="api_key">API key</option>
                <option value="basic">Username / password</option>
              </select>
            </div>

            {config.auth_type === "api_key" && (
              <>
                <div className="col-md-4">
                  <label className="form-label">API-key header name</label>
                  <input
                    className="form-control"
                    value={config.api_key_header || ""}
                    onChange={(event) => setConfig({ ...config, api_key_header: event.target.value })}
                  />
                </div>
                <div className="col-md-8">
                  <label className="form-label">API key</label>
                  <input
                    className="form-control"
                    type="password"
                    autoComplete="new-password"
                    value={authSecret}
                    placeholder={config.has_credentials ? "Saved securely; leave blank to keep" : ""}
                    onChange={(event) => setAuthSecret(event.target.value)}
                  />
                </div>
              </>
            )}
            {config.auth_type === "bearer" && (
              <div className="col-12">
                <label className="form-label">Bearer token</label>
                <input
                  className="form-control"
                  type="password"
                  autoComplete="new-password"
                  value={authSecret}
                  placeholder={config.has_credentials ? "Saved securely; leave blank to keep" : ""}
                  onChange={(event) => setAuthSecret(event.target.value)}
                />
              </div>
            )}
            {config.auth_type === "basic" && (
              <>
                <div className="col-md-6">
                  <label className="form-label">Username</label>
                  <input
                    className="form-control"
                    autoComplete="off"
                    value={authUsername}
                    placeholder={config.has_credentials ? "Saved securely; leave blank to keep" : ""}
                    onChange={(event) => setAuthUsername(event.target.value)}
                  />
                </div>
                <div className="col-md-6">
                  <label className="form-label">Password</label>
                  <input
                    className="form-control"
                    type="password"
                    autoComplete="new-password"
                    value={authPassword}
                    placeholder={config.has_credentials ? "Saved securely; leave blank to keep" : ""}
                    onChange={(event) => setAuthPassword(event.target.value)}
                  />
                </div>
              </>
            )}

            <div className="col-md-6">
              <label className="form-label">Request query parameters (JSON object)</label>
              <textarea
                className="form-control"
                rows="2"
                value={requestParamsText}
                onChange={(event) => setRequestParamsText(event.target.value)}
                placeholder={config.has_request_parameters ? "Saved securely; enter JSON only to replace" : "{}"}
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">POST request body (JSON object)</label>
              <textarea
                className="form-control"
                rows="2"
                value={requestBodyText}
                onChange={(event) => setRequestBodyText(event.target.value)}
                placeholder={config.has_request_body ? "Saved securely; enter JSON only to replace" : "{}"}
              />
            </div>
            <div className="col-12">
              <label className="form-label">Additional request headers (JSON object, optional)</label>
              <textarea
                className="form-control"
                rows="2"
                value={requestHeadersText}
                onChange={(event) => setRequestHeadersText(event.target.value)}
                placeholder={config.has_request_headers ? "Saved securely; enter JSON only to replace" : "{}"}
              />
            </div>

            <div className="col-md-4">
              <label className="form-label">Punch list path</label>
              <input
                className="form-control"
                value={config.records_path || ""}
                onChange={(event) => setConfig({ ...config, records_path: event.target.value })}
                placeholder="e.g. data.records (blank if response is an array)"
              />
            </div>
            <div className="col-md-4">
              <label className="form-label">Employee ID field path</label>
              <input
                className="form-control"
                required
                value={config.employee_id_path || ""}
                onChange={(event) => setConfig({ ...config, employee_id_path: event.target.value })}
                placeholder="e.g. employeeCode"
              />
            </div>
            <div className="col-md-4">
              <label className="form-label">Punch timestamp field path</label>
              <input
                className="form-control"
                required
                value={config.timestamp_path || ""}
                onChange={(event) => setConfig({ ...config, timestamp_path: event.target.value })}
                placeholder="e.g. punchTime"
              />
            </div>
            <div className="col-md-4">
              <label className="form-label">Punch/reference ID path (optional)</label>
              <input
                className="form-control"
                value={config.punch_id_path || ""}
                onChange={(event) => setConfig({ ...config, punch_id_path: event.target.value })}
              />
            </div>
            <div className="col-md-4">
              <label className="form-label">Device ID path (optional)</label>
              <input
                className="form-control"
                value={config.device_id_path || ""}
                onChange={(event) => setConfig({ ...config, device_id_path: event.target.value })}
              />
            </div>
            <div className="col-md-4">
              <label className="form-label">Timestamp format (optional)</label>
              <input
                className="form-control"
                value={config.timestamp_format || ""}
                onChange={(event) => setConfig({ ...config, timestamp_format: event.target.value })}
                placeholder="Default: ISO-8601"
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">From-date parameter (optional)</label>
              <input
                className="form-control"
                value={config.from_date_parameter || ""}
                onChange={(event) => setConfig({ ...config, from_date_parameter: event.target.value })}
                placeholder="Sent in query for GET, JSON body for POST"
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">To-date parameter (optional)</label>
              <input
                className="form-control"
                value={config.to_date_parameter || ""}
                onChange={(event) => setConfig({ ...config, to_date_parameter: event.target.value })}
                placeholder="Sent in query for GET, JSON body for POST"
              />
            </div>
          </div>
          <div className="d-flex flex-wrap gap-2 mt-3">
            <button className="btn btn-primary" type="submit" disabled={saving}>
              {saving ? "Saving..." : "Save ESSL Configuration"}
            </button>
            <button
              className="btn btn-outline-primary"
              type="button"
              disabled={!config.configured || saving || testing}
              onClick={testConnection}
            >
              {testing ? "Testing connection..." : "Test ESSL Connection"}
            </button>
          </div>
          <small className="text-muted d-block mt-2">
            Response field paths are configured from your ESSL API's actual JSON schema. The endpoint must return the requested punches in one JSON response; pagination is not auto-followed. Naive timestamps are treated as Asia/Kolkata.
          </small>
        </form>

        <div className="border rounded p-3 mb-4">
          <h5>Employee ID Mapping</h5>
          <p className="small text-muted">
            Map the ID sent by ESSL to the HRMS employee code. Unmapped IDs are held for review and never assigned to another employee.
          </p>
          <div className="table-responsive">
            <table className="table table-sm align-middle">
              <thead>
                <tr><th>Employee</th><th>HRMS Employee Code</th><th>Department</th><th>ESSL Employee ID</th><th></th></tr>
              </thead>
              <tbody>
                {employees.map((employee) => (
                  <tr key={employee.user_id}>
                    <td>{employee.name || "—"}{!employee.is_active && <span className="badge text-bg-secondary ms-2">Inactive</span>}</td>
                    <td>{employee.employee_id || "—"}</td>
                    <td>{employee.department || "—"}</td>
                    <td style={{ maxWidth: 220 }}>
                      <input
                        className="form-control form-control-sm"
                        value={employee.essl_employee_id || ""}
                        onChange={(event) =>
                          setEmployees((current) =>
                            current.map((item) =>
                              item.user_id === employee.user_id
                                ? { ...item, essl_employee_id: event.target.value }
                                : item,
                            ),
                          )
                        }
                        aria-label={`ESSL employee ID for ${employee.name}`}
                      />
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-sm btn-outline-primary"
                        disabled={savingMappingId === employee.user_id}
                        onClick={() => saveMapping(employee)}
                      >
                        {savingMappingId === employee.user_id ? "Saving..." : "Save mapping"}
                      </button>
                    </td>
                  </tr>
                ))}
                {employees.length === 0 && <tr><td colSpan="5" className="text-center text-muted">No employee accounts found.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>

        <div className="border rounded p-3 mb-4">
          <h5>Import ESSL Attendance File</h5>
          <p className="text-muted small mb-3">
            Upload an ESSL CSV, XLS, or XLSX report. Employee IDs must be mapped in the table above. Re-importing replaces previous file-import punches for each employee/date included in the file, so corrected or removed punches do not remain as stale records. API-synchronized punches are preserved. Existing manual/WFH attendance is protected.
          </p>
          <div className="row g-2 align-items-end">
            <div className="col-md-8">
              <label className="form-label">Attendance file</label>
              <input className="form-control" type="file" accept=".csv,.xls,.xlsx" onChange={(event) => { setImportFile(event.target.files?.[0] || null); setImportSummary(null); }} />
              <div className="form-text">Maximum file size: 15 MB. Supports raw punch rows and daily reports with IN-1/OUT-1 style columns when a date column is available.</div>
            </div>
            <div className="col-md-4">
              <button type="button" className="btn btn-primary w-100" disabled={!importFile || importing} onClick={async () => {
                try {
                  setImporting(true); setError(""); setNotice(""); setImportSummary(null);
                  const formData = new FormData(); formData.append("file", importFile);
                  const response = await fetch(`${ESSL_API_URL}/import-file`, { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: formData });
                  const data = await response.json().catch(() => ({}));
                  if (!response.ok) throw new Error(data?.detail || "Attendance file import failed.");
                  setImportSummary(data);
                  setNotice("Attendance file processed. Review the import summary and unmatched employee IDs.");
                  await loadAdminData();
                } catch (importError) {
                  setError(importError.message || "Attendance file import failed.");
                } finally { setImporting(false); }
              }}>
                {importing ? <><span className="spinner-border spinner-border-sm me-2" role="status"></span>Importing...</> : "Import Attendance File"}
              </button>
            </div>
          </div>
          {importSummary && (
            <div className="alert alert-info mt-3 mb-0">
              <strong>Import completed</strong>
              <div className="row mt-2 g-2">
                <div className="col-md-4">Punch rows found: {importSummary.rows_with_punches}</div>
                <div className="col-md-4">Punches imported: {importSummary.punches_imported}</div>
                <div className="col-md-4">Duplicates in file: {importSummary.duplicate_punches_in_file}</div>
                <div className="col-md-4">Invalid time values: {importSummary.invalid_punch_values}</div>
                <div className="col-md-4">Attendance created: {importSummary.attendance_records_created}</div>
                <div className="col-md-4">Attendance updated: {importSummary.attendance_records_updated}</div>
                <div className="col-md-4">Protected existing records: {importSummary.attendance_records_protected}</div>
              </div>
              {importSummary.unmatched_employee_ids?.length > 0 && <div className="mt-2"><strong>Unmapped ESSL IDs:</strong> {importSummary.unmatched_employee_ids.join(", ")}</div>}
            </div>
          )}
        </div>

        <div className="border rounded p-3 mb-4">
          <h5>Synchronize Attendance</h5>
          <div className="row g-2 align-items-end">
            <div className="col-sm-4">
              <label className="form-label">From Date</label>
              <input className="form-control" type="date" value={fromDate} max={todayInIndia()} onChange={(event) => setFromDate(event.target.value)} />
            </div>
            <div className="col-sm-4">
              <label className="form-label">To Date</label>
              <input className="form-control" type="date" value={toDate} max={todayInIndia()} onChange={(event) => setToDate(event.target.value)} />
            </div>
            <div className="col-sm-4">
              <button
                type="button"
                className="btn btn-success w-100"
                disabled={!config.configured || syncing || testing || !fromDate || !toDate || fromDate > toDate}
                onClick={syncAttendance}
              >
                {syncing ? <><span className="spinner-border spinner-border-sm me-2" role="status"></span>Synchronizing ESSL attendance...</> : "Sync ESSL Attendance"}
              </button>
            </div>
          </div>
          {syncSummary && (
            <div className="alert alert-success mt-3 mb-0">
              <strong>ESSL Synchronization Completed</strong>
              <div className="row mt-2">
                <div className="col-md-4">Employees processed: {syncSummary.employees_processed}</div>
                <div className="col-md-4">Punches received: {syncSummary.punches_received}</div>
                <div className="col-md-4">Punches processed: {syncSummary.processed_punches}</div>
                <div className="col-md-4">Attendance created: {syncSummary.attendance_records_created}</div>
                <div className="col-md-4">Attendance updated: {syncSummary.attendance_records_updated}</div>
                <div className="col-md-4">Unmatched employees: {syncSummary.unmatched_employees}</div>
                <div className="col-md-4">Invalid records: {syncSummary.invalid_records}</div>
              </div>
            </div>
          )}
        </div>

        <div className="border rounded p-3 mb-4">
          <h5>Unmatched / Invalid Punches</h5>
          <p className="text-muted small mb-2">
            This list shows unresolved issues from the most recently imported attendance file. Earlier import records are retained for audit and are not mixed into this table.
          </p>
          <div
            className="table-responsive"
            style={{ maxHeight: "320px", overflowY: "auto", overflowX: "auto" }}
          >
            <table className="table table-sm table-hover mb-0">
              <thead className="table-light" style={{ position: "sticky", top: 0, zIndex: 1 }}><tr><th>ESSL Employee ID</th><th>Timestamp</th><th>Device</th><th>Reason</th><th>Last Seen</th></tr></thead>
              <tbody>
                {issues.map((issue) => (
                  <tr key={issue.id}>
                    <td>{issue.essl_employee_id || "—"}</td>
                    <td>{issue.punch_timestamp || "—"}</td>
                    <td>{issue.device_id || "—"}</td>
                    <td>{issue.last_reason}</td>
                    <td>{formatDateTime(issue.last_seen_at)}</td>
                  </tr>
                ))}
                {issues.length === 0 && <tr><td colSpan="5" className="text-center text-muted">No unmatched or invalid punches.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>

        <div className="border rounded p-3">
          <h5>Synchronization History</h5>
          <div className="table-responsive">
            <table className="table table-sm">
              <thead><tr><th>Started</th><th>Range</th><th>Status</th><th>Received</th><th>Created</th><th>Updated</th><th>Unmatched / Invalid</th></tr></thead>
              <tbody>
                {history.map((item) => (
                  <tr key={item.sync_id}>
                    <td>{formatDateTime(item.started_at)}</td>
                    <td>{item.from_date || "—"} – {item.to_date || "—"}</td>
                    <td>{item.status}</td>
                    <td>{item.punches_received ?? "—"}</td>
                    <td>{item.attendance_records_created ?? "—"}</td>
                    <td>{item.attendance_records_updated ?? "—"}</td>
                    <td>{item.unmatched_employees ?? "—"} / {item.invalid_records ?? "—"}</td>
                  </tr>
                ))}
                {history.length === 0 && <tr><td colSpan="7" className="text-center text-muted">No synchronization has been run.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
}

export default EsslAdminSettings;
