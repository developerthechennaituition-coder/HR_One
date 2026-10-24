// Fallback page for routes the current user's role cannot access.
function AccessDenied({ onNavigate }) {
  return (
    <main className="access-denied-page">
      <section className="card border-0 shadow-sm access-denied-card">
        <div className="card-body p-4 p-md-5 text-center">
          <div className="access-denied-icon" aria-hidden="true">
            <i className="bi bi-shield-lock"></i>
          </div>
          <h1>Access Denied</h1>
          <p className="text-muted mb-4">
            You do not have permission to view this page.
          </p>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => onNavigate("dashboard")}
          >
            Return to Dashboard
          </button>
        </div>
      </section>
    </main>
  );
}

export default AccessDenied;
