import { HRONE_API_BASE } from "../config/api.js";
import { useEffect, useMemo, useState } from "react";
import AttendanceDayBreakdown from "../components/AttendanceDayBreakdown";
import { authenticatedFetch as fetch } from "../services/authenticatedFetch";

const API_URL = HRONE_API_BASE + "/api/auth/me";

const ATTENDANCE_API_URL =
  HRONE_API_BASE + "/api/attendance/my";

const LEAVE_API_URL =
  HRONE_API_BASE + "/api/leave";

function Profile({ onNavigate }) {
  const [user, setUser] = useState(null);
  const [organizationData, setOrganizationData] = useState(null);
  const [organizationLoading, setOrganizationLoading] = useState(true);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  const currentDate = useMemo(() => {
    const parts = new Intl.DateTimeFormat("en", {
      timeZone: "Asia/Kolkata",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    })
      .formatToParts(new Date())
      .reduce((values, part) => {
        if (part.type !== "literal") {
          values[part.type] = part.value;
        }
        return values;
      }, {});

    return new Date(
      Number(parts.year),
      Number(parts.month) - 1,
      Number(parts.day),
    );
  }, []);

  const currentYear = currentDate.getFullYear();
  const currentMonth = currentDate.getMonth() + 1;
  const todayDateKey = `${currentYear}-${String(
    currentMonth,
  ).padStart(2, "0")}-${String(
    currentDate.getDate(),
  ).padStart(2, "0")}`;

  const selectedYear = currentYear;

  const [selectedMonth, setSelectedMonth] =
    useState(currentMonth);

  const [attendance, setAttendance] = useState([]);

  const [approvedLeaves, setApprovedLeaves] =
    useState([]);

  const [attendanceLoading, setAttendanceLoading] =
    useState(false);

  const [attendanceError, setAttendanceError] =
    useState("");

  const [selectedAttendanceDate, setSelectedAttendanceDate] =
    useState(null);

  useEffect(() => {
    let cancelled = false;
    const loadOrganizationData = async () => {
      try {
        const response = await fetch(HRONE_API_BASE + "/api/organization/me");
        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          throw new Error(body.detail || "Unable to load organization information.");
        }
        const data = await response.json();
        if (!cancelled) setOrganizationData(data);
      } catch (requestError) {
        if (!cancelled) {
          console.error("Organization profile loading error:", requestError);
          setOrganizationData({ error: requestError.message });
        }
      } finally {
        if (!cancelled) setOrganizationLoading(false);
      }
    };
    loadOrganizationData();
    return () => { cancelled = true; };
  }, []);

  /*
   * ============================================================
   * DATE DISPLAY
   * ============================================================
   */

  function formatDateForDisplay(value) {
    if (!value) return "";

    const stringValue = String(value).trim();

    if (/^\d{2}-\d{2}-\d{4}$/.test(stringValue)) {
      return stringValue;
    }

    const match = stringValue.match(
      /^(\d{4})-(\d{2})-(\d{2})$/,
    );

    if (!match) return stringValue;

    const [, year, month, day] = match;

    return `${day}/${month}/${year}`;
  }

  /*
   * ============================================================
   * NORMALIZE DATE
   * ============================================================
   *
   * Converts:
   *
   * 2026-10-05
   * 2026-10-05T00:00:00
   *
   * into:
   *
   * 2026-10-05
   *
   * This prevents date comparison problems.
   */

  const normalizeDate = (value) => {
    if (!value) return "";

    return String(value)
      .trim()
      .split("T")[0];
  };

  /*
   * ============================================================
   * LEAVE DAY PART
   * ============================================================
   */

  const getLeaveDayPartLabel = (dayPart) => {
    if (!dayPart) return "Full Day";

    const normalized = String(dayPart)
      .trim()
      .toLowerCase();

    if (
      normalized === "full_day" ||
      normalized === "full day"
    ) {
      return "Full Day";
    }

    if (
      normalized === "first_half" ||
      normalized === "first half"
    ) {
      return "First Half";
    }

    if (
      normalized === "second_half" ||
      normalized === "second half"
    ) {
      return "Second Half";
    }

    return dayPart;
  };

  /*
   * ============================================================
   * LOAD PROFILE
   * ============================================================
   */

  useEffect(() => {
    let cancelled = false;
    let fetching = false;

    const fetchProfile = async (initialLoad = false) => {
      if (fetching) return;
      fetching = true;

      if (initialLoad) {
        setIsLoading(true);
        setError("");
      }

      try {
        const storedSession =
          sessionStorage.getItem("hrms_session");

        if (!storedSession) {
          throw new Error(
            "Login session not found. Please login again.",
          );
        }

        const session = JSON.parse(storedSession);

        if (!session?.token) {
          throw new Error(
            "Authentication token not found. Please login again.",
          );
        }

        const response = await fetch(API_URL, {
          method: "GET",
          headers: {
            Authorization: `Bearer ${session.token}`,
            "Content-Type": "application/json",
          },
        });

        if (response.status === 401) {
          throw new Error(
            "Your session has expired. Please login again.",
          );
        }

        if (!response.ok) {
          if (!initialLoad) return;
          throw new Error(
            "Unable to load profile details.",
          );
        }

        const profileData =
          await response.json();

        if (!cancelled) setUser(profileData);
      } catch (requestError) {
        if (!initialLoad || cancelled) return;

        console.error(
          "Profile loading error:",
          requestError,
        );

        setError(
          requestError.message ||
            "Unable to load profile details.",
        );
      } finally {
        fetching = false;
        if (initialLoad && !cancelled) setIsLoading(false);
      }
    };

    fetchProfile(true);
    const refreshTimer = window.setInterval(
      () => fetchProfile(),
      5000,
    );

    return () => {
      cancelled = true;
      window.clearInterval(refreshTimer);
    };
  }, []);

  /*
   * ============================================================
   * LOAD ATTENDANCE + APPROVED LEAVE
   * ============================================================
   *
   * Attendance and Leave are separate modules.
   *
   * Attendance API:
   *   Present / Absent / existing attendance
   *
   * Leave API:
   *   Approved future/current leave
   *
   * Both are loaded here and combined for the calendar.
   * ============================================================
   */

  useEffect(() => {
    const fetchAttendanceAndLeave = async () => {
      if (!user?.id) return;

      setAttendanceLoading(true);
      setAttendanceError("");
      setSelectedAttendanceDate(null);

      try {
        const storedSession =
          sessionStorage.getItem("hrms_session");

        if (!storedSession) {
          throw new Error(
            "Login session not found. Please login again.",
          );
        }

        const session = JSON.parse(storedSession);

        if (!session?.token) {
          throw new Error(
            "Authentication token not found.",
          );
        }

        const requestHeaders = {
          Authorization: `Bearer ${session.token}`,
          "Content-Type": "application/json",
        };

        /*
         * --------------------------------------------------------
         * LOAD ATTENDANCE
         * --------------------------------------------------------
         */

        const attendanceResponse =
          await fetch(
            `${ATTENDANCE_API_URL}?year=${selectedYear}&month=${selectedMonth}`,
            {
              method: "GET",
              headers: requestHeaders,
            },
          );

        if (attendanceResponse.status === 401) {
          throw new Error(
            "Your session has expired. Please login again.",
          );
        }

        if (!attendanceResponse.ok) {
          let errorMessage =
            "Unable to load attendance.";

          try {
            const errorData =
              await attendanceResponse.json();

            errorMessage =
              errorData.detail ||
              errorMessage;
          } catch {}

          throw new Error(errorMessage);
        }

        const attendanceData =
          await attendanceResponse.json();

        setAttendance(
          Array.isArray(attendanceData)
            ? attendanceData
            : [],
        );

        /*
         * --------------------------------------------------------
         * LOAD MY LEAVE REQUESTS
         * --------------------------------------------------------
         */

        const leaveResponse =
          await fetch(
            `${LEAVE_API_URL}/my-requests`,
            {
              method: "GET",
              headers: requestHeaders,
            },
          );

        if (leaveResponse.status === 401) {
          throw new Error(
            "Your session has expired. Please login again.",
          );
        }

        if (!leaveResponse.ok) {
          let errorMessage =
            "Unable to load leave information.";

          try {
            const errorData =
              await leaveResponse.json();

            errorMessage =
              errorData.detail ||
              errorData.message ||
              errorMessage;
          } catch {}

          throw new Error(errorMessage);
        }

        const leaveData =
          await leaveResponse.json();

        /*
         * --------------------------------------------------------
         * ONLY APPROVED LEAVE
         * --------------------------------------------------------
         *
         * Pending / rejected / cancelled requests must NOT
         * appear as approved leave in the attendance calendar.
         */

        const approvedOnly =
          Array.isArray(leaveData)
            ? leaveData.filter(
                (request) =>
                  String(
                    request?.status || "",
                  )
                    .trim()
                    .toLowerCase() ===
                  "approved",
              )
            : [];

        setApprovedLeaves(approvedOnly);
      } catch (requestError) {
        console.error(
          "Attendance / Leave loading error:",
          requestError,
        );

        setAttendanceError(
          requestError.message ||
            "Unable to load attendance and leave information.",
        );

        setAttendance([]);
        setApprovedLeaves([]);
      } finally {
        setAttendanceLoading(false);
      }
    };

    fetchAttendanceAndLeave();
  }, [user?.id, selectedYear, selectedMonth]);

  /*
   * ============================================================
   * MONTH INFORMATION
   * ============================================================
   */

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

  /*
   * ============================================================
   * STATUS COLORS
   * ============================================================
   */

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

    notmarked: {
      background: "#F0E0C8",
      text: "#7a5a2f",
    },
  };

  /*
   * ============================================================
   * DAYS IN SELECTED MONTH
   * ============================================================
   */

  const daysInMonth = new Date(
    selectedYear,
    selectedMonth,
    0,
  ).getDate();

  /*
   * ============================================================
   * FIRST DAY OF MONTH
   * ============================================================
   */

  const firstDayOfMonth = new Date(
    selectedYear,
    selectedMonth - 1,
    1,
  ).getDay();

  /*
   * ============================================================
   * CALENDAR CELLS
   * ============================================================
   */

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

  while (calendarCells.length % 7 !== 0) {
    calendarCells.push(null);
  }

  const calendarRows = [];

  for (
    let index = 0;
    index < calendarCells.length;
    index += 7
  ) {
    calendarRows.push(
      calendarCells.slice(index, index + 7),
    );
  }

  /*
   * ============================================================
   * DAY NAME
   * ============================================================
   */

  const getShortDayName = (day) => {
    const dateObj = new Date(
      selectedYear,
      selectedMonth - 1,
      day,
    );

    return dateObj.toLocaleDateString(
      "en-US",
      {
        weekday: "short",
      },
    );
  };

  /*
   * ============================================================
   * API DATE FORMAT
   * ============================================================
   */

  const formatDate = (day) => {
    return `${selectedYear}-${String(
      selectedMonth,
    ).padStart(2, "0")}-${String(day).padStart(
      2,
      "0",
    )}`;
  };

  /*
   * ============================================================
   * GET ATTENDANCE FOR DATE
   * ============================================================
   */

  const getAttendanceForDate = (dateString) => {
    return attendance.find(
      (record) =>
        normalizeDate(
          record?.attendance_date,
        ) === dateString,
    );
  };

  /*
   * ============================================================
   * BUILD APPROVED LEAVE DATE MAP
   * ============================================================
   *
   * Example:
   *
   * Leave:
   * 2026-10-05 -> 2026-10-07
   *
   * becomes:
   *
   * 2026-10-05 -> Casual Leave
   * 2026-10-06 -> Casual Leave
   * 2026-10-07 -> Casual Leave
   *
   * This is what allows future leave dates to appear
   * in the calendar.
   * ============================================================
   */

  const approvedLeaveByDate = useMemo(() => {
    const map = {};

    approvedLeaves.forEach((request) => {
      const fromDate =
        normalizeDate(request?.from_date);

      const toDate =
        normalizeDate(
          request?.to_date ||
            request?.from_date,
        );

      if (!fromDate || !toDate) {
        return;
      }

      let cursor = new Date(
        `${fromDate}T00:00:00`,
      );

      const endDate = new Date(
        `${toDate}T00:00:00`,
      );

      if (
        Number.isNaN(cursor.getTime()) ||
        Number.isNaN(endDate.getTime())
      ) {
        return;
      }

      while (cursor <= endDate) {
        const year = cursor.getFullYear();
        const month = String(
          cursor.getMonth() + 1,
        ).padStart(2, "0");
        const day = String(
          cursor.getDate(),
        ).padStart(2, "0");

        const dateKey =
          `${year}-${month}-${day}`;

        /*
         * If multiple approved leave requests somehow
         * overlap, keep the latest mapped request.
         */

        map[dateKey] = request;

        cursor.setDate(
          cursor.getDate() + 1,
        );
      }
    });

    return map;
  }, [approvedLeaves]);

  /*
   * ============================================================
   * GET APPROVED LEAVE FOR DATE
   * ============================================================
   */

  const getApprovedLeaveForDate = (
    dateString,
  ) => {
    return (
      approvedLeaveByDate[dateString] ||
      null
    );
  };

  /*
   * ============================================================
   * GET COMBINED RECORD
   * ============================================================
   *
   * Priority:
   *
   * 1. Existing attendance
   * 2. Approved leave
   * 3. No record
   *
   * This prevents approved leave from unnecessarily
   * replacing a real attendance record.
   *
   * For future dates where attendance does not exist,
   * approved leave becomes the calendar record.
   * ============================================================
   */

  const getCombinedRecordForDate = (
    dateString,
  ) => {
    const attendanceRecord =
      getAttendanceForDate(
        dateString,
      );

    if (attendanceRecord) {
      return {
        ...attendanceRecord,
        is_approved_leave: false,
      };
    }

    const leaveRecord =
      getApprovedLeaveForDate(
        dateString,
      );

    if (leaveRecord) {
      return {
        attendance_date: dateString,
        status: "leave",
        is_approved_leave: true,

        leave_type_code:
          leaveRecord.leave_type_code ||
          "",

        leave_type_name:
          leaveRecord.leave_type_name ||
          leaveRecord.leave_type_code ||
          "Leave",

        leave_day_part:
          leaveRecord.day_part ||
          "full_day",

        leave_day_part_label:
          leaveRecord.day_part_label ||
          getLeaveDayPartLabel(
            leaveRecord.day_part,
          ),

        leave_request_id:
          leaveRecord.id,

        leave_from_date:
          leaveRecord.from_date,

        leave_to_date:
          leaveRecord.to_date,

        leave_status:
          leaveRecord.status,
      };
    }

    return null;
  };

  /*
   * ============================================================
   * STATUS KEY
   * ============================================================
   */

  const getStatusKey = (record) => {
    const statusValue =
      record?.status?.toLowerCase();

    if (statusValue === "present") {
      return "present";
    }

    if (statusValue === "absent") {
      return "absent";
    }

    if (
      statusValue === "leave" ||
      record?.is_approved_leave
    ) {
      return "leave";
    }

    if (statusValue === "holiday") {
      return "holiday";
    }

    return "notmarked";
  };

  /*
   * ============================================================
   * SELECTED ATTENDANCE RECORD
   * ============================================================
   */

  const selectedRecord =
    selectedAttendanceDate
      ? getCombinedRecordForDate(
          selectedAttendanceDate,
        )
      : null;

  /*
   * ============================================================
   * ATTENDANCE COUNTS
   * ============================================================
   *
   * Approved leave is included in the Leave count even when
   * there is no attendance record for the future date.
   * ============================================================
   */

  const monthDateKeys = useMemo(() => {
    const keys = [];

    for (
      let day = 1;
      day <= daysInMonth;
      day++
    ) {
      keys.push(formatDate(day));
    }

    return keys;
  }, [
    selectedYear,
    selectedMonth,
    daysInMonth,
  ]);

  const presentCount =
    monthDateKeys.filter((dateString) => {
      const record =
        getAttendanceForDate(dateString);

      return (
        record?.status?.toLowerCase() ===
        "present"
      );
    }).length;

  const absentCount =
    monthDateKeys.filter((dateString) => {
      const record =
        getAttendanceForDate(dateString);

      return (
        record?.status?.toLowerCase() ===
        "absent"
      );
    }).length;

  const leaveCount =
    monthDateKeys.filter((dateString) => {
      const attendanceRecord =
        getAttendanceForDate(
          dateString,
        );

      if (
        attendanceRecord?.status?.toLowerCase() ===
        "leave"
      ) {
        return true;
      }

      return Boolean(
        getApprovedLeaveForDate(
          dateString,
        ),
      );
    }).length;

  /*
   * ============================================================
   * ATTENDANCE PERCENTAGE
   * ============================================================
   */

  const percentageTotal =
    presentCount +
    absentCount +
    leaveCount;

  const presentPercentage =
    percentageTotal > 0
      ? (
          (presentCount /
            percentageTotal) *
          100
        ).toFixed(1)
      : "0.0";

  const absentPercentage =
    percentageTotal > 0
      ? (
          (absentCount /
            percentageTotal) *
          100
        ).toFixed(1)
      : "0.0";

  const leavePercentage =
    percentageTotal > 0
      ? (
          (leaveCount /
            percentageTotal) *
          100
        ).toFixed(1)
      : "0.0";

  /*
   * ============================================================
   * RENDER CALENDAR DAY
   * ============================================================
   */

  const renderCalendarDay = (day) => {
    if (!day) {
      return null;
    }

    const dateString =
      formatDate(day);

    const isToday = dateString === todayDateKey;

    const record =
      getCombinedRecordForDate(
        dateString,
      );

    const statusKey =
      getStatusKey(record);

    const colors =
      statusColors[statusKey];

    const isSelected =
      selectedAttendanceDate ===
      dateString;

    const shortDay =
      getShortDayName(day);

    return (
      <button
        type="button"
        onClick={() =>
          setSelectedAttendanceDate(
            dateString,
          )
        }
        className="w-100"
        style={{
          position: "relative",
          minHeight: "64px",
          width: "100%",

          border: isSelected
            ? "2px solid #0d6efd"
            : "1px solid rgba(0,0,0,0.12)",

          borderRadius: "8px",

          backgroundColor:
            colors.background,

          color: colors.text,

          cursor: "pointer",

          padding: "4px 2px",

          display: "flex",

          flexDirection: "column",

          justifyContent: "center",

          alignItems: "center",

          transition:
            "border-color 0.15s ease, transform 0.15s ease",
        }}
      >
        <div
          style={{
            fontSize: "10px",
            opacity: 0.85,
            lineHeight: "1.2",
          }}
        >
          {shortDay}
        </div>

        <div
          className="fw-semibold"
          style={{
            fontSize: "14px",
            lineHeight: "1.3",
          }}
        >
          {day}
        </div>

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

        {/* ----------------------------------------------------
            Show Leave Type Code on approved leave calendar day
        ---------------------------------------------------- */}

       {/*{record?.is_approved_leave && (
          <div
            style={{
              fontSize: "9px",
              fontWeight: "600",
              marginTop: "2px",
              opacity: 0.95,
              maxWidth: "95%",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {record.leave_type_code ||
              "Leave"}
          </div>
        )} */}
      </button>
    );
  };

  /*
   * ============================================================
   * LOADING
   * ============================================================
   */

  if (isLoading) {
    return (
      <div className="container-fluid">
        <div className="d-flex justify-content-center align-items-center py-5">
          <div className="text-center">
            <div
              className="spinner-border text-primary mb-3"
              role="status"
              aria-hidden="true"
            ></div>

            <p className="text-muted mb-0">
              Loading profile...
            </p>
          </div>
        </div>
      </div>
    );
  }

  /*
   * ============================================================
   * PROFILE ERROR
   * ============================================================
   */

  if (error) {
    return (
      <div className="container-fluid">
        <div className="d-flex justify-content-between align-items-center mb-4">
          <div>
            <h2 className="mb-1">
              My Profile
            </h2>

            <p className="text-muted mb-0">
              View your account information
            </p>
          </div>
        </div>

        <div
          className="alert alert-danger"
          role="alert"
        >
          <strong>
            Unable to load profile
          </strong>

          <div className="mt-1">
            {error}
          </div>
        </div>
      </div>
    );
  }

  /*
   * ============================================================
   * NO USER
   * ============================================================
   */

  if (!user) {
    return (
      <div className="container-fluid">
        <div
          className="alert alert-warning"
          role="alert"
        >
          Profile information is not available.
        </div>
      </div>
    );
  }

  /*
   * ============================================================
   * LAST LOGIN
   * ============================================================
   */

  const formattedLastLogin =
    user.last_login
      ? new Date(
          user.last_login,
        ).toLocaleString("en-IN", {
          timeZone: "Asia/Kolkata",
          day: "2-digit",
          month: "2-digit",
          year: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          hour12: true,
          timeZoneName: "short",
        })
      : "Not available";

  /*
   * ============================================================
   * MAIN PROFILE
   * ============================================================
   */

  return (
    <div className="container-fluid">
      {/* ======================================================
          PAGE HEADER
      ====================================================== */}

      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <h2 className="mb-1">
            My Profile
          </h2>

          <p className="text-muted mb-0">
            View your account information
          </p>
        </div>

        <button
          type="button"
          className="btn btn-primary"
          onClick={() =>
            onNavigate?.(
              "change-password",
            )
          }
        >
          <i className="bi bi-key me-2"></i>
          Create New Password
        </button>
      </div>

      <div className="card border-0 shadow-sm">
        <div className="card-body p-4">

          {/* ==================================================
              PROFILE HEADER
          ================================================== */}

          <div className="d-flex align-items-center mb-4">
            <div
              className="bg-primary text-white rounded-circle d-flex justify-content-center align-items-center me-3"
              style={{
                width: "64px",
                height: "64px",
                fontSize: "24px",
                fontWeight: "600",
              }}
            >
              {user.name
                ? user.name
                    .charAt(0)
                    .toUpperCase()
                : "U"}
            </div>

            <div>
              <h4 className="mb-1">
                {user.name || "User"}
              </h4>

              <p className="text-muted mb-0">
                {user.role ||
                  "Unassigned"}
              </p>
            </div>
          </div>

          <hr />

          {/* ==================================================
              ACCOUNT INFORMATION
          ================================================== */}

          <div className="mt-4">
            <h5 className="mb-3">
              Account Information
            </h5>

            <div className="row g-3">

              <div className="col-md-6">
                <div className="border rounded p-3 h-100">
                  <small className="text-muted d-block mb-1">
                    Employee ID
                  </small>

                  <div className="fw-semibold">
                    {user.employee_id ||
                      "Not available"}
                  </div>
                </div>
              </div>

              <div className="col-md-6">
                <div className="border rounded p-3 h-100">
                  <small className="text-muted d-block mb-1">
                    Full Name
                  </small>

                  <div className="fw-semibold">
                    {user.name ||
                      "Not available"}
                  </div>
                </div>
              </div>

              <div className="col-md-6">
                <div className="border rounded p-3 h-100">
                  <small className="text-muted d-block mb-1">
                    Work Email
                  </small>

                  <div className="fw-semibold">
                    {user.work_email ||
                      "Not available"}
                  </div>
                </div>
              </div>

              <div className="col-md-6">
                <div className="border rounded p-3 h-100">
                  <small className="text-muted d-block mb-1">
                    Mobile Number
                  </small>

                  <div className="fw-semibold">
                    {[user.country_code, user.mobile]
                      .filter(Boolean)
                      .join(" ") || "Not available"}
                  </div>
                </div>
              </div>

              <div className="col-md-6">
                <div className="border rounded p-3 h-100">
                  <small className="text-muted d-block mb-1">
                    Mobile Country
                  </small>

                  <div className="fw-semibold">
                    {user.country || user.country_iso || "Not available"}
                  </div>
                </div>
              </div>

              <div className="col-md-6">
                <div className="border rounded p-3 h-100">
                  <small className="text-muted d-block mb-1">
                    Calling Code
                  </small>

                  <div className="fw-semibold">
                    {user.country_code || "Not available"}
                  </div>
                </div>
              </div>

              <div className="col-md-6">
                <div className="border rounded p-3 h-100">
                  <small className="text-muted d-block mb-1">
                    Country ISO Code
                  </small>

                  <div className="fw-semibold">
                    {user.country_iso || "Not available"}
                  </div>
                </div>
              </div>

              <div className="col-md-6">
                <div className="border rounded p-3 h-100">
                  <small className="text-muted d-block mb-1">
                    Role
                  </small>

                  <div className="fw-semibold">
                    {user.role ||
                      "Unassigned"}
                  </div>
                </div>
              </div>

              <div className="col-md-6">
                <div className="border rounded p-3 h-100">
                  <small className="text-muted d-block mb-1">
                    Account Status
                  </small>

                  <div>
                    {user.is_active ? (
                      <span className="badge bg-success">
                        Active
                      </span>
                    ) : (
                      <span className="badge bg-secondary">
                        Inactive
                      </span>
                    )}
                  </div>
                </div>
              </div>

              <div className="col-md-6">
                <div className="border rounded p-3 h-100">
                  <small className="text-muted d-block mb-1">
                    Last Login
                  </small>

                  <div className="fw-semibold">
                    {formattedLastLogin}
                  </div>
                </div>
              </div>

              <div className="col-md-6">
                <div className="border rounded p-3 h-100">
                  <small className="text-muted d-block mb-1">
                    Active Login Sessions
                  </small>

                  <div className="fw-semibold">
                    {user.active_login_count ?? 0}
                  </div>
                </div>
              </div>

            </div>
          </div>

          {/* ==================================================
              FULL EMPLOYEE INFORMATION
          ================================================== */}

          <div className="d-flex justify-content-end mt-4">
            <button
              type="button"
              className="btn btn-outline-primary"
              onClick={() =>
                onNavigate?.(
                  "employee-details",user?.id || user?._id
                )
              }
            >
              <i className="bi bi-person-vcard me-2"></i>
              Show Full Information
            </button>
          </div>

          {/* ======================================================
              ATTENDANCE
          ====================================================== */}

          <div className="mt-5">
            <hr className="mb-4" />

            {/* ==================================================
                ATTENDANCE HEADER
            ================================================== */}

            <div className="d-flex justify-content-between align-items-center mb-3">
              <div>
                <h5 className="mb-1">
                  Attendance
                </h5>

                <p className="text-muted mb-0">
                  View your monthly attendance
                </p>
              </div>

              <div className="d-flex gap-2">

                {/* MONTH */}

                <select
                  className="form-select"
                  value={selectedMonth}
                  onChange={(event) => {
                    setSelectedMonth(
                      Number(
                        event.target.value,
                      ),
                    );
                  }}
                  style={{
                    minWidth: "150px",
                  }}
                >
                  {monthNames.map(
                    (
                      month,
                      index,
                    ) => {
                      const monthNumber =
                        index + 1;

                      return (
                        <option
                          key={
                            monthNumber
                          }
                          value={
                            monthNumber
                          }
                          disabled={
                            monthNumber >
                            currentMonth
                          }
                        >
                          {month}
                        </option>
                      );
                    },
                  )}
                </select>

                {/* YEAR */}

                <select
                  className="form-select"
                  value={selectedYear}
                  disabled
                  style={{
                    minWidth: "100px",
                  }}
                >
                  <option
                    value={
                      currentYear
                    }
                  >
                    {currentYear}
                  </option>
                </select>

              </div>
            </div>

            {/* ==================================================
                ATTENDANCE ERROR
            ================================================== */}

            {attendanceError && (
              <div
                className="alert alert-danger"
                role="alert"
              >
                {attendanceError}
              </div>
            )}

            {attendanceLoading ? (
              <div className="text-center py-5">
                <div
                  className="spinner-border text-primary"
                  role="status"
                ></div>

                <p className="text-muted mt-2 mb-0">
                  Loading attendance...
                </p>
              </div>
            ) : (
              <>

                {/* ==================================================
                    LEGEND
                ================================================== */}

                <div className="d-flex flex-wrap gap-3 mb-3">
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
                      key: "notmarked",
                      label: "Not Marked Yet",
                    },
                  ].map((item) => (
                    <div
                      className="d-flex align-items-center gap-2"
                      key={item.key}
                    >
                      <span
                        style={{
                          width: "20px",
                          height: "20px",
                          borderRadius: "4px",

                          backgroundColor:
                            statusColors[
                              item.key
                            ].background,

                          border:
                            "1px solid rgba(0,0,0,0.15)",

                          display:
                            "inline-block",
                        }}
                      ></span>

                      <span className="text-muted">
                        {item.label}
                      </span>
                    </div>
                  ))}
                </div>

                {/* ==================================================
                    PROFESSIONAL CALENDAR
                ================================================== */}

                <div className="border rounded p-3">

                  {/* MONTH TITLE */}

                  <div
                    className="fw-semibold mb-3"
                    style={{
                      fontSize: "16px",
                    }}
                  >
                    {
                      monthNames[
                        selectedMonth - 1
                      ]
                    }{" "}
                    {selectedYear}
                  </div>

                  {/* ==================================================
                      WEEKDAY HEADER
                  ================================================== */}

                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns:
                        "repeat(7, minmax(0, 1fr))",
                      gap: "8px",
                      marginBottom: "8px",
                    }}
                  >
                    {[
                      "Sun",
                      "Mon",
                      "Tue",
                      "Wed",
                      "Thu",
                      "Fri",
                      "Sat",
                    ].map((dayName) => (
                      <div
                        key={dayName}
                        className="text-center text-muted fw-semibold"
                        style={{
                          fontSize: "12px",
                          padding:
                            "4px 0",
                        }}
                      >
                        {dayName}
                      </div>
                    ))}
                  </div>

                  {/* ==================================================
                      CALENDAR ROWS
                  ================================================== */}

                  {calendarRows.map(
                    (
                      row,
                      rowIndex,
                    ) => (
                      <div
                        key={
                          rowIndex
                        }
                        style={{
                          display:
                            "grid",

                          gridTemplateColumns:
                            "repeat(7, minmax(0, 1fr))",

                          gap: "8px",

                          marginBottom:
                            rowIndex ===
                            calendarRows.length -
                              1
                              ? "0"
                              : "8px",
                        }}
                      >
                        {row.map(
                          (
                            day,
                            columnIndex,
                          ) => (
                            <div
                              key={
                                day
                                  ? `day-${day}`
                                  : `empty-${rowIndex}-${columnIndex}`
                              }
                              style={{
                                minWidth:
                                  0,
                              }}
                            >
                              {day
                                ? renderCalendarDay(
                                    day,
                                  )
                                : (
                                  <div
                                    style={{
                                      minHeight:
                                        "64px",
                                      width:
                                        "100%",
                                    }}
                                  />
                                )}
                            </div>
                          ),
                        )}
                      </div>
                    ),
                  )}

                </div>

                {/* ==================================================
                    MONTHLY SUMMARY
                ================================================== */}

                <div className="row g-3 mt-3">

                  {/* PRESENT */}

                  <div className="col-md-4">
                    <div className="border rounded p-3 h-100">
                      <small className="text-muted d-block mb-1">
                        Present
                      </small>

                      <div className="fw-semibold text-success">
                        {presentCount}{" "}
                        day
                        {presentCount ===
                        1
                          ? ""
                          : "s"}{" "}
                        ({presentPercentage}%)
                      </div>
                    </div>
                  </div>

                  {/* ABSENT */}

                  <div className="col-md-4">
                    <div className="border rounded p-3 h-100">
                      <small className="text-muted d-block mb-1">
                        Absent
                      </small>

                      <div className="fw-semibold text-danger">
                        {absentCount}{" "}
                        day
                        {absentCount ===
                        1
                          ? ""
                          : "s"}{" "}
                        ({absentPercentage}%)
                      </div>
                    </div>
                  </div>

                  {/* LEAVE */}

                  <div className="col-md-4">
                    <div className="border rounded p-3 h-100">
                      <small className="text-muted d-block mb-1">
                        Leave
                      </small>

                      <div
                        className="fw-semibold"
                        style={{
                          color:
                            statusColors
                              .leave
                              .background,
                        }}
                      >
                        {leaveCount}{" "}
                        day
                        {leaveCount ===
                        1
                          ? ""
                          : "s"}{" "}
                        ({leavePercentage}%)
                      </div>
                    </div>
                  </div>

                </div>

                {/* ==================================================
                    SELECTED DATE DETAILS
                ================================================== */}

                {selectedAttendanceDate && (
                  <div className="mt-4">

                    {/* ==================================================
                        APPROVED LEAVE
                    ================================================== */}

                    {selectedRecord?.is_approved_leave && (
                      <div
                        className="card"
                        style={{
                          borderColor:
                            statusColors
                              .leave
                              .background,
                          borderWidth:
                            "1px",
                        }}
                      >
                        <div className="card-body">

                          {/* HEADER */}

                          <div className="d-flex justify-content-between align-items-start mb-3">

                            <div>
                              <h6
                                className="mb-1"
                                style={{
                                  color:
                                    statusColors
                                      .leave
                                      .background,
                                }}
                              >
                                Leave
                              </h6>

                              <small className="text-muted">
                                Approved leave details
                              </small>
                            </div>

                            {/* APPROVED LEAVE BADGE */}

                            <span
                              className="badge bg-success"
                              style={{
                                fontSize:
                                  "12px",
                                padding:
                                  "7px 10px",
                              }}
                            >
                              <i className="bi bi-check-circle me-1"></i>
                              Approved Leave
                            </span>

                          </div>

                          <div className="row g-3">

                            {/* DATE */}

                            <div className="col-md-4">
                              <small className="text-muted d-block mb-1">
                                Date
                              </small>

                              <strong>
                                {formatDateForDisplay(
                                  selectedAttendanceDate,
                                )}
                              </strong>
                            </div>

                            {/* LEAVE TYPE */}

                            <div className="col-md-4">
                              <small className="text-muted d-block mb-1">
                                Leave Type
                              </small>

                              <strong>
                                {selectedRecord.leave_type_name ||
                                  selectedRecord.leave_type_code ||
                                  "Leave"}
                              </strong>

                              {selectedRecord.leave_type_code &&
                                selectedRecord.leave_type_name && (
                                  <div className="small text-muted mt-1">
                                    Code:{" "}
                                    {
                                      selectedRecord.leave_type_code
                                    }
                                  </div>
                                )}
                            </div>

                            {/* LEAVE DURATION */}

                            <div className="col-md-4">
                              <small className="text-muted d-block mb-1">
                                Leave Duration
                              </small>

                              <strong>
                                {getLeaveDayPartLabel(
                                  selectedRecord.leave_day_part ||
                                    selectedRecord.leave_day_part_label,
                                )}
                              </strong>
                            </div>

                          </div>

                          {/* LEAVE PERIOD */}

                          {selectedRecord.leave_from_date &&
                            selectedRecord.leave_to_date && (
                              <div className="mt-3">
                                <small className="text-muted d-block mb-1">
                                  Approved Leave Period
                                </small>

                                <strong>
                                  {formatDateForDisplay(
                                    normalizeDate(
                                      selectedRecord.leave_from_date,
                                    ),
                                  )}{" "}
                                  -{" "}
                                  {formatDateForDisplay(
                                    normalizeDate(
                                      selectedRecord.leave_to_date,
                                    ),
                                  )}
                                </strong>
                              </div>
                            )}

                          <div className="mt-3">
                            <p className="mb-0 text-muted">
                              This date is marked as approved leave.
                            </p>
                          </div>

                        </div>
                      </div>
                    )}

                    {/* ==================================================
                        PRESENT
                    ================================================== */}

                    {!selectedRecord?.is_approved_leave &&
                      selectedRecord?.status?.toLowerCase() ===
                        "present" && (
                        <div className="card border-success">
                          <div className="card-body">
                            <h6 className="text-success mb-3">
                              Present
                            </h6>

                            <div className="row g-3">

                              <div className="col-md-4">
                                <small className="text-muted d-block">
                                  Date
                                </small>

                                <strong>
                                  {formatDateForDisplay(
                                    selectedAttendanceDate,
                                  )}
                                </strong>
                              </div>

                              <div className="col-md-4">
                                <small className="text-muted d-block">
                                  Check-in Time
                                </small>

                                <strong>
                                  {selectedRecord.check_in_time ||
                                    "Not available"}
                                </strong>
                              </div>

                              <div className="col-md-4">
                                <small className="text-muted d-block">
                                  Check-out Time
                                </small>

                                <strong>
                                  {selectedRecord.check_out_time ||
                                    "Not available"}
                                </strong>
                              </div>

                            </div>
                          </div>
                        </div>
                      )}

                    {/* ==================================================
                        ABSENT
                    ================================================== */}

                    {!selectedRecord?.is_approved_leave &&
                      selectedRecord?.status?.toLowerCase() ===
                        "absent" && (
                        <div className="card border-danger">
                          <div className="card-body">

                            <h6 className="text-danger mb-2">
                              Absent
                            </h6>

                            <p className="mb-0">
                              Date:{" "}
                              <strong>
                                {formatDateForDisplay(
                                  selectedAttendanceDate,
                                )}
                              </strong>
                            </p>

                            <p className="text-danger mt-2 mb-0">
                              You are absent on this date.
                            </p>

                          </div>
                        </div>
                      )}

                    {/* ==================================================
                        LEAVE FROM ATTENDANCE
                    ================================================== */}

                    {!selectedRecord?.is_approved_leave &&
                      selectedRecord?.status?.toLowerCase() ===
                        "leave" && (
                        <div
                          className="card"
                          style={{
                            borderColor:
                              statusColors
                                .leave
                                .background,
                          }}
                        >
                          <div className="card-body">

                            <div className="d-flex justify-content-between align-items-start mb-3">

                              <h6
                                className="mb-0"
                                style={{
                                  color:
                                    statusColors
                                      .leave
                                      .background,
                                }}
                              >
                                Leave
                              </h6>

                              <span className="badge bg-success">
                                <i className="bi bi-check-circle me-1"></i>
                                Approved Leave
                              </span>

                            </div>

                            <div className="row g-3">

                              <div className="col-md-4">
                                <small className="text-muted d-block mb-1">
                                  Date
                                </small>

                                <strong>
                                  {formatDateForDisplay(
                                    selectedAttendanceDate,
                                  )}
                                </strong>
                              </div>

                              <div className="col-md-4">
                                <small className="text-muted d-block mb-1">
                                  Leave Type
                                </small>

                                <strong>
                                  {selectedRecord.leave_type_name ||
                                    selectedRecord.leave_type_code ||
                                    "Leave"}
                                </strong>
                              </div>

                              <div className="col-md-4">
                                <small className="text-muted d-block mb-1">
                                  Leave Duration
                                </small>

                                <strong>
                                  {getLeaveDayPartLabel(
                                    selectedRecord.leave_day_part,
                                  )}
                                </strong>
                              </div>

                            </div>

                            <div className="mt-3">
                              <p className="mb-0 text-muted">
                                This date is marked as approved leave.
                              </p>
                            </div>

                          </div>
                        </div>
                      )}

                    {/* ==================================================
                        HOLIDAY
                    ================================================== */}

                    {!selectedRecord?.is_approved_leave &&
                      selectedRecord?.status?.toLowerCase() ===
                        "holiday" && (
                        <div
                          className="card"
                          style={{
                            borderColor:
                              statusColors
                                .holiday
                                .background,
                          }}
                        >
                          <div className="card-body">

                            <h6
                              className="mb-2"
                              style={{
                                color:
                                  "#0b3d4a",
                              }}
                            >
                              Holiday
                            </h6>

                            <p className="mb-0">
                              Date:{" "}
                              <strong>
                                {formatDateForDisplay(
                                  selectedAttendanceDate,
                                )}
                              </strong>
                            </p>

                            <p className="text-muted mt-2 mb-0">
                              This date is marked as a holiday.
                            </p>

                          </div>
                        </div>
                      )}

                    {/* ==================================================
                        NOT MARKED
                    ================================================== */}

                    {!selectedRecord && (
                      <div
                        className="card"
                        style={{
                          borderColor:
                            statusColors
                              .notmarked
                              .background,
                        }}
                      >
                        <div className="card-body">

                          <h6
                            className="mb-2"
                            style={{
                              color:
                                "#7a5a2f",
                            }}
                          >
                            Not Marked Yet
                          </h6>

                          <p className="mb-0">
                            Date:{" "}
                            <strong>
                              {formatDateForDisplay(
                                selectedAttendanceDate,
                              )}
                            </strong>
                          </p>

                          <p className="text-muted mt-2 mb-0">
                            Attendance has not been marked yet for this date.
                          </p>

                        </div>
                      </div>
                    )}

                  </div>
                )}

              </>
            )}
          </div>

          <AttendanceDayBreakdown userId="me" title="Daily working time and breaks" />


          <section className="mt-5" aria-labelledby="organization-information-heading">
            <hr className="mb-4" />
            <h5 id="organization-information-heading" className="mb-3">
              Organization Information
            </h5>
            {organizationLoading ? (
              <p className="text-muted">Loading organization information...</p>
            ) : organizationData?.error ? (
              <div className="alert alert-warning">{organizationData.error}</div>
            ) : (
              <div className="row g-3">
                {[
                  ["Company Name", organizationData?.company?.company_name],
                  ["Legal Entity Name", organizationData?.company?.legal_entity_name],
                  ["Company Address", organizationData?.company?.address],
                  ["Department", organizationData?.employee?.department],
                  ["Designation", organizationData?.employee?.designation],
                  ["Reporting Manager", organizationData?.employee?.reporting_manager],
                  ["Reporting Manager Id", organizationData?.employee?.reporting_manager_id],
                  ["Work Location", organizationData?.company?.work_location],
                  ["Employment Type", organizationData?.employee?.employment_type],
                  ["Employment Status", organizationData?.employee?.employment_status],
                  ["Employee ID", organizationData?.employee?.employee_id],
                ].filter(([, value]) => value).map(([label, value]) => (
                  <div className="col-sm-6 col-lg-4" key={label}>
                    <small className="text-muted d-block">{label}</small>
                    <strong>{value}</strong>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="mt-5" aria-labelledby="employee-holidays-heading">
            <hr className="mb-4" />
            <div className="d-flex flex-wrap justify-content-between align-items-start gap-3 mb-3">
              <div>
                <h5 id="employee-holidays-heading" className="mb-1">Holiday Calendar</h5>
                <p className="text-muted mb-0">
                  Applicable calendar: {organizationData?.holiday_calendar?.year || currentYear}
                  {organizationData?.holiday_calendar?.calendar?.state
                    ? ` · ${organizationData.holiday_calendar.calendar.state}, ${organizationData.holiday_calendar.calendar.country}`
                    : ""}
                </p>
              </div>
              <button
                type="button"
                className="btn btn-sm btn-outline-primary"
                onClick={() => onNavigate?.("holiday-calendar")}
              >
                View yearly calendar
              </button>
            </div>
            {organizationData?.holiday_calendar?.holidays?.length ? (
              <>
                <div className="list-group mb-4">
                  {organizationData.holiday_calendar.holidays
                    .filter((holiday) => holiday.holiday_date >= todayDateKey)
                    .slice(0, 8)
                    .map((holiday) => (
                      <div
                        className="list-group-item d-flex flex-wrap justify-content-between gap-2"
                        key={holiday.id}
                      >
                        <strong>{holiday.holiday_name}</strong>
                        <span className="text-muted">
                          {new Date(`${holiday.holiday_date}T00:00:00`).toLocaleDateString("en-IN", {
                            weekday: "long", day: "numeric", month: "long",
                          })} · {holiday.holiday_type}
                        </span>
                      </div>
                    ))}
                </div>
                <div className="row row-cols-1 row-cols-sm-2 row-cols-xl-3 g-3">
                  {Array.from({ length: 12 }, (_, monthIndex) => {
                    const calendarYear = organizationData.holiday_calendar.year || currentYear;
                    const firstWeekday = new Date(calendarYear, monthIndex, 1).getDay();
                    const monthLength = new Date(calendarYear, monthIndex + 1, 0).getDate();
                    const holidaysByDay = new Map(
                      organizationData.holiday_calendar.holidays
                        .filter((holiday) => Number(holiday.holiday_date.slice(5, 7)) === monthIndex + 1)
                        .map((holiday) => [Number(holiday.holiday_date.slice(8, 10)), holiday]),
                    );
                    return (
                      <div className="col" key={monthIndex}>
                        <div className="border rounded p-2 h-100">
                          <h6 className="text-center">{monthNames[monthIndex]} {calendarYear}</h6>
                          <div className="d-grid" style={{ gridTemplateColumns: "repeat(7, 1fr)", gap: 2 }}>
                            {["S", "M", "T", "W", "T", "F", "S"].map((day, index) => (
                              <small className="text-center text-muted" key={`${day}-${index}`}>{day}</small>
                            ))}
                            {Array.from({ length: firstWeekday }, (_, index) => (
                              <span aria-hidden="true" key={`blank-${index}`} />
                            ))}
                            {Array.from({ length: monthLength }, (_, index) => {
                              const day = index + 1;
                              const holiday = holidaysByDay.get(day);
                              const weekend = new Date(calendarYear, monthIndex, day).getDay() === 0;
                              const isToday =
                                calendarYear === currentYear &&
                                monthIndex + 1 === currentMonth &&
                                day === currentDate.getDate();
                              return (
                                <span
                                  className={`text-center rounded ${holiday?.is_company_holiday ? "bg-success text-white" : holiday ? "bg-info-subtle" : weekend ? "text-danger" : ""} ${isToday ? "border border-primary border-2" : ""}`}
                                  key={day}
                                  title={holiday?.holiday_name || ""}
                                >
                                  {day}
                                </span>
                              );
                            })}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </>
            ) : (
              <p className="text-muted">No published eligible holidays are available for this calendar year.</p>
            )}
          </section>

          {/* ======================================================
              USER ID
          ====================================================== */}

          {user.id && (
            <div className="mt-4">
              <small className="text-muted">
                User ID
              </small>

              <div className="text-muted small mt-1">
                {user.id}
              </div>
            </div>
          )}

        </div>
      </div>
    </div>
  );
}

export default Profile;