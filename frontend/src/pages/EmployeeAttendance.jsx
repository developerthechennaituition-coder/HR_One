import { HRONE_API_BASE } from "../config/api.js";
import { useCallback, useEffect, useMemo, useState } from "react";
import AttendanceDayBreakdown from "../components/AttendanceDayBreakdown";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || HRONE_API_BASE;

const companyDateParts = (date = new Date()) => {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  return Object.fromEntries(parts.map(({ type, value }) => [type, value]));
};

const formatTime = (time) => {
  if (!time) return "Not Marked";
  const [hourText, minute] = time.split(":");
  const hour = Number(hourText);
  return `${hour % 12 || 12}:${minute} ${hour >= 12 ? "PM" : "AM"}`;
};

const getSource = (record) => {
  if (record?.attendance_source === "ESSL") return "ESSL";
  if (record?.attendance_source === "MANUAL") return "Manual";
  if (record?.attendance_source === "WFH_FACE_GPS") {
    return "WFH · Face + Liveness + GPS";
  }
  return record?.attendance_source || "—";
};

const getAttendanceType = (record) => {
  if (record?.attendance_type === "WFH" || record?.attendance_source === "WFH_FACE_GPS") {
    return "Work From Home";
  }
  if (record?.attendance_type === "OFFICE" || record?.attendance_source) {
    return "Work From Office";
  }
  return "Not Marked";
};

function EmployeeAttendance({ session, onNavigate }) {
  const token = session?.token;
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [todayRemark, setTodayRemark] = useState("");
  const [remarkDraft, setRemarkDraft] = useState("");
  const [remarkOpen, setRemarkOpen] = useState(false);
  const [remarkSaving, setRemarkSaving] = useState(false);
  const [remarkMessage, setRemarkMessage] = useState("");
  const [remarkError, setRemarkError] = useState("");

  const today = useMemo(() => companyDateParts(), []);
  const month = Number(today.month);
  const year = Number(today.year);
  const todayDate = `${today.year}-${today.month}-${today.day}`;

  const loadAttendance = useCallback(async () => {
    try {
      if (!token) throw new Error("Login session not found. Please login again.");
      const response = await fetch(
        `${API_BASE_URL}/api/attendance/my?year=${year}&month=${month}`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data?.detail || "Unable to load your attendance.");
      }
      setError("");
      setRecords(data);
    } catch (loadError) {
      setError(loadError.message || "Unable to load your attendance.");
    } finally {
      setLoading(false);
    }
  }, [month, token, year]);

  useEffect(() => {
    loadAttendance();
  }, [loadAttendance]);

  useEffect(() => {
    if (!token) return;
    fetch(`${API_BASE_URL}/api/attendance/my/remarks`, {
      headers: { Authorization: `Bearer ${token}` },
    }).then(async (response) => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || "Unable to load today's remark.");
      setTodayRemark(data.remark || "");
      setRemarkDraft(data.remark || "");
    }).catch((err) => setRemarkError(err.message || "Unable to load today's remark."));
  }, [token]);

  const saveTodayRemark = async () => {
    const value = remarkDraft.trim();
    if (!value) {
      setRemarkError("Please enter a remark before saving.");
      return;
    }
    setRemarkSaving(true);
    setRemarkError("");
    setRemarkMessage("");
    try {
      const response = await fetch(`${API_BASE_URL}/api/attendance/my/remarks`, {
        method: "PUT",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ remark: value }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || "Unable to save today's remark.");
      setTodayRemark(data.remark || value);
      setRemarkDraft(data.remark || value);
      setRemarkOpen(false);
      setRemarkMessage("Today's attendance remark saved successfully.");
      await loadAttendance();
    } catch (err) {
      setRemarkError(err.message || "Unable to save today's remark.");
    } finally {
      setRemarkSaving(false);
    }
  };

  const todaysRecord = records.find(
    (record) => record.attendance_date === todayDate,
  );

  if (loading) {
    return (
      <div className="container-fluid py-5 text-center">
        <div className="spinner-border text-primary" role="status"></div>
        <p className="text-muted mt-3 mb-0">Loading your attendance...</p>
      </div>
    );
  }

  return (
    <div className="container-fluid">
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <h2 className="mb-1">My Attendance</h2>
          <p className="text-muted mb-0">Your WFO and WFH attendance in one place</p>
        </div>
        <div className="d-flex gap-2">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => onNavigate?.("wfh-attendance")}
          >
            WFH Attendance
          </button>
          <button
            type="button"
            className="btn btn-outline-secondary"
            onClick={loadAttendance}
          >
            Refresh
          </button>
        </div>
      </div>

      {error && <div className="alert alert-danger" role="alert">{error}</div>}

      <div className="card border-0 shadow-sm mb-4">
        <div className="card-body">
          <div className="d-flex flex-wrap justify-content-between align-items-center gap-2">
            <h4 className="mb-0">Today's Attendance</h4>
            <button type="button" className="btn btn-outline-primary d-inline-flex align-items-center gap-2"
              onClick={() => { setRemarkDraft(todayRemark); setRemarkOpen((open) => !open); setRemarkError(""); setRemarkMessage(""); }}>
              <i className="bi bi-chat-left-text" aria-hidden="true" />
              {todayRemark ? "Edit Remark" : "Add Remark"}
            </button>
          </div>
          <div className="row g-3 mt-1">
            <div className="col-md-3">
              <small className="text-muted">Attendance Type</small>
              <div className="fw-semibold">{getAttendanceType(todaysRecord)}</div>
            </div>
            <div className="col-md-3">
              <small className="text-muted">Status</small>
              <div className="fw-semibold">{todaysRecord?.status || "Not Marked"}</div>
            </div>
            <div className="col-md-3">
              <small className="text-muted">Check-In</small>
              <div className="fw-semibold">{formatTime(todaysRecord?.check_in_time)}</div>
            </div>
            <div className="col-md-3">
              <small className="text-muted">Check-Out</small>
              <div className="fw-semibold">{formatTime(todaysRecord?.check_out_time)}</div>
            </div>
            <div className="col-md-3">
              <small className="text-muted">Source</small>
              <div className="fw-semibold">{todaysRecord ? getSource(todaysRecord) : "—"}</div>
            </div>
          </div>
          {!todaysRecord && (
            <p className="small text-muted mt-3 mb-0">
              No attendance has been recorded for today. This does not mean you are absent.
            </p>
          )}
          {todayRemark && !remarkOpen && <div className="alert alert-light border mt-3 mb-0"><strong>Today's remark:</strong> {todayRemark}</div>}
          {remarkMessage && <div className="alert alert-success mt-3 mb-0" role="status">{remarkMessage}</div>}
          {remarkError && <div className="alert alert-danger mt-3 mb-0" role="alert">{remarkError}</div>}
          {remarkOpen && <div className="border rounded-3 p-3 mt-3 bg-light">
            <label htmlFor="today-attendance-remark" className="form-label fw-semibold">Remark for today's attendance</label>
            <textarea id="today-attendance-remark" className="form-control" rows={3} maxLength={1000}
              placeholder="Example: I worked overtime today from 6:00 PM to 8:00 PM."
              value={remarkDraft} onChange={(event) => setRemarkDraft(event.target.value)} />
            <div className="d-flex justify-content-between align-items-center mt-2 gap-2 flex-wrap">
              <small className="text-muted">Maximum 1,000 characters. This remark will appear in Monthly Attendance Details and the Excel report.</small>
              <div className="d-flex gap-2">
                <button type="button" className="btn btn-light border" onClick={() => { setRemarkOpen(false); setRemarkDraft(todayRemark); }}>Cancel</button>
                <button type="button" className="btn btn-primary" disabled={remarkSaving || !remarkDraft.trim()} onClick={saveTodayRemark}>
                  {remarkSaving ? "Saving..." : "Save Remark"}
                </button>
              </div>
            </div>
          </div>}
        </div>
      </div>

      <div className="card border-0 shadow-sm">
        <div className="card-body">
          <h4 className="mb-3">Attendance History</h4>
          <div className="table-responsive">
            <table className="table table-hover align-middle">
              <thead className="table-light">
                <tr>
                  <th>Date</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Check-In</th>
                  <th>Check-Out</th>
                  <th>Source</th>
                  <th>Remark</th>
                </tr>
              </thead>
              <tbody>
                {[...records].reverse().map((record) => (
                  <tr key={record.id}>
                    <td>{record.attendance_date}</td>
                    <td>{getAttendanceType(record)}</td>
                    <td>{record.status}</td>
                    <td>{formatTime(record.check_in_time)}</td>
                    <td>{formatTime(record.check_out_time)}</td>
                    <td>{getSource(record)}</td>
                    <td>{record.attendance_date === todayDate ? (todayRemark || "—") : "—"}</td>
                  </tr>
                ))}
                {records.length === 0 && (
                  <tr><td colSpan="7" className="text-center text-muted py-4">No attendance records for this month.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <AttendanceDayBreakdown userId="me" title="Detailed attendance by date" />
      
    </div>
  );
}

export default EmployeeAttendance;
