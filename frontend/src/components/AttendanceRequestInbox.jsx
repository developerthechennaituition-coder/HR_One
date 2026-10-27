import { useCallback, useEffect, useRef, useState } from "react";
import {
  REQUEST_STATUS_BADGES,
  REQUEST_STATUS_LABELS,
  formatRequestDate,
  formatRequestDateTime,
  getAttendanceRequest,
  listAttendanceRequests,
  respondToAttendanceRequest,
} from "../services/attendanceRequestApi";
import { DateDecisionList } from "./MyAttendanceRequests";

const EMPTY_FILTERS = { status: "pending", employee: "", department: "", period: "", attendance_date: "" };

function StatusBadge({ value }) {
  return <span className={`badge ${REQUEST_STATUS_BADGES[value] || "text-bg-secondary"}`}>{REQUEST_STATUS_LABELS[value] || value}</span>;
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

  const openReview = async (requestId) => {
    setReviewLoading(true);
    setReviewError("");
    setSuccess("");
    setDecisions({});
    setComments("");
    setConfirming(false);
    try {
      setReview(await getAttendanceRequest(requestId));
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

  const undecided = review ? review.dates.filter((item) => !decisions[item.attendance_date]) : [];

  const submitResponse = async () => {
    if (undecided.length) {
      setReviewError(`Select a final status for every date. Missing: ${undecided.map((item) => formatRequestDate(item.attendance_date)).join(", ")}.`);
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
        review.dates.map((item) => ({ attendance_date: item.attendance_date, final_status: decisions[item.attendance_date] })),
        comments.trim(),
      );
      const { applied, not_applied: notApplied, notification_sent: notified, outdated_payroll_periods: outdated = [] } = result.summary;
      setSuccess(
        `${result.request_number} saved as ${REQUEST_STATUS_LABELS[result.status]}: ${applied} date(s) updated` +
        (notApplied ? `, ${notApplied} date(s) not applied (see details)` : "") +
        (notified ? ". The employee has been notified." : ". The employee notification could not be created.") +
        (outdated.length ? ` Calculated payroll for ${outdated.join(", ")} is now marked outdated — disapprove and recalculate it in Payroll.` : ""),
      );
      setReview(null);
      window.dispatchEvent(new Event("hrms-notifications-updated"));
      await loadRequests();
    } catch (submitError) {
      setConfirming(false);
      setReviewError(submitError.message);
      // Refresh in case the request was processed elsewhere.
      getAttendanceRequest(review.id).then(setReview).catch(() => {});
    } finally {
      setSubmitting(false);
    }
  };

  const applyFilters = (event) => {
    event.preventDefault();
    setAppliedFilters(filters);
  };

  return (
    <section ref={sectionRef} className="card border-0 shadow-sm mt-4" aria-label="Attendance requests inbox">
      <div className="card-body">
        <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-3">
          <div>
            <h4 className="mb-1">Attendance Requests</h4>
            <small className="text-muted">Review employee requests and decide every date individually.</small>
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
          <div className="col-6 col-md-1 d-flex gap-1">
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
                  <td><DateDecisionList dates={request.dates} /></td>
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
          <div className="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable">
            <div className="modal-content">
              <div className="modal-header">
                <h5 className="modal-title" id="ar-review-title">
                  {review.request_number} · {review.employee_name} ({review.employee_id}) <StatusBadge value={review.status} />
                </h5>
                <button type="button" className="btn-close" aria-label="Close" onClick={closeReview} disabled={submitting} />
              </div>
              <div className="modal-body">
                <div className="row g-2 small mb-3">
                  <div className="col-md-3"><span className="text-muted">Department</span><div>{review.department || "—"}</div></div>
                  <div className="col-md-3"><span className="text-muted">Submitted</span><div>{formatRequestDateTime(review.submitted_at)}</div></div>
                  <div className="col-md-6"><span className="text-muted">Employee remarks</span><div>{review.employee_remarks || "—"}</div></div>
                </div>
                <div className="table-responsive border rounded">
                  <table className="table table-sm align-middle mb-0">
                    <thead className="table-light">
                      <tr><th>Date</th><th>Current status</th><th>Check-in / out</th><th>Category</th><th>Requested</th><th>Payroll / eligibility</th><th>Final decision</th></tr>
                    </thead>
                    <tbody>
                      {review.dates.map((item) => {
                        const decided = Boolean(decisions[item.attendance_date]);
                        return (
                          <tr key={item.attendance_date} className={review.can_respond && !decided ? "table-warning" : ""}>
                            <td className="fw-semibold">{formatRequestDate(item.attendance_date)}</td>
                            <td>{item.current_status}</td>
                            <td>{item.current_check_in_time || "—"} / {item.current_check_out_time || "—"}</td>
                            <td>{item.current_attendance_category || "—"}</td>
                            <td>{item.requested_status}</td>
                            <td className="small">
                              {item.payroll_status ? `Payroll ${item.payroll_status}` : "No payroll yet"}
                              {item.eligibility_message && <div className="text-danger">{item.eligibility_message}</div>}
                              {item.payroll_note && <div className="text-warning-emphasis">{item.payroll_note}</div>}
                            </td>
                            <td style={{ minWidth: 150 }}>
                              {review.can_respond ? (
                                <select className={`form-select form-select-sm ${decided ? "" : "border-warning"}`} aria-label={`Final status for ${item.attendance_date}`}
                                  value={decisions[item.attendance_date] || ""} disabled={submitting}
                                  onChange={(e) => { setConfirming(false); setDecisions({ ...decisions, [item.attendance_date]: e.target.value }); }}>
                                  <option value="">Select...</option>
                                  <option value="Present">Present</option>
                                  <option value="Holiday">Holiday</option>
                                  <option value="Absent">Absent</option>
                                </select>
                              ) : (
                                <span>{item.final_status || "—"}{item.message && <div className="small text-muted">{item.message}</div>}</span>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                {review.can_respond ? (
                  <>
                    <div className="small mt-2">{undecided.length ? <span className="text-warning-emphasis">{undecided.length} date(s) still need a decision.</span> : <span className="text-success">Every date has a decision.</span>}</div>
                    <label htmlFor="ar-comments" className="form-label mt-3">Review comments (optional)</label>
                    <textarea id="ar-comments" className="form-control" rows={2} maxLength={1000} value={comments} onChange={(e) => setComments(e.target.value)} disabled={submitting} />
                  </>
                ) : (
                  <div className="small mt-3">
                    Reviewed {formatRequestDateTime(review.reviewed_at)} by {review.reviewed_by_name || "HR Admin"}
                    {review.review_comments && <div>Comments: {review.review_comments}</div>}
                  </div>
                )}
                {confirming && !reviewError && (
                  <div className="alert alert-warning py-2 mt-3 mb-0" role="alert">
                    Confirm: attendance will be saved for every eligible date in {review.request_number} and the employee will be notified. Click <strong>Confirm Submit</strong> to continue, or Cancel to discard.
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
