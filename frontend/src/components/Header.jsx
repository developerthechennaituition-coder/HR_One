function Header() {
  return (
    <header className="top-header">
      <div>
        <h5>Dashboard</h5>
      </div>

      <div className="header-right">
        <button className="notification-btn">
          🔔
        </button>

        <div className="user-info">
          <div className="user-avatar">
            A
          </div>

          <div>
            <strong>Admin User</strong>
            <small>Administrator</small>
          </div>
        </div>
      </div>
    </header>
  )
}

export default Header