import { HRONE_API_BASE } from "../config/api.js";
import { useCallback, useEffect, useMemo, useState } from "react";
import AttendanceDayBreakdown from "../components/AttendanceDayBreakdown";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || HRONE_API_BASE;

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

// Same colours as Profile -> Attendance so both views look alike.
const STATUS_COLORS = {
  present: { background: "#198754", text: "#ffffff", label: "Present" },
  absent: { background: "#dc3545", text: "#ffffff", label: "Absent" },
  leave: { background: "#6f42c1", text: "#ffffff", label: "Leave" },
  holiday: { background: "#ADD8E6", text: "#0b3d4a", label: "Holiday" },
  notmarked: { background: "#F0E0C8", text: "#7a5a2f", label: "Not Marked" },
};

const companyToday = () => {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const value = Object.fromEntries(parts.map(({ type, value: v }) => [type, v]));
  return { year: Number(value.year), month: Number(value.month), day: Number(value.day) };
};

const formatTime = (time) => {
  if (!time) return "Not marked";
  const [hourText, minute] = String(time).split(":");
  const hour = Number(hourText);
  return `${hour % 12 || 12}:${minute} ${hour >= 12 ? "PM" : "AM"}`;
};

const formatLocalDateTime = (value) => {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  }).format(parsed);
};

const sourceLabel = (record) => {
  if (!record) return "—";
  if (record.attendance_source === "ESSL") return "ESSL biometric";
  if (record.attendance_source === "MANUAL") return "Manual (HR)";
  if (record.attendance_source === "WFH_FACE_GPS") return "WFH · Face + GPS";
  return record.attendance_source || "—";
};

const statusKey = (record) => {
  const status = String(record?.status || "").trim().toLowerCase();
  return STATUS_COLORS[status] ? status : "notmarked";
};

const readError = async (response, fallback) => {
  try {
    const data = await response.json();
    return typeof data.detail === "string" ? data.detail : fallback;
  } catch {
    return fallback;
  }
};

function UserAttendance({ session }) {
  const token = session?.token;
  const today = useMemo(companyToday, []);

  const [employees, setEmployees] = useState([]);
  const [employeesLoading, setEmployeesLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [selectedUserId, setSelectedUserId] = useState("");
  const [year, setYear] = useState(today.year);
  const [month, setMonth] = useState(today.month);
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedDate, setSelectedDate] = useState(null);
  const [monthlyDetails, setMonthlyDetails] = useState(null);
  const [monthlyDetailsLoading, setMonthlyDetailsLoading] = useState(false);
  const [monthlyDetailsError, setMonthlyDetailsError] = useState("");
  const [exportLoading, setExportLoading] = useState(false);

  const headers = useMemo(() => ({ Authorization: `Bearer ${token}` }), [token]);

  // ---- employee list ------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/api/attendance/employees`, { headers });
        if (!response.ok) throw new Error(await readError(response, "Unable to load employees."));
        const data = await response.json();
        if (!cancelled) setEmployees(Array.isArray(data) ? data : []);
      } catch (e) {
        if (!cancelled) setError(e.message || "Unable to load employees.");
      } finally {
        if (!cancelled) setEmployeesLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [headers]);

  const filteredEmployees = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return employees;
    return employees.filter((e) =>
      `${e.name} ${e.employee_id} ${e.email}`.toLowerCase().includes(needle),
    );
  }, [employees, search]);

  const selectedEmployee = employees.find((e) => e.user_id === selectedUserId) || null;

  // ---- attendance of the chosen employee ----------------------------------
  const loadAttendance = useCallback(async () => {
    if (!selectedUserId) {
      setRecords([]);
      return;
    }
    setLoading(true);
    setError("");
    setSelectedDate(null);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/attendance/user/${selectedUserId}?year=${year}&month=${month}`,
        { headers },
      );
      if (!response.ok) throw new Error(await readError(response, "Unable to load attendance."));
      const data = await response.json();
      setRecords(Array.isArray(data) ? data : []);
    } catch (e) {
      setRecords([]);
      setError(e.message || "Unable to load attendance.");
    } finally {
      setLoading(false);
    }
  }, [selectedUserId, year, month, headers]);

  const loadMonthlyDetails = useCallback(async () => {
    if (!selectedUserId) {
      setMonthlyDetails(null);
      setMonthlyDetailsError("Select an employee first.");
      return;
    }
    setMonthlyDetailsLoading(true);
    setMonthlyDetailsError("");
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/attendance/user/${encodeURIComponent(selectedUserId)}/monthly-details?year=${year}&month=${month}`,
        { headers },
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.detail || "Unable to load monthly attendance details.");
      setMonthlyDetails(body);
    } catch (err) {
      setMonthlyDetails(null);
      setMonthlyDetailsError(err?.message || "Unable to load monthly attendance details.");
    } finally {
      setMonthlyDetailsLoading(false);
    }
  }, [selectedUserId, year, month, headers]);

  const downloadMonthlyExcel = useCallback(async () => {
    if (!selectedUserId) {
      setError("Select an employee before downloading attendance.");
      return;
    }
    setExportLoading(true);
    setError("");
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/attendance/user/${encodeURIComponent(selectedUserId)}/export.xlsx?year=${year}&month=${month}`,
        { headers },
      );
      if (!response.ok) throw new Error(await readError(response, "Unable to export attendance."));
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") || "";
      const match = disposition.match(/filename="?([^";]+)"?/i);
      const filename = match?.[1] || `Employee_Attendance_${selectedEmployee?.employee_id || "employee"}_${MONTH_NAMES[month - 1]}_${year}.xlsx`;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err?.message || "Unable to export attendance.");
    } finally {
      setExportLoading(false);
    }
  }, [selectedUserId, year, month, headers, selectedEmployee]);

  useEffect(() => { loadAttendance(); }, [loadAttendance]);
  useEffect(() => { setMonthlyDetails(null); setMonthlyDetailsError(""); }, [selectedUserId, year, month]);

  const recordByDate = useMemo(() => {
    const map = {};
    records.forEach((r) => { map[String(r.attendance_date).slice(0, 10)] = r; });
    return map;
  }, [records]);

  const daysInMonth = new Date(year, month, 0).getDate();
  const firstWeekday = new Date(year, month - 1, 1).getDay();
  const dateKey = (day) => `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  const isFuture = (day) => dateKey(day) > `${today.year}-${String(today.month).padStart(2, "0")}-${String(today.day).padStart(2, "0")}`;

  const counts = useMemo(() => {
    const result = { present: 0, absent: 0, leave: 0, holiday: 0 };
    records.forEach((r) => {
      const key = String(r.status || "").toLowerCase();
      if (key in result) result[key] += 1;
    });
    return result;
  }, [records]);
  const marked = counts.present + counts.absent + counts.leave;
  const percent = (value) => (marked ? ((value / marked) * 100).toFixed(1) : "0.0");

  const cells = [];
  for (let i = 0; i < firstWeekday; i += 1) cells.push(null);
  for (let day = 1; day <= daysInMonth; day += 1) cells.push(day);
  while (cells.length % 7) cells.push(null);

  const years = [today.year, today.year - 1, today.year - 2];
  const selectedRecord = selectedDate ? recordByDate[selectedDate] : null;

  return (
    <div className="container-fluid">
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <h2 className="mb-1">Employee Attendance</h2>
          <p className="text-muted mb-0">View any employee's monthly attendance calendar (read-only)</p>
        </div>
      </div>

      <div className="card mb-4">
        <div className="card-body">
          <div className="row g-3 align-items-end">
            <div className="col-lg-3 col-md-6">
              <label className="form-label fw-semibold" htmlFor="ua-search">Search employee</label>
              <input
                id="ua-search"
                className="form-control"
                placeholder="Name, ID or email"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <div className="col-lg-4 col-md-6">
              <label className="form-label fw-semibold" htmlFor="ua-employee">Employee</label>
              <select
                id="ua-employee"
                className="form-select"
                value={selectedUserId}
                disabled={employeesLoading}
                onChange={(e) => setSelectedUserId(e.target.value)}
              >
                <option value="">{employeesLoading ? "Loading employees..." : "Select an employee"}</option>
                {filteredEmployees.map((e) => (
                  <option key={e.user_id} value={e.user_id}>
                    {e.name} ({e.employee_id})
                  </option>
                ))}
              </select>
            </div>
            <div className="col-lg-3 col-md-6">
              <label className="form-label fw-semibold" htmlFor="ua-month">Month</label>
              <select id="ua-month" className="form-select" value={month} onChange={(e) => setMonth(Number(e.target.value))}>
                {MONTH_NAMES.map((name, index) => (
                  <option key={name} value={index + 1} disabled={year === today.year && index + 1 > today.month}>
                    {name}
                  </option>
                ))}
              </select>
            </div>
            <div className="col-lg-2 col-md-6">
              <label className="form-label fw-semibold" htmlFor="ua-year">Year</label>
              <select
                id="ua-year"
                className="form-select"
                value={year}
                onChange={(e) => {
                  const next = Number(e.target.value);
                  setYear(next);
                  if (next === today.year && month > today.month) setMonth(today.month);
                }}
              >
                {years.map((y) => <option key={y} value={y}>{y}</option>)}
              </select>
            </div>
          </div>
        </div>
      </div>

      {error && <div className="alert alert-danger" role="alert">{error}</div>}

      {!selectedEmployee ? (
        <div className="card">
          <div className="card-body text-center text-muted py-5">
            <i className="bi bi-person-lines-fill fs-1 d-block mb-2"></i>
            Select an employee to view their attendance.
          </div>
        </div>
      ) : (
        <div className="card">
          <div className="card-body">
            <div className="d-flex justify-content-between align-items-center flex-wrap gap-2 mb-3">
              <div>
                <h5 className="mb-1">{selectedEmployee.name}</h5>
                <p className="text-muted mb-0">
                  {selectedEmployee.employee_id} · {selectedEmployee.role}
                </p>
              </div>
              <div className="d-flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={loadMonthlyDetails}
                  disabled={loading || monthlyDetailsLoading}
                >
                  <i className="bi bi-eye me-1"></i>
                  {monthlyDetailsLoading ? "Loading..." : "View Attendance"}
                </button>
                <button
                  type="button"
                  className="btn btn-success btn-sm"
                  onClick={downloadMonthlyExcel}
                  disabled={loading || exportLoading}
                >
                  <i className="bi bi-file-earmark-excel me-1"></i>
                  {exportLoading ? "Preparing Excel..." : "Download Excel"}
                </button>
                <button type="button" className="btn btn-outline-secondary btn-sm" onClick={loadAttendance} disabled={loading}>
                  <i className="bi bi-arrow-clockwise me-1"></i>Refresh
                </button>
              </div>
            </div>

            <div className="row g-3 mb-3">
              {[
                ["present", counts.present, percent(counts.present)],
                ["absent", counts.absent, percent(counts.absent)],
                ["leave", counts.leave, percent(counts.leave)],
                ["holiday", counts.holiday, null],
              ].map(([key, value, pct]) => (
                <div className="col-6 col-lg-3" key={key}>
                  <div className="border rounded p-3 h-100" style={{ borderLeft: `5px solid ${STATUS_COLORS[key].background}` }}>
                    <div className="text-muted small">{STATUS_COLORS[key].label}</div>
                    <div className="fs-4 fw-semibold">{value}</div>
                    {pct !== null && <div className="text-muted small">{pct}% of marked days</div>}
                  </div>
                </div>
              ))}
            </div>

            <div className="d-flex flex-wrap gap-3 mb-3">
              {Object.entries(STATUS_COLORS).map(([key, c]) => (
                <div className="d-flex align-items-center gap-2" key={key}>
                  <span style={{ width: 20, height: 20, borderRadius: 4, backgroundColor: c.background, border: "1px solid rgba(0,0,0,0.15)", display: "inline-block" }}></span>
                  <span className="text-muted">{c.label === "Not Marked" ? "Not Marked Yet" : c.label}</span>
                </div>
              ))}
            </div>

            {loading ? (
              <div className="text-center py-5">
                <div className="spinner-border text-primary" role="status"></div>
                <p className="text-muted mt-2 mb-0">Loading attendance...</p>
              </div>
            ) : (
              <div className="border rounded p-3">
                <div className="fw-semibold mb-3" style={{ fontSize: 16 }}>{MONTH_NAMES[month - 1]} {year}</div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 8, marginBottom: 8 }}>
                  {WEEKDAYS.map((d) => (
                    <div key={d} className="text-center text-muted fw-semibold" style={{ fontSize: 12, padding: "4px 0" }}>{d}</div>
                  ))}
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 8 }}>
                  {cells.map((day, index) => {
                    if (!day) return <div key={`blank-${index}`} />;
                    const key = dateKey(day);
                    const record = recordByDate[key];
                    const colors = STATUS_COLORS[isFuture(day) && !record ? "notmarked" : statusKey(record)];
                    const selected = selectedDate === key;
                    return (
                      <button
                        key={key}
                        type="button"
                        onClick={() => setSelectedDate(key)}
                        title={record ? `${colors.label}${record.check_in_time ? ` · In ${formatTime(record.check_in_time)}` : ""}` : "Not marked"}
                        style={{
                          minHeight: 64,
                          border: selected ? "2px solid #0d6efd" : "1px solid rgba(0,0,0,0.12)",
                          borderRadius: 8,
                          backgroundColor: colors.background,
                          color: colors.text,
                          opacity: isFuture(day) && !record ? 0.55 : 1,
                          textAlign: "left",
                          padding: "6px 8px",
                          cursor: "pointer",
                        }}
                      >
                        <div className="fw-semibold">{day}</div>
                        <div style={{ fontSize: 11 }}>{isFuture(day) && !record ? "—" : colors.label}</div>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {selectedDate && !loading && (
              <div className="border rounded p-3 mt-3 bg-light">
                <h6 className="mb-3">{new Date(`${selectedDate}T00:00:00`).toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" })}</h6>
                <div className="row g-2">
                  <div className="col-md-3"><div className="text-muted small">Status</div><div className="fw-semibold">{STATUS_COLORS[statusKey(selectedRecord)].label}</div></div>
                  <div className="col-md-3"><div className="text-muted small">Check-in</div><div className="fw-semibold">{formatTime(selectedRecord?.check_in_time)}</div></div>
                  <div className="col-md-3"><div className="text-muted small">Check-out</div><div className="fw-semibold">{formatTime(selectedRecord?.check_out_time)}</div></div>
                  <div className="col-md-3"><div className="text-muted small">Source</div><div className="fw-semibold">{sourceLabel(selectedRecord)}</div></div>
                  {selectedRecord?.leave_type_code && (
                    <div className="col-md-3"><div className="text-muted small">Leave type</div><div className="fw-semibold">{selectedRecord.leave_type_code}{selectedRecord.leave_day_part && selectedRecord.leave_day_part !== "full_day" ? ` (${selectedRecord.leave_day_part.replace("_", " ")})` : ""}</div></div>
                  )}
                </div>
              </div>
            )}
            {monthlyDetailsError && <div className="alert alert-danger mt-3 mb-0" role="alert">{monthlyDetailsError}</div>}

            {monthlyDetails && (
              <section className="mt-4">
                <div className="d-flex flex-wrap justify-content-between align-items-end gap-2 mb-3">
                  <div>
                    <h5 className="mb-1">Monthly Attendance Details</h5>
                    <div className="text-muted small">
                      {MONTH_NAMES[monthlyDetails.month - 1]} {monthlyDetails.year} · {monthlyDetails.records.length} recorded day(s)
                    </div>
                  </div>
                  <div className="d-flex flex-wrap gap-3 small">
                    <span><strong>Employee:</strong> {monthlyDetails.employee?.name || selectedEmployee.name}</span>
                    <span><strong>ID:</strong> {monthlyDetails.employee?.employee_id || selectedEmployee.employee_id}</span>
                  </div>
                </div>
                <div className="table-responsive border rounded monthly-attendance-scroll" style={{ maxHeight: "480px", overflowY: "auto", overflowX: "auto" }}>
                  <table className="table table-hover table-striped align-middle mb-0">
                    <thead className="table-light">
                      <tr>
                        <th>Date</th>
                        <th>Status</th>
                        <th>First Check-In</th>
                        <th>Last Check-Out</th>
                        <th>Break Duration</th>
                        <th>Break Time List</th>
                        <th>Net Working Time</th>
                        <th>Attendance Type</th>
                        <th>Source</th>
                        <th>Remarks</th>
                      </tr>
                    </thead>
                    <tbody>
                      {monthlyDetails.records.map((record) => (
                        <tr key={record.attendance_date}>
                          <td className="text-nowrap">
                            {new Date(`${record.attendance_date}T00:00:00`).toLocaleDateString("en-IN", {
                              timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric",
                            })}
                          </td>
                          <td>
                            <span className={`badge ${String(record.status).toLowerCase() === "present" ? "text-bg-success" : String(record.status).toLowerCase() === "absent" ? "text-bg-danger" : ["leave", "holiday"].includes(String(record.status).toLowerCase()) ? "text-bg-warning" : "text-bg-secondary"}`}>
                              {record.status || "Not Marked"}
                            </span>
                          </td>
                          <td className="text-nowrap">{formatLocalDateTime(record.first_check_in)}</td>
                          <td className="text-nowrap">{formatLocalDateTime(record.last_check_out)}</td>
                          <td className="text-nowrap">{record.break_duration || "0h 00m"}</td>
                          <td style={{ minWidth: 220, whiteSpace: "pre-line" }}>
                            {record.break_intervals?.length
                              ? record.break_intervals.map((item, index) => (
                                <div key={`${item.from_time}-${index}`} className="small mb-1">
                                  <span className="fw-semibold">{formatLocalDateTime(item.from_time)} – {formatLocalDateTime(item.to_time)}</span>
                                  <span className="text-muted"> ({item.duration || `${Math.floor((item.duration_minutes || 0) / 60)}h ${String((item.duration_minutes || 0) % 60).padStart(2, "0")}m`})</span>
                                </div>
                              ))
                              : <span className="text-muted small">No qualifying breaks</span>}
                          </td>
                          <td className="text-nowrap">{record.working_duration || "0h 00m"}</td>
                          <td>{record.attendance_type === "WFH" ? "Work From Home" : record.attendance_type === "OFFICE" ? "Work From Office" : record.attendance_type || "—"}</td>
                          <td>{record.attendance_source || "—"}</td>
                          <td style={{ minWidth: 220, whiteSpace: "pre-wrap" }}>{record.remarks || "—"}</td>
                        </tr>
                      ))}
                      {!monthlyDetails.records.length && (
                        <tr><td colSpan="10" className="text-center text-muted py-4">No attendance records found for this employee and month.</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
                <p className="small text-muted mt-2 mb-0">
                  Break details use the existing daily attendance calculation. Only qualifying break intervals reported by that calculation are listed.
                </p>
              </section>
            )}

            {selectedUserId && (
              <AttendanceDayBreakdown userId={selectedUserId} title="Selected employee · working time and breaks" />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default UserAttendance;
