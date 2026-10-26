import { useEffect, useState } from "react";

const PAYROLL_PAGES = [
  ["payroll-dashboard", "Payroll Dashboard", "speedometer2"],
  ["employee-salary-setup", "Employee Salary Setup", "person-vcard"],
  ["monthly-payroll", "Monthly Payroll", "calendar2-week"],
  ["payslip-management", "Payslip Management", "file-earmark-pdf"],
];

function Sidebar({
  activePage,
  onNavigate,
  isMobileOpen = false,
  role = "",
  canManageUsers,
  canManageAttendance,
  canAccessAttendance,
  canAccessLeave,
  canManageEmployeeMaster,
}) {
  const normalizedRole = String(role || "").trim().toLowerCase();
  const isHrAdmin = normalizedRole === "hr admin";
  const isAdministrator = normalizedRole === "administrator";
  const canManageOrganization = isHrAdmin || isAdministrator;
  const canManageUsersForRole = isHrAdmin;
  const [isPayrollOpen, setIsPayrollOpen] = useState(
    ["employee-salary-setup", "payroll-dashboard", "monthly-payroll", "payslip-management"].includes(activePage),
  );
  const attendancePages = [
    "attendance", "my-attendance", "wfh-attendance", "wfh-settings", "user-attendance", "essl-setup",
  ];
  const [isAttendanceOpen, setIsAttendanceOpen] = useState(attendancePages.includes(activePage));
  const [isOrganizationOpen, setIsOrganizationOpen] = useState(
    ["company-setup", "organization-setup", "holiday-calendar"].includes(activePage),
  );

  useEffect(() => {
    if (attendancePages.includes(activePage)) {
      setIsAttendanceOpen(true);
    }
    if (["employee-salary-setup", "payroll-dashboard", "monthly-payroll", "payslip-management"].includes(activePage)) {
      setIsPayrollOpen(true);
    }
    if (["company-setup", "organization-setup", "holiday-calendar"].includes(activePage)) {
      setIsOrganizationOpen(true);
    }
  }, [activePage]);

  const navigate = (event, page) => {
    event.preventDefault();
    onNavigate(page);
  };

  const item = (page, label, icon, onClick) => (
    <a key={page} href={`#${page}`} className={activePage === page ? "active" : ""}
      aria-current={activePage === page ? "page" : undefined}
      onClick={(event) => onClick ? onClick(event) : navigate(event, page)}>
      <i className={`bi bi-${icon}`} aria-hidden="true" />
      <span>{label}</span>
    </a>
  );

  const payrollPages = isHrAdmin
    ? PAYROLL_PAGES
    : [["payslip-management", "My Payslips", "file-earmark-pdf"]];

  return (
    <aside className={`sidebar${isMobileOpen ? " sidebar-mobile-open" : ""}`} aria-label="Main navigation">
      <div className="sidebar-brand">
        <h4>HRMS</h4>
        <small>Human Resource Management</small>
      </div>
      <nav className="sidebar-menu">
        {(canManageUsers || normalizedRole === "employee") && item("dashboard", "Dashboard", "grid-1x2-fill")}
        {canManageOrganization && (
          <div className="sidebar-group">
            <button type="button" className={`sidebar-parent ${isOrganizationOpen ? "expanded" : ""}`}
              onClick={() => setIsOrganizationOpen((open) => !open)} aria-expanded={isOrganizationOpen}>
              <i className="bi bi-diagram-3" aria-hidden="true" />
              <span>Organization Setup</span>
              <i className={`bi ${isOrganizationOpen ? "bi-chevron-up" : "bi-chevron-down"} sidebar-chevron`} aria-hidden="true" />
            </button>
            {isOrganizationOpen && <div className="sidebar-submenu">
              {item("company-setup", "Company / Legal Entity", "building")}
              {item("holiday-calendar", "Holiday Calendar", "calendar-event")}
              {item("organization-setup", "Organization Structure", "diagram-3")}
            </div>}
          </div>
        )}
        {canManageEmployeeMaster && item("employees", "Employees", "people-fill")}
        {canManageUsersForRole && item("users", "User Management", "person-gear")}
        {canAccessAttendance && (
          <div className="sidebar-group sidebar-attendance-group">
            <button type="button"
              className={`sidebar-parent ${isAttendanceOpen ? "expanded" : ""} ${attendancePages.includes(activePage) ? "section-active" : ""}`}
              onClick={() => setIsAttendanceOpen((open) => !open)} aria-expanded={isAttendanceOpen}>
              <i className="bi bi-calendar-check" aria-hidden="true" />
              <span>Attendance</span>
              <i className={`bi ${isAttendanceOpen ? "bi-chevron-up" : "bi-chevron-down"} sidebar-chevron`} aria-hidden="true" />
            </button>
            {isAttendanceOpen && <div className="sidebar-submenu">
              {item("attendance", (isHrAdmin || isAdministrator) ? "Daily Attendance" : "My Attendance", "calendar-check")}
              {(isHrAdmin || isAdministrator) && item("my-attendance", "My Attendance", "person-check")}
              {(isHrAdmin || isAdministrator) && item("user-attendance", "Employee Attendance", "person-lines-fill")}
              {(normalizedRole === "employee" || normalizedRole === "hr manager" || isHrAdmin || isAdministrator) && item("wfh-attendance", "WFH Attendance", "house-check")}
              {(isHrAdmin || isAdministrator) && item("wfh-settings", "WFH Settings", "geo-alt")}
              {(isHrAdmin || isAdministrator) && item("essl-setup", "ESSL Attendance Setup", "fingerprint")}
            </div>}
          </div>
        )}
        {canAccessLeave && item("leave", "Leave", "calendar2-check")}
        <div className="sidebar-group sidebar-payroll-group">
          {isHrAdmin ? (
            <>
              <button type="button"
                className={`sidebar-parent ${isPayrollOpen ? "expanded" : ""} ${payrollPages.some(([page]) => activePage === page) ? "section-active" : ""}`}
                onClick={() => setIsPayrollOpen((open) => !open)} aria-expanded={isPayrollOpen}>
                <i className="bi bi-cash-stack" aria-hidden="true" />
                <span>Payroll</span>
                <i className={`bi ${isPayrollOpen ? "bi-chevron-up" : "bi-chevron-down"} sidebar-chevron`} aria-hidden="true" />
              </button>
              {isPayrollOpen && <div className="sidebar-submenu payroll-submenu">
                {payrollPages.map(([page, label, icon]) => item(page, label, icon))}
              </div>}
            </>
          ) : item("payslip-management", "My Payslips", "file-earmark-pdf")}
        </div>
      </nav>
    </aside>
  );
}

export default Sidebar;
