import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { HRONE_API_BASE } from "../config/api.js";
import { roleLabel, ROLE_OPTIONS } from "../services/roleLabels";
import Pagination from "../components/Pagination";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || HRONE_API_BASE).replace(/\/+$/, "");
const WFH_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
const todayIso = () => {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
};

function WfhAdminSettings({ session }) {
  const token = session?.token;
  const [employees, setEmployees] = useState([]);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [savingId, setSavingId] = useState("");
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [faceDetails, setFaceDetails] = useState(null);
  const [faceDetailsLoading, setFaceDetailsLoading] = useState(false);
  const [faceDetailsError, setFaceDetailsError] = useState("");

  // Filters (same fields and behaviour as the Daily Attendance page).
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [departmentFilter, setDepartmentFilter] = useState("all");
  const [roleFilter, setRoleFilter] = useState("all");
  const [selectedEmployeeFilter, setSelectedEmployeeFilter] = useState("all");
  const [wfhFilter, setWfhFilter] = useState("all");
  const [departmentOptions, setDepartmentOptions] = useState([]);
  const [departmentEmployeeIds, setDepartmentEmployeeIds] = useState(null);
  const [departmentLoading, setDepartmentLoading] = useState(false);
  const [departmentError, setDepartmentError] = useState("");
  const departmentRequest = useRef(0);

  // Manage Permission popup: edits a draft copy so Cancel discards changes.
  const [permissionUserId, setPermissionUserId] = useState("");
  const [permissionDraft, setPermissionDraft] = useState(null);
  const [permissionError, setPermissionError] = useState("");

  const headers = useCallback(() => {
    if (!token) throw new Error("Login session not found. Please login again.");
    return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  }, [token]);

  const loadEmployees = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/attendance/wfh/admin/employees`, { headers: headers() });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || "Unable to load WFH settings.");
      setEmployees(Array.isArray(data) ? data : []);
      setError("");
    } catch (e) {
      setError(e.message || "Unable to load WFH settings.");
    } finally {
      setLoading(false);
    }
  }, [headers]);

  useEffect(() => { loadEmployees(); }, [loadEmployees]);

  useEffect(() => {
    (async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/api/attendance/departments`, { headers: headers() });
        const data = await response.json().catch(() => ([]));
        if (response.ok) setDepartmentOptions(Array.isArray(data) ? data : []);
      } catch { /* department filter simply stays empty */ }
    })();
  }, [headers]);

  const departments = useMemo(() => {
    const byId = new Map();
    departmentOptions.forEach((item) => {
      const id = String(item.id || item._id || "").trim();
      const name = String(item.department_name || item.name || "").trim();
      if (id && name && !byId.has(id)) byId.set(id, name);
    });
    return [...byId].map(([id, name]) => ({ id, name })).sort((x, y) => x.name.localeCompare(y.name));
  }, [departmentOptions]);

  // Employees of the selected department, resolved by the server.
  useEffect(() => {
    const requestId = ++departmentRequest.current;
    setSelectedEmployeeFilter("all");
    setDepartmentError("");
    if (departmentFilter === "all") {
      setDepartmentEmployeeIds(null);
      setDepartmentLoading(false);
      return undefined;
    }
    const controller = new AbortController();
    setDepartmentEmployeeIds(null);
    setDepartmentLoading(true);
    (async () => {
      try {
        const response = await fetch(
          `${API_BASE_URL}/api/attendance/department-employees?department_id=${encodeURIComponent(departmentFilter)}`,
          { headers: headers(), signal: controller.signal },
        );
        const data = await response.json().catch(() => ([]));
        if (!response.ok) throw new Error(data?.detail || "Unable to load department employees.");
        if (requestId !== departmentRequest.current) return;
        setDepartmentEmployeeIds(new Set((Array.isArray(data) ? data : []).map((item) => String(item.id))));
      } catch (requestError) {
        if (requestError.name === "AbortError" || requestId !== departmentRequest.current) return;
        setDepartmentEmployeeIds(new Set());
        setDepartmentError(requestError.message || "Unable to load department employees.");
      } finally {
        if (requestId === departmentRequest.current) setDepartmentLoading(false);
      }
    })();
    return () => controller.abort();
  }, [departmentFilter, headers]);

  const filteredEmployees = useMemo(() => {
    const query = searchTerm.trim().toLowerCase();
    return employees.filter((employee) => {
      const matchesSearch = !query
        || employee.name?.toLowerCase().includes(query)
        || employee.work_email?.toLowerCase().includes(query)
        || employee.employee_id?.toLowerCase().includes(query);
      const matchesStatus = statusFilter === "all" || (statusFilter === "active" ? employee.is_active : !employee.is_active);
      const matchesDepartment = departmentFilter === "all"
        || Boolean(departmentEmployeeIds && departmentEmployeeIds.has(String(employee.user_id)));
      const matchesRole = roleFilter === "all" || roleLabel(employee.role) === roleFilter;
      const matchesEmployee = selectedEmployeeFilter === "all" || String(employee.user_id) === String(selectedEmployeeFilter);
      const matchesWfh = wfhFilter === "all" || (wfhFilter === "enabled" ? Boolean(employee.is_wfh_enabled) : !employee.is_wfh_enabled);
      return matchesSearch && matchesStatus && matchesDepartment && matchesRole && matchesEmployee && matchesWfh;
    });
  }, [employees, searchTerm, statusFilter, departmentFilter, departmentEmployeeIds, roleFilter, selectedEmployeeFilter, wfhFilter]);

  // Restore every filter (including WFH Enabled) to its default and show the full list again.
  const filtersActive = searchTerm !== "" || statusFilter !== "all" || departmentFilter !== "all"
    || roleFilter !== "all" || selectedEmployeeFilter !== "all" || wfhFilter !== "all";
  const resetFilters = () => {
    setSearchTerm("");
    setStatusFilter("all");
    setDepartmentFilter("all");
    setRoleFilter("all");
    setSelectedEmployeeFilter("all");
    setWfhFilter("all");
    setDepartmentEmployeeIds(null);
    setDepartmentError("");
    setPage(1);
  };

  const pageSize = 10;
  const pageEmployees = filteredEmployees.slice((page - 1) * pageSize, page * pageSize);
  useEffect(() => { setPage(1); }, [filteredEmployees.length, searchTerm, statusFilter, departmentFilter, roleFilter, selectedEmployeeFilter, wfhFilter]);

  const permissionEmployee = employees.find((row) => row.user_id === permissionUserId) || null;
  const openPermission = (employee) => {
    setPermissionUserId(employee.user_id);
    setPermissionDraft({
      wfh_permitted_from: employee.wfh_permitted_from || "",
      wfh_permitted_to: employee.wfh_permitted_to || "",
      wfh_permitted_start_time: employee.wfh_permitted_start_time || "",
      wfh_permitted_end_time: employee.wfh_permitted_end_time || "",
      wfh_permitted_weekdays: [...(employee.wfh_permitted_weekdays || WFH_WEEKDAYS)],
    });
    setPermissionError("");
  };
  const closePermission = () => { setPermissionUserId(""); setPermissionDraft(null); setPermissionError(""); };
  const updateDraft = (field, value) => { setPermissionDraft((draft) => ({ ...draft, [field]: value })); setPermissionError(""); };

  const updateEmployee = (userId, field, value) => {
    setEmployees((rows) => rows.map((row) => row.user_id === userId ? { ...row, [field]: value } : row));
    setError("");
    setSuccess("");
  };

  const saveEmployee = async (employee, { inPopup = false } = {}) => {
    setSavingId(employee.user_id); setError(""); setSuccess(""); setPermissionError("");
    let saved = false;
    try {
      if (employee.is_wfh_enabled && (!employee.wfh_permitted_from || !employee.wfh_permitted_to)) {
        throw new Error("Choose both WFH permitted from and until dates before enabling WFH.");
      }
      if (employee.is_wfh_enabled && (!employee.wfh_permitted_start_time || !employee.wfh_permitted_end_time)) {
        throw new Error("WFH start time and end time are mandatory.");
      }
      if (employee.wfh_permitted_from && employee.wfh_permitted_from < todayIso()) throw new Error("WFH start date must be today or a future date.");
      if (employee.wfh_permitted_to && employee.wfh_permitted_to < todayIso()) throw new Error("WFH end date must be today or a future date.");
      if (employee.wfh_permitted_from && employee.wfh_permitted_to && employee.wfh_permitted_to < employee.wfh_permitted_from) throw new Error("WFH end date must be on or after the start date.");
      if (employee.wfh_permitted_start_time && employee.wfh_permitted_end_time && employee.wfh_permitted_end_time <= employee.wfh_permitted_start_time) throw new Error("WFH end time must be later than start time.");
      if (employee.is_wfh_enabled && !(employee.wfh_permitted_weekdays || WFH_WEEKDAYS).length) throw new Error("Select at least one permitted WFH weekday.");

      const payload = {
        is_wfh_enabled: Boolean(employee.is_wfh_enabled),
        home_latitude: null,
        home_longitude: null,
        allowed_radius: Number(employee.allowed_radius || 100),
        home_location_accuracy_meters: null,
        home_location_timestamp: null,
        home_location_verification_method: "manual",
        home_location_manually_verified: false,
        wfh_permitted_from: employee.wfh_permitted_from || null,
        wfh_permitted_to: employee.wfh_permitted_to || null,
        wfh_permitted_start_time: employee.wfh_permitted_start_time || null,
        wfh_permitted_end_time: employee.wfh_permitted_end_time || null,
        wfh_permitted_weekdays: employee.wfh_permitted_weekdays || WFH_WEEKDAYS,
      };
      const response = await fetch(`${API_BASE_URL}/api/attendance/wfh/admin/employees/${employee.user_id}`, {
        method: "PUT", headers: headers(), body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || "Unable to save WFH settings.");
      setEmployees((rows) => rows.map((row) => row.user_id === employee.user_id ? { ...row, ...data } : row));
      setSuccess(`WFH settings saved for ${employee.name || employee.employee_id || "account"}.`);
      saved = true;
    } catch (e) {
      if (inPopup) setPermissionError(e.message || "Unable to save WFH settings.");
      else setError(e.message || "Unable to save WFH settings.");
    } finally { setSavingId(""); }
    return saved;
  };

  const savePermission = async () => {
    if (!permissionEmployee || !permissionDraft) return;
    const saved = await saveEmployee({ ...permissionEmployee, ...permissionDraft }, { inPopup: true });
    if (saved) closePermission();
  };

  const resetLocation = async (employee) => {
    if (!window.confirm(`Reset the registered WFH location for ${employee.name}? They must register a new location from their WFH Attendance page.`)) return;
    setBusyAction(`${employee.user_id}:location`); setError(""); setSuccess("");
    try {
      const response = await fetch(`${API_BASE_URL}/api/attendance/wfh/admin/employees/${employee.user_id}/location`, { method: "DELETE", headers: headers() });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || "Unable to reset location.");
      setEmployees((rows) => rows.map((row) => row.user_id === employee.user_id ? {
        ...row, home_latitude: null, home_longitude: null, home_location_accuracy_meters: null,
        home_location_timestamp: null, home_location_updated_at: null,
        location_registration_required: true,
      } : row));
      setSuccess(`Location reset for ${employee.name}. They can register a new location on their WFH Attendance page.`);
    } catch (e) { setError(e.message || "Unable to reset location."); }
    finally { setBusyAction(""); }
  };

  const resetFace = async (employee) => {
    if (!window.confirm(`Reset the registered face template for ${employee.name}? They will need to register their face again.`)) return;
    setBusyAction(`${employee.user_id}:face`); setError(""); setSuccess("");
    try {
      const response = await fetch(`${API_BASE_URL}/api/attendance/wfh/admin/employees/${employee.user_id}/face`, { method: "DELETE", headers: headers() });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || "Unable to reset face registration.");
      setEmployees((rows) => rows.map((row) => row.user_id === employee.user_id ? { ...row, face_registered: false } : row));
      setSuccess(`Face registration reset for ${employee.name}.`);
    } catch (e) { setError(e.message || "Unable to reset face registration."); }
    finally { setBusyAction(""); }
  };

  const showLocation = (employee) => {
    if (employee.home_latitude == null || employee.home_longitude == null) {
      window.alert("Location is not registered yet. The employee must register it from their WFH Attendance page.");
      return;
    }
    const accuracy = employee.home_location_accuracy_meters != null ? `\nReported GPS accuracy: ±${Math.round(employee.home_location_accuracy_meters)} m` : "";
    const registered = employee.home_location_updated_at ? `\nRegistered/updated: ${new Date(employee.home_location_updated_at).toLocaleString()}` : "";
    window.alert(`Registered WFH location for ${employee.name}\nLatitude: ${employee.home_latitude}\nLongitude: ${employee.home_longitude}${accuracy}${registered}`);
  };

  const showFace = async (employee) => {
    setFaceDetails({
      employee: {
        name: employee.name,
        employee_id: employee.employee_id,
        role: employee.role,
      },
      face_registered: Boolean(employee.face_registered),
      registered_at: null,
      photograph_available: false,
      photograph_data_url: null,
    });
    setFaceDetailsError("");
    setFaceDetailsLoading(true);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/attendance/wfh/admin/employees/${employee.user_id}/face`,
        { headers: headers() }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || "Unable to load face registration details.");
      setFaceDetails(data);
    } catch (e) {
      setFaceDetailsError(e.message || "Unable to load face registration details.");
    } finally {
      setFaceDetailsLoading(false);
    }
  };

  return (
    <section className="card border-0 shadow-sm mb-4">
      <div className="card-body">
        <div className="d-flex flex-wrap justify-content-between align-items-start gap-2 mb-3">
          <div>
            <h3 className="h5 mb-1">WFH Settings & Permission Duration</h3>
            <p className="text-muted small mb-0">Manage WFH access, permitted dates, and mandatory daily start/end times. The same daily schedule applies throughout the selected date range.</p>
          </div>
          <button className="btn btn-outline-secondary btn-sm" type="button" onClick={() => { setLoading(true); loadEmployees(); }} disabled={loading}>Refresh</button>
        </div>
        {error && <div className="alert alert-danger py-2" role="alert">{error}</div>}
        {success && <div className="alert alert-success py-2" role="status">{success}</div>}
        <div className="row g-3 mb-3 align-items-end" aria-label="WFH employee filters">
          <div className="col-6 col-md-4 col-xl-2">
            <label className="form-label">Department</label>
            <select className="form-select" value={departmentFilter} onChange={(e) => { setDepartmentFilter(e.target.value); setSelectedEmployeeFilter("all"); }}>
              <option value="all">All departments</option>
              {departments.map((department) => <option key={department.id} value={department.id}>{department.name}</option>)}
            </select>
            {departmentLoading && <div className="small text-muted mt-1">Loading employees…</div>}
            {departmentError && <div className="small text-danger mt-1">{departmentError}</div>}
          </div>
          <div className="col-6 col-md-4 col-xl-2">
            <label className="form-label">Role</label>
            <select className="form-select" value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}>
              <option value="all">All Roles</option>
              {ROLE_OPTIONS.map((role) => <option key={role} value={role}>{role}</option>)}
            </select>
          </div>
          <div className="col-12 col-md-6 col-xl-3">
            <label className="form-label">Search Employee</label>
            <input type="text" className="form-control" placeholder="Name, email or employee ID" value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)} />
          </div>
          <div className="col-6 col-md-4 col-xl-2">
            <label className="form-label">Employee</label>
            <select className="form-select" value={selectedEmployeeFilter} onChange={(e) => setSelectedEmployeeFilter(e.target.value)}>
              <option value="all">All employees</option>
              {employees.filter((employee) => departmentFilter === "all" || Boolean(departmentEmployeeIds && departmentEmployeeIds.has(String(employee.user_id))))
                .map((employee) => <option key={employee.user_id} value={employee.user_id}>{employee.name} ({employee.employee_id || "No ID"})</option>)}
            </select>
          </div>
          <div className="col-6 col-md-4 col-xl-2">
            <label className="form-label">User Status</label>
            <select className="form-select" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="all">All</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </div>
          <div className="col-6 col-md-4 col-xl-2">
            <label className="form-label">WFH Enabled</label>
            <select className="form-select" value={wfhFilter} onChange={(e) => setWfhFilter(e.target.value)}>
              <option value="all">All</option>
              <option value="enabled">Enable</option>
              <option value="disabled">Disable</option>
            </select>
          </div>
          <div className="col-6 col-md-4 col-xl-2">
            <button type="button" className="btn btn-outline-secondary w-100" onClick={resetFilters} disabled={!filtersActive} title="Clear all filters and show every employee">Reset</button>
          </div>
        </div>
        {loading ? <div className="text-muted py-3">Loading WFH settings...</div> : employees.length === 0 ? <div className="text-muted py-3">No eligible accounts found.</div> : filteredEmployees.length === 0 ? <div className="text-muted py-3">No employees match the selected filters.</div> : (
          <div className="table-responsive">
            <table className="table table-sm align-middle mb-0">
              <thead><tr><th>Employee / Role</th><th>WFH Enabled</th><th>Manage Permission</th><th>Face</th><th>Location</th><th>Actions</th></tr></thead>
              <tbody>{pageEmployees.map((employee) => {
                const locationRegistered = employee.home_latitude != null && employee.home_longitude != null;
                const busy = busyAction.startsWith(`${employee.user_id}:`);
                const hasPermission = Boolean(employee.wfh_permitted_from && employee.wfh_permitted_to);
                return <tr key={employee.user_id}>
                  <td><div className="fw-semibold">{employee.name || "Unnamed account"}</div><small className="text-muted d-block">{employee.employee_id || "No employee ID"} · {roleLabel(employee.role) || "Employee"}{!employee.is_active && " · Inactive"}</small></td>
                  <td><div className="form-check form-switch"><input className="form-check-input" type="checkbox" role="switch" id={`wfh-enabled-${employee.user_id}`} checked={Boolean(employee.is_wfh_enabled)} onChange={(event) => updateEmployee(employee.user_id, "is_wfh_enabled", event.target.checked)} aria-label={`Enable WFH for ${employee.name}`} /></div></td>
                  <td>
                    <button type="button" className="btn btn-outline-primary btn-sm" disabled={!employee.is_wfh_enabled} onClick={() => openPermission(employee)}
                      title={employee.is_wfh_enabled ? "Manage permit dates, times and permitted days" : "Enable WFH to manage permission"}>
                      <i className="bi bi-calendar2-check me-1" aria-hidden="true" />Manage Permission
                    </button>
                    {employee.is_wfh_enabled && hasPermission && <small className="d-block text-muted mt-1">{employee.wfh_permitted_from} → {employee.wfh_permitted_to}</small>}
                  </td>
                  <td><button type="button" className={`btn btn-sm ${employee.face_registered ? "btn-outline-success" : "btn-outline-secondary"}`} onClick={() => showFace(employee)}>{employee.face_registered ? "Face Registered" : "Not Registered"}</button></td>
                  <td><button type="button" className={`btn btn-sm ${locationRegistered ? "btn-outline-success" : "btn-outline-secondary"}`} onClick={() => showLocation(employee)}>{locationRegistered ? "Location Registered" : "Not Registered"}</button>{locationRegistered && employee.home_location_accuracy_meters != null && <small className="d-block text-muted mt-1">±{Math.round(employee.home_location_accuracy_meters)} m</small>}</td>
                  <td><div className="d-flex flex-wrap gap-1"><button type="button" className="btn btn-primary btn-sm" onClick={() => saveEmployee(employee)} disabled={savingId === employee.user_id}>{savingId === employee.user_id ? "Saving..." : "Save"}</button><button type="button" className="btn btn-outline-danger btn-sm" onClick={() => resetFace(employee)} disabled={!employee.face_registered || busy}>{busyAction === `${employee.user_id}:face` ? "Resetting..." : "Reset Face"}</button><button type="button" className="btn btn-outline-warning btn-sm" onClick={() => resetLocation(employee)} disabled={!locationRegistered || busy}>{busyAction === `${employee.user_id}:location` ? "Resetting..." : "Reset Location"}</button></div></td>
                </tr>;
              })}</tbody>
            </table>
<Pagination page={page} totalItems={filteredEmployees.length} pageSize={pageSize} onPageChange={setPage} />
          </div>
        )}
      </div>
      {permissionEmployee && permissionDraft && (
        <div
          className="modal d-block"
          role="dialog"
          aria-modal="true"
          aria-labelledby="wfh-permission-title"
          style={{ background: "rgba(0, 0, 0, 0.55)", zIndex: 1060 }}
          onClick={(event) => { if (event.target === event.currentTarget) closePermission(); }}
        >
          <div className="modal-dialog modal-dialog-centered modal-dialog-scrollable modal-lg">
            <div className="modal-content">
              <div className="modal-header">
                <div>
                  <h2 className="modal-title h5 mb-1" id="wfh-permission-title">Manage WFH Permission</h2>
                  <div className="small text-muted">
                    {permissionEmployee.name || "Employee"}{permissionEmployee.employee_id ? ` · ${permissionEmployee.employee_id}` : ""} · {roleLabel(permissionEmployee.role) || "Employee"}
                  </div>
                </div>
                <button type="button" className="btn-close" aria-label="Close" onClick={closePermission} />
              </div>
              <div className="modal-body">
                {permissionError && <div className="alert alert-danger py-2" role="alert">{permissionError}</div>}
                <div className="row g-3">
                  <div className="col-12 col-sm-6">
                    <label className="form-label" htmlFor="wfh-permit-from">Permit From</label>
                    <input id="wfh-permit-from" className="form-control" type="date" min={todayIso()} value={permissionDraft.wfh_permitted_from} onChange={(event) => updateDraft("wfh_permitted_from", event.target.value)} />
                  </div>
                  <div className="col-12 col-sm-6">
                    <label className="form-label" htmlFor="wfh-permit-until">Permit Until</label>
                    <input id="wfh-permit-until" className="form-control" type="date" min={permissionDraft.wfh_permitted_from || todayIso()} value={permissionDraft.wfh_permitted_to} onChange={(event) => updateDraft("wfh_permitted_to", event.target.value)} />
                  </div>
                  <div className="col-12 col-sm-6">
                    <label className="form-label" htmlFor="wfh-start-time">Required Start Time</label>
                    <input id="wfh-start-time" className="form-control" type="time" required value={permissionDraft.wfh_permitted_start_time} onChange={(event) => updateDraft("wfh_permitted_start_time", event.target.value)} />
                  </div>
                  <div className="col-12 col-sm-6">
                    <label className="form-label" htmlFor="wfh-end-time">Required End Time</label>
                    <input id="wfh-end-time" className="form-control" type="time" required value={permissionDraft.wfh_permitted_end_time} onChange={(event) => updateDraft("wfh_permitted_end_time", event.target.value)} />
                  </div>
                  <div className="col-12">
                    <div className="form-label">Permitted Days</div>
                    <div className="d-flex flex-wrap gap-3">
                      {WFH_WEEKDAYS.map((day) => (
                        <label key={day} className="form-check mb-0">
                          <input className="form-check-input" type="checkbox" checked={permissionDraft.wfh_permitted_weekdays.includes(day)}
                            onChange={(event) => updateDraft("wfh_permitted_weekdays", event.target.checked ? [...permissionDraft.wfh_permitted_weekdays, day] : permissionDraft.wfh_permitted_weekdays.filter((item) => item !== day))} />
                          <span className="form-check-label text-capitalize">{day}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                </div>
                <p className="small text-muted mt-3 mb-0">The same daily start/end time applies throughout the permitted date range. Employees who miss the check-in window are marked Absent automatically.</p>
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-secondary" onClick={closePermission} disabled={savingId === permissionEmployee.user_id}>Cancel</button>
                <button type="button" className="btn btn-primary" onClick={savePermission} disabled={savingId === permissionEmployee.user_id}>{savingId === permissionEmployee.user_id ? "Saving..." : "Save Permission"}</button>
              </div>
            </div>
          </div>
        </div>
      )}
      {faceDetails && (
        <div
          className="modal d-block"
          role="dialog"
          aria-modal="true"
          aria-labelledby="wfh-face-details-title"
          style={{ background: "rgba(0, 0, 0, 0.55)", zIndex: 1060 }}
          onClick={(event) => {
            if (event.target === event.currentTarget) setFaceDetails(null);
          }}
        >
          <div className="modal-dialog modal-dialog-centered modal-lg">
            <div className="modal-content">
              <div className="modal-header">
                <div>
                  <h2 className="modal-title h5 mb-1" id="wfh-face-details-title">
                    Face Registration Details
                  </h2>
                  <div className="small text-muted">
                    {faceDetails.employee?.name || "Employee"}
                    {faceDetails.employee?.employee_id ? ` · ${faceDetails.employee.employee_id}` : ""}
                  </div>
                </div>
                <button type="button" className="btn-close" aria-label="Close" onClick={() => setFaceDetails(null)} />
              </div>
              <div className="modal-body">
                {faceDetailsLoading ? (
                  <div className="text-muted py-4 text-center">Loading face registration details...</div>
                ) : faceDetailsError ? (
                  <div className="alert alert-danger mb-0" role="alert">{faceDetailsError}</div>
                ) : (
                  <div className="row g-4 align-items-start">
                    <div className="col-md-5 text-center">
                      {faceDetails.photograph_data_url ? (
                        <img
                          src={faceDetails.photograph_data_url}
                          alt={`Registered face for ${faceDetails.employee?.name || "employee"}`}
                          className="img-fluid rounded border"
                          style={{ maxHeight: "320px", objectFit: "contain" }}
                        />
                      ) : (
                        <div className="border rounded bg-light text-muted p-4">
                          <div className="fw-semibold mb-2">
                            {faceDetails.face_registered ? "Photograph not available" : "No face registered"}
                          </div>
                          <div className="small">
                            {faceDetails.face_registered
                              ? "This face was registered before photograph storage was enabled. Reset and register the face again to create a photograph."
                              : "The employee has not registered a face yet."}
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="col-md-7">
                      <dl className="row mb-0">
                        <dt className="col-sm-5">Registration status</dt>
                        <dd className="col-sm-7">
                          <span className={`badge ${faceDetails.face_registered ? "text-bg-success" : "text-bg-secondary"}`}>
                            {faceDetails.face_registered ? "Face Registered" : "Not Registered"}
                          </span>
                        </dd>
                        <dt className="col-sm-5">Employee</dt>
                        <dd className="col-sm-7">{faceDetails.employee?.name || "—"}</dd>
                        <dt className="col-sm-5">Employee ID</dt>
                        <dd className="col-sm-7">{faceDetails.employee?.employee_id || "—"}</dd>
                        <dt className="col-sm-5">Role</dt>
                        <dd className="col-sm-7">{roleLabel(faceDetails.employee?.role) || "—"}</dd>
                        <dt className="col-sm-5">Registered at</dt>
                        <dd className="col-sm-7">
                          {faceDetails.registered_at
                            ? new Date(faceDetails.registered_at).toLocaleString()
                            : "Not available"}
                        </dd>
                        <dt className="col-sm-5">Photograph</dt>
                        <dd className="col-sm-7">
                          {faceDetails.photograph_available ? "Available (encrypted at rest)" : "Not available"}
                        </dd>
                      </dl>
                    </div>
                  </div>
                )}
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-secondary" onClick={() => setFaceDetails(null)}>Close</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

export default WfhAdminSettings;
