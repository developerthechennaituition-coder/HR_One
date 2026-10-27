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
  const [importPreview, setImportPreview] = useState(null);
  const [mappingSearch, setMappingSearch] = useState("");
  const [mappingDepartment, setMappingDepartment] = useState("all");
  const [savingMappingId, setSavingMappingId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [syncSummary, setSyncSummary] = useState(null);
  const lastSuccessfulSync = history.find(
    (item) => item.status === "COMPLETED",
  );
  const mappingDepartments = [...new Set(
    employees.map((employee) => String(employee.department || "").trim()).filter(Boolean),
  )].sort((a, b) => a.localeCompare(b));
  const filteredEmployees = employees.filter((employee) => {
    const query = mappingSearch.trim().toLocaleLowerCase();
    const searchable = [
      employee.name, employee.employee_id, employee.essl_employee_id,
      employee.department, employee.role,
    ].some((value) => String(value || "").toLocaleLowerCase().includes(query));
    return (!query || searchable) &&
      (mappingDepartment === "all" || employee.department === mappingDepartment);
  });

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
          <div className="d-flex flex-wrap justify-content-between align-items-start gap-2 mb-2">
            <div>
              <h5 className="mb-1">Employee ID Mapping</h5>
              <p className="small text-muted mb-0">
                Map each ESSL employee code to the correct HRMS employee. All users with an employee code are listed across roles and departments.
              </p>
            </div>
            <span className="badge text-bg-light border">{filteredEmployees.length} of {employees.length} employees</span>
          </div>
          <div className="row g-2 my-2">
            <div className="col-md-8">
              <label className="visually-hidden" htmlFor="essl-mapping-search">Search employee mappings</label>
              <input id="essl-mapping-search" type="search" className="form-control form-control-sm"
                placeholder="Search Employee Name, Employee ID, ESSL Employee Code, Department or Role"
                value={mappingSearch} onChange={(event) => setMappingSearch(event.target.value)} />
            </div>
            <div className="col-md-4">
              <label className="visually-hidden" htmlFor="essl-mapping-department">Filter by HRMS department</label>
              <select id="essl-mapping-department" className="form-select form-select-sm" value={mappingDepartment}
                onChange={(event) => setMappingDepartment(event.target.value)}>
                <option value="all">All Departments</option>
                {mappingDepartments.map((department) => <option key={department} value={department}>{department}</option>)}
              </select>
            </div>
          </div>
          <div className="table-responsive essl-employee-mapping-scroll" style={{ height: `${Math.min(460, Math.max(180, employees.length * 40 + 44))}px`, maxHeight: "460px", overflowY: "auto", overflowX: "auto", overscrollBehavior: "contain" }}>
            <table className="table table-sm table-hover align-middle mb-0">
              <thead className="table-light" style={{ position: "sticky", top: 0, zIndex: 2 }}>
                <tr><th>Employee Name</th><th>Employee ID</th><th>Role</th><th>Department</th><th>ESSL Employee Code</th><th>Mapping Status</th><th>Action</th></tr>
              </thead>
              <tbody>
                {filteredEmployees.map((employee) => (
                  <tr key={employee.user_id}>
                    <td>{employee.name || "—"}{!employee.is_active && <span className="badge text-bg-secondary ms-2">Inactive</span>}</td>
                    <td>{employee.employee_id || "—"}</td>
                    <td>{employee.role || "—"}</td>
                    <td>{employee.department || "Unassigned"}</td>
                    <td style={{ minWidth: "180px" }}>
                      <input className="form-control form-control-sm" value={employee.essl_employee_id || ""}
                        onChange={(event) => setEmployees((current) => current.map((item) =>
                          item.user_id === employee.user_id ? { ...item, essl_employee_id: event.target.value } : item,
                        ))} aria-label={`ESSL employee code for ${employee.name}`} />
                    </td>
                    <td><span className={`badge ${employee.essl_employee_id ? "text-bg-success" : "text-bg-warning"}`}>
                      {employee.essl_employee_id ? "Mapped" : "Unmapped"}
                    </span></td>
                    <td><button type="button" className="btn btn-sm btn-outline-primary"
                      disabled={savingMappingId === employee.user_id}
                      onClick={() => saveMapping(employee)}>
                      {savingMappingId === employee.user_id ? "Saving..." : "Save"}
                    </button></td>
                  </tr>
                ))}
                {filteredEmployees.length === 0 && <tr><td colSpan="7" className="text-center text-muted py-4">No employees match the selected search or department.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>

        <div className="border rounded p-3 mb-4">
          <h5>Import ESSL Attendance File</h5>
          <p className="text-muted small mb-3">
            Upload the original eTimeTrackLite daily report. The importer detects each repeated date section and its IN/OUT columns. Review the preview and resolve unmapped employees before importing. Re-importing reconciles file-import punches for employee/date pairs in the report; API punches and protected manual/WFH attendance are preserved.
          </p>
          <div className="row g-2 align-items-end">
            <div className="col-md-8">
              <label className="form-label" htmlFor="essl-attendance-file">Attendance file</label>
              <input id="essl-attendance-file" className="form-control" type="file" accept=".csv,.xls,.xlsx"
                onChange={(event) => { setImportFile(event.target.files?.[0] || null); setImportSummary(null); setImportPreview(null); }}
              />
              <div className="form-text">Maximum 15 MB. Supports ESSL reports with repeated daily headings and up to five IN/OUT pairs.</div>
            </div>
            <div className="col-md-4">
              <button type="button" className="btn btn-outline-primary w-100" disabled={!importFile || importing}
                onClick={async () => {
                  try {
                    setImporting(true); setError(""); setNotice(""); setImportSummary(null); setImportPreview(null);
                    const formData = new FormData(); formData.append("file", importFile);
                    const response = await fetch(`${ESSL_API_URL}/preview-file`, {
                      method: "POST", headers: { Authorization: `Bearer ${token}` }, body: formData,
                    });
                    const data = await response.json().catch(() => ({}));
                    if (!response.ok) throw new Error(data?.detail || "Attendance file validation failed.");
                    setImportPreview(data);
                    setNotice("File validated. Review the employee mappings and punch preview before confirming import.");
                  } catch (previewError) {
                    setError(previewError.message || "Attendance file validation failed.");
                  } finally { setImporting(false); }
                }}>
                {importing ? <><span className="spinner-border spinner-border-sm me-2" role="status" />Validating...</> : "Validate & Preview"}
              </button>
            </div>
          </div>
          {importPreview && (
            <div className="mt-3">
              <div className="alert alert-info mb-3">
                <strong>Validated: {importPreview.filename}</strong>
                <div>Reporting period: {importPreview.reporting_period?.from_date} to {importPreview.reporting_period?.to_date}</div>
                <div className="row g-2 mt-1">
                  <div className="col-sm-4">Employees: {importPreview.employee_count}</div>
                  <div className="col-sm-4">Employee/date records: {importPreview.daily_employee_records}</div>
                  <div className="col-sm-4">Valid punches: {importPreview.valid_punches}</div>
                  <div className="col-sm-4">Potential duplicate punches: {importPreview.potential_duplicate_punches || 0}</div>
                  <div className="col-sm-4">Unmapped ESSL employees: {importPreview.unmapped_employee_count}</div>
                  <div className="col-sm-4">Department differences: {importPreview.department_mismatch_count || 0}</div>
                  <div className="col-sm-4">Invalid time values: {importPreview.invalid_punch_values}</div>
                </div>
                {importPreview.unmapped_employee_count > 0 && <div className="mt-2 text-danger">
Only employees with saved ESSL mappings will be imported. You can import mapped employees now and map the remaining employees later; unmapped employees will be skipped and listed in the result.
                </div>}
                {(importPreview.department_mismatch_count || 0) > 0 && <div className="mt-2 text-warning-emphasis">
                  Some mapped employees have different ESSL and HRMS departments. Review the differences below; the import will not change HRMS departments.
                </div>}
              </div>
              <h6>Employee mapping review</h6>
              <div className="table-responsive mb-3" style={{ maxHeight: "240px", overflowY: "auto" }}>
                <table className="table table-sm table-hover align-middle">
                  <thead className="table-light" style={{ position: "sticky", top: 0, zIndex: 1 }}>
                    <tr><th>ESSL Code</th><th>ESSL Name</th><th>ESSL Department</th><th>HRMS Employee</th><th>HRMS Department</th><th>Status</th></tr>
                  </thead>
                  <tbody>{(importPreview.employees || []).map((item) => (
                    <tr key={item.essl_employee_id}>
                      <td>{item.essl_employee_id}</td><td>{item.name || "—"}</td><td>{item.department || "—"}</td>
                      <td>{item.hrms_employee_name ? `${item.hrms_employee_name} (${item.hrms_employee_id})` : "—"}</td>
                      <td>{item.hrms_department || "—"}</td>
                      <td><span className={`badge ${item.mapping_status === "mapped" ? "text-bg-success" : item.mapping_status === "department_mismatch" ? "text-bg-warning" : "text-bg-danger"}`}>{item.mapping_status === "department_mismatch" ? "Department differs" : item.mapping_status}</span></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              <h6>Punch preview (first {Math.min(importPreview.preview?.length || 0, importPreview.preview_limit || 100)} rows)</h6>
              <div className="table-responsive mb-3" style={{ maxHeight: "240px", overflowY: "auto" }}>
                <table className="table table-sm align-middle">
                  <thead className="table-light" style={{ position: "sticky", top: 0, zIndex: 1 }}>
                    <tr><th>ESSL Code</th><th>Name</th><th>Date</th><th>Time</th><th>Direction</th></tr>
                  </thead>
                  <tbody>{(importPreview.preview || []).map((item, index) => (
                    <tr key={`${item.employee_code}-${item.attendance_date}-${item.punch_time}-${index}`}>
                      <td>{item.employee_code}</td><td>{item.employee_name || "—"}</td><td>{item.attendance_date}</td><td>{item.punch_time}</td><td>{item.direction || "—"}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              <button type="button" className="btn btn-primary" disabled={importing || (importPreview.mapped_employee_count ?? ((importPreview.employees || []).filter((item) => item.mapping_status !== "unmapped").length)) === 0}
                onClick={async () => {
                  try {
                    setImporting(true); setError(""); setNotice(""); setImportSummary(null);
                    const formData = new FormData(); formData.append("file", importFile);
                    const response = await fetch(`${ESSL_API_URL}/import-file`, { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: formData });
                    const data = await response.json().catch(() => ({}));
                    if (!response.ok) throw new Error(data?.detail || "Attendance file import failed.");
                    setImportSummary(data);
                    setNotice("Attendance file imported. Review the result summary below.");
                    await loadAdminData();
                  } catch (importError) {
                    setError(importError.message || "Attendance file import failed.");
                  } finally { setImporting(false); }
                }}>
                {importing ? "Importing..." : "Confirm and Import Attendance"}
              </button>
              {importSummary && (
                <div className="alert alert-success mt-3 mb-0">
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
          )}
          {importSummary && !importPreview && (
            <div className="alert alert-info mt-3 mb-0">Import completed. {importSummary.punches_imported} punches imported.</div>
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
