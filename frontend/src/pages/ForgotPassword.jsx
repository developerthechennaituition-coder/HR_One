import { HRONE_API_BASE } from "../config/api.js";
import { useState } from "react";
import AuthLayout, { AuthAlert } from "../components/AuthLayout";


function ForgotPassword() {
  const [identifier, setIdentifier] = useState("");

  const [loading, setLoading] = useState(false);

  const [error, setError] = useState("");

  const [message, setMessage] = useState("");


  const handleSubmit = async (event) => {
    event.preventDefault();

    setError("");
    setMessage("");


    // -------------------------------------------------------
    // Basic validation
    // -------------------------------------------------------
    if (!identifier.trim()) {
      setError("Please enter your Employee ID or Work Email.");
      return;
    }


    try {
      setLoading(true);


      // Remove old password reset data
      sessionStorage.removeItem("password_reset_id");
      sessionStorage.removeItem("password_reset_token");


      // -----------------------------------------------------
      // Call backend
      // -----------------------------------------------------
      const response = await fetch(
        HRONE_API_BASE + "/api/auth/forgot-password",
        {
          method: "POST",

          headers: {
            "Content-Type": "application/json",
          },

          body: JSON.stringify({
            identifier: identifier.trim(),
          }),
        }
      );


      const data = await response.json();


      // -----------------------------------------------------
      // Backend error
      // -----------------------------------------------------
      if (!response.ok) {
        throw new Error(
          data.detail || "Unable to process password reset request."
        );
      }


      // -----------------------------------------------------
      // Save reset request ID and OTP expiration time
      // -----------------------------------------------------
      if (data.reset_id) {
        sessionStorage.setItem(
          "password_reset_id",
          data.reset_id
        );
        sessionStorage.setItem(
            "password_reset_expires_at",
            data.otp_expires_at
        );
      }


      // Show backend message
      setMessage(
        data.message ||
          "If an account matches the information provided, a verification OTP has been sent."
      );


      // -----------------------------------------------------
      // Move to OTP verification page
      // -----------------------------------------------------
      setTimeout(() => {
        window.location.href = "/verify-reset-otp";
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


  return (
    <AuthLayout
      icon="bi-key"
      step={1}
      title="Forgot your password?"
      subtitle="No worries. Enter your Employee ID or Work Email and we'll send a 6-digit OTP to your registered email."
      footer={
        <button
          type="button"
          className="auth-link"
          onClick={() => {
            window.location.href = "/login";
          }}
        >
          <i className="bi bi-arrow-left" /> Back to Login
        </button>
      }
    >
      <AuthAlert type="error">{error}</AuthAlert>
      <AuthAlert type="success">{message}</AuthAlert>

      <form className="auth-form" onSubmit={handleSubmit}>
        <div className="auth-field">
          <label htmlFor="identifier">Employee ID / Work Email</label>
          <div className="auth-input">
            <i className="bi bi-envelope-at auth-input-icon" />
            <input
              id="identifier"
              type="text"
              placeholder="Enter Employee ID or Work Email"
              value={identifier}
              onChange={(event) =>
                setIdentifier(event.target.value)
              }
              disabled={loading}
              autoComplete="username"
              autoFocus
            />
          </div>
        </div>

        <button type="submit" className="auth-submit" disabled={loading}>
          {loading ? (
            <>
              <span className="spinner-border spinner-border-sm" aria-hidden="true" />
              Sending OTP...
            </>
          ) : (
            <>
              Send OTP <i className="bi bi-send" />
            </>
          )}
        </button>
      </form>
    </AuthLayout>
  );
}


export default ForgotPassword;
