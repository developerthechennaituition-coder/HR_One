import { HRONE_API_BASE } from "../config/api.js";
import { useEffect, useRef, useState } from "react";
import AuthLayout, { AuthAlert } from "../components/AuthLayout";

const OTP_LENGTH = 6;

function VerifyResetOTP() {

  const [otp, setOtp] = useState("");

  const [loading, setLoading] = useState(false);

  const [error, setError] = useState("");

  const [message, setMessage] = useState("");

  const [remainingSeconds, setRemainingSeconds] = useState(null);

  const otpInputs = useRef([]);

  // First countdown value; the progress ring drains from here.
  const totalSeconds = useRef(null);
  if (totalSeconds.current === null && remainingSeconds > 0) {
    totalSeconds.current = remainingSeconds;
  }


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
  // OTP digit boxes
  // ---------------------------------------------------------
  const focusDigit = (index) => {
    otpInputs.current[Math.max(0, Math.min(index, OTP_LENGTH - 1))]?.focus();
  };

  const handleDigitChange = (index, rawValue) => {
    const digits = rawValue.replace(/\D/g, "");
    if (!digits) return;
    // Typing fills one box; autofill or paste may deliver several digits at once.
    const chars = otp.padEnd(OTP_LENGTH, " ").split("");
    digits.slice(0, OTP_LENGTH - index).split("").forEach((digit, offset) => {
      chars[index + offset] = digit;
    });
    setOtp(chars.join("").replace(/\s+$/, ""));
    focusDigit(index + digits.length);
  };

  const handleDigitKeyDown = (index, event) => {
    if (event.key === "Backspace") {
      event.preventDefault();
      const chars = otp.padEnd(OTP_LENGTH, " ").split("");
      const target = chars[index].trim() ? index : index - 1;
      if (target < 0) return;
      chars[target] = " ";
      setOtp(chars.join("").replace(/\s+$/, ""));
      focusDigit(target);
    } else if (event.key === "ArrowLeft") {
      focusDigit(index - 1);
    } else if (event.key === "ArrowRight") {
      focusDigit(index + 1);
    }
  };

  const handleDigitPaste = (event) => {
    event.preventDefault();
    const digits = event.clipboardData.getData("text").replace(/\D/g, "").slice(0, OTP_LENGTH);
    if (!digits) return;
    setOtp(digits);
    focusDigit(digits.length);
  };

  const ringProgress =
    remainingSeconds && totalSeconds.current
      ? remainingSeconds / totalSeconds.current
      : 0;


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


  const expired = remainingSeconds === 0;
  const urgent = remainingSeconds !== null && remainingSeconds <= 60;
  const ringCircumference = 2 * Math.PI * 26;

  return (
    <AuthLayout
      icon="bi-shield-lock"
      step={2}
      title="Check your inbox"
      subtitle="Enter the 6-digit OTP sent to your registered email address."
      footer={
        <button
          type="button"
          className="auth-link"
          onClick={handleStartAgain}
        >
          <i className="bi bi-arrow-counterclockwise" /> Didn't get it? Start Again
        </button>
      }
    >
      {/* ------------------------------------------------
          OTP Countdown
      ------------------------------------------------ */}
      {remainingSeconds !== null && (
        <div className={`auth-timer ${expired ? "expired" : urgent ? "urgent" : ""}`}>
          <svg viewBox="0 0 60 60" aria-hidden="true">
            <circle className="auth-timer-track" cx="30" cy="30" r="26" />
            <circle
              className="auth-timer-progress"
              cx="30"
              cy="30"
              r="26"
              strokeDasharray={ringCircumference}
              strokeDashoffset={ringCircumference * (1 - ringProgress)}
            />
          </svg>
          <div>
            <strong>{expired ? "OTP expired" : formatTime(remainingSeconds)}</strong>
            <small>{expired ? "Request a new OTP to continue" : "until your OTP expires"}</small>
          </div>
        </div>
      )}

      <AuthAlert type="error">{error}</AuthAlert>
      <AuthAlert type="success">{message}</AuthAlert>

      <form className="auth-form" onSubmit={handleSubmit}>
        <fieldset className="auth-field">
          <legend className="visually-hidden">One-time password</legend>
          <div className="auth-otp" onPaste={handleDigitPaste}>
            {Array.from({ length: OTP_LENGTH }, (_, index) => {
              const digit = (otp[index] || "").trim();
              return (
                <input
                  key={index}
                  ref={(element) => {
                    otpInputs.current[index] = element;
                  }}
                  type="text"
                  className={digit ? "filled" : ""}
                  value={digit}
                  onChange={(event) => handleDigitChange(index, event.target.value)}
                  onKeyDown={(event) => handleDigitKeyDown(index, event)}
                  onFocus={(event) => event.target.select()}
                  inputMode="numeric"
                  autoComplete={index === 0 ? "one-time-code" : "off"}
                  aria-label={`Digit ${index + 1} of ${OTP_LENGTH}`}
                  disabled={loading || expired}
                  autoFocus={index === 0}
                />
              );
            })}
          </div>
        </fieldset>

        <button
          type="submit"
          className="auth-submit"
          disabled={loading || expired}
        >
          {loading ? (
            <>
              <span className="spinner-border spinner-border-sm" aria-hidden="true" />
              Verifying...
            </>
          ) : expired ? (
            "OTP Expired"
          ) : (
            <>
              Verify OTP <i className="bi bi-patch-check" />
            </>
          )}
        </button>
      </form>
    </AuthLayout>
  );
}


export default VerifyResetOTP;
