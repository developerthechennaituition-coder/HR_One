import { HRONE_API_BASE } from "../config/api.js";
import { useEffect, useState } from "react";

// Validate the one-time token first, then submit the account password.
const API_URL = HRONE_API_BASE + "/api/auth/activate";

function ActivateAccount() {
  const token = new URLSearchParams(window.location.search).get("token") || "";
  const [details, setDetails] = useState(null);
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Do not render the password form until the token has been accepted.
  useEffect(() => {
    if (!token) {
      setError("This activation link is invalid or expired.");
      return;
    }
    fetch(`${API_URL}?token=${encodeURIComponent(token)}`)
      .then(async (response) => {
        if (!response.ok)
          throw new Error(
            (await response.json()).detail ||
              "This activation link is invalid or expired.",
          );
        return response.json();
      })
      .then(setDetails)
      .catch((requestError) => setError(requestError.message));
  }, [token]);

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (password.length < 8)
      return setError("Password must be at least 8 characters.");
    if (password !== confirmation) return setError("Passwords do not match.");
    setIsSubmitting(true);
    setError("");
    try {
      const response = await fetch(
        `${API_URL}?token=${encodeURIComponent(token)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password }),
        },
      );
      if (!response.ok)
        throw new Error(
          (await response.json()).detail || "Unable to activate account.",
        );
      setSuccess(true);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="login-page">
      <section className="login-card card border-0 shadow-sm">
        <div className="card-body p-4 p-md-5">
          <div className="login-brand">HRMS</div>
          {success ? (
            <>
              <h1>Account activated</h1>
              <p className="text-muted">
                Your password is set and your account is ready.
              </p>
              <a className="btn btn-primary w-100" href="/login">
                Go to Sign In
              </a>
            </>
          ) : (
            <>
              <h1>Activate your account</h1>
              {details && (
                <p className="text-muted">
                  Set a password for {details.work_email}.
                </p>
              )}
              {error && (
                <div className="alert alert-danger" role="alert">
                  {error}
                </div>
              )}
              {details && (
                <form onSubmit={handleSubmit}>
                  <label htmlFor="activation-password" className="form-label">
                    New Password
                  </label>
                  <input
                    id="activation-password"
                    type="password"
                    minLength="8"
                    required
                    autoComplete="new-password"
                    className="form-control mb-3"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                  />
                  <label
                    htmlFor="activation-confirmation"
                    className="form-label"
                  >
                    Confirm Password
                  </label>
                  <input
                    id="activation-confirmation"
                    type="password"
                    minLength="8"
                    required
                    autoComplete="new-password"
                    className="form-control mb-4"
                    value={confirmation}
                    onChange={(event) => setConfirmation(event.target.value)}
                  />
                  <button
                    type="submit"
                    className="btn btn-primary w-100"
                    disabled={isSubmitting}
                  >
                    {isSubmitting ? "Activating..." : "Activate Account"}
                  </button>
                </form>
              )}
            </>
          )}
        </div>
      </section>
    </main>
  );
}

export default ActivateAccount;
