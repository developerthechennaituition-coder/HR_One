const normalize = (value) => String(value || "")
  .trim()
  .toLowerCase()
  .replace(/[_-]+/g, " ")
  .replace(/\s+/g, " ");

const isPresentHalf = (value) => normalize(value) === "present";

export const attendanceCalendarStatusKey = (day) => {
  if (!day) return "notmarked";
  const status = normalize(day?.status || day?.attendance_status);
  const first = normalize(day?.first_half_status);
  const second = normalize(day?.second_half_status);

  // Check-in without check-out: never Present, never Absent.
  if (status === "attendance issue" || day?.is_attendance_issue) return "issue";
  if (
    String(day?.half_day_present || "No").startsWith("Yes") ||
    status === "half day present" ||
    status === "halfday present" ||
    (first && second && first !== second && (isPresentHalf(first) || isPresentHalf(second)))
  ) {
    return "halfday";
  }
  if (
    day?.medical_leave ||
    status === "medical leave" ||
    normalize(day?.status_label) === "medical leave" ||
    normalize(day?.leave_type_code) === "ml"
  ) {
    return "medicalleave";
  }
  if (status === "present") {
    return normalize(day?.attendance_category) === "work from home" ? "wfh" : "present";
  }
  if (status === "absent") return "absent";
  if (status === "leave" || day?.is_approved_leave) return "leave";
  if (status === "holiday") return "holiday";
  if (status === "weekly off" || status === "weeklyoff") return "weeklyoff";
  if (status === "not applicable" || status === "notapplicable") return "notapplicable";
  if (status === "not marked") return "notmarked";
  return "other";
};

export const attendanceCalendarLabel = (day) => {
  if (!day) return "";
  const key = attendanceCalendarStatusKey(day);
  const first = day?.first_half_status;
  const second = day?.second_half_status;

  if (first && second && normalize(first) !== normalize(second)) {
    return `1st: ${first} · 2nd: ${second}`;
  }
  if (key === "medicalleave") return "ML";
  if (key === "halfday") {
    const side = String(day?.half_day_present || "");
    if (side.endsWith("First Half") || isPresentHalf(first)) return "First Half Present";
    if (side.endsWith("Second Half") || isPresentHalf(second)) return "Second Half Present";
    return "Half-Day Present";
  }
  if (key === "issue") return "Attendance Issue";
  if (key === "leave") return day?.leave_type_code || "Leave";
  if (key === "holiday") return day?.holiday_name || "Holiday";
  return {
    wfh: "Work From Home",
    present: "Present",
    absent: "Absent",
    issue: "Attendance Issue",
    weeklyoff: "Weekly Off",
    notapplicable: "Not Applicable",
    notmarked: "",
    other: day?.status_label || day?.status || "Upcoming",
  }[key];
};

export const attendanceCalendarHalfSymbol = (day) => {
  const side = String(day?.half_day_present || "");
  const first = normalize(day?.first_half_status);
  const second = normalize(day?.second_half_status);
  if (side.endsWith("First Half") || (isPresentHalf(first) && !isPresentHalf(second))) return "◐";
  if (side.endsWith("Second Half") || (isPresentHalf(second) && !isPresentHalf(first))) return "◑";
  return "◒";
};

// Splits one calendar day into Present / Absent / Leave / Holiday portions.
// A full day contributes 1 to one bucket; a half-day contributes 0.5 per half
// according to that half's own status (e.g. Present + Absent => 0.5 + 0.5).
export const attendanceCalendarPortions = (day) => {
  const portions = { present: 0, absent: 0, leave: 0, holiday: 0, issue: 0 };
  const classify = (value) => {
    const status = normalize(value);
    if (status === "attendance issue") return "issue";
    if (status === "present") return "present";
    if (status === "absent") return "absent";
    if (status.includes("leave")) return "leave";
    if (status === "holiday") return "holiday";
    if (status === "weekly off" || status === "weeklyoff") return "ignore";
    return null;
  };
  const add = (key, value) => { if (key && key !== "ignore") portions[key] += value; };
  const first = classify(day?.first_half_status);
  const second = classify(day?.second_half_status);
  const isHalfDay = attendanceCalendarStatusKey(day) === "halfday";

  if (isHalfDay || (first && second && first !== second)) {
    add(first, 0.5);
    add(second, 0.5);
    if (isHalfDay && (!first || !second)) {
      // The unrecorded half of a Half-Day Present is counted as Absent.
      const side = String(day?.half_day_present_side || "");
      const flaggedPresentSide = side === "first" || side === "second"
        ? side
        : String(day?.half_day_present || "").endsWith("First Half")
          ? "first"
          : String(day?.half_day_present || "").endsWith("Second Half")
            ? "second"
            : first === "present"
              ? "first"
              : second === "present"
                ? "second"
                : first === "absent"
                  ? "second"
                  : second === "absent"
                    ? "first"
                    : "first";
      if (!first) portions[flaggedPresentSide === "first" ? "present" : "absent"] += 0.5;
      if (!second) portions[flaggedPresentSide === "second" ? "present" : "absent"] += 0.5;
    }
    return portions;
  }

  const status = normalize(day?.status || day?.attendance_status);
  if (status === "attendance issue" || day?.is_attendance_issue) portions.issue = 1;
  else if (status === "present") portions.present = 1;
  else if (status === "absent") portions.absent = 1;
  else if (status.includes("leave") || day?.is_approved_leave) portions.leave = 1;
  else if (status === "holiday") portions.holiday = 1;
  return portions;
};

// One shared monthly summary for every attendance calendar page, so the counts
// and percentages always agree. Present / Absent / Leave carry a percentage
// (out of Present + Absent + Leave); Holiday and Attendance Issue are counts only.
// Attendance Issue dates are never part of Present or Absent.
// `days` are calendar day objects; `resolve` may return an adjusted day
// (e.g. approved leave or a published holiday for a date with no record).
export const summarizeAttendancePortions = (days, resolve = (day) => day) => {
  const totals = { present: 0, absent: 0, leave: 0, holiday: 0, issue: 0 };
  days.forEach((day) => {
    const portions = attendanceCalendarPortions(resolve(day));
    Object.keys(totals).forEach((key) => { totals[key] += portions[key]; });
  });
  const counted = totals.present + totals.absent + totals.leave;
  const percentages = {};
  ["present", "absent", "leave"].forEach((key) => {
    percentages[key] = counted > 0 ? ((totals[key] / counted) * 100).toFixed(1) : "0.0";
  });
  return { counts: totals, counted, percentages };
};

// Summary for backend calendar payloads (`calendar.days`): recorded days plus
// published holidays and approved leave inside the visible window.
export const summarizeCalendarDays = (calendar) => summarizeAttendancePortions(
  (calendar?.days || []).filter((day) => {
    if (day?.is_before_joining || day?.is_after_window) return false;
    const status = normalize(day?.status);
    return Boolean(day?.has_record) || status === "holiday" || status === "leave" || status === "attendance issue";
  }),
);

// Friendly Source text shared by the attendance detail panels.
const SOURCE_LABELS = {
  ESSL: "ESSL",
  MANUAL: "Manual",
  ATTENDANCE_REQUEST: "Attendance Request (approved by HR Admin)",
  WFH_FACE_GPS: "WFH (Face + GPS)",
  WFH_AUTO_ABSENT: "WFH Auto Absent",
};
export const attendanceSourceLabel = (source) => SOURCE_LABELS[source] || source || "—";

// 2 -> "2", 2.5 -> "2.5"
export const formatAttendanceCount = (value) => (
  Number.isInteger(value) ? String(value) : value.toFixed(1)
);
