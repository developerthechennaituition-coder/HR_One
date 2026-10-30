import { useCallback, useEffect, useRef, useState } from "react";
import {
  REQUEST_STATUS_BADGES,
  REQUEST_STATUS_LABELS,
  formatRequestDate,
  formatRequestDateTime,
  formatTimeRange,
  getAttendanceRequest,
  listAttendanceRequests,
  respondToAttendanceRequest,
} from "../services/attendanceRequestApi";
import { DateDecisionList } from "./MyAttendanceRequests";

const EMPTY_FILTERS = { status: "pending", employee: "", department: "", period: "", attendance_date: "" };

function StatusBadge({ value }) {
  return <span className={`badge ${REQUEST_STATUS_BADGES[value] || "text-bg-secondary"}`}>{REQUEST_STATUS_LABELS[value] || value}</span>;
}

const timesOrDash = (checkIn, checkOut) => formatTimeRange(checkIn, checkOut) || "—";

// No date is pre-approved: HR must choose Approve or Reject for each one.
const initialDecisions = (request) => Object.fromEntries((request.dates || []).map((item) => [item.attendance_date, {
  action: "",
  final_status: (item.allowed_final_statuses || []).includes(item.requested_status) ? item.requested_status : "",
  check_in: item.requested_check_in_time || item.original_check_in_time || "",
  check_out: item.requested_check_out_time || item.original_check_out_time || "",
  permission_start: item.requested_permission_start_time || "",
  permission_end: item.requested_permission_end_time || "",
  permission_reason: item.requested_permission_reason || "",
}]));

function decisionProblem(item, decision) {
  if (!decision?.action) return "Choose Approve or Reject.";
  if (decision.action === "reject") return "";
  if (!decision.final_status) return "Select the final status.";
  if (decision.final_status === "Permission") {
    if (!decision.permission_start || !decision.permission_end || decision.permission_end <= decision.permission_start) return "Enter valid Permission start and end times.";
    if (!decision.permission_reason?.trim()) return "Enter a Permission reason.";
    return "";
  }
  if (decision.final_status !== "Present") return "";
  if (decision.check_in && decision.check_out && decision.check_out <= decision.check_in) return "Check-out must be later than check-in.";
  if (item.original_status === "Present") {
    if (item.original_check_in_time && !decision.check_in) return "An existing check-in cannot be removed.";
    if (item.original_check_out_time && !decision.check_out) return "An existing check-out cannot be removed.";
  }
  return "";
}

function AttendanceRequestInbox() {
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [appliedFilters, setAppliedFilters] = useState(EMPTY_FILTERS);
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [review, setReview] = useState(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [decisions, setDecisions] = useState({});
  const [comments, setComments] = useState("");
  const [reviewError, setReviewError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const sectionRef = useRef(null);

  const loadRequests = useCallback(async () => {
    setLoading(true);
    try {
      setRequests(await listAttendanceRequests(appliedFilters));
      setError("");
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }, [appliedFilters]);

  useEffect(() => { loadRequests(); }, [loadRequests]);

  // Opened from a notification's "Review & Respond" action (see Leave.jsx).
  useEffect(() => {
    const requestId = sessionStorage.getItem("hrms_open_attendance_request");
    if (!requestId) return;
    sessionStorage.removeItem("hrms_open_attendance_request");
    sectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    openReview(requestId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const showReview = (request) => {
    setReview(request);
    setDecisions(initialDecisions(request));
  };

  const openReview = async (requestId) => {
    setReviewLoading(true);
    setReviewError("");
    setSuccess("");
    setComments("");
    setConfirming(false);
    try {
      showReview(await getAttendanceRequest(requestId));
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setReviewLoading(false);
    }
  };

  // Cancel discards unsaved decisions; nothing is sent to the server.
  const closeReview = () => {
    if (submitting) return;
    setReview(null);
    setDecisions({});
    setComments("");
    setReviewError("");
    setConfirming(false);
  };

  const updateDecision = (iso, changes) => {
    setConfirming(false);
    setDecisions((current) => ({ ...current, [iso]: { ...current[iso], ...changes } }));
  };

  const problems = review ? review.dates.map((item) => [item, decisionProblem(item, decisions[item.attendance_date])]).filter(([, problem]) => problem) : [];
  const hasReject = review ? review.dates.some((item) => decisions[item.attendance_date]?.action === "reject") : false;

  const submitResponse = async () => {
    if (problems.length) {
      setReviewError(problems.map(([item, problem]) => `${formatRequestDate(item.attendance_date)}: ${problem}`).join(" "));
      return;
    }
    if (hasReject && !comments.trim()) {
      setReviewError("Enter response remarks when rejecting a date.");
      return;
    }
    if (!confirming) {
      setConfirming(true);
      setReviewError("");
      return;
    }
    setSubmitting(true);
    setReviewError("");
    try {
      const result = await respondToAttendanceRequest(
        review.id,
        review.dates.map((item) => {
          const decision = decisions[item.attendance_date];
          const isPresent = decision.action === "approve" && decision.final_status === "Present";
          return decision.action === "reject"
            ? { attendance_date: item.attendance_date, action: "reject" }
            : {
              attendance_date: item.attendance_date,
              action: "approve",
              final_status: decision.final_status,
              final_check_in_time: isPresent ? decision.check_in || null : null,
              final_check_out_time: isPresent ? decision.check_out || null : null,
              final_permission_start_time: decision.final_status === "Permission" ? decision.permission_start : null,
              final_permission_end_time: decision.final_status === "Permission" ? decision.permission_end : null,
              final_permission_reason: decision.final_status === "Permission" ? decision.permission_reason.trim() : null,
            };
        }),
        comments.trim(),
      );
      const { applied, rejected = 0, not_applied: notApplied, notification_sent: notified, outdated_payroll_periods: outdated = [] } = result.summary;
      setSuccess(
        `${result.request_number} saved as ${REQUEST_STATUS_LABELS[result.status]}: ${applied} date(s) approved and applied, ${rejected} rejected` +
        (notApplied ? `, ${notApplied} not applied (see details)` : "") +
        (notified ? ". The employee has been notified." : ". The employee notification could not be created.") +
        (outdated.length ? ` Calculated payroll for ${outdated.join(", ")} is now marked outdated — disapprove and recalculate it in Payroll.` : ""),
      );
      setReview(null);
      window.dispatchEvent(new Event("hrms-notifications-updated"));
      await loadRequests();
    } catch (submitError) {
      setConfirming(false);
      setReviewError(submitError.message);
      // Refresh in case the request or attendance changed elsewhere.
      getAttendanceRequest(review.id).then((fresh) => setReview(fresh)).catch(() => {});
    } finally {
      setSubmitting(false);
    }
  };

  const applyFilters = (event) => {
    event.preventDefault();
    setAppliedFilters(filters);
  };

  const finalSummary = (item) => {
    const decision = decisions[item.attendance_date];
    if (decision?.action === "reject") return "Reject (attendance unchanged)";
    if (decision?.action !== "approve" || !decision.final_status) return "No decision";
    const times = decision.final_status === "Present" ? formatTimeRange(decision.check_in, decision.check_out) : decision.final_status === "Permission" ? `${decision.permission_start}–${decision.permission_end}` : "";
    return `Approve as ${decision.final_status}${times ? ` (${times})` : ""}`;
  };

  return (
    <section ref={sectionRef} className="card border-0 shadow-sm mt-4" aria-label="Attendance requests inbox">
      <div className="card-body">
        <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-3">
          <div>
            <h4 className="mb-1">Attendance Requests</h4>
            <small className="text-muted">Review employee requests and approve or reject every date individually.</small>
          </div>
          <button type="button" className="btn btn-outline-secondary btn-sm" onClick={loadRequests} disabled={loading}>{loading ? "Loading..." : "Refresh"}</button>
        </div>

        <form className="row g-2 align-items-end mb-3" onSubmit={applyFilters}>
          <div className="col-6 col-md-2">
            <label className="form-label small mb-1" htmlFor="ar-status">Status</label>
            <select id="ar-status" className="form-select form-select-sm" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
              <option value="all">All</option>
              {Object.entries(REQUEST_STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </div>
          <div className="col-6 col-md-3">
            <label className="form-label small mb-1" htmlFor="ar-employee">Employee</label>
            <input id="ar-employee" className="form-control form-control-sm" placeholder="Name or employee ID" value={filters.employee} onChange={(e) => setFilters({ ...filters, employee: e.target.value })} />
          </div>
          <div className="col-6 col-md-2">
            <label className="form-label small mb-1" htmlFor="ar-department">Department</label>
            <input id="ar-department" className="form-control form-control-sm" value={filters.department} onChange={(e) => setFilters({ ...filters, department: e.target.value })} />
          </div>
          <div className="col-6 col-md-2">
            <label className="form-label small mb-1" htmlFor="ar-period">Payroll period</label>
            <input id="ar-period" type="month" className="form-control form-control-sm" value={filters.period} onChange={(e) => setFilters({ ...filters, period: e.target.value })} />
          </div>
          <div className="col-6 col-md-2">
            <label className="form-label small mb-1" htmlFor="ar-date">Attendance date</label>
            <input id="ar-date" type="date" className="form-control form-control-sm" value={filters.attendance_date} onChange={(e) => setFilters({ ...filters, attendance_date: e.target.value })} />
          </div>
          <div className="col-12 col-md-auto d-flex gap-1">
            <button type="submit" className="btn btn-primary btn-sm">Apply</button>
            <button type="button" className="btn btn-light border btn-sm" onClick={() => { setFilters(EMPTY_FILTERS); setAppliedFilters(EMPTY_FILTERS); }}>Reset</button>
          </div>
        </form>

        {error && <div className="alert alert-danger py-2" role="alert">{error}</div>}
        {success && <div className="alert alert-success py-2" role="status">{success}</div>}

        <div className="table-responsive">
          <table className="table table-hover align-middle mb-0">
            <thead className="table-light">
              <tr><th>Request ID</th><th>Employee</th><th>Department</th><th>Submitted</th><th>Requested dates</th><th>Status</th><th>Action</th></tr>
            </thead>
            <tbody>
              {loading && !requests.length ? (
                <tr><td colSpan="7" className="text-center text-muted py-4">Loading attendance requests...</td></tr>
              ) : requests.length === 0 ? (
                <tr><td colSpan="7" className="text-center text-muted py-4">No attendance requests match these filters.</td></tr>
              ) : requests.map((request) => (
                <tr key={request.id}>
                  <td className="fw-semibold">{request.request_number}</td>
                  <td>{request.employee_name}<div className="small text-muted">{request.employee_id}</div></td>
                  <td>{request.department || "—"}</td>
                  <td className="small">{formatRequestDateTime(request.submitted_at)}</td>
                  <td style={{ minWidth: 220 }}><DateDecisionList dates={request.dates} /></td>
                  <td><StatusBadge value={request.status} /></td>
                  <td>
                    <button type="button" className="btn btn-sm btn-outline-primary" disabled={reviewLoading} onClick={() => openReview(request.id)}>
                      {["pending", "processing"].includes(request.status) ? "Review" : "View"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {review && (
        <div className="modal d-block" tabIndex="-1" role="dialog" aria-modal="true" aria-labelledby="ar-review-title" style={{ backgroundColor: "rgba(0, 0, 0, 0.5)" }}>
          <div className="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable modal-fullscreen-md-down">
            <div className="modal-content">
              <div className="modal-header">
                <h5 className="modal-title" id="ar-review-title">
                  {review.request_number} · {review.employee_name} ({review.employee_id}) <StatusBadge value={review.status} />
                </h5>
                <button type="button" className="btn-close" aria-label="Close" onClick={closeReview} disabled={submitting} />
              </div>
              <div className="modal-body">
                <div className="row g-2 small mb-3">
                  <div className="col-6 col-md-3"><span className="text-muted">Department</span><div>{review.department || "—"}</div></div>
                  <div className="col-6 col-md-3"><span className="text-muted">Submitted</span><div>{formatRequestDateTime(review.submitted_at)}</div></div>
                  <div className="col-12 col-md-6"><span className="text-muted">Employee remarks</span><div>{review.employee_remarks || "—"}</div></div>
                </div>

                {review.can_respond ? (
                  <div className="table-responsive border rounded">
                    <table className="table table-sm align-middle mb-0">
                      <thead className="table-light">
                        <tr><th>Date</th><th>Original attendance</th><th>Employee requested</th><th>Payroll / eligibility</th><th>HR Admin decision</th></tr>
                      </thead>
                      <tbody>
                        {review.dates.map((item) => {
                          const iso = item.attendance_date;
                          const decision = decisions[iso] || {};
                          const problem = decisionProblem(item, decision);
                          return (
                            <tr key={iso} className={decision.action ? "" : "table-warning"}>
                              <td className="fw-semibold text-nowrap">{formatRequestDate(iso)}</td>
                              <td>
                                <div>{item.original_status}</div>
                                <div className="small text-muted">{timesOrDash(item.original_check_in_time, item.original_check_out_time)}</div>
                                {item.original_attendance_category && <div className="small text-muted">{item.original_attendance_category}</div>}
                              </td>
                              <td>
                                <div className="fw-semibold">{item.requested_status}</div>
                                {item.requested_status === "Present" && <div className="small">Requested: {timesOrDash(item.requested_check_in_time, item.requested_check_out_time)}</div>}
                                {item.requested_status === "Permission" && <div className="small">Requested: {item.requested_permission_start_time}–{item.requested_permission_end_time}<br />{item.requested_permission_reason}</div>}
                              </td>
                              <td className="small" style={{ minWidth: 150 }}>
                                {item.payroll_status ? `Payroll ${item.payroll_status}` : "No payroll yet"}
                                {item.eligibility_message && <div className="text-danger">{item.eligibility_message}</div>}
                                {item.payroll_note && <div className="text-warning-emphasis">{item.payroll_note}</div>}
                              </td>
                              <td style={{ minWidth: 240 }}>
                                <div className="btn-group btn-group-sm mb-2" role="group" aria-label={`Decision for ${iso}`}>
                                  <button type="button" className={`btn ${decision.action === "approve" ? "btn-success" : "btn-outline-success"}`} aria-pressed={decision.action === "approve"}
                                    disabled={submitting} onClick={() => updateDecision(iso, { action: "approve" })}>
                                    <i className="bi bi-check-lg me-1" aria-hidden="true" />Approve
                                  </button>
                                  <button type="button" className={`btn ${decision.action === "reject" ? "btn-danger" : "btn-outline-danger"}`} aria-pressed={decision.action === "reject"}
                                    disabled={submitting} onClick={() => updateDecision(iso, { action: "reject" })}>
                                    <i className="bi bi-x-lg me-1" aria-hidden="true" />Reject
                                  </button>
                                </div>
                                {decision.action === "approve" && (
                                  <>
                                    <select className={`form-select form-select-sm ${decision.final_status ? "" : "border-warning"}`} aria-label={`Final status for ${iso}`}
                                      value={decision.final_status} disabled={submitting} onChange={(e) => updateDecision(iso, { final_status: e.target.value })}>
                                      <option value="">Final status...</option>
                                      {(item.allowed_final_statuses || []).map((option) => <option key={option} value={option}>{option}</option>)}
                                    </select>
                                    {decision.final_status === "Permission" && (
                                      <div className="d-flex flex-wrap gap-2 mt-2">
                                        <label className="small mb-0">Permission start<input type="time" className="form-control form-control-sm" value={decision.permission_start} disabled={submitting} onChange={(e) => updateDecision(iso, { permission_start: e.target.value })} /></label>
                                        <label className="small mb-0">Permission end<input type="time" className="form-control form-control-sm" value={decision.permission_end} disabled={submitting} onChange={(e) => updateDecision(iso, { permission_end: e.target.value })} /></label>
                                        <label className="small mb-0 w-100">Reason<input type="text" className="form-control form-control-sm" value={decision.permission_reason} disabled={submitting} onChange={(e) => updateDecision(iso, { permission_reason: e.target.value })} /></label>
                                      </div>
                                    )}
                                    {decision.final_status === "Present" && (
                                      <div className="d-flex flex-wrap gap-2 mt-2">
                                        <label className="small mb-0">Final check-in
                                          <input type="time" className="form-control form-control-sm" value={decision.check_in} disabled={submitting}
                                            onChange={(e) => updateDecision(iso, { check_in: e.target.value })} />
                                        </label>
                                        <label className="small mb-0">Final check-out
                                          <input type="time" className="form-control form-control-sm" value={decision.check_out} disabled={submitting}
                                            onChange={(e) => updateDecision(iso, { check_out: e.target.value })} />
                                        </label>
                                      </div>
                                    )}
                                    {(item.option_notes || []).map((note) => <div key={note} className="small text-warning-emphasis mt-1">{note}</div>)}
                                  </>
                                )}
                                {decision.action && problem && <div className="small text-danger mt-1">{problem}</div>}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <>
                    <DateDecisionList dates={review.dates} />
                    <div className="small mt-3">
                      Reviewed {formatRequestDateTime(review.reviewed_at)} by {review.reviewed_by_name || "HR Admin"}
                      {review.review_comments && <div>Response remarks: {review.review_comments}</div>}
                    </div>
                  </>
                )}

                {review.can_respond && (
                  <>
                    <div className="small mt-2">
                      {problems.length ? <span className="text-warning-emphasis">{problems.length} date(s) still need a complete decision.</span> : <span className="text-success">Every date has a decision.</span>}
                    </div>
                    <label htmlFor="ar-comments" className="form-label mt-3">Response remarks {hasReject ? <span className="text-danger">(required when rejecting)</span> : "(optional)"}</label>
                    <textarea id="ar-comments" className="form-control" rows={2} maxLength={1000} value={comments} onChange={(e) => { setConfirming(false); setComments(e.target.value); }} disabled={submitting} />
                  </>
                )}
                {confirming && !reviewError && (
                  <div className="alert alert-warning py-2 mt-3 mb-0" role="alert">
                    <div className="fw-semibold mb-1">Confirm the final values for {review.request_number}:</div>
                    <ul className="mb-1 ps-3">
                      {review.dates.map((item) => <li key={item.attendance_date}>{formatRequestDate(item.attendance_date)} — {finalSummary(item)}</li>)}
                    </ul>
                    Approved dates update the employee&apos;s attendance and the employee will be notified. Click <strong>Confirm Submit</strong> to continue, or Cancel to discard.
                  </div>
                )}
                {reviewError && <div className="alert alert-danger py-2 mt-3 mb-0" role="alert">{reviewError}</div>}
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-light border" onClick={closeReview} disabled={submitting}>Cancel</button>
                {review.can_respond && (
                  <button type="button" className="btn btn-primary" onClick={submitResponse} disabled={submitting}>
                    {submitting ? "Submitting..." : confirming ? "Confirm Submit" : "Submit Response"}
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

export default AttendanceRequestInbox;
