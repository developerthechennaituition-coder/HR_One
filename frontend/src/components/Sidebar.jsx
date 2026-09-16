import { useState } from 'react'

function Sidebar({ activePage, onNavigate }) {
  const [isOrganizationOpen, setIsOrganizationOpen] = useState(activePage === 'company-setup')

  const handleOrganizationToggle = () => {
    setIsOrganizationOpen((isOpen) => !isOpen)
  }

  const handleCompanyNavigate = () => {
    setIsOrganizationOpen(true)
    onNavigate('company-setup')
  }

  return (
    <aside className="sidebar">

      <div className="sidebar-brand">
        <h4>HRMS</h4>
        <small>Human Resource Management</small>
      </div>

      <nav className="sidebar-menu">

        <a
          href="#dashboard"
          className={activePage === 'dashboard' ? 'active' : ''}
          onClick={() => onNavigate('dashboard')}
        >
          <i className="bi bi-grid-1x2-fill"></i>
          <span>Dashboard</span>
        </a>

        <div className="sidebar-group">
          <button
            type="button"
            className={`sidebar-parent ${isOrganizationOpen ? 'expanded' : ''}`}
            onClick={handleOrganizationToggle}
            aria-expanded={isOrganizationOpen}
          >
            <i className="bi bi-people"></i>
            <span>Organization Setup</span>
            <i className="bi bi-chevron-down sidebar-chevron"></i>
          </button>

          {isOrganizationOpen && (
            <div className="sidebar-submenu">
              <a
                href="#company-setup"
                className={activePage === 'company-setup' ? 'active' : ''}
                onClick={handleCompanyNavigate}
              >
                <i className="bi bi-building"></i>
                <span>Company / Legal Entity</span>
              </a>
            </div>
          )}
        </div>

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