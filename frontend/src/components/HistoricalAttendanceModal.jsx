import { useEffect, useMemo, useState } from "react";
import { HRONE_API_BASE } from "../config/api.js";

const API = `${HRONE_API_BASE}/api/attendance`;
const STATUSES = ["Present", "Absent", "Holiday", "Leave", "Weekly Off", "Medical Leave", "Permission", "Not Applicable", "Not Marked"];

function previousCompletedMonths() {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit" }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  const first = new Date(Number(values.year), Number(values.month) - 1, 1);
  return [1, 2, 3].map((offset) => {
    const value = new Date(first.getFullYear(), first.getMonth() - offset, 1);
    return {
      value: `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}`,
      label: value.toLocaleDateString("en-IN", { month: "long", year: "numeric" }),
      year: value.getFullYear(),
      month: value.getMonth() + 1,
    };
  });
}

const timeValue = (value) => {
  if (!value) return "";
  const text = String(value);
  const match = text.match(/(?:T|\s)(\d{2}:\d{2})/);
  return match ? match[1] : text.slice(0, 5);
};

const display = (value) => value || "—";

export default function HistoricalAttendanceModal({ users, departments, getHeaders, onClose }) {
  const months = useMemo(() => previousCompletedMonths(), []);
  const [selectedMonth, setSelectedMonth] = useState(months[0]?.value || "");
  const [department, setDepartment] = useState("all");
  const [employeeSearch, setEmployeeSearch] = useState("");
  const [employeeId, setEmployeeId] = useState("");
  const [attendanceFilter, setAttendanceFilter] = useState("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [editingDate, setEditingDate] = useState("");
  const [edit, setEdit] = useState({ status: "Not Marked", check_in_time: "", check_out_time: "", permission_start_time: "", permission_end_time: "", permission_reason: "", modification_reason: "" });
  const [historyDate, setHistoryDate] = useState("");
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  const selectedMonthData = months.find((item) => item.value === selectedMonth) || months[0];
  const filteredEmployees = useMemo(() => {
    const query = employeeSearch.trim().toLowerCase();
    return users.filter((user) => {
      const dept = String(user.department || "").trim().toLowerCase();
      const matchesDepartment = department === "all" || dept === department.toLowerCase();
      const matchesSearch = !query || String(user.name || "").toLowerCase().includes(query) || String(user.employee_id || "").toLowerCase().includes(query);
      return matchesDepartment && matchesSearch;
    });
  }, [users, department, employeeSearch]);

  useEffect(() => {
    if (employeeId && !filteredEmployees.some((user) => String(user.id) === employeeId)) setEmployeeId("");
  }, [filteredEmployees, employeeId]);

  useEffect(() => {
    const load = async () => {
      if (!employeeId || !selectedMonthData) { setRecords([]); return; }
      setLoading(true); setError(""); setMessage(""); setEditingDate("");
      try {
        const response = await fetch(`${API}/historical/user/${encodeURIComponent(employeeId)}?year=${selectedMonthData.year}&month=${selectedMonthData.month}`, { headers: getHeaders() });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data?.detail || "Unable to load historical attendance.");
        setRecords(data.records || []);
      } catch (err) { setError(err.message || "Unable to load historical attendance."); }
      finally { setLoading(false); }
    };
    load();
  }, [employeeId, selectedMonth]);

  const sourceOptions = useMemo(() => [...new Set(records.map((row) => row.attendance_source).filter(Boolean))].sort(), [records]);
  const visibleRecords = records.filter((row) =>
    (attendanceFilter === "all" || row.status === attendanceFilter) &&
    (sourceFilter === "all" || row.attendance_source === sourceFilter)
  );
  const selectedEmployee = users.find((user) => String(user.id) === employeeId);

  const beginEdit = (row) => {
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
  };

  const saveEdit = async () => {
    if (!editingDate || !employeeId) return;
    if (!edit.modification_reason.trim()) { setError("Please enter a reason for this attendance modification."); return; }
    if (edit.status === "Present" && !edit.check_in_time) { setError("Check-in time is required for Present attendance."); return; }
    if (edit.check_in_time && edit.check_out_time && edit.check_out_time < edit.check_in_time && edit.status === "Present") { setError("Check-out time cannot be earlier than check-in time."); return; }
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
      setMessage(data.message || "Historical attendance updated successfully.");
      setEditingDate("");
      const refreshed = await fetch(`${API}/historical/user/${encodeURIComponent(employeeId)}?year=${selectedMonthData.year}&month=${selectedMonthData.month}`, { headers: getHeaders() });
      const refreshedData = await refreshed.json().catch(() => ({}));
      if (refreshed.ok) setRecords(refreshedData.records || []);
    } catch (err) { setError(err.message || "Unable to save attendance changes."); }
    finally { setSaving(false); }
  };

  const loadHistory = async (date) => {
    setHistoryDate(date); setHistoryLoading(true); setHistory([]); setError("");
    try {
      const response = await fetch(`${API}/historical/user/${encodeURIComponent(employeeId)}/history?attendance_date=${date}`, { headers: getHeaders() });
      const data = await response.json().catch(() => ([]));
      if (!response.ok) throw new Error(data?.detail || "Unable to load modification history.");
      setHistory(Array.isArray(data) ? data : []);
    } catch (err) { setError(err.message || "Unable to load modification history."); }
    finally { setHistoryLoading(false); }
  };

  const resetFilters = () => { setDepartment("all"); setEmployeeSearch(""); setEmployeeId(""); setAttendanceFilter("all"); setSourceFilter("all"); setHistoryDate(""); setHistory([]); setError(""); setMessage(""); };

  return (
    <div className="modal d-block" role="dialog" aria-modal="true" aria-labelledby="historicalAttendanceTitle" style={{ background: "rgba(0,0,0,.55)", zIndex: 1060 }}>
      <div className="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable" style={{ maxWidth: "min(1200px, 96vw)" }}>
        <div className="modal-content border-0 shadow">
          <div className="modal-header">
            <div><h5 className="modal-title" id="historicalAttendanceTitle">Historical Attendance Modification</h5><small className="text-muted">Only the previous three completed months are available. Current month is excluded.</small></div>
            <button type="button" className="btn-close" aria-label="Close" onClick={onClose} />
          </div>
          <div className="modal-body">
            {error && <div className="alert alert-danger py-2">{error}</div>}
            {message && <div className="alert alert-success py-2">{message}</div>}
            <div className="row g-3 align-items-end mb-3">
              <div className="col-12 col-md-3"><label className="form-label">Department</label><select className="form-select" value={department} onChange={(e) => setDepartment(e.target.value)}><option value="all">All departments</option>{departments.map((item) => <option key={item.id || item.department_name} value={item.department_name}>{item.department_name}</option>)}</select></div>
              <div className="col-12 col-md-3"><label className="form-label">Search Employee</label><input className="form-control" value={employeeSearch} onChange={(e) => setEmployeeSearch(e.target.value)} placeholder="Employee ID or name" /></div>
              <div className="col-12 col-md-3"><label className="form-label">Employee</label><select className="form-select" value={employeeId} onChange={(e) => setEmployeeId(e.target.value)}><option value="">Select employee</option>{filteredEmployees.map((user) => <option key={user.id} value={user.id}>{user.name} ({user.employee_id || "No ID"})</option>)}</select></div>
              <div className="col-12 col-md-3"><label className="form-label">Month / Year</label><select className="form-select" value={selectedMonth} onChange={(e) => setSelectedMonth(e.target.value)}>{months.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></div>
              <div className="col-12 col-md-3"><label className="form-label">Attendance Status</label><select className="form-select" value={attendanceFilter} onChange={(e) => setAttendanceFilter(e.target.value)}><option value="all">All statuses</option>{STATUSES.map((status) => <option key={status} value={status}>{status}</option>)}</select></div>
              <div className="col-12 col-md-3"><label className="form-label">Source</label><select className="form-select" value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)}><option value="all">All sources</option>{sourceOptions.map((source) => <option key={source} value={source}>{source}</option>)}</select></div>
              <div className="col-12 col-md-3"><button type="button" className="btn btn-outline-secondary w-100" onClick={resetFilters}><i className="bi bi-arrow-counterclockwise me-1" />Reset filters</button></div>
            </div>
            {selectedEmployee && <div className="small text-muted mb-2">Attendance history for <strong>{selectedEmployee.name}</strong> · {selectedEmployee.employee_id || "No employee ID"}</div>}
            {!employeeId && <div className="text-center text-muted py-5">Select a department and employee to view attendance history.</div>}
            {loading && <div className="text-center py-4"><span className="spinner-border spinner-border-sm me-2" />Loading attendance history…</div>}
            {employeeId && !loading && <div className="table-responsive border rounded" style={{ maxHeight: "46vh", overflowY: "auto" }}><table className="table table-sm table-hover align-middle mb-0"><thead className="table-light" style={{ position: "sticky", top: 0, zIndex: 2 }}><tr><th>Date</th><th>Day</th><th>Status</th><th>Check-in</th><th>Check-out</th><th>Permission Time</th><th>Source</th><th>Modification Access</th><th className="text-end">Actions</th></tr></thead><tbody>
              {visibleRecords.length === 0 ? <tr><td colSpan="9" className="text-center text-muted py-4">No attendance records match the selected filters.</td></tr> : visibleRecords.map((row) => <tr key={row.attendance_date}>
                <td className="text-nowrap">{row.attendance_date}</td><td>{new Date(`${row.attendance_date}T00:00:00`).toLocaleDateString("en-IN", { weekday: "short" })}</td><td><span className="badge text-bg-light border">{row.status}</span></td><td>{display(timeValue(row.check_in_time))}</td><td>{display(timeValue(row.check_out_time))}</td><td>{row.permission_start_time && row.permission_end_time ? `${row.permission_start_time}–${row.permission_end_time}` : "—"}</td><td>{display(row.attendance_source)}</td><td><span className={`badge ${row.is_before_joining ? "text-bg-secondary" : "text-bg-success"}`}>{row.is_before_joining ? "Unavailable" : "Allowed"}</span></td>
                <td className="text-end text-nowrap"><button type="button" className="btn btn-sm btn-outline-primary me-1" disabled={!row.has_modification_permission || row.is_before_joining} title={row.is_before_joining ? "Before employee joining date" : "Modify attendance"} onClick={() => beginEdit(row)}><i className="bi bi-pencil-square" /> Edit</button><button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => loadHistory(row.attendance_date)} title="View audit history"><i className="bi bi-clock-history" /> History</button></td>
              </tr>)}
            </tbody></table></div>}
            {editingDate && <div className="border rounded p-3 mt-3 bg-light"><div className="d-flex justify-content-between align-items-center mb-3"><strong>Modify attendance · {editingDate}</strong><button className="btn btn-sm btn-outline-secondary" onClick={() => setEditingDate("")}>Cancel</button></div><div className="row g-3">
              <div className="col-12 col-md-3"><label className="form-label">Attendance Status</label><select className="form-select" value={edit.status} onChange={(e) => setEdit((old) => ({ ...old, status: e.target.value }))}>{STATUSES.map((status) => <option key={status}>{status}</option>)}</select></div>
              <div className="col-6 col-md-2"><label className="form-label">Check-in</label><input type="time" className="form-control" disabled={edit.status !== "Present"} value={edit.check_in_time} onChange={(e) => setEdit((old) => ({ ...old, check_in_time: e.target.value }))} /></div>
              <div className="col-6 col-md-2"><label className="form-label">Check-out</label><input type="time" className="form-control" disabled={edit.status !== "Present"} value={edit.check_out_time} onChange={(e) => setEdit((old) => ({ ...old, check_out_time: e.target.value }))} /></div>
              <div className="col-6 col-md-2"><label className="form-label">Permission Start</label><input type="time" className="form-control" value={edit.permission_start_time} onChange={(e) => setEdit((old) => ({ ...old, permission_start_time: e.target.value }))} /></div>
              <div className="col-6 col-md-3"><label className="form-label">Permission End</label><input type="time" className="form-control" value={edit.permission_end_time} onChange={(e) => setEdit((old) => ({ ...old, permission_end_time: e.target.value }))} /></div>
              <div className="col-12 col-md-5"><label className="form-label">Permission Reason</label><input className="form-control" value={edit.permission_reason} onChange={(e) => setEdit((old) => ({ ...old, permission_reason: e.target.value }))} /></div>
              <div className="col-12 col-md-7"><label className="form-label">Reason for Modification <span className="text-danger">*</span></label><input className="form-control" maxLength={500} value={edit.modification_reason} onChange={(e) => setEdit((old) => ({ ...old, modification_reason: e.target.value }))} placeholder="Explain why this attendance is being corrected" /></div>
              <div className="col-12 d-flex justify-content-end"><button type="button" className="btn btn-primary" disabled={saving} onClick={saveEdit}>{saving ? "Saving…" : "Save Attendance Change"}</button></div>
            </div></div>}
            {historyDate && <div className="border rounded p-3 mt-3"><div className="d-flex justify-content-between align-items-center"><strong>Modification history · {historyDate}</strong><button className="btn btn-sm btn-outline-secondary" onClick={() => setHistoryDate("")}>Close</button></div>{historyLoading ? <div className="py-3 text-muted">Loading history…</div> : history.length === 0 ? <div className="py-3 text-muted">No modifications have been recorded for this date.</div> : history.map((item) => <div className="border-top mt-2 pt-2" key={item.id}><div className="small text-muted">{item.modified_by_name || item.modified_by} · {item.modified_at ? new Date(item.modified_at).toLocaleString() : ""}</div><div className="small">Reason: {item.reason || "—"}</div><ul className="small mb-1">{Object.entries(item.changes || {}).map(([field, values]) => <li key={field}><strong>{field.replaceAll("_", " ")}</strong>: {JSON.stringify(values.before ?? null)} → {JSON.stringify(values.after ?? null)}</li>)}</ul></div>)}</div>}
          </div>
          <div className="modal-footer"><button type="button" className="btn btn-secondary" onClick={onClose}>Close</button></div>
        </div>
      </div>
    </div>
  );
}
