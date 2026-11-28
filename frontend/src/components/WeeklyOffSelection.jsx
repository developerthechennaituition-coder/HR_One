import { useCallback, useEffect, useMemo, useState } from "react";
import { HRONE_API_BASE } from "../config/api.js";

const API_URL = `${HRONE_API_BASE}/api/attendance-requests/weekly-offs`;

function getToken() {
  try {
    const session = JSON.parse(sessionStorage.getItem("hrms_session") || "null");
    return session?.token || session?.access_token || "";
  } catch {
    return "";
  }
}

async function apiRequest(path, options = {}) {
  const token = getToken();
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  });
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) {
    throw new Error(data?.detail || "Unable to complete the Weekly Off request.");
  }
  return data;
}

function dateLabel(value) {
  if (!value) return "—";
  return new Date(`${value}T00:00:00`).toLocaleDateString("en-IN", {
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function statusClass(status) {
  const value = String(status || "").toLowerCase();
  if (value === "approved") return "badge bg-success";
  if (value === "rejected") return "badge bg-danger";
  if (value === "cancelled") return "badge bg-secondary";
  return "badge bg-warning text-dark";
}

function displayStatus(request) {
  if (request?.cancellation?.status === "pending") return "Cancellation Pending";
  if (request?.status === "pending") {
    return request?.approval_stage === "hr_admin"
      ? "Pending HR Admin Approval"
      : "Pending Reporting Manager Approval";
  }
  return String(request?.status || "Unknown").replace(/_/g, " ");
}

function WeeklyOffSelection({ user }) {
  const isHrAdmin = ["hr admin", "administrator"].includes(String(user?.role || "").trim().toLowerCase());
  const [data, setData] = useState(null);
  const [pending, setPending] = useState([]);
  const [selectedDates, setSelectedDates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [processingId, setProcessingId] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const loadData = useCallback(async () => {
    try {
      const [employeeData, pendingData] = await Promise.all([
        apiRequest("/my"),
        apiRequest("/pending"),
      ]);
      setData(employeeData);
      setPending(Array.isArray(pendingData) ? pendingData : []);
    } catch (requestError) {
      setError(requestError.message || "Unable to load Weekly Off selections.");
    } finally {
      setLoading(false);
    }
  }, [isHrAdmin]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const monthEntitlements = useMemo(
    () => new Map((data?.monthly_entitlements || []).map((item) => [item.period, item])),
    [data?.monthly_entitlements],
  );
  const selectedPerMonth = useMemo(() => selectedDates.reduce((counts, iso) => {
    const period = iso.slice(0, 7);
    counts[period] = (counts[period] || 0) + 1;
    return counts;
  }, {}), [selectedDates]);

  const toggleDate = (item) => {
    setError("");
    if (selectedDates.includes(item.attendance_date)) {
      setSelectedDates(selectedDates.filter((value) => value !== item.attendance_date));
      return;
    }
    const monthRemaining = monthEntitlements.get(item.period)?.remaining || 0;
    if ((selectedPerMonth[item.period] || 0) >= monthRemaining) {
      setError(`No remaining Weekly Off entitlement for ${item.period}.`);
      return;
    }
    setSelectedDates([...selectedDates, item.attendance_date].sort());
  };

  const submitSelections = async (event) => {
    event.preventDefault();
    if (!selectedDates.length) {
      setError("Select at least one eligible date.");
      return;
    }
    setSubmitting(true);
    setError("");
    setSuccess("");
    try {
      const saved = await apiRequest("", {
        method: "POST",
        body: JSON.stringify({ attendance_dates: selectedDates }),
      });
      setSelectedDates([]);
      const requests = Array.isArray(saved?.requests) ? saved.requests : [];
      const hasHrAdminRequest = requests.some((request) => request.approval_stage === "hr_admin");
      const hasManagerRequest = requests.some((request) => request.approval_stage === "reporting_manager");
      const approvalMessage = hasHrAdminRequest && !hasManagerRequest
        ? "Your Weekly Off dates have been submitted successfully and are pending HR Admin approval because the selected date is immediately before or after an approved holiday or requires HR review."
        : hasHrAdminRequest
          ? "Your Weekly Off dates have been submitted successfully. Requests are pending approval from the assigned Reporting Manager or HR Admin."
          : "Your Weekly Off dates have been submitted successfully and are pending Reporting Manager approval.";
      setSuccess(
        saved?.notification_sent === false
          ? `${approvalMessage} The notification could not be delivered; the request remains in the approver's queue.`
          : approvalMessage,
      );
      await loadData();
    } catch (requestError) {
      setError(requestError.message || "Unable to save Weekly Off selections.");
    } finally {
      setSubmitting(false);
    }
  };

  const cancelSelection = async (request) => {
    const approved = request.status === "approved";
    const needsApproval = approved;
    const confirmation = needsApproval
      ? "Submit this Weekly Off cancellation to the person who originally approved it?"
      : approved
        ? "Submit a cancellation request for this approved Weekly Off?"
        : "Cancel this pending Weekly Off request?";
    if (!window.confirm(confirmation)) return;
    setProcessingId(request.id);
    setError("");
    setSuccess("");
    try {
      const saved = await apiRequest(`/my/${request.id}`, { method: "DELETE" });
      setSuccess(needsApproval
        ? `Cancellation request for ${dateLabel(request.attendance_date)} has been submitted to the original approving authority.`
        : `Weekly Off request for ${dateLabel(request.attendance_date)} cancelled.`);
      if (needsApproval && saved?.notification_sent === false) {
        setSuccess("Cancellation request saved, but the original approver could not be notified. It remains in the approval queue.");
      }
      await loadData();
      if (saved?.cancellation?.status === "pending") setSuccess("Cancellation request is waiting for approval from the original approving authority.");
    } catch (requestError) {
      setError(requestError.message || "Unable to cancel this Weekly Off selection.");
    } finally {
      setProcessingId("");
    }
  };

  const decide = async (request, action, cancellation = false) => {
    const title = cancellation ? "cancellation" : "Weekly Off request";
    const remarks = window.prompt(`${action === "approve" ? "Approval" : "Rejection"} remarks for this ${title} (optional):`, "");
    if (remarks === null) return;
    setProcessingId(request.id);
    setError("");
    setSuccess("");
    try {
      await apiRequest(`/${request.id}/${cancellation ? "cancellation-decision" : "decision"}`, {
        method: "PUT",
        body: JSON.stringify({ action, remarks: remarks.trim() || null }),
      });
      setSuccess(`${cancellation ? "Cancellation" : "Weekly Off request"} ${action === "approve" ? "approved" : "rejected"}.`);
      await loadData();
    } catch (requestError) {
      setError(requestError.message || `Unable to process the ${title}.`);
    } finally {
      setProcessingId("");
    }
  };

  return (
    <section aria-labelledby="weekly-off-heading">
      <div className="mb-3">
        <h3 id="weekly-off-heading" className="h4 mb-1">Weekly Off Selection</h3>
        <p className="text-muted mb-0">Choose from today through {dateLabel(data?.last_date)}. You can select dates within your remaining monthly entitlement.</p>
      </div>
      {error && <div className="alert alert-danger" role="alert">{error}</div>}
      {success && <div className="alert alert-success" role="status">{success}</div>}
      {loading ? (
        <div className="card border-0 shadow-sm"><div className="card-body text-center py-5"><span className="spinner-border spinner-border-sm me-2" role="status" />Loading Weekly Off details...</div></div>
      ) : (
        <>
          <div className="row g-3 mb-4">
            {(data?.monthly_entitlements || []).map((item) => (
              <div className="col-12 col-sm-6 col-lg-4" key={item.period}>
                <div className="card border-0 shadow-sm h-100"><div className="card-body">
                  <div className="d-flex justify-content-between align-items-start gap-2">
                    <strong>{new Date(`${item.period}-01T00:00:00`).toLocaleDateString("en-IN", { month: "long", year: "numeric" })}</strong>
                    <span className="badge text-bg-primary">{item.remaining} remaining</span>
                  </div>
                  <div className="small text-muted mt-2">{item.used} of {item.limit} Weekly Off day(s) used</div>
                  {item.payroll_locked && <div className="small text-danger mt-2">Payroll is {item.payroll_status}; selections are locked.</div>}
                </div></div>
              </div>
            ))}
          </div>

          <div className="card border-0 shadow-sm mb-4">
            <div className="card-header bg-white"><h4 className="h5 mb-0">Select dates</h4></div>
            <div className="card-body">
              <form onSubmit={submitSelections}>
                <div className="row g-2">
                  {(data?.eligible_dates || []).map((item) => {
                    const checked = selectedDates.includes(item.attendance_date);
                    const remaining = monthEntitlements.get(item.period)?.remaining || 0;
                    const monthlyLimitReached = !checked && (selectedPerMonth[item.period] || 0) >= remaining;
                    const disabled = !item.can_select || (monthlyLimitReached && remaining > 0) || submitting;
                    return (
                      <div className="col-12 col-sm-6 col-lg-4" key={item.attendance_date}>
                        <label className={`border rounded p-3 h-100 d-flex gap-2 align-items-start ${disabled ? "bg-light text-muted" : "cursor-pointer"}`}>
                          <input
                            className="form-check-input mt-1 flex-shrink-0"
                            type="checkbox"
                            checked={checked}
                            disabled={disabled}
                            onChange={() => toggleDate(item)}
                            aria-label={`Select Weekly Off for ${dateLabel(item.attendance_date)}`}
                          />
                          <span className="flex-grow-1">
                            <span className="fw-semibold d-block">{dateLabel(item.attendance_date)}</span>
                            <span className="small d-block">Attendance: {item.current_status}</span>
                            {item.can_select && <span className="small text-warning-emphasis d-block">{item.approval_reason ? "Requires HR Admin approval" : "Requires Reporting Manager approval"}</span>}
                            {!item.can_select && <span className="small text-danger d-block">{item.reason}</span>}
                          </span>
                        </label>
                      </div>
                    );
                  })}
                </div>
                <div className="d-flex flex-wrap align-items-center gap-3 mt-3">
                  <button type="submit" className="btn btn-primary" disabled={submitting || selectedDates.length === 0}>
                    {submitting ? <><span className="spinner-border spinner-border-sm me-2" role="status" />Saving...</> : "Save Weekly Off dates"}
                  </button>
                  <span className="small text-muted">Normal Weekly Off requests require Reporting Manager approval. Weekly Off requests selected one day before or after an approved holiday require HR Admin approval. Cancellation requests must be approved by the person who originally approved the Weekly Off.</span>
                </div>
              </form>
            </div>
          </div>

          <div className="card border-0 shadow-sm mb-4">
            <div className="card-header bg-white"><h4 className="h5 mb-0">My Weekly Off selections and requests</h4></div>
            <div className="card-body">
              {(data?.requests || []).length ? <div className="weekly-off-scroll-list" aria-label="My Weekly Off selections and requests">
                {data.requests.map((request) => {
                    const cancellationPending = request.cancellation?.status === "pending";
                    const canCancel = ["pending", "approved"].includes(request.status) && !cancellationPending;
                    return <article className="weekly-off-request-card" key={request.id}>
                      <div className="weekly-off-request-card__details">
                        <div className="fw-semibold">{dateLabel(request.attendance_date)}</div>
                        <div className="small text-muted">Attendance at selection: {request.original_status}</div>
                        {request.previous_weekly_off_date && <div className="small text-muted mt-1">Previous Weekly Off: {dateLabel(request.previous_weekly_off_date)}</div>}
                        <div className="mt-2"><span className={statusClass(request.status)}>{displayStatus(request)}</span>{cancellationPending && <span className="small text-warning-emphasis ms-2">Cancellation pending original approver approval</span>}</div>
                        {request.cancellation?.status && !cancellationPending && <div className="small text-muted mt-1">Cancellation {request.cancellation.status}{request.cancellation.reviewed_by_name ? ` by ${request.cancellation.reviewed_by_name}` : ""}</div>}
                        <div className="small text-muted mt-2">{request.approval_reason || request.review_comments || request.cancellation?.remarks || (request.approval_stage === "hr_admin" ? "Pending HR Admin approval" : "Pending Reporting Manager approval")}</div>
                      </div>
                      {canCancel && <div className="weekly-off-request-card__actions">
                        <button className="btn btn-sm btn-outline-danger" disabled={processingId === request.id} onClick={() => cancelSelection(request)}>{processingId === request.id ? "Processing..." : request.status === "approved" ? "Request cancellation" : "Cancel selection"}</button>
                      </div>}
                    </article>;
                  })}
              </div> : <div className="text-center text-muted py-4">No Weekly Off selections or requests yet.</div>}
            </div>
          </div>

          {(isHrAdmin || pending.length > 0) && <div className="card border-0 shadow-sm">
            <div className="card-header bg-white d-flex flex-wrap justify-content-between align-items-center gap-2">
              <div><h4 className="h5 mb-0">My Weekly Off approval queue</h4><small className="text-muted">Review Weekly Off requests and cancellation requests assigned to you.</small></div>
              <span className="badge text-bg-warning">{pending.length} awaiting review</span>
            </div>
            <div className="card-body">
              {pending.length ? <div className="weekly-off-scroll-list weekly-off-scroll-list--queue" aria-label="My Weekly Off approval queue">
                {pending.map((request) => {
                    const cancellation = request.status === "approved" && request.cancellation?.status === "pending";
                    return <article className="weekly-off-request-card" key={request.id}>
                      <div className="weekly-off-request-card__details">
                        <div className="fw-semibold">{request.employee_name || "Employee"} <span className="text-muted fw-normal">({request.employee_id})</span></div>
                        <div className="small mt-1">{dateLabel(request.attendance_date)} · Current attendance: {request.original_status}</div>
                        {request.previous_weekly_off_date && <div className="small text-muted mt-1">Previous Weekly Off: {dateLabel(request.previous_weekly_off_date)}</div>}
                        <div className="small text-muted mt-2">{cancellation ? "Cancellation request from original approver" : request.approval_reason || `Pending ${request.approval_stage === "hr_admin" ? "HR Admin" : "Reporting Manager"} approval`} · {request.request_number}</div>
                      </div>
                      <div className="weekly-off-request-card__actions"><div className="d-flex flex-wrap gap-2">
                        <button className="btn btn-sm btn-success" disabled={processingId === request.id} onClick={() => decide(request, "approve", cancellation)}>{cancellation ? "Approve cancellation" : "Approve"}</button>
                        <button className="btn btn-sm btn-outline-danger" disabled={processingId === request.id} onClick={() => decide(request, "reject", cancellation)}>{cancellation ? "Reject cancellation" : "Reject"}</button>
                      </div></div>
                    </article>;
                  })}
              </div> : <div className="text-center text-muted py-4">No Weekly Off requests are waiting for review.</div>}
            </div>
          </div>}
        </>
      )}
    </section>
  );
}

export default WeeklyOffSelection;
