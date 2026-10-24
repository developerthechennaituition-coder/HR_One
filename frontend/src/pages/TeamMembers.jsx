import { HRONE_API_BASE } from "../config/api.js";
import { useEffect, useMemo, useState } from "react";

const API_URL =
  HRONE_API_BASE + "/api/reporting-manager";

function getSession() {
  try {
    return JSON.parse(
      sessionStorage.getItem("hrms_session") || "null"
    );
  } catch {
    return null;
  }
}

function formatDate(value) {
  if (!value) return "-";

  const raw = String(value).trim();

  const dateOnlyMatch = raw.match(
    /^(\d{4})-(\d{2})-(\d{2})$/
  );

  if (dateOnlyMatch) {
    const [, year, month, day] = dateOnlyMatch;

    return `${day}/${month}/${year}`;
  }

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return raw;
  }

  return date.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function getInitials(name) {
  if (!name) return "U";

  const parts = String(name)
    .trim()
    .split(/\s+/)
    .filter(Boolean);

  if (parts.length === 1) {
    return parts[0].charAt(0).toUpperCase();
  }

  return (
    parts[0].charAt(0) +
    parts[parts.length - 1].charAt(0)
  ).toUpperCase();
}

function normalizeStatus(value) {
  return String(value || "")
    .trim()
    .toLowerCase();
}

function getStatusClass(status) {
  const normalized = normalizeStatus(status);

  if (normalized === "active") {
    return "team-status-badge team-status-active";
  }

  if (normalized === "inactive") {
    return "team-status-badge team-status-inactive";
  }

  return "team-status-badge team-status-unknown";
}

function TeamMembers({ onNavigate }) {
  const [members, setMembers] = useState([]);
  const [manager, setManager] = useState(null);

  const [search, setSearch] = useState("");
  const [department, setDepartment] = useState("all");
  const [designation, setDesignation] =
    useState("all");
  const [status, setStatus] = useState("all");

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  /*
   * ============================================================
   * LOAD TEAM MEMBERS
   * ============================================================
   */

  const loadTeamMembers = async () => {
    try {
      setLoading(true);
      setError("");

      const session = getSession();
      const token = session?.token;

      if (!token) {
        setError(
          "Authentication session not found. Please login again."
        );
        return;
      }

      /*
       * Production endpoint:
       *
       * GET /api/reporting-manager/team-members
       *
       * The backend must verify that the logged-in user
       * is actually the Reporting Manager of these employees.
       */

      const response = await fetch(
        `${API_URL}/team-members/list`,
        {
          method: "GET",
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: "application/json",
          },
        }
      );

      let data = null;

      try {
        data = await response.json();
      } catch {
        data = null;
      }

      if (!response.ok) {
        throw new Error(
          data?.detail ||
            data?.message ||
            "Unable to load team members."
        );
      }

      setManager(
        data?.reporting_manager ||
          data?.manager ||
          null
      );

      /*
       * Supports both:
       *
       * {
       *   team_members: [...]
       * }
       *
       * and:
       *
       * [...]
       */

      if (Array.isArray(data)) {
        setMembers(data);
      } else {
        setMembers(
          Array.isArray(data?.team_members)
            ? data.team_members
            : []
        );
      }
    } catch (err) {
      console.error(
        "Team members loading error:",
        err
      );

      setError(
        err?.message ||
          "Unable to load team members."
      );

      setMembers([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTeamMembers();
  }, []);

  /*
   * ============================================================
   * DEPARTMENTS
   * ============================================================
   */

  const departments = useMemo(() => {
    return [
      ...new Set(
        members
          .map(
            (member) =>
              member.department ||
              member.department_name
          )
          .filter(Boolean)
      ),
    ].sort((a, b) =>
      String(a).localeCompare(String(b))
    );
  }, [members]);

  /*
   * ============================================================
   * DESIGNATIONS
   * ============================================================
   */

  const designations = useMemo(() => {
    return [
      ...new Set(
        members
          .map(
            (member) =>
              member.designation ||
              member.designation_name
          )
          .filter(Boolean)
      ),
    ].sort((a, b) =>
      String(a).localeCompare(String(b))
    );
  }, [members]);

  /*
   * ============================================================
   * FILTERED MEMBERS
   * ============================================================
   */

  const filteredMembers = useMemo(() => {
    const keyword = search
      .trim()
      .toLowerCase();

    return members.filter((member) => {
      const memberDepartment =
        member.department ||
        member.department_name ||
        "";

      const memberDesignation =
        member.designation ||
        member.designation_name ||
        "";

      const memberStatus =
        member.status ||
        (member.is_active
          ? "Active"
          : "Inactive");

      const matchesSearch =
        !keyword ||
        [
          member.name,
          member.employee_id,
          member.work_email,
          member.mobile,
          member.department,
          member.department_name,
          member.designation,
          member.designation_name,
        ]
          .filter(Boolean)
          .some((value) =>
            String(value)
              .toLowerCase()
              .includes(keyword)
          );

      const matchesDepartment =
        department === "all" ||
        memberDepartment === department;

      const matchesDesignation =
        designation === "all" ||
        memberDesignation === designation;

      const matchesStatus =
        status === "all" ||
        normalizeStatus(memberStatus) ===
          normalizeStatus(status);

      return (
        matchesSearch &&
        matchesDepartment &&
        matchesDesignation &&
        matchesStatus
      );
    });
  }, [
    members,
    search,
    department,
    designation,
    status,
  ]);

  /*
   * ============================================================
   * CLEAR FILTERS
   * ============================================================
   */

  const clearFilters = () => {
    setSearch("");
    setDepartment("all");
    setDesignation("all");
    setStatus("all");
  };

  /*
   * ============================================================
   * OPEN MEMBER
   * ============================================================
   */

  const openMember = (memberId) => {
    if (!memberId) return;

    onNavigate?.(
      "team-member-details",
      memberId
    );
  };

  /*
   * ============================================================
   * RENDER
   * ============================================================
   */

  return (
    <div className="team-members-page">

      {/* ======================================================
          PAGE HEADER
      ====================================================== */}

      <div className="team-members-page-header">

        <div className="team-members-title-area">

          <div className="team-members-title-icon">
            <i className="bi bi-people-fill" />
          </div>

          <div>
            <h3 className="team-members-page-title">
              Team Members
            </h3>

            <p className="team-members-page-subtitle">
              View and monitor employees reporting
              directly to you.
            </p>
          </div>

        </div>

        <div className="team-manager-summary">

          <div className="team-manager-summary-icon">
            <i className="bi bi-person-badge" />
          </div>

          <div>
            <span className="team-manager-summary-label">
              Reporting Manager
            </span>

            <strong className="team-manager-summary-name">
              {manager?.name ||
                "Current Reporting Manager"}
            </strong>

            {manager?.employee_id && (
              <span className="team-manager-summary-id">
                Employee ID:{" "}
                {manager.employee_id}
              </span>
            )}
          </div>

        </div>

      </div>

      {/* ======================================================
          SUMMARY CARDS
      ====================================================== */}

      <div className="row g-3 mb-4">

        <div className="col-12 col-sm-6 col-xl-3">
          <div className="team-stat-card">

            <div className="team-stat-icon">
              <i className="bi bi-people" />
            </div>

            <div>
              <span className="team-stat-label">
                Total Team
              </span>

              <strong className="team-stat-value">
                {members.length}
              </strong>
            </div>

          </div>
        </div>

        <div className="col-12 col-sm-6 col-xl-3">
          <div className="team-stat-card">

            <div className="team-stat-icon">
              <i className="bi bi-person-check" />
            </div>

            <div>
              <span className="team-stat-label">
                Active
              </span>

              <strong className="team-stat-value">
                {
                  members.filter(
                    (member) =>
                      normalizeStatus(
                        member.status ||
                          (member.is_active
                            ? "Active"
                            : "Inactive")
                      ) === "active"
                  ).length
                }
              </strong>
            </div>

          </div>
        </div>

        <div className="col-12 col-sm-6 col-xl-3">
          <div className="team-stat-card">

            <div className="team-stat-icon">
              <i className="bi bi-building" />
            </div>

            <div>
              <span className="team-stat-label">
                Departments
              </span>

              <strong className="team-stat-value">
                {departments.length}
              </strong>
            </div>

          </div>
        </div>

        <div className="col-12 col-sm-6 col-xl-3">
          <div className="team-stat-card">

            <div className="team-stat-icon">
              <i className="bi bi-briefcase" />
            </div>

            <div>
              <span className="team-stat-label">
                Designations
              </span>

              <strong className="team-stat-value">
                {designations.length}
              </strong>
            </div>

          </div>
        </div>

      </div>

      {/* ======================================================
          ERROR
      ====================================================== */}

      {error && (
        <div
          className="alert alert-danger d-flex align-items-start gap-2"
          role="alert"
        >
          <i className="bi bi-exclamation-triangle-fill mt-1" />

          <div>
            <strong>
              Unable to load team members
            </strong>

            <div className="mt-1">
              {error}
            </div>
          </div>
        </div>
      )}

      {/* ======================================================
          FILTER CARD
      ====================================================== */}

      <div className="team-members-filter-card">

        <div className="team-filter-header">

          <div>
            <h5 className="mb-1">
              Team Directory
            </h5>

            <p className="text-muted mb-0">
              Search and filter your direct team
              members.
            </p>
          </div>

          <span className="team-view-only-badge">
            <i className="bi bi-eye me-1" />
            View Only
          </span>

        </div>

        <div className="row g-3 mt-1">

          {/* SEARCH */}

          <div className="col-12 col-lg-5">

            <label className="form-label">
              Search Employee
            </label>

            <div className="input-group">

              <span className="input-group-text">
                <i className="bi bi-search" />
              </span>

              <input
                type="text"
                className="form-control"
                placeholder="Name, employee ID, email..."
                value={search}
                onChange={(event) =>
                  setSearch(
                    event.target.value
                  )
                }
              />

            </div>

          </div>

          {/* DEPARTMENT */}

          <div className="col-12 col-sm-6 col-lg-2">

            <label className="form-label">
              Department
            </label>

            <select
              className="form-select"
              value={department}
              onChange={(event) =>
                setDepartment(
                  event.target.value
                )
              }
            >
              <option value="all">
                All Departments
              </option>

              {departments.map((item) => (
                <option
                  key={item}
                  value={item}
                >
                  {item}
                </option>
              ))}
            </select>

          </div>

          {/* DESIGNATION */}

          <div className="col-12 col-sm-6 col-lg-2">

            <label className="form-label">
              Designation
            </label>

            <select
              className="form-select"
              value={designation}
              onChange={(event) =>
                setDesignation(
                  event.target.value
                )
              }
            >
              <option value="all">
                All Designations
              </option>

              {designations.map((item) => (
                <option
                  key={item}
                  value={item}
                >
                  {item}
                </option>
              ))}
            </select>

          </div>

          {/* STATUS */}

          <div className="col-12 col-sm-6 col-lg-2">

            <label className="form-label">
              Status
            </label>

            <select
              className="form-select"
              value={status}
              onChange={(event) =>
                setStatus(
                  event.target.value
                )
              }
            >
              <option value="all">
                All Status
              </option>

              <option value="Active">
                Active
              </option>

              <option value="Inactive">
                Inactive
              </option>
            </select>

          </div>

          {/* CLEAR */}

          <div className="col-12 col-sm-6 col-lg-1 d-flex align-items-end">

            <button
              type="button"
              className="btn btn-outline-secondary w-100 team-clear-filter-btn"
              onClick={clearFilters}
              title="Clear filters"
            >
              <i className="bi bi-arrow-counterclockwise" />
            </button>

          </div>

        </div>

      </div>

      {/* ======================================================
          RESULT SUMMARY
      ====================================================== */}

      <div className="team-members-summary-row">

        <div>
          Showing{" "}
          <strong>
            {filteredMembers.length}
          </strong>{" "}
          of{" "}
          <strong>
            {members.length}
          </strong>{" "}
          team members
        </div>

        {(search ||
          department !== "all" ||
          designation !== "all" ||
          status !== "all") && (
          <button
            type="button"
            className="btn btn-link btn-sm p-0"
            onClick={clearFilters}
          >
            Clear filters
          </button>
        )}

      </div>

      {/* ======================================================
          TABLE
      ====================================================== */}

      <div className="team-members-table-card">

        {loading ? (
          <div className="team-members-loading">

            <div
              className="spinner-border"
              role="status"
            >
              <span className="visually-hidden">
                Loading...
              </span>
            </div>

            <span>
              Loading team members...
            </span>

          </div>
        ) : filteredMembers.length === 0 ? (
          <div className="team-members-empty">

            <div className="team-empty-icon">
              <i className="bi bi-people" />
            </div>

            <h5>
              No team members found
            </h5>

            <p className="text-muted mb-3">
              No employees match the current
              search or filter criteria.
            </p>

            <button
              type="button"
              className="btn btn-outline-primary"
              onClick={clearFilters}
            >
              Clear Filters
            </button>

          </div>
        ) : (
          <>
            {/* ==================================================
                DESKTOP TABLE
            ================================================== */}

            <div className="table-responsive team-members-desktop-table">

              <table className="table table-hover align-middle mb-0">

                <thead>
                  <tr>
                    <th>Employee</th>
                    <th>Employee ID</th>
                    <th>Date of Birth</th>
                    <th>Date of Joining</th>
                    <th>Department</th>
                    <th>Designation</th>
                    <th>Status</th>
                    <th className="text-end">
                      Action
                    </th>
                  </tr>
                </thead>

                <tbody>
                  {filteredMembers.map(
                    (member) => {
                      const memberStatus =
                        member.status ||
                        (member.is_active
                          ? "Active"
                          : "Inactive");

                      const memberDepartment =
                        member.department ||
                        member.department_name ||
                        "-";

                      const memberDesignation =
                        member.designation ||
                        member.designation_name ||
                        "-";

                      return (
                        <tr
                          key={member.id}
                          className="team-member-row"
                          onClick={() =>
                            openMember(
                              member.id
                            )
                          }
                        >

                          <td>
                            <div className="team-member-name-cell">

                              <div className="team-member-avatar">
                                {getInitials(
                                  member.name
                                )}
                              </div>

                              <div className="team-member-primary-info">

                                <div className="team-member-name">
                                  {member.name ||
                                    "-"}
                                </div>

                                <div className="team-member-email">
                                  {member.work_email ||
                                    "-"}
                                </div>

                              </div>

                            </div>
                          </td>

                          <td>
                            <span className="team-member-id">
                              {member.employee_id ||
                                "-"}
                            </span>
                          </td>

                          <td>
                            {formatDate(
                              member.date_of_birth
                            )}
                          </td>

                          <td>
                            {formatDate(
                              member.date_of_joining
                            )}
                          </td>

                          <td>
                            <span className="team-table-text">
                              {memberDepartment}
                            </span>
                          </td>

                          <td>
                            <span className="team-table-text">
                              {memberDesignation}
                            </span>
                          </td>

                          <td>
                            <span
                              className={getStatusClass(
                                memberStatus
                              )}
                            >
                              <span className="team-status-dot" />
                              {memberStatus}
                            </span>
                          </td>

                          <td className="text-end">

                            <button
                              type="button"
                              className="btn btn-sm btn-outline-primary team-view-btn"
                              onClick={(event) => {
                                event.stopPropagation();

                                openMember(
                                  member.id
                                );
                              }}
                            >
                              <i className="bi bi-eye me-1" />
                              View
                            </button>

                          </td>

                        </tr>
                      );
                    }
                  )}
                </tbody>

              </table>

            </div>

            {/* ==================================================
                MOBILE CARDS
            ================================================== */}

            <div className="team-members-mobile-list">

              {filteredMembers.map(
                (member) => {
                  const memberStatus =
                    member.status ||
                    (member.is_active
                      ? "Active"
                      : "Inactive");

                  return (
                    <div
                      key={member.id}
                      className="team-mobile-member-card"
                    >

                      <div className="team-mobile-member-header">

                        <div className="team-member-name-cell">

                          <div className="team-member-avatar">
                            {getInitials(
                              member.name
                            )}
                          </div>

                          <div className="team-member-primary-info">

                            <div className="team-member-name">
                              {member.name ||
                                "-"}
                            </div>

                            <div className="team-member-email">
                              {member.work_email ||
                                "-"}
                            </div>

                          </div>

                        </div>

                        <span
                          className={getStatusClass(
                            memberStatus
                          )}
                        >
                          <span className="team-status-dot" />
                          {memberStatus}
                        </span>

                      </div>

                      <div className="team-mobile-member-grid">

                        <div>
                          <span>
                            Employee ID
                          </span>

                          <strong>
                            {member.employee_id ||
                              "-"}
                          </strong>
                        </div>

                        <div>
                          <span>
                            Date of Birth
                          </span>

                          <strong>
                            {formatDate(
                              member.date_of_birth
                            )}
                          </strong>
                        </div>

                        <div>
                          <span>
                            Date of Joining
                          </span>

                          <strong>
                            {formatDate(
                              member.date_of_joining
                            )}
                          </strong>
                        </div>

                        <div>
                          <span>
                            Department
                          </span>

                          <strong>
                            {member.department ||
                              member.department_name ||
                              "-"}
                          </strong>
                        </div>

                        <div>
                          <span>
                            Designation
                          </span>

                          <strong>
                            {member.designation ||
                              member.designation_name ||
                              "-"}
                          </strong>
                        </div>

                        <div>
                          <span>
                            Mobile
                          </span>

                          <strong>
                            {member.mobile ||
                              "-"}
                          </strong>
                        </div>

                      </div>

                      <button
                        type="button"
                        className="btn btn-outline-primary w-100 mt-3"
                        onClick={() =>
                          openMember(
                            member.id
                          )
                        }
                      >
                        <i className="bi bi-eye me-2" />
                        View Full Profile
                      </button>

                    </div>
                  );
                }
              )}

            </div>
          </>
        )}

      </div>

    </div>
  );
}

export default TeamMembers;