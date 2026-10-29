import { HRONE_API_BASE } from "../config/api.js";
import { useEffect, useMemo, useState } from "react";
import { roleLabel } from "../services/roleLabels";

const API_URL = HRONE_API_BASE + "/api/users";

const ROLE_OPTIONS = [
  "Super Admin",
  "HR Admin",
  "Administrator",
  "HR Manager",
  "Employee",
];

function readStoredSession() {
  try {
    const raw =
      sessionStorage.getItem("hrms_session");

    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function EmployeeMaster({
  onNavigate,
  onOpenEmployee,
}) {
  const [users, setUsers] = useState([]);
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] =
    useState("all");
  const [roleFilter, setRoleFilter] =
    useState("all");

  const [isLoading, setIsLoading] =
    useState(true);

  const [feedback, setFeedback] =
    useState({
      type: "",
      message: "",
    });

  const session = useMemo(
    () => readStoredSession(),
    [],
  );

  const token =
    session?.token || "";

  const sessionUserId =
    session?.user?.id ||
    session?.user?._id ||
    "";

  /* ========================================================
     ERROR
     ======================================================== */

  const readError = async (
    response,
  ) => {
    try {
      const data =
        await response.json();

      return (
        data.detail ||
        data.message ||
        "The request could not be completed."
      );
    } catch {
      return "The request could not be completed.";
    }
  };

  /* ========================================================
     LOAD EMPLOYEES
     ======================================================== */

  const loadEmployees = async () => {
    setIsLoading(true);

    setFeedback({
      type: "",
      message: "",
    });

    try {
      const headers = {
        "Content-Type":
          "application/json",
      };

      if (token) {
        headers.Authorization =
          `Bearer ${token}`;
      }

      if (sessionUserId) {
        headers["X-User-Id"] =
          String(sessionUserId);
      }

      const response =
        await fetch(API_URL, {
          method: "GET",
          headers,
        });

      if (!response.ok) {
        throw new Error(
          await readError(response),
        );
      }

      const data =
        await response.json();

      setUsers(
        Array.isArray(data)
          ? data
          : [],
      );
    } catch (error) {
      console.error(
        "Employee Master load error:",
        error,
      );

      setFeedback({
        type: "danger",
        message:
          error.message ||
          "Unable to load Employee Master.",
      });
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadEmployees();
  }, []);

  /* ========================================================
     AUTO HIDE FEEDBACK
     ======================================================== */

  useEffect(() => {
    if (!feedback.message) {
      return undefined;
    }

    const timer =
      window.setTimeout(() => {
        setFeedback({
          type: "",
          message: "",
        });
      }, 4000);

    return () =>
      window.clearTimeout(timer);
  }, [feedback.message]);

  /* ========================================================
     FILTER
     ======================================================== */

  const filteredUsers = useMemo(() => {
    const query =
      searchTerm
        .trim()
        .toLowerCase();

    return users.filter(
      (user) => {
        const matchesSearch =
          !query ||
          String(
            user.employee_id || "",
          )
            .toLowerCase()
            .includes(query) ||
          String(
            user.name || "",
          )
            .toLowerCase()
            .includes(query) ||
          String(
            user.work_email || "",
          )
            .toLowerCase()
            .includes(query) ||
          String(
            user.mobile || "",
          )
            .toLowerCase()
            .includes(query);

        const matchesStatus =
          statusFilter === "all" ||
          (
            statusFilter === "active"
              ? user.is_active
              : !user.is_active
          );

        const matchesRole =
          roleFilter === "all" ||
          user.role === roleFilter;

        return (
          matchesSearch &&
          matchesStatus &&
          matchesRole
        );
      },
    );
  }, [
    users,
    searchTerm,
    statusFilter,
    roleFilter,
  ]);

  /* ========================================================
     RESET
     ======================================================== */

  const handleReset = () => {
    setSearchTerm("");
    setStatusFilter("all");
    setRoleFilter("all");
  };

  /* ========================================================
     OPEN EMPLOYEE DETAILS
     ======================================================== */

  const openEmployee = (
    employee,
  ) => {
    const employeeId =
      employee?.id ||
      employee?._id;

    if (!employeeId) {
      setFeedback({
        type: "danger",
        message:
          "Employee ID is missing. Unable to open Employee Details.",
      });

      return;
    }

    /*
     * IMPORTANT
     *
     * This calls MainLayout's
     * openEmployeeDetails().
     *
     * That function sets:
     *
     * readOnly = false
     * canEdit = true for HR Admin
     */
    if (onOpenEmployee) {
      onOpenEmployee(
        String(employeeId),
      );

      return;
    }

    /*
     * Fallback
     */
    onNavigate?.(
      "employee-details",
      String(employeeId),
    );
  };

  /* ========================================================
     RENDER
     ======================================================== */

  return (
    <section className="company-setup-page">

      {/* ==================================================
          HEADER
          ================================================== */}

      <div className="d-flex flex-wrap justify-content-between align-items-center gap-3 mb-4">

        <div>
          <h2 className="mb-1">
            Employee Master
          </h2>

          <p className="text-muted mb-0">
            Employee records are created
            from User Management.
          </p>
        </div>

      </div>

      {/* ==================================================
          FEEDBACK
          ================================================== */}

      {feedback.message && (
        <div
          className={`alert alert-${feedback.type}`}
          role="alert"
        >
          {feedback.message}
        </div>
      )}

      {/* ==================================================
          MAIN CARD
          ================================================== */}

      <div className="card border-0 shadow-sm">

        <div className="card-body">

          {/* ==================================================
              FILTERS
              ================================================== */}

          <div className="row g-3 mb-4">

            <div className="col-lg-6">

              <label
                htmlFor="employee-master-search"
                className="visually-hidden"
              >
                Search employees
              </label>

              <input
                id="employee-master-search"
                type="search"
                className="form-control"
                placeholder="Search by Employee ID, Name, Work Email or Mobile"
                value={searchTerm}
                onChange={(event) =>
                  setSearchTerm(
                    event.target.value,
                  )
                }
              />

            </div>

            <div className="col-md-3 col-lg-2">

              <select
                className="form-select"
                value={statusFilter}
                onChange={(event) =>
                  setStatusFilter(
                    event.target.value,
                  )
                }
                aria-label="Filter by status"
              >
                <option value="all">
                  All Status
                </option>

                <option value="active">
                  Active
                </option>

                <option value="inactive">
                  Inactive
                </option>
              </select>

            </div>

            <div className="col-md-3 col-lg-2">

              <select
                className="form-select"
                value={roleFilter}
                onChange={(event) =>
                  setRoleFilter(
                    event.target.value,
                  )
                }
                aria-label="Filter by role"
              >
                <option value="all">
                  All Role
                </option>

                {ROLE_OPTIONS.map(
                  (role) => (
                    <option
                      key={role}
                      value={role}
                    >
                      {roleLabel(role)}
                    </option>
                  ),
                )}
              </select>

            </div>

            <div className="col-md-3 col-lg-2">

              <button
                type="button"
                className="btn btn-outline-primary w-100"
                onClick={
                  handleReset
                }
              >
                <i className="bi bi-arrow-clockwise me-2" />
                Reset
              </button>

            </div>

          </div>

          {/* ==================================================
              TABLE
              ================================================== */}

          <div className="table-responsive">

            <table className="table table-hover align-middle mb-0">

              <thead>
                <tr>
                  <th>
                    Employee ID
                  </th>

                  <th>
                    Name
                  </th>

                  <th>
                    Role
                  </th>

                  <th>
                    Work Email
                  </th>

                  <th>
                    Mobile
                  </th>

                  <th>
                    Status
                  </th>

                  <th>
                    Action
                  </th>
                </tr>
              </thead>

              <tbody>

                {isLoading ? (
                  <tr>
                    <td
                      colSpan="7"
                      className="text-center text-muted py-5"
                    >
                      <div
                        className="spinner-border spinner-border-sm text-primary me-2"
                        role="status"
                      />

                      Loading employees...
                    </td>
                  </tr>
                ) : filteredUsers.length === 0 ? (
                  <tr>
                    <td
                      colSpan="7"
                      className="text-center text-muted py-5"
                    >
                      No employees available.
                    </td>
                  </tr>
                ) : (
                  filteredUsers.map(
                    (employee) => (
                      <tr
                        key={
                          employee.id ||
                          employee._id
                        }
                        role="button"
                        onClick={() =>
                          openEmployee(
                            employee,
                          )
                        }
                        style={{
                          cursor:
                            "pointer",
                        }}
                      >

                        <td className="fw-semibold">
                          {employee.employee_id ||
                            "-"}
                        </td>

                        <td>
                          {employee.name ||
                            "-"}
                        </td>

                        <td>
                          {roleLabel(employee.role) ||
                            "-"}
                        </td>

                        <td>
                          {employee.work_email ||
                            "-"}
                        </td>

                        <td>
                          {employee.country_code
                            ? `${employee.country_code} ${
                                employee.mobile ||
                                ""
                              }`
                            : employee.mobile ||
                              "-"}
                        </td>

                        <td>

                          <span
                            className={`badge ${
                              employee.is_active
                                ? "text-bg-success"
                                : "text-bg-secondary"
                            }`}
                          >
                            {employee.is_active
                              ? "Active"
                              : "Inactive"}
                          </span>

                        </td>

                        <td>

                          <button
                            type="button"
                            className="btn btn-sm btn-outline-primary"
                            onClick={(
                              event,
                            ) => {
                              event.stopPropagation();

                              openEmployee(
                                employee,
                              );
                            }}
                          >
                            <i className="bi bi-eye me-1" />

                            View
                          </button>

                        </td>

                      </tr>
                    ),
                  )
                )}

              </tbody>

            </table>

          </div>

        </div>

      </div>

    </section>
  );
}

export default EmployeeMaster;