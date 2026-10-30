import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  OUTCOME_LABELS,
  REQUEST_STATUS_BADGES,
  REQUEST_STATUS_LABELS,
  cancelAttendanceRequest,
  createAttendanceRequest,
  formatRequestDate,
  formatRequestDateTime,
  formatTimeRange,
  getMyAttendanceRequests,
} from "../services/attendanceRequestApi";

const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const STATUS_FILTERS = ["Not Marked", "Absent", "Present", "Weekly Off", "Holiday", "Not Applicable", "all"];
const ACTIVE_STATUSES = new Set(["pending", "processing"]);

const timeSuffix = (checkIn, checkOut) => {
  const text = formatTimeRange(checkIn, checkOut);
  return text ? ` (${text})` : "";
};

export function DateDecisionList({ dates }) {
  return (
    <ul className="list-unstyled mb-0 small d-grid gap-1">
      {(dates || []).map((item) => (
        <li key={item.attendance_date}>
          <strong>{formatRequestDate(item.attendance_date)}</strong>
          {item.original_status && <span className="text-muted"> · was {item.original_status}{timeSuffix(item.original_check_in_time, item.original_check_out_time)}</span>}
          <div>Requested {item.requested_status}{timeSuffix(item.requested_check_in_time, item.requested_check_out_time)}{item.requested_status === "Permission" ? ` (${item.requested_permission_start_time}–${item.requested_permission_end_time})` : ""}</div>
          {item.outcome ? (
            <div className={item.outcome === "rejected" || item.outcome === "not_applied" ? "text-danger" : "text-success"}>
              {OUTCOME_LABELS[item.outcome]}
              {(item.outcome === "approved" || item.outcome === "changed") && <> <strong>{item.final_status}</strong>{timeSuffix(item.final_check_in_time, item.final_check_out_time)}{item.final_status === "Permission" && item.requested_permission_start_time ? ` (${item.requested_permission_start_time}–${item.requested_permission_end_time})` : ""}</>}
              {item.outcome === "not_applied" && item.message && <span> — {item.message}</span>}
            </div>
          ) : <div className="text-muted">Awaiting HR Admin decision</div>}
        </li>
      ))}
    </ul>
  );
}

function rowProblem(entry) {
  const { day, requested_status: status, check_in: checkIn, check_out: checkOut } = entry;
  if (!status) return "Select the status you are requesting.";
  if (status === "Permission") {
    if (!entry.permission_start || !entry.permission_end) return "Enter Permission start and end times.";
    if (entry.permission_end <= entry.permission_start) return "Permission end must be later than Permission start.";
    if (!entry.permission_reason?.trim()) return "Enter a Permission reason.";
    return "";
  }
  if (status !== "Present") return "";
  if (checkIn && checkOut && checkOut <= checkIn) return "Check-out must be later than check-in.";
  if (day.status === "Present") {
    if (day.check_in_time && !checkIn) return "An existing check-in cannot be removed.";
    if (day.check_out_time && !checkOut) return "An existing check-out cannot be removed.";
    if ((checkIn || null) === (day.check_in_time || null) && (checkOut || null) === (day.check_out_time || null)) {
      return "Change the check-in or check-out time to request a correction.";
    }
  }
  return "";
}

function MyAttendanceRequests({ calendar, year, month, maxYear, maxMonth, open, onOpenChange, onMonthChange, onChanged, onDecided, refreshKey = 0 }) {
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [selected, setSelected] = useState({});
  const [remarks, setRemarks] = useState("");
  const [formError, setFormError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [statusFilter, setStatusFilter] = useState("Not Marked");
  const [dateFilter, setDateFilter] = useState("");
  const [cancellingId, setCancellingId] = useState("");
  const [confirmCancelId, setConfirmCancelId] = useState("");
  const activeIdsRef = useRef(null);

  const loadRequests = useCallback(async () => {
    try {
      const data = await getMyAttendanceRequests();
      setRequests(data);
      setError("");
      // A request decided by HR Admin since the last load changes attendance:
      // let the page refresh the calendar and detailed attendance.
      const active = new Set(data.filter((item) => ACTIVE_STATUSES.has(item.status)).map((item) => item.id));
      if (activeIdsRef.current && [...activeIdsRef.current].some((id) => !active.has(id))) onDecided?.();
      activeIdsRef.current = active;
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }, [onDecided]);

  useEffect(() => { loadRequests(); }, [loadRequests, refreshKey]);

  const hasActive = requests.some((item) => ACTIVE_STATUSES.has(item.status));
  useEffect(() => {
    if (!hasActive) return undefined;
    const timer = window.setInterval(loadRequests, 30000);
    return () => window.clearInterval(timer);
  }, [hasActive, loadRequests]);

  // Past dates of the displayed month that can be corrected or explain why not.
  const visibleDays = useMemo(() => (calendar?.days || []).filter((day) => {
    if (day.is_future) return false;
    if (dateFilter) return day.attendance_date === dateFilter;
    return statusFilter === "all" ? day.eligible_for_request || day.request : day.status === statusFilter;
  }), [calendar, dateFilter, statusFilter]);

  const selectedDates = Object.keys(selected).sort();
  const limits = calendar?.limits;

  const resetForm = () => {
    setSelected({});
    setRemarks("");
    setFormError("");
    setDateFilter("");
    setStatusFilter("Not Marked");
  };

  const openForm = () => {
    resetForm();
    setSuccess("");
    onOpenChange(true);
  };

  const closeForm = () => {
    resetForm();
    onOpenChange(false);
  };

  const toggleDate = (day, checked) => {
    setSelected((current) => {
      const next = { ...current };
      if (checked) {
        next[day.attendance_date] = {
          day,
          requested_status: day.request_options.length === 1 ? day.request_options[0] : "",
          // Existing times prefill the Present correction fields; nothing is invented.
          check_in: day.check_in_time || "",
          check_out: day.check_out_time || "",
          permission_start: "",
          permission_end: "",
          permission_reason: "",
        };
      } else {
        delete next[day.attendance_date];
      }
      return next;
    });
  };

  const updateEntry = (iso, changes) => setSelected((current) => ({ ...current, [iso]: { ...current[iso], ...changes } }));

  const jumpToDate = (iso) => {
    setDateFilter(iso);
    if (!iso) return;
    const [jumpYear, jumpMonth] = iso.split("-").map(Number);
    if (jumpYear !== year || jumpMonth !== month) onMonthChange?.(jumpYear, jumpMonth);
  };

  const shiftMonth = (delta) => {
    const index = year * 12 + (month - 1) + delta;
    setDateFilter("");
    onMonthChange?.(Math.floor(index / 12), (index % 12) + 1);
  };

  const submit = async (event) => {
    event.preventDefault();
    if (!selectedDates.length) {
      setFormError("Select at least one eligible date.");
      return;
    }
    const firstProblem = selectedDates.map((iso) => [iso, rowProblem(selected[iso])]).find(([, problem]) => problem);
    if (firstProblem) {
      setFormError(`${formatRequestDate(firstProblem[0])}: ${firstProblem[1]}`);
      return;
    }
    setSubmitting(true);
    setFormError("");
    try {
      const created = await createAttendanceRequest(
        selectedDates.map((iso) => {
          const entry = selected[iso];
          const isPresent = entry.requested_status === "Present";
          return {
            attendance_date: iso,
            requested_status: entry.requested_status,
            requested_check_in_time: isPresent ? entry.check_in || null : null,
            requested_check_out_time: isPresent ? entry.check_out || null : null,
            requested_permission_start_time: entry.requested_status === "Permission" ? entry.permission_start : null,
            requested_permission_end_time: entry.requested_status === "Permission" ? entry.permission_end : null,
            requested_permission_reason: entry.requested_status === "Permission" ? entry.permission_reason.trim() : null,
          };
        }),
        remarks.trim(),
      );
      setSuccess(`Attendance request ${created.request_number} submitted for ${created.dates.length} date(s). Status: Pending HR Admin review. Your attendance will change only after approval.`);
      closeForm();
      window.dispatchEvent(new Event("hrms-notifications-updated"));
      await Promise.all([loadRequests(), onChanged?.()]);
    } catch (submitError) {
      setFormError(submitError.message);
    } finally {
      setSubmitting(false);
    }
  };

  const cancelRequest = async (request) => {
    // Two-step confirmation in the page (window.confirm can be suppressed by the browser).
    if (confirmCancelId !== request.id) {
      setConfirmCancelId(request.id);
      return;
    }
    setConfirmCancelId("");
    setCancellingId(request.id);
    setError("");
    setSuccess("");
    try {
      await cancelAttendanceRequest(request.id);
      setSuccess(`Attendance request ${request.request_number} was cancelled.`);
      await Promise.all([loadRequests(), onChanged?.()]);
    } catch (cancelError) {
      setError(cancelError.message);
    } finally {
      setCancellingId("");
    }
  };

  const atLatestMonth = year > maxYear || (year === maxYear && month >= maxMonth);

  return (
    <>
      <section className="card border-0 shadow-sm mb-4" aria-label="My attendance requests">
        <div className="card-body">
          <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-3">
            <div>
              <h4 className="mb-1">My Attendance Requests</h4>
              <small className="text-muted">Request a correction for a past date. HR Admin approves or rejects each date; attendance changes only after approval.</small>
            </div>
            <button type="button" className="btn btn-primary" onClick={openForm} disabled={!calendar}>
              <i className="bi bi-calendar-plus me-2" aria-hidden="true" />Attendance Request
            </button>
          </div>
          {error && <div className="alert alert-danger py-2" role="alert">{error}</div>}
          {success && <div className="alert alert-success py-2" role="status">{success}</div>}
          <div className="table-responsive">
            <table className="table table-hover align-middle mb-0">
              <thead className="table-light">
                <tr><th>Request</th><th>Submitted</th><th>Dates and decisions</th><th>Status</th><th>Response</th><th /></tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan="6" className="text-center text-muted py-4">Loading requests...</td></tr>
                ) : requests.length === 0 ? (
                  <tr><td colSpan="6" className="text-center text-muted py-4">You have not submitted any attendance requests.</td></tr>
                ) : requests.map((request) => (
                  <tr key={request.id}>
                    <td className="fw-semibold">{request.request_number}</td>
                    <td className="small">{formatRequestDateTime(request.submitted_at)}</td>
                    <td style={{ minWidth: 220 }}><DateDecisionList dates={request.dates} /></td>
                    <td><span className={`badge ${REQUEST_STATUS_BADGES[request.status] || "text-bg-secondary"}`}>{REQUEST_STATUS_LABELS[request.status] || request.status}</span></td>
                    <td className="small">
                      {request.reviewed_at ? <>{formatRequestDateTime(request.reviewed_at)}<div className="text-muted">by {request.reviewed_by_name || "HR Admin"}</div></> : "—"}
                      {request.review_comments && <div className="mt-1">Remarks: {request.review_comments}</div>}
                    </td>
                    <td className="text-end">
                      {request.status === "pending" && (
                        <div className="d-flex justify-content-end gap-1">
                          <button type="button" className={`btn btn-sm ${confirmCancelId === request.id ? "btn-danger" : "btn-outline-danger"}`} disabled={cancellingId === request.id} onClick={() => cancelRequest(request)}>
                            {cancellingId === request.id ? "Cancelling..." : confirmCancelId === request.id ? "Confirm cancel" : "Cancel"}
                          </button>
                          {confirmCancelId === request.id && (
                            <button type="button" className="btn btn-sm btn-light border" onClick={() => setConfirmCancelId("")}>Keep</button>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {open && (
        <div className="modal d-block" tabIndex="-1" role="dialog" aria-modal="true" aria-labelledby="attendance-request-title" style={{ backgroundColor: "rgba(0, 0, 0, 0.5)" }}>
          <div className="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable modal-fullscreen-md-down">
            <form className="modal-content" onSubmit={submit} noValidate>
              <div className="modal-header">
                <h5 className="modal-title" id="attendance-request-title">Attendance Request</h5>
                <button type="button" className="btn-close" aria-label="Close" onClick={closeForm} disabled={submitting} />
              </div>
              <div className="modal-body">
                <div className="row g-2 align-items-end mb-3">
                  <div className="col-12 col-md-5">
                    <span className="form-label small d-block mb-1">Month</span>
                    <div className="d-flex align-items-center gap-2">
                      <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => shiftMonth(-1)} disabled={submitting} aria-label="Previous month"><i className="bi bi-chevron-left" aria-hidden="true" /></button>
                      <strong className="flex-grow-1 text-center">{MONTH_NAMES[month - 1]} {year}</strong>
                      <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => shiftMonth(1)} disabled={submitting || atLatestMonth} aria-label="Next month"><i className="bi bi-chevron-right" aria-hidden="true" /></button>
                    </div>
                  </div>
                  <div className="col-6 col-md-3">
                    <label className="form-label small mb-1" htmlFor="ar-status-filter">Attendance status</label>
                    <select id="ar-status-filter" className="form-select form-select-sm" value={statusFilter} disabled={Boolean(dateFilter)} onChange={(e) => setStatusFilter(e.target.value)}>
                      {STATUS_FILTERS.map((value) => <option key={value} value={value}>{value === "all" ? "All eligible dates" : value}</option>)}
                    </select>
                  </div>
                  <div className="col-6 col-md-4">
                    <label className="form-label small mb-1" htmlFor="ar-date-filter">Find a date</label>
                    <div className="d-flex gap-1">
                      <input id="ar-date-filter" type="date" className="form-control form-control-sm" value={dateFilter} onChange={(e) => jumpToDate(e.target.value)} />
                      {dateFilter && <button type="button" className="btn btn-sm btn-light border" onClick={() => setDateFilter("")}>Clear</button>}
                    </div>
                  </div>
                </div>

                {limits && (
                  <div className="small text-muted mb-2">
                    This month: Weekly Off {limits.weekly_off_used} of {limits.weekly_off_limit} used · Medical Leave {limits.medical_leave_used} of {limits.medical_leave_limit} used
                  </div>
                )}

                {[...new Set(visibleDays.filter((day) => day.eligible_for_request).flatMap((day) => day.option_notes))].map((note) => (
                  <div key={note} className="alert alert-warning py-2 small mb-2">{note}</div>
                ))}

                {visibleDays.length === 0 ? (
                  <div className="alert alert-light border mb-0">No past dates match this filter in {MONTH_NAMES[month - 1]} {year}.</div>
                ) : (
                  <div className="table-responsive border rounded">
                    <table className="table table-sm align-middle mb-0">
                      <thead className="table-light">
                        <tr><th style={{ width: 40 }}><span className="visually-hidden">Select</span></th><th>Date</th><th>Current attendance</th><th>Requested status</th><th>Requested times</th></tr>
                      </thead>
                      <tbody>
                        {visibleDays.map((day) => {
                          const iso = day.attendance_date;
                          const entry = selected[iso];
                          const problem = entry ? rowProblem(entry) : "";
                          return (
                            <tr key={iso} className={day.eligible_for_request ? "" : "text-muted"}>
                              <td>
                                <input type="checkbox" className="form-check-input" id={`request-${iso}`} checked={Boolean(entry)}
                                  disabled={!day.eligible_for_request || submitting} onChange={(e) => toggleDate(day, e.target.checked)} />
                              </td>
                              <td className="text-nowrap"><label htmlFor={`request-${iso}`} className="mb-0">{formatRequestDate(iso)}</label></td>
                              <td>
                                <div>{day.status_label || day.status}</div>
                                {(day.check_in_time || day.check_out_time) && <div className="small text-muted">{formatTimeRange(day.check_in_time, day.check_out_time)}</div>}
                                {!day.eligible_for_request && <div className="small">{day.ineligible_reason}</div>}
                              </td>
                              <td style={{ minWidth: 160 }}>
                                {entry ? (
                                  <select className={`form-select form-select-sm ${entry.requested_status ? "" : "border-warning"}`} aria-label={`Requested status for ${iso}`}
                                    value={entry.requested_status} disabled={submitting}
                                    onChange={(e) => updateEntry(iso, { requested_status: e.target.value })}>
                                    <option value="">Select status...</option>
                                    {day.request_options.map((option) => <option key={option} value={option}>{option}</option>)}
                                  </select>
                                ) : null}
                              </td>
                              <td style={{ minWidth: 200 }}>
                                {entry?.requested_status === "Permission" ? (
                                  <div className="d-flex flex-wrap gap-2">
                                    <label className="small mb-0">Permission start<input type="time" className="form-control form-control-sm" value={entry.permission_start || ""} disabled={submitting} onChange={(e) => updateEntry(iso, { permission_start: e.target.value })} /></label>
                                    <label className="small mb-0">Permission end<input type="time" className="form-control form-control-sm" value={entry.permission_end || ""} disabled={submitting} onChange={(e) => updateEntry(iso, { permission_end: e.target.value })} /></label>
                                    <label className="small mb-0 w-100">Reason<input type="text" className="form-control form-control-sm" value={entry.permission_reason || ""} disabled={submitting} onChange={(e) => updateEntry(iso, { permission_reason: e.target.value })} /></label>
                                  </div>
                                ) : entry?.requested_status === "Present" ? (
                                  <div className="d-flex flex-wrap gap-2">
                                    <label className="small mb-0">Check-in
                                      <input type="time" className="form-control form-control-sm" value={entry.check_in} disabled={submitting}
                                        onChange={(e) => updateEntry(iso, { check_in: e.target.value })} />
                                    </label>
                                    <label className="small mb-0">Check-out
                                      <input type="time" className="form-control form-control-sm" value={entry.check_out} disabled={submitting}
                                        onChange={(e) => updateEntry(iso, { check_out: e.target.value })} />
                                    </label>
                                  </div>
                                ) : <span className="small text-muted">—</span>}
                                {problem && entry?.requested_status && <div className="small text-danger mt-1">{problem}</div>}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}

                <div className="mt-3"><strong>{selectedDates.length} date(s) selected</strong></div>
                {selectedDates.length > 0 && (
                  <div className="d-flex flex-wrap gap-2 mt-2">
                    {selectedDates.map((iso) => (
                      <span key={iso} className="badge text-bg-light border d-inline-flex align-items-center gap-1 text-wrap text-start">
                        {formatRequestDate(iso)} · {selected[iso].requested_status || "status not selected"}
                        {selected[iso].requested_status === "Present" && timeSuffix(selected[iso].check_in, selected[iso].check_out)}
                        <button type="button" className="btn-close" style={{ fontSize: 8 }} aria-label={`Remove ${iso}`} onClick={() => toggleDate(selected[iso].day, false)} disabled={submitting} />
                      </span>
                    ))}
                  </div>
                )}

                <label htmlFor="attendance-request-remarks" className="form-label mt-3">Reason / remarks</label>
                <textarea id="attendance-request-remarks" className="form-control" rows={2} maxLength={1000} value={remarks}
                  placeholder="Explain why this attendance needs correction" onChange={(e) => setRemarks(e.target.value)} disabled={submitting} />
                {formError && <div className="alert alert-danger py-2 mt-3 mb-0" role="alert">{formError}</div>}
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-light border" onClick={closeForm} disabled={submitting}>Close</button>
                <button type="submit" className="btn btn-primary" disabled={submitting || !selectedDates.length}>
                  {submitting ? "Submitting..." : `Submit Request (${selectedDates.length})`}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}

export default MyAttendanceRequests;
