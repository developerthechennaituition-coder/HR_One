import { useEffect, useState } from "react";
import { HRONE_API_BASE } from "../config/api.js";

// JavaScript Date#getDay() index for each weekday name stored by the backend.
const JS_WEEKDAY = { sunday: 0, monday: 1, tuesday: 2, wednesday: 3, thursday: 4, friday: 5, saturday: 6 };

function getToken() {
  try {
    const session = JSON.parse(sessionStorage.getItem("hrms_session") || "null");
    return session?.token || session?.access_token || "";
  } catch {
    return "";
  }
}

/** Weekly-off weekday names configured by HR Admin (empty until configured). */
export function useCompanyWeeklyOffs() {
  const [days, setDays] = useState([]);
  useEffect(() => {
    const token = getToken();
    if (!token) return undefined;
    let active = true;
    fetch(`${HRONE_API_BASE}/api/leave/company-policy`, { headers: { Authorization: `Bearer ${token}` } })
      .then((response) => (response.ok ? response.json() : null))
      .then((policy) => {
        if (active && Array.isArray(policy?.weekly_off_weekdays)) setDays(policy.weekly_off_weekdays);
      })
      .catch(() => {});
    return () => { active = false; };
  }, []);
  return days;
}

export const isWeeklyOff = (days, date) => days.some((day) => JS_WEEKDAY[day] === date.getDay());

export const weeklyOffLabel = (days) =>
  days.length ? days.map((day) => day[0].toUpperCase() + day.slice(1)).join(", ") : "Weekly off (not configured)";
