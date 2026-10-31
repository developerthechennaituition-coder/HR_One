import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { HRONE_API_BASE } from "../config/api.js";
import { buildChangeRows, formatIST } from "../services/attendanceHistoryFormat.js";

const API = `${HRONE_API_BASE}/api/attendance`;
const STATUSES = ["Present", "Absent", "Holiday", "Leave", "Weekly Off", "Medical Leave", "Permission", "Not Applicable", "Not Marked"];
const LOCKED_PAYROLL = new Set(["Approved", "Processed", "Paid"]);

function companyDateParts() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  return Object.fromEntries(parts.map(({ type, value }) => [type, value]));
}

function monthKey(year, month) {
  return `${year}-${String(month).padStart(2, "0")}`;
}

function monthsFromJoining(joiningDate) {
  const today = companyDateParts();
  const current = new Date(Number(today.year), Number(today.month) - 1, 1);
  const joinText = String(joiningDate || `${today.year}-${today.month}-01`).slice(0, 10);
  const joinDate = /^\d{4}-\d{2}-\d{2}$/.test(joinText) ? new Date(`${joinText}T00:00:00`) : current;
  const start = new Date(joinDate.getFullYear(), joinDate.getMonth(), 1);
  const result = [];
  for (let cursor = new Date(start); cursor <= current; cursor.setMonth(cursor.getMonth() + 1)) {
    result.push({
      value: monthKey(cursor.getFullYear(), cursor.getMonth() + 1),
      label: cursor.toLocaleDateString("en-IN", { month: "long", year: "numeric" }),
      year: cursor.getFullYear(),
      month: cursor.getMonth() + 1,
    });
  }
  return result.reverse();
}

function editableMonthKeys() {
  const today = companyDateParts();
  const first = new Date(Number(today.year), Number(today.month) - 1, 1);
  return new Set([1, 2, 3].map((offset) => {
    const date = new Date(first.getFullYear(), first.getMonth() - offset, 1);
    return monthKey(date.getFullYear(), date.getMonth() + 1);
  }));
}

const timeValue = (value) => {
  if (!value) return "";
  const text = String(value);
  const match = text.match(/(?:T|\s)(\d{2}:\d{2})/);
  return match ? match[1] : text.slice(0, 5);
};
const display = (value) => value || "—";
const normaliseKey = (value) => String(value ?? "").trim();
export default function HistoricalAttendanceModal({ getHeaders, onClose }) {
  const [department, setDepartment] = useState("all"); // department id, or "all"
  const [employees, setEmployees] = useState([]);
  const [employeesLoading, setEmployeesLoading] = useState(false);
  const [employeesError, setEmployeesError] = useState("");
  const [departments, setDepartments] = useState([]);
  const [departmentsLoading, setDepartmentsLoading] = useState(true);
  const [departmentsError, setDepartmentsError] = useState("");
  const [employeeSearch, setEmployeeSearch] = useState("");
  const [employeeId, setEmployeeId] = useState("");
  const [selectedMonth, setSelectedMonth] = useState("");
  const [attendanceFilter, setAttendanceFilter] = useState("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [editingDate, setEditingDate] = useState("");
  const [expandedDate, setExpandedDate] = useState("");
  const [historyDate, setHistoryDate] = useState("");
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [edit, setEdit] = useState({
    status: "Not Marked", check_in_time: "", check_out_time: "",
    permission_start_time: "", permission_end_time: "", permission_reason: "", modification_reason: "",
  });

  // Guards against stale async responses: every load bumps its counter and
  // only applies its result while it is still the latest one.
  const employeesRequest = useRef(0);
  const recordsRequest = useRef(0);
  const historyRequest = useRef(0);
  const getHeadersRef = useRef(getHeaders);
  getHeadersRef.current = getHeaders;

  const departmentOptions = useMemo(() => {
    const byId = new Map();
    departments.forEach((item) => {
      const id = normaliseKey(item.id || item._id);
      const name = normaliseKey(item.department_name || item.name);
      if (id && name && !byId.has(id)) byId.set(id, name);
    });
    return [...byId].map(([id, name]) => ({ id, name })).sort((x, y) => x.name.localeCompare(y.name));
  }, [departments]);

  // Complete department list (Department master + departments found on employee records).
  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const response = await fetch(`${API}/departments`, { headers: getHeadersRef.current(), signal: controller.signal });
        const data = await response.json().catch(() => ([]));
        if (!response.ok) throw new Error(data?.detail || "Unable to load departments.");
        setDepartments(Array.isArray(data) ? data : []);
      } catch (err) {
        if (err.name !== "AbortError") setDepartmentsError(err.message || "Unable to load departments.");
      } finally {
        if (!controller.signal.aborted) setDepartmentsLoading(false);
      }
    })();
    return () => controller.abort();
  }, []);

  // Employees come from the server, already resolved to the selected department id.
  useEffect(() => {
    const requestId = ++employeesRequest.current;
    const controller = new AbortController();
    setEmployees([]); setEmployeesLoading(true); setEmployeesError("");
    (async () => {
      try {
        const response = await fetch(`${API}/department-employees?department_id=${encodeURIComponent(department)}`, { headers: getHeadersRef.current(), signal: controller.signal });
        const data = await response.json().catch(() => ([]));
        if (!response.ok) throw new Error(data?.detail || "Unable to load employees.");
        if (requestId !== employeesRequest.current) return;
        const unique = new Map();
        (Array.isArray(data) ? data : []).forEach((item) => { if (item?.id && !unique.has(String(item.id))) unique.set(String(item.id), item); });
        setEmployees([...unique.values()]);
      } catch (err) {
        if (err.name === "AbortError" || requestId !== employeesRequest.current) return;
        setEmployeesError(err.message || "Unable to load employees.");
      } finally {
        if (requestId === employeesRequest.current) setEmployeesLoading(false);
      }
    })();
    return () => controller.abort();
  }, [department]);

  // Drop every piece of per-employee state (and invalidate in-flight requests).
  const clearAttendanceContext = useCallback(() => {
    recordsRequest.current += 1;
    historyRequest.current += 1;
    setRecords([]); setLoading(false);
    setHistory([]); setHistoryDate(""); setHistoryLoading(false);
    setExpandedDate(""); setEditingDate("");
    setError(""); setMessage("");
  }, []);

  const changeDepartment = (value) => {
    clearAttendanceContext();
    setAttendanceFilter("all"); setSourceFilter("all");
    setDepartment(value); setEmployeeId(""); setSelectedMonth("");
  };
  const changeEmployee = (value) => {
    clearAttendanceContext();
    setAttendanceFilter("all"); setSourceFilter("all");
    setEmployeeId(value); setSelectedMonth("");
  };
  const changeMonth = (value) => {
    clearAttendanceContext();
    setSelectedMonth(value);
  };

  // Invalidate anything still running when the popup closes.
  useEffect(() => () => {
    employeesRequest.current += 1; recordsRequest.current += 1; historyRequest.current += 1;
  }, []);

  const filteredEmployees = useMemo(() => {
    const query = employeeSearch.trim().toLowerCase();
    return employees.filter((user) => !query ||
      String(user.name || "").toLowerCase().includes(query) ||
      String(user.employee_id || "").toLowerCase().includes(query));
  }, [employees, employeeSearch]);

  useEffect(() => {
    if (employeeId && !employeesLoading && !filteredEmployees.some((user) => String(user.id) === String(employeeId))) {
      clearAttendanceContext();
      setEmployeeId("");
      setSelectedMonth("");
    }
  }, [filteredEmployees, employeeId, employeesLoading, clearAttendanceContext]);

  const selectedEmployee = employees.find((user) => String(user.id) === String(employeeId));
  const months = useMemo(() => selectedEmployee ? monthsFromJoining(selectedEmployee.joining_date) : [], [selectedEmployee]);
  const editableMonths = useMemo(() => editableMonthKeys(), []);
  useEffect(() => {
    if (months.length && !months.some((item) => item.value === selectedMonth)) setSelectedMonth(months[0].value);
  }, [months, selectedMonth]);

  useEffect(() => {
    const requestId = ++recordsRequest.current;
    const selected = months.find((item) => item.value === selectedMonth);
    if (!employeeId || !selectedMonth || !selected) { setRecords([]); setLoading(false); return undefined; }
    const controller = new AbortController();
    setRecords([]); setLoading(true); setError(""); setMessage(""); setEditingDate(""); setExpandedDate("");
    setHistory([]); setHistoryDate(""); setHistoryLoading(false); historyRequest.current += 1;
    (async () => {
      try {
        const response = await fetch(`${API}/historical/user/${encodeURIComponent(employeeId)}?year=${selected.year}&month=${selected.month}`, { headers: getHeadersRef.current(), signal: controller.signal });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data?.detail || "Unable to load attendance history.");
        if (requestId !== recordsRequest.current) return;
        setRecords(data.records || []);
      } catch (err) {
        if (err.name === "AbortError" || requestId !== recordsRequest.current) return;
        setError(err.message || "Unable to load attendance history.");
      } finally {
        if (requestId === recordsRequest.current) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [employeeId, selectedMonth, months]);

  const sourceOptions = useMemo(() => [...new Set(records.map((row) => row.attendance_source).filter(Boolean))].sort(), [records]);
  const visibleRecords = records.filter((row) =>
    (attendanceFilter === "all" || row.status === attendanceFilter) &&
    (sourceFilter === "all" || row.attendance_source === sourceFilter)
  );

  const payrollStatus = records.find((row) => row.payroll_status)?.payroll_status || null;
  const payrollLocked = records.some((row) => row.payroll_locked) || LOCKED_PAYROLL.has(payrollStatus);
  const monthCanBeEdited = editableMonths.has(selectedMonth);
  const selectedDate = companyDateParts();
  const todayISO = `${selectedDate.year}-${selectedDate.month}-${selectedDate.day}`;

  const canEditRow = (row) => Boolean(
    monthCanBeEdited && row.can_modify !== false && !row.is_before_joining &&
    !row.payroll_locked && !payrollLocked && row.attendance_date < todayISO
  );

  const beginEdit = (row) => {
    setExpandedDate(row.attendance_date);
    setEditingDate(row.attendance_date);
    setEdit({
      status: row.status || "Not Marked",
      check_in_time: timeValue(row.check_in_time),
      check_out_time: timeValue(row.check_out_time),
      permission_start_time: row.permission_start_time || "",
      permission_end_time: row.permission_end_time || "",
      permission_reason: row.permission_reason || "",
      modification_reason: "",
    });
    setError(""); setMessage("");
  };

  const saveEdit = async () => {
    if (!editingDate || !employeeId) return;
    if (!edit.modification_reason.trim()) { setError("Please enter a reason for this attendance modification."); return; }
    if (edit.status === "Present" && !edit.check_in_time) { setError("Check-in time is required for Present attendance."); return; }
    if (edit.status === "Present" && edit.check_in_time && edit.check_out_time && edit.check_out_time < edit.check_in_time) {
      setError("Check-out time cannot be earlier than check-in time."); return;
    }
    setSaving(true); setError(""); setMessage("");
    try {
      const response = await fetch(`${API}/historical/modify`, {
        method: "POST", headers: getHeaders(),
        body: JSON.stringify({
          user_id: employeeId, attendance_date: editingDate, status: edit.status,
          check_in_time: edit.status === "Present" ? edit.check_in_time : null,
          check_out_time: edit.status === "Present" ? (edit.check_out_time || null) : null,
          permission_start_time: edit.permission_start_time || null,
          permission_end_time: edit.permission_end_time || null,
          permission_reason: edit.permission_reason || null,
          modification_reason: edit.modification_reason.trim(),
          expected_updated_at: records.find((row) => row.attendance_date === editingDate)?.updated_at || null,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || "Unable to save attendance changes.");
      setMessage(data.message || "Attendance updated successfully. The employee has been notified.");
      setEditingDate("");
      const selected = months.find((item) => item.value === selectedMonth);
      const requestId = ++recordsRequest.current;
      const refreshed = await fetch(`${API}/historical/user/${encodeURIComponent(employeeId)}?year=${selected.year}&month=${selected.month}`, { headers: getHeaders() });
      const refreshedData = await refreshed.json().catch(() => ({}));
      if (refreshed.ok && requestId === recordsRequest.current) setRecords(refreshedData.records || []);
    } catch (err) { setError(err.message || "Unable to save attendance changes."); }
    finally { setSaving(false); }
  };

  const loadHistory = async (date) => {
    if (!employeeId) return;
    const requestId = ++historyRequest.current;
    const requestedEmployee = employeeId;
    setHistoryDate(date); setHistoryLoading(true); setHistory([]); setError("");
    try {
      const response = await fetch(`${API}/historical/user/${encodeURIComponent(requestedEmployee)}/history?attendance_date=${date}`, { headers: getHeaders() });
      const data = await response.json().catch(() => ([]));
      if (!response.ok) throw new Error(data?.detail || "Unable to load modification history.");
      if (requestId !== historyRequest.current) return;
      setHistory(Array.isArray(data) ? data : []);
    } catch (err) {
      if (requestId === historyRequest.current) setError(err.message || "Unable to load modification history.");
    } finally {
      if (requestId === historyRequest.current) setHistoryLoading(false);
    }
  };

  const resetFilters = () => {
    clearAttendanceContext();
    setAttendanceFilter("all"); setSourceFilter("all");
    setDepartment("all"); setEmployeeSearch(""); setEmployeeId(""); setSelectedMonth("");
  };

  const renderIntervals = (items, label, color) => (
    items?.length ? items.map((interval, index) => (
      <div className={`small ${color}`} key={`${label}-${index}`}>
        {label}: {interval.from_time ? formatIST(interval.from_time) : "—"} – {interval.to_time ? formatIST(interval.to_time) : "—"} ({interval.duration || "duration unavailable"})
      </div>
    )) : <div className="small text-muted">No {label.toLowerCase()} intervals recorded.</div>
  );

  return (
    <div className="modal d-block" role="dialog" aria-modal="true" aria-labelledby="historicalAttendanceTitle" style={{ background: "rgba(0,0,0,.55)", zIndex: 1060 }}>
      <div className="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable" style={{ maxWidth: "min(1400px, 96vw)" }}>
        <div className="modal-content border-0 shadow">
          <div className="modal-header">
            <div>
              <h5 className="modal-title" id="historicalAttendanceTitle">Modify the Previous Attendance</h5>
              <small className="text-muted">History is available from the employee joining month through the current month. The current month is view-only. Only the previous three completed months can be modified, subject to payroll approval locks.</small>
            </div>
            <button type="button" className="btn-close" aria-label="Close" onClick={onClose} />
          </div>
          <div className="modal-body">
            {error && <div className="alert alert-danger py-2">{error}</div>}
            {message && <div className="alert alert-success py-2">{message}</div>}
            <div className="row g-3 align-items-end mb-3">
              <div className="col-12 col-md-3">
                <label className="form-label">Department</label>
                <select className="form-select" value={department} disabled={departmentsLoading} onChange={(e) => changeDepartment(e.target.value)}>
                  <option value="all">{departmentsLoading ? "Loading departments…" : "All departments"}</option>
                  {departmentOptions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
                {departmentsError && <div className="small text-danger mt-1">{departmentsError}</div>}
                {!departmentsLoading && !departmentsError && departmentOptions.length === 0 && <div className="small text-muted mt-1">No departments found.</div>}
              </div>
              <div className="col-12 col-md-3">
                <label className="form-label">Search Employee</label>
                <input className="form-control" value={employeeSearch} onChange={(e) => setEmployeeSearch(e.target.value)} placeholder="Employee ID or name" />
              </div>
              <div className="col-12 col-md-3">
                <label className="form-label">Employee</label>
                <select className="form-select" value={employeeId} disabled={employeesLoading} onChange={(e) => changeEmployee(e.target.value)}>
                  <option value="">{employeesLoading ? "Loading employees…" : filteredEmployees.length ? "Select employee" : "No employees found"}</option>
                  {filteredEmployees.map((user) => <option key={user.id} value={user.id}>{user.name} ({user.employee_id || "No ID"})</option>)}
                </select>
                {employeesError && <div className="small text-danger mt-1">{employeesError}</div>}
              </div>
              <div className="col-12 col-md-3">
                <label className="form-label">Month / Year</label>
                <select className="form-select" value={selectedMonth} disabled={!employeeId} onChange={(e) => changeMonth(e.target.value)}>
                  <option value="">Select month</option>
                  {months.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
              </div>
              <div className="col-12 col-md-3">
                <label className="form-label">Attendance Status</label>
                <select className="form-select" value={attendanceFilter} onChange={(e) => setAttendanceFilter(e.target.value)}>
                  <option value="all">All statuses</option>
                  {STATUSES.map((status) => <option key={status} value={status}>{status}</option>)}
                </select>
              </div>
              <div className="col-12 col-md-3">
                <label className="form-label">Source</label>
                <select className="form-select" value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)}>
                  <option value="all">All sources</option>
                  {sourceOptions.map((source) => <option key={source} value={source}>{source}</option>)}
                </select>
              </div>
              <div className="col-12 col-md-3"><button type="button" className="btn btn-outline-secondary w-100" onClick={resetFilters}><i className="bi bi-arrow-counterclockwise me-1" />Reset filters</button></div>
            </div>

            {selectedEmployee && <div className="small text-muted mb-2">Attendance history for <strong>{selectedEmployee.name}</strong> · {selectedEmployee.employee_id || "No employee ID"} · Joining date: {selectedEmployee.joining_date ? String(selectedEmployee.joining_date).slice(0, 10) : "Not available"}</div>}
            {payrollLocked && <div className="alert alert-warning py-2 mb-3"><strong>Payroll {payrollStatus || "approved"}:</strong> This month's attendance is view-only. You can view attendance details and modification history, but editing is disabled.</div>}
            {employeeId && selectedMonth && !editableMonths.has(selectedMonth) && !payrollLocked && <div className="alert alert-info py-2 mb-3">This month is outside the attendance correction window. Records and history are available in view-only mode.</div>}
            {!employeeId && <div className="text-center text-muted py-5">Select a department and employee to view attendance history.</div>}
            {loading && <div className="text-center py-4"><span className="spinner-border spinner-border-sm me-2" />Loading attendance history…</div>}

            {employeeId && selectedMonth && !loading && (
              <div className="table-responsive border rounded" style={{ maxHeight: "48vh", overflow: "auto" }}>
                <table className="table table-sm table-hover align-middle mb-0">
                  <thead className="table-light" style={{ position: "sticky", top: 0, zIndex: 2 }}>
                    <tr><th>Date</th><th>Day</th><th>Status</th><th>Check-in</th><th>Check-out</th><th>Permission</th><th>Source</th><th>Payroll</th><th className="text-end">Actions</th></tr>
                  </thead>
                  <tbody>
                    {visibleRecords.length === 0 ? <tr><td colSpan="9" className="text-center text-muted py-4">No attendance records match the selected filters.</td></tr> : visibleRecords.map((row) => {
                      const rowCanEdit = canEditRow(row);
                      return (
                        <Fragment key={row.attendance_date}>
                          <tr>
                            <td className="text-nowrap">{row.attendance_date}</td>
                            <td>{new Date(`${row.attendance_date}T00:00:00`).toLocaleDateString("en-IN", { weekday: "short" })}</td>
                            <td><span className="badge text-bg-light border">{row.status}</span></td>
                            <td>{display(timeValue(row.check_in_time))}</td>
                            <td>{display(timeValue(row.check_out_time))}</td>
                            <td>{row.permission_start_time && row.permission_end_time ? `${row.permission_start_time}–${row.permission_end_time}` : "—"}</td>
                            <td>{display(row.attendance_source)}</td>
                            <td>{row.payroll_status || "—"}</td>
                            <td className="text-end text-nowrap">
                              <button type="button" className="btn btn-sm btn-outline-secondary me-1" onClick={() => setExpandedDate(expandedDate === row.attendance_date ? "" : row.attendance_date)}><i className="bi bi-eye me-1" />View</button>
                              <button type="button" className="btn btn-sm btn-outline-primary me-1" disabled={!rowCanEdit} title={!rowCanEdit ? (row.is_before_joining ? "Before employee joining date" : payrollLocked || row.payroll_locked ? "Payroll is approved/processed/paid; view only." : "Only the previous three completed months can be modified.") : "Modify attendance"} onClick={() => beginEdit(row)}><i className="bi bi-pencil-square me-1" />Edit</button>
                              <button type="button" className="btn btn-sm btn-outline-dark" onClick={() => loadHistory(row.attendance_date)} title="View modification history"><i className="bi bi-clock-history me-1" />History</button>
                            </td>
                          </tr>
                          {expandedDate === row.attendance_date && (
                            <tr key={`${row.attendance_date}-details`} className="table-light">
                              <td colSpan="9">
                                <div className="row g-3 p-2">
                                  <div className="col-6 col-lg-3"><strong>Working duration</strong><div>{row.working_duration || "0h 00m"}</div></div>
                                  <div className="col-6 col-lg-3"><strong>Break duration</strong><div>{row.break_duration || "0h 00m"}</div></div>
                                  <div className="col-6 col-lg-3"><strong>Outside duration</strong><div>{row.outside_duration || "0h 00m"}</div></div>
                                  <div className="col-6 col-lg-3"><strong>Attendance state</strong><div>{row.attendance_state || "—"}</div></div>
                                  <div className="col-12 col-lg-6"><strong className="d-block mb-1">Working intervals</strong>{renderIntervals(row.working_intervals, "Work", "text-success")}</div>
                                  <div className="col-12 col-lg-6"><strong className="d-block mb-1">Break intervals</strong>{renderIntervals(row.break_intervals, "Break", "text-warning-emphasis")}</div>
                                </div>
                              </td>
                            </tr>
                          )}
                          {editingDate === row.attendance_date && (
                            <tr key={`${row.attendance_date}-edit`}>
                              <td colSpan="9">
                                <div className="border rounded p-3 my-2 bg-light">
                                  <div className="d-flex flex-wrap justify-content-between align-items-center mb-3 gap-2">
                                    <strong>Edit attendance · {editingDate}</strong>
                                    <button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => setEditingDate("")}>Cancel edit</button>
                                  </div>
                                  <div className="row g-3">
                                    <div className="col-12 col-md-3"><label className="form-label">Attendance Status</label><select className="form-select" value={edit.status} onChange={(e) => setEdit((old) => ({ ...old, status: e.target.value }))}>{STATUSES.map((status) => <option key={status}>{status}</option>)}</select></div>
                                    <div className="col-6 col-md-2"><label className="form-label">Check-in</label><input type="time" className="form-control" disabled={edit.status !== "Present"} value={edit.check_in_time} onChange={(e) => setEdit((old) => ({ ...old, check_in_time: e.target.value }))} /></div>
                                    <div className="col-6 col-md-2"><label className="form-label">Check-out</label><input type="time" className="form-control" disabled={edit.status !== "Present"} value={edit.check_out_time} onChange={(e) => setEdit((old) => ({ ...old, check_out_time: e.target.value }))} /></div>
                                    <div className="col-6 col-md-2"><label className="form-label">Permission Start</label><input type="time" className="form-control" value={edit.permission_start_time} onChange={(e) => setEdit((old) => ({ ...old, permission_start_time: e.target.value }))} /></div>
                                    <div className="col-6 col-md-3"><label className="form-label">Permission End</label><input type="time" className="form-control" value={edit.permission_end_time} onChange={(e) => setEdit((old) => ({ ...old, permission_end_time: e.target.value }))} /></div>
                                    <div className="col-12 col-md-5"><label className="form-label">Permission Reason</label><input className="form-control" value={edit.permission_reason} onChange={(e) => setEdit((old) => ({ ...old, permission_reason: e.target.value }))} /></div>
                                    <div className="col-12 col-md-7"><label className="form-label">Reason for Modification <span className="text-danger">*</span></label><input className="form-control" maxLength={500} value={edit.modification_reason} onChange={(e) => setEdit((old) => ({ ...old, modification_reason: e.target.value }))} placeholder="Explain why this attendance is being corrected" /></div>
                                    <div className="col-12 d-flex justify-content-end"><button type="button" className="btn btn-primary" disabled={saving} onClick={saveEdit}>{saving ? "Saving…" : "Save Attendance Change"}</button></div>
                                  </div>
                                </div>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {historyDate && <div className="border rounded p-3 mt-3">
              <div className="d-flex justify-content-between align-items-center"><strong>Modification history · {historyDate}</strong><button className="btn btn-sm btn-outline-secondary" onClick={() => setHistoryDate("")}>Close</button></div>
              {historyLoading ? <div className="py-3 text-muted"><span className="spinner-border spinner-border-sm me-2" />Loading history…</div> : history.length === 0 ? <div className="py-3 text-muted">No modification history available for this attendance date.</div> : history.map((item) => {
                const rows = buildChangeRows(item.changes);
                return (
                  <div className="border-top mt-3 pt-3" key={item.id}>
                    <div className="fw-semibold">{item.modified_by_name || "Unknown user"} <span className="text-muted fw-normal">· {item.modified_at_ist || formatIST(item.modified_at)}</span></div>
                    <div className="small mt-1"><strong>Reason:</strong> {item.reason || "Not Available"}</div>
                    <div className="small mt-2"><strong>Changed Details</strong></div>
                    {rows.length === 0 ? <div className="small text-muted">No field changes were recorded.</div> : (
                      <ul className="small mb-0 mt-1 ps-3">
                        {rows.map((row, index) => (
                          <li key={`${row.label}-${index}`} className="mb-1" style={{ overflowWrap: "anywhere" }}>
                            <strong>{row.label}:</strong> {row.before} → {row.after}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                );
              })}
            </div>}
          </div>
          <div className="modal-footer"><button type="button" className="btn btn-secondary" onClick={onClose}>Close</button></div>
        </div>
      </div>
    </div>
  );
}
