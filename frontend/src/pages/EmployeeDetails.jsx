import { HRONE_API_BASE } from "../config/api.js";
import { useEffect, useMemo, useState } from "react";

const API_BASE_URL = HRONE_API_BASE;

function getStoredSession() {
  try {
    return JSON.parse(
      sessionStorage.getItem("hrms_session") || "null"
    );
  } catch {
    return null;
  }
}

function getTokenFromSession(session) {
  if (!session) return null;

  return (
    session?.token ||
    session?.access_token ||
    session?.user?.token ||
    null
  );
}

function getUserId(user) {
  return user?.id || user?._id || null;
}

function formatDate(value) {
  if (!value) return "-";

  try {
    const dateOnly = String(value).match(
      /^(\d{4})-(\d{2})-(\d{2})$/
    );

    if (dateOnly) {
      return `${dateOnly[3]}/${dateOnly[2]}/${dateOnly[1]}`;
    }

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
      return value;
    }

    return date.toLocaleDateString("en-IN", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    });
  } catch {
    return value;
  }
}

function formatDateTime(value) {
  if (!value) return "-";

  try {
    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
      return value;
    }

    return date.toLocaleString("en-IN", {
      timeZone: "Asia/Kolkata",
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: true,
      timeZoneName: "short",
    });
  } catch {
    return value;
  }
}

function getDisplayValue(value) {
  if (
    value === null ||
    value === undefined ||
    value === ""
  ) {
    return "-";
  }

  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }

  return String(value);
}

/* =========================================================
   NORMALIZE BACKEND RESPONSE

   Backend returns:

   {
     user: {...},
     details: {...}
   }

   Frontend combines both objects into one employee object.
========================================================= */

function normalizeEmployeeDetails(data) {
  if (!data) return null;

  const user = data?.user || {};
  const details = data?.details || {};

  return {
    ...user,
    ...details,

    id:
      user?.id ||
      user?._id ||
      data?.id ||
      data?._id ||
      null,

    employee_id:
      user?.employee_id ||
      user?.employeeId ||
      "",

    name:
      user?.name ||
      user?.full_name ||
      user?.fullName ||
      "",

    work_email:
      user?.work_email ||
      user?.email ||
      "",

    country_iso:
      user?.country_iso ||
      "",

    country_code:
      user?.country_code ||
      "",

    mobile:
      user?.mobile ||
      user?.phone ||
      user?.phone_number ||
      "",

    role:
      user?.role ||
      "",

    is_active:
      user?.is_active !== undefined
        ? user.is_active
        : user?.active !== undefined
        ? user.active
        : true,

    last_login:
      user?.last_login ||
      user?.lastLogin ||
      null,

    login_count:
      user?.login_count ||
      0,

    date_of_birth:
      details?.date_of_birth ||
      "",

    blood_group:
      details?.blood_group ||
      "",

    date_of_joining:
      details?.date_of_joining ||
      "",

    work_field:
      details?.work_field ||
      "",

    nationality:
      details?.nationality ||
      "",

    address:
      details?.address ||
      "",

    gender:
      details?.gender ||
      "",

    marital_status:
      details?.marital_status ||
      "",

    religion:
      details?.religion ||
      "",

    department:
      details?.department ||
      "",

    designation:
      details?.designation ||
      "",

    city:
      details?.city ||
      "",

    state:
      details?.state ||
      "",

    country:
      details?.country ||
      "",

    phone_country:
      user?.country ||
      user?.country_iso ||
      "",

    phone_country_iso:
      user?.country_iso ||
      "",
  };
}

/* =========================================================
   FIELD
========================================================= */

function Field({
  label,
  value,
  name,
  type = "text",
  editing,
  onChange,
  disabled = false,
  placeholder = "",
}) {
  return (
    <div className="employee-details-field">
      <label className="employee-details-label">
        {label}
      </label>

      {editing ? (
        <input
          className="employee-details-input"
          type={type}
          name={name}
          value={value ?? ""}
          onChange={onChange}
          disabled={disabled}
          placeholder={placeholder}
        />
      ) : (
        <div className="employee-details-value">
          {getDisplayValue(value)}
        </div>
      )}
    </div>
  );
}

/* =========================================================
   TEXTAREA FIELD
========================================================= */

function TextAreaField({
  label,
  value,
  name,
  editing,
  onChange,
  disabled = false,
  placeholder = "",
}) {
  return (
    <div className="employee-details-field">
      <label className="employee-details-label">
        {label}
      </label>

      {editing ? (
        <textarea
          className="employee-details-input"
          name={name}
          value={value ?? ""}
          onChange={onChange}
          disabled={disabled}
          placeholder={placeholder}
          rows={4}
        />
      ) : (
        <div className="employee-details-value">
          {getDisplayValue(value)}
        </div>
      )}
    </div>
  );
}

/* =========================================================
   SELECT FIELD
========================================================= */

const EMPTY_LEAVE_APPROVERS = {
  hod_id: "",
  hr_admin_id: "",
  hod: null,
  hr_admin: null,
};

function SelectField({
  label,
  value,
  name,
  options = [],
  editing,
  onChange,
  disabled = false,
}) {
  return (
    <div className="employee-details-field">
      <label className="employee-details-label">
        {label}
      </label>

      {editing ? (
        <select
          className="employee-details-input"
          name={name}
          value={value ?? ""}
          onChange={onChange}
          disabled={disabled}
        >
          <option value="">Select</option>

          {options.map((option) => (
            <option
              key={option.value ?? option}
              value={option.value ?? option}
            >
              {option.label ?? option}
            </option>
          ))}
        </select>
      ) : (
        <div className="employee-details-value">
          {getDisplayValue(value)}
        </div>
      )}
    </div>
  );
}

/* =========================================================
   MAIN COMPONENT
========================================================= */

export default function EmployeeDetails({
  userId,
  onNavigate,
  readOnly = true,
  canEdit = false,
  openedFromEmployeeMaster = false,
}) {
  const [session, setSession] = useState(
    () => getStoredSession()
  );

  const [employee, setEmployee] = useState(null);

  const [reportingManager, setReportingManager] =
    useState(null);

  const [managerOptions, setManagerOptions] =
    useState([]);

  // Leave approvers assigned by HR Admin (stable user IDs + display summary).
  const [leaveApprovers, setLeaveApprovers] =
    useState(EMPTY_LEAVE_APPROVERS);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const [error, setError] = useState("");
  const [successMessage, setSuccessMessage] =
    useState("");

  const [isEditing, setIsEditing] = useState(false);

  const canEditDetails = Boolean(
    canEdit && !readOnly
  );

  // Only HR Admin assigns Reporting Manager, HOD and HR Admin (enforced by the backend too).
  const canAssignApprovers =
    String(session?.user?.role || "").trim().toLowerCase() === "hr admin";

  const employeeId =
    userId || getUserId(employee);

  const currentUserId = useMemo(() => {
    return (
      getUserId(session?.user) ||
      session?.user_id ||
      session?.id ||
      null
    );
  }, [session]);

  /* =========================================================
     SESSION
  ========================================================= */

  useEffect(() => {
    const storedSession = getStoredSession();

    setSession(storedSession);
  }, []);

  /* =========================================================
     LOAD EMPLOYEE
  ========================================================= */

  useEffect(() => {
    if (!userId) {
      setEmployee(null);
      setReportingManager(null);
      setLoading(false);
      setError(
        "Employee information is not available."
      );

      return;
    }

    loadEmployeeDetails();
  }, [userId]);

  /* =========================================================
     LOAD MANAGER OPTIONS
  ========================================================= */

  useEffect(() => {
    if (!canEditDetails || !employeeId) {
      return;
    }

    loadManagerOptions();
  }, [canEditDetails, employeeId]);

  /* =========================================================
     RESET EDIT MODE
  ========================================================= */

  useEffect(() => {
    if (!canEditDetails) {
      setIsEditing(false);
    }
  }, [canEditDetails]);

  /* =========================================================
     LOAD EMPLOYEE DETAILS
  ========================================================= */

  async function loadEmployeeDetails() {
    setLoading(true);
    setError("");
    setSuccessMessage("");

    try {
      const storedSession =
        getStoredSession();

      setSession(storedSession);

      const token =
        getTokenFromSession(
          storedSession
        );

      const headers = {
        "Content-Type":
          "application/json",
      };

      if (token) {
        headers.Authorization =
          `Bearer ${token}`;
      }

      /* =====================================================
         USER + EMPLOYEE EXTRA DETAILS
      ===================================================== */

      const employeeResponse =
        await fetch(
          `${API_BASE_URL}/api/employee-details/${userId}`,
          {
            method: "GET",
            headers,
          }
        );

      if (!employeeResponse.ok) {
        let message =
          "Failed to load employee details.";

        try {
          const errorData =
            await employeeResponse.json();

          message =
            errorData?.detail ||
            errorData?.message ||
            message;
        } catch {
          // Ignore JSON parsing error.
        }

        throw new Error(message);
      }

      const employeeData =
        await employeeResponse.json();

      /*
       * IMPORTANT:
       * Backend returns { user, details }.
       * normalizeEmployeeDetails() combines both.
       */
      const normalizedEmployee =
        normalizeEmployeeDetails(
          employeeData
        );

      setEmployee(
        normalizedEmployee
      );

      /* =====================================================
         REPORTING MANAGER
      ===================================================== */

      try {
        const managerResponse =
          await fetch(
            `${API_BASE_URL}/api/reporting-manager/${userId}`,
            {
              method: "GET",
              headers,
            }
          );

        if (managerResponse.ok) {
          const managerData =
            await managerResponse.json();

          setReportingManager(
            managerData?.manager ||
              managerData?.reporting_manager ||
              null
          );

          setLeaveApprovers({
            hod_id: managerData?.hod_id || "",
            hr_admin_id: managerData?.hr_admin_id || "",
            hod: managerData?.hod || null,
            hr_admin: managerData?.hr_admin || null,
          });
        } else {
          setReportingManager(null);
          setLeaveApprovers(EMPTY_LEAVE_APPROVERS);
        }
      } catch {
        setReportingManager(null);
        setLeaveApprovers(EMPTY_LEAVE_APPROVERS);
      }
    } catch (err) {
      console.error(
        "Employee details loading error:",
        err
      );

      setEmployee(null);
      setReportingManager(null);

      setError(
        err?.message ||
          "Unable to load employee details."
      );
    } finally {
      setLoading(false);
    }
  }

  /* =========================================================
     LOAD MANAGER OPTIONS
  ========================================================= */

  async function loadManagerOptions() {
    try {
      const storedSession =
        getStoredSession();

      const token =
        getTokenFromSession(
          storedSession
        );

      const headers = {
        "Content-Type":
          "application/json",
      };

      if (token) {
        headers.Authorization =
          `Bearer ${token}`;
      }

      const response =
        await fetch(
          `${API_BASE_URL}/api/reporting-manager/options`,
          {
            method: "GET",
            headers,
          }
        );

      if (!response.ok) {
        setManagerOptions([]);
        return;
      }

      const data =
        await response.json();

      const options =
        Array.isArray(data)
          ? data
          : Array.isArray(
              data?.options
            )
          ? data.options
          : Array.isArray(
              data?.employees
            )
          ? data.employees
          : Array.isArray(
              data?.users
            )
          ? data.users
          : [];

      setManagerOptions(options);
    } catch (err) {
      console.error(
        "Reporting manager options error:",
        err
      );

      setManagerOptions([]);
    }
  }

  /* =========================================================
     EMPLOYEE CHANGE
  ========================================================= */

  function handleChange(event) {
    const {
      name,
      value,
      type,
      checked,
    } = event.target;

    setEmployee((previous) => ({
      ...(previous || {}),
      [name]:
        type === "checkbox"
          ? checked
          : value,
    }));
  }

  /* =========================================================
     MANAGER CHANGE
  ========================================================= */

  function handleManagerChange(event) {
    const managerId =
      event.target.value;

    const selectedManager =
      managerOptions.find(
        (item) =>
          String(
            item?.id ||
              item?._id ||
              item?.user_id
          ) ===
          String(managerId)
      ) || null;

    setReportingManager(
      managerId
        ? {
            ...(selectedManager || {}),
            id:
              selectedManager?.id ||
              selectedManager?._id ||
              selectedManager?.user_id ||
              managerId,
          }
        : null
    );
  }

  /* =========================================================
     HOD / HR ADMIN CHANGE
  ========================================================= */

  function handleLeaveApproverChange(event) {
    const { name, value } = event.target;

    setLeaveApprovers((previous) => ({
      ...previous,
      [name]: value,
    }));
  }

  /* =========================================================
     EDIT
  ========================================================= */

  function handleEdit() {
    if (!canEditDetails) {
      return;
    }

    setError("");
    setSuccessMessage("");
    setIsEditing(true);
  }

  /* =========================================================
     CANCEL
  ========================================================= */

  function handleCancel() {
    setError("");
    setSuccessMessage("");
    setIsEditing(false);

    loadEmployeeDetails();
  }

  /* =========================================================
     SAVE BASIC USER INFORMATION
     
     Existing User Management API is responsible for:
     employee_id
     name
     work_email
     country_code
     mobile
     role
     password is NOT changed here
  ========================================================= */

  async function saveBasicEmployeeInformation(
    token
  ) {
    const headers = {
      "Content-Type":
        "application/json",
    };

    if (token) {
      headers.Authorization =
        `Bearer ${token}`;
    }

    const basicPayload = {
      employee_id:
        employee?.employee_id || "",

      name:
        employee?.name || "",

      work_email:
        employee?.work_email || "",

      country:
        employee?.phone_country || "",

      country_iso:
        employee?.country_iso || "",

      country_code:
        employee?.country_code || "",

      mobile:
        employee?.mobile || "",

      role:
        employee?.role || "",
    };

    const response =
      await fetch(
        `${API_BASE_URL}/api/users/${
          employee.id || userId
        }`,
        {
          method: "PUT",
          headers,
          body: JSON.stringify(
            basicPayload
          ),
        }
      );

    if (!response.ok) {
      let message =
        "Failed to save employee basic information.";

      try {
        const errorData =
          await response.json();

        message =
          errorData?.detail ||
          errorData?.message ||
          message;
      } catch {
        // Ignore JSON parsing error.
      }

      throw new Error(message);
    }

    return response.json();
  }

  /* =========================================================
     SAVE EMPLOYEE MASTER EXTRA INFORMATION

     This uses the API already created in employee_details.py
  ========================================================= */

  async function saveExtraEmployeeInformation(
    token
  ) {
    const headers = {
      "Content-Type":
        "application/json",
    };

    if (token) {
      headers.Authorization =
        `Bearer ${token}`;
    }

    const extraPayload = {
      date_of_birth:
        employee?.date_of_birth ||
        null,

      blood_group:
        employee?.blood_group || "",

      date_of_joining:
        employee?.date_of_joining ||
        null,

      work_field:
        employee?.work_field || "",

      nationality:
        employee?.nationality || "",

      address:
        employee?.address || "",

      gender:
        employee?.gender || "",

      marital_status:
        employee?.marital_status || "",

      religion:
        employee?.religion || "",

      department:
        employee?.department || "",

      designation:
        employee?.designation || "",

      city:
        employee?.city || "",

      state:
        employee?.state || "",

      country:
        employee?.country || "",
    };

    const response =
      await fetch(
        `${API_BASE_URL}/api/employee-details/${
          employee.id || userId
        }`,
        {
          method: "PUT",
          headers,
          body: JSON.stringify(
            extraPayload
          ),
        }
      );

    if (!response.ok) {
      let message =
        "Failed to save Employee Master extra information.";

      try {
        const errorData =
          await response.json();

        message =
          errorData?.detail ||
          errorData?.message ||
          message;
      } catch {
        // Ignore JSON parsing error.
      }

      throw new Error(message);
    }

    return response.json();
  }

  /* =========================================================
     SAVE REPORTING MANAGER
  ========================================================= */

  async function saveReportingManager(
    token
  ) {
    const headers = {
      "Content-Type":
        "application/json",
    };

    if (token) {
      headers.Authorization =
        `Bearer ${token}`;
    }

    const managerId =
      reportingManager?.id ||
      reportingManager?._id ||
      reportingManager?.user_id ||
      null;

    const response =
      await fetch(
        `${API_BASE_URL}/api/reporting-manager/${
          employee.id || userId
        }`,
        {
          method: "PUT",
          headers,
          body: JSON.stringify({
            reporting_manager_id:
              managerId,
            hod_id:
              leaveApprovers.hod_id || null,
            hr_admin_id:
              leaveApprovers.hr_admin_id || null,
          }),
        }
      );

    if (!response.ok) {
      let message =
        "Reporting Manager, HOD or HR Admin assignment could not be updated.";

      try {
        const errorData =
          await response.json();

        message =
          errorData?.detail ||
          errorData?.message ||
          message;
      } catch {
        // Ignore JSON parsing error.
      }

      throw new Error(message);
    }

    return response.json();
  }

  /* =========================================================
     SAVE
  ========================================================= */

  async function handleSave() {
    if (!employee) {
      return;
    }

    if (!canEditDetails) {
      return;
    }

    setSaving(true);
    setError("");
    setSuccessMessage("");

    try {
      const storedSession =
        getStoredSession();

      const token =
        getTokenFromSession(
          storedSession
        );

      /*
       * 1. Save existing User Management information
       */
      await saveBasicEmployeeInformation(
        token
      );

      /*
       * 2. Save new Employee Master information
       */
      await saveExtraEmployeeInformation(
        token
      );

      /*
       * 3. Save Reporting Manager, HOD and HR Admin (HR Admin only)
       */
      if (canAssignApprovers) {
        await saveReportingManager(
          token
        );
      }

      setSuccessMessage(
        "Employee details updated successfully."
      );

      setIsEditing(false);

      /*
       * Reload everything from MongoDB.
       * This ensures the UI displays the actual
       * saved values instead of only local state.
       */
      await loadEmployeeDetails();
    } catch (err) {
      console.error(
        "Employee details save error:",
        err
      );

      setError(
        err?.message ||
          "Unable to save employee details."
      );
    } finally {
      setSaving(false);
    }
  }

  /* =========================================================
     BACK
  ========================================================= */

  function handleBack() {
    if (openedFromEmployeeMaster) {
      onNavigate?.("employees");
      return;
    }

    onNavigate?.("profile");
  }

  /* =========================================================
     LOADING
  ========================================================= */

  if (loading) {
    return (
      <div className="employee-details-page">
        <div className="employee-details-loading">
          <div className="employee-details-spinner" />

          <span>
            Loading employee details...
          </span>
        </div>
      </div>
    );
  }

  /* =========================================================
     ERROR
  ========================================================= */

  if (error && !employee) {
    return (
      <div className="employee-details-page">
        <div className="employee-details-header">
          <div className="employee-details-header-left">
            <button
              type="button"
              className="employee-details-back-button"
              onClick={handleBack}
            >
              ← Back
            </button>

            <div>
              <h2>
                Employee Details
              </h2>

              <p>
                Unable to load employee
                information.
              </p>
            </div>
          </div>
        </div>

        <div className="employee-details-error">
          {error}
        </div>
      </div>
    );
  }

  /* =========================================================
     EMPTY
  ========================================================= */

  if (!employee) {
    return (
      <div className="employee-details-page">
        <div className="employee-details-header">
          <div className="employee-details-header-left">
            <button
              type="button"
              className="employee-details-back-button"
              onClick={handleBack}
            >
              ← Back
            </button>

            <div>
              <h2>
                Employee Details
              </h2>

              <p>
                No employee information
                found.
              </p>
            </div>
          </div>
        </div>
      </div>
    );
  }

  /* =========================================================
     MANAGER OPTIONS
  ========================================================= */

  const selectedManagerId =
    reportingManager?.id ||
    reportingManager?._id ||
    reportingManager?.user_id ||
    "";

  const managerSelectOptions =
    managerOptions
      .filter((item) => {
        const id =
          item?.id ||
          item?._id ||
          item?.user_id;

        return (
          String(id) !==
          String(
            employee?.id || userId
          )
        );
      })
      .map((item) => ({
        value:
          item?.id ||
          item?._id ||
          item?.user_id,

        label:
          item?.name ||
          item?.full_name ||
          item?.fullName ||
          item?.work_email ||
          item?.email ||
          "Employee",
      }));

  // HOD keeps the stored role "HR Manager"; the backend re-validates both roles.
  const approverOptionsForRole = (role) =>
    managerOptions
      .filter((item) => {
        const id = item?.id || item?._id || item?.user_id;

        return (
          item?.is_active !== false &&
          String(item?.role || "").trim().toLowerCase() === role &&
          String(id) !== String(employee?.id || userId)
        );
      })
      .map((item) => ({
        value: item?.id || item?._id || item?.user_id,
        label: [item?.name, item?.employee_id && `(${item.employee_id})`]
          .filter(Boolean)
          .join(" ") || item?.work_email || "Employee",
      }));

  const hodSelectOptions = approverOptionsForRole("hr manager");

  const hrAdminSelectOptions = approverOptionsForRole("hr admin");

  const approverSummary = (person) =>
    person
      ? [person.name, person.employee_id && `(${person.employee_id})`]
          .filter(Boolean)
          .join(" ")
      : "";

  const roleOptions = [
    {
      value: "HR Admin",
      label: "HR Admin",
    },
    {
      value: "Administrator",
      label: "Administrator",
    },
    {
      value: "HR Manager",
      label: "HOD",
    },
    {
      value: "Employee",
      label: "Employee",
    },
  ];

  /* =========================================================
     BLOOD GROUP OPTIONS
  ========================================================= */

  const bloodGroupOptions = [
    "A+ve",
    "A-ve",
    "B+ve",
    "B-ve",
    "AB+ve",
    "AB-ve",
    "O+ve",
    "O-ve",
  ].map((value) => ({
    value,
    label: value,
  }));

  /* =========================================================
     UI
  ========================================================= */

  return (
    <div className="employee-details-page">

      {/* =====================================================
          HEADER
      ===================================================== */}

      <div className="employee-details-header">
        <div className="employee-details-header-left">

          <button
            type="button"
            className="employee-details-back-button"
            onClick={handleBack}
          >
            <span className="employee-details-back-icon">
              ←
            </span>

            <span>Back</span>
          </button>

          <div className="employee-details-title-block">
            <h2>
              Employee Details
            </h2>

            <p>
              {openedFromEmployeeMaster
                ? "View and manage employee information"
                : "View your complete employee information"}
            </p>
          </div>
        </div>

        <div className="employee-details-header-actions">

          {canEditDetails &&
            !isEditing && (
              <button
                type="button"
                className="employee-details-edit-button"
                onClick={handleEdit}
              >
                Edit
              </button>
            )}

          {canEditDetails &&
            isEditing && (
              <>
                <button
                  type="button"
                  className="employee-details-cancel-button"
                  onClick={handleCancel}
                  disabled={saving}
                >
                  Cancel
                </button>

                <button
                  type="button"
                  className="employee-details-save-button"
                  onClick={handleSave}
                  disabled={saving}
                >
                  {saving
                    ? "Saving..."
                    : "Save Changes"}
                </button>
              </>
            )}

        </div>
      </div>

      {/* =====================================================
          MESSAGES
      ===================================================== */}

      {successMessage && (
        <div className="employee-details-success">
          <span className="employee-details-message-icon">
            ✓
          </span>

          <span>
            {successMessage}
          </span>
        </div>
      )}

      {error && (
        <div className="employee-details-error">
          <span className="employee-details-message-icon">
            !
          </span>

          <span>
            {error}
          </span>
        </div>
      )}

      {/* =====================================================
          BASIC INFORMATION
      ===================================================== */}

      <section className="employee-details-section">

        <div className="employee-details-section-header">
          <div>
            <h3>
              Basic Information
            </h3>

            <p>
              Employee information from
              User Management
            </p>
          </div>
        </div>

        <div className="employee-details-grid">

          <Field
            label="Employee ID"
            name="employee_id"
            value={
              employee.employee_id
            }
            editing={isEditing}
            onChange={handleChange}
          />

          <Field
            label="Employee Name"
            name="name"
            value={employee.name}
            editing={isEditing}
            onChange={handleChange}
          />

          <Field
            label="Work Email"
            name="work_email"
            type="email"
            value={
              employee.work_email
            }
            editing={isEditing}
            onChange={handleChange}
          />

          <Field
            label="Mobile Number"
            name="mobile"
            type="tel"
            value={employee.mobile}
            editing={isEditing}
            onChange={handleChange}
          />

          <Field
            label="Mobile Country"
            name="country"
            value={employee.phone_country}
            editing={false}
            onChange={handleChange}
          />

          <Field
            label="Country Code"
            name="country_code"
            value={
              employee.country_code
            }
            editing={false}
            onChange={handleChange}
          />

          <Field
            label="Country ISO Code"
            name="country_iso"
            value={employee.phone_country_iso}
            editing={false}
            onChange={handleChange}
          />

          <SelectField
            label="Role"
            name="role"
            value={employee.role}
            options={roleOptions}
            editing={isEditing}
            onChange={handleChange}
          />

          <div className="employee-details-field">

            <label className="employee-details-label">
              Account Status
            </label>

            {isEditing ? (
              <label className="employee-details-checkbox">

                <input
                  type="checkbox"
                  name="is_active"
                  checked={Boolean(
                    employee.is_active
                  )}
                  onChange={
                    handleChange
                  }
                />

                <span>
                  {employee.is_active
                    ? "Active"
                    : "Inactive"}
                </span>

              </label>
            ) : (
              <div className="employee-details-value">

                <span
                  className={
                    employee.is_active
                      ? "employee-status-active"
                      : "employee-status-inactive"
                  }
                >
                  {employee.is_active
                    ? "Active"
                    : "Inactive"}
                </span>

              </div>
            )}

          </div>

        </div>
      </section>

      {/* =====================================================
          EMPLOYEE MASTER INFORMATION
      ===================================================== */}

      <section className="employee-details-section">

        <div className="employee-details-section-header">
          <div>
            <h3>
              Employee Master Information
            </h3>

            <p>
              Additional employee information
              maintained in Employee Master
            </p>
          </div>
        </div>

        <div className="employee-details-grid">

          <SelectField
            label="Gender"
            name="gender"
            value={employee.gender}
            options={[
              "Female",
              "Male",
              "Other",
              "Prefer not to say",
            ]}
            editing={isEditing}
            onChange={handleChange}
          />

          <SelectField
            label="Marital Status"
            name="marital_status"
            value={employee.marital_status}
            options={[
              "Single",
              "Married",
              "Divorced",
              "Widowed",
              "Prefer not to say",
            ]}
            editing={isEditing}
            onChange={handleChange}
          />

          <Field
            label="Religion (optional)"
            name="religion"
            value={employee.religion}
            editing={isEditing}
            onChange={handleChange}
            placeholder="Used only for applicable holiday calendars"
          />

          <Field
            label="Department"
            name="department"
            value={employee.department}
            editing={isEditing}
            onChange={handleChange}
            placeholder="Enter department"
          />

          <Field
            label="Designation"
            name="designation"
            value={employee.designation}
            editing={isEditing}
            onChange={handleChange}
            placeholder="Enter designation"
          />

          <Field
            label="City"
            name="city"
            value={employee.city}
            editing={isEditing}
            onChange={handleChange}
            placeholder="Enter city"
          />

          <Field
            label="State"
            name="state"
            value={employee.state}
            editing={isEditing}
            onChange={handleChange}
            placeholder="Enter state"
          />

          <Field
            label="Country"
            name="country"
            value={employee.country}
            editing={isEditing}
            onChange={handleChange}
            placeholder="Enter country"
          />

          <Field
            label="Date of Birth"
            name="date_of_birth"
            type="date"
            value={
              employee.date_of_birth
            }
            editing={isEditing}
            onChange={handleChange}
          />

          <SelectField
            label="Blood Group"
            name="blood_group"
            value={
              employee.blood_group
            }
            options={
              bloodGroupOptions
            }
            editing={isEditing}
            onChange={handleChange}
          />

          <Field
            label="Date of Joining"
            name="date_of_joining"
            type="date"
            value={
              employee.date_of_joining
            }
            editing={isEditing}
            onChange={handleChange}
          />

          <Field
            label="Work Field"
            name="work_field"
            value={
              employee.work_field
            }
            editing={isEditing}
            onChange={handleChange}
            placeholder="Enter work field"
          />

          <Field 
            label="Nationality" 
            name="nationality" 
            value={employee.nationality} 
            editing={isEditing} 
            onChange={handleChange} 
            placeholder="Enter nationality" 
          /> 
          
          <TextAreaField 
            label="Address" 
            name="address" 
            value={employee.address} 
            editing={isEditing} 
            onChange={handleChange} 
            placeholder="Enter employee address" 
          />
        </div>

      </section>

      {/* =====================================================
          REPORTING MANAGER
      ===================================================== */}

      <section className="employee-details-section">

        <div className="employee-details-section-header">
          <div>
            <h3>
              Reporting Manager
            </h3>

            <p>
              Employee reporting structure
              and manager information
            </p>
          </div>
        </div>

        <div className="employee-details-grid">

          {isEditing && canAssignApprovers ? (
            <SelectField
              label="Reporting Manager"
              name="reporting_manager_id"
              value={
                selectedManagerId
              }
              options={
                managerSelectOptions
              }
              editing={true}
              onChange={
                handleManagerChange
              }
            />
          ) : (
            <>
              <div className="employee-details-field">
                <label className="employee-details-label">
                  Manager Name
                </label>

                <div className="employee-details-value">
                  {getDisplayValue(
                    reportingManager?.name ||
                      reportingManager?.full_name ||
                      reportingManager?.fullName
                  )}
                </div>
              </div>

              <div className="employee-details-field">
                <label className="employee-details-label">
                  Manager Employee ID
                </label>

                <div className="employee-details-value">
                  {getDisplayValue(
                    reportingManager?.employee_id
                  )}
                </div>
              </div>

              <div className="employee-details-field">
                <label className="employee-details-label">
                  Email Id
                </label>

                <div className="employee-details-value">
                  {getDisplayValue(
                    reportingManager?.work_email ||
                      reportingManager?.email
                  )}
                </div>
              </div>

              <div className="employee-details-field">
                <label className="employee-details-label">
                  Mobile Number
                </label>

                <div className="employee-details-value">
                  {getDisplayValue(
                    [reportingManager?.country_code,reportingManager?.mobile ||
                      reportingManager?.phone,]
                      .filter(Boolean)
                      .join(" ")
                  )}
                </div>
              </div>
            </>
          )}

        </div>
      </section>

      {/* =====================================================
          LEAVE APPROVERS (HOD / HR ADMIN)
      ===================================================== */}

      <section className="employee-details-section">

        <div className="employee-details-section-header">
          <div>
            <h3>
              Leave Approvers
            </h3>

            <p>
              HOD and HR Admin who approve this employee's leave and permissions, assigned by HR Admin
              {isEditing && !canAssignApprovers ? " (only HR Admin can change these)" : ""}
            </p>
          </div>
        </div>

        <div className="employee-details-grid">

          {isEditing && canAssignApprovers ? (
            <>
              <SelectField
                label="HOD"
                name="hod_id"
                value={leaveApprovers.hod_id}
                options={hodSelectOptions}
                editing={true}
                onChange={handleLeaveApproverChange}
              />

              <SelectField
                label="HR Admin"
                name="hr_admin_id"
                value={leaveApprovers.hr_admin_id}
                options={hrAdminSelectOptions}
                editing={true}
                onChange={handleLeaveApproverChange}
              />
            </>
          ) : (
            <>
              <div className="employee-details-field">
                <label className="employee-details-label">
                  HOD
                </label>

                <div className="employee-details-value">
                  {getDisplayValue(
                    approverSummary(leaveApprovers.hod)
                  )}
                </div>
              </div>

              <div className="employee-details-field">
                <label className="employee-details-label">
                  HR Admin
                </label>

                <div className="employee-details-value">
                  {getDisplayValue(
                    approverSummary(leaveApprovers.hr_admin)
                  )}
                </div>
              </div>
            </>
          )}

        </div>
      </section>

      {/* =====================================================
          ACCOUNT INFORMATION
      ===================================================== */}

      <section className="employee-details-section">

        <div className="employee-details-section-header">
          <div>
            <h3>
              Account Information
            </h3>

            <p>
              System account and activity
              information
            </p>
          </div>
        </div>

        <div className="employee-details-grid">

          <div className="employee-details-field">
            <label className="employee-details-label">
              User ID
            </label>

            <div className="employee-details-value employee-details-mono">
              {getDisplayValue(
                employee.id
              )}
            </div>
          </div>

          <div className="employee-details-field">
            <label className="employee-details-label">
              Created At
            </label>

            <div className="employee-details-value">
              {formatDateTime(
                employee.created_at ||
                  employee.createdAt
              )}
            </div>
          </div>

          <div className="employee-details-field">
            <label className="employee-details-label">
              Last Login
            </label>

            <div className="employee-details-value">
              {formatDateTime(
                employee.last_login ||
                  employee.lastLogin
              )}
            </div>
          </div>

          <div className="employee-details-field">
            <label className="employee-details-label">
              Account Created Date
            </label>

            <div className="employee-details-value">
              {formatDate(
                employee.created_at ||
                  employee.createdAt
              )}
            </div>
          </div>

          <div className="employee-details-field">
            <label className="employee-details-label">
              Number of Logins
            </label>

            <div className="employee-details-value">
              {getDisplayValue(
                employee.login_count
              )}
            </div>
          </div> 

        </div>
      </section>

      {/* =====================================================
          READ ONLY NOTE
      ===================================================== */}

      {!canEditDetails && (
        <div className="employee-details-readonly-note">

          <div className="employee-details-readonly-icon">
            i
          </div>

          <div className="employee-details-readonly-content">

            <strong>
              View Only
            </strong>

            <span>
              Employee information can be
              edited only from Employee Master
              by an authorized HR Admin.
            </span>

          </div>

        </div>
      )}

      {/* =====================================================
          CURRENT USER NOTE
      ===================================================== */}

      {currentUserId &&
        employeeId &&
        String(currentUserId) ===
          String(employeeId) && (
          <div className="employee-details-current-user-note">
            This is your employee profile
            information.
          </div>
        )}

    </div>
  );
}