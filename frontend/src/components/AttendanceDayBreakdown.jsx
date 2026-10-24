import { HRONE_API_BASE } from "../config/api.js";
import { useCallback, useEffect, useState } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || HRONE_API_BASE;

function indiaToday() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function getToken() {
  try {
    const session = JSON.parse(sessionStorage.getItem("hrms_session") || "null");
    return session?.token || session?.access_token || "";
  } catch {
    return "";
  }
}

function formatClock(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: true,
  }).format(date);
}

function AttendanceDayBreakdown({ userId = "me", title = "Daily attendance" }) {
  const [selectedDate, setSelectedDate] = useState(indiaToday);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const token = getToken();
    if (!token || !userId) {
      setData(null);
      setError("Your login session or employee selection is missing.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/attendance/day/${encodeURIComponent(userId)}?attendance_date=${encodeURIComponent(selectedDate)}`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.detail || "Unable to load daily attendance.");
      setData(body);
    } catch (err) {
      setError(err?.message || "Unable to load daily attendance.");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [selectedDate, userId]);

  useEffect(() => { load(); }, [load]);

  const duration = (minutes) => `${Math.floor((minutes || 0) / 60)}h ${String((minutes || 0) % 60).padStart(2, "0")}m`;

  return (
    <section className="card border-0 shadow-sm my-3">
      <div className="card-body">
        <div className="d-flex flex-wrap align-items-center justify-content-between gap-3 mb-3">
          <div>
            <h5 className="mb-1">{title}</h5>
            <div className="small text-muted">Working intervals and break details · India time</div>
          </div>
          <div className="d-flex align-items-end gap-2">
            <div>
              <label className="form-label small mb-1" htmlFor={`attendance-date-${userId}`}>Attendance date</label>
              <input
                id={`attendance-date-${userId}`}
                type="date"
                className="form-control form-control-sm"
                value={selectedDate}
                max={indiaToday()}
                onChange={(event) => setSelectedDate(event.target.value)}
              />
            </div>
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={load} disabled={loading}>
              {loading ? "Loading…" : "Refresh"}
            </button>
          </div>
        </div>

        {error && <div className="alert alert-danger py-2" role="alert">{error}</div>}
        {loading && !data && <div className="text-muted small py-2">Calculating attendance…</div>}

        {data && (
          <>
            <div className="row g-3 mb-3">
              <div className="col-6 col-xl-3">
                <div className="rounded border p-3 h-100">
                  <div className="small text-muted">First check-in</div>
                  <div className="fs-5 fw-semibold">{formatClock(data.first_check_in)}</div>
                </div>
              </div>
              <div className="col-6 col-xl-3">
                <div className="rounded border p-3 h-100">
                  <div className="small text-muted">Last check-out</div>
                  <div className="fs-5 fw-semibold">{data.attendance_state === "In Progress" ? "In Progress" : formatClock(data.last_check_out)}</div>
                </div>
              </div>
              <div className="col-6 col-xl-3">
                <div className="rounded border p-3 h-100">
                  <div className="small text-muted">Net working time</div>
                  <div className="fs-5 fw-semibold text-success">{data.working_duration}</div>
                  {data.attendance_state === "In Progress" && <div className="small text-success">Live estimate</div>}
                </div>
              </div>
              <div className="col-6 col-xl-3">
                <div className="rounded border p-3 h-100">
                  <div className="small text-muted">Outside time (all gaps)</div>
                  <div className="fs-5 fw-semibold">{data.outside_duration}</div>
                  <div className="small text-muted">Only gaps over 10m appear below as breaks</div>
                </div>
              </div>
            </div>

            <div className="row g-3">
              <div className="col-lg-7">
                <h6 className="mb-2">Working intervals</h6>
                <div className="table-responsive border rounded">
                  <table className="table table-sm align-middle mb-0">
                    <thead className="table-light"><tr><th>From</th><th>To</th><th>Duration</th><th>Status</th></tr></thead>
                    <tbody>
                      {data.working_intervals?.map((item, index) => (
                        <tr key={`${item.from_time}-${index}`}>
                          <td>{formatClock(item.from_time)}</td><td>{formatClock(item.to_time)}</td>
                          <td>{duration(item.duration_minutes)}</td><td>{item.status}</td>
                        </tr>
                      ))}
                      {!data.working_intervals?.length && <tr><td colSpan="4" className="text-center text-muted py-3">No working interval recorded.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </div>
              <div className="col-lg-5">
                <div className="d-flex align-items-center justify-content-between mb-2">
                  <h6 className="mb-0">Breaks over 10 minutes</h6>
                  <span className="badge text-bg-warning">{data.break_duration}</span>
                </div>
                <div className="table-responsive border rounded">
                  <table className="table table-sm align-middle mb-0">
                    <thead className="table-light"><tr><th>From</th><th>To</th><th>Duration</th></tr></thead>
                    <tbody>
                      {data.break_intervals?.map((item, index) => (
                        <tr key={`${item.from_time}-${index}`}>
                          <td>{formatClock(item.from_time)}</td><td>{formatClock(item.to_time)}</td><td>{duration(item.duration_minutes)}</td>
                        </tr>
                      ))}
                      {!data.break_intervals?.length && <tr><td colSpan="3" className="text-center text-muted py-3">No break over 10 minutes.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </section>
  );
}

export default AttendanceDayBreakdown;
