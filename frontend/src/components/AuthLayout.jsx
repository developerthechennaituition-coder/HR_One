// Shared shell for the public sign-in and password-recovery pages.

const RESET_STEPS = ["Identify", "Verify", "Reset"];

function AuthLayout({ icon, title, subtitle, step, children, footer }) {
  return (
    <main className="auth-shell">
      <div className="auth-aurora" aria-hidden="true">
        <span className="auth-blob auth-blob-one" />
        <span className="auth-blob auth-blob-two" />
        <span className="auth-blob auth-blob-three" />
      </div>

      <div className="auth-frame">
        <aside className="auth-showcase" aria-hidden="true">
          <div className="auth-brand">
            <span className="auth-brand-mark"><i className="bi bi-people-fill" /></span>
            <span>HRMS</span>
          </div>

          <div className="auth-showcase-copy">
            <span className="auth-eyebrow"><i className="bi bi-stars" /> People workspace</span>
            <h2>Everything your workday needs, beautifully in one place.</h2>
            <p>Attendance, leave, holidays and payroll — simple for employees, powerful for HR.</p>
          </div>

          <div className="auth-preview">
            <div className="auth-preview-card auth-preview-main">
              <div className="auth-preview-head">
                <span className="auth-preview-dot" />
                <span>Today</span>
              </div>
              <strong>Checked in · 09:02 AM</strong>
              <div className="auth-preview-bar"><span /></div>
              <small>6h 40m logged of 9h</small>
            </div>
            <div className="auth-preview-card auth-preview-float auth-preview-leave">
              <i className="bi bi-airplane" />
              <div>
                <strong>12 days</strong>
                <small>Leave balance</small>
              </div>
            </div>
            <div className="auth-preview-card auth-preview-float auth-preview-pay">
              <i className="bi bi-receipt" />
              <div>
                <strong>Payslip ready</strong>
                <small>October 2026</small>
              </div>
            </div>
          </div>

          <div className="auth-trust">
            <i className="bi bi-shield-check" /> Secured with role-based access
          </div>
        </aside>

        <section className="auth-panel" aria-labelledby="auth-title">
          <div className="auth-mobile-brand">
            <span className="auth-brand-mark"><i className="bi bi-people-fill" /></span>
            <span>HRMS</span>
          </div>

          {step && (
            <ol className="auth-steps" aria-label="Password reset progress">
              {RESET_STEPS.map((label, index) => {
                const number = index + 1;
                const state = number < step ? "done" : number === step ? "active" : "";
                return (
                  <li key={label} className={state} aria-current={number === step ? "step" : undefined}>
                    <span className="auth-step-dot">
                      {number < step ? <i className="bi bi-check-lg" /> : number}
                    </span>
                    <span className="auth-step-label">{label}</span>
                  </li>
                );
              })}
            </ol>
          )}

          <div className="auth-heading">
            <span className="auth-heading-icon"><i className={`bi ${icon}`} /></span>
            <h1 id="auth-title">{title}</h1>
            <p>{subtitle}</p>
          </div>

          {children}

          {footer && <div className="auth-footer">{footer}</div>}
        </section>
      </div>
    </main>
  );
}

export function AuthAlert({ type, children }) {
  if (!children) return null;
  const icon = type === "success" ? "bi-check-circle-fill" : "bi-exclamation-triangle-fill";
  return (
    <div className={`auth-alert auth-alert-${type}`} role={type === "success" ? "status" : "alert"}>
      <i className={`bi ${icon}`} />
      <span>{children}</span>
    </div>
  );
}

export default AuthLayout;
