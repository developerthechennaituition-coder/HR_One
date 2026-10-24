import { HRONE_API_BASE } from "../config/api.js";
import { useState } from "react";


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
    <div className="container-fluid min-vh-100 d-flex align-items-center justify-content-center bg-light">

      <div
        className="card shadow-sm border-0"
        style={{
          width: "100%",
          maxWidth: "450px",
        }}
      >

        <div className="card-body p-4">

          {/* ------------------------------------------------
              Heading
          ------------------------------------------------ */}
          <div className="text-center mb-4">

            <h3 className="fw-bold">
              Forgot Password
            </h3>

            <p className="text-muted mb-0">
              Enter your Employee ID or Work Email to
              reset your password.
            </p>

          </div>


          {/* ------------------------------------------------
              Error
          ------------------------------------------------ */}
          {error && (
            <div
              className="alert alert-danger"
              role="alert"
            >
              {error}
            </div>
          )}


          {/* ------------------------------------------------
              Success
          ------------------------------------------------ */}
          {message && (
            <div
              className="alert alert-success"
              role="alert"
            >
              {message}
            </div>
          )}


          {/* ------------------------------------------------
              Form
          ------------------------------------------------ */}
          <form onSubmit={handleSubmit}>

            <div className="mb-3">

              <label
                htmlFor="identifier"
                className="form-label fw-semibold"
              >
                Employee ID / Work Email
              </label>

              <input
                id="identifier"
                type="text"
                className="form-control"
                placeholder="Enter Employee ID or Work Email"
                value={identifier}
                onChange={(event) =>
                  setIdentifier(event.target.value)
                }
                disabled={loading}
                autoFocus
              />

            </div>


            <button
              type="submit"
              className="btn btn-primary w-100"
              disabled={loading}
            >
              {loading
                ? "Sending OTP..."
                : "Send OTP"}
            </button>

          </form>


          {/* ------------------------------------------------
              Back to Login
          ------------------------------------------------ */}
          <div className="text-center mt-3">

            <button
              type="button"
              className="btn btn-link text-decoration-none"
              onClick={() => {
                window.location.href = "/login";
              }}
            >
              Back to Login
            </button>

          </div>

        </div>

      </div>

    </div>
  );
}


export default ForgotPassword;