import { useEffect, useState } from "react";

import {
  acknowledgeNotification,
  createNotification,
  deleteLeaveNotification,
  deleteNotification,
  getNotificationLookups,
  getNotifications,
  holdNotification,
  markLeaveNotificationRead,
  resumeNotification,
  starLeaveNotification,
  starNotification,
  unstarLeaveNotification,
  unstarNotification,
  updateNotification,
} from "../services/notificationApi";

const emptyForm = {
  title: "",
  message: "",
  audienceType: "all",
  roleIds: [],
  teamIds: "",
  departmentIds: [],
  userIds: [],
  validityValue: "1",
  validityUnit: "days",
  status: "ACTIVE",
};

const parseIdList = (value) => value.split(",").map((item) => item.trim()).filter(Boolean);

function NotificationManagement({ user = {}, onNavigate }) {
  const [notifications, setNotifications] = useState([]);
  const [lookups, setLookups] = useState({ roles: [], departments: [], employees: [] });
  const [form, setForm] = useState(emptyForm);
  const [editingId, setEditingId] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const canManage = ["hr admin", "manager", "administrator"].includes(
    String(user?.role || "").trim().toLowerCase(),
  );

  const refresh = async () => {
    const data = await getNotifications();
    setNotifications(Array.isArray(data) ? data : data?.notifications || []);
  };

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError("");
      try {
        const data = await getNotifications();
        if (cancelled) return;
        setNotifications(Array.isArray(data) ? data : data?.notifications || []);
        if (canManage) {
          setLookups(await getNotificationLookups());
        }
      } catch (requestError) {
        if (!cancelled) {
          console.error("Unable to load notifications:", requestError);
          setError(requestError.message || "Unable to load notifications.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [canManage]);

  const updateForm = (event) => {
    const { name, value } = event.target;
    setForm((current) => ({ ...current, [name]: value }));
  };

  const updateMultiple = (event) => {
    const values = Array.from(event.target.selectedOptions, (option) => option.value);
    setForm((current) => ({ ...current, [event.target.name]: values }));
  };

  const resetForm = () => {
    setForm(emptyForm);
    setEditingId("");
    setError("");
    setSuccess("");
  };

  const buildPayload = () => ({
    title: form.title.trim(),
    message: form.message.trim(),
    audience: {
      type: form.audienceType,
      role_ids: form.audienceType === "roles" ? form.roleIds : [],
      team_ids: form.audienceType === "teams" ? parseIdList(form.teamIds) : [],
      department_ids: form.audienceType === "departments" ? form.departmentIds : [],
      user_ids: form.audienceType === "users" ? form.userIds : [],
    },
    validity_value: Number(form.validityValue),
    validity_unit: form.validityUnit,
    status: form.status,
  });

  const validateForm = () => {
    if (!form.title.trim() || !form.message.trim()) {
      return "Enter both a notification title and message.";
    }
    if (!Number.isInteger(Number(form.validityValue)) || Number(form.validityValue) <= 0) {
      return "Validity must be a whole number greater than zero.";
    }
    if (form.audienceType === "roles" && form.roleIds.length === 0) {
      return "Select at least one role.";
    }
    if (form.audienceType === "teams" && parseIdList(form.teamIds).length === 0) {
      return "Enter at least one team ID.";
    }
    if (form.audienceType === "departments" && form.departmentIds.length === 0) {
      return "Select at least one department.";
    }
    if (form.audienceType === "users" && form.userIds.length === 0) {
      return "Select at least one employee.";
    }
    return "";
  };

  const saveNotification = async () => {
    const validationMessage = validateForm();
    if (validationMessage) {
      setError(validationMessage);
      return;
    }
    setSaving(true);
    setError("");
    setSuccess("");
    try {
      const payload = buildPayload();
      if (editingId) {
        await updateNotification(editingId, payload);
        setSuccess("Notification updated successfully.");
      } else {
        await createNotification(payload);
        setSuccess("Notification created successfully.");
      }
      resetForm();
      await refresh();
      window.dispatchEvent(new Event("hrms-notifications-updated"));
    } catch (requestError) {
      console.error("Unable to save notification:", requestError);
      setError(requestError.message || "Unable to save notification.");
    } finally {
      setSaving(false);
    }
  };

  const editNotification = (item) => {
    const audience = item.audience || {};
    setEditingId(item.id);
    setForm({
      title: item.title || "",
      message: item.message || "",
      audienceType: audience.type || "all",
      roleIds: audience.role_ids || [],
      teamIds: (audience.team_ids || []).join(", "),
      departmentIds: audience.department_ids || [],
      userIds: audience.user_ids || [],
      validityValue: String(item.validity_value || 1),
      validityUnit: item.validity_unit || "days",
      status: item.status || "ACTIVE",
    });
    setError("");
    setSuccess("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const mutateNotification = async (operation, item) => {
    setError("");
    try {
      await operation(item);
      await refresh();
      window.dispatchEvent(new Event("hrms-notifications-updated"));
    } catch (requestError) {
      console.error("Unable to update notification state:", requestError);
      setError(requestError.message || "Unable to update notification.");
    }
  };

  const toggleStar = (item) => mutateNotification(async (notification) => {
    if (notification.notification_kind === "leave") {
      return notification.is_starred
        ? unstarLeaveNotification(notification.leave_notification_id)
        : starLeaveNotification(notification.leave_notification_id);
    }
    return notification.is_starred
      ? unstarNotification(notification.id)
      : starNotification(notification.id);
  }, item);

  const markRead = (item) => mutateNotification(async (notification) => {
    if (notification.notification_kind === "leave") {
      return markLeaveNotificationRead(notification.leave_notification_id);
    }
    return acknowledgeNotification(notification.id);
  }, item);

  const removeNotification = (item) => {
    const confirmation = item.can_edit
      ? "Delete this notification for all recipients?"
      : "Delete this notification from your inbox?";
    if (!window.confirm(confirmation)) return;
    mutateNotification(async (notification) => {
      if (notification.notification_kind === "leave") {
        return deleteLeaveNotification(notification.leave_notification_id);
      }
      return deleteNotification(notification.id);
    }, item);
  };

  const toggleHold = (item) => mutateNotification(async (notification) => (
    notification.status === "HOLD"
      ? resumeNotification(notification.id)
      : holdNotification(notification.id)
  ), item);

  const formatDate = (value) => {
    if (!value) return "";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? "" : parsed.toLocaleString("en-IN", {
      timeZone: "Asia/Kolkata",
      dateStyle: "medium",
      timeStyle: "short",
    });
  };

  return (
    <div className="container-fluid py-4">
      <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-4">
        <div>
          <h2 className="mb-1">Notifications</h2>
          <p className="text-muted mb-0">Company updates and leave activity for your account.</p>
        </div>
        <button type="button" className="btn btn-outline-secondary" onClick={() => onNavigate?.("dashboard")}>
          <i className="bi bi-arrow-left me-1" /> Back
        </button>
      </div>

      {error && <div className="alert alert-danger" role="alert">{error}</div>}
      {success && <div className="alert alert-success" role="status">{success}</div>}

      {canManage && (
        <div className="card border-0 shadow-sm mb-4">
          <div className="card-header bg-white">
            <h5 className="mb-0">{editingId ? "Edit Notification" : "Create Notification"}</h5>
          </div>
          <div className="card-body">
            <div className="row g-3">
              <div className="col-lg-6">
                <label className="form-label" htmlFor="notification-title">Title</label>
                <input id="notification-title" className="form-control" maxLength={200} name="title" value={form.title} onChange={updateForm} disabled={saving} />
              </div>
              <div className="col-lg-6">
                <label className="form-label" htmlFor="notification-target">Target type</label>
                <select id="notification-target" className="form-select" name="audienceType" value={form.audienceType} onChange={updateForm} disabled={saving}>
                  <option value="all">All Employees</option>
                  <option value="roles">Specific Role</option>
                  <option value="departments">Specific Department</option>
                  <option value="users">Specific Employees</option>
                  <option value="teams">Specific Teams (legacy)</option>
                </select>
              </div>
              {form.audienceType === "roles" && (
                <div className="col-lg-6">
                  <label className="form-label" htmlFor="notification-roles">Roles</label>
                  <select id="notification-roles" className="form-select" name="roleIds" multiple value={form.roleIds} onChange={updateMultiple} disabled={saving}>
                    {lookups.roles.map((role) => <option key={role.id} value={role.id}>{role.name}</option>)}
                  </select>
                </div>
              )}
              {form.audienceType === "teams" && (
                <div className="col-lg-6">
                  <label className="form-label" htmlFor="notification-teams">Team IDs</label>
                  <input id="notification-teams" className="form-control" name="teamIds" value={form.teamIds} onChange={updateForm} disabled={saving} placeholder="Comma-separated team IDs" />
                </div>
              )}
              {form.audienceType === "departments" && (
                <div className="col-lg-6">
                  <label className="form-label" htmlFor="notification-departments">Departments</label>
                  <select id="notification-departments" className="form-select" name="departmentIds" multiple value={form.departmentIds} onChange={updateMultiple} disabled={saving}>
                    {lookups.departments.map((department) => <option key={department.id} value={department.id}>{department.name}</option>)}
                  </select>
                </div>
              )}
              {form.audienceType === "users" && (
                <div className="col-lg-6">
                  <label className="form-label" htmlFor="notification-employees">Employees</label>
                  <select id="notification-employees" className="form-select" name="userIds" multiple value={form.userIds} onChange={updateMultiple} disabled={saving}>
                    {lookups.employees.map((employee) => (
                      <option key={employee.id} value={employee.id}>
                        {employee.employee_id || "No employee ID"} — {employee.name}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <div className="col-12">
                <label className="form-label" htmlFor="notification-message">Message</label>
                <textarea id="notification-message" className="form-control" rows={4} maxLength={5000} name="message" value={form.message} onChange={updateForm} disabled={saving} />
              </div>
              <div className="col-md-4">
                <label className="form-label" htmlFor="notification-validity">Validity</label>
                <div className="input-group">
                  <input id="notification-validity" type="number" min="1" step="1" className="form-control" name="validityValue" value={form.validityValue} onChange={updateForm} disabled={saving} />
                  <select className="form-select" aria-label="Validity unit" name="validityUnit" value={form.validityUnit} onChange={updateForm} disabled={saving}>
                    <option value="minutes">Minutes</option>
                    <option value="hours">Hours</option>
                    <option value="days">Days</option>
                    <option value="weeks">Weeks</option>
                  </select>
                </div>
              </div>
              <div className="col-md-4">
                <label className="form-label" htmlFor="notification-status">Delivery status</label>
                <select id="notification-status" className="form-select" name="status" value={form.status} onChange={updateForm} disabled={saving}>
                  <option value="ACTIVE">Active</option>
                  <option value="HOLD">Hold (do not deliver)</option>
                </select>
              </div>
              <div className="col-12 d-flex gap-2">
                <button type="button" className="btn btn-primary" onClick={saveNotification} disabled={saving}>
                  {saving ? "Saving..." : editingId ? "Update Notification" : "Post Notification"}
                </button>
                {editingId && <button type="button" className="btn btn-outline-secondary" onClick={resetForm} disabled={saving}>Cancel</button>}
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="card border-0 shadow-sm">
        <div className="card-header bg-white d-flex justify-content-between align-items-center">
          <h5 className="mb-0">{canManage ? "Notifications" : "Your Notifications"}</h5>
          <button type="button" className="btn btn-sm btn-outline-primary" onClick={() => refresh().catch((loadError) => setError(loadError.message))} disabled={loading}>
            <i className="bi bi-arrow-clockwise me-1" /> Refresh
          </button>
        </div>
        <div className="list-group list-group-flush">
          {loading ? (
            <div className="text-center py-5"><span className="spinner-border spinner-border-sm me-2" />Loading notifications...</div>
          ) : notifications.length === 0 ? (
            <div className="text-center text-muted py-5">
              <i className="bi bi-bell-slash fs-2 d-block mb-2" />
              No active notifications.
            </div>
          ) : notifications.map((item) => {
            const isHeld = item.status === "HOLD";
            const canActOnInbox = item.is_recipient && !isHeld;
            return (
              <div className="list-group-item py-3" key={`${item.notification_kind || "common"}-${item.id}`}>
                <div className="d-flex align-items-start gap-3">
                  {canActOnInbox && (
                    <button type="button" className="btn btn-link p-0 text-warning" aria-label={item.is_starred ? "Unstar notification" : "Star notification"} title={item.is_starred ? "Unstar" : "Star"} onClick={() => toggleStar(item)}>
                      <i className={`bi ${item.is_starred ? "bi-star-fill" : "bi-star"} fs-5`} />
                    </button>
                  )}
                  <div className="flex-grow-1">
                    <div className="d-flex flex-wrap justify-content-between gap-2">
                      <div>
                        <span className={`fw-semibold ${item.is_read ? "text-muted" : ""}`}>{item.title}</span>
                        {item.notification_id && <span className="badge bg-light text-dark border ms-2">{item.notification_id}</span>}
                        {!item.is_read && item.is_recipient && <span className="badge bg-primary ms-2">Unread</span>}
                        {isHeld && <span className="badge bg-secondary ms-2">On hold</span>}
                      </div>
                      <small className="text-muted">{formatDate(item.created_at)}</small>
                    </div>
                    <p className="mb-0 mt-2">{item.message}</p>
                    <div className="d-flex flex-wrap justify-content-end gap-2 mt-3">
                      {canActOnInbox && !item.is_read && (
                        <button type="button" className="btn btn-sm btn-outline-success" title="Mark as read" aria-label="Mark as read" onClick={() => markRead(item)}>
                          <i className="bi bi-check-lg me-1" /> Mark read
                        </button>
                      )}
                      {canActOnInbox && !item.can_edit && (
                        <button type="button" className="btn btn-sm btn-outline-danger" title="Delete from my inbox" onClick={() => removeNotification(item)}>
                          <i className="bi bi-trash me-1" /> Delete
                        </button>
                      )}
                      {item.can_edit && (
                        <>
                          <button type="button" className="btn btn-sm btn-outline-primary" onClick={() => editNotification(item)}>
                            <i className="bi bi-pencil me-1" /> Edit
                          </button>
                          <button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => toggleHold(item)}>
                            <i className={`bi ${isHeld ? "bi-play-fill" : "bi-pause-fill"} me-1`} />
                            {isHeld ? "Resume" : "Hold"}
                          </button>
                          <button type="button" className="btn btn-sm btn-outline-danger" onClick={() => removeNotification(item)}>
                            <i className="bi bi-trash me-1" /> Delete
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export default NotificationManagement;
