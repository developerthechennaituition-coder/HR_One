function Sidebar() {
  return (
    <aside className="sidebar">

      <div className="sidebar-brand">
        <h4>HRMS</h4>
        <small>Human Resource Management</small>
      </div>

      <nav className="sidebar-menu">

        <a href="#" className="active">
          <i className="bi bi-grid-1x2-fill"></i>
          <span>Dashboard</span>
        </a>

        <a href="#">
          <i className="bi bi-people-fill"></i>
          <span>Employees</span>
        </a>

        <a href="#">
          <i className="bi bi-calendar-check"></i>
          <span>Attendance</span>
        </a>

        <a href="#">
          <i className="bi bi-calendar-event"></i>
          <span>Leave</span>
        </a>

        <a href="#">
          <i className="bi bi-cash-stack"></i>
          <span>Payroll</span>
        </a>

        <a href="#">
          <i className="bi bi-bar-chart-fill"></i>
          <span>Reports</span>
        </a>

        <a href="#">
          <i className="bi bi-gear-fill"></i>
          <span>Settings</span>
        </a>

      </nav>

    </aside>
  )
}

export default Sidebar