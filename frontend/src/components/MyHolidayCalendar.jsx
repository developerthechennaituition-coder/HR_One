import { HRONE_API_BASE } from "../config/api.js";
import { useCallback, useEffect, useState } from "react";
import { authenticatedFetch as fetch } from "../services/authenticatedFetch";
import { isWeeklyOff } from "../services/weeklyOff";

const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

const parts = (iso) => {
  const value = new Date(`${iso}T00:00:00Z`);
  return {
    day: value.getUTCDate(),
    weekday: value.toLocaleDateString("en-IN", { weekday: "short", timeZone: "UTC" }),
    month: value.toLocaleDateString("en-IN", { month: "short", timeZone: "UTC" }),
    long: value.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }),
  };
};

/**
 * The signed-in employee's holidays: the published calendar for their own
 * religion and branch (resolved by the backend) plus the company-wide calendar.
 */
function MyHolidayCalendar({ companyCalendar, weeklyOffDays = [], todayKey, canManage = false, onManage }) {
  const [year, setYear] = useState(null);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [view, setView] = useState("list");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${HRONE_API_BASE}/api/my-holidays${year ? `?year=${year}` : ""}`);
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.detail || "Unable to load your holiday calendar.");
      setData(body);
      if (!year) setYear(body.year);
    } catch (err) {
      setError(err.message || "Unable to load your holiday calendar.");
    } finally {
      setLoading(false);
    }
  }, [year]);

  useEffect(() => { load(); }, [load]);

  // Pick up published changes (or deletions) when the employee returns to the tab.
  useEffect(() => {
    const refresh = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", refresh);
    return () => document.removeEventListener("visibilitychange", refresh);
  }, [load]);

  const own = data?.holidays || [];
  const companyHolidays = companyCalendar?.year === year ? companyCalendar?.holidays || [] : [];
  // One entry per date: the religion calendar wins over the company calendar.
  const holidays = [
    ...own.map((item) => ({ ...item, source: `${data.religion} calendar` })),
    ...companyHolidays
      .filter((item) => !own.some((mine) => mine.holiday_date === String(item.holiday_date).slice(0, 10)))
      .map((item) => ({ ...item, holiday_date: String(item.holiday_date).slice(0, 10), source: "Company calendar" })),
  ].sort((left, right) => left.holiday_date.localeCompare(right.holiday_date));
  const byDate = new Map(holidays.map((item) => [item.holiday_date, item]));
  const upcoming = holidays.filter((item) => item.holiday_date >= todayKey);
  const nextHoliday = upcoming[0];
  const months = MONTH_NAMES.map((name, index) => ({
    name,
    items: holidays.filter((item) => Number(item.holiday_date.slice(5, 7)) === index + 1),
  })).filter((month) => month.items.length);
  const assignments = data?.calendar?.assignments || [];

  const emptyMessage = !data?.religion
    ? "Your religion is not recorded in your employee profile, so no religion holiday calendar applies yet. Please contact HR Admin."
    : !data?.calendar
      ? `No ${data.religion} holiday calendar has been published for your branch for ${year} yet.`
      : `The ${data.religion} holiday calendar for ${year} has no holidays.`;

  return (
    <section className="my-holiday-calendar mt-5" aria-labelledby="employee-holidays-heading">
      <hr className="mb-4" />
      <div className="my-holiday-header">
        <div className="min-w-0">
          <p className="my-holiday-eyebrow mb-1">Holiday Calendar</p>
          <h5 id="employee-holidays-heading" className="mb-1">
            {data?.religion ? `${data.religion} holidays · ${year || ""}` : `Holidays · ${year || ""}`}
          </h5>
          <p className="text-muted small mb-0">
            {data?.calendar?.calendar_name || (data?.religion ? "Religion holiday calendar" : "Religion not recorded")}
          </p>
          {assignments.length > 0 && (
            <ul className="my-holiday-orgs" aria-label="Calendar applies to">
              {assignments.map((item, index) => (
                <li key={`${item.branch}-${index}`} title={`${item.legal_entity || item.company} › ${item.branch} › ${item.location}`}>
                  <i className="bi bi-building" aria-hidden="true" /> {item.legal_entity || item.company} › {item.branch}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="my-holiday-controls">
          <div className="btn-group btn-group-sm" role="group" aria-label="Holiday calendar year">
            {(data?.allowed_years || []).map((item) => (
              <button key={item} type="button" className={`btn ${item === year ? "btn-primary" : "btn-outline-primary"}`}
                onClick={() => setYear(item)} disabled={loading} aria-pressed={item === year}>{item}</button>
            ))}
          </div>
          <div className="btn-group btn-group-sm" role="group" aria-label="Holiday view">
            <button type="button" className={`btn ${view === "list" ? "btn-secondary" : "btn-outline-secondary"}`} onClick={() => setView("list")} aria-pressed={view === "list"}>
              <i className="bi bi-list-ul me-1" aria-hidden="true" />List
            </button>
            <button type="button" className={`btn ${view === "calendar" ? "btn-secondary" : "btn-outline-secondary"}`} onClick={() => setView("calendar")} aria-pressed={view === "calendar"}>
              <i className="bi bi-calendar3 me-1" aria-hidden="true" />Calendar
            </button>
          </div>
          {canManage && (
            <button type="button" className="btn btn-sm btn-outline-primary" onClick={onManage}>Manage holiday calendars</button>
          )}
        </div>
      </div>

      {holidays.length > 0 && (
        <div className="my-holiday-stats">
          <div><span>Total holidays</span><strong>{holidays.length}</strong></div>
          <div><span>Upcoming</span><strong>{upcoming.length}</strong></div>
          <div className="my-holiday-next">
            <span>Next holiday</span>
            <strong>{nextHoliday ? nextHoliday.holiday_name : "—"}</strong>
            {nextHoliday && <small>{parts(nextHoliday.holiday_date).long}</small>}
          </div>
        </div>
      )}

      {error && (
        <div className="alert alert-danger d-flex flex-wrap align-items-center justify-content-between gap-2 py-2" role="alert">
          <span>{error}</span>
          <button type="button" className="btn btn-sm btn-outline-danger" onClick={load}>Retry</button>
        </div>
      )}
      {loading && !data ? (
        <div className="text-muted small py-3"><span className="spinner-border spinner-border-sm me-2" role="status" />Loading holidays…</div>
      ) : !holidays.length ? (
        !error && <div className="my-holiday-empty"><i className="bi bi-calendar2-x" aria-hidden="true" /><span>{emptyMessage}</span></div>
      ) : view === "list" ? (
        <div className="my-holiday-list" tabIndex={0} aria-label="Holiday list">
          {months.map((month) => (
            <div key={month.name} className="my-holiday-month">
              <h6>{month.name} {year}</h6>
              <ul>
                {month.items.map((holiday) => {
                  const date = parts(holiday.holiday_date);
                  const state = holiday.holiday_date === todayKey ? "Today" : holiday.holiday_date > todayKey ? "Upcoming" : "Past";
                  return (
                    <li key={`${holiday.source}-${holiday.id || holiday.holiday_date}`} className={state === "Past" ? "is-past" : ""}>
                      <span className="my-holiday-date" aria-hidden="true">
                        <small>{date.month}</small>
                        <strong>{date.day}</strong>
                        <small>{date.weekday}</small>
                      </span>
                      <span className="my-holiday-copy">
                        <strong>{holiday.holiday_name}</strong>
                        <small>{date.long}</small>
                        <small>{holiday.source}{holiday.holiday_type ? ` · ${holiday.holiday_type}` : ""}{holiday.is_optional ? " · Optional" : ""}</small>
                        {holiday.description && <small className="text-muted">{holiday.description}</small>}
                      </span>
                      <span className={`my-holiday-state my-holiday-state-${state.toLowerCase()}`}>{state}</span>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      ) : (
        <>
          <div className="d-flex flex-wrap gap-3 small mb-2">
            <span><i className="bi bi-square-fill text-info me-1" />Holiday</span>
            <span><i className="bi bi-circle-fill text-danger me-1" />Weekly off</span>
            <span><i className="bi bi-square border border-primary border-2 me-1" />Today</span>
          </div>
          <div className="row row-cols-1 row-cols-sm-2 row-cols-xl-3 g-3">
            {MONTH_NAMES.map((monthName, monthIndex) => {
              const firstWeekday = new Date(year, monthIndex, 1).getDay();
              const monthLength = new Date(year, monthIndex + 1, 0).getDate();
              return (
                <div className="col" key={monthName}>
                  <div className="border rounded p-2 h-100">
                    <h6 className="text-center">{monthName} {year}</h6>
                    <div className="d-grid" style={{ gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 2 }}>
                      {["S", "M", "T", "W", "T", "F", "S"].map((day, index) => <small className="text-center text-muted" key={`${day}-${index}`}>{day}</small>)}
                      {Array.from({ length: firstWeekday }, (_, index) => <span aria-hidden="true" key={`blank-${index}`} />)}
                      {Array.from({ length: monthLength }, (_, index) => {
                        const day = index + 1;
                        const key = `${year}-${String(monthIndex + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
                        const holiday = byDate.get(key);
                        const weekend = isWeeklyOff(weeklyOffDays, new Date(year, monthIndex, day));
                        return (
                          <span key={day} title={holiday?.holiday_name || ""}
                            aria-label={holiday ? `${key}: ${holiday.holiday_name}` : undefined}
                            className={`text-center rounded ${holiday ? "bg-info-subtle fw-semibold" : weekend ? "text-danger" : ""} ${key === todayKey ? "border border-primary border-2" : ""}`}>
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
      )}
    </section>
  );
}

export default MyHolidayCalendar;
