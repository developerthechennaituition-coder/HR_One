import { HRONE_API_BASE } from "../config/api.js";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AttendanceDayBreakdown from "../components/AttendanceDayBreakdown";
import MyAttendanceCalendar from "../components/MyAttendanceCalendar";
import { getEmployeeAttendanceCalendar } from "../services/attendanceRequestApi";
import {
  MONTH_NAMES, buildCalendarRange, clampMonth, companyToday, isMonthInRange, isSelectableDate, minSelectableDate, monthOptions, yearOptions,
} from "../services/attendanceCalendarRange";
import { roleLabel } from "../services/roleLabels";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || HRONE_API_BASE;

// Summary card colours (same as Profile -> Attendance).
const STATUS_COLORS = {
  present: { background: "#198754", label: "Present" },
  absent: { background: "#dc3545", label: "Absent" },
  leave: { background: "#6f42c1", label: "Leave" },
  holiday: { background: "#ADD8E6", label: "Holiday" },
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
  const todayIso = useMemo(companyToday, []);
  const today = { year: Number(todayIso.slice(0, 4)), month: Number(todayIso.slice(5, 7)) };

  const [employees, setEmployees] = useState([]);
  const [employeesLoading, setEmployeesLoading] = useState(true);
  const [departments, setDepartments] = useState([]);
  const [departmentsLoading, setDepartmentsLoading] = useState(true);
  const [departmentsError, setDepartmentsError] = useState("");
  const [departmentId, setDepartmentId] = useState("all");
  const employeesRequest = useRef(0);
  const calendarRequest = useRef(0);
  const monthlyRequest = useRef(0);
  const [search, setSearch] = useState("");
  const [selectedUserId, setSelectedUserId] = useState("");
  const [year, setYear] = useState(today.year);
  const [month, setMonth] = useState(today.month);
  // Same Monthly Attendance Calendar data as My Attendance, for the chosen employee.
  const [calendar, setCalendar] = useState(null);
  const [joiningDate, setJoiningDate] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedDate, setSelectedDate] = useState(todayIso);
  const [monthlyDetails, setMonthlyDetails] = useState(null);
  const [monthlyDetailsLoading, setMonthlyDetailsLoading] = useState(false);
  const [monthlyDetailsError, setMonthlyDetailsError] = useState("");
  const [exportLoading, setExportLoading] = useState(false);

  const headers = useMemo(() => ({ Authorization: `Bearer ${token}` }), [token]);

  // ---- departments (Department master + departments found on employee records) ----
  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/api/attendance/departments`, { headers, signal: controller.signal });
        if (!response.ok) throw new Error(await readError(response, "Unable to load departments."));
        const data = await response.json();
        setDepartments(Array.isArray(data) ? data : []);
      } catch (e) {
        if (e.name !== "AbortError") setDepartmentsError(e.message || "Unable to load departments.");
      } finally {
        if (!controller.signal.aborted) setDepartmentsLoading(false);
      }
    })();
    return () => controller.abort();
  }, [headers]);

  // ---- employees of the selected department ---------------------------------
  // The server resolves each employee's department id, so this filters on the
  // real department relationship. Only the latest response is applied.
  useEffect(() => {
    const requestId = ++employeesRequest.current;
    const controller = new AbortController();
    setEmployees([]);
    setEmployeesLoading(true);
    setSelectedUserId("");
    (async () => {
      try {
        const response = await fetch(
          `${API_BASE_URL}/api/attendance/department-employees?department_id=${encodeURIComponent(departmentId)}`,
          { headers, signal: controller.signal },
        );
        if (!response.ok) throw new Error(await readError(response, "Unable to load employees."));
        const data = await response.json();
        if (requestId !== employeesRequest.current) return;
        const unique = new Map();
        (Array.isArray(data) ? data : []).forEach((item) => {
          if (item?.user_id && item.employee_id && !unique.has(item.user_id)) unique.set(item.user_id, item);
        });
        setEmployees([...unique.values()]);
        setError("");
      } catch (e) {
        if (e.name === "AbortError" || requestId !== employeesRequest.current) return;
        setError(e.message || "Unable to load employees.");
      } finally {
        if (requestId === employeesRequest.current) setEmployeesLoading(false);
      }
    })();
    return () => controller.abort();
  }, [headers, departmentId]);

  const filteredEmployees = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return employees;
    return employees.filter((e) =>
      `${e.name} ${e.employee_id} ${e.email}`.toLowerCase().includes(needle),
    );
  }, [employees, search]);

  const selectedEmployee = employees.find((e) => e.user_id === selectedUserId) || null;

  // ---- calendar of the chosen employee ------------------------------------
  // Joining date of the selected employee through today + 89 days.
  const calendarRange = useMemo(() => buildCalendarRange(joiningDate, todayIso), [joiningDate, todayIso]);
  const reportAvailable = year < today.year || (year === today.year && month <= today.month);

  const loadAttendance = useCallback(async () => {
    if (!selectedUserId) {
      calendarRequest.current += 1;
      setCalendar(null);
      setLoading(false);
      return;
    }
    const requestId = ++calendarRequest.current;
    setLoading(true);
    setError("");
    try {
      const data = await getEmployeeAttendanceCalendar(selectedUserId, year, month);
      if (requestId !== calendarRequest.current) return;
      setCalendar(data);
      setJoiningDate(data?.joining_date || null);
    } catch (e) {
      if (requestId !== calendarRequest.current) return;
      setCalendar(null);
      setError(e.message || "Unable to load attendance.");
    } finally {
      if (requestId === calendarRequest.current) setLoading(false);
    }
  }, [selectedUserId, year, month]);

  // A different employee has a different joining date.
  useEffect(() => {
    setJoiningDate(null);
    setSelectedDate(todayIso);
  }, [selectedUserId, todayIso]);

  // Keep the month and selected date inside the employee's calendar range.
  useEffect(() => {
    if (!isMonthInRange(calendarRange, year, month)) {
      const target = clampMonth(calendarRange, year, month);
      setYear(target.year);
      setMonth(target.month);
    }
    setSelectedDate((current) => (isSelectableDate(calendarRange, current) ? current : todayIso));
  }, [calendarRange, year, month, todayIso]);

  const changeMonth = (nextYear, nextMonth) => {
    const target = clampMonth(calendarRange, nextYear, nextMonth);
    setYear(target.year);
    setMonth(target.month);
  };

  // Past and today's dates only; upcoming dates open details inside the calendar.
  const selectDate = (iso) => {
    if (!isSelectableDate(calendarRange, iso)) return;
    setSelectedDate(iso);
    const [selectedYear, selectedMonth] = iso.split("-").map(Number);
    if (selectedYear !== year || selectedMonth !== month) changeMonth(selectedYear, selectedMonth);
  };

  const loadMonthlyDetails = useCallback(async () => {
    if (!selectedUserId) {
      setMonthlyDetails(null);
      setMonthlyDetailsError("Select an employee first.");
      return;
    }
    const requestId = ++monthlyRequest.current;
    setMonthlyDetailsLoading(true);
    setMonthlyDetailsError("");
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/attendance/user/${encodeURIComponent(selectedUserId)}/monthly-details?year=${year}&month=${month}`,
        { headers },
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.detail || "Unable to load monthly attendance details.");
      if (requestId !== monthlyRequest.current) return;
      setMonthlyDetails(body);
    } catch (err) {
      if (requestId !== monthlyRequest.current) return;
      setMonthlyDetails(null);
      setMonthlyDetailsError(err?.message || "Unable to load monthly attendance details.");
    } finally {
      if (requestId === monthlyRequest.current) setMonthlyDetailsLoading(false);
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
  useEffect(() => {
    monthlyRequest.current += 1;
    setMonthlyDetails(null); setMonthlyDetailsError(""); setMonthlyDetailsLoading(false);
  }, [selectedUserId, year, month]);

  // Recorded attendance only (as before); upcoming holidays/leave are not counted.
  const counts = useMemo(() => {
    const result = { present: 0, absent: 0, leave: 0, holiday: 0 };
    (calendar?.days || []).forEach((day) => {
      const key = String(day.status || "").toLowerCase();
      if (day.has_record && key in result) result[key] += 1;
    });
    return result;
  }, [calendar]);
  const marked = counts.present + counts.absent + counts.leave;
  const percent = (value) => (marked ? ((value / marked) * 100).toFixed(1) : "0.0");

  const selectedCalendarDay = calendar?.days?.find((day) => day.attendance_date === selectedDate) || null;

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
              <label className="form-label fw-semibold" htmlFor="ua-department">Department</label>
              <select
                id="ua-department"
                className="form-select"
                value={departmentId}
                disabled={departmentsLoading}
                onChange={(e) => setDepartmentId(e.target.value)}
              >
                <option value="all">{departmentsLoading ? "Loading departments..." : "All Departments"}</option>
                {departments.map((item) => (
                  <option key={item.id} value={item.id}>{item.department_name || item.name}</option>
                ))}
              </select>
              {departmentsError && <div className="small text-danger mt-1">{departmentsError}</div>}
              {!departmentsLoading && !departmentsError && departments.length === 0 && <div className="small text-muted mt-1">No departments found.</div>}
            </div>
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
            <div className="col-lg-3 col-md-6">
              <label className="form-label fw-semibold" htmlFor="ua-employee">Employee</label>
              <select
                id="ua-employee"
                className="form-select"
                value={selectedUserId}
                disabled={employeesLoading}
                onChange={(e) => setSelectedUserId(e.target.value)}
              >
                <option value="">{employeesLoading ? "Loading employees..." : filteredEmployees.length ? "Select an employee" : "No employees found"}</option>
                {filteredEmployees.map((e) => (
                  <option key={e.user_id} value={e.user_id}>
                    {e.name} ({e.employee_id})
                  </option>
                ))}
              </select>
            </div>
            <div className="col-lg-2 col-md-6">
              <label className="form-label fw-semibold" htmlFor="ua-month">Month</label>
              <select id="ua-month" className="form-select" value={month} onChange={(e) => changeMonth(year, Number(e.target.value))}>
                {monthOptions(calendarRange, year).map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </select>
            </div>
            <div className="col-lg-1 col-md-6">
              <label className="form-label fw-semibold" htmlFor="ua-year">Year</label>
              <select
                id="ua-year"
                className="form-select"
                value={year}
                onChange={(e) => changeMonth(Number(e.target.value), month)}
              >
                {yearOptions(calendarRange).map((y) => <option key={y} value={y}>{y}</option>)}
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
                  {selectedEmployee.employee_id} · {roleLabel(selectedEmployee.role)}
                </p>
              </div>
              <div className="d-flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={loadMonthlyDetails}
                  disabled={loading || monthlyDetailsLoading || !reportAvailable}
                  title={reportAvailable ? undefined : "Available for the current and past months"}
                >
                  <i className="bi bi-eye me-1"></i>
                  {monthlyDetailsLoading ? "Loading..." : "View Attendance"}
                </button>
                <button
                  type="button"
                  className="btn btn-success btn-sm"
                  onClick={downloadMonthlyExcel}
                  disabled={loading || exportLoading || !reportAvailable}
                  title={reportAvailable ? undefined : "Available for the current and past months"}
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

            <MyAttendanceCalendar
              year={year}
              month={month}
              range={calendarRange}
              calendar={calendar}
              loading={loading}
              selectedDate={selectedDate}
              onSelectDate={selectDate}
              onMonthChange={changeMonth}
            />
            {monthlyDetailsError && <div className="alert alert-danger mt-3 mb-0" role="alert">{monthlyDetailsError}</div>}

            {monthlyDetails && (() => {
              const days = monthlyDetails.days || [];
              const summary = monthlyDetails.summary || {};
              const pendingRequests = days.flatMap((day) => day.pending_requests || []);
              const badgeClass = (status) => {
                const key = String(status || "").toLowerCase();
                if (key === "present") return "text-bg-success";
                if (key === "absent") return "text-bg-danger";
                if (["leave", "medical leave"].includes(key)) return "text-bg-primary";
                if (["holiday", "permission"].includes(key)) return "text-bg-warning";
                if (key === "not marked") return "text-bg-light border";
                return "text-bg-secondary";
              };
              return (
              <section className="mt-4">
                <div className="d-flex flex-wrap justify-content-between align-items-end gap-2 mb-3">
                  <div>
                    <h5 className="mb-1">Monthly Attendance Details</h5>
                    <div className="text-muted small">
                      {MONTH_NAMES[monthlyDetails.month - 1]} {monthlyDetails.year} · {days.length} day(s) · {monthlyDetails.records.length} with attendance records
                    </div>
                  </div>
                  <div className="d-flex flex-wrap gap-3 small">
                    <span><strong>Employee:</strong> {monthlyDetails.employee?.name || selectedEmployee.name}</span>
                    <span><strong>ID:</strong> {monthlyDetails.employee?.employee_id || selectedEmployee.employee_id}</span>
                  </div>
                </div>
                <div className="row g-2 mb-3 small">
                  {[
                    ["Monthly Permission Count", summary.permission_count ?? 0],
                    ["Monthly Permission Total Duration", summary.permission_total_text || "00 Hours 00 Minutes"],
                    ["Medical Leave (ML) Days", summary.medical_leave_days ?? 0],
                    ["Normal Leave Days", summary.normal_leave_days ?? 0],
                    ["Pending Requests", summary.pending_request_count ?? 0],
                  ].map(([label, value]) => (
                    <div className="col-6 col-lg" key={label}>
                      <div className="border rounded p-2 h-100"><div className="text-muted">{label}</div><div className="fw-semibold">{value}</div></div>
                    </div>
                  ))}
                </div>
                <div className="table-responsive border rounded monthly-attendance-scroll" style={{ maxHeight: "480px", overflowY: "auto", overflowX: "auto" }}>
                  <table className="table table-hover table-striped align-middle mb-0">
                    <thead className="table-light">
                      <tr>
                        <th>Date</th>
                        <th>Day</th>
                        <th>Status</th>
                        <th>Check-In</th>
                        <th>Check-Out</th>
                        <th>Attendance Category</th>
                        <th>Source</th>
                        <th>Permission</th>
                        <th>Leave Details</th>
                        <th>Break Duration</th>
                        <th>Break Time List</th>
                        <th>Net Working Time</th>
                        <th>Request Status</th>
                        <th>Remarks</th>
                      </tr>
                    </thead>
                    <tbody>
                      {days.map((record) => (
                        <tr key={record.attendance_date}>
                          <td className="text-nowrap">
                            {new Date(`${record.attendance_date}T00:00:00`).toLocaleDateString("en-IN", {
                              timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric",
                            })}
                          </td>
                          <td>{record.day}</td>
                          <td>
                            <span className={`badge ${badgeClass(record.status)}`}>{record.status || "Not Marked"}</span>
                            {record.hr_admin_edited && <div className="mt-1"><span className="badge text-bg-info">HR Admin Edited</span></div>}
                            {record.holiday_name && <div className="small text-muted">{record.holiday_name}</div>}
                          </td>
                          <td className="text-nowrap">{record.check_in_text}</td>
                          <td className="text-nowrap">{record.check_out_text}</td>
                          <td>{record.attendance_category}</td>
                          <td>{record.attendance_source_label || record.attendance_source || "—"}</td>
                          <td style={{ minWidth: 200 }}>
                            {record.permissions?.length ? (
                              <>
                                {record.permissions.map((item, index) => (
                                  <div key={`${item.start_time}-${index}`} className="small mb-1">
                                    <span className="fw-semibold">{item.start_text} – {item.end_text}</span>
                                    <span className="text-muted"> · {item.duration_text}</span>
                                    <span className="badge text-bg-success ms-1">{item.status}</span>
                                    {item.reason && <div className="text-muted">{item.reason}</div>}
                                  </div>
                                ))}
                                {record.permissions.length > 1 && <div className="small text-muted">Total: {record.permission_total_text}</div>}
                              </>
                            ) : "—"}
                          </td>
                          <td style={{ minWidth: 160 }}>
                            {record.leave ? (
                              <>
                                <span className={`badge ${record.leave.is_medical ? "text-bg-danger" : "text-bg-primary"}`}>{record.leave.leave_type}</span>
                                <div className="small text-muted">{record.leave.day_part_label}</div>
                              </>
                            ) : "—"}
                          </td>
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
                          <td>
                            {record.request_status
                              ? <span className={`badge ${["Pending", "Under Review"].includes(record.request_status) ? "text-bg-warning" : record.request_status === "Approved" ? "text-bg-success" : "text-bg-secondary"}`}>{record.request_status}</span>
                              : "—"}
                          </td>
                          <td style={{ minWidth: 220, whiteSpace: "pre-wrap" }}>{record.remarks || "—"}</td>
                        </tr>
                      ))}
                      {!days.length && (
                        <tr><td colSpan="14" className="text-center text-muted py-4">No dates found for this employee and month.</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
                <p className="small text-muted mt-2 mb-3">
                  Every date of the month is listed. Break details use the existing daily attendance calculation. Pending requests are shown separately and are not treated as approved attendance.
                </p>

                <h6 className="mb-2">Pending Requests</h6>
                {pendingRequests.length === 0 ? (
                  <div className="text-muted small">No pending attendance or permission requests for this month.</div>
                ) : (
                  <div className="table-responsive border rounded">
                    <table className="table table-sm align-middle mb-0">
                      <thead className="table-light">
                        <tr>
                          <th>Request Date</th><th>Attendance Date</th><th>Request Type</th><th>Requested Status</th>
                          <th>Check-In / Check-Out</th><th>Permission</th><th>Reason</th><th>Status</th><th>Pending Approver</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pendingRequests.map((item, index) => (
                          <tr key={`${item.request_type}-${item.request_number}-${item.attendance_date}-${item.permission_start}-${index}`}>
                            <td className="text-nowrap">{item.request_date}</td>
                            <td className="text-nowrap">{item.attendance_date}</td>
                            <td>{item.request_type}{item.request_number ? <div className="small text-muted">{item.request_number}</div> : null}</td>
                            <td>{item.requested_status || "—"}</td>
                            <td className="text-nowrap">{item.requested_check_in || item.requested_check_out ? `${item.requested_check_in || "—"} – ${item.requested_check_out || "—"}` : "—"}</td>
                            <td className="text-nowrap">{item.permission_start || item.permission_end ? `${item.permission_start || "—"} – ${item.permission_end || "—"}` : "—"}</td>
                            <td style={{ minWidth: 160 }}>{item.reason || "—"}</td>
                            <td><span className="badge text-bg-warning">{item.status}</span></td>
                            <td>{item.approval_stage}{item.approver ? <div className="small text-muted">{item.approver}</div> : null}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
              );
            })()}

            {selectedUserId && (
              <AttendanceDayBreakdown
                userId={selectedUserId}
                title="Selected employee · working time and breaks"
                date={selectedDate}
                minDate={minSelectableDate(calendarRange)}
                onDateChange={selectDate}
                showStatus
                calendarDay={selectedCalendarDay}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default UserAttendance;
