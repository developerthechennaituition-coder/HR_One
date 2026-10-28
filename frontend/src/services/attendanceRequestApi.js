import { HRONE_API_BASE } from "../config/api.js";

const API_URL = `${HRONE_API_BASE}/api/attendance-requests`;

function getToken() {
  try {
    const session = JSON.parse(sessionStorage.getItem("hrms_session") || "null");
    return session?.token || session?.access_token || "";
  } catch {
    return "";
  }
}

function errorMessage(detail, fallback) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => item?.msg).filter(Boolean).join(" ") || fallback;
  if (detail && typeof detail === "object") {
    const dates = Array.isArray(detail.dates)
      ? detail.dates.map((item) => `${item.attendance_date}: ${item.message}`).join(" ")
      : "";
    return [detail.message, dates].filter(Boolean).join(" ") || fallback;
  }
  return fallback;
}

async function request(path = "", options = {}, fallback = "Unable to complete the attendance request.") {
  const token = getToken();
  if (!token) throw new Error("Your session has expired. Please login again.");
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...options.headers },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) throw new Error("Your session has expired. Please login again.");
    throw new Error(errorMessage(data?.detail, fallback));
  }
  return data;
}

export const getAttendanceCalendar = (year, month) =>
  request(`/calendar?year=${year}&month=${month}`, {}, "Unable to load the attendance calendar.");

export const createAttendanceRequest = (dates, remarks) =>
  request("", { method: "POST", body: JSON.stringify({ dates, remarks: remarks || null }) }, "Unable to submit the attendance request.");

export const getMyAttendanceRequests = () => request("/my", {}, "Unable to load your attendance requests.");

export const cancelAttendanceRequest = (requestId) =>
  request(`/${encodeURIComponent(requestId)}/cancel`, { method: "POST" }, "Unable to cancel the attendance request.");

export const listAttendanceRequests = (filters = {}) => {
  const params = new URLSearchParams(
    Object.entries(filters).filter(([, value]) => value && value !== "all"),
  );
  const query = params.toString();
  return request(query ? `?${query}` : "", {}, "Unable to load attendance requests.");
};

export const getAttendanceRequest = (requestId) =>
  request(`/${encodeURIComponent(requestId)}`, {}, "Unable to load the attendance request.");

export const respondToAttendanceRequest = (requestId, decisions, reviewComments) =>
  request(
    `/${encodeURIComponent(requestId)}/respond`,
    { method: "POST", body: JSON.stringify({ decisions, review_comments: reviewComments || null }) },
    "Unable to submit the response.",
  );

export const REQUEST_STATUS_LABELS = {
  pending: "Pending",
  processing: "Processing",
  approved: "Approved",
  partially_approved: "Partially Approved",
  rejected: "Rejected",
  cancelled: "Cancelled",
};

export const REQUEST_STATUS_BADGES = {
  pending: "text-bg-warning",
  processing: "text-bg-info",
  approved: "text-bg-success",
  partially_approved: "text-bg-primary",
  rejected: "text-bg-danger",
  cancelled: "text-bg-secondary",
};

export const OUTCOME_LABELS = {
  approved: "Approved as",
  changed: "Approved with changes as",
  rejected: "Rejected",
  not_applied: "Not applied",
};

/** Check-in/check-out pair as text, without inventing missing values. */
export const formatTimeRange = (checkIn, checkOut) =>
  checkIn || checkOut ? `${checkIn || "no check-in"} – ${checkOut || "no check-out"}` : "";

// Date-only values are built in UTC so the weekday never shifts with the browser timezone.
export const formatRequestDate = (iso) => {
  if (!iso) return "—";
  const [year, month, day] = String(iso).slice(0, 10).split("-").map(Number);
  const value = new Date(Date.UTC(year, month - 1, day));
  const weekday = value.toLocaleDateString("en-IN", { weekday: "short", timeZone: "UTC" });
  const text = value.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
  return `${weekday}, ${text}`;
};

export const formatRequestDateTime = (value) => {
  if (!value) return "—";
  // The API returns naive UTC timestamps; mark them as UTC before formatting.
  const text = String(value);
  const date = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(text) ? text : `${text}Z`);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" });
};
