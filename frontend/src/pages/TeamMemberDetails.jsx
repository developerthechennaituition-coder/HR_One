import { HRONE_API_BASE } from "../config/api.js";
import {
  useEffect,
  useMemo,
  useState,
} from "react";
import AttendanceDayBreakdown from "../components/AttendanceDayBreakdown";
import { isWeeklyOff, useCompanyWeeklyOffs } from "../services/weeklyOff";
import { buildCalendarRange, clampMonth, dateState, isMonthInRange, isSelectableDate, minSelectableDate, monthOptions, yearOptions } from "../services/attendanceCalendarRange";

const API_URL =
  HRONE_API_BASE + "/api/reporting-manager";

/* ============================================================
   SESSION
============================================================ */

function getSession() {
  try {
    return JSON.parse(
      sessionStorage.getItem("hrms_session") || "null"
    );
  } catch {
    return null;
  }
}

/* ============================================================
   DATE HELPERS
============================================================ */

function normalizeDate(value) {
  if (!value) return "";

  const raw = String(value).trim();

  if (!raw) return "";

  return raw.split("T")[0].split(" ")[0];
}

function formatDate(value) {
  if (!value) return "-";

  const raw = String(value).trim();

  const match = raw.match(
    /^(\d{4})-(\d{2})-(\d{2})$/
  );

  if (match) {
    const [, year, month, day] = match;

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

function formatDateTime(value) {
  if (!value) return "-";

  try {
    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
      return value;
    }

    return date.toLocaleString("en-IN", {
      timeZone: "Asia/Kolkata",
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: true,
      timeZoneName: "short",
    });
  } catch {
    return value;
  }
}

/* ============================================================
   IMPORTANT STATUS HELPER
   ============================================================
   This function was missing in your previous file.
   That missing function was causing the blank page.
============================================================ */

function normalizeStatus(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[\s_-]+/g, "");
}

function getDisplayStatus(member) {
  if (member?.status) {
    return String(member.status);
  }

  return member?.is_active
    ? "Active"
    : "Inactive";
}

function getInitials(name) {
  if (!name) return "U";

  const parts = String(name)
    .trim()
    .split(/\s+/)
    .filter(Boolean);

  if (parts.length === 1) {
    return parts[0]
      .charAt(0)
      .toUpperCase();
  }

  return (
    parts[0].charAt(0) +
    parts[parts.length - 1].charAt(0)
  ).toUpperCase();
}

/* ============================================================
   SAFE VALUE
============================================================ */

function displayValue(value) {
  if (
    value === null ||
    value === undefined ||
    value === ""
  ) {
    return "-";
  }

  return String(value);
}

/* ============================================================
   COMPONENT
============================================================ */

function TeamMemberDetails({
  memberId,
  onNavigate,
}) {
  const todayInIndia = useMemo(() => {
    const parts = new Intl.DateTimeFormat(
      "en",
      {
        timeZone: "Asia/Kolkata",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }
    )
      .formatToParts(new Date())
      .reduce((values, part) => {
        if (part.type !== "literal") {
          values[part.type] = part.value;
        }
        return values;
      }, {});

    return {
      year: Number(parts.year),
      month: Number(parts.month),
      key: `${parts.year}-${parts.month}-${parts.day}`,
    };
  }, []);

  const currentYear = todayInIndia.year;

  const currentMonth = todayInIndia.month;

  const weeklyOffDays = useCompanyWeeklyOffs();

  /* ==========================================================
     STATE
  ========================================================== */

  const [member, setMember] =
    useState(null);

  const [attendance, setAttendance] =
    useState([]);

  const [approvedLeaves, setApprovedLeaves] =
    useState([]);

  // Published holidays that apply to this member (from the backend).
  const [memberHolidays, setMemberHolidays] =
    useState([]);

  // Pending/approved permissions of this member for the month.
  const [memberPermissions, setMemberPermissions] =
    useState([]);

  const [selectedYear, setSelectedYear] =
    useState(currentYear);

  const [selectedMonth, setSelectedMonth] =
    useState(currentMonth);

  const [selectedDate, setSelectedDate] =
    useState(null);

  const [loading, setLoading] =
    useState(true);

  const [
    attendanceLoading,
    setAttendanceLoading,
  ] = useState(false);

  const [error, setError] =
    useState("");

  const [
    attendanceError,
    setAttendanceError,
  ] = useState("");

  /* ==========================================================
     CALENDAR RANGE
     The selected team member's own joining date through
     today + 89 days; future dates are view-only.
  ========================================================== */

  const calendarRange = useMemo(
    () =>
      buildCalendarRange(
        member?.joining_date || member?.date_of_joining,
        todayInIndia.key
      ),
    [member?.joining_date, member?.date_of_joining, todayInIndia.key]
  );

  const changeCalendarMonth = (year, month) => {
    const target = clampMonth(calendarRange, year, month);
    setSelectedYear(target.year);
    setSelectedMonth(target.month);
    setSelectedDate(null);
  };

  const selectCalendarDate = (dateString) => {
    if (isSelectableDate(calendarRange, dateString)) {
      setSelectedDate(dateString);
    }
  };

  // Reset to a valid month when the member (and joining date) changes.
  useEffect(() => {
    if (!isMonthInRange(calendarRange, selectedYear, selectedMonth)) {
      const target = clampMonth(calendarRange, selectedYear, selectedMonth);
      setSelectedYear(target.year);
      setSelectedMonth(target.month);
      setSelectedDate(null);
    }
  }, [calendarRange, selectedYear, selectedMonth]);

  /* ==========================================================
     MONTH NAMES
  ========================================================== */

  const monthNames = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
  ];

  /* ==========================================================
     STATUS COLORS
  ========================================================== */

  const statusColors = {
    present: {
      background: "#198754",
      text: "#ffffff",
    },

    absent: {
      background: "#dc3545",
      text: "#ffffff",
    },

    leave: {
      background: "#6f42c1",
      text: "#ffffff",
    },

    holiday: {
      background: "#ADD8E6",
      text: "#0b3d4a",
    },

    weeklyoff: {
      background: "#6c757d",
      text: "#ffffff",
    },

    notapplicable: {
      background: "#fd7e14",
      text: "#ffffff",
    },

    notmarked: {
      background: "#F0E0C8",
      text: "#7a5a2f",
    },
  };

  /* ==========================================================
     LOAD TEAM MEMBER PROFILE
  ========================================================== */

  useEffect(() => {
    let cancelled = false;

    const loadMember = async () => {
      if (!memberId) {
        if (!cancelled) {
          setError(
            "Employee was not selected."
          );
          setLoading(false);
        }

        return;
      }

      try {
        setLoading(true);
        setError("");
        setMember(null);

        const session = getSession();

        if (!session?.token) {
          throw new Error(
            "Authentication session not found. Please login again."
          );
        }

        const response = await fetch(
          `${API_URL}/team-members/${encodeURIComponent(
            memberId
          )}`,
          {
            method: "GET",

            headers: {
              Authorization: `Bearer ${session.token}`,
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
              "Unable to load employee details."
          );
        }

        /*
         * Backend currently returns the employee object
         * directly.
         *
         * This also supports:
         *
         * {
         *   member: {...}
         * }
         */

        const employee =
          data?.member ||
          data?.employee ||
          data;

        if (
          !employee ||
          typeof employee !== "object"
        ) {
          throw new Error(
            "Employee details were not returned by the server."
          );
        }

        if (!cancelled) {
          setMember(employee);
        }
      } catch (err) {
        console.error(
          "Team member profile loading error:",
          err
        );

        if (!cancelled) {
          setError(
            err?.message ||
              "Unable to load employee details."
          );
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    loadMember();

    return () => {
      cancelled = true;
    };
  }, [memberId]);

  /* ==========================================================
     LOAD ATTENDANCE
  ========================================================== */

  useEffect(() => {
    let cancelled = false;

    const loadAttendance = async () => {
      if (!memberId) {
        return;
      }

      try {
        setAttendanceLoading(true);
        setAttendanceError("");

        const session = getSession();

        if (!session?.token) {
          throw new Error(
            "Authentication session not found."
          );
        }

        const query =
          new URLSearchParams({
            year: String(selectedYear),
            month: String(selectedMonth),
          });

        const response = await fetch(
          `${API_URL}/team-members/${encodeURIComponent(
            memberId
          )}/attendance?${query.toString()}`,
          {
            method: "GET",

            headers: {
              Authorization: `Bearer ${session.token}`,
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
              "Unable to load attendance."
          );
        }

        /*
         * Backend response:
         *
         * {
         *   employee: {...},
         *   count: 5,
         *   attendance: [...]
         * }
         */

        const records = Array.isArray(
          data
        )
          ? data
          : Array.isArray(
              data?.attendance
            )
          ? data.attendance
          : [];

        if (!cancelled) {
          setAttendance(records);
          setApprovedLeaves(
            Array.isArray(data?.approved_leaves)
              ? data.approved_leaves
              : []
          );
          setMemberHolidays(
            Array.isArray(data?.holidays)
              ? data.holidays
              : []
          );
          setMemberPermissions(
            Array.isArray(data?.permissions)
              ? data.permissions
              : []
          );
        }
      } catch (err) {
        console.error(
          "Team member attendance error:",
          err
        );

        if (!cancelled) {
          setAttendance([]);
          setApprovedLeaves([]);
          setMemberHolidays([]);
          setMemberPermissions([]);
          setAttendanceError(
            err?.message ||
              "Unable to load attendance."
          );
        }
      } finally {
        if (!cancelled) {
          setAttendanceLoading(false);
        }
      }
    };

    loadAttendance();

    return () => {
      cancelled = true;
    };
  }, [
    memberId,
    selectedYear,
    selectedMonth,
  ]);

  /* ==========================================================
     ATTENDANCE DATE MAP
  ========================================================== */

  const attendanceByDate = useMemo(() => {
    const map = {};

    attendance.forEach(
      (record) => {
        const date =
          normalizeDate(
            record?.attendance_date ||
              record?.date
          );

        if (date) {
          map[date] = record;
        }
      }
    );

    return map;
  }, [attendance]);

  const approvedLeaveByDate = useMemo(() => {
    const map = {};

    approvedLeaves.forEach((request) => {
      const fromDate = normalizeDate(request?.from_date);
      const toDate = normalizeDate(
        request?.to_date || request?.from_date
      );

      if (!fromDate || !toDate) return;

      const cursor = new Date(`${fromDate}T00:00:00`);
      const endDate = new Date(`${toDate}T00:00:00`);

      if (
        Number.isNaN(cursor.getTime()) ||
        Number.isNaN(endDate.getTime())
      ) return;

      while (cursor <= endDate) {
        const dateKey = [
          cursor.getFullYear(),
          String(cursor.getMonth() + 1).padStart(2, "0"),
          String(cursor.getDate()).padStart(2, "0"),
        ].join("-");

        map[dateKey] = request;
        cursor.setDate(cursor.getDate() + 1);
      }
    });

    return map;
  }, [approvedLeaves]);

  const permissionsByDate = useMemo(() => {
    const map = {};
    memberPermissions.forEach((item) => {
      const key = normalizeDate(item?.permission_date);
      if (key) (map[key] = map[key] || []).push(item);
    });
    return map;
  }, [memberPermissions]);

  /* ==========================================================
     DAYS IN MONTH
  ========================================================== */

  const daysInMonth = new Date(
    selectedYear,
    selectedMonth,
    0
  ).getDate();

  /* ==========================================================
     FIRST DAY
  ========================================================== */

  const firstDayOfMonth =
    new Date(
      selectedYear,
      selectedMonth - 1,
      1
    ).getDay();

  /* ==========================================================
     CALENDAR CELLS
  ========================================================== */

  const calendarCells = [];

  for (
    let index = 0;
    index < firstDayOfMonth;
    index++
  ) {
    calendarCells.push(null);
  }

  for (
    let day = 1;
    day <= daysInMonth;
    day++
  ) {
    calendarCells.push(day);
  }

  while (
    calendarCells.length % 7 !== 0
  ) {
    calendarCells.push(null);
  }

  /* ==========================================================
     DATE KEY
  ========================================================== */

  const getDateKey = (day) => {
    return `${selectedYear}-${String(
      selectedMonth
    ).padStart(2, "0")}-${String(
      day
    ).padStart(2, "0")}`;
  };

  /* ==========================================================
     GET ATTENDANCE
  ========================================================== */

  const getAttendanceForDate = (
    dateString
  ) => {
    const attendanceRecord = attendanceByDate[dateString];
    if (attendanceRecord) return attendanceRecord;

    const leave = approvedLeaveByDate[dateString];
    const holiday = memberHolidays.find((item) => item.holiday_date === dateString);
    if (!leave && holiday) {
      return { attendance_date: dateString, status: "Holiday", holiday_name: holiday.holiday_name, is_calendar_holiday: true };
    }
    if (!leave) {
      // HR-configured weekly-off weekday without an attendance record.
      return isWeeklyOff(weeklyOffDays, new Date(`${dateString}T00:00:00`))
        ? { attendance_date: dateString, status: "Weekly Off", is_weekly_off: true }
        : null;
    }

    return {
      attendance_date: dateString,
      status: "leave",
      is_approved_leave: true,
      leave_request_id: leave.id,
      leave_type_code: leave.leave_type_code,
      leave_type_name: leave.leave_type_name,
      leave_day_part: leave.day_part,
    };
  };

  /* ==========================================================
     STATUS KEY
  ========================================================== */

  const getStatusKey = (record) => {
    const status =
      normalizeStatus(
        record?.status ||
          record?.attendance_status
      );

    if (
      status === "present"
    ) {
      return "present";
    }

    if (
      status === "absent"
    ) {
      return "absent";
    }

    if (
      status === "leave"
    ) {
      return "leave";
    }

    if (
      status === "holiday"
    ) {
      return "holiday";
    }

    if (status === "weeklyoff") {
      return "weeklyoff";
    }

    if (status === "notapplicable") {
      return "notapplicable";
    }

    return "notmarked";
  };

  // Card label: leave type code (CL, ML) for leave, otherwise the status.
  const getCalendarLabel = (record) => {
    const statusKey = getStatusKey(record);
    if (statusKey === "notmarked") return "";
    if (statusKey === "leave") return record.leave_type_code || "Leave";
    if (statusKey === "holiday" && record.holiday_name) return record.holiday_name;
    return {
      present: "Present",
      absent: "Absent",
      holiday: "Holiday",
      weeklyoff: "Weekly Off",
      notapplicable: "Not Applicable",
    }[statusKey];
  };

  /* ==========================================================
     MONTH DATE KEYS
  ========================================================== */

  const monthDateKeys = useMemo(() => {
    const result = [];

    for (
      let day = 1;
      day <= daysInMonth;
      day++
    ) {
      const dateKey = `${selectedYear}-${String(
        selectedMonth
      ).padStart(2, "0")}-${String(
        day
      ).padStart(2, "0")}`;

      // Dates before joining or beyond the calendar window are not counted.
      const state = dateState(calendarRange, dateKey);
      if (!state.hidden && !state.afterWindow) {
        result.push(dateKey);
      }
    }

    return result;
  }, [
    selectedYear,
    selectedMonth,
    daysInMonth,
    calendarRange,
  ]);

  /* ==========================================================
     MONTH COUNTS
  ========================================================== */

  const getCountByStatus = (
    targetStatus
  ) => {
    return monthDateKeys.filter(
      (date) => {
        const record =
          getAttendanceForDate(
            date
          );

        return (
          normalizeStatus(
            record?.status ||
              record?.attendance_status
          ) === targetStatus ||
          (targetStatus === "leave" && record?.is_approved_leave)
        );
      }
    ).length;
  };

  const presentCount =
    getCountByStatus("present");

  const absentCount =
    getCountByStatus("absent");

  const leaveCount =
    getCountByStatus("leave");

  const holidayCount =
    getCountByStatus("holiday");

  const totalMarked =
    presentCount +
    absentCount +
    leaveCount;

  const presentPercentage =
    totalMarked > 0
      ? (
          (presentCount /
            totalMarked) *
          100
        ).toFixed(1)
      : "0.0";

  const absentPercentage =
    totalMarked > 0
      ? (
          (absentCount /
            totalMarked) *
          100
        ).toFixed(1)
      : "0.0";

  const leavePercentage =
    totalMarked > 0
      ? (
          (leaveCount /
            totalMarked) *
          100
        ).toFixed(1)
      : "0.0";

  /* ==========================================================
     SELECTED RECORD
  ========================================================== */

  const selectedRecord =
    selectedDate
      ? getAttendanceForDate(
          selectedDate
        )
      : null;

  /* ==========================================================
     CALENDAR DAY
  ========================================================== */

  const renderCalendarDay = (
    day
  ) => {
    if (!day) {
      return (
        <div className="team-calendar-empty-cell" />
      );
    }

    const dateString =
      getDateKey(day);

    const state = dateState(
      calendarRange,
      dateString
    );

    // Before joining: keep the grid cell empty.
    if (state.hidden) {
      return (
        <div className="team-calendar-empty-cell" />
      );
    }

    // After the 90th day the date is shown without holiday/leave details.
    const record = state.afterWindow
      ? null
      : getAttendanceForDate(
          dateString
        );

    const statusKey =
      getStatusKey(record);

    const colors =
      statusColors[statusKey];

    const isSelected =
      selectedDate ===
      dateString;

    const dateObject =
      new Date(
        selectedYear,
        selectedMonth - 1,
        day
      );

    const isToday = dateString === todayInIndia.key;

    const shortDay =
      dateObject.toLocaleDateString(
        "en-US",
        {
          weekday: "short",
        }
      );

    return (
      <button
        type="button"
        className="team-attendance-day"
        onClick={() =>
          selectCalendarDate(
            dateString
          )
        }
        disabled={state.isFuture}
        title={state.isFuture ? "Upcoming date · view only" : undefined}
        style={{
          position: "relative",
          backgroundColor:
            colors.background,

          color:
            colors.text,

          border: isSelected
            ? "2px solid #0d6efd"
            : state.isFuture
              ? "1px dashed rgba(0,0,0,0.25)"
              : "1px solid rgba(0,0,0,0.12)",

          opacity: state.isFuture ? 0.8 : 1,

          cursor: state.isFuture ? "not-allowed" : "pointer",
        }}
        aria-label={`${dateString} ${
          getCalendarLabel(record) ||
          record?.status ||
          record?.attendance_status ||
          "Not Marked"
        }${state.isFuture ? " (upcoming, not selectable)" : ""}`}
      >
        <span className="team-calendar-weekday">
          {shortDay}
        </span>

        <strong className="team-calendar-day-number">
          {day}
        </strong>
        
        {isToday && (
          <i
            className="bi bi-record-circle-fill"
            aria-label="Today"
            title="Today"
            style={{
              position: "absolute",
              top: "5px",
              right: "6px",
              color: "#2563EB",
              fontSize: "10px",
              lineHeight: 1,
              filter: "drop-shadow(0 1px 2px rgba(37, 99, 235, 0.3))",
            }}
          />
        )}

        {getCalendarLabel(record) && (
          <span className="team-calendar-status">
            {getCalendarLabel(record)}
          </span>
        )}

        {(permissionsByDate[dateString] || []).length > 0 && (
          <span className="team-calendar-status" style={{ fontWeight: 700 }}>
            P {permissionsByDate[dateString][0].start_time}–{permissionsByDate[dateString][0].end_time}
            {permissionsByDate[dateString].length > 1 ? ` +${permissionsByDate[dateString].length - 1}` : ""}
          </span>
        )}
      </button>
    );
  };

  /* ==========================================================
     BACK
  ========================================================== */

  const handleBack = () => {
    onNavigate?.(
      "team-members"
    );
  };

  /* ==========================================================
     LOADING
  ========================================================== */

  if (loading) {
    return (
      <div className="team-member-details-loading">
        <div
          className="spinner-border text-primary"
          role="status"
        >
          <span className="visually-hidden">
            Loading...
          </span>
        </div>

        <strong>
          Loading employee profile...
        </strong>

        <span className="text-muted">
          Please wait while the employee
          information is loaded.
        </span>
      </div>
    );
  }

  /* ==========================================================
     ERROR
  ========================================================== */

  if (error) {
    return (
      <div className="team-member-details-page">
        <button
          type="button"
          className="btn btn-outline-secondary mb-4"
          onClick={handleBack}
        >
          <i className="bi bi-arrow-left me-2" />
          Back to Team Members
        </button>

        <div
          className="alert alert-danger"
          role="alert"
        >
          <i className="bi bi-exclamation-triangle-fill me-2" />

          <strong className="me-2">
            Unable to load employee:
          </strong>

          {error}
        </div>
      </div>
    );
  }

  if (!member) {
    return (
      <div className="team-member-details-page">
        <button
          type="button"
          className="btn btn-outline-secondary mb-4"
          onClick={handleBack}
        >
          <i className="bi bi-arrow-left me-2" />
          Back to Team Members
        </button>

        <div className="alert alert-warning">
          Employee details are not available.
        </div>
      </div>
    );
  }

  /* ==========================================================
     MEMBER DATA
  ========================================================== */

  const department =
    member.department ||
    member.department_name ||
    "-";

  const designation =
    member.designation ||
    member.designation_name ||
    "-";

  const status =
    getDisplayStatus(member);

  const reportingManager =
    member.reporting_manager ||
    member.reporting_manager_name ||
    null;

  /* ==========================================================
     MAIN UI
  ========================================================== */

  return (
    <div className="team-member-details-page">

      {/* ======================================================
          TOP NAVIGATION
      ====================================================== */}

      <div className="team-detail-topbar">

        <button
          type="button"
          className="btn btn-outline-secondary"
          onClick={handleBack}
        >
          <i className="bi bi-arrow-left me-2" />
          Back to Team Members
        </button>

        <div className="team-detail-view-only-badge">
          <i className="bi bi-eye me-1" />
          View Only
        </div>

      </div>

      {/* ======================================================
          PROFILE HERO
      ====================================================== */}

      <div className="team-member-profile-hero">

        <div className="team-member-profile-main">

          <div className="team-member-large-avatar">
            {getInitials(
              member.name
            )}
          </div>

          <div className="team-member-hero-content">

            <h2>
              {member.name ||
                "Employee"}
            </h2>

            <div className="team-member-hero-meta">

              <span>
                <i className="bi bi-person-badge me-1" />
                {displayValue(
                  member.employee_id
                )}
              </span>

              <span>
                <i className="bi bi-briefcase me-1" />
                {designation}
              </span>

              <span>
                <i className="bi bi-building me-1" />
                {department}
              </span>

            </div>

          </div>

        </div>

        <div className="team-member-hero-status">

          <span
            className={
              normalizeStatus(
                status
              ) === "active"
                ? "team-profile-status active"
                : "team-profile-status inactive"
            }
          >
            <span className="team-status-dot" />
            {status}
          </span>

          <small>
            Reporting Manager View
          </small>

        </div>

      </div>

      {/* ======================================================
          QUICK INFORMATION
      ====================================================== */}

      <div className="row g-3 mb-4">

        <div className="col-12 col-md-6 col-xl-3">
          <div className="team-detail-quick-card">

            <div className="team-detail-quick-icon">
              <i className="bi bi-calendar-event" />
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

          </div>
        </div>

        <div className="col-12 col-md-6 col-xl-3">
          <div className="team-detail-quick-card">

            <div className="team-detail-quick-icon">
              <i className="bi bi-cake2" />
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

          </div>
        </div>

        <div className="col-12 col-md-6 col-xl-3">
          <div className="team-detail-quick-card">

            <div className="team-detail-quick-icon">
              <i className="bi bi-envelope" />
            </div>

            <div>
              <span>
                Work Email
              </span>

              <strong>
                {displayValue(
                  member.work_email
                )}
              </strong>
            </div>

          </div>
        </div>

        <div className="col-12 col-md-6 col-xl-3">
          <div className="team-detail-quick-card">

            <div className="team-detail-quick-icon">
              <i className="bi bi-telephone" />
            </div>

            <div>
              <span>
                Mobile
              </span>

              <strong>
                {displayValue(
                  [member.country_code, member.mobile]
                    .filter(Boolean)
                    .join(" ")
                )}
              </strong>
            </div>

          </div>
        </div>

      </div>

      {/* ======================================================
          PERSONAL INFORMATION
      ====================================================== */}

      <div className="team-member-section-card">

        <div className="team-member-section-heading">

          <div className="team-member-section-heading-icon">
            <i className="bi bi-person-vcard" />
          </div>

          <div>
            <h5>
              Personal Information
            </h5>

            <p>
              Employee personal and contact details
            </p>
          </div>

        </div>

        <div className="row g-4">

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Employee ID
              </span>

              <strong>
                {displayValue(
                  member.employee_id
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Full Name
              </span>

              <strong>
                {displayValue(
                  member.name
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Date of Birth
              </span>

              <strong>
                {formatDate(
                  member.date_of_birth
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Work Email
              </span>

              <strong>
                {displayValue(
                  member.work_email
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Mobile
              </span>

              <strong>
                {displayValue(
                  member.mobile
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Blood Group
              </span>

              <strong>
                {displayValue(
                  member.blood_group
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Nationality
              </span>

              <strong>
                {displayValue(
                  member.nationality
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Religion
              </span>

              <strong>
                {displayValue(
                  member.religion || "Not Specified"
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Gender
              </span>

              <strong>
                {displayValue(
                  member.gender
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Marital Status
              </span>

              <strong>
                {displayValue(
                  member.marital_status
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Address
              </span>

              <strong className="team-info-address">
                {displayValue(
                  member.address
                )}
              </strong>
            </div>
          </div>

        </div>

      </div>

      {/* ======================================================
          EMPLOYMENT INFORMATION
      ====================================================== */}

      <div className="team-member-section-card">

        <div className="team-member-section-heading">

          <div className="team-member-section-heading-icon">
            <i className="bi bi-briefcase" />
          </div>

          <div>
            <h5>
              Employment Information
            </h5>

            <p>
              Organization and employment details
            </p>
          </div>

        </div>

        <div className="row g-4">

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Department
              </span>

              <strong>
                {department}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Designation
              </span>

              <strong>
                {designation}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Date of Joining
              </span>

              <strong>
                {formatDate(
                  member.date_of_joining
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Work Field
              </span>

              <strong>
                {displayValue(
                  member.work_field
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">

              <span>
                Employee Status
              </span>

              <strong>

                <span
                  className={
                    normalizeStatus(
                      status
                    ) === "active"
                      ? "team-profile-status active"
                      : "team-profile-status inactive"
                  }
                >
                  <span className="team-status-dot" />
                  {status}
                </span>

              </strong>

            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Reporting Manager
              </span>

              <strong>
                {typeof reportingManager ===
                "object"
                  ? displayValue(
                      [reportingManager?.name ||
                        reportingManager?.full_name ||
                        reportingManager?.id , reportingManager?.employee_id]
                        .filter(Boolean).join(" - ")
                    )
                  : displayValue(
                      reportingManager
                    )}
              </strong>
            </div>
          </div>

        </div>

      </div>

      {/* ======================================================
          ADDITIONAL EMPLOYMENT / CONTACT
      ====================================================== */}

      <div className="team-member-section-card">

        <div className="team-member-section-heading">

          <div className="team-member-section-heading-icon">
            <i className="bi bi-info-circle" />
          </div>

          <div>
            <h5>
              Additional Information
            </h5>

            <p>
              Additional employee information available in HRMS
            </p>
          </div>

        </div>

        <div className="row g-4">

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Employee User ID
              </span>

              <strong>
                {displayValue(
                  member.user_id ||
                    member.id
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Employee Role
              </span>

              <strong>
                {displayValue(
                  member.role
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Account Status
              </span>

              <strong>
                {member.is_active
                  ? "Active"
                  : "Inactive"}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                City
              </span>

              <strong>
                {displayValue(
                  member.city
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                State
              </span>

              <strong>
                {displayValue(
                  member.state
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Country
              </span>

              <strong>
                {displayValue(
                  member.country
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Mobile Country
              </span>

              <strong>
                {displayValue(
                  member.mobile_country
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Country ISO Code
              </span>

              <strong>
                {displayValue(
                  member.country_iso
                )}
              </strong>
            </div>
          </div>

          <div className="col-12 col-md-6 col-xl-4">
            <div className="team-info-field">
              <span>
                Mobile Country Code
              </span>

              <strong>
                {displayValue(
                  member.country_code
                )}
              </strong>
            </div>
          </div>

        </div>

      </div>

      {/* ======================================================
          ATTENDANCE
      ====================================================== */}

      <div className="team-member-section-card">

        <div className="team-attendance-heading">

          <div className="team-member-section-heading">

            <div className="team-member-section-heading-icon">
              <i className="bi bi-calendar-check" />
            </div>

            <div>
              <h5>
                Attendance
              </h5>

              <p>
                Monthly attendance overview
              </p>
            </div>
          </div>
            <div className="team-attendance-filters">

              {/* Joining month/year through the 90-day window only. */}
              <select
                className="form-select"
                aria-label="Month"
                value={selectedMonth}
                onChange={(event) => {
                  changeCalendarMonth(
                    selectedYear,
                    Number(
                      event.target.value
                    )
                  );
                }}
              >
                {monthOptions(
                  calendarRange,
                  selectedYear
                ).map(
                  (item) => (
                    <option
                      key={item.value}
                      value={item.value}
                    >
                      {item.label}
                    </option>
                  )
                )}
              </select>

              <select
                className="form-select"
                aria-label="Year"
                value={selectedYear}
                onChange={(event) => {
                  changeCalendarMonth(
                    Number(
                      event.target.value
                    ),
                    selectedMonth
                  );
                }}
              >
                {yearOptions(
                  calendarRange
                ).map(
                  (year) => (
                    <option
                      key={year}
                      value={year}
                    >
                      {year}
                    </option>
                  )
                )}
              </select>
            </div>

          

        </div>
        {/* ====================================================
            ATTENDANCE SUMMARY
        ==================================================== */}

        <div className="row g-3 mt-2 mb-4">

          <div className="col-12 col-md-3">

            <div className="team-attendance-summary-card present">

              <span>
                Present
              </span>

              <strong>
                {presentCount}
              </strong>

              <small>
                {presentPercentage}%
              </small>

            </div>

          </div>

          <div className="col-12 col-md-3">

            <div className="team-attendance-summary-card absent">

              <span>
                Absent
              </span>

              <strong>
                {absentCount}
              </strong>

              <small>
                {absentPercentage}%
              </small>

            </div>

          </div>

          <div className="col-12 col-md-3">

            <div className="team-attendance-summary-card leave">

              <span>
                Leave
              </span>

              <strong>
                {leaveCount}
              </strong>

              <small>
                {leavePercentage}%
              </small>

            </div>

          </div>

          <div className="col-12 col-md-3">

            <div className="team-attendance-summary-card">

              <span>
                Holidays
              </span>

              <strong>
                {holidayCount}
              </strong>

              <small>
                Calendar
              </small>

            </div>

          </div>

        </div>

        {/* ====================================================
            LEGEND
        ==================================================== */}

        <div className="team-attendance-legend">

          {[
            {
              key: "present",
              label: "Present",
            },
            {
              key: "absent",
              label: "Absent",
            },
            {
              key: "leave",
              label: "Leave",
            },
            {
              key: "holiday",
              label: "Holiday",
            },
            {
              key: "weeklyoff",
              label: "Weekly Off",
            },
            {
              key: "notapplicable",
              label: "Not Applicable",
            },
            {
              key: "notmarked",
              label: "Not Marked",
            },
          ].map(
            (item) => (
              <div
                key={item.key}
                className="team-attendance-legend-item"
              >

                <span
                  style={{
                    backgroundColor:
                      statusColors[
                        item.key
                      ].background,
                  }}
                />

                <span>
                  {item.label}
                </span>

              </div>
            )
          )}

        </div>

        {/* ====================================================
            ATTENDANCE CALENDAR
        ==================================================== */}

        {attendanceLoading ? (
          <div className="team-member-attendance-loading">

            <div
              className="spinner-border spinner-border-sm text-primary"
              role="status"
            />

            <span>
              Loading attendance...
            </span>

          </div>
        ) : (
          <div className="team-attendance-calendar-wrapper">

            <div className="team-attendance-calendar-title">
              {monthNames[
                selectedMonth - 1
              ]}{" "}
              {selectedYear}
            </div>

            {/* WEEK HEADER */}

            <div className="team-attendance-week-header">

              {[
                "Sun",
                "Mon",
                "Tue",
                "Wed",
                "Thu",
                "Fri",
                "Sat",
              ].map(
                (day) => (
                  <div key={day}>
                    {day}
                  </div>
                )
              )}

            </div>

            {/* CALENDAR GRID */}

            <div className="team-attendance-calendar-grid">

              {calendarCells.map(
                (
                  day,
                  index
                ) => (
                  <div
                    key={
                      day
                        ? `day-${day}`
                        : `empty-${index}`
                    }
                  >
                    {renderCalendarDay(
                      day
                    )}
                  </div>
                )
              )}

            </div>

          </div>
        )}

        {/* ====================================================
            SELECTED DATE
        ==================================================== */}

        {selectedDate && (
          <div className="team-selected-attendance-card">

            <div className="team-selected-attendance-header">

              <div>

                <span>
                  Selected Date
                </span>

                <strong>
                  {formatDate(
                    selectedDate
                  )}
                </strong>

              </div>

              <span
                className={
                  `team-selected-status ${getStatusKey(
                    selectedRecord
                  )}`
                }
              >
                {selectedRecord?.status ||
                  selectedRecord?.attendance_status ||
                  "Not Marked"}
              </span>

            </div>

            {!selectedRecord ? (
              <div className="team-selected-empty">

                <i className="bi bi-calendar-x" />

                <div>
                  No attendance record has
                  been marked for this date.
                </div>

              </div>
            ) : (
              <div className="row g-3">

                <div className="col-12 col-md-4">

                  <div className="team-selected-info">

                    <span>
                      Status
                    </span>

                    <strong>
                      {displayValue(
                        selectedRecord.status ||
                          selectedRecord.attendance_status
                      )}
                    </strong>

                  </div>

                </div>

                <div className="col-12 col-md-4">

                  <div className="team-selected-info">

                    <span>
                      Check In
                    </span>

                    <strong>
                      {displayValue(
                        selectedRecord.check_in ||
                          selectedRecord.check_in_time
                      )}
                    </strong>

                  </div>

                </div>

                <div className="col-12 col-md-4">

                  <div className="team-selected-info">

                    <span>
                      Check Out
                    </span>

                    <strong>
                      {displayValue(
                        selectedRecord.check_out ||
                          selectedRecord.check_out_time
                      )}
                    </strong>

                  </div>

                </div>

                <div className="col-12 col-md-4">

                  <div className="team-selected-info">

                    <span>
                     Attendance Type
                    </span>

                    <strong>
                      {selectedRecord.attendance_type ||   
                        "-"}
                    </strong>

                  </div>

                </div>

                <div className="col-12 col-md-4">

                  <div className="team-selected-info">

                    <span>
                      Attendance Date
                    </span>

                    <strong>
                      {formatDate(
                        selectedRecord.attendance_date ||
                          selectedRecord.date
                      )}
                    </strong>

                  </div>

                </div>

                <div className="col-12 col-md-4">

                  <div className="team-selected-info">

                    <span>
                      Last Updated
                    </span>

                    <strong>
                      {formatDateTime(
                        selectedRecord.updated_at
                      )}
                    </strong>

                  </div>

                </div>

              </div>
            )}

            {(permissionsByDate[selectedDate] || []).length > 0 && (
              <div className="team-selected-info mt-3">
                <span>
                  Permission
                </span>

                {permissionsByDate[selectedDate].map((item, index) => (
                  <strong className="d-block" key={`${item.start_time}-${index}`}>
                    {item.start_time} – {item.end_time} ({item.status === "approved" ? "Approved" : "Pending"})
                  </strong>
                ))}
              </div>
            )}

          </div>
        )}

      </div>

          <AttendanceDayBreakdown
            userId={memberId}
            title="Team member · working time and breaks"
            date={selectedDate || undefined}
            minDate={minSelectableDate(calendarRange)}
            onDateChange={selectCalendarDate}
          />

          {/* Attendance error */}

          {attendanceError && (
          <div
            className="alert alert-warning mt-3 mb-0"
            role="alert"
          >
            <i className="bi bi-exclamation-triangle me-2" />
            {attendanceError}
          </div>
          )}
         
    </div>
  );
}

export default TeamMemberDetails;