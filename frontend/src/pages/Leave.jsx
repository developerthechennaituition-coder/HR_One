import { HRONE_API_BASE } from "../config/api.js";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  deleteLeaveNotification,
  markLeaveNotificationRead,
  starLeaveNotification,
  unstarLeaveNotification,
} from "../services/notificationApi";

const API_URL = HRONE_API_BASE + "/api/leave";

async function fileToBase64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return window.btoa(binary);
}

function getSession() {
  try {
    return JSON.parse(sessionStorage.getItem("hrms_session") || "null");
  } catch {
    return null;
  }
}

async function apiRequest(path, options = {}) {
  const session = getSession();
  const token = session?.token || "";
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  });
  let data = null;
  try { data = await response.json(); } catch { data = null; }
  if (!response.ok) {
    if (response.status === 401) throw new Error("Your session has expired. Please login again.");
    throw new Error(data?.detail || data?.message || "Unable to complete the request.");
  }
  return data;
}

function formatDate(value) {
  if (!value) return "-";
  const parsed = new Date(`${value}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

function formatDateTime(value) {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "" : parsed.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  });
}

function badgeClass(status) {
  const value = String(status || "").toLowerCase();
  if (value === "approved") return "badge bg-success";
  if (value === "rejected") return "badge bg-danger";
  if (value === "cancelled") return "badge bg-secondary";
  if (value === "pending_hr_admin") return "badge bg-info text-dark";
  return "badge bg-warning text-dark";
}

function policyFromApi(policy, id) {
  return {
    leave_type_id: policy.leave_type_id || id,
    annual_entitlement: policy.annual_entitlement ?? 0,
    carry_forward_allowed: Boolean(policy.carry_forward_allowed),
    max_carry_forward: policy.max_carry_forward ?? 0,
    accrual_enabled: Boolean(policy.accrual_enabled),
    accrual_frequency: policy.accrual_frequency || "annual",
    is_active: policy.is_active !== false,
  };
}

function typeFromApi(type) {
  return {
    id: type.id,
    code: type.code || "",
    name: type.name || "",
    description: type.description || "",
    is_paid: type.is_paid !== false,
    allow_half_day: type.allow_half_day !== false,
    requires_attachment: Boolean(type.requires_attachment),
    is_active: type.is_active !== false,
  };
}

function StatCard({ title, value, subtitle, icon }) {
  return (
    <div className="col-sm-6 col-xl-3">
      <div className="card border-0 shadow-sm h-100">
        <div className="card-body">
          <div className="d-flex justify-content-between">
            <div><div className="text-muted small">{title}</div><div className="fs-3 fw-semibold">{value}</div><div className="small text-muted">{subtitle}</div></div>
            <i className={`bi ${icon} fs-3 text-primary`} />
          </div>
        </div>
      </div>
    </div>
  );
}

function Leave({ user, onNavigate }) {
  const currentYear = new Date().getFullYear();
  const role = String(user?.role || "").trim().toLowerCase();
  const isAdminApprover = ["hr admin", "administrator", "hr manager"].includes(role);
  const isHrAdmin = role === "hr admin";
  const [canViewApprovalQueue, setCanViewApprovalQueue] = useState(false);
  const [approvalRole, setApprovalRole] = useState("");
  const isApprover = isAdminApprover || canViewApprovalQueue;

  const [activeTab, setActiveTab] = useState("dashboard");
  const [leaveTypes, setLeaveTypes] = useState([]);
  const [balances, setBalances] = useState([]);
  const [requests, setRequests] = useState([]);
  const [pendingRequests, setPendingRequests] = useState([]);
  const [permissionRequests, setPermissionRequests] = useState([]);
  const [pendingPermissions, setPendingPermissions] = useState([]);
  const [permissionDate, setPermissionDate] = useState("");
  const [permissionStart, setPermissionStart] = useState("09:00");
  const [permissionEnd, setPermissionEnd] = useState("10:00");
  const [permissionReason, setPermissionReason] = useState("");
  const [dashboard, setDashboard] = useState(null);
  const [report, setReport] = useState(null);
  const [notifications, setNotifications] = useState([]);
  const [reportYear, setReportYear] = useState(currentYear);
  const [reportMonth, setReportMonth] = useState("");
  const [loading, setLoading] = useState(true);
  const [processingId, setProcessingId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [savingSetup, setSavingSetup] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const [leaveTypeId, setLeaveTypeId] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [dayPart, setDayPart] = useState("full_day");
  const [reason, setReason] = useState("");
  const [attachmentName, setAttachmentName] = useState("");
  const [attachmentFile, setAttachmentFile] = useState(null);

  const [setupType, setSetupType] = useState({ id: "", code: "", name: "", description: "", is_paid: true, allow_half_day: true, requires_attachment: false, is_active: true });
  const [setupPolicy, setSetupPolicy] = useState({ leave_type_id: "", annual_entitlement: 0, carry_forward_allowed: false, max_carry_forward: 0, accrual_enabled: false, accrual_frequency: "annual", is_active: true });
  const [companyPolicy, setCompanyPolicy] = useState({
    weekly_offs_per_month: 4,
    weekly_off_weekdays: [],
    probation_period_months: 3,
    sick_leave_after_probation: 1,
    sick_leave_before_probation_allowed: false,
    one_day_approver: "reporting_manager",
    continuous_two_day_leave_allowed: false,
    more_than_two_days_approver: "hr_admin",
    medical_certificate_required: true,
    religion_holidays_enabled: true,
  });

  // Remember which leave type is open in Leave Setup without re-triggering loadData.
  const setupIdRef = useRef("");

  const loadPolicy = useCallback(async (id) => {
    if (!id) return;
    try {
      const policy = await apiRequest(`/policies/${id}`);
      setSetupPolicy(policyFromApi(policy, id));
    } catch {
      /* policy may not exist yet - keep current values */
    }
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [types, balanceData, requestData, dashboardData, notificationData, companyPolicyData] = await Promise.all([
        apiRequest("/types"),
        apiRequest(`/balance?year=${currentYear}`),
        apiRequest("/my-requests"),
        apiRequest(`/dashboard?year=${currentYear}`),
        apiRequest("/notifications"),
        apiRequest("/company-policy"),
      ]);
      setLeaveTypes(types || []);
      setBalances(balanceData || []);
      setRequests(requestData || []);
      setDashboard(dashboardData || null);
      setNotifications(Array.isArray(notificationData) ? notificationData : notificationData?.notifications || []);
      setCompanyPolicy((current) => ({ ...current, ...companyPolicyData }));
      try {
        const [myPermissions, managerPermissions] = await Promise.all([
          apiRequest("/permissions/my-requests"),
          apiRequest("/permissions/pending"),
        ]);
        setPermissionRequests(Array.isArray(myPermissions) ? myPermissions : []);
        setPendingPermissions(managerPermissions?.requests || []);
      } catch {
        setPermissionRequests([]);
        setPendingPermissions([]);
      }

      if (types?.length) {
        setLeaveTypeId((current) => current || types[0].id);
        if (!setupIdRef.current) {
          setupIdRef.current = types[0].id;
          setSetupType(typeFromApi(types[0]));
          setSetupPolicy((current) => ({ ...current, leave_type_id: types[0].id }));
        }
      }
      // Approval Queue is available to HR/Admin roles and to employees who
      // are assigned as Reporting Managers. The backend determines the
      // current user's approval scope and returns only authorized requests.
      try {
        const pendingData = await apiRequest("/pending");
        if (Array.isArray(pendingData)) {
          // Backward compatibility with the older API response shape.
          setPendingRequests(pendingData);
          setCanViewApprovalQueue(isAdminApprover && pendingData.length > 0);
          setApprovalRole(isAdminApprover ? "admin" : "");
        } else {
          setPendingRequests(pendingData?.requests || []);
          setCanViewApprovalQueue(Boolean(pendingData?.can_approve));
          setApprovalRole(pendingData?.approval_role || "");
        }
      } catch (pendingError) {
        setPendingRequests([]);
        setCanViewApprovalQueue(false);
        setApprovalRole("");
        throw pendingError;
      }

      if (isAdminApprover) {
        await loadPolicy(setupIdRef.current || types?.[0]?.id);
      }
    } catch (requestError) {
      setError(requestError.message || "Unable to load Leave module.");
    } finally {
      setLoading(false);
    }
  }, [currentYear, isAdminApprover, loadPolicy]);

  const refreshLeaveNotifications = useCallback(async () => {
    const [notificationData, pendingData] = await Promise.all([
      apiRequest("/notifications"),
      apiRequest("/pending"),
    ]);
    return { notificationData, pendingData };
  }, []);

  useEffect(() => { loadData(); }, [loadData]);
  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const { notificationData, pendingData } = await refreshLeaveNotifications();
        if (!cancelled) {
          setNotifications(Array.isArray(notificationData) ? notificationData : notificationData?.notifications || []);
          setPendingRequests(pendingData?.requests || []);
          setCanViewApprovalQueue(Boolean(pendingData?.can_approve));
          setApprovalRole(pendingData?.approval_role || "");
        }
      } catch (requestError) {
        if (!cancelled) {
          console.error("Unable to refresh Leave notifications:", requestError);
          setError(requestError.message || "Unable to refresh Leave notifications.");
        }
      }
    };
    const intervalId = window.setInterval(refresh, 10000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [refreshLeaveNotifications]);

  const selectedBalance = useMemo(() => balances.find((item) => item.leave_type_id === leaveTypeId), [balances, leaveTypeId]);
  const selectedType = useMemo(() => leaveTypes.find((item) => item.id === leaveTypeId), [leaveTypes, leaveTypeId]);
  const selectedSetupType = useMemo(() => leaveTypes.find((item) => item.id === setupType.id), [leaveTypes, setupType.id]);
  const isMedicalLeaveType = ["sl", "medical"].includes(String(selectedType?.code || "").toLowerCase()) || String(selectedType?.name || "").toLowerCase().includes("medical");
  const requiresMedicalCertificate = Boolean(companyPolicy.medical_certificate_required && isMedicalLeaveType);
  const unreadLeaveNotificationCount = notifications.filter((item) => !item.is_read).length;

  const handleFromDateChange = (value) => {
    setFromDate(value);
    // Half-day leave is always a single date.
    if (dayPart !== "full_day") setToDate(value);
  };

  const handleDayPartChange = (value) => {
    setDayPart(value);
    if (value !== "full_day" && fromDate) setToDate(fromDate);
  };

  const handleApply = async (event) => {
    event.preventDefault(); setError(""); setSuccess("");
    if (!leaveTypeId || !fromDate || !toDate || !reason.trim()) return setError("Please fill all required leave application fields.");
    if (toDate < fromDate) return setError("To date must be on or after From date.");
    if (dayPart !== "full_day" && !selectedType?.allow_half_day) return setError("Half-day leave is not allowed for this leave type.");
    if (dayPart !== "full_day" && fromDate !== toDate) return setError("Half-day leave can be applied for a single date only.");
    if (selectedType?.requires_attachment && !attachmentName.trim()) return setError("Attachment reference is required for this leave type.");
    if (requiresMedicalCertificate && !attachmentFile) return setError("Upload a medical certificate PDF, PNG or JPEG file before submitting.");
    if (attachmentFile && (attachmentFile.size > 5 * 1024 * 1024 || !["application/pdf", "image/png", "image/jpeg"].includes(attachmentFile.type))) return setError("Medical certificate must be a PDF, PNG or JPEG file smaller than 5 MB.");
    setSubmitting(true);
    try {
      const attachmentContent = attachmentFile ? await fileToBase64(attachmentFile) : null;
      await apiRequest("/apply", { method: "POST", body: JSON.stringify({ leave_type_id: leaveTypeId, from_date: fromDate, to_date: toDate, day_part: dayPart, reason: reason.trim(), attachment_name: attachmentFile?.name || attachmentName.trim() || null, attachment_content_base64: attachmentContent, attachment_content_type: attachmentFile?.type || null }) });
      window.dispatchEvent(new Event("hrms-notifications-updated"));
      setSuccess("Leave application submitted successfully."); setFromDate(""); setToDate(""); setReason(""); setAttachmentName(""); setAttachmentFile(null); setDayPart("full_day"); await loadData();
    } catch (requestError) { setError(requestError.message || "Unable to submit leave application."); }
    finally { setSubmitting(false); }
  };

  const submitPermission = async (event) => {
    event.preventDefault();
    setError(""); setSuccess("");
    if (!permissionDate || !permissionReason.trim()) { setError("Select a permission date and enter a reason."); return; }
    if (permissionEnd <= permissionStart) { setError("End time must be later than start time."); return; }
    setSubmitting(true);
    try {
      await apiRequest("/permissions", { method: "POST", body: JSON.stringify({ permission_date: permissionDate, start_time: permissionStart, end_time: permissionEnd, reason: permissionReason.trim() }) });
      window.dispatchEvent(new Event("hrms-notifications-updated"));
      setSuccess("Permission request submitted to your reporting manager.");
      setPermissionReason(""); await loadData();
    } catch (requestError) { setError(requestError.message || "Unable to submit permission request."); }
    finally { setSubmitting(false); }
  };

  const decidePermission = async (requestId, decision) => {
    const remarks = window.prompt(`${decision === "approve" ? "Approval" : "Rejection"} remarks (optional):`, "");
    if (remarks === null) return;
    setProcessingId(requestId); setError(""); setSuccess("");
    try {
      await apiRequest(`/permissions/${requestId}/${decision}`, { method: "PUT", body: JSON.stringify({ remarks: remarks.trim() || null }) });
      window.dispatchEvent(new Event("hrms-notifications-updated"));
      setSuccess(`Permission request ${decision === "approve" ? "approved" : "rejected"}.`); await loadData();
    } catch (requestError) { setError(requestError.message || "Unable to process permission request."); }
    finally { setProcessingId(""); }
  };

  const handleCancel = async (requestId) => {
    if (!window.confirm("Are you sure you want to cancel this leave request?")) return;
    setProcessingId(requestId); setError(""); setSuccess("");
    try { await apiRequest(`/requests/${requestId}/cancel`, { method: "PUT" }); window.dispatchEvent(new Event("hrms-notifications-updated")); setSuccess("Leave request cancelled successfully."); await loadData(); }
    catch (requestError) { setError(requestError.message || "Unable to cancel leave request."); await loadData(); }
    finally { setProcessingId(""); }
  };

  const downloadAttachment = async (request) => {
    const token = getSession()?.token;
    try {
      const response = await fetch(`${API_URL}/requests/${request.id}/attachment`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!response.ok) {
        let message = "Unable to download the leave attachment.";
        try {
          const data = await response.json();
          message = data?.detail || message;
        } catch {
          // Retain the explicit fallback for non-JSON error responses.
        }
        throw new Error(message);
      }
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = request.attachment_name || "leave-certificate";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (requestError) {
      setError(requestError.message || "Unable to download the leave attachment.");
    }
  };

  const handleDecision = async (requestId, decision) => {
    const remarks = window.prompt(`${decision === "approve" ? "Approval" : "Rejection"} remarks (optional):`, "");
    if (remarks === null) return;
    setProcessingId(requestId); setError(""); setSuccess("");
    try { await apiRequest(`/requests/${requestId}/${decision}`, { method: "PUT", body: JSON.stringify({ remarks: remarks.trim() || null }) }); window.dispatchEvent(new Event("hrms-notifications-updated")); setSuccess(`Leave request ${decision === "approve" ? "approved" : "rejected"} successfully.`); await loadData(); }
    catch (requestError) { setError(requestError.message || "Unable to process leave request."); }
    finally { setProcessingId(""); }
  };

  const loadReport = async () => {
    if (!isAdminApprover) return;
    setError("");
    try { setReport(await apiRequest(`/reports?year=${reportYear}${reportMonth ? `&month=${reportMonth}` : ""}`)); setActiveTab("reports"); }
    catch (requestError) { setError(requestError.message || "Unable to load leave report."); }
  };

  const downloadReport = async () => {
    const session = getSession();
    const response = await fetch(`${API_URL}/reports/csv?year=${reportYear}${reportMonth ? `&month=${reportMonth}` : ""}`, { headers: { Authorization: `Bearer ${session?.token || ""}` } });
    if (!response.ok) { setError("Unable to export leave report."); return; }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = `leave-report-${reportYear}${reportMonth ? `-${String(reportMonth).padStart(2, "0")}` : ""}.csv`; anchor.click(); URL.revokeObjectURL(url);
  };

  const handleTypeSelect = async (id) => {
    const selected = leaveTypes.find((item) => item.id === id);
    if (!selected) return;
    setupIdRef.current = id;
    setSetupType(typeFromApi(selected));
    setSetupPolicy((current) => ({ ...current, leave_type_id: id }));
    await loadPolicy(id);
  };

  const handleSaveType = async (event) => {
    event.preventDefault(); if (!setupType.id || !setupType.name.trim()) return setError("Select a leave type and enter a name.");
    setSavingSetup(true); setError(""); setSuccess("");
    try { await apiRequest(`/types/${setupType.id}`, { method: "PUT", body: JSON.stringify({ name: setupType.name.trim(), description: setupType.description.trim() || null, is_paid: setupType.is_paid, allow_half_day: setupType.allow_half_day, requires_attachment: setupType.requires_attachment, is_active: setupType.is_active }) }); setSuccess("Leave type settings updated successfully."); await loadData(); }
    catch (requestError) { setError(requestError.message || "Unable to update leave type."); }
    finally { setSavingSetup(false); }
  };

  const handleSavePolicy = async (event) => {
    event.preventDefault(); if (!setupPolicy.leave_type_id) return setError("Select a leave type for the policy.");
    setSavingSetup(true); setError(""); setSuccess("");
    try { await apiRequest(`/policies/${setupPolicy.leave_type_id}`, { method: "PUT", body: JSON.stringify({ annual_entitlement: Number(setupPolicy.annual_entitlement), carry_forward_allowed: setupPolicy.carry_forward_allowed, max_carry_forward: Number(setupPolicy.max_carry_forward), accrual_enabled: setupPolicy.accrual_enabled, accrual_frequency: setupPolicy.accrual_frequency, is_active: setupPolicy.is_active }) }); setSuccess("Leave policy updated successfully."); await loadData(); }
    catch (requestError) { setError(requestError.message || "Unable to update leave policy."); }
    finally { setSavingSetup(false); }
  };

  const handleSaveCompanyPolicy = async (event) => {
    event.preventDefault();
    setSavingSetup(true); setError(""); setSuccess("");
    try {
      const savedPolicy = await apiRequest("/company-policy", {
        method: "PUT",
        body: JSON.stringify({
          ...companyPolicy,
          weekly_offs_per_month: Number(companyPolicy.weekly_offs_per_month),
          probation_period_months: Number(companyPolicy.probation_period_months),
          sick_leave_after_probation: Number(companyPolicy.sick_leave_after_probation),
          one_day_approver: "reporting_manager",
          continuous_two_day_leave_allowed: true,
          more_than_two_days_approver: "hr_admin",
        }),
      });
      setCompanyPolicy((current) => ({ ...current, ...savedPolicy }));
      setSuccess("Company leave policy updated successfully.");
    } catch (requestError) {
      setError(requestError.message || "Unable to update company leave policy.");
    } finally {
      setSavingSetup(false);
    }
  };

  const handleLeaveNotificationAction = async (item, action) => {
    const notificationId = item.leave_notification_id;
    if (!notificationId) return;
    setProcessingId(notificationId);
    setError("");
    try {
      if (action === "read") {
        await markLeaveNotificationRead(notificationId);
      } else if (action === "star") {
        await starLeaveNotification(notificationId);
      } else if (action === "unstar") {
        await unstarLeaveNotification(notificationId);
      } else if (action === "delete") {
        await deleteLeaveNotification(notificationId);
      }
      await loadData();
    } catch (requestError) {
      setError(requestError.message || "Unable to update the leave notification.");
    } finally {
      setProcessingId("");
    }
  };

  const renderActionCell = (request, approval) => {
    if (approval) {
      return (
        <div className="d-flex justify-content-end gap-2">
          <button className="btn btn-sm btn-success" disabled={processingId === request.id} onClick={() => handleDecision(request.id, "approve")}>Approve</button>
          <button className="btn btn-sm btn-outline-danger" disabled={processingId === request.id} onClick={() => handleDecision(request.id, "reject")}>Reject</button>
        </div>
      );
    }
    if (!["pending", "approved"].includes(request.status)) return null;
    if (request.can_cancel) {
      return <button className="btn btn-sm btn-outline-danger" disabled={processingId === request.id} onClick={() => handleCancel(request.id)}>Cancel</button>;
    }
    return <span className="small text-muted">Cancel closed</span>;
  };

  const renderRequestsTable = (rows, approval = false) => (
    <div className="table-responsive">
      <table className="table table-hover align-middle mb-0">
        <thead className="table-light"><tr><th>Employee</th><th>Leave</th><th>Leave Dates</th><th>Applied On <span className="fw-normal text-muted">(IST)</span></th><th>Days</th><th>Reason</th><th>Status</th><th className="text-end">Action</th></tr></thead>
        <tbody>{rows.length === 0 ? <tr><td colSpan="8" className="text-center text-muted py-4">No leave requests found.</td></tr> : rows.map((request) => (
          <tr key={request.id}>
            <td>{approval ? <><strong>{request.employee_name || "Employee"}</strong><div className="small text-muted">{request.employee_id || "-"}</div></> : <strong>{request.employee_name || "You"}</strong>}</td>
            <td><strong>{request.leave_type_code}</strong><div className="small text-muted">{request.leave_type_name}</div></td>
            <td>{formatDate(request.from_date)} - {formatDate(request.to_date)}<div className="small text-muted">{request.day_part_label}</div></td>
            <td className="text-nowrap">{request.created_at ? <><div className="fw-medium">{formatDateTime(request.created_at).split(", ")[0]}</div><div className="small text-muted">{formatDateTime(request.created_at).split(", ").slice(1).join(", ")}</div></> : <span className="text-muted">—</span>}</td>
            <td>{Number(request.days).toFixed(1)}</td>
            <td>{request.reason}{request.attachment_name && <div><button type="button" className="btn btn-link btn-sm p-0" onClick={() => downloadAttachment(request)}><i className="bi bi-paperclip me-1" />{request.attachment_name}</button></div>}</td>
            <td>
              <span className={badgeClass(request.status)}>
                {request.status === "pending_hr_admin" ? "Pending HR Admin Approval" : request.status === "approved" ? "Approved" : request.status === "rejected" ? "Rejected" : request.status === "cancelled" ? "Cancelled" : "Pending Reporting Manager Approval"}
              </span>
              {approval && request.approval_stage === "reporting_manager" && (
                <div className="small text-muted mt-1">Reporting Manager Approval</div>
              )}
              {approval && request.approval_stage === "hr_admin" && (
                <div className="small text-muted mt-1">Final HR Admin Approval</div>
              )}
              {!approval && request.status === "pending_hr_admin" && (
                <div className="small text-muted mt-1">Reporting Manager approved. Awaiting HR Admin.</div>
              )}
              {!approval && request.can_cancel && request.cancel_deadline && (
                <div className="small text-muted mt-1">Cancel until {formatDateTime(request.cancel_deadline)}</div>
              )}
            </td>
            <td className="text-end">{renderActionCell(request, approval)}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );

  return (
    <div className="container-fluid py-3">
      <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-4">
        <div><h2 className="mb-1">Leave Management</h2></div>
        <button className="btn btn-outline-secondary" onClick={() => onNavigate?.("profile")}>My Profile</button>
      </div>
      {error && <div className="alert alert-danger">{error}</div>}
      {success && <div className="alert alert-success">{success}</div>}

      <div className="d-flex flex-wrap gap-2 mb-4">
        <button className={`btn ${activeTab === "dashboard" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setActiveTab("dashboard")}>Dashboard</button>
        <button className={`btn ${activeTab === "my-leave" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setActiveTab("my-leave")}>My Leave</button>
        <button className={`btn ${activeTab === "permissions" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setActiveTab("permissions")}>Permission {pendingPermissions.length > 0 && <span className="badge bg-danger ms-2">{pendingPermissions.length}</span>}</button>
        {isApprover && <>
          <button className={`btn ${activeTab === "approvals" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setActiveTab("approvals")}>Approval Queue {pendingRequests.length > 0 && <span className="badge bg-danger ms-2">{pendingRequests.length}</span>}</button>
          {isAdminApprover && <button className={`btn ${activeTab === "reports" ? "btn-primary" : "btn-outline-primary"}`} onClick={loadReport}>Reports</button>}
          {isAdminApprover && <button className={`btn ${activeTab === "setup" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setActiveTab("setup")}>Leave Setup</button>}
        </>}
        <button className={`btn ${activeTab === "notifications" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setActiveTab("notifications")}>Notifications {unreadLeaveNotificationCount > 0 && <span className="badge bg-danger ms-2">{unreadLeaveNotificationCount}</span>}</button>
      </div>

      {loading ? <div className="card border-0 shadow-sm"><div className="card-body text-center py-5">Loading Leave module...</div></div> : activeTab === "dashboard" ? (
        <>
          <div className="row g-3 mb-4">
            <StatCard title="Total Requests" value={dashboard?.summary?.total_requests ?? 0} subtitle="Selected year" icon="bi-file-earmark-text" />
            <StatCard title="Pending" value={dashboard?.summary?.pending ?? 0} subtitle="Awaiting decision" icon="bi-hourglass-split" />
            <StatCard title="Approved Days" value={dashboard?.summary?.approved_days ?? 0} subtitle="Approved leave" icon="bi-check2-circle" />
            <StatCard title="Total Days" value={dashboard?.summary?.total_days ?? 0} subtitle="All request days" icon="bi-calendar-check" />
          </div>
          <div className="row g-4">
            <div className="col-lg-7"><div className="card border-0 shadow-sm"><div className="card-header bg-white"><h5 className="mb-0">Leave Balance</h5></div><div className="card-body"><div className="row g-3">{balances.map((balance) => <div className="col-md-6" key={balance.leave_type_id}><div className="border rounded p-3"><div className="d-flex justify-content-between"><strong>{balance.code} - {balance.name}</strong><span className="badge bg-light text-dark">{balance.is_paid ? "Paid" : "LOP"}</span></div><div className="fs-4 fw-semibold mt-2">{Number(balance.available).toFixed(1)}</div><small className="text-muted">Available / {Number(balance.entitled).toFixed(1)} entitled</small><div className="small mt-2">Used {Number(balance.used).toFixed(1)} · Pending {Number(balance.pending).toFixed(1)} · Carry {Number(balance.carry_forward).toFixed(1)}</div></div></div>)}</div></div></div></div>
            <div className="col-lg-5"><div className="card border-0 shadow-sm h-100"><div className="card-header bg-white"><h5 className="mb-0">Leave Usage by Type</h5></div><div className="card-body">{Object.keys(dashboard?.days_by_leave_type || {}).length === 0 ? <div className="text-muted">No leave usage yet.</div> : Object.entries(dashboard.days_by_leave_type).map(([code, days]) => <div className="d-flex justify-content-between border-bottom py-2" key={code}><span>{code}</span><strong>{Number(days).toFixed(1)} day(s)</strong></div>)}</div></div></div>
          </div>
        </>
      ) : activeTab === "my-leave" ? (
        <>
          <div className="card border-0 shadow-sm mb-4"><div className="card-header bg-white"><h5 className="mb-0">Apply Leave</h5></div><div className="card-body"><form onSubmit={handleApply}><div className="row g-3">
            <div className="col-md-4"><label className="form-label">Leave Type</label><select className="form-select" value={leaveTypeId} onChange={(e) => { setLeaveTypeId(e.target.value); setDayPart("full_day"); setAttachmentFile(null); setAttachmentName(""); }} required><option value="">Select leave type</option>{leaveTypes.map((type) => <option key={type.id} value={type.id}>{type.code} - {type.name}</option>)}</select></div>
            <div className="col-md-4"><label className="form-label">From Date</label><input type="date" className="form-control" value={fromDate} onChange={(e) => handleFromDateChange(e.target.value)} required /></div>
            <div className="col-md-4"><label className="form-label">To Date</label><input type="date" className="form-control" value={toDate} min={fromDate || undefined} disabled={dayPart !== "full_day"} onChange={(e) => setToDate(e.target.value)} required /></div>
            <div className="col-md-4"><label className="form-label">Day Type</label><select className="form-select" value={dayPart} onChange={(e) => handleDayPartChange(e.target.value)} disabled={!selectedType?.allow_half_day}><option value="full_day">Full Day</option><option value="first_half">First Half</option><option value="second_half">Second Half</option></select></div>
            {requiresMedicalCertificate && <div className="col-md-4"><label className="form-label">Medical Certificate</label><input type="file" accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg" className="form-control" onChange={(e) => { const file = e.target.files?.[0] || null; setAttachmentFile(file); setAttachmentName(file?.name || ""); }} required /><small className="text-muted">PDF, PNG or JPEG, maximum 5 MB.</small></div>}
            {selectedType?.requires_attachment && !requiresMedicalCertificate && <div className="col-md-4"><label className="form-label">Attachment Reference</label><input className="form-control" value={attachmentName} onChange={(e) => setAttachmentName(e.target.value)} placeholder="File name / reference" required /></div>}
            <div className="col-12"><label className="form-label">Reason</label><textarea className="form-control" rows="3" maxLength="1000" value={reason} onChange={(e) => setReason(e.target.value)} required /></div>
            {selectedBalance && <div className="col-12"><div className="alert alert-light border mb-0"><strong>{selectedBalance.name}:</strong> {Number(selectedBalance.available).toFixed(1)} day(s) available. Carry forward: {Number(selectedBalance.carry_forward).toFixed(1)}.</div></div>}
            <div className="col-12"><div className="small text-muted">One-day requests need Reporting Manager approval. Requests for two or more days need Reporting Manager approval followed by HR Admin approval. Approved leave appears in Attendance and your Profile on the leave day itself.</div></div>
          </div><div className="mt-3"><button className="btn btn-primary" disabled={submitting}>{submitting ? "Submitting..." : "Submit Leave Request"}</button></div></form></div></div>
          <div className="card border-0 shadow-sm"><div className="card-header bg-white"><h5 className="mb-0">My Leave Requests</h5></div><div className="card-body p-0">{renderRequestsTable(requests, false)}</div></div>
        </>
      ) : activeTab === "permissions" ? (
        <>
          <div className="card border-0 shadow-sm mb-4"><div className="card-header bg-white"><h5 className="mb-0">Apply Permission</h5><small className="text-muted">Choose the date and exact time interval. Your reporting manager will be notified.</small></div><div className="card-body"><form onSubmit={submitPermission}><div className="row g-3">
            <div className="col-md-3"><label className="form-label">Permission Date</label><input type="date" className="form-control" min={new Date().toISOString().slice(0,10)} value={permissionDate} onChange={(e) => setPermissionDate(e.target.value)} required /></div>
            <div className="col-md-3"><label className="form-label">Start Time</label><input type="time" className="form-control" value={permissionStart} onChange={(e) => setPermissionStart(e.target.value)} required /></div>
            <div className="col-md-3"><label className="form-label">End Time</label><input type="time" className="form-control" value={permissionEnd} onChange={(e) => setPermissionEnd(e.target.value)} required /></div>
            <div className="col-md-12"><label className="form-label">Reason</label><textarea className="form-control" rows="2" maxLength="1000" value={permissionReason} onChange={(e) => setPermissionReason(e.target.value)} required /></div>
          </div><button className="btn btn-primary mt-3" disabled={submitting}>{submitting ? "Submitting..." : "Submit Permission"}</button></form></div></div>
          <div className="card border-0 shadow-sm mb-4"><div className="card-header bg-white"><h5 className="mb-0">My Permission Requests</h5></div><div className="card-body p-0"><div className="table-responsive"><table className="table table-hover align-middle mb-0"><thead className="table-light"><tr><th>Date</th><th>Time</th><th>Reason</th><th>Status</th></tr></thead><tbody>{permissionRequests.length ? permissionRequests.map((request) => <tr key={request.id}><td>{formatDate(request.permission_date)}</td><td>{request.start_time} – {request.end_time}</td><td>{request.reason}</td><td><span className={badgeClass(request.status)}>{request.status}</span>{request.decision_remarks && <div className="small text-muted">{request.decision_remarks}</div>}</td></tr>) : <tr><td colSpan="4" className="text-center text-muted py-4">No permission requests yet.</td></tr>}</tbody></table></div></div></div>
          {pendingPermissions.length > 0 && <div className="card border-0 shadow-sm"><div className="card-header bg-white"><h5 className="mb-0">Permission Approval Queue</h5></div><div className="card-body p-0"><div className="table-responsive"><table className="table table-hover align-middle mb-0"><thead className="table-light"><tr><th>Employee</th><th>Date</th><th>Time</th><th>Reason</th><th>Action</th></tr></thead><tbody>{pendingPermissions.map((request) => <tr key={request.id}><td>{request.employee_name}<div className="small text-muted">{request.employee_id || ""}</div></td><td>{formatDate(request.permission_date)}{request.request_type === "wfh_late_check_in" && <div><span className="badge bg-warning text-dark">WFH late check-in</span></div>}</td><td>{request.start_time} – {request.end_time}</td><td>{request.reason}</td><td className="text-nowrap"><button className="btn btn-sm btn-success me-2" disabled={processingId === request.id} onClick={() => decidePermission(request.id, "approve")}>Approve</button><button className="btn btn-sm btn-outline-danger" disabled={processingId === request.id} onClick={() => decidePermission(request.id, "reject")}>Reject</button></td></tr>)}</tbody></table></div></div></div>}
        </>
      ) : activeTab === "approvals" && isApprover ? (
        <div className="card border-0 shadow-sm"><div className="card-header bg-white d-flex justify-content-between"><div><h5 className="mb-0">Pending Leave Approvals</h5><small className="text-muted">Review, approve or reject employee leave.</small></div><span className="badge bg-primary">{pendingRequests.length} pending</span></div><div className="card-body p-0">{renderRequestsTable(pendingRequests, true)}</div></div>
      ) : activeTab === "reports" && isAdminApprover ? (
        <div className="card border-0 shadow-sm"><div className="card-header bg-white"><div className="row g-2 align-items-end"><div className="col-md-3"><label className="form-label">Year</label><input type="number" className="form-control" value={reportYear} onChange={(e) => setReportYear(e.target.value)} /></div><div className="col-md-3"><label className="form-label">Month</label><select className="form-select" value={reportMonth} onChange={(e) => setReportMonth(e.target.value)}><option value="">All Months</option>{Array.from({ length: 12 }, (_, i) => <option key={i + 1} value={i + 1}>{new Date(2000, i, 1).toLocaleString("en-IN", { month: "long" })}</option>)}</select></div><div className="col-md-auto"><button className="btn btn-primary" onClick={loadReport}>Load Report</button></div><div className="col-md-auto"><button className="btn btn-outline-success" onClick={downloadReport}>Export CSV</button></div></div></div><div className="card-body"><div className="row g-3 mb-4"><StatCard title="Requests" value={report?.totals?.requests ?? 0} subtitle="Report rows" icon="bi-files" /><StatCard title="Total Days" value={report?.totals?.days ?? 0} subtitle="Requested days" icon="bi-calendar3" /><StatCard title="Approved Days" value={report?.totals?.approved_days ?? 0} subtitle="Approved days" icon="bi-check-circle" /></div>{report ? <div className="table-responsive"><table className="table table-hover"><thead className="table-light"><tr><th>Employee</th><th>Leave</th><th>Dates</th><th>Days</th><th>Status</th></tr></thead><tbody>{report.rows.map((row) => <tr key={row.id}><td>{row.employee_name}<div className="small text-muted">{row.employee_id}</div></td><td>{row.leave_type_code}</td><td>{formatDate(row.from_date)} - {formatDate(row.to_date)}</td><td>{row.days}</td><td><span className={badgeClass(row.status)}>{row.status}</span></td></tr>)}</tbody></table></div> : <div className="text-muted">Select a period and load the report.</div>}</div></div>
      ) : activeTab === "setup" && isAdminApprover ? (
        <div className="row g-4">
          <div className="col-lg-6"><div className="card border-0 shadow-sm"><div className="card-header bg-white"><h5 className="mb-0">Leave Type Configuration</h5></div><div className="card-body"><form onSubmit={handleSaveType}><div className="mb-3"><label className="form-label">Leave Type</label><select className="form-select" value={setupType.id} onChange={(e) => handleTypeSelect(e.target.value)}><option value="">Select leave type</option>{leaveTypes.map((type) => <option key={type.id} value={type.id}>{type.code} - {type.name}</option>)}</select></div><div className="mb-3"><label className="form-label">Name</label><input className="form-control" value={setupType.name} onChange={(e) => setSetupType({ ...setupType, name: e.target.value })} /></div><div className="mb-3"><label className="form-label">Description</label><textarea className="form-control" rows="2" value={setupType.description} onChange={(e) => setSetupType({ ...setupType, description: e.target.value })} /></div><div className="form-check mb-2"><input className="form-check-input" type="checkbox" checked={setupType.is_paid} onChange={(e) => setSetupType({ ...setupType, is_paid: e.target.checked })} id="paid" /><label className="form-check-label" htmlFor="paid">Paid Leave</label></div><div className="form-check mb-2"><input className="form-check-input" type="checkbox" checked={setupType.allow_half_day} onChange={(e) => setSetupType({ ...setupType, allow_half_day: e.target.checked })} id="half" /><label className="form-check-label" htmlFor="half">Allow Half Day</label></div><div className="form-check mb-2"><input className="form-check-input" type="checkbox" checked={setupType.requires_attachment} onChange={(e) => setSetupType({ ...setupType, requires_attachment: e.target.checked })} id="attach" /><label className="form-check-label" htmlFor="attach">Attachment Required</label></div><div className="form-check mb-3"><input className="form-check-input" type="checkbox" checked={setupType.is_active} onChange={(e) => setSetupType({ ...setupType, is_active: e.target.checked })} id="active" /><label className="form-check-label" htmlFor="active">Active</label></div><button className="btn btn-primary" disabled={savingSetup || !selectedSetupType}>{savingSetup ? "Saving..." : "Save Leave Type"}</button></form></div></div></div>
          <div className="col-lg-6"><div className="card border-0 shadow-sm"><div className="card-header bg-white"><h5 className="mb-0">Advanced Leave Policy</h5></div><div className="card-body"><form onSubmit={handleSavePolicy}><div className="mb-3"><label className="form-label">Leave Type</label><select className="form-select" value={setupPolicy.leave_type_id} onChange={(e) => handleTypeSelect(e.target.value)}><option value="">Select leave type</option>{leaveTypes.map((type) => <option key={type.id} value={type.id}>{type.code} - {type.name}</option>)}</select></div><div className="mb-3"><label className="form-label">Annual Entitlement</label><input type="number" min="0" step="0.5" className="form-control" value={setupPolicy.annual_entitlement} onChange={(e) => setSetupPolicy({ ...setupPolicy, annual_entitlement: e.target.value })} /></div><div className="form-check mb-2"><input className="form-check-input" type="checkbox" checked={setupPolicy.carry_forward_allowed} onChange={(e) => setSetupPolicy({ ...setupPolicy, carry_forward_allowed: e.target.checked })} id="carry" /><label className="form-check-label" htmlFor="carry">Allow Carry Forward</label></div><div className="mb-3"><label className="form-label">Maximum Carry Forward</label><input type="number" min="0" step="0.5" className="form-control" disabled={!setupPolicy.carry_forward_allowed} value={setupPolicy.max_carry_forward} onChange={(e) => setSetupPolicy({ ...setupPolicy, max_carry_forward: e.target.value })} /></div><div className="form-check mb-2"><input className="form-check-input" type="checkbox" checked={setupPolicy.accrual_enabled} onChange={(e) => setSetupPolicy({ ...setupPolicy, accrual_enabled: e.target.checked })} id="accrual" /><label className="form-check-label" htmlFor="accrual">Enable Accrual</label></div><div className="mb-3"><label className="form-label">Accrual Frequency</label><select className="form-select" disabled={!setupPolicy.accrual_enabled} value={setupPolicy.accrual_frequency} onChange={(e) => setSetupPolicy({ ...setupPolicy, accrual_frequency: e.target.value })}><option value="annual">Annual</option><option value="monthly">Monthly</option><option value="quarterly">Quarterly</option></select></div><button className="btn btn-primary" disabled={savingSetup}>{savingSetup ? "Saving..." : "Save Leave Policy"}</button></form></div></div></div>
          <div className="col-12"><div className="card border-0 shadow-sm"><div className="card-header bg-white"><h5 className="mb-0">Company Leave Rules</h5><small className="text-muted">These rules are checked by the backend for every leave request.</small></div><div className="card-body"><form onSubmit={handleSaveCompanyPolicy}><div className="row g-3">
            <div className="col-md-3"><label className="form-label">Weekly Offs per Month</label><input type="number" min="1" max="10" className="form-control" value={companyPolicy.weekly_offs_per_month} onChange={(e) => setCompanyPolicy({ ...companyPolicy, weekly_offs_per_month: e.target.value })} disabled={!isHrAdmin || savingSetup} /></div>
            <div className="col-md-9"><span className="form-label d-block">Weekly Off Days</span><div className="d-flex flex-wrap gap-3">{["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"].map((day) => <div className="form-check" key={day}><input className="form-check-input" type="checkbox" id={`weekly-off-${day}`} checked={(companyPolicy.weekly_off_weekdays || []).includes(day)} onChange={(e) => setCompanyPolicy({ ...companyPolicy, weekly_off_weekdays: e.target.checked ? [...(companyPolicy.weekly_off_weekdays || []), day] : (companyPolicy.weekly_off_weekdays || []).filter((item) => item !== day) })} disabled={!isHrAdmin || savingSetup || (!(companyPolicy.weekly_off_weekdays || []).includes(day) && (companyPolicy.weekly_off_weekdays || []).length >= 6)} /><label className="form-check-label" htmlFor={`weekly-off-${day}`}>{day[0].toUpperCase() + day.slice(1)}</label></div>)}</div>{!(companyPolicy.weekly_off_weekdays || []).length && <small className="text-warning-emphasis">No weekly off is configured. Select the company's weekly off day(s); they are used for leave day counts and the attendance calendar.</small>}</div>
            <div className="col-md-3"><label className="form-label">Probation (Months)</label><input type="number" min="0" max="24" className="form-control" value={companyPolicy.probation_period_months} onChange={(e) => setCompanyPolicy({ ...companyPolicy, probation_period_months: e.target.value })} disabled={!isHrAdmin || savingSetup} /></div>
            <div className="col-md-3"><label className="form-label">Sick Leave after Probation</label><input type="number" min="0" step="0.5" className="form-control" value={companyPolicy.sick_leave_after_probation} onChange={(e) => setCompanyPolicy({ ...companyPolicy, sick_leave_after_probation: e.target.value })} disabled={!isHrAdmin || savingSetup} /></div>
            <div className="col-md-6"><label className="form-label">One-Day Leave Approval</label><div className="form-control bg-light">Reporting Manager approval required</div></div>
            <div className="col-md-6"><label className="form-label">Two or More Days Approval</label><div className="form-control bg-light">Reporting Manager, then HR Admin approval required</div></div>
            <div className="col-md-6"><div className="form-check"><input id="sick-before-probation" type="checkbox" className="form-check-input" checked={companyPolicy.sick_leave_before_probation_allowed} onChange={(e) => setCompanyPolicy({ ...companyPolicy, sick_leave_before_probation_allowed: e.target.checked })} disabled={!isHrAdmin || savingSetup} /><label className="form-check-label" htmlFor="sick-before-probation">Allow Sick Leave before probation (HR exception)</label></div></div>
            <div className="col-md-6"><div className="form-check"><input id="medical-certificate" type="checkbox" className="form-check-input" checked={companyPolicy.medical_certificate_required} onChange={(e) => setCompanyPolicy({ ...companyPolicy, medical_certificate_required: e.target.checked })} disabled={!isHrAdmin || savingSetup} /><label className="form-check-label" htmlFor="medical-certificate">Require medical certificate for Sick/Medical Leave</label></div></div>
            <div className="col-md-6"><div className="form-check"><input id="religion-holidays" type="checkbox" className="form-check-input" checked={companyPolicy.religion_holidays_enabled} onChange={(e) => setCompanyPolicy({ ...companyPolicy, religion_holidays_enabled: e.target.checked })} disabled={!isHrAdmin || savingSetup} /><label className="form-check-label" htmlFor="religion-holidays">Enable religion-specific calendar holidays</label></div></div>
            {isHrAdmin && <div className="col-12"><button className="btn btn-primary" disabled={savingSetup}>{savingSetup ? "Saving..." : "Save Company Rules"}</button></div>}
          </div></form></div></div></div>
        </div>
      ) : (
        <div className="card border-0 shadow-sm"><div className="card-header bg-white"><h5 className="mb-0">Leave Notifications</h5></div><div className="card-body">{notifications.length === 0 ? <div className="text-muted">No leave notifications.</div> : notifications.map((item) => <div className={`border rounded p-3 mb-3 ${item.is_read ? "bg-light" : ""}`} key={item.id}><div className="d-flex flex-wrap justify-content-between gap-3"><div className="flex-grow-1"><div className="d-flex align-items-center gap-2"><strong>{item.title}</strong><span className="small text-muted">{item.leave_notification_id}</span>{!item.is_read && <span className="badge bg-primary">New</span>}</div><div className="mt-1">{item.message}</div>        <small className="text-muted">{item.created_at ? formatDateTime(item.created_at) : ""} IST</small></div><div className="d-flex align-items-start gap-2">{item.permission_request_id && <button type="button" className="btn btn-sm btn-outline-primary" onClick={() => setActiveTab("permissions")}>Review request</button>}{item.attendance_request_id && <button type="button" className="btn btn-sm btn-outline-primary" onClick={() => { if (["hr admin", "administrator"].includes(role)) sessionStorage.setItem("hrms_open_attendance_request", item.attendance_request_id); onNavigate?.("attendance"); }}>{["hr admin", "administrator"].includes(role) ? "Review & Respond" : "View request"}</button>}<button type="button" className="btn btn-sm btn-outline-warning" title={item.is_starred ? "Unstar" : "Star"} aria-label={item.is_starred ? "Unstar leave notification" : "Star leave notification"} disabled={processingId === item.leave_notification_id} onClick={() => handleLeaveNotificationAction(item, item.is_starred ? "unstar" : "star")}><i className={`bi ${item.is_starred ? "bi-star-fill" : "bi-star"}`} /></button>{!item.is_read && <button type="button" className="btn btn-sm btn-outline-success" title="Mark as read" aria-label="Mark leave notification as read" disabled={processingId === item.leave_notification_id} onClick={() => handleLeaveNotificationAction(item, "read")}><i className="bi bi-check-lg" /></button>}<button type="button" className="btn btn-sm btn-outline-danger" title="Delete" aria-label="Delete leave notification" disabled={processingId === item.leave_notification_id} onClick={() => handleLeaveNotificationAction(item, "delete")}><i className="bi bi-trash" /></button></div></div></div>)}</div></div>
      )}
    </div>
  );
}

export default Leave;