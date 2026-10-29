import { useCallback, useEffect, useState } from "react";
import { HRONE_API_BASE } from "../config/api.js";
import { roleLabel } from "../services/roleLabels";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || HRONE_API_BASE).replace(/\/+$/, "");
const todayIso = () => {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
};

function WfhAdminSettings({ session }) {
  const token = session?.token;
  const [employees, setEmployees] = useState([]);
  const [loading, setLoading] = useState(true);
  const [savingId, setSavingId] = useState("");
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [faceDetails, setFaceDetails] = useState(null);
  const [faceDetailsLoading, setFaceDetailsLoading] = useState(false);
  const [faceDetailsError, setFaceDetailsError] = useState("");

  const headers = useCallback(() => {
    if (!token) throw new Error("Login session not found. Please login again.");
    return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  }, [token]);

  const loadEmployees = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/attendance/wfh/admin/employees`, { headers: headers() });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || "Unable to load WFH settings.");
      setEmployees(Array.isArray(data) ? data : []);
      setError("");
    } catch (e) {
      setError(e.message || "Unable to load WFH settings.");
    } finally {
      setLoading(false);
    }
  }, [headers]);

  useEffect(() => { loadEmployees(); }, [loadEmployees]);

  const updateEmployee = (userId, field, value) => {
    setEmployees((rows) => rows.map((row) => row.user_id === userId ? { ...row, [field]: value } : row));
    setError("");
    setSuccess("");
  };

  const saveEmployee = async (employee) => {
    setSavingId(employee.user_id); setError(""); setSuccess("");
    try {
      if (employee.is_wfh_enabled && (!employee.wfh_permitted_from || !employee.wfh_permitted_to)) {
        throw new Error("Choose both WFH permitted from and until dates before enabling WFH.");
      }
      if (employee.is_wfh_enabled && (!employee.wfh_permitted_start_time || !employee.wfh_permitted_end_time)) {
        throw new Error("WFH start time and end time are mandatory.");
      }
      if (employee.wfh_permitted_from && employee.wfh_permitted_from < todayIso()) throw new Error("WFH start date must be today or a future date.");
      if (employee.wfh_permitted_to && employee.wfh_permitted_to < todayIso()) throw new Error("WFH end date must be today or a future date.");
      if (employee.wfh_permitted_from && employee.wfh_permitted_to && employee.wfh_permitted_to < employee.wfh_permitted_from) throw new Error("WFH end date must be on or after the start date.");
      if (employee.wfh_permitted_start_time && employee.wfh_permitted_end_time && employee.wfh_permitted_end_time <= employee.wfh_permitted_start_time) throw new Error("WFH end time must be later than start time.");

      const payload = {
        is_wfh_enabled: Boolean(employee.is_wfh_enabled),
        home_latitude: null,
        home_longitude: null,
        allowed_radius: Number(employee.allowed_radius || 100),
        home_location_accuracy_meters: null,
        home_location_timestamp: null,
        home_location_verification_method: "manual",
        home_location_manually_verified: false,
        wfh_permitted_from: employee.wfh_permitted_from || null,
        wfh_permitted_to: employee.wfh_permitted_to || null,
        wfh_permitted_start_time: employee.wfh_permitted_start_time || null,
        wfh_permitted_end_time: employee.wfh_permitted_end_time || null,
      };
      const response = await fetch(`${API_BASE_URL}/api/attendance/wfh/admin/employees/${employee.user_id}`, {
        method: "PUT", headers: headers(), body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || "Unable to save WFH settings.");
      setEmployees((rows) => rows.map((row) => row.user_id === employee.user_id ? { ...row, ...data } : row));
      setSuccess(`WFH settings saved for ${employee.name || employee.employee_id || "account"}.`);
    } catch (e) {
      setError(e.message || "Unable to save WFH settings.");
    } finally { setSavingId(""); }
  };

  const resetLocation = async (employee) => {
    if (!window.confirm(`Reset the registered WFH location for ${employee.name}? They must register a new location from their WFH Attendance page.`)) return;
    setBusyAction(`${employee.user_id}:location`); setError(""); setSuccess("");
    try {
      const response = await fetch(`${API_BASE_URL}/api/attendance/wfh/admin/employees/${employee.user_id}/location`, { method: "DELETE", headers: headers() });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || "Unable to reset location.");
      setEmployees((rows) => rows.map((row) => row.user_id === employee.user_id ? {
        ...row, home_latitude: null, home_longitude: null, home_location_accuracy_meters: null,
        home_location_timestamp: null, home_location_updated_at: null,
        location_registration_required: true,
      } : row));
      setSuccess(`Location reset for ${employee.name}. They can register a new location on their WFH Attendance page.`);
    } catch (e) { setError(e.message || "Unable to reset location."); }
    finally { setBusyAction(""); }
  };

  const resetFace = async (employee) => {
    if (!window.confirm(`Reset the registered face template for ${employee.name}? They will need to register their face again.`)) return;
    setBusyAction(`${employee.user_id}:face`); setError(""); setSuccess("");
    try {
      const response = await fetch(`${API_BASE_URL}/api/attendance/wfh/admin/employees/${employee.user_id}/face`, { method: "DELETE", headers: headers() });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || "Unable to reset face registration.");
      setEmployees((rows) => rows.map((row) => row.user_id === employee.user_id ? { ...row, face_registered: false } : row));
      setSuccess(`Face registration reset for ${employee.name}.`);
    } catch (e) { setError(e.message || "Unable to reset face registration."); }
    finally { setBusyAction(""); }
  };

  const showLocation = (employee) => {
    if (employee.home_latitude == null || employee.home_longitude == null) {
      window.alert("Location is not registered yet. The employee must register it from their WFH Attendance page.");
      return;
    }
    const accuracy = employee.home_location_accuracy_meters != null ? `\nReported GPS accuracy: ±${Math.round(employee.home_location_accuracy_meters)} m` : "";
    const registered = employee.home_location_updated_at ? `\nRegistered/updated: ${new Date(employee.home_location_updated_at).toLocaleString()}` : "";
    window.alert(`Registered WFH location for ${employee.name}\nLatitude: ${employee.home_latitude}\nLongitude: ${employee.home_longitude}${accuracy}${registered}`);
  };

  const showFace = async (employee) => {
    setFaceDetails({
      employee: {
        name: employee.name,
        employee_id: employee.employee_id,
        role: employee.role,
      },
      face_registered: Boolean(employee.face_registered),
      registered_at: null,
      photograph_available: false,
      photograph_data_url: null,
    });
    setFaceDetailsError("");
    setFaceDetailsLoading(true);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/attendance/wfh/admin/employees/${employee.user_id}/face`,
        { headers: headers() }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || "Unable to load face registration details.");
      setFaceDetails(data);
    } catch (e) {
      setFaceDetailsError(e.message || "Unable to load face registration details.");
    } finally {
      setFaceDetailsLoading(false);
    }
  };

  return (
    <section className="card border-0 shadow-sm mb-4">
      <div className="card-body">
        <div className="d-flex flex-wrap justify-content-between align-items-start gap-2 mb-3">
          <div>
            <h3 className="h5 mb-1">WFH Settings & Permission Duration</h3>
            <p className="text-muted small mb-0">Manage WFH access, permitted dates, and mandatory daily start/end times. The same daily schedule applies throughout the selected date range.</p>
          </div>
          <button className="btn btn-outline-secondary btn-sm" type="button" onClick={() => { setLoading(true); loadEmployees(); }} disabled={loading}>Refresh</button>
        </div>
        {error && <div className="alert alert-danger py-2" role="alert">{error}</div>}
        {success && <div className="alert alert-success py-2" role="status">{success}</div>}
        {loading ? <div className="text-muted py-3">Loading WFH settings...</div> : employees.length === 0 ? <div className="text-muted py-3">No eligible accounts found.</div> : (
          <div className="table-responsive">
            <table className="table table-sm align-middle mb-0">
              <thead><tr><th>Employee / Role</th><th>WFH Enabled</th><th>Permit From</th><th>Permit Until</th><th>Required Start Time</th><th>Required End Time</th><th>Face</th><th>Location</th><th>Actions</th></tr></thead>
              <tbody>{employees.map((employee) => {
                const locationRegistered = employee.home_latitude != null && employee.home_longitude != null;
                const busy = busyAction.startsWith(`${employee.user_id}:`);
                return <tr key={employee.user_id}>
                  <td><div className="fw-semibold">{employee.name || "Unnamed account"}</div><small className="text-muted d-block">{employee.employee_id || "No employee ID"} · {roleLabel(employee.role) || "Employee"}{!employee.is_active && " · Inactive"}</small></td>
                  <td><div className="form-check form-switch"><input className="form-check-input" type="checkbox" role="switch" id={`wfh-enabled-${employee.user_id}`} checked={Boolean(employee.is_wfh_enabled)} onChange={(event) => updateEmployee(employee.user_id, "is_wfh_enabled", event.target.checked)} aria-label={`Enable WFH for ${employee.name}`} /></div></td>
                  <td><input className="form-control form-control-sm" type="date" min={todayIso()} value={employee.wfh_permitted_from || ""} onChange={(event) => updateEmployee(employee.user_id, "wfh_permitted_from", event.target.value)} aria-label={`WFH permit start date for ${employee.name}`} /></td>
                  <td><input className="form-control form-control-sm" type="date" min={employee.wfh_permitted_from || todayIso()} value={employee.wfh_permitted_to || ""} onChange={(event) => updateEmployee(employee.user_id, "wfh_permitted_to", event.target.value)} aria-label={`WFH permit end date for ${employee.name}`} /></td>
                  <td><input className="form-control form-control-sm" type="time" required={Boolean(employee.is_wfh_enabled)} value={employee.wfh_permitted_start_time || ""} onChange={(event) => updateEmployee(employee.user_id, "wfh_permitted_start_time", event.target.value)} aria-label={`WFH start time for ${employee.name}`} /></td>
                  <td><input className="form-control form-control-sm" type="time" required={Boolean(employee.is_wfh_enabled)} value={employee.wfh_permitted_end_time || ""} onChange={(event) => updateEmployee(employee.user_id, "wfh_permitted_end_time", event.target.value)} aria-label={`WFH end time for ${employee.name}`} /></td>
                  <td><button type="button" className={`btn btn-sm ${employee.face_registered ? "btn-outline-success" : "btn-outline-secondary"}`} onClick={() => showFace(employee)}>{employee.face_registered ? "Face Registered" : "Not Registered"}</button></td>
                  <td><button type="button" className={`btn btn-sm ${locationRegistered ? "btn-outline-success" : "btn-outline-secondary"}`} onClick={() => showLocation(employee)}>{locationRegistered ? "Location Registered" : "Not Registered"}</button>{locationRegistered && employee.home_location_accuracy_meters != null && <small className="d-block text-muted mt-1">±{Math.round(employee.home_location_accuracy_meters)} m</small>}</td>
                  <td><div className="d-flex flex-wrap gap-1"><button type="button" className="btn btn-primary btn-sm" onClick={() => saveEmployee(employee)} disabled={savingId === employee.user_id}>{savingId === employee.user_id ? "Saving..." : "Save"}</button><button type="button" className="btn btn-outline-danger btn-sm" onClick={() => resetFace(employee)} disabled={!employee.face_registered || busy}>{busyAction === `${employee.user_id}:face` ? "Resetting..." : "Reset Face"}</button><button type="button" className="btn btn-outline-warning btn-sm" onClick={() => resetLocation(employee)} disabled={!locationRegistered || busy}>{busyAction === `${employee.user_id}:location` ? "Resetting..." : "Reset Location"}</button></div></td>
                </tr>;
              })}</tbody>
            </table>
          </div>
        )}
      </div>
      {faceDetails && (
        <div
          className="modal d-block"
          role="dialog"
          aria-modal="true"
          aria-labelledby="wfh-face-details-title"
          style={{ background: "rgba(0, 0, 0, 0.55)", zIndex: 1060 }}
          onClick={(event) => {
            if (event.target === event.currentTarget) setFaceDetails(null);
          }}
        >
          <div className="modal-dialog modal-dialog-centered modal-lg">
            <div className="modal-content">
              <div className="modal-header">
                <div>
                  <h2 className="modal-title h5 mb-1" id="wfh-face-details-title">
                    Face Registration Details
                  </h2>
                  <div className="small text-muted">
                    {faceDetails.employee?.name || "Employee"}
                    {faceDetails.employee?.employee_id ? ` · ${faceDetails.employee.employee_id}` : ""}
                  </div>
                </div>
                <button type="button" className="btn-close" aria-label="Close" onClick={() => setFaceDetails(null)} />
              </div>
              <div className="modal-body">
                {faceDetailsLoading ? (
                  <div className="text-muted py-4 text-center">Loading face registration details...</div>
                ) : faceDetailsError ? (
                  <div className="alert alert-danger mb-0" role="alert">{faceDetailsError}</div>
                ) : (
                  <div className="row g-4 align-items-start">
                    <div className="col-md-5 text-center">
                      {faceDetails.photograph_data_url ? (
                        <img
                          src={faceDetails.photograph_data_url}
                          alt={`Registered face for ${faceDetails.employee?.name || "employee"}`}
                          className="img-fluid rounded border"
                          style={{ maxHeight: "320px", objectFit: "contain" }}
                        />
                      ) : (
                        <div className="border rounded bg-light text-muted p-4">
                          <div className="fw-semibold mb-2">
                            {faceDetails.face_registered ? "Photograph not available" : "No face registered"}
                          </div>
                          <div className="small">
                            {faceDetails.face_registered
                              ? "This face was registered before photograph storage was enabled. Reset and register the face again to create a photograph."
                              : "The employee has not registered a face yet."}
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="col-md-7">
                      <dl className="row mb-0">
                        <dt className="col-sm-5">Registration status</dt>
                        <dd className="col-sm-7">
                          <span className={`badge ${faceDetails.face_registered ? "text-bg-success" : "text-bg-secondary"}`}>
                            {faceDetails.face_registered ? "Face Registered" : "Not Registered"}
                          </span>
                        </dd>
                        <dt className="col-sm-5">Employee</dt>
                        <dd className="col-sm-7">{faceDetails.employee?.name || "—"}</dd>
                        <dt className="col-sm-5">Employee ID</dt>
                        <dd className="col-sm-7">{faceDetails.employee?.employee_id || "—"}</dd>
                        <dt className="col-sm-5">Role</dt>
                        <dd className="col-sm-7">{roleLabel(faceDetails.employee?.role) || "—"}</dd>
                        <dt className="col-sm-5">Registered at</dt>
                        <dd className="col-sm-7">
                          {faceDetails.registered_at
                            ? new Date(faceDetails.registered_at).toLocaleString()
                            : "Not available"}
                        </dd>
                        <dt className="col-sm-5">Photograph</dt>
                        <dd className="col-sm-7">
                          {faceDetails.photograph_available ? "Available (encrypted at rest)" : "Not available"}
                        </dd>
                      </dl>
                    </div>
                  </div>
                )}
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-secondary" onClick={() => setFaceDetails(null)}>Close</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

export default WfhAdminSettings;
