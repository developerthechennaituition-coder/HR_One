import { HRONE_API_BASE } from "../config/api.js";
import { useEffect, useState } from "react";


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
              Reset Password
            </h3>

            <p className="text-muted mb-0">
              Create a new password for your HRMS account.
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


            {/* New Password */}
            <div className="mb-3">

              <label
                htmlFor="newPassword"
                className="form-label fw-semibold"
              >
                New Password
              </label>

              <input
                id="newPassword"
                type="password"
                className="form-control"
                placeholder="Enter new password"
                value={newPassword}
                onChange={(event) =>
                  setNewPassword(event.target.value)
                }
                disabled={loading}
                autoComplete="new-password"
                autoFocus
              />

            </div>


            {/* Confirm Password */}
            <div className="mb-3">

              <label
                htmlFor="confirmPassword"
                className="form-label fw-semibold"
              >
                Confirm Password
              </label>

              <input
                id="confirmPassword"
                type="password"
                className="form-control"
                placeholder="Confirm new password"
                value={confirmPassword}
                onChange={(event) =>
                  setConfirmPassword(event.target.value)
                }
                disabled={loading}
                autoComplete="new-password"
              />

            </div>


            {/* Password requirement */}
            <div className="small text-muted mb-3">

              Password must contain at least
              8 characters.

            </div>


            {/* Submit */}
            <button
              type="submit"
              className="btn btn-primary w-100"
              disabled={loading}
            >
              {loading
                ? "Resetting Password..."
                : "Reset Password"}
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

                sessionStorage.removeItem(
                  "password_reset_token"
                );

                sessionStorage.removeItem(
                  "password_reset_id"
                );

                window.location.href =
                  "/login";

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


export default ResetPassword;