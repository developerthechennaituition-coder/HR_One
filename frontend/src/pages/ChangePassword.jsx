import { HRONE_API_BASE } from "../config/api.js";
import { useState } from "react";

const API_URL = HRONE_API_BASE + "/api/auth/change-password";

function ChangePassword({ onNavigate }) {
  const [formValues, setFormValues] = useState({
    current_password: "",
    new_password: "",
    confirm_password: "",
  });
  const [showPasswords, setShowPasswords] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const handleChange = (event) => {
    const { name, value } = event.target;
    setFormValues((previous) => ({ ...previous, [name]: value }));
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError("");
    setSuccess("");

    if (formValues.new_password.length < 8) {
      setError("New password must be at least 8 characters.");
      return;
    }

    if (formValues.new_password !== formValues.confirm_password) {
      setError("New password and confirm password do not match.");
      return;
    }

    const storedSession = sessionStorage.getItem("hrms_session");
    let session;

    try {
      session = JSON.parse(storedSession || "null");
    } catch {
      session = null;
    }

    if (!session?.token) {
      setError("Your session has expired. Please login again.");
      return;
    }

    setIsSubmitting(true);

    try {
      const response = await fetch(API_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session.token}`,
        },
        body: JSON.stringify({
          current_password: formValues.current_password,
          new_password: formValues.new_password,
        }),
      });

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(data?.detail || "Unable to change password.");
      }

      setFormValues({
        current_password: "",
        new_password: "",
        confirm_password: "",
      });
      setSuccess(data?.message || "Password changed successfully.");
    } catch (requestError) {
      setError(requestError.message || "Unable to change password.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <section className="container-fluid px-0">
      <div className="d-flex justify-content-end mt-4">
        <button
          type="button"
          className="btn btn-outline-primary"
          onClick={() =>
          onNavigate?.(
            "profile"
          )
          }
        >
        <i className="bi bi-person me-2"></i>
          Back to Profile
        </button>
      </div>
      <div className="mb-4">
        <h2 className="mb-1">Change Password</h2>
        <p className="text-muted mb-0">
          Update your account password securely.
        </p>
      </div>

      <div className="card border-0 shadow-sm" style={{ maxWidth: "560px" }}>
        <div className="card-body p-4">
          {error && (
            <div className="alert alert-danger" role="alert">
              {error}
            </div>
          )}

          {success && (
            <div className="alert alert-success" role="alert">
              {success}
            </div>
          )}

          <form onSubmit={handleSubmit}>
            <div className="mb-3">
              <label htmlFor="current-password" className="form-label">
                Current Password
              </label>
              <input
                id="current-password"
                name="current_password"
                type={showPasswords ? "text" : "password"}
                className="form-control"
                autoComplete="current-password"
                required
                value={formValues.current_password}
                onChange={handleChange}
              />
            </div>

            <div className="mb-3">
              <label htmlFor="new-password" className="form-label">
                New Password
              </label>
              <input
                id="new-password"
                name="new_password"
                type={showPasswords ? "text" : "password"}
                className="form-control"
                autoComplete="new-password"
                minLength={8}
                required
                value={formValues.new_password}
                onChange={handleChange}
              />
              <div className="form-text">Minimum 8 characters.</div>
            </div>

            <div className="mb-3">
              <label htmlFor="confirm-password" className="form-label">
                Confirm New Password
              </label>
              <input
                id="confirm-password"
                name="confirm_password"
                type={showPasswords ? "text" : "password"}
                className="form-control"
                autoComplete="new-password"
                minLength={8}
                required
                value={formValues.confirm_password}
                onChange={handleChange}
              />
            </div>

            <div className="form-check mb-4">
              <input
                id="show-passwords"
                className="form-check-input"
                type="checkbox"
                checked={showPasswords}
                onChange={(event) => setShowPasswords(event.target.checked)}
              />
              <label className="form-check-label" htmlFor="show-passwords">
                Show passwords
              </label>
            </div>

            <button
              type="submit"
              className="btn btn-primary"
              disabled={isSubmitting}
            >
              {isSubmitting ? "Changing Password..." : "Change Password"}
            </button>
          </form>
        </div>
      </div>
    </section>
  );
}

export default ChangePassword;
