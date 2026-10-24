import { HRONE_API_BASE } from "../config/api.js";
import { useEffect, useMemo, useState } from "react";

const USERS_API_URL = HRONE_API_BASE + "/api/users";
const EMPLOYEES_API_URL = HRONE_API_BASE + "/api/employees";
const ATTENDANCE_API_URL = HRONE_API_BASE + "/api/attendance";

const getCompanyDateString = (date = new Date()) => {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const values = Object.fromEntries(
    parts.map(({ type, value }) => [type, value]),
  );
  return `${values.year}-${values.month}-${values.day}`;
};

const getTodayDate = () => getCompanyDateString();

const getYesterdayDate = () => {
  const [year, month, day] = getTodayDate().split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day - 1))
    .toISOString()
    .slice(0, 10);
};

const getCurrentTime = () => {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date());
};

const EMPTY = {
  status: "Not Marked",
  check_in_time: "",
  check_out_time: "",
  attendance_source: null,
  attendance_type: null,
  leave_request_id: null,
  leave_day_part: null,
  leave_type_code: null,
  working_duration: "0h 00m",
  outside_duration: "0h 00m",
  break_duration: "0h 00m",
  working_intervals: [],
  break_intervals: [],
  attendance_state: null,
};

const PART_LABEL = {
  full_day: "Full Day",
  first_half: "First Half",
  second_half: "Second Half",
};

const toState = (record) => ({
  status: record?.status || "Not Marked",
  check_in_time: record?.check_in_time || "",
  check_out_time: record?.check_out_time || "",
  attendance_source: record?.attendance_source || null,
  attendance_type: record?.attendance_type || null,
  leave_request_id: record?.leave_request_id || null,
  leave_day_part: record?.leave_request_id
    ? record.leave_day_part || "full_day"
    : null,
  leave_type_code: record?.leave_request_id
    ? record.leave_type_code || null
    : null,
  working_duration: record?.working_duration || "0h 00m",
  outside_duration: record?.outside_duration || "0h 00m",
  break_duration: record?.break_duration || "0h 00m",
  working_intervals: record?.working_intervals || [],
  break_intervals: record?.break_intervals || [],
  attendance_state: record?.attendance_state || null,
});

const formatAttendanceClock = (value) => {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  }).format(date);
};

const getAttendanceSourceLabel = (source) => {
  if (source === "WFH_FACE_GPS") return "WFH · Face + Location";
  if (source === "DEVICE" || source === "BIOMETRIC") {
    return "Biometric · Device";
  }
  if (source === "MANUAL" || source === "ADMIN") return "Manual · Admin";
  return source || "";
};

/*
 * Approved full-day leave is locked.
 * HR must cancel/reject the leave through the Leave module
 * before changing the attendance.
 */
const isLeaveLocked = (row) =>
  row?.status === "Leave" &&
  row?.leave_request_id &&
  row?.leave_day_part === "full_day";

function Attendance({ session }) {
  const [users, setUsers] = useState([]);
  const [selectedDate, setSelectedDate] = useState(getTodayDate());

  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [attendanceFilter, setAttendanceFilter] = useState("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [departmentFilter, setDepartmentFilter] = useState("all");

  const [attendanceState, setAttendanceState] = useState({});

  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState({});

  const [error, setError] = useState("");
  const [successMessage, setSuccessMessage] = useState("");

  const isToday = selectedDate === getTodayDate();
  const isEditableDate =
    isToday || selectedDate === getYesterdayDate();

  const getHeaders = () => {
    const stored = sessionStorage.getItem("hrms_session");

    if (!stored) {
      throw new Error("Login session not found. Please login again.");
    }

    const session = JSON.parse(stored);

    if (!session?.token) {
      throw new Error("Authentication token not found.");
    }

    return {
      Authorization: `Bearer ${session.token}`,
      "Content-Type": "application/json",
    };
  };

  const request = async (url, fallback) => {
    const response = await fetch(url, {
      method: "GET",
      headers: getHeaders(),
    });

    if (!response.ok) {
      if (response.status === 401) {
        throw new Error(
          "Your session has expired. Please login again."
        );
      }

      if (response.status === 403) {
        throw new Error(
          "Only HR Admin or Administrator can manage attendance."
        );
      }

      let message = fallback;

      try {
        const data = await response.json();
        message = data?.detail || fallback;
      } catch {
        // Keep fallback message.
      }

      throw new Error(message);
    }

    return response.json();
  };

  /*
   * Load employees + attendance for selected date.
   *
   * Approved leave records returned by the backend are converted
   * into Attendance state automatically.
   */
  useEffect(() => {
    const loadData = async () => {
      try {
        setIsLoading(true);
        setError("");

        const [userData, attendanceData, employeeData] = await Promise.all([
          request(
            `${USERS_API_URL}?status=active`,
            "Unable to load users."
          ),
          request(
            `${ATTENDANCE_API_URL}?attendance_date=${selectedDate}`,
            "Unable to load attendance."
          ),
          request(EMPLOYEES_API_URL, "Unable to load employee departments."),
        ]);

        const departmentsByEmployeeId = new Map(
          employeeData.map((employee) => [
            employee.employee_id,
            employee.department,
          ]),
        );
        const usersWithDepartments = userData.map((user) => ({
          ...user,
          department:
            user.department ||
            departmentsByEmployeeId.get(user.employee_id) ||
            "",
        }));
        setUsers(usersWithDepartments);

        const initial = {};

        usersWithDepartments.forEach((user) => {
          const attendanceRecord = attendanceData.find(
            (attendance) =>
              String(attendance.user_id) === String(user.id)
          );

          initial[user.id] = attendanceRecord
            ? toState(attendanceRecord)
            : { ...EMPTY };
        });

        setAttendanceState(initial);
      } catch (requestError) {
        console.error(requestError);

        setError(
          requestError.message ||
            "Unable to load attendance."
        );
      } finally {
        setIsLoading(false);
      }
    };

    loadData();

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDate]);

  /*
   * Change attendance status.
   *
   * Leave is normally supplied by the approved Leave module.
   * The HR Admin can still see it as the selected value.
   */
  const handleStatusChange = (userId, newStatus) => {
    const current = attendanceState[userId] || EMPTY;

    /*
     * Do not allow HR to manually overwrite an approved
     * full-day leave.
     */
    if (isLeaveLocked(current)) {
      return;
    }

    setAttendanceState((prev) => ({
      ...prev,
      [userId]: {
        ...prev[userId],
        status: newStatus,

        check_in_time:
          newStatus !== "Present"
            ? ""
            : prev[userId]?.check_in_time || "",

        check_out_time:
          newStatus !== "Present"
            ? ""
            : prev[userId]?.check_out_time || "",

        /*
         * If HR manually changes away from Leave,
         * clear leave metadata from local state.
         */
        leave_request_id:
          newStatus === "Leave"
            ? prev[userId]?.leave_request_id || null
            : null,

        leave_day_part:
          newStatus === "Leave"
            ? prev[userId]?.leave_day_part || null
            : null,

        leave_type_code:
          newStatus === "Leave"
            ? prev[userId]?.leave_type_code || null
            : null,
      },
    }));

    setError("");
  };

  const handleCheckInChange = (userId, time) => {
    const current = attendanceState[userId] || EMPTY;

    if (isLeaveLocked(current)) {
      return;
    }

    if (
      isToday &&
      time > getCurrentTime()
    ) {
      setError(
        "Check-in time cannot be in the future."
      );

      return;
    }

    setError("");

    setAttendanceState((prev) => ({
      ...prev,

      [userId]: {
        ...prev[userId],

        check_in_time: time,

        check_out_time:
          prev[userId]?.check_out_time &&
          prev[userId].check_out_time < time
            ? ""
            : prev[userId]?.check_out_time || "",
      },
    }));
  };

  const handleCheckOutChange = (userId, time) => {
    const current = attendanceState[userId] || EMPTY;

    if (isLeaveLocked(current)) {
      return;
    }

    if (
      current?.check_in_time &&
      time < current.check_in_time
    ) {
      setError(
        "Check-out time cannot be earlier than check-in time."
      );

      return;
    }

    if (
      isToday &&
      time > getCurrentTime()
    ) {
      setError(
        "Check-out time cannot be in the future."
      );

      return;
    }

    setError("");

    setAttendanceState((prev) => ({
      ...prev,

      [userId]: {
        ...prev[userId],
        check_out_time: time,
      },
    }));
  };

  /*
   * Save attendance.
   *
   * Important:
   * Leave is now a valid attendance status.
   * For an approved leave, the backend validates the approved
   * leave before storing it.
   */
  const handleSaveAttendance = async (user) => {
    const current = attendanceState[user.id];

    if (!current) {
      return;
    }

    if (isLeaveLocked(current)) {
      /*
       * Full-day approved leave is already controlled by
       * the Leave module. We do not overwrite it.
       */
      setError(
        `${user.name} is on approved full-day leave. Cancel the leave to change attendance.`
      );

      return;
    }

    if (
      !current.status ||
      current.status === "Not Marked"
    ) {
      setError(
        `Please select Present, Absent, Holiday or Leave for ${user.name}.`
      );

      return;
    }

    /*
     * Leave validation.
     */
    if (current.status === "Leave") {
      if (!current.leave_request_id) {
        setError(
          `An approved leave request is required before marking Leave for ${user.name}.`
        );

        return;
      }
    }

    /*
     * Present validation.
     */
    if (
      current.status === "Present" &&
      !current.check_in_time
    ) {
      setError(
        `Please select check-in time for ${user.name}.`
      );

      return;
    }

    if (
      isToday &&
      current.status === "Present" &&
      current.check_in_time > getCurrentTime()
    ) {
      setError(
        `Check-in time cannot be in the future for ${user.name}.`
      );

      return;
    }

    if (
      current.check_out_time &&
      current.check_in_time &&
      current.check_out_time < current.check_in_time
    ) {
      setError(
        `Check-out time cannot be earlier than check-in time for ${user.name}.`
      );

      return;
    }

    if (
      isToday &&
      current.check_out_time &&
      current.check_out_time > getCurrentTime()
    ) {
      setError(
        `Check-out time cannot be in the future for ${user.name}.`
      );

      return;
    }

    try {
      setIsSaving((prev) => ({
        ...prev,
        [user.id]: true,
      }));

      setError("");
      setSuccessMessage("");

      const payload = {
        user_id: user.id,
        attendance_date: selectedDate,
        status: current.status,

        check_in_time:
          current.status === "Present"
            ? current.check_in_time
            : null,

        check_out_time:
          current.status === "Present" &&
          current.check_out_time
            ? current.check_out_time
            : null,

        /*
         * Leave metadata.
         */
        leave_request_id:
          current.status === "Leave"
            ? current.leave_request_id
            : null,

        leave_day_part:
          current.status === "Leave"
            ? current.leave_day_part
            : null,

        leave_type_code:
          current.status === "Leave"
            ? current.leave_type_code
            : null,
      };

      const response = await fetch(
        ATTENDANCE_API_URL,
        {
          method: "POST",
          headers: getHeaders(),
          body: JSON.stringify(payload),
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
            "Unable to save attendance."
        );
      }

      /*
       * Always use the backend response as the source
       * of truth after saving.
       */
      setAttendanceState((prev) => ({
        ...prev,

        [user.id]: toState(data),
      }));

      setSuccessMessage(
        `${user.name}'s ${current.status.toLowerCase()} attendance saved successfully.`
      );

      setTimeout(() => {
        setSuccessMessage("");
      }, 3000);
    } catch (requestError) {
      console.error(requestError);

      setError(
        requestError.message ||
          "Unable to save attendance."
      );
    } finally {
      setIsSaving((prev) => ({
        ...prev,
        [user.id]: false,
      }));
    }
  };

  const filteredUsers = useMemo(() => {
    const query = searchTerm
      .trim()
      .toLowerCase();

    return users.filter((user) => {
      const matchesSearch =
        !query ||
        user.name
          ?.toLowerCase()
          .includes(query) ||
        user.work_email
          ?.toLowerCase()
          .includes(query) ||
        user.employee_id
          ?.toLowerCase()
          .includes(query);

      const matchesStatus =
        statusFilter === "all" ||
        (statusFilter === "active"
          ? user.is_active
          : !user.is_active);

      const currentStatus =
        attendanceState[user.id]?.status ||
        "Not Marked";

      const matchesAttendance =
        attendanceFilter === "all" ||
        currentStatus === attendanceFilter;
      const currentSource =
        attendanceState[user.id]?.attendance_source || "";
      const matchesSource =
        sourceFilter === "all" || currentSource === sourceFilter;
      const matchesDepartment =
        departmentFilter === "all" ||
        (user.department || "") === departmentFilter;

      return (
        matchesSearch &&
        matchesStatus &&
        matchesAttendance &&
        matchesSource &&
        matchesDepartment
      );
    });
  }, [
    users,
    searchTerm,
    statusFilter,
    attendanceFilter,
    attendanceState,
    sourceFilter,
    departmentFilter,
  ]);

  const departments = useMemo(
    () =>
      [...new Set(users.map((user) => user.department).filter(Boolean))].sort(),
    [users],
  );

  const count = (status) =>
    users.filter(
      (user) =>
        (
          attendanceState[user.id]?.status ||
          "Not Marked"
        ) === status
    ).length;

  const summaryCards = [
    {
      label: "Total Employees",
      value: users.length,
      className: "",
    },
    {
      label: "Present",
      value: count("Present"),
      className: "text-success",
    },
    {
      label: "Absent",
      value: count("Absent"),
      className: "text-danger",
    },
    {
      label: "Leave",
      value: count("Leave"),
      className: "text-primary",
    },
    {
      label: "Holiday",
      value: count("Holiday"),
      style: { color: "#0b7285" },
    },
    {
      label: "Not Marked",
      value: count("Not Marked"),
      style: { color: "#7a5a2f" },
    },
  ];

  const borderFor = (status) =>
    status === "Present"
      ? "border-success"
      : status === "Absent"
      ? "border-danger"
      : status === "Leave"
      ? "border-primary"
      : status === "Holiday"
      ? "border-info"
      : "border-secondary";

  if (isLoading) {
    return (
      <div className="container-fluid">
        <div className="d-flex justify-content-center py-5">
          <div className="text-center">
            <div
              className="spinner-border text-primary mb-3"
              role="status"
            ></div>

            <p className="text-muted mb-0">
              Loading attendance...
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="container-fluid">
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <h2 className="mb-1">
            Attendance
          </h2>

          <p className="text-muted mb-0">
            Manage employee daily attendance
          </p>
        </div>
      </div>

      {error && (
        <div
          className="alert alert-danger alert-dismissible fade show"
          role="alert"
        >
          {error}

          <button
            type="button"
            className="btn-close"
            onClick={() => setError("")}
          ></button>
        </div>
      )}

      {successMessage && (
        <div
          className="alert alert-success"
          role="alert"
        >
          {successMessage}
        </div>
      )}

      <div className="row g-3 mb-4">
        {summaryCards.map((card) => (
          <div
            className="col-6 col-md"
            key={card.label}
          >
            <div className="card border-0 shadow-sm">
              <div className="card-body">
                <small className="text-muted">
                  {card.label}
                </small>

                <h3
                  className={`mb-0 mt-1 ${
                    card.className || ""
                  }`}
                  style={card.style}
                >
                  {card.value}
                </h3>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="card border-0 shadow-sm mb-4">
        <div className="card-body">
          <div className="row g-3 align-items-end">
            <div className="col-md-3">
              <label className="form-label">
                Attendance Date
              </label>

              <input
                type="date"
                className="form-control"
                value={selectedDate}
                max={getTodayDate()}
                onChange={(e) =>
                  setSelectedDate(e.target.value)
                }
              />

              <small className="text-muted">
                Historical attendance is view-only. Manual changes remain limited to today and yesterday.
              </small>
            </div>

            <div className="col-md-3">
              <label className="form-label">
                Search Employee
              </label>

              <input
                type="text"
                className="form-control"
                placeholder="Name, email or employee ID"
                value={searchTerm}
                onChange={(e) =>
                  setSearchTerm(e.target.value)
                }
              />
            </div>

            <div className="col-md-2">
              <label className="form-label">
                User Status
              </label>

              <select
                className="form-select"
                value={statusFilter}
                onChange={(e) =>
                  setStatusFilter(e.target.value)
                }
              >
                <option value="all">
                  All
                </option>

                <option value="active">
                  Active
                </option>

                <option value="inactive">
                  Inactive
                </option>
              </select>
            </div>

            <div className="col-md-2">
              <label className="form-label">
                Attendance
              </label>

              <select
                className="form-select"
                value={attendanceFilter}
                onChange={(e) =>
                  setAttendanceFilter(e.target.value)
                }
              >
                <option value="all">
                  All
                </option>

                <option value="Present">
                  Present
                </option>

                <option value="Absent">
                  Absent
                </option>

                <option value="Leave">
                  Leave
                </option>

                <option value="Holiday">
                  Holiday
                </option>

                <option value="Not Marked">
                  Not Marked
                </option>
              </select>
            </div>

            <div className="col-md-2">
              <label className="form-label">Department</label>
              <select
                className="form-select"
                value={departmentFilter}
                onChange={(e) => setDepartmentFilter(e.target.value)}
              >
                <option value="all">All departments</option>
                {departments.map((department) => (
                  <option key={department} value={department}>
                    {department}
                  </option>
                ))}
              </select>
            </div>

            <div className="col-md-2">
              <label className="form-label">Source</label>
              <select
                className="form-select"
                value={sourceFilter}
                onChange={(e) => setSourceFilter(e.target.value)}
              >
                <option value="all">All sources</option>
                <option value="ESSL">ESSL</option>
                <option value="MANUAL">Manual</option>
                <option value="WFH_FACE_GPS">WFH</option>
              </select>
            </div>

            <div className="col-md-2">
              <button
                type="button"
                className="btn btn-outline-secondary w-100"
                onClick={() => {
                  setSearchTerm("");
                  setStatusFilter("all");
                  setAttendanceFilter("all");
                  setSourceFilter("all");
                  setDepartmentFilter("all");
                }}
              >
                <i className="bi bi-arrow-counterclockwise me-1"></i>
                Reset
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="card border-0 shadow-sm">
        <div className="card-body p-0">
          <div className="table-responsive">
            <table className="table table-hover align-middle mb-0">
              <thead className="table-light">
                <tr>
                  <th>Employee</th>
                  <th>Employee ID</th>
                  <th>Email</th>
                  <th>Date</th>
                  <th>Type</th>
                  <th className="text-center">
                    Attendance
                  </th>
                  <th>Check-in</th>
                  <th>Check-out</th>
                  <th>Working / Break</th>
                  <th>Source</th>
                  <th className="text-center">
                    Action
                  </th>
                </tr>
              </thead>

              <tbody>
                {filteredUsers.length === 0 ? (
                  <tr>
                    <td
                      colSpan="11"
                      className="text-center py-5 text-muted"
                    >
                      No employees found.
                    </td>
                  </tr>
                ) : (
                  filteredUsers.map((user) => {
                    const current =
                      attendanceState[user.id] ||
                      EMPTY;

                    const saving =
                      isSaving[user.id];

                    const locked =
                      isLeaveLocked(current);

                    const onLeave =
                      current.status === "Leave";

                    return (
                      <tr key={user.id}>
                        <td>
                          <div className="d-flex align-items-center">
                            <div
                              className="bg-primary text-white rounded-circle d-flex justify-content-center align-items-center me-2"
                              style={{
                                width: "38px",
                                height: "38px",
                                fontWeight: "600",
                              }}
                            >
                              {user.name
                                ?.charAt(0)
                                .toUpperCase() ||
                                "U"}
                            </div>

                            <div>
                              <div className="fw-semibold">
                                {user.name}
                              </div>

                              <small className="text-muted">
                                {user.role ||
                                  "Unassigned"}
                              </small>
                              {current.attendance_source && (
                                <span
                                  className={`badge d-block mt-1 ${
                                    current.attendance_source === "WFH_FACE_GPS"
                                      ? "text-bg-info"
                                      : "text-bg-secondary"
                                  }`}
                                >
                                  {getAttendanceSourceLabel(
                                    current.attendance_source,
                                  )}
                                </span>
                              )}
                            </div>
                          </div>
                        </td>

                        <td>
                          {user.employee_id ||
                            "-"}
                        </td>

                        <td>
                          {user.work_email ||
                            "-"}
                        </td>

                        <td>{selectedDate}</td>

                        <td>
                          {current.attendance_type === "OFFICE" ||
                          current.attendance_source === "ESSL" ||
                          current.attendance_source === "MANUAL"
                            ? "Office"
                            : current.attendance_type || "—"}
                        </td>

                        <td className="text-center">
                          <select
                            className={`form-select form-select-sm ${borderFor(
                              current.status
                            )}`}
                            value={
                              current.status ||
                              "Not Marked"
                            }
                            disabled={locked || !isEditableDate}
                            onChange={(e) =>
                              handleStatusChange(
                                user.id,
                                e.target.value
                              )
                            }
                          >
                            <option value="Not Marked">
                              Not Marked
                            </option>

                            <option value="Present">
                              Present
                            </option>

                            <option value="Absent">
                              Absent
                            </option>

                            <option value="Holiday">
                              Holiday
                            </option>

                            {onLeave && (
                              <option
                                value="Leave"
                                disabled={locked}
                              >
                                Leave
                              </option>
                            )}
                          </select>

                          {onLeave && (
                            <small className="text-primary d-block mt-1">
                              <i className="bi bi-calendar-check me-1"></i>

                              {current.leave_type_code ||
                                "Leave"}

                              {current.leave_day_part &&
                                PART_LABEL[
                                  current
                                    .leave_day_part
                                ] && (
                                  <>
                                    {" · "}
                                    {
                                      PART_LABEL[
                                        current
                                          .leave_day_part
                                      ]
                                    }
                                  </>
                                )}
                            </small>
                          )}
                        </td>

                        <td>
                          <input
                            type="time"
                            className="form-control form-control-sm"
                            value={
                              current.check_in_time
                            }
                            disabled={
                              current.status !== "Present" ||
                              !isEditableDate
                            }
                            max={
                              isToday
                                ? getCurrentTime()
                                : undefined
                            }
                            onChange={(e) =>
                              handleCheckInChange(
                                user.id,
                                e.target.value
                              )
                            }
                          />
                        </td>

                        <td>
                          <input
                            type="time"
                            className="form-control form-control-sm"
                            value={
                              current.check_out_time
                            }
                            disabled={
                              current.status !== "Present" ||
                              !isEditableDate
                            }
                            min={
                              current.check_in_time ||
                              undefined
                            }
                            max={
                              isToday
                                ? getCurrentTime()
                                : undefined
                            }
                            onChange={(e) =>
                              handleCheckOutChange(
                                user.id,
                                e.target.value
                              )
                            }
                          />
                        </td>

                        <td>
                          <div className="small">
                            <div><span className="text-muted">Work:</span> <strong className="text-success">{current.working_duration}</strong></div>
                            <div><span className="text-muted">Outside:</span> <strong>{current.outside_duration}</strong></div>
                            <div><span className="text-muted">Breaks &gt;10m:</span> <strong>{current.break_duration}</strong></div>
                          </div>
                          {(current.working_intervals?.length > 0 || current.break_intervals?.length > 0) && (
                            <details className="small mt-1">
                              <summary className="text-primary" style={{ cursor: "pointer" }}>View intervals</summary>
                              {current.working_intervals?.map((interval, index) => (
                                <div key={`work-${index}`} className="text-success mt-1">
                                  Work: {formatAttendanceClock(interval.from_time)} – {formatAttendanceClock(interval.to_time)} ({interval.duration})
                                </div>
                              ))}
                              {current.break_intervals?.map((interval, index) => (
                                <div key={`break-${index}`} className="text-warning-emphasis mt-1">
                                  Break: {formatAttendanceClock(interval.from_time)} – {formatAttendanceClock(interval.to_time)} ({interval.duration})
                                </div>
                              ))}
                            </details>
                          )}
                          {current.attendance_state === "In Progress" && <span className="badge text-bg-success mt-1">In Progress</span>}
                        </td>

                        <td>
                          {getAttendanceSourceLabel(
                            current.attendance_source,
                          ) || "—"}
                        </td>

                        <td className="text-center">
                          <button
                            type="button"
                            className="btn btn-sm btn-primary"
                            disabled={
                              saving || locked || !isEditableDate
                            }
                            title={
                              locked
                                ? "Approved full-day leave - cancel the leave to change attendance"
                                : !isEditableDate
                                ? "Manual attendance changes are limited to today and yesterday"
                                : undefined
                            }
                            onClick={() =>
                              handleSaveAttendance(
                                user
                              )
                            }
                          >
                            {saving ? (
                              <>
                                <span
                                  className="spinner-border spinner-border-sm me-1"
                                  role="status"
                                ></span>

                                Saving
                              </>
                            ) : (
                              <>
                                <i className="bi bi-check-lg me-1"></i>

                                Save
                              </>
                            )}
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

export default Attendance;