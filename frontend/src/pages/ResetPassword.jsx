import { HRONE_API_BASE } from "../config/api.js";
import { useEffect, useState } from "react";
import AuthLayout, { AuthAlert } from "../components/AuthLayout";

// Only the 8-character minimum is enforced; the other checks guide a stronger choice.
const PASSWORD_CHECKS = [
  { label: "At least 8 characters", test: (value) => value.length >= 8 },
  { label: "Upper & lowercase letters", test: (value) => /[a-z]/.test(value) && /[A-Z]/.test(value) },
  { label: "A number", test: (value) => /\d/.test(value) },
  { label: "A special character", test: (value) => /[^A-Za-z0-9]/.test(value) },
];

const STRENGTH_LEVELS = ["Too weak", "Weak", "Fair", "Good", "Strong"];


function ResetPassword() {

  const [newPassword, setNewPassword] =
    useState("");

  const [confirmPassword, setConfirmPassword] =
    useState("");

  const [loading, setLoading] =
    useState(false);

  const [error, setError] =
    useState("");

  const [message, setMessage] =
    useState("");

  const [showPassword, setShowPassword] =
    useState(false);


  // ---------------------------------------------------------
  // Check reset token
  // ---------------------------------------------------------
  useEffect(() => {

    const resetToken =
      sessionStorage.getItem(
        "password_reset_token"
      );


    if (!resetToken) {

      window.location.href =
        "/forgot-password";

    }

  }, []);


  // ---------------------------------------------------------
  // Password reset
  // ---------------------------------------------------------
  const handleSubmit = async (event) => {

    event.preventDefault();

    setError("");
    setMessage("");


    const resetToken =
      sessionStorage.getItem(
        "password_reset_token"
      );


    // -------------------------------------------------------
    // Check token
    // -------------------------------------------------------
    if (!resetToken) {

      setError(
        "Password reset session has expired. Please start again."
      );

      return;
    }


    // -------------------------------------------------------
    // Basic validation
    // -------------------------------------------------------
    if (!newPassword) {

      setError(
        "Please enter your new password."
      );

      return;
    }


    if (!confirmPassword) {

      setError(
        "Please confirm your new password."
      );

      return;
    }


    if (newPassword.length < 8) {

      setError(
        "Password must be at least 8 characters."
      );

      return;
    }


    if (newPassword !== confirmPassword) {

      setError(
        "Passwords do not match."
      );

      return;
    }


    try {

      setLoading(true);


      // -----------------------------------------------------
      // Reset password API
      // -----------------------------------------------------
      const response = await fetch(
        HRONE_API_BASE + "/api/auth/reset-password",
        {
          method: "POST",

          headers: {
            "Content-Type": "application/json",
          },

          body: JSON.stringify({
            reset_token: resetToken,
            new_password: newPassword,
            confirm_password: confirmPassword,
          }),
        }
      );


      const data = await response.json();


      // -----------------------------------------------------
      // Backend error
      // -----------------------------------------------------
      if (!response.ok) {

        throw new Error(
          data.detail ||
            "Unable to reset password."
        );

      }


      // -----------------------------------------------------
      // Password reset successful
      // -----------------------------------------------------
      sessionStorage.removeItem(
        "password_reset_token"
      );

      sessionStorage.removeItem(
        "password_reset_id"
      );


      setMessage(
        data.message ||
          "Password reset successfully."
      );


      // -----------------------------------------------------
      // Go back to login
      // -----------------------------------------------------
      setTimeout(() => {

        window.location.href = "/login";

      }, 1000);


    } catch (err) {

      setError(
        err.message ||
          "Something went wrong. Please try again."
      );

    } finally {

      setLoading(false);

    }
  };


  const passedChecks = PASSWORD_CHECKS.filter((check) => check.test(newPassword));
  const strength = newPassword ? passedChecks.length : 0;
  const passwordsMatch = confirmPassword && newPassword === confirmPassword;

  const leaveReset = () => {
    sessionStorage.removeItem(
      "password_reset_token"
    );

    sessionStorage.removeItem(
      "password_reset_id"
    );

    window.location.href =
      "/login";
  };

  return (
    <AuthLayout
      icon="bi-shield-check"
      step={3}
      title="Create a new password"
      subtitle="Choose a strong password you haven't used before for your HRMS account."
      footer={
        <button
          type="button"
          className="auth-link"
          onClick={leaveReset}
        >
          <i className="bi bi-arrow-left" /> Back to Login
        </button>
      }
    >
      <AuthAlert type="error">{error}</AuthAlert>
      <AuthAlert type="success">{message}</AuthAlert>

      <form className="auth-form" onSubmit={handleSubmit}>
        {/* New Password */}
        <div className="auth-field">
          <label htmlFor="newPassword">New Password</label>
          <div className="auth-input">
            <i className="bi bi-lock auth-input-icon" />
            <input
              id="newPassword"
              type={showPassword ? "text" : "password"}
              placeholder="Enter new password"
              value={newPassword}
              onChange={(event) =>
                setNewPassword(event.target.value)
              }
              disabled={loading}
              autoComplete="new-password"
              autoFocus
            />
            <button
              type="button"
              className="auth-eye"
              onClick={() => setShowPassword(!showPassword)}
              aria-label={showPassword ? "Hide passwords" : "Show passwords"}
            >
              <i className={`bi ${showPassword ? "bi-eye-slash" : "bi-eye"}`} />
            </button>
          </div>

          {/* Password strength */}
          <div className={`auth-strength level-${strength}`} aria-live="polite">
            <div className="auth-strength-bars">
              {[1, 2, 3, 4].map((bar) => <span key={bar} className={bar <= strength ? "on" : ""} />)}
            </div>
            <small>{newPassword ? STRENGTH_LEVELS[strength] : "Password strength"}</small>
          </div>

          <ul className="auth-checklist">
            {PASSWORD_CHECKS.map((check) => {
              const passed = check.test(newPassword);
              return (
                <li key={check.label} className={passed ? "passed" : ""}>
                  <i className={`bi ${passed ? "bi-check-circle-fill" : "bi-circle"}`} />
                  {check.label}
                </li>
              );
            })}
          </ul>
        </div>

        {/* Confirm Password */}
        <div className="auth-field">
          <label htmlFor="confirmPassword">Confirm Password</label>
          <div className={`auth-input ${confirmPassword ? (passwordsMatch ? "is-valid" : "is-invalid") : ""}`}>
            <i className="bi bi-lock-fill auth-input-icon" />
            <input
              id="confirmPassword"
              type={showPassword ? "text" : "password"}
              placeholder="Confirm new password"
              value={confirmPassword}
              onChange={(event) =>
                setConfirmPassword(event.target.value)
              }
              disabled={loading}
              autoComplete="new-password"
            />
          </div>
          {confirmPassword && (
            <small className={`auth-hint ${passwordsMatch ? "auth-hint-ok" : "auth-hint-warn"}`}>
              <i className={`bi ${passwordsMatch ? "bi-check2" : "bi-x"}`} />
              {passwordsMatch ? "Passwords match" : "Passwords do not match yet"}
            </small>
          )}
        </div>

        {/* Submit */}
        <button
          type="submit"
          className="auth-submit"
          disabled={loading}
        >
          {loading ? (
            <>
              <span className="spinner-border spinner-border-sm" aria-hidden="true" />
              Resetting Password...
            </>
          ) : (
            <>
              Reset Password <i className="bi bi-check2-circle" />
            </>
          )}
        </button>
      </form>
    </AuthLayout>
  );
}


export default ResetPassword;
