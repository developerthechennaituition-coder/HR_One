import { useCallback, useEffect, useMemo, useState } from "react";
import {
  OUTCOME_LABELS,
  REQUEST_STATUS_BADGES,
  REQUEST_STATUS_LABELS,
  cancelAttendanceRequest,
  createAttendanceRequest,
  formatRequestDate,
  formatRequestDateTime,
  getMyAttendanceRequests,
} from "../services/attendanceRequestApi";

const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

export function DateDecisionList({ dates }) {
  return (
    <ul className="list-unstyled mb-0 small">
      {(dates || []).map((item) => (
        <li key={item.attendance_date}>
          <strong>{formatRequestDate(item.attendance_date)}</strong> — Requested {item.requested_status}
          {item.outcome
            ? <> — {OUTCOME_LABELS[item.outcome]}{item.outcome !== "not_applied" && <> <strong>{item.final_status}</strong></>}
              {item.outcome === "not_applied" && item.message && <span className="text-muted"> ({item.message})</span>}</>
            : <span className="text-muted"> — Awaiting decision</span>}
        </li>
      ))}
    </ul>
  );
}

function MyAttendanceRequests({ calendar, year, month, open, onOpenChange, onChanged, refreshKey = 0 }) {
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [selected, setSelected] = useState({});
  const [remarks, setRemarks] = useState("");
  const [formError, setFormError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [cancellingId, setCancellingId] = useState("");
  const [confirmCancelId, setConfirmCancelId] = useState("");

  const loadRequests = useCallback(async () => {
    try {
      setRequests(await getMyAttendanceRequests());
      setError("");
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadRequests(); }, [loadRequests, refreshKey]);

  // Requestable dates come from the same authoritative calendar data.
  const notMarkedDays = useMemo(
    () => (calendar?.days || []).filter((day) => day.status === "Not Marked" && !day.is_future),
    [calendar],
  );
  // Only dates that are eligible in the current calendar data can be submitted.
  const eligibleDates = useMemo(
    () => new Set(notMarkedDays.filter((day) => day.eligible_for_request).map((day) => day.attendance_date)),
    [notMarkedDays],
  );
  const selectedDates = Object.keys(selected).filter((iso) => eligibleDates.has(iso)).sort();

  const resetForm = () => {
    setSelected({});
    setRemarks("");
    setFormError("");
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

  const toggleDate = (iso, checked) => {
    setSelected((current) => {
      const next = { ...current };
      if (checked) next[iso] = next[iso] || "Present";
      else delete next[iso];
      return next;
    });
  };

  const applyToAll = (status) => setSelected((current) => Object.fromEntries(Object.keys(current).map((iso) => [iso, status])));

  const submit = async (event) => {
    event.preventDefault();
    if (!selectedDates.length) {
      setFormError("Select at least one eligible date.");
      return;
    }
    setSubmitting(true);
    setFormError("");
    try {
      const created = await createAttendanceRequest(
        selectedDates.map((iso) => ({ attendance_date: iso, requested_status: selected[iso] })),
        remarks.trim(),
      );
      setSuccess(`Attendance request ${created.request_number} submitted for ${created.dates.length} date(s). HR Admin will review each date.`);
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

  return (
    <>
      <section className="card border-0 shadow-sm mb-4" aria-label="My attendance requests">
        <div className="card-body">
          <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-3">
            <div>
              <h4 className="mb-1">My Attendance Requests</h4>
              <small className="text-muted">Request Present or Holiday for past dates that are Not Marked. HR Admin decides each date.</small>
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
                    <td><DateDecisionList dates={request.dates} /></td>
                    <td><span className={`badge ${REQUEST_STATUS_BADGES[request.status] || "text-bg-secondary"}`}>{REQUEST_STATUS_LABELS[request.status] || request.status}</span></td>
                    <td className="small">
                      {request.reviewed_at ? <>{formatRequestDateTime(request.reviewed_at)}<div className="text-muted">by {request.reviewed_by_name || "HR Admin"}</div></> : "—"}
                      {request.review_comments && <div className="mt-1">“{request.review_comments}”</div>}
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
          <div className="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable">
            <form className="modal-content" onSubmit={submit}>
              <div className="modal-header">
                <h5 className="modal-title" id="attendance-request-title">Attendance Request · {MONTH_NAMES[month - 1]} {year}</h5>
                <button type="button" className="btn-close" aria-label="Close" onClick={closeForm} disabled={submitting} />
              </div>
              <div className="modal-body">
                <p className="small text-muted">
                  Select one or more Not Marked dates and choose the status you are requesting for each date. Use the calendar's month controls to request dates in another month.
                </p>
                {notMarkedDays.length === 0 ? (
                  <div className="alert alert-light border">There are no Not Marked past dates in this month.</div>
                ) : (
                  <div className="table-responsive border rounded">
                    <table className="table table-sm align-middle mb-0">
                      <thead className="table-light"><tr><th style={{ width: 40 }} /><th>Date</th><th>Current status</th><th>Requested status</th></tr></thead>
                      <tbody>
                        {notMarkedDays.map((day) => {
                          const iso = day.attendance_date;
                          const checked = Boolean(selected[iso]);
                          return (
                            <tr key={iso} className={day.eligible_for_request ? "" : "text-muted"}>
                              <td>
                                <input type="checkbox" className="form-check-input" id={`request-${iso}`} checked={checked}
                                  disabled={!day.eligible_for_request || submitting} onChange={(event) => toggleDate(iso, event.target.checked)} />
                              </td>
                              <td><label htmlFor={`request-${iso}`} className="mb-0">{formatRequestDate(iso)}</label></td>
                              <td>{day.eligible_for_request ? "Not Marked" : <span className="small">{day.ineligible_reason}</span>}</td>
                              <td>
                                {checked && (
                                  <select className="form-select form-select-sm" aria-label={`Requested status for ${iso}`} value={selected[iso]}
                                    onChange={(event) => setSelected((current) => ({ ...current, [iso]: event.target.value }))} disabled={submitting}>
                                    <option value="Present">Present</option>
                                    <option value="Holiday">Holiday</option>
                                  </select>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}

                <div className="d-flex flex-wrap align-items-center gap-2 mt-3">
                  <strong>{selectedDates.length} date(s) selected</strong>
                  {selectedDates.length > 1 && (
                    <>
                      <span className="text-muted small">Set all selected to:</span>
                      <button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => applyToAll("Present")} disabled={submitting}>Present</button>
                      <button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => applyToAll("Holiday")} disabled={submitting}>Holiday</button>
                    </>
                  )}
                </div>
                {selectedDates.length > 0 && (
                  <div className="d-flex flex-wrap gap-2 mt-2">
                    {selectedDates.map((iso) => (
                      <span key={iso} className="badge text-bg-light border d-inline-flex align-items-center gap-1">
                        {formatRequestDate(iso)} · {selected[iso]}
                        <button type="button" className="btn-close" style={{ fontSize: 8 }} aria-label={`Remove ${iso}`} onClick={() => toggleDate(iso, false)} disabled={submitting} />
                      </span>
                    ))}
                  </div>
                )}

                <label htmlFor="attendance-request-remarks" className="form-label mt-3">Reason / remarks (optional)</label>
                <textarea id="attendance-request-remarks" className="form-control" rows={2} maxLength={1000} value={remarks}
                  onChange={(event) => setRemarks(event.target.value)} disabled={submitting} />
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
