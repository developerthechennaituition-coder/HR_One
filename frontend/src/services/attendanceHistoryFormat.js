// Formatting helpers for the attendance Modification History.
//
// Timestamps: the API stores UTC. History timestamps reach the browser as ISO
// strings with an explicit offset and are converted to IST exactly once here.
// Attendance clock times (check-in/out, permission start/end) are stored as
// company-local "HH:MM" wall-clock strings and are only reformatted, never
// timezone-converted.

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const ISO_DATETIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/;
const HAS_OFFSET = /(Z|[+-]\d{2}:?\d{2})$/i;
const CLOCK = /^(\d{1,2}):(\d{2})(?::\d{2})?$/;

// Parse a backend timestamp as an absolute instant (no offset => UTC, never browser-local).
export const parseInstant = (value) => {
  if (value instanceof Date) return value;
  const text = String(value ?? "").trim();
  if (!text) return null;
  const normalised = ISO_DATETIME.test(text) && !HAS_OFFSET.test(text) ? `${text.replace(" ", "T")}Z` : text;
  const date = new Date(normalised);
  return Number.isNaN(date.getTime()) ? null : date;
};

// "31 Oct 2026, 4:44:59 am IST" - always Asia/Kolkata, so the date rolls over
// correctly when UTC -> IST crosses midnight.
export const formatIST = (value) => {
  if (!value) return "—";
  const date = parseInstant(value);
  if (!date) return String(value);
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Kolkata", year: "numeric", month: "numeric", day: "numeric",
    hour: "numeric", minute: "2-digit", second: "2-digit", hourCycle: "h12",
  }).formatToParts(date).map(({ type, value: part }) => [type, part]));
  return `${Number(parts.day)} ${MONTH_NAMES[Number(parts.month) - 1]} ${parts.year}, ${parts.hour}:${parts.minute}:${parts.second} ${String(parts.dayPeriod).toLowerCase()} IST`;
};

const isBlank = (value) => value === null || value === undefined || (typeof value === "string" && value.trim() === "");

// "14:48" -> "02:48 PM"; ISO timestamps -> IST; anything else as plain text.
export const formatClockTime = (value) => {
  const text = String(value).trim();
  const match = CLOCK.exec(text);
  if (match) {
    const hours = Number(match[1]);
    if (hours > 23 || Number(match[2]) > 59) return text;
    return `${String(hours % 12 || 12).padStart(2, "0")}:${match[2]} ${hours >= 12 ? "PM" : "AM"}`;
  }
  if (ISO_DATETIME.test(text)) return formatIST(text);
  return text;
};

const SOURCE_LABELS = { ESSL: "ESSL", MANUAL: "Manual", WFH: "WFH", WFH_FACE_GPS: "WFH (Face & GPS)", BIOMETRIC: "Biometric" };

const humanise = (key) => String(key).replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim()
  .replace(/\b\w/g, (letter) => letter.toUpperCase());

const FIELD_LABELS = {
  status: "Attendance Status",
  check_in_time: "Check-in Time",
  check_out_time: "Check-out Time",
  attendance_source: "Attendance Source",
  attendance_type: "Attendance Type",
  leave_type: "Leave Type",
  permission: "Permission",
};

const NOT_MARKED_FIELDS = new Set(["check_in_time", "check_out_time"]);

// A value of any shape -> readable text (never raw JSON, `null` or `undefined`).
function readable(field, value) {
  if (isBlank(value)) return NOT_MARKED_FIELDS.has(field) ? "Not Marked" : "Not Available";
  if (field === "attendance_source") return SOURCE_LABELS[String(value).toUpperCase()] || humanise(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.map((item) => readable(field, item)).join(", ") || "Not Available";
  if (typeof value === "object") {
    const text = Object.entries(value)
      .filter(([key, item]) => !isBlank(item) && !/(^|_)(id|by)$/i.test(key))
      .map(([key, item]) => `${humanise(key)}: ${readable(key, item)}`)
      .join(", ");
    return text || "Not Available";
  }
  if (NOT_MARKED_FIELDS.has(field) || /(^|_)(time|at)$/.test(field)) return formatClockTime(value);
  return String(value);
}

const PERMISSION_ROWS = [
  ["start_time", "Permission Start Time", (value) => formatClockTime(value)],
  ["end_time", "Permission End Time", (value) => formatClockTime(value)],
  ["reason", "Permission Reason", (value) => String(value)],
];

function permissionRows(pair) {
  const hasBefore = Object.prototype.hasOwnProperty.call(pair, "before");
  const before = pair.before && typeof pair.before === "object" ? pair.before : null;
  const after = pair.after && typeof pair.after === "object" ? pair.after : null;
  const side = (permission, field, format, recorded) => {
    if (!recorded) return "Not Previously Recorded";
    if (!permission || isBlank(permission[field])) return "Not Available";
    return format(permission[field]);
  };
  const rows = PERMISSION_ROWS.map(([field, label, format]) => ({
    label,
    before: side(before, field, format, hasBefore),
    after: side(after, field, format, true),
  }));
  const approver = (permission) => permission?.approved_by_name || "";
  if (approver(before) !== approver(after) && (approver(before) || approver(after))) {
    rows.push({
      label: "Approved By",
      before: approver(before) || (hasBefore ? "Not Available" : "Not Previously Recorded"),
      after: approver(after) || "Not Available",
    });
  }
  return rows;
}

// changes ({field: {before, after}}) -> [{label, before, after}] for display.
export function buildChangeRows(changes) {
  if (!changes || typeof changes !== "object") return [];
  const rows = [];
  Object.entries(changes).forEach(([field, pair]) => {
    if (!pair || typeof pair !== "object" || Array.isArray(pair)) {
      rows.push({ label: FIELD_LABELS[field] || humanise(field), before: "Not Previously Recorded", after: readable(field, pair) });
      return;
    }
    if (field === "permission") { rows.push(...permissionRows(pair)); return; }
    const hasBefore = Object.prototype.hasOwnProperty.call(pair, "before");
    rows.push({
      label: FIELD_LABELS[field] || humanise(field),
      before: hasBefore ? readable(field, pair.before) : "Not Previously Recorded",
      after: readable(field, pair.after),
    });
  });
  return rows;
}
