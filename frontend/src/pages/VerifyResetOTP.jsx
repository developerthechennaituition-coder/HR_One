import { HRONE_API_BASE } from "../config/api.js";
import { useEffect, useState } from "react";

function VerifyResetOTP() {

  const [otp, setOtp] = useState("");

  const [loading, setLoading] = useState(false);

  const [error, setError] = useState("");

  const [message, setMessage] = useState("");

  const [remainingSeconds, setRemainingSeconds] = useState(null);


  // ---------------------------------------------------------
  // Check reset request ID + start countdown
  // ---------------------------------------------------------
  useEffect(() => {

    const resetId = sessionStorage.getItem(
      "password_reset_id"
    );

    const expiresAt = sessionStorage.getItem(
      "password_reset_expires_at"
    );


    // -------------------------------------------------------
    // Reset ID missing
    // -------------------------------------------------------
    if (!resetId) {

      window.location.href = "/forgot-password";

      return;
    }


    // -------------------------------------------------------
    // Expiry time missing
    // -------------------------------------------------------
    if (!expiresAt) {

      setError(
        "OTP session information is missing. Please request a new OTP."
      );

      return;
    }


    // -------------------------------------------------------
    // Countdown function
    // -------------------------------------------------------
    const updateCountdown = () => {

      const expiryTime =
        new Date(expiresAt).getTime();

      const currentTime =
        Date.now();

      const difference =
        expiryTime - currentTime;


      // -----------------------------------------------------
      // OTP expired
      // -----------------------------------------------------
      if (difference <= 0) {

        setRemainingSeconds(0);

        setError(
          "Your OTP has expired. Please request a new OTP."
        );

        return;
      }


      // -----------------------------------------------------
      // Convert milliseconds to seconds
      // -----------------------------------------------------
      const seconds = Math.ceil(
        difference / 1000
      );

      setRemainingSeconds(seconds);

    };


    // Run immediately
    updateCountdown();


    // Update every second
    const timer = setInterval(
      updateCountdown,
      1000
    );


    // Cleanup timer
    return () => {
      clearInterval(timer);
    };

  }, []);


  // ---------------------------------------------------------
  // Format remaining time
  // ---------------------------------------------------------
  const formatTime = (seconds) => {

    if (
      seconds === null ||
      seconds === undefined
    ) {
      return "--:--";
    }


    const minutes =
      Math.floor(seconds / 60);

    const remaining =
      seconds % 60;


    return `${String(minutes).padStart(2, "0")}:${String(
      remaining
    ).padStart(2, "0")}`;

  };


  // ---------------------------------------------------------
  // OTP submit
  // ---------------------------------------------------------
  const handleSubmit = async (event) => {

    event.preventDefault();

    setError("");
    setMessage("");


    const resetId =
      sessionStorage.getItem(
        "password_reset_id"
      );


    // -------------------------------------------------------
    // Check reset ID
    // -------------------------------------------------------
    if (!resetId) {

      setError(
        "Password reset session is missing. Please start again."
      );

      return;
    }


    // -------------------------------------------------------
    // Check countdown
    // -------------------------------------------------------
    if (
      remainingSeconds === null
    ) {

      setError(
        "OTP expiry information is not available. Please start again."
      );

      return;
    }


    if (remainingSeconds <= 0) {

      setError(
        "Your OTP has expired. Please request a new OTP."
      );

      return;
    }


    // -------------------------------------------------------
    // OTP validation
    // -------------------------------------------------------
    const trimmedOtp = otp.trim();


    if (!trimmedOtp) {

      setError(
        "Please enter the OTP."
      );

      return;
    }


    if (!/^\d{6}$/.test(trimmedOtp)) {

      setError(
        "OTP must contain exactly 6 digits."
      );

      return;
    }


    try {

      setLoading(true);


      // -----------------------------------------------------
      // Verify OTP
      // -----------------------------------------------------
      const response = await fetch(
        HRONE_API_BASE + "/api/auth/verify-reset-otp",
        {
          method: "POST",

          headers: {
            "Content-Type": "application/json",
          },

          body: JSON.stringify({
            reset_id: resetId,
            otp: trimmedOtp,
          }),
        }
      );


      const data =
        await response.json();


      // -----------------------------------------------------
      // Backend error
      // -----------------------------------------------------
      if (!response.ok) {

        throw new Error(
          data.detail ||
            "OTP verification failed."
        );

      }


      // -----------------------------------------------------
      // Save reset token
      // -----------------------------------------------------
      if (!data.reset_token) {

        throw new Error(
          "Reset token was not received from the server."
        );

      }


      sessionStorage.setItem(
        "password_reset_token",
        data.reset_token
      );


      // -----------------------------------------------------
      // Remove old reset information
      // -----------------------------------------------------
      sessionStorage.removeItem(
        "password_reset_id"
      );

      sessionStorage.removeItem(
        "password_reset_expires_at"
      );


      setMessage(
        data.message ||
          "OTP verified successfully."
      );


      // -----------------------------------------------------
      // Move to reset password page
      // -----------------------------------------------------
      setTimeout(() => {

        window.location.href =
          "/reset-password";

      }, 500);


    } catch (err) {

      setError(
        err.message ||
          "Unable to verify OTP. Please try again."
      );

    } finally {

      setLoading(false);

    }

  };


  // ---------------------------------------------------------
  // Start again
  // ---------------------------------------------------------
  const handleStartAgain = () => {

    sessionStorage.removeItem(
      "password_reset_id"
    );

    sessionStorage.removeItem(
      "password_reset_token"
    );

    sessionStorage.removeItem(
      "password_reset_expires_at"
    );


    window.location.href =
      "/forgot-password";

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
              Verify OTP
            </h3>

            <p className="text-muted mb-2">
              Enter the 6-digit OTP sent to your
              registered email address.
            </p>


            {/* ------------------------------------------------
                OTP Countdown
            ------------------------------------------------ */}
            {remainingSeconds !== null && (
              <div
                className={
                  remainingSeconds <= 60
                    ? "text-danger fw-semibold"
                    : "text-primary fw-semibold"
                }
              >
                OTP expires in{" "}
                <span>
                  {formatTime(
                    remainingSeconds
                  )}
                </span>
              </div>
            )}

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
                htmlFor="otp"
                className="form-label fw-semibold"
              >
                OTP
              </label>

              <input
                id="otp"
                type="text"
                className="form-control text-center"
                placeholder="Enter 6-digit OTP"
                value={otp}
                onChange={(event) => {

                  const value =
                    event.target.value
                      .replace(/\D/g, "")
                      .slice(0, 6);

                  setOtp(value);

                }}
                maxLength={6}
                inputMode="numeric"
                autoComplete="one-time-code"
                disabled={
                  loading ||
                  remainingSeconds === 0
                }
                autoFocus
              />

            </div>


            <button
              type="submit"
              className="btn btn-primary w-100"
              disabled={
                loading ||
                remainingSeconds === 0
              }
            >

              {loading
                ? "Verifying..."
                : remainingSeconds === 0
                ? "OTP Expired"
                : "Verify OTP"}

            </button>

          </form>


          {/* ------------------------------------------------
              Back
          ------------------------------------------------ */}
          <div className="text-center mt-3">

            <button
              type="button"
              className="btn btn-link text-decoration-none"
              onClick={handleStartAgain}
            >
              Start Again
            </button>

          </div>


        </div>

      </div>

    </div>
  );
}


export default VerifyResetOTP;