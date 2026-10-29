import { HRONE_API_BASE } from "../config/api.js";
import { useEffect, useRef, useState } from "react";
import { logout } from "../logout";
import NotificationBell from "./NotificationBell";
import { roleLabel } from "../services/roleLabels";

const REPORTING_MANAGER_API =
  HRONE_API_BASE + "/api/reporting-manager";

// Display title for each logical page key used by MainLayout.
const pageTitles = {
  dashboard: "Dashboard",
  "company-setup": "Company / Legal Entity",
  "organization-setup": "Organization Structure",
  "holiday-calendar": "Holiday Calendar",
  employees: "Employee Master",
  users: "User Management",
  profile: "My Profile",
  attendance: "Attendance",
  "wfh-attendance": "WFH Attendance",
  "wfh-settings": "WFH Settings",
  "user-attendance": "Employee Attendance",
  "my-attendance": "My Attendance",
  leave: "Leave Management",
  "employee-details": "Employee Details",
  "change-password": "Change Password",
  "403": "Access Denied",
  "essl-setup": "ESSL Attendance Setup",
  notifications: "Notifications",
  "team-members": "Team Members",
  "team-member-details": "Team Member Details",
  "payroll-dashboard": "Payroll Dashboard",
  "employee-salary-setup": "Employee Salary Setup",
  "monthly-payroll": "Monthly Payroll",
  "payslip-management": "Payslip Management",
};

function getSession() {
  try {
    return JSON.parse(
      sessionStorage.getItem("hrms_session") || "null"
    );
  } catch {
    return null;
  }
}

function Header({
  activePage,
  user = {},
  onLogout,
  onNavigate,
  onToggleSidebar,
  isMobileSidebarOpen = false,
}) {
  const [isProfileOpen, setIsProfileOpen] =
    useState(false);

  const [isReportingManager, setIsReportingManager] =
    useState(false);

  const profileMenuRef = useRef(null);

  const pageTitle =
    pageTitles[activePage] || "Dashboard";

  // ==========================================================
  // CHECK REPORTING MANAGER STATUS
  // ==========================================================

  useEffect(() => {
    let cancelled = false;

    const checkReportingManager = async () => {
      try {
        const session = getSession();

        if (!session?.token) {
          if (!cancelled) {
            setIsReportingManager(false);
          }
          return;
        }

        const response = await fetch(
          `${REPORTING_MANAGER_API}/me`,
          {
            method: "GET",
            headers: {
              Authorization: `Bearer ${session.token}`,
            },
          }
        );

        if (!response.ok) {
          if (!cancelled) {
            setIsReportingManager(false);
          }
          return;
        }

        const data = await response.json();

        if (!cancelled) {
          setIsReportingManager(
            Boolean(
              data?.is_reporting_manager
            )
          );
        }
      } catch (error) {
        console.error(
          "Reporting Manager status error:",
          error
        );

        if (!cancelled) {
          setIsReportingManager(false);
        }
      }
    };

    checkReportingManager();

    return () => {
      cancelled = true;
    };
  }, [user?.id, user?.employee_id]);

  // ==========================================================
  // CLOSE PROFILE MENU OUTSIDE CLICK
  // ==========================================================

  useEffect(() => {
    const closeProfileMenu = (event) => {
      if (
        !profileMenuRef.current?.contains(
          event.target
        )
      ) {
        setIsProfileOpen(false);
      }
    };

    document.addEventListener(
      "mousedown",
      closeProfileMenu
    );

    return () => {
      document.removeEventListener(
        "mousedown",
        closeProfileMenu
      );
    };
  }, []);

  // ==========================================================
  // PROFILE
  // ==========================================================

  const handleProfileClick = () => {
    window.location.hash = "profile";

    onNavigate("profile");

    setIsProfileOpen(false);
  };

  // ==========================================================
  // TEAM MEMBERS
  // ==========================================================

  const handleTeamMembersClick = () => {
    window.location.hash = "team-members";

    onNavigate("team-members");

    setIsProfileOpen(false);
  };

  return (
    <header className="top-header">

      <div className="header-title-group">
        <button
          type="button"
          className="mobile-menu-toggle"
          aria-label={isMobileSidebarOpen ? "Close navigation menu" : "Open navigation menu"}
          aria-expanded={isMobileSidebarOpen}
          onClick={onToggleSidebar}
        >
          <i className={`bi ${isMobileSidebarOpen ? "bi-x-lg" : "bi-list"}`} aria-hidden="true" />
        </button>
        <h5>{pageTitle}</h5>
      </div>

      <div className="header-right">

        {/* Notification Bell */}
        <NotificationBell
          user={user}
          onNavigate={onNavigate}
        />

        {/* User Profile Menu */}
        <div
          className="user-menu"
          ref={profileMenuRef}
        >
          <button
            type="button"
            className="user-info"
            aria-expanded={isProfileOpen}
            aria-haspopup="menu"
            onClick={() =>
              setIsProfileOpen(
                (isOpen) => !isOpen
              )
            }
          >
            <span className="user-avatar">
              {(user.name || "U")
                .charAt(0)
                .toUpperCase()}
            </span>

            <span>
              <strong>
                {user.name || "User"}
              </strong>

              <small>
                {roleLabel(user.role) || "Unassigned"}
              </small>
            </span>

            <i
              className="bi bi-chevron-down"
              aria-hidden="true"
            />
          </button>

          {isProfileOpen && (
            <div
              className="profile-dropdown"
              role="menu"
            >
              {/* Profile */}
              <button
                type="button"
                className="profile-dropdown-item"
                role="menuitem"
                onClick={handleProfileClick}
              >
                <i
                  className="bi bi-person"
                  aria-hidden="true"
                />

                <span>
                  Profile
                </span>
              </button>

              {/* Team Members */}
              {isReportingManager && (
                <button
                  type="button"
                  className="profile-dropdown-item"
                  role="menuitem"
                  onClick={
                    handleTeamMembersClick
                  }
                >
                  <i
                    className="bi bi-people"
                    aria-hidden="true"
                  />

                  <span>
                    Team Members
                  </span>
                </button>
              )}

              {/* Logout */}
              <button
                type="button"
                className="profile-dropdown-item"
                role="menuitem"
                onClick={() =>
                  logout(onLogout)
                }
              >
                <i
                  className="bi bi-box-arrow-right"
                  aria-hidden="true"
                />

                <span>
                  Logout
                </span>
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

export default Header;