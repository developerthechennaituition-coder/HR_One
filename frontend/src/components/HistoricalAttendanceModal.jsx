import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { HRONE_API_BASE } from "../config/api.js";
import { buildChangeRows, formatAttendanceClock, formatIST } from "../services/attendanceHistoryFormat.js";

const API = `${HRONE_API_BASE}/api/attendance`;
const STATUSES = ["Present", "Half-Day Present", "Absent", "Holiday", "Leave", "Weekly Off", "Medical Leave", "Permission", "Not Applicable", "Not Marked"];
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
  return new Set([0, 1, 2, 3].map((offset) => {
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
const durationForSpan = (start, end) => {
  if (!start || !end) return "—";
  const toMinutes = (value) => {
    const [hours, minutes] = String(value).slice(0, 5).split(":").map(Number);
    return hours * 60 + minutes;
  };
  let minutes = toMinutes(end) - toMinutes(start);
  if (minutes < 0) minutes += 1440;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
};
const normaliseKey = (value) => String(value ?? "").trim();
const MAX_WEEKLY_OFFS = 4;
const weekdayName = (iso) => (/^\d{4}-\d{2}-\d{2}$/.test(iso || "")
  ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { weekday: "long" }) : "—");
const formatDateLabel = (iso) => (/^\d{4}-\d{2}-\d{2}$/.test(iso || "")
  ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "—");

export default function HistoricalAttendanceModal({ getHeaders, onClose, onWeeklyOffGenerated }) {
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
  const [weeklyOffSaving, setWeeklyOffSaving] = useState(false);
  const [weeklyOffChecking, setWeeklyOffChecking] = useState(false);
  const [weeklyOffPreview, setWeeklyOffPreview] = useState(null);
  const [weeklyOffModalOpen, setWeeklyOffModalOpen] = useState(false);
  const [weeklyOffSelected, setWeeklyOffSelected] = useState([]);
  const [weeklyOffError, setWeeklyOffError] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [editingDate, setEditingDate] = useState("");
  const [expandedDate, setExpandedDate] = useState("");
  const [historyDate, setHistoryDate] = useState("");
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [edit, setEdit] = useState({
    status: "Not Marked", check_in_time: "", check_out_time: "",
    first_half_status: "", second_half_status: "",
    first_half_check_in_time: "", first_half_check_out_time: "",
    second_half_check_in_time: "", second_half_check_out_time: "",
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
    setWeeklyOffPreview(null); setWeeklyOffModalOpen(false); setWeeklyOffSelected([]); setWeeklyOffError("");
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
    !row.payroll_locked && !payrollLocked && row.attendance_date <= todayISO
  );

  const beginEdit = (row) => {
    setExpandedDate(row.attendance_date);
    setEditingDate(row.attendance_date);
    setEdit({
      status: row.status || "Not Marked",
      check_in_time: timeValue(row.check_in_time),
      check_out_time: timeValue(row.check_out_time),
      first_half_status: row.first_half_status || (row.half_day_present_side === "first" ? "Present" : "Absent"),
      second_half_status: row.second_half_status || (row.half_day_present_side === "second" ? "Present" : "Absent"),
      first_half_check_in_time: timeValue(row.first_half_check_in_time || (row.half_day_present_side === "first" ? row.check_in_time : "")),
      first_half_check_out_time: timeValue(row.first_half_check_out_time || (row.half_day_present_side === "first" ? row.check_out_time : "")),
      second_half_check_in_time: timeValue(row.second_half_check_in_time || (row.half_day_present_side === "second" ? row.check_in_time : "")),
      second_half_check_out_time: timeValue(row.second_half_check_out_time || (row.half_day_present_side === "second" ? row.check_out_time : "")),
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
    if (edit.status === "Half-Day Present") {
      if (!edit.first_half_status || !edit.second_half_status) { setError("Select a status for both halves."); return; }
      if (edit.first_half_status !== "Present" && edit.second_half_status !== "Present") { setError("At least one half must be Present."); return; }
      for (const [label, halfStatus, start, end] of [
        ["First", edit.first_half_status, edit.first_half_check_in_time, edit.first_half_check_out_time],
        ["Second", edit.second_half_status, edit.second_half_check_in_time, edit.second_half_check_out_time],
      ]) {
        if (halfStatus === "Present" && (!start || !end)) { setError(`${label} Half requires check-in and check-out times.`); return; }
        if (halfStatus === "Present" && end <= start) { setError(`${label} Half check-out must be later than check-in.`); return; }
      }
      if (edit.first_half_status === "Present" && edit.second_half_status === "Present" &&
          edit.first_half_check_out_time > edit.second_half_check_in_time) {
        setError("First-half and second-half working intervals cannot overlap."); return;
      }
    }
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
          first_half_status: edit.status === "Half-Day Present" ? edit.first_half_status : null,
          second_half_status: edit.status === "Half-Day Present" ? edit.second_half_status : null,
          first_half_check_in_time: edit.status === "Half-Day Present" && edit.first_half_status === "Present" ? edit.first_half_check_in_time : null,
          first_half_check_out_time: edit.status === "Half-Day Present" && edit.first_half_status === "Present" ? edit.first_half_check_out_time : null,
          second_half_check_in_time: edit.status === "Half-Day Present" && edit.second_half_status === "Present" ? edit.second_half_check_in_time : null,
          second_half_check_out_time: edit.status === "Half-Day Present" && edit.second_half_status === "Present" ? edit.second_half_check_out_time : null,
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

  // Weekly Off generation works for every month listed for the employee (joining month .. current
  // month) and does not depend on company Weekly Off weekdays. The server decides what is eligible.
  const weeklyOffRequest = useRef(0);
  const fetchWeeklyOffPreview = useCallback(async (headers) => {
    const [year, month] = selectedMonth.split("-").map(Number);
    const response = await fetch(`${API}/historical/weekly-off-preview`, {
      method: "POST", headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: employeeId, year, month }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data?.detail || "Unable to analyze Weekly Off dates.");
    return data;
  }, [employeeId, selectedMonth]);

  useEffect(() => {
    const requestId = ++weeklyOffRequest.current;
    setWeeklyOffPreview(null);
    if (!employeeId || !selectedMonth || loading) return undefined;
    (async () => {
      try {
        const data = await fetchWeeklyOffPreview(getHeadersRef.current());
        if (requestId === weeklyOffRequest.current) setWeeklyOffPreview(data);
      } catch (err) {
        if (requestId === weeklyOffRequest.current) {
          setWeeklyOffPreview({ eligible_dates: [], suggested_dates: [], can_generate: false, reason: err.message || "Unable to check eligible dates." });
        }
      }
    })();
    return () => { weeklyOffRequest.current += 1; };
  }, [employeeId, selectedMonth, loading, records, fetchWeeklyOffPreview]);

  const refreshRecords = async () => {
    const selected = months.find((item) => item.value === selectedMonth);
    if (!selected) return;
    const requestId = ++recordsRequest.current;
    const refreshed = await fetch(`${API}/historical/user/${encodeURIComponent(employeeId)}?year=${selected.year}&month=${selected.month}`, { headers: getHeaders() });
    const refreshedData = await refreshed.json().catch(() => ({}));
    if (refreshed.ok && requestId === recordsRequest.current) setRecords(refreshedData.records || []);
  };

  const openWeeklyOffApproval = async () => {
    if (!employeeId || !selectedMonth || weeklyOffChecking || weeklyOffSaving) return;
    setWeeklyOffChecking(true); setWeeklyOffError(""); setError(""); setMessage("");
    try {
      const data = await fetchWeeklyOffPreview(getHeaders());
      setWeeklyOffPreview(data);
      if (!data.can_generate || !data.suggested_dates?.length) {
        setWeeklyOffError(data.reason || "No eligible Not Marked dates are available for Weekly Off.");
        return;
      }
      setWeeklyOffSelected(data.suggested_dates.slice(0, MAX_WEEKLY_OFFS));
      setWeeklyOffModalOpen(true);
    } catch (err) { setWeeklyOffError(err.message || "Unable to analyze Weekly Off dates."); }
    finally { setWeeklyOffChecking(false); }
  };

  const closeWeeklyOffModal = () => { if (!weeklyOffSaving) { setWeeklyOffModalOpen(false); setWeeklyOffSelected([]); } };

  const changeSuggestedDate = (slot, value) => setWeeklyOffSelected((old) => old.map((item, index) => (index === slot ? value : item)));

  const approveWeeklyOff = async () => {
    if (!weeklyOffSelected.length || !employeeId || weeklyOffSaving) return;
    if (new Set(weeklyOffSelected).size !== weeklyOffSelected.length || weeklyOffSelected.some((day) => !day)) {
      setWeeklyOffError("Choose a different date for each Weekly Off."); return;
    }
    const [year, month] = selectedMonth.split("-").map(Number);
    setWeeklyOffSaving(true); setWeeklyOffError(""); setError(""); setMessage("");
    try {
      const expected_updated_at = Object.fromEntries((weeklyOffPreview?.eligible_dates || [])
        .filter((item) => weeklyOffSelected.includes(item.attendance_date))
        .map((item) => [item.attendance_date, item.expected_updated_at ?? null]));
      const response = await fetch(`${API}/historical/weekly-off-approve`, {
        method: "POST", headers: { ...getHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: employeeId, year, month, attendance_dates: [...weeklyOffSelected].sort(), expected_updated_at }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || "Unable to save Weekly Off dates.");
      setWeeklyOffModalOpen(false); setWeeklyOffSelected([]);
      setMessage(data.message || `Generated ${data.total_generated || 0} Weekly Off date(s).`);
      if (data.skipped_dates?.length) setWeeklyOffError(data.skipped_dates.map((item) => `${item.attendance_date}: ${item.reason}`).join(" "));
      await refreshRecords();
      if (typeof onWeeklyOffGenerated === "function") onWeeklyOffGenerated(data);
    } catch (err) {
      // Keep the dialog open so the HR Admin can adjust; the server re-validates everything.
      setWeeklyOffError(err.message || "Unable to save Weekly Off dates.");
    } finally { setWeeklyOffSaving(false); }
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
        {label}: {formatAttendanceClock(interval.from_time)} – {formatAttendanceClock(interval.to_time)} ({interval.duration || "duration unavailable"})
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
              <small className="text-muted">History is available from the employee joining month through the current month. Previous dates in the current month and previous three completed months can be modified, subject to payroll approval locks. Today and future dates are view-only.</small>
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
                              <button type="button" className="btn btn-sm btn-outline-primary me-1" disabled={!rowCanEdit} title={!rowCanEdit ? (row.is_before_joining ? "Before employee joining date" : payrollLocked || row.payroll_locked ? "Payroll is approved/processed/paid; view only." : "Current date and eligible previous dates in the current month and previous three completed months can be modified.") : "Modify attendance"} onClick={() => beginEdit(row)}><i className="bi bi-pencil-square me-1" />Edit</button>
                              <button type="button" className="btn btn-sm btn-outline-dark" onClick={() => loadHistory(row.attendance_date)} title="View modification history"><i className="bi bi-clock-history me-1" />History</button>
                            </td>
                          </tr>
                          {expandedDate === row.attendance_date && (
                            <tr key={`${row.attendance_date}-details`} className="table-light">
                              <td colSpan="9">
                                <div className="row g-3 p-2">
                                  <div className="col-6 col-lg-3"><strong>Working duration</strong><div>{row.working_duration || "0h 00m"}</div></div>
                                  <div className="col-6 col-lg-3"><strong>Break duration</strong><div>{row.break_duration || "0h 00m"}</div></div>
                                  <div className="col-6 col-lg-3"><strong>Total duration</strong><div>{row.total_duration || "0h 00m"}</div></div>
                                  <div className="col-6 col-lg-3"><strong>Scheduled time</strong><div>{row.scheduled_work_start_time || "—"} – {row.scheduled_work_end_time || "—"}</div></div>
                                  <div className="col-6 col-lg-3"><strong>Half-day threshold</strong><div>{row.half_day_working_duration_text || "—"}{row.half_day_threshold_met ? " · Reached" : ""}</div></div>
                                  {(row.first_half_status || row.second_half_status) && (
                                    <div className="col-12 col-lg-6"><strong>Half statuses</strong><div>First: {row.first_half_status || "—"} · Second: {row.second_half_status || "—"}</div></div>
                                  )}
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
                                    {edit.status === "Present" && <>
                                      <div className="col-6 col-md-2"><label className="form-label">Check-in</label><input type="time" className="form-control" value={edit.check_in_time} onChange={(e) => setEdit((old) => ({ ...old, check_in_time: e.target.value }))} /></div>
                                      <div className="col-6 col-md-2"><label className="form-label">Check-out</label><input type="time" className="form-control" value={edit.check_out_time} onChange={(e) => setEdit((old) => ({ ...old, check_out_time: e.target.value }))} /></div>
                                      <div className="col-12"><div className="small rounded bg-light px-3 py-2"><strong>Total Duration:</strong> {durationForSpan(edit.check_in_time, edit.check_out_time)} <span className="text-muted">(Check-out − Check-in)</span></div></div>
                                    </>}
                                    {edit.status === "Half-Day Present" && [["first", "First Half"], ["second", "Second Half"]].map(([side, label]) => {
                                      const statusKey = `${side}_half_status`;
                                      const inKey = `${side}_half_check_in_time`;
                                      const outKey = `${side}_half_check_out_time`;
                                      return (
                                        <fieldset className="col-12 col-md-6 border rounded p-3" key={side}>
                                          <legend className="float-none w-auto px-2 fs-6 fw-semibold">{label}</legend>
                                          <label className="form-label">Status
                                            <select className="form-select" value={edit[statusKey]} onChange={(e) => setEdit((old) => ({ ...old, [statusKey]: e.target.value }))}>
                                              {["Present", "Absent", "Leave", "Medical Leave", "Holiday", "Weekly Off"].map((value) => <option key={value}>{value}</option>)}
                                            </select>
                                          </label>
                                          {edit[statusKey] === "Present" && (
                                            <div className="row g-2">
                                              <div className="col-6"><label className="form-label">Check-in<input type="time" className="form-control" value={edit[inKey]} onChange={(e) => setEdit((old) => ({ ...old, [inKey]: e.target.value }))} /></label></div>
                                              <div className="col-6"><label className="form-label">Check-out<input type="time" className="form-control" value={edit[outKey]} onChange={(e) => setEdit((old) => ({ ...old, [outKey]: e.target.value }))} /></label></div>
                                            </div>
                                          )}
                                        </fieldset>
                                      );
                                    })}
                                    {edit.status === "Half-Day Present" && (() => {
                                       const present = [[edit.first_half_status, edit.first_half_check_in_time, edit.first_half_check_out_time], [edit.second_half_status, edit.second_half_check_in_time, edit.second_half_check_out_time]].filter(([status]) => status === "Present");
                                       const starts = present.map(([, start]) => start).filter(Boolean).sort();
                                       const ends = present.map(([, , end]) => end).filter(Boolean).sort();
                                       return <div className="col-12"><div className="small rounded bg-light px-3 py-2"><strong>Total Duration:</strong> {starts.length && ends.length ? durationForSpan(starts[0], ends[ends.length - 1]) : "—"} <span className="text-muted">(Check-out − Check-in)</span></div></div>;
                                     })()}
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

            {employeeId && selectedMonth && !loading && (() => {
              const preview = weeklyOffPreview;
              const locked = payrollLocked || Boolean(preview?.payroll_locked);
              const limit = preview?.monthly_limit ?? MAX_WEEKLY_OFFS;
              const info = locked ? `Payroll ${preview?.payroll_status || payrollStatus || "approved"} is finalized; Weekly Off generation is protected.`
                : !preview ? "Checking Not Marked dates…"
                : preview.limit_reached ? `All ${limit} Weekly Off days already exist for this month.`
                : preview.can_generate ? `${preview.existing_weekly_offs ?? 0} of ${limit} Weekly Off recorded · ${preview.remaining_limit} can be generated from ${preview.total_eligible} Not Marked date(s).`
                : (preview.reason || "No Not Marked dates are available for Weekly Off.");
              const disabled = weeklyOffChecking || weeklyOffSaving || locked || !preview?.can_generate;
              return (
                <div className="d-flex flex-column flex-sm-row align-items-sm-center justify-content-between gap-2 mt-3">
                  <div className="small text-muted">{info}</div>
                  <button type="button" className="btn btn-outline-success flex-shrink-0" disabled={disabled}
                    onClick={openWeeklyOffApproval} title={disabled ? info : "Suggest Weekly Off dates from this employee's Not Marked days"}>
                    {weeklyOffChecking ? <><span className="spinner-border spinner-border-sm me-2" />Analyzing…</> : <><i className="bi bi-calendar-check me-2" />Generate Weekly Off</>}
                  </button>
                </div>
              );
            })()}
            {weeklyOffError && !weeklyOffModalOpen && <div className="alert alert-warning py-2 mt-2 mb-0">{weeklyOffError}</div>}

            {weeklyOffModalOpen && weeklyOffPreview && (
              <div className="modal d-block" role="dialog" aria-modal="true" aria-labelledby="weekly-off-approval-title" style={{ background: "rgba(0,0,0,.45)", zIndex: 1085 }}>
                <div className="modal-dialog modal-dialog-centered" style={{ maxWidth: 520 }}>
                  <div className="modal-content">
                    <div className="modal-header py-2">
                      <h5 className="modal-title fs-6" id="weekly-off-approval-title">Generate Weekly Off</h5>
                      <button type="button" className="btn-close" onClick={closeWeeklyOffModal} disabled={weeklyOffSaving} aria-label="Close" />
                    </div>
                    <div className="modal-body">
                      <div className="small text-muted mb-2">
                        {selectedEmployee?.name || "—"} ({selectedEmployee?.employee_id || "—"}) · {months.find((item) => item.value === selectedMonth)?.label || selectedMonth}
                      </div>
                      {weeklyOffPreview.suggested_weekdays?.length > 0 && (
                        <div className="small mb-2">Most common Not Marked weekdays: <strong>{weeklyOffPreview.suggested_weekdays.slice(0, 3).join(", ")}</strong>. Review the suggested dates or pick another Not Marked date.</div>
                      )}
                      {weeklyOffError && <div className="alert alert-danger py-2 small">{weeklyOffError}</div>}
                      <div className="table-responsive">
                      <table className="table table-sm align-middle mb-2">
                        <thead className="table-light"><tr><th style={{ width: 36 }}>#</th><th>Weekly Off date</th><th>Day</th></tr></thead>
                        <tbody>
                          {weeklyOffSelected.map((value, slot) => (
                            <tr key={slot}>
                              <td>{slot + 1}</td>
                              <td>
                                <select className="form-select form-select-sm" value={value} disabled={weeklyOffSaving} aria-label={`Weekly Off date ${slot + 1}`}
                                  onChange={(event) => changeSuggestedDate(slot, event.target.value)}>
                                  {weeklyOffPreview.eligible_dates
                                    .filter((item) => item.attendance_date === value || !weeklyOffSelected.includes(item.attendance_date))
                                    .map((item) => <option key={item.attendance_date} value={item.attendance_date}>{formatDateLabel(item.attendance_date)}</option>)}
                                </select>
                              </td>
                              <td className="text-nowrap">{weekdayName(value)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      </div>
                      <div className="small text-muted">
                        Existing Weekly Off: {weeklyOffPreview.existing_weekly_offs ?? 0} · Monthly limit: {weeklyOffPreview.monthly_limit ?? MAX_WEEKLY_OFFS}. Only Not Marked dates are listed; Present, Absent, Leave, Holiday and existing Weekly Off records are never changed. Nothing is saved until you press Submit.
                      </div>
                    </div>
                    <div className="modal-footer py-2">
                      <button type="button" className="btn btn-secondary" disabled={weeklyOffSaving} onClick={closeWeeklyOffModal}>Cancel</button>
                      <button type="button" className="btn btn-success" disabled={weeklyOffSaving || !weeklyOffSelected.length} onClick={approveWeeklyOff}>
                        {weeklyOffSaving ? <><span className="spinner-border spinner-border-sm me-2" />Saving…</> : "Submit"}
                      </button>
                    </div>
                  </div>
                </div>
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
