import { HRONE_API_BASE } from "../config/api.js";
import { useEffect, useRef, useState } from "react";
import { parsePhoneNumberFromString } from "libphonenumber-js/max";
// Purpose: Read libphonenumber-js numbering-plan metadata so the allowed
// phone-number lengths are detected dynamically for each selected country.
import { Metadata } from "libphonenumber-js";
import { roleLabel } from "../services/roleLabels";

// HR administrators create, activate, edit and filter application users here.
const API_URL = HRONE_API_BASE + "/api/users";
const ROLE_OPTIONS = ["HR Admin", "Administrator", "HR Manager", "Employee"];
const emptyUser = {
  employee_id: "",
  name: "",
  work_email: "",
  country_iso: "IN",
  country_code: "+91",
  mobile: "",
  role: "",
  password: "",
};
const userFields = [
  ["employee_id", "Employee ID", "text"],
  ["name", "Name", "text"],
  ["work_email", "Work Email", "email"],
];

// Purpose: Keep the country ISO code, country calling code and country name together.
// ISO code is used for validation because some countries share the same calling code.
const countries = [
  { iso: "IN", name: "India", code: "+91" },
  { iso: "US", name: "United States", code: "+1" },
  { iso: "CA", name: "Canada", code: "+1" },
  { iso: "GB", name: "United Kingdom", code: "+44" },
  { iso: "AU", name: "Australia", code: "+61" },
  { iso: "DE", name: "Germany", code: "+49" },
  { iso: "FR", name: "France", code: "+33" },
  { iso: "IT", name: "Italy", code: "+39" },
  { iso: "ES", name: "Spain", code: "+34" },
  { iso: "AE", name: "United Arab Emirates", code: "+971" },
  { iso: "SA", name: "Saudi Arabia", code: "+966" },
  { iso: "QA", name: "Qatar", code: "+974" },
  { iso: "SG", name: "Singapore", code: "+65" },
  { iso: "MY", name: "Malaysia", code: "+60" },
  { iso: "JP", name: "Japan", code: "+81" },
  { iso: "KR", name: "South Korea", code: "+82" },
  { iso: "CN", name: "China", code: "+86" },
  { iso: "BR", name: "Brazil", code: "+55" },
  { iso: "ZA", name: "South Africa", code: "+27" },
];


function UserManagement() {
  const [users, setUsers] = useState([]);
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [roleFilter, setRoleFilter] = useState("all");
  const [formValues, setFormValues] = useState(emptyUser);
  const [isFormOpen, setIsFormOpen] = useState(() =>
    window.location.pathname.endsWith("/admin/users/new"),
  );
  const [editingId, setEditingId] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [feedback, setFeedback] = useState({ type: "", message: "" });
  const [fieldErrors, setFieldErrors] = useState({});

  // Purpose: Store the user selected from the three-dot menu.
  // This object is used to display the selected user's complete details in the View modal.
  const [viewingUser, setViewingUser] = useState(null);

  // Purpose: Store the ID of the user whose three-dot action menu is currently open.
  // This ensures only one action menu is displayed at a time.
  const [openActionMenuId, setOpenActionMenuId] = useState(null);

  // Purpose: Keep the feedback timer outside the render cycle so repeated
  // feedback updates do not create unnecessary component re-renders.
  const feedbackTimerRef = useRef(null);

  const readError = async (response) => {
    try {
      const error = await response.json();
      return error.detail || "The request could not be completed.";
    } catch {
      return "The request could not be completed.";
    }
  };

  // Purpose: Show feedback for exactly 5 seconds and then remove it
  // automatically, so the message does not stay until the page is refreshed.
  const showFeedback = (type, message) => {
    // Purpose: Cancel any previous timer so a new message always gets
    // its full 5-second display time.
    if (feedbackTimerRef.current) {
      clearTimeout(feedbackTimerRef.current);
    }

    setFeedback({ type, message });

    // Purpose: Automatically hide the feedback message after 5 seconds.
    feedbackTimerRef.current = setTimeout(() => {
      setFeedback({ type: "", message: "" });
      feedbackTimerRef.current = null;
    }, 5000);
  };

  // Purpose: Clear the active timer when the component is removed to avoid
  // updating state after the User Management page has been unmounted.
  useEffect(() => {
    return () => {
      if (feedbackTimerRef.current) {
        clearTimeout(feedbackTimerRef.current);
        feedbackTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    const loadUsers = async () => {
      try {
        const response = await fetch(API_URL);
        if (!response.ok) throw new Error(await readError(response));
        setUsers(await response.json());
      } catch (error) {
        showFeedback(
          "danger",
          error.message || "Unable to load users.",
        );
      } finally {
        setIsLoading(false);
      }
    };
    loadUsers();
  }, []);

  // Apply search, account-status, and role filters before rendering the table.
  const filteredUsers = users.filter((user) => {
    const query = searchTerm.toLowerCase();
    const matchesSearch =
      user.name.toLowerCase().includes(query) ||
      user.work_email.toLowerCase().includes(query) ||
      (user.employee_id || "").toLowerCase().includes(query);
    const matchesStatus =
      statusFilter === "all" ||
      (statusFilter === "active" ? user.is_active : !user.is_active);
    const matchesRole = roleFilter === "all" || user.role === roleFilter;
    return matchesSearch && matchesStatus && matchesRole;
  });

  //Reset search and filters to default values
  const handleReset = () => {
    setSearchTerm("");
    setStatusFilter("all");
    setRoleFilter("all");
  };

  const openAddForm = () => {
    setEditingId(null);
    setFormValues(emptyUser);
    setFeedback({ type: "", message: "" });
    setFieldErrors({});
    setIsFormOpen(true);
  };

  const openEditForm = (user) => {
    setEditingId(user.id);
    // Prefer explicit country identity; old records may only have an international number.
    const parsedPhone = parsePhoneNumberFromString(
      String(user.mobile || ""),
    );
    const savedCountry =
      countries.find((country) => country.iso === user.country_iso) ||
      countries.find(
        (country) =>
          country.name.toLowerCase() ===
          String(user.country || "").toLowerCase(),
      ) ||
      countries.find((country) => country.iso === parsedPhone?.country) ||
      countries.find((country) => country.code === user.country_code) ||
      countries.find((country) => country.iso === "IN");

    const parsedNationalNumber =
      parsedPhone?.country === savedCountry?.iso
        ? parsedPhone.nationalNumber
        : null;

    setFormValues({
      employee_id: user.employee_id || "",
      name: user.name || "",
      work_email: user.work_email || "",
      country_iso: savedCountry.iso,
      country_code: savedCountry.code,
      mobile:
        parsedNationalNumber ||
        String(user.mobile || "").replace(/\D/g, ""),
      role: user.role || "",
      password: "",
    });
    setFeedback({ type: "", message: "" });
    setFieldErrors({});
    setIsFormOpen(true);
  };

  const closeForm = () => {
    setEditingId(null);
    setFormValues(emptyUser);
    setIsFormOpen(false);
    setFieldErrors({});
  };

  // Purpose: Open the selected user's details in the View modal.
  const handleViewUser = (user) => {
    setViewingUser(user);
    setOpenActionMenuId(null);
  };

  // Purpose: Close the user details modal and clear the selected user.
  const closeViewModal = () => {
    setViewingUser(null);
  };

  // Purpose: Delete the selected user after confirmation and update the table
  // immediately without requiring a page refresh.
  const handleDeleteUser = async (user) => {
    const confirmed = window.confirm(
      `Are you sure you want to delete ${user.name}?`,
    );

    if (!confirmed) return;

    try {
      // Purpose: Send the DELETE request to the backend for the selected user.
      const response = await fetch(`${API_URL}/${user.id}`, {
        method: "DELETE",
      });

      if (!response.ok) throw new Error(await readError(response));

      // Purpose: Remove the deleted user from the current table immediately.
      setUsers((currentUsers) =>
        currentUsers.filter((item) => item.id !== user.id),
      );

      // Purpose: Close the three-dot menu after successful deletion.
      setOpenActionMenuId(null);

      // Purpose: Show successful deletion feedback using the existing feedback system.
      showFeedback("success", "User deleted successfully.");
    } catch (error) {
      showFeedback(
        "danger",
        error.message || "Unable to delete user.",
      );
    }
  };

  // Validate form fields and set error messages
  const validateForm = () => {
    const errors = {};
    if (!editingId && !formValues.employee_id.trim())
      errors.employee_id = "Employee ID is required.";
    if (!formValues.name.trim()) errors.name = "Name is required.";
    if (!formValues.work_email.trim())
      errors.work_email = "Work email is required.";
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(formValues.work_email.trim()))
      errors.work_email = "Enter a valid work email.";
    // Purpose: Validate the mobile number using the selected country's
    // dynamically loaded numbering-plan rules.
    if (!formValues.mobile.trim()) {
      errors.mobile = "Mobile number is required.";
    } else {
      // Purpose: Find the country selected by the HR user.
      const selectedCountry = countries.find(
        (country) => country.iso === formValues.country_iso,
      );

      // Purpose: Keep only numeric digits because the country calling code
      // is already displayed and stored separately.
      const mobileNumber = formValues.mobile.trim().replace(/\D/g, "");

      if (!selectedCountry) {
        // Purpose: Stop validation if the selected country is not available
        // in the supported country list.
        errors.mobile = "Please select a valid country.";
      } else {
        // Purpose: Read the country's allowed national-number lengths
        // directly from libphonenumber-js metadata instead of hard-coding
        // values such as 10 digits or 8 digits.
        const metadata = new Metadata();
        metadata.selectNumberingPlan(selectedCountry.iso);

        // Purpose: Get every possible national phone-number length supported
        // by the selected country's numbering plan.
        const allowedLengths =
          metadata.numberingPlan.possibleLengths();

        // Purpose: Check whether the entered number contains one of the
        // lengths officially supported by the selected country.
        const isLengthValid = allowedLengths.includes(
          mobileNumber.length,
        );

        if (!isLengthValid) {
          // Purpose: Create a user-friendly message when the number length
          // is incorrect. Multiple valid lengths are displayed when present.
          const lengthMessage =
            allowedLengths.length === 1
              ? `${allowedLengths[0]} digit${allowedLengths[0] === 1 ? "" : "s"}`
              : allowedLengths
                  .map((length) => `${length} digits`)
                  .join(" or ");

          // Purpose: Tell the user exactly how many digits are expected
          // and how many digits they actually entered.
          errors.mobile =
            `${selectedCountry.name} mobile number must contain ` +
            `${lengthMessage}. You entered ${mobileNumber.length} digits.`;
        } else {
          // Purpose: Perform strict country-specific validation after the
          // number length has passed the metadata length check.
          const phoneNumber = parsePhoneNumberFromString(
            mobileNumber,
            selectedCountry.iso,
          );

          // Purpose: Reject numbers that have the correct length but do not
          // match the selected country's valid phone-number digit pattern.
          if (
            !phoneNumber ||
            !phoneNumber.isValid() ||
            phoneNumber.country !== selectedCountry.iso
          ) {
            errors.mobile =
              `Enter a valid ${selectedCountry.name} mobile number.`;
          }
        }
      }
    }
    
    if (!formValues.role) errors.role = "Select one role.";
    if (!editingId && !formValues.password.trim())
      errors.password = "Password is required.";
    else if (!editingId && formValues.password.length < 8)
      errors.password = "Password must contain at least 8 characters.";
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) {
      showFeedback(
        "danger",
        "Please complete all required fields.",
      );
    }
    return Object.keys(errors).length === 0;
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!validateForm()) return;

    setIsSaving(true);
    try {
      const response = await fetch(
        editingId ? `${API_URL}/${editingId}` : API_URL,
        {
          method: editingId ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          // Purpose: Persist the same country selection used to validate the phone number.
          body: JSON.stringify({
            employee_id: formValues.employee_id.trim(),
            name: formValues.name.trim(),
            work_email: formValues.work_email.trim(),
            country: countries.find(
              (country) => country.iso === formValues.country_iso,
            )?.name || "",
            country_iso: formValues.country_iso,
            country_code: formValues.country_code.trim(),
            mobile: formValues.mobile.trim(),
            role: formValues.role.trim(),
            password: formValues.password.trim(),
          }),
        },
      );
      if (!response.ok) throw new Error(await readError(response));
      const result = await response.json();
      const savedUser = result.user || result;
      setUsers((currentUsers) =>
        editingId
          ? currentUsers.map((user) =>
              user.id === editingId ? savedUser : user,
            )
          : [savedUser, ...currentUsers],
      );
      showFeedback(
        "success",
        editingId
          ? "User updated successfully."
          : result.activation_url
            ? `User created successfully. Development activation link: ${result.activation_url}`
            : "User created successfully. An activation email has been sent.",
      );
      closeForm();
    } catch (error) {
      showFeedback(
        "danger",
        error.message || "Unable to save user.",
      );
    } finally {
      setIsSaving(false);
    }
  };

  const toggleStatus = async (user) => {
    const shouldDeactivate = user.is_active;
    if (shouldDeactivate && !window.confirm(`Deactivate ${user.name}?`)) return;
    try {
      const response = await fetch(`${API_URL}/${user.id}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: !shouldDeactivate }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const updatedUser = await response.json();
      setUsers((currentUsers) =>
        currentUsers.map((item) => (item.id === user.id ? updatedUser : item)),
      );
      showFeedback(
        "success",
        "User status updated successfully.",
      );
    } catch (error) {
      showFeedback(
        "danger",
        error.message || "Unable to update user status.",
      );
    }
  };

  return (
    <section className="user-management-page">
      {/* Purpose: Display the selected user's complete information when View is clicked. */}
      {viewingUser && (
        <div
          className="modal d-block"
          tabIndex="-1"
          role="dialog"
          aria-modal="true"
          style={{ backgroundColor: "rgba(0, 0, 0, 0.5)" }}
        >
          <div className="modal-dialog modal-lg modal-dialog-centered">
            <div className="modal-content">
              <div className="modal-header">
                <h5 className="modal-title">User Details</h5>
                <button
                  type="button"
                  className="btn-close"
                  onClick={closeViewModal}
                  aria-label="Close"
                ></button>
              </div>

              <div className="modal-body">
                <div className="row g-3">
                  <div className="col-md-6">
                    <label className="form-label fw-semibold">Employee ID</label>
                    <div className="form-control bg-light">
                      {viewingUser.employee_id || "-"}
                    </div>
                  </div>

                  <div className="col-md-6">
                    <label className="form-label fw-semibold">Name</label>
                    <div className="form-control bg-light">
                      {viewingUser.name || "-"}
                    </div>
                  </div>

                  <div className="col-md-6">
                    <label className="form-label fw-semibold">Work Email</label>
                    <div className="form-control bg-light">
                      {viewingUser.work_email || "-"}
                    </div>
                  </div>

                  <div className="col-md-6">
                    <label className="form-label fw-semibold">Mobile</label>
                    <div className="form-control bg-light">
                      {viewingUser.country_code
                        ? `${viewingUser.country_code} ${viewingUser.mobile || ""}`
                        : viewingUser.mobile || "-"}
                    </div>
                  </div>

                  <div className="col-md-6">
                    <label className="form-label fw-semibold">Role</label>
                    <div className="form-control bg-light">
                      {roleLabel(viewingUser.role) || "-"}
                    </div>
                  </div>

                  <div className="col-md-6">
                    <label className="form-label fw-semibold">Status</label>
                    <div className="form-control bg-light">
                      {viewingUser.is_active ? "Active" : "Inactive"}
                    </div>
                  </div>

                  <div className="col-md-6">
                    <label className="form-label fw-semibold">User ID</label>
                    <div className="form-control bg-light">
                      {viewingUser.id || "-"}
                    </div>
                  </div>

                  <div className="col-md-6">
                    <label className="form-label fw-semibold">Country</label>
                    <div className="form-control bg-light">
                      {viewingUser.country ||
                        countries.find(
                          (country) => country.iso === viewingUser.country_iso,
                        )?.name ||
                        viewingUser.country_iso ||
                        "-"}
                    </div>
                  </div>
                </div>
              </div>

              <div className="modal-footer">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={closeViewModal}
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="user-management-toolbar d-flex flex-wrap justify-content-between align-items-center gap-3 mb-4">
        <h2 className="mb-0">User Management</h2>
        <button type="button" className="btn btn-primary" onClick={openAddForm}>
          + Add User
        </button>
      </div>

      {feedback.message && (
        <div
          className={`alert alert-${feedback.type}`}
          role="alert"
          aria-live="polite"
        >
          {feedback.message}
        </div>
      )}

      {isFormOpen && (
        <div className="card border-0 shadow-sm mb-4">
          <div className="card-body p-4">
            <h5 className="card-title mb-4">
              {editingId ? "Edit User" : "Add User"}
            </h5>
            <form onSubmit={handleSubmit}>
              <div className="row g-3">
                {editingId && (
                  <div className="col-md-6">
                    <label htmlFor="user-id" className="form-label">
                      User ID
                    </label>
                    <input
                      id="user-id"
                      type="text"
                      className="form-control"
                      value={editingId}
                      readOnly
                      aria-readonly="true"
                    />
                  </div>
                )}
                {userFields.map(([name, label, type]) => (
                  <div className="col-md-6" key={name}>
                    <label htmlFor={`user-${name}`} className="form-label">
                      {label}
                    </label>
                    <input
                      id={`user-${name}`}
                      name={name}
                      type={type}
                      required
                      className={`form-control ${fieldErrors[name] ? "is-invalid" : ""}`}
                      value={formValues[name]}
                      onChange={(event) => {
                        setFormValues({
                          ...formValues,
                          [name]: event.target.value,
                        });
                        setFieldErrors({ ...fieldErrors, [name]: "" });
                      }}
                    />
                    {fieldErrors[name] && (
                      <div className="invalid-feedback">
                        {fieldErrors[name]}
                      </div>
                    )}
                  </div>
                ))}                    
                {/* Purpose: Let HR select the employee's country so the country-specific
                    mobile validation and country calling code stay synchronized. */}
                <div className="col-md-6">
                  <label htmlFor="user-country" className="form-label">
                    Country
                  </label>
                  <select
                    id="user-country"
                    name="country_iso"
                    required
                    className="form-select"
                    value={formValues.country_iso}
                    onChange={(event) => {
                      const selectedCountry = countries.find(
                        (country) => country.iso === event.target.value,
                      );

                      setFormValues({
                        ...formValues,
                        country_iso: selectedCountry?.iso || "",
                        country_code: selectedCountry?.code || "",
                        mobile: "",
                      });

                      setFieldErrors({ ...fieldErrors, mobile: "" });
                    }}
                  >
                    {countries.map((country) => (
                      <option key={country.iso} value={country.iso}>
                        {country.name} ({country.code})
                      </option>
                    ))}
                  </select>
                </div>

                {/* Purpose: Show the selected country calling code separately and accept
                    only the national mobile number from the employee. */}
                <div className="col-md-6">
                  <label htmlFor="user-mobile" className="form-label">
                    Mobile Number
                  </label>
                  <div className="input-group">
                    <span className="input-group-text">
                      {formValues.country_code}
                    </span>
                    <input
                      id="user-mobile"
                      name="mobile"
                      type="tel"
                      inputMode="numeric"
                      autoComplete="tel-national"
                      required
                      className={`form-control ${fieldErrors.mobile ? "is-invalid" : ""}`}
                      value={formValues.mobile}
                      placeholder="Enter mobile number"
                      onChange={(event) => {
                        // Purpose: Keep the stored mobile value numeric-only because the
                        // selected country code is already stored separately.
                        const value = event.target.value.replace(/\D/g, "");

                        setFormValues({
                          ...formValues,
                          mobile: value,
                        });

                        setFieldErrors({ ...fieldErrors, mobile: "" });
                      }}
                    />
                  </div>
                  {fieldErrors.mobile && (
                    <div className="invalid-feedback d-block">
                      {fieldErrors.mobile}
                    </div>
                  )}
                </div>

                {!editingId && (
                  <div className="col-md-6">
                    <label htmlFor="user-password" className="form-label">
                      Password
                    </label>
                    <input
                      id="user-password"
                      name="password"
                      type="password"
                      autoComplete="new-password"
                      required
                      minLength="8"
                      className={`form-control ${fieldErrors.password ? "is-invalid" : ""}`}
                      value={formValues.password}
                      onChange={(event) => {
                        setFormValues({
                          ...formValues,
                          password: event.target.value,
                        });
                        setFieldErrors({ ...fieldErrors, password: "" });
                      }}
                    />
                    {fieldErrors.password && (
                      <div className="invalid-feedback">
                        {fieldErrors.password}
                      </div>
                    )}
                  </div>
                )}
                <div className="col-md-6">
                  <label htmlFor="user-role" className="form-label">
                    Role
                  </label>
                  <select
                    id="user-role"
                    name="role"
                    required
                    className={`form-select ${fieldErrors.role ? "is-invalid" : ""}`}
                    value={formValues.role}
                    onChange={(event) => {
                      setFormValues({
                        ...formValues,
                        role: event.target.value,
                      });
                      setFieldErrors({ ...fieldErrors, role: "" });
                    }}
                  >
                    <option value="">Select a role</option>
                    {ROLE_OPTIONS.map((role) => (
                      <option key={role} value={role}>
                        {roleLabel(role)}
                      </option>
                    ))}
                  </select>
                  {fieldErrors.role && (
                    <div className="invalid-feedback">{fieldErrors.role}</div>
                  )}
                </div>
              </div>
              <div className="d-flex justify-content-end gap-2 mt-4">
                <button
                  type="button"
                  className="btn btn-outline-secondary"
                  onClick={closeForm}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isSaving}
                >
                  {isSaving ? "Saving..." : "Save"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <div className="card border-0 shadow-sm">
        <div className="card-body">
          <div className="row g-3 mb-4">
            <div className="col-md-6">
              <label htmlFor="user-search" className="visually-hidden">
                Search users
              </label>
              <input
                id="user-search"
                type="search"
                className="form-control"
                placeholder="Search by Employee ID, Name or Work Email"
                value={searchTerm}
                onChange={(event) => setSearchTerm(event.target.value)}
              />
            </div>
            <div className="col-md-2">
              <label htmlFor="user-status-filter" className="visually-hidden">
                Filter by Status
              </label>
              <select
                id="user-status-filter"
                className="form-select"
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value)}
              >
                <option value="all">All Status</option>
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
              </select>
            </div>
            <div className="col-md-2">
              <label htmlFor="user-role-filter" className="visually-hidden">
                Filter by Role
              </label>
              <select
                id="user-role-filter"
                className="form-select"
                value={roleFilter}
                onChange={(event) => setRoleFilter(event.target.value)}
              >
                <option value="all">All Role</option>
                <option value="Super Admin">Super Admin</option>
                <option value="HR Admin">HR Admin</option>
                <option value="HR Manager">HOD</option>
                <option value="Employee">Employee</option>
              </select>
            </div>
            <div className="col-md-2 d-flex align-items-center">
              <button
                type="button"
                className="btn btn-outline-primary"
                onClick={handleReset}
              >
                <i className="bi bi-arrow-clockwise me-2"></i>Reset
              </button>
            </div>
          </div>
          <div className="user-table-wrapper table-responsive">
            <table className="user-table table table-hover align-middle mb-0">
              <thead>
                <tr>
                  <th>Employee ID</th>
                  <th>Name</th>
                  <th>Work Email</th>
                  <th>Mobile</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {isLoading ? (
                  <tr>
                    <td colSpan="7" className="text-center text-muted py-4">
                      Loading users...
                    </td>
                  </tr>
                ) : filteredUsers.length === 0 ? (
                  <tr>
                    <td colSpan="7" className="text-center text-muted py-4">
                      No users available
                    </td>
                  </tr>
                ) : (
                  filteredUsers.map((user) => (
                    <tr key={user.id}>
                      <td>{user.employee_id}</td>
                      <td>{user.name}</td>
                      <td>{user.work_email}</td>
                      <td>{user.country_code ? `${user.country_code} ${user.mobile}` : user.mobile}</td>
                      <td>{roleLabel(user.role)}</td>
                      <td>
                        <span
                          className={`badge ${user.is_active ? "text-bg-success" : "text-bg-secondary"}`}
                        >
                          {user.is_active ? "Active" : "Inactive"}
                        </span>
                      </td>
                      <td className="text-nowrap">
                        {/* Purpose: Keep Edit, Activate/Deactivate and additional actions together. */}
                        <div className="d-flex align-items-center gap-2">
                          <button
                            type="button"
                            className="btn btn-sm btn-outline-primary"
                            onClick={() => openEditForm(user)}
                            title="Edit"
                          >
                            <i className="bi bi-pencil"></i>
                          </button>

                          <button
                            type="button"
                            className={`btn btn-sm ${user.is_active ? "btn-outline-success" : "btn-outline-secondary"}`}
                            onClick={() => toggleStatus(user)}
                            title={user.is_active ? "Deactivate" : "Activate"}
                          >
                            <i
                              className={`bi ${user.is_active ? "bi-toggle-on" : "bi-toggle-off"}`}
                            ></i>
                          </button>

                          {/* Purpose: Toggle the extra View/Delete actions for this specific user. */}
                          <div className="position-relative">
                            <button
                              type="button"
                              className="btn btn-sm btn-outline-secondary"
                              onClick={() =>
                                setOpenActionMenuId(
                                  openActionMenuId === user.id ? null : user.id,
                                )
                              }
                              title="More actions"
                              aria-label={`More actions for ${user.name}`}
                            >
                              <i className="bi bi-three-dots-vertical"></i>
                            </button>

                            {openActionMenuId === user.id && (
                              <div
                                className="position-absolute bg-white border rounded shadow-sm p-1"
                                style={{
                                  left: 0,
                                  top: "100%",
                                  zIndex: 1050,
                                  minWidth: "140px",
                                }}
                              >
                                {/* Purpose: Open a modal containing all available details for this user. */}
                                <button
                                  type="button"
                                  className="dropdown-item rounded"
                                  onClick={() => handleViewUser(user)}
                                >
                                  <i className="bi bi-eye me-2"></i>
                                  View
                                </button>

                                {/* Purpose: Permanently delete this user after confirmation. */}
                                <button
                                  type="button"
                                  className="dropdown-item rounded text-danger"
                                  onClick={() => handleDeleteUser(user)}
                                >
                                  <i className="bi bi-trash me-2"></i>
                                  Delete
                                </button>
                              </div>
                            )}
                          </div>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
}

export default UserManagement;
