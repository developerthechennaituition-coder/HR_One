import { HRONE_API_BASE } from "../config/api.js";
import { useEffect, useState } from "react";

import Sidebar from "./Sidebar";
import Header from "./Header";

import CompanySetup from "../pages/CompanySetup";
import EmployeeMaster from "../pages/EmployeeMaster";
import EmployeeSalarySetup from "../pages/EmployeeSalarySetup";
import PayrollWorkspace from "../pages/PayrollWorkspace";
import EmployeePayslips from "../pages/EmployeePayslips";
import EmployeeDetails from "../pages/EmployeeDetails";
import OrganizationSetup from "../pages/OrganizationSetup";
import HolidayCalendarSetup from "../pages/HolidayCalendarSetup";
import UserManagement from "../pages/UserManagement";
import Attendance from "../pages/Attendance";
import EmployeeAttendance from "../pages/EmployeeAttendance";
import WfhAttendance from "../pages/WfhAttendance";
import WfhAdminSettings from "../pages/WfhAdminSettings";
import UserAttendance from "../pages/UserAttendance";
import EsslSetup from "../pages/EsslSetup";
import AccessDenied from "../pages/AccessDenied";
import Profile from "../pages/Profile";
import ChangePassword from "../pages/ChangePassword";
import Leave from "../pages/Leave";
import NotificationManagement from "../pages/NotificationManagement";
import Dashboard from "../pages/Dashboard";

import TeamMembers from "../pages/TeamMembers";
import TeamMemberDetails from "../pages/TeamMemberDetails";

import { canManageUsers } from "../auth";

const API_BASE_URL = HRONE_API_BASE;

/*
 * =========================================================
 * SESSION HELPERS
 * =========================================================
 */

function getStoredSession() {
  try {
    return JSON.parse(
      sessionStorage.getItem("hrms_session") || "null"
    );
  } catch {
    return null;
  }
}

function getUserId(user) {
  return user?.id || user?._id || null;
}

/*
 * =========================================================
 * INITIAL PAGE
 * =========================================================
 */

function getInitialPage() {
  const hash = window.location.hash
    .replace("#", "")
    .trim();

  const allowedPages = new Set([
    "dashboard",
    "company-setup",
    "organization-setup",
    "holiday-calendar",
    "employees",
    "employee-salary-setup",
    "payroll-dashboard",
    "monthly-payroll",
    "payslip-management",
    "employee-details",
    "users",
    "attendance",
    "my-attendance",
    "wfh-attendance",
    "wfh-settings",
    "user-attendance",
    "essl-setup",
    "leave",
    "notifications",
    "profile",
    "change-password",
    "team-members",
    "team-member-details",
    "403",
  ]);

  if (!hash || !allowedPages.has(hash)) {
    return "dashboard";
  }

  return hash;
}

/*
 * =========================================================
 * MAIN LAYOUT
 * =========================================================
 */

function MainLayout({ session, onLogout }) {
  const currentUser = session?.user || {};

  const currentUserRole = String(
    currentUser.role || ""
  )
    .trim()
    .toLowerCase();

  /*
   * =======================================================
   * PERMISSIONS
   * =======================================================
   */

  const canManageUsersForSession =
    canManageUsers(currentUser);

  const canManageOrganization = new Set([
    "hr admin",
    "administrator",
    "hr manager",
  ]).has(currentUserRole);

  const canManageAttendance = new Set([
    "hr admin",
    "administrator",
  ]).has(currentUserRole);
  const canAccessAttendance =
    canManageAttendance ||
    currentUserRole === "employee" ||
    currentUserRole === "hr manager";

  const canAccessEmployeeMaster =
    currentUserRole === "hr admin";

  const canManageEmployeeMaster =
    currentUserRole === "hr admin";

  const canManageUserManagement =
    currentUserRole === "hr admin";

  const canAccessLeave = new Set([
    "hr admin",
    "administrator",
    "hr manager",
    "employee",
  ]).has(currentUserRole);

  /*
   * =======================================================
   * PAGE STATE
   * =======================================================
   */

  const [activePage, setActivePage] = useState(() => {
    const initialPage = getInitialPage();
    if (
      currentUserRole !== "hr admin" &&
      ["employee-salary-setup", "payroll-dashboard", "monthly-payroll"].includes(initialPage)
    ) {
      return "payslip-management";
    }
    return initialPage;
  });

  /*
   * Selected employee for:
   *
   * Employee Details
   * Team Member Details
   */
  const [
    selectedEmployeeUserId,
    setSelectedEmployeeUserId,
  ] = useState(null);

  /*
   * -------------------------------------------------------
   * Employee Details mode
   * -------------------------------------------------------
   *
   * true:
   *   Profile -> Employee Details
   *   Read Only
   *
   * false:
   *   Employee Master -> Employee Details
   *   Editable
   */
  const [
    employeeDetailsReadOnly,
    setEmployeeDetailsReadOnly,
  ] = useState(true);

  /*
   * -------------------------------------------------------
   * Employee Details source
   * -------------------------------------------------------
   *
   * profile
   * employee-master
   *
   * This is kept separately because edit permission
   * and navigation source are two different things.
   */
  const [
    employeeDetailsSource,
    setEmployeeDetailsSource,
  ] = useState("profile");

  /*
   * =======================================================
   * REPORTING MANAGER STATE
   * =======================================================
   */

  const [
    isReportingManager,
    setIsReportingManager,
  ] = useState(false);

  const [
    reportingManagerStatusLoading,
    setReportingManagerStatusLoading,
  ] = useState(true);

  const [
    reportingManagerStatusError,
    setReportingManagerStatusError,
  ] = useState(false);

  /*
   * =======================================================
   * HASH HELPER
   * =======================================================
   */

  const updateHash = (page) => {
    if (
      window.location.hash !== `#${page}`
    ) {
      window.location.hash = page;
    }
  };

  /*
   * =======================================================
   * REPORTING MANAGER STATUS
   * =======================================================
   */

  useEffect(() => {
    let cancelled = false;

    async function loadReportingManagerStatus() {
      setReportingManagerStatusLoading(true);
      setReportingManagerStatusError(false);

      try {
        const storedSession = getStoredSession();

        const token =
          session?.token ||
          storedSession?.token;

        if (!token) {
          if (!cancelled) {
            setIsReportingManager(false);
            setReportingManagerStatusLoading(
              false
            );
          }

          return;
        }

        const response = await fetch(
          `${API_BASE_URL}/api/reporting-manager/me`,
          {
            method: "GET",
            headers: {
              Authorization: `Bearer ${token}`,
              Accept: "application/json",
            },
          }
        );

        if (!response.ok) {
          if (!cancelled) {
            setIsReportingManager(false);

            /*
             * 401 / 403 means the user is not allowed
             * to be treated as Reporting Manager.
             */
            if (
              response.status !== 401 &&
              response.status !== 403
            ) {
              setReportingManagerStatusError(
                true
              );
            }
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
          "Failed to load Reporting Manager status:",
          error
        );

        if (!cancelled) {
          setIsReportingManager(false);
          setReportingManagerStatusError(
            true
          );
        }
      } finally {
        if (!cancelled) {
          setReportingManagerStatusLoading(
            false
          );
        }
      }
    }

    loadReportingManagerStatus();

    return () => {
      cancelled = true;
    };
  }, [
    session?.token,
    currentUser?.id,
    currentUser?._id,
  ]);

  /*
   * =======================================================
   * HASH CHANGE
   * =======================================================
   */

  useEffect(() => {
    const handleHashChange = () => {
      const page = getInitialPage();

      /*
       * If browser goes directly to:
       *
       * #employee-details
       *
       * without a selected employee,
       * do not show Access Denied.
       *
       * Go safely to Profile.
       */
      if (
        page === "employee-details" &&
        !selectedEmployeeUserId
      ) {
        setEmployeeDetailsSource("profile");
        setEmployeeDetailsReadOnly(true);

        updateHash("profile");
        setActivePage("profile");

        return;
      }

      /*
       * Team Member Details also requires
       * a selected user.
       */
      if (
        page === "team-member-details" &&
        !selectedEmployeeUserId
      ) {
        updateHash("team-members");
        setActivePage("team-members");

        return;
      }

      setActivePage(page);
    };

    window.addEventListener(
      "hashchange",
      handleHashChange
    );

    return () => {
      window.removeEventListener(
        "hashchange",
        handleHashChange
      );
    };
  }, [selectedEmployeeUserId]);

  /*
   * =======================================================
   * CENTRAL NAVIGATION
   * =======================================================
   */

  const handleNavigate = (
    page,
    itemId = null
  ) => {
    /*
     * =====================================================
     * TEAM MEMBERS
     * =====================================================
     */

    if (page === "team-members") {
      if (reportingManagerStatusLoading) {
        return;
      }

      if (!isReportingManager) {
        updateHash("403");
        setActivePage("403");
        return;
      }

      /*
       * Clear employee selection because this is
       * a different navigation context.
       */
      setSelectedEmployeeUserId(null);

      updateHash("team-members");
      setActivePage("team-members");

      return;
    }

    /*
     * =====================================================
     * TEAM MEMBER DETAILS
     * =====================================================
     */

    if (page === "team-member-details") {
      if (reportingManagerStatusLoading) {
        return;
      }

      if (
        !isReportingManager ||
        !itemId
      ) {
        updateHash("403");
        setActivePage("403");
        return;
      }

      setSelectedEmployeeUserId(itemId);

      updateHash("team-member-details");
      setActivePage("team-member-details");

      return;
    }

    /*
     * =====================================================
     * EMPLOYEE DETAILS
     * =====================================================
     *
     * Generic navigation to Employee Details is treated
     * as Profile / Read Only navigation.
     *
     * Employee Master uses openEmployeeDetails()
     * separately and explicitly enables edit mode.
     * =====================================================
     */

    if (page === "employee-details") {
      /*
       * No employee ID means there is no employee
       * to display.
       *
       * Do NOT redirect to Employee Master because that
       * caused Profile navigation to behave incorrectly.
       */
      if (!itemId) {
        setSelectedEmployeeUserId(null);

        setEmployeeDetailsSource("profile");
        setEmployeeDetailsReadOnly(true);

        updateHash("profile");
        setActivePage("profile");

        return;
      }

      /*
       * Generic employee-details navigation:
       *
       * READ ONLY
       */
      setSelectedEmployeeUserId(itemId);

      setEmployeeDetailsSource("profile");
      setEmployeeDetailsReadOnly(true);

      updateHash("employee-details");
      setActivePage("employee-details");

      return;
    }

    /*
     * =====================================================
     * EMPLOYEE MASTER
     * =====================================================
     */

    if (page === "employees") {
      if (!canAccessEmployeeMaster) {
        updateHash("403");
        setActivePage("403");
        return;
      }

      setSelectedEmployeeUserId(null);

      updateHash("employees");
      setActivePage("employees");

      return;
    }
    /*
     * =====================================================
     * EMPLOYEE SALARY SETUP / PAYROLL
     * =====================================================
     */

    if (page === "employee-salary-setup") {
      if (!canManageEmployeeMaster) {
        updateHash("403");
        setActivePage("403");
        return;
      }

      updateHash("employee-salary-setup");
      setActivePage("employee-salary-setup");
      return;
    }

    if (page === "payslip-management") {
      // HR Admin sees the payroll-wide payslip register; all other roles see
      // only the self-service payslip page, whose API is employee-scoped.
      updateHash(page);
      setActivePage(page);
      return;
    }

    if (["payroll-dashboard", "monthly-payroll"].includes(page)) {
      if (currentUserRole !== "hr admin") { updateHash("403"); setActivePage("403"); return; }
      updateHash(page); setActivePage(page); return;
    }

    /*
     * =====================================================
     * ATTENDANCE
     * =====================================================
     */

    if (page === "attendance") {
      if (!canAccessAttendance) {
        updateHash("403");
        setActivePage("403");
        return;
      }

      updateHash("attendance");
      setActivePage("attendance");

      return;
    }

    if (page === "my-attendance") {
      if (!new Set(["hr admin", "administrator"]).has(currentUserRole)) {
        updateHash("403");
        setActivePage("403");
        return;
      }
      updateHash("my-attendance");
      setActivePage("my-attendance");
      return;
    }

    if (page === "wfh-attendance") {
      if (!new Set(["employee", "hr admin", "administrator", "hr manager", "manager", "admin", "hr_admin"]).has(currentUserRole)) {
        updateHash("403");
        setActivePage("403");
        return;
      }

      updateHash("wfh-attendance");
      setActivePage("wfh-attendance");
      return;
    }

    if (page === "wfh-settings") {
      if (!canManageAttendance) {
        updateHash("403");
        setActivePage("403");
        return;
      }

      updateHash("wfh-settings");
      setActivePage("wfh-settings");
      return;
    }

    if (page === "user-attendance" || page === "essl-setup") {
      if (!canManageAttendance) {
        updateHash("403");
        setActivePage("403");
        return;
      }

      updateHash(page);
      setActivePage(page);
      return;
    }

    /*
     * =====================================================
     * USER MANAGEMENT
     * =====================================================
     */

    if (page === "users") {
      if (!canManageUserManagement) {
        updateHash("403");
        setActivePage("403");
        return;
      }

      updateHash("users");
      setActivePage("users");

      return;
    }

    /*
     * =====================================================
     * LEAVE
     * =====================================================
     */

    if (page === "leave") {
      if (!canAccessLeave) {
        updateHash("403");
        setActivePage("403");
        return;
      }

      updateHash("leave");
      setActivePage("leave");

      return;
    }

    /*
     * =====================================================
     * PROFILE
     * =====================================================
     */

    if (page === "profile") {
      /*
       * Leaving Employee Details should clear
       * selected employee.
       */
      setSelectedEmployeeUserId(null);

      setEmployeeDetailsSource("profile");
      setEmployeeDetailsReadOnly(true);

      updateHash("profile");
      setActivePage("profile");

      return;
    }

    /*
     * =====================================================
     * CHANGE PASSWORD
     * =====================================================
     */

    if (page === "change-password") {
      updateHash("change-password");
      setActivePage("change-password");

      return;
    }

    /*
     * =====================================================
     * GENERIC / PROTECTED PAGES
     * =====================================================
     */

    const protectedPages = {
      "company-setup": true,
      "organization-setup": true,
      "holiday-calendar": true,
      "notifications": true,
      "dashboard": true,
      "403": true,
    };

    if (protectedPages[page]) {
      updateHash(page);
      setActivePage(page);

      return;
    }

    /*
     * =====================================================
     * UNKNOWN PAGE
     * =====================================================
     */

    updateHash("dashboard");
    setActivePage("dashboard");
  };

  /*
   * =======================================================
   * EMPLOYEE MASTER -> EMPLOYEE DETAILS
   * =======================================================
   *
   * This function is intentionally different from
   * handleNavigate("employee-details").
   *
   * Employee Master:
   *     HR Admin
   *     Editable
   *
   * Profile:
   *     Read Only
   * =======================================================
   */

  const openEmployeeDetails = (userId) => {
    if (!userId) {
      console.warn(
        "Employee Details: userId is missing."
      );

      return;
    }

    /*
     * Store selected employee.
     */
    setSelectedEmployeeUserId(userId);

    /*
     * Remember source.
     */
    setEmployeeDetailsSource(
      "employee-master"
    );

    /*
     * IMPORTANT:
     *
     * Employee Master opened this page.
     * Since Employee Master is already protected
     * for HR Admin, make details editable.
     */
    setEmployeeDetailsReadOnly(false);

    updateHash("employee-details");
    setActivePage("employee-details");
  };

  /*
   * =======================================================
   * PROFILE -> EMPLOYEE DETAILS
   * =======================================================
   *
   * This function is provided to Profile.jsx so the
   * Profile page can explicitly open the user's own
   * complete information in READ ONLY mode.
   * =======================================================
   */

  const openProfileEmployeeDetails = (
    userId
  ) => {
    const profileUserId =
      userId ||
      getUserId(currentUser);

    if (!profileUserId) {
      console.warn(
        "Profile Employee Details: userId is missing."
      );

      return;
    }

    setSelectedEmployeeUserId(
      profileUserId
    );

    setEmployeeDetailsSource("profile");

    /*
     * Profile is always read-only.
     *
     * Even if current user is HR Admin,
     * Profile -> Show Full Information should
     * not automatically grant Employee Master edit mode.
     */
    setEmployeeDetailsReadOnly(true);

    updateHash("employee-details");
    setActivePage("employee-details");
  };

  /*
   * =======================================================
   * REPORTING MANAGER DIRECT URL PROTECTION
   * =======================================================
   */

  useEffect(() => {
    if (reportingManagerStatusLoading) {
      return;
    }

    if (
      activePage === "team-members" ||
      activePage ===
        "team-member-details"
    ) {
      if (!isReportingManager) {
        updateHash("403");
        setActivePage("403");
        setSelectedEmployeeUserId(null);
      }
    }
  }, [
    reportingManagerStatusLoading,
    isReportingManager,
    activePage,
  ]);

  /*
   * =======================================================
   * REPORTING MANAGER API ERROR PROTECTION
   * =======================================================
   */

  useEffect(() => {
    if (
      reportingManagerStatusError &&
      (
        activePage === "team-members" ||
        activePage ===
          "team-member-details"
      )
    ) {
      updateHash("403");
      setActivePage("403");
      setSelectedEmployeeUserId(null);
    }
  }, [
    reportingManagerStatusError,
    activePage,
  ]);

  /*
   * =======================================================
   * RENDER PAGE
   * =======================================================
   */

  const renderPage = () => {
    /*
     * =====================================================
     * ACCESS DENIED
     * =====================================================
     */

    if (activePage === "403") {
      return (
        <AccessDenied
          onNavigate={handleNavigate}
        />
      );
    }

    /*
     * =====================================================
     * DASHBOARD
     * =====================================================
     */

    if (activePage === "dashboard") {
      return <Dashboard session={session} user={currentUser} onNavigate={handleNavigate} />;
    }

    /*
     * =====================================================
     * COMPANY SETUP
     * =====================================================
     */

    if (activePage === "company-setup") {
      if (!canManageOrganization) return <AccessDenied onNavigate={handleNavigate} />;
      return <CompanySetup currentUser={currentUser} />;
    }

    /*
     * =====================================================
     * ORGANIZATION SETUP
     * =====================================================
     */

    if (
      activePage ===
      "organization-setup"
    ) {
      if (!canManageOrganization) return <AccessDenied onNavigate={handleNavigate} />;
      return <OrganizationSetup currentUser={currentUser} />;
    }

    /*
     * =====================================================
     * HOLIDAY CALENDAR
     * =====================================================
     */

    if (
      activePage ===
      "holiday-calendar"
    ) {
      if (!canManageOrganization) return <AccessDenied onNavigate={handleNavigate} />;
      return (
        <HolidayCalendarSetup currentUser={currentUser} />
      );
    }

    /*
     * =====================================================
     * LEAVE
     * =====================================================
     */

    if (activePage === "leave") {
      if (!canAccessLeave) {
        return (
          <AccessDenied
            onNavigate={handleNavigate}
          />
        );
      }

      return (
        <Leave
          user={currentUser}
          session={session}
          onNavigate={handleNavigate}
        />
      );
    }

    /*
     * =====================================================
     * NOTIFICATIONS
     * =====================================================
     */

    if (
      activePage ===
      "notifications"
    ) {
      return (
        <NotificationManagement
          user={currentUser}
          session={session}
          onNavigate={handleNavigate}
        />
      );
    }

    /*
     * =====================================================
     * EMPLOYEE MASTER
     * =====================================================
     */

    if (
      activePage ===
      "employees"
    ) {
      if (!canAccessEmployeeMaster) {
        return (
          <AccessDenied
            onNavigate={handleNavigate}
          />
        );
      }

      return (
        <EmployeeMaster
          user={currentUser}
          session={session}
          onNavigate={handleNavigate}

          /*
           * IMPORTANT FIX
           *
           * EmployeeMaster.jsx expects:
           *
           *     onOpenEmployee
           *
           * NOT:
           *
           *     onOpenEmployeeDetails
           */
          onOpenEmployee={
            openEmployeeDetails
          }
        />
      );
    }

    if (activePage === "employee-salary-setup") {
      if (!canManageEmployeeMaster) {
        return <AccessDenied onNavigate={handleNavigate} />;
      }
      return <EmployeeSalarySetup />;
    }

    if (activePage === "payslip-management") {
      if (currentUserRole === "hr admin") {
        return <PayrollWorkspace mode="payslips" onNavigate={handleNavigate} />;
      }
      return <EmployeePayslips session={session} />;
    }

    if (["payroll-dashboard", "monthly-payroll"].includes(activePage)) {
      if (currentUserRole !== "hr admin") return <AccessDenied onNavigate={handleNavigate} />;
      const mode = activePage === "payroll-dashboard" ? "dashboard" : "monthly";
      return <PayrollWorkspace mode={mode} onNavigate={handleNavigate} />;
    }

    /*
     * =====================================================
     * EMPLOYEE DETAILS
     * =====================================================
     */

    if (
      activePage ===
      "employee-details"
    ) {
      /*
       * Employee Details needs a selected employee.
       *
       * Without this ID, do not show a false Access Denied.
       * Redirect to Profile.
       */
      if (!selectedEmployeeUserId) {
        return (
          <AccessDenied
            onNavigate={handleNavigate}
          />
        );
      }

      return (
        <EmployeeDetails
          user={currentUser}
          session={session}

          /*
           * Selected employee
           */
          userId={
            selectedEmployeeUserId
          }

          employeeUserId={
            selectedEmployeeUserId
          }

          /*
           * Profile:
           *     true
           *
           * Employee Master:
           *     false
           */
          readOnly={
            employeeDetailsReadOnly
          }

          /*
           * IMPORTANT FIX
           *
           * EmployeeDetails.jsx has:
           *
           *     canEdit = false
           *
           * as default.
           *
           * Therefore MainLayout MUST explicitly pass
           * canEdit.
           */
          canEdit={
            canManageEmployeeMaster
          }

          /*
           * IMPORTANT:
           *
           * Back button behaviour:
           *
           * Employee Master -> Employees
           * Profile -> Profile
           */
          openedFromEmployeeMaster={
            employeeDetailsSource ===
            "employee-master"
          }

          onNavigate={
            handleNavigate
          }
        />
      );
    }

    /*
     * =====================================================
     * USER MANAGEMENT
     * =====================================================
     */

    if (activePage === "users") {
      if (
        !canManageUserManagement
      ) {
        return (
          <AccessDenied
            onNavigate={handleNavigate}
          />
        );
      }

      return (
        <UserManagement
          user={currentUser}
          session={session}
          onNavigate={handleNavigate}
        />
      );
    }

    /*
     * =====================================================
     * ATTENDANCE
     * =====================================================
     */

    if (
      activePage ===
      "attendance"
    ) {
      if (!canAccessAttendance) {
        return (
          <AccessDenied
            onNavigate={handleNavigate}
          />
        );
      }

      return canManageAttendance ? (
        <Attendance
          user={currentUser}
          session={session}
          onNavigate={handleNavigate}
        />
      ) : (
        <EmployeeAttendance
          session={session}
          onNavigate={handleNavigate}
        />
      );
    }

    if (activePage === "my-attendance") {
      if (!new Set(["hr admin", "administrator"]).has(currentUserRole)) {
        return <AccessDenied onNavigate={handleNavigate} />;
      }
      return <EmployeeAttendance session={session} onNavigate={handleNavigate} />;
    }

    if (activePage === "wfh-attendance") {
      if (!new Set(["employee", "hr admin", "administrator", "hr manager", "manager", "admin", "hr_admin"]).has(currentUserRole)) {
        return <AccessDenied onNavigate={handleNavigate} />;
      }
      return <WfhAttendance session={session} />;
    }

    if (activePage === "wfh-settings") {
      if (!canManageAttendance) {
        return <AccessDenied onNavigate={handleNavigate} />;
      }
      return <WfhAdminSettings session={session} />;
    }

    if (activePage === "user-attendance") {
      if (!canManageAttendance) {
        return <AccessDenied onNavigate={handleNavigate} />;
      }
      return <UserAttendance session={session} />;
    }

    if (activePage === "essl-setup") {
      if (!canManageAttendance) {
        return <AccessDenied onNavigate={handleNavigate} />;
      }
      return <EsslSetup session={session} />;
    }

    /*
     * =====================================================
     * PROFILE
     * =====================================================
     */

    if (
      activePage ===
      "profile"
    ) {
      return (
        <Profile
          user={currentUser}
          session={session}
          onNavigate={
            handleNavigate
          }

          /*
           * Dedicated Profile -> Employee Details
           * callback.
           *
           * If your Profile.jsx supports this prop,
           * Show Full Information can use it directly.
           */
          onOpenEmployeeDetails={
            openProfileEmployeeDetails
          }
        />
      );
    }

    /*
     * =====================================================
     * CHANGE PASSWORD
     * =====================================================
     */

    if (
      activePage ===
      "change-password"
    ) {
      return (
        <ChangePassword
          user={currentUser}
          session={session}
          onNavigate={
            handleNavigate
          }
        />
      );
    }

    /*
     * =====================================================
     * TEAM MEMBERS
     * =====================================================
     */

    if (
      activePage ===
      "team-members"
    ) {
      if (
        reportingManagerStatusLoading
      ) {
        return (
          <div
            style={{
              minHeight: "300px",
              display: "flex",
              alignItems:
                "center",
              justifyContent:
                "center",
              flexDirection:
                "column",
              gap: "12px",
            }}
          >
            <div
              className="spinner-border"
              role="status"
              aria-hidden="true"
            />

            <div>
              Checking team access...
            </div>
          </div>
        );
      }

      if (
        reportingManagerStatusError ||
        !isReportingManager
      ) {
        return (
          <AccessDenied
            onNavigate={
              handleNavigate
            }
          />
        );
      }

      return (
        <TeamMembers
          user={currentUser}
          session={session}
          onNavigate={
            handleNavigate
          }
        />
      );
    }

    /*
     * =====================================================
     * TEAM MEMBER DETAILS
     * =====================================================
     */

    if (
      activePage ===
      "team-member-details"
    ) {
      if (
        reportingManagerStatusLoading
      ) {
        return (
          <div
            style={{
              minHeight: "300px",
              display: "flex",
              alignItems:
                "center",
              justifyContent:
                "center",
              flexDirection:
                "column",
              gap: "12px",
            }}
          >
            <div
              className="spinner-border"
              role="status"
              aria-hidden="true"
            />

            <div>
              Checking team access...
            </div>
          </div>
        );
      }

      if (
        reportingManagerStatusError ||
        !isReportingManager ||
        !selectedEmployeeUserId
      ) {
        return (
          <AccessDenied
            onNavigate={
              handleNavigate
            }
          />
        );
      }

      return (
        <TeamMemberDetails
          user={currentUser}
          session={session}

          /*
           * Use selected employee ID as
           * Team Member ID.
           */
          memberId={
            selectedEmployeeUserId
          }

          onNavigate={
            handleNavigate
          }
        />
      );
    }

    /*
     * =====================================================
     * FALLBACK
     * =====================================================
     */

    return (
      <div className="dashboard-page">
        <h4>Dashboard</h4>

        <p>
          Welcome{" "}
          <strong>
            {currentUser.name ||
              "User"}
          </strong>
        </p>
      </div>
    );
  };

  /*
   * =======================================================
   * MAIN LAYOUT UI
   * =======================================================
   */

  return (
    <div className="hrms-layout">
      <Sidebar
        activePage={activePage}
        onNavigate={handleNavigate}
        role={currentUserRole}
        canManageUsers={
          canManageUsersForSession
        }
        canManageAttendance={
          canManageAttendance
        }
        canAccessAttendance={canAccessAttendance}
        canAccessLeave={
          canAccessLeave
        }
        canManageEmployeeMaster={
          canManageEmployeeMaster
        }
      />

      <div className="main-area">
        <Header
          activePage={activePage}
          user={currentUser}
          session={session}
          onLogout={onLogout}
          onNavigate={handleNavigate}
        />

        <main className="main-content">
          {renderPage()}
        </main>
      </div>
    </div>
  );
}

export default MainLayout;