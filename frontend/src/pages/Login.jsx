import { HRONE_API_BASE } from "../config/api.js";
import { useState } from "react";

// FastAPI owns authentication; this component owns form and session UI state.

const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || HRONE_API_BASE
).replace(/\/+$/, "");

const API_URL = `${API_BASE_URL}/api/auth/login`;


function Login({ onLogin }) {
  const [formValues, setFormValues] = useState({
    identifier: "",
    password: "",
  });
  const [showPassword, setShowPassword] = useState(false);
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

  return (
    <main className="login-page">
      <section
        className="login-card card border-0 shadow-sm"
        aria-labelledby="login-title"
      >
        <div className="card-body p-4 p-md-5">
          <div className="login-brand">HRMS</div>
          <h1 id="login-title">Welcome back</h1>
          <p className="text-muted mb-4">
            Sign in to continue to your workspace.
          </p>
          {error && (
            <div className="alert alert-danger" role="alert">
              {error}
            </div>
          )}
          <form onSubmit={handleSubmit}>
            <div className="mb-3">
              <label htmlFor="login-identifier" className="form-label">
                Work Email or Employee ID
              </label>
              <input
                id="login-identifier"
                type="text"
                className="form-control"
                autoComplete="username"
                required
                value={formValues.identifier}
                onChange={(event) =>
                  setFormValues({
                    ...formValues,
                    identifier: event.target.value,
                  })
                }
              />
            </div>
            <div className="mb-3">
              <label htmlFor="login-password" className="form-label">
                Password
              </label>
              <div className="input-group">
                <input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  className="form-control"
                  autoComplete="current-password"
                  required
                  value={formValues.password}
                  onChange={(event) =>
                    setFormValues({
                      ...formValues,
                      password: event.target.value,
                    })
                  }
                />
                <button
                  type="button"
                  className="btn btn-outline-secondary"
                  onClick={() => setShowPassword(!showPassword)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  {showPassword ? "Hide" : "Show"}
                </button>
              </div>
            </div>
            <div className="d-flex justify-content-end mb-4">
              <button
                type="button"
                className="btn btn-link btn-sm p-0"
                onClick={() => {window.location.href = "/forgot-password"}}
              >
                Forgot Password?
              </button>
            </div>
            <button
              type="submit"
              className="btn btn-primary w-100"
              disabled={isSubmitting}
            >
              {isSubmitting ? "Signing in..." : "Sign In"}
            </button>
          </form>
        </div>
      </section>
    </main>
  );
}

export default Login;
