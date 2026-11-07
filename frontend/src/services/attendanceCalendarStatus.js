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
  if (key === "leave") return day?.leave_type_code || "Leave";
  if (key === "holiday") return day?.holiday_name || "Holiday";
  return {
    wfh: "Work From Home",
    present: "Present",
    absent: "Absent",
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

export const attendanceCalendarPortions = (day) => {
  const portions = { present: 0, absent: 0, leave: 0 };
  const classify = (value) => {
    const status = normalize(value);
    if (status === "present") return "present";
    if (status === "absent") return "absent";
    if (status.includes("leave")) return "leave";
    return null;
  };
  const first = classify(day?.first_half_status);
  const second = classify(day?.second_half_status);
  const isHalfDay = attendanceCalendarStatusKey(day) === "halfday";

  if (first || second || isHalfDay) {
    if (first) portions[first] += 0.5;
    if (second) portions[second] += 0.5;
    if (isHalfDay && (!first || !second)) {
      const side = String(day?.half_day_present_side || "");
      const flaggedPresentSide = side === "first" || side === "second"
        ? side
        : String(day?.half_day_present || "").endsWith("First Half")
          ? "first"
          : String(day?.half_day_present || "").endsWith("Second Half")
            ? "second"
            : !first && !second
              ? "first"
              : first === "present"
                ? "first"
                : second === "present"
                  ? "second"
                  : first === "absent"
                    ? "second"
                    : second === "absent"
                      ? "first"
                      : "";
      if (!first) portions[flaggedPresentSide === "first" ? "present" : "absent"] += 0.5;
      if (!second) portions[flaggedPresentSide === "second" ? "present" : "absent"] += 0.5;
    }
    return portions;
  }

  const status = normalize(day?.status || day?.attendance_status);
  if (status === "present") portions.present = 1;
  else if (status === "absent") portions.absent = 1;
  else if (status.includes("leave") || day?.is_approved_leave) portions.leave = 1;
  return portions;
};
