// Shared date rules for the attendance calendars (My Attendance, Profile,
// Team Member Details). The calendar covers the employee's joining date
// through today + 89 days (a 90-day forward window); dates after today are
// shown for holidays/leave but never selectable for attendance actions.
// All dates are ISO "YYYY-MM-DD" strings in company time (Asia/Kolkata).

export const CALENDAR_FORWARD_DAYS = 89;

export const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export function companyToday() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${values.year}-${values.month}-${values.day}`;
}

// UTC arithmetic so month/year boundaries and leap days never drift.
export function addDays(iso, days) {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

// Accepts "YYYY-MM-DD", ISO datetimes, or "DD-MM-YYYY" / "DD/MM/YYYY"; returns ISO or null.
export function normalizeJoiningDate(value) {
  if (!value) return null;
  const text = String(value).trim();
  let iso = text.slice(0, 10);
  const dayFirst = text.match(/^(\d{2})[-/](\d{2})[-/](\d{4})$/);
  if (dayFirst) iso = `${dayFirst[3]}-${dayFirst[2]}-${dayFirst[1]}`;
  if (!ISO_DATE.test(iso)) return null;
  const [year, month, day] = iso.split("-").map(Number);
  const check = new Date(Date.UTC(year, month - 1, day));
  return check.getUTCMonth() === month - 1 && check.getUTCDate() === day ? iso : null;
}

const monthIndex = (year, month) => year * 12 + (month - 1);
const ymOf = (iso) => ({ year: Number(iso.slice(0, 4)), month: Number(iso.slice(5, 7)) });

/*
 * Calendar bounds for one employee. Without a valid joining date there is no
 * lower bound (nothing is invented); `fallbackYearsBack` only sizes the year list.
 */
export function buildCalendarRange(joiningDate, today = companyToday(), fallbackYearsBack = 5) {
  const joining = normalizeJoiningDate(joiningDate);
  const end = addDays(today, CALENDAR_FORWARD_DAYS);
  const max = ymOf(end);
  const min = joining ? ymOf(joining) : { year: Number(today.slice(0, 4)) - fallbackYearsBack, month: 1 };
  // A future-dated joining beyond the window still gets its own month.
  if (monthIndex(min.year, min.month) > monthIndex(max.year, max.month)) Object.assign(max, min);
  return { today, joining, end, minYear: min.year, minMonth: min.month, maxYear: max.year, maxMonth: max.month };
}

export function isMonthInRange(range, year, month) {
  const index = monthIndex(year, month);
  return index >= monthIndex(range.minYear, range.minMonth) && index <= monthIndex(range.maxYear, range.maxMonth);
}

export function clampMonth(range, year, month) {
  const index = Math.min(
    Math.max(monthIndex(year, month), monthIndex(range.minYear, range.minMonth)),
    monthIndex(range.maxYear, range.maxMonth),
  );
  return { year: Math.floor(index / 12), month: (index % 12) + 1 };
}

export function shiftMonth(year, month, delta) {
  const index = monthIndex(year, month) + delta;
  return { year: Math.floor(index / 12), month: (index % 12) + 1 };
}

export function yearOptions(range) {
  return Array.from({ length: range.maxYear - range.minYear + 1 }, (_, index) => range.minYear + index);
}

// Months of `year` inside the range; earlier/later months are not offered.
export function monthOptions(range, year) {
  return MONTH_NAMES
    .map((label, index) => ({ value: index + 1, label }))
    .filter((item) => isMonthInRange(range, year, item.value));
}

/*
 * Per-date rules:
 *   hidden      – before joining: not shown at all.
 *   afterWindow – after the 90th day: shown (the last month is displayed in
 *                 full) but without holiday / leave / permission details.
 *   isFuture    – after today: read-only; holiday, leave and permission
 *                 dates inside the window can be opened for details.
 *   selectable  – on/after joining and not after today (attendance actions).
 */
export function dateState(range, iso) {
  const beforeJoining = Boolean(range.joining && iso < range.joining);
  const afterWindow = iso > range.end;
  const isFuture = iso > range.today;
  return {
    beforeJoining,
    afterWindow,
    hidden: beforeJoining,
    isFuture,
    selectable: !beforeJoining && !afterWindow && !isFuture,
  };
}

export const isSelectableDate = (range, iso) => Boolean(iso) && ISO_DATE.test(iso) && dateState(range, iso).selectable;

// Earliest date the date pickers may offer (joining date, if known).
export const minSelectableDate = (range) => range.joining || undefined;
