import { HRONE_API_BASE } from "../config/api.js";
import { useState } from "react";
import AuthLayout, { AuthAlert } from "../components/AuthLayout";

// FastAPI owns authentication; this component owns form and session UI state.

const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || HRONE_API_BASE
).replace(/\/+$/, "");

const API_URL = `${API_BASE_URL}/api/auth/login`;

const greeting = () => {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
};


function Login({ onLogin }) {
  const [formValues, setFormValues] = useState({
    identifier: "",
    password: "",
  });
  const [showPassword, setShowPassword] = useState(false);
  const [capsLockOn, setCapsLockOn] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");

  // Submit credentials, persist the session, and enter the authenticated shell.
  const handleSubmit = async (event) => {
    event.preventDefault();
    setError("");
    setIsSubmitting(true);
    try {
      const response = await fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          identifier: formValues.identifier.trim(),
          password: formValues.password,
        }),
      });
      if (!response.ok) {
        throw new Error("Invalid email or password");
      }
      const session = await response.json();
      sessionStorage.setItem("hrms_session", JSON.stringify(session));
      onLogin(session);
    } catch (requestError) {
      setError(requestError.message || "Unable to sign in");
    } finally {
      setIsSubmitting(false);
    }
  };

  const trackCapsLock = (event) => {
    setCapsLockOn(Boolean(event.getModifierState?.("CapsLock")));
  };

  return (
    <AuthLayout
      icon="bi-hand-thumbs-up"
      title={`${greeting()}!`}
      subtitle="Sign in to continue to your HRMS workspace."
      footer={
        <>
          <i className="bi bi-headset" /> Trouble signing in? Contact your HR administrator.
        </>
      }
    >
      <AuthAlert type="error">{error}</AuthAlert>

      <form className="auth-form" onSubmit={handleSubmit}>
        <div className="auth-field">
          <label htmlFor="login-identifier">Work Email or Employee ID</label>
          <div className="auth-input">
            <i className="bi bi-person-badge auth-input-icon" />
            <input
              id="login-identifier"
              type="text"
              placeholder="name@company.com or EMP001"
              autoComplete="username"
              required
              autoFocus
              value={formValues.identifier}
              onChange={(event) =>
                setFormValues({
                  ...formValues,
                  identifier: event.target.value,
                })
              }
            />
          </div>
        </div>

        <div className="auth-field">
          <div className="auth-label-row">
            <label htmlFor="login-password">Password</label>
            <button
              type="button"
              className="auth-link"
              onClick={() => {window.location.href = "/forgot-password"}}
            >
              Forgot password?
            </button>
          </div>
          <div className="auth-input">
            <i className="bi bi-lock auth-input-icon" />
            <input
              id="login-password"
              type={showPassword ? "text" : "password"}
              placeholder="Enter your password"
              autoComplete="current-password"
              required
              value={formValues.password}
              onKeyUp={trackCapsLock}
              onKeyDown={trackCapsLock}
              onBlur={() => setCapsLockOn(false)}
              onChange={(event) =>
                setFormValues({
                  ...formValues,
                  password: event.target.value,
                })
              }
            />
            <button
              type="button"
              className="auth-eye"
              onClick={() => setShowPassword(!showPassword)}
              aria-label={showPassword ? "Hide password" : "Show password"}
            >
              <i className={`bi ${showPassword ? "bi-eye-slash" : "bi-eye"}`} />
            </button>
          </div>
          {capsLockOn && (
            <small className="auth-hint auth-hint-warn">
              <i className="bi bi-capslock-fill" /> Caps Lock is on
            </small>
          )}
        </div>

        <button type="submit" className="auth-submit" disabled={isSubmitting}>
          {isSubmitting ? (
            <>
              <span className="spinner-border spinner-border-sm" aria-hidden="true" />
              Signing in...
            </>
          ) : (
            <>
              Sign In <i className="bi bi-arrow-right" />
            </>
          )}
        </button>
      </form>
    </AuthLayout>
  );
}

export default Login;
