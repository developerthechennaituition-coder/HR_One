import { useEffect, useState } from "react";
import { dateState, monthOptions, clampMonth, isMonthInRange, shiftMonth as shiftCalendarMonth, yearOptions } from "../services/attendanceCalendarRange";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const CALENDAR_LEGEND = [
  { key: "present", label: "Present", background: "#d1e7dd", text: "#0f5132" },
  { key: "wfh", label: "Work From Home", background: "#cff4fc", text: "#055160" },
  { key: "absent", label: "Absent", background: "#f8d7da", text: "#842029" },
  { key: "holiday", label: "Holiday", background: "#e2d9f3", text: "#432874" },
  { key: "leave", label: "Leave", background: "#ffe5d0", text: "#984c0c" },
  { key: "weeklyoff", label: "Weekly Off", background: "#e9ecef", text: "#495057" },
  { key: "notmarked", label: "Not Marked", background: "#fff3cd", text: "#664d03" },
  { key: "notapplicable", label: "Not Applicable (LOP)", background: "#dee2e6", text: "#842029" },
  { key: "other", label: "Upcoming", background: "#f8f9fa", text: "#6c757d" },
];
const LEGEND_BY_KEY = Object.fromEntries(CALENDAR_LEGEND.map((item) => [item.key, item]));

const dayStyleKey = (day) => {
  const value = String(day?.status || "").toLowerCase();
  if (value === "present") return day.attendance_category === "Work From Home" ? "wfh" : "present";
  if (value === "absent") return "absent";
  if (value === "holiday") return "holiday";
  if (value === "leave") return "leave";
  if (value === "weekly off") return "weeklyoff";
  if (value === "not marked") return "notmarked";
  if (value === "not applicable") return "notapplicable";
  return "other";
};

const DAY_PART_LABELS = { full_day: "Full day", first_half: "First half", second_half: "Second half" };

const formatLongDate = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", {
  weekday: "long", day: "numeric", month: "long", year: "numeric",
});

const formatClock = (time) => {
  if (!time) return "—";
  const [hourText, minute] = String(time).split(":");
  const hour = Number(hourText);
  return `${hour % 12 || 12}:${minute} ${hour >= 12 ? "PM" : "AM"}`;
};

// Read-only details for an upcoming holiday / leave / permission date.
function UpcomingDayDetails({ day, onClose }) {
  const leave = day.upcoming_leave;
  return (
    <div className="border rounded p-3 mt-3 bg-light" role="region" aria-label="Upcoming date details">
      <div className="d-flex justify-content-between align-items-start gap-2 mb-2">
        <div>
          <div className="small text-muted">Upcoming date · view only</div>
          <h6 className="mb-0">{formatLongDate(day.attendance_date)}</h6>
        </div>
        <button type="button" className="btn-close" aria-label="Close upcoming date details" onClick={onClose} />
      </div>
      <div className="row g-3">
        {day.status === "Holiday" && (
          <div className="col-md-4">
            <div className="small text-muted">Holiday</div>
            <div className="fw-semibold">{day.holiday_name || "Holiday"}</div>
          </div>
        )}
        {day.status === "Leave" && (
          <div className="col-md-8">
            <div className="small text-muted">Approved leave</div>
            <div className="fw-semibold">
              {leave?.leave_type_name || day.leave_type_code || "Leave"}
              {leave?.leave_type_name && day.leave_type_code ? ` (${day.leave_type_code})` : ""}
              {` · ${DAY_PART_LABELS[leave?.day_part] || "Full day"}`}
            </div>
            {leave?.from_date && (
              <div className="small text-muted">
                Leave period: {leave.from_date === leave.to_date ? leave.from_date : `${leave.from_date} to ${leave.to_date}`}
              </div>
            )}
          </div>
        )}
        {day.permissions?.length > 0 && (
          <div className="col-12">
            <div className="small text-muted mb-1">Permission</div>
            {day.permissions.map((item, index) => (
              <div key={`${item.start_time}-${index}`} className="d-flex flex-wrap align-items-center gap-2 mb-1">
                <span className="fw-semibold">{formatClock(item.start_time)} – {formatClock(item.end_time)}</span>
                <span className={`badge ${item.status === "approved" ? "text-bg-success" : "text-bg-warning"}`}>
                  {item.status === "approved" ? "Approved" : "Pending"}
                </span>
                {item.reason && <span className="small text-muted">{item.reason}</span>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// `range` comes from buildCalendarRange(): joining date through today + 89 days.
function MyAttendanceCalendar({ year, month, range, calendar, loading, selectedDate, onSelectDate, onMonthChange }) {
  const days = calendar?.days || [];
  // Upcoming holiday / leave / permission date opened for read-only details.
  const [previewDate, setPreviewDate] = useState(null);
  useEffect(() => { setPreviewDate(null); }, [calendar]);
  const previewDay = days.find((day) => day.attendance_date === previewDate) || null;
  const leadingBlanks = days.length ? new Date(Date.UTC(year, month - 1, 1)).getUTCDay() : 0;
  const previous = shiftCalendarMonth(year, month, -1);
  const next = shiftCalendarMonth(year, month, 1);
  const atEarliestMonth = !isMonthInRange(range, previous.year, previous.month);
  const atLatestMonth = !isMonthInRange(range, next.year, next.month);
  const changeMonth = (nextYear, nextMonth) => {
    const target = clampMonth(range, nextYear, nextMonth);
    onMonthChange(target.year, target.month);
  };
  const shiftMonth = (delta) => {
    const target = shiftCalendarMonth(year, month, delta);
    changeMonth(target.year, target.month);
  };

  return (
    <section className="card border-0 shadow-sm mb-4" aria-label="Monthly attendance calendar">
      <div className="card-body">
        <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-3">
          <h4 className="mb-0">Monthly Attendance Calendar</h4>
          <div className="d-flex flex-wrap align-items-center gap-2">
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => shiftMonth(-1)} disabled={loading || atEarliestMonth} aria-label="Previous month">
              <i className="bi bi-chevron-left" aria-hidden="true" />
            </button>
            <select className="form-select form-select-sm w-auto" aria-label="Month" value={month} disabled={loading}
              onChange={(event) => changeMonth(year, Number(event.target.value))}>
              {monthOptions(range, year).map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>
            <select className="form-select form-select-sm w-auto" aria-label="Year" value={year} disabled={loading}
              onChange={(event) => changeMonth(Number(event.target.value), month)}>
              {yearOptions(range).map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => shiftMonth(1)} disabled={loading || atLatestMonth} aria-label="Next month">
              <i className="bi bi-chevron-right" aria-hidden="true" />
            </button>
          </div>
        </div>

        <div className="d-flex flex-wrap gap-3 mb-3 small">
          {CALENDAR_LEGEND.map((item) => (
            <div className="d-flex align-items-center gap-2" key={item.key}>
              <span style={{ width: 16, height: 16, borderRadius: 4, backgroundColor: item.background, border: "1px solid rgba(0,0,0,0.15)", display: "inline-block" }} />
              <span className="text-muted">{item.label}</span>
            </div>
          ))}
          <div className="d-flex align-items-center gap-2">
            <span className="badge text-bg-warning">Req</span><span className="text-muted">Attendance request pending</span>
          </div>
          <div className="d-flex align-items-center gap-2">
            <span className="badge text-bg-info">P</span><span className="text-muted">Permission</span>
          </div>
        </div>
        <p className="small text-muted mb-3">
          Holidays, leave and permissions are shown up to {range.end}. Click an upcoming holiday, leave or permission date to view its details.
        </p>

        {calendar?.payroll_locked && (
          <div className="alert alert-secondary py-2 small">Payroll for this month is {calendar.payroll_status}; attendance requests are closed for this month.</div>
        )}

        {loading && !days.length ? (
          <div className="text-center py-4"><div className="spinner-border text-primary" role="status" /></div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <div style={{ minWidth: 420 }}>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 6, marginBottom: 6 }}>
                {WEEKDAYS.map((name) => <div key={name} className="text-center text-muted fw-semibold small">{name}</div>)}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 6, opacity: loading ? 0.6 : 1 }}>
                {Array.from({ length: leadingBlanks }, (_, index) => <div key={`blank-${index}`} />)}
                {days.map((day) => {
                  const state = dateState(range, day.attendance_date);
                  // Dates before joining are not shown.
                  if (state.hidden) return <div key={day.attendance_date} aria-hidden="true" />;
                  const colors = LEGEND_BY_KEY[dayStyleKey(day)];
                  const leaveLabel = colors.key === "leave" && day.leave_type_code ? `Leave · ${day.leave_type_code}` : null;
                  const label = day.holiday_name || leaveLabel || (day.status_label && day.status_label !== day.status ? day.status_label : colors.key === "other" ? day.status : colors.label);
                  // Upcoming holiday / leave / permission dates (within the 90 days) open details only.
                  const viewable = state.isFuture && !state.afterWindow && Boolean(day.has_upcoming_details);
                  const readOnly = !state.selectable;
                  const clickable = state.selectable || viewable;
                  const selected = day.attendance_date === (viewable ? previewDate : selectedDate);
                  const handleClick = () => {
                    if (state.selectable) {
                      setPreviewDate(null);
                      onSelectDate(day.attendance_date);
                    } else if (viewable) {
                      setPreviewDate((current) => (current === day.attendance_date ? null : day.attendance_date));
                    }
                  };
                  return (
                    <button
                      key={day.attendance_date}
                      type="button"
                      onClick={handleClick}
                      disabled={!clickable}
                      aria-pressed={selected}
                      aria-label={`${day.attendance_date}: ${label}${viewable ? " (upcoming, view details)" : readOnly ? " (upcoming, not selectable)" : ""}`}
                      title={[label, day.check_in_time && `In ${day.check_in_time}`, day.check_out_time && `Out ${day.check_out_time}`, day.request && `Request ${day.request.request_number} ${day.request.status}`, ...(day.permissions || []).map((item) => `Permission ${item.start_time}–${item.end_time} (${item.status})`), viewable ? "Upcoming date · click to view details" : readOnly && "Upcoming date · view only"].filter(Boolean).join(" · ")}
                      style={{
                        minHeight: 64, minWidth: 0, textAlign: "left", padding: "6px 8px", borderRadius: 8,
                        border: selected ? "2px solid #0d6efd" : readOnly ? "1px dashed rgba(0,0,0,0.25)" : "1px solid rgba(0,0,0,0.12)",
                        backgroundColor: colors.background, color: colors.text,
                        opacity: readOnly && !viewable ? 0.8 : 1, cursor: clickable ? "pointer" : "not-allowed",
                      }}
                    >
                      <div className="d-flex justify-content-between align-items-start gap-1">
                        <span className="fw-semibold">{Number(day.attendance_date.slice(8, 10))}</span>
                        <span className="d-flex gap-1">
                          {day.permissions?.length > 0 && <span className="badge text-bg-info" style={{ fontSize: 9 }}>P</span>}
                          {day.request && <span className="badge text-bg-warning" style={{ fontSize: 9 }}>Req</span>}
                        </span>
                      </div>
                      <div style={{ fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</div>
                      {day.permissions?.length > 0 && (
                        <div style={{ fontSize: 10, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          P {day.permissions[0].start_time}–{day.permissions[0].end_time}
                          {day.permissions.length > 1 ? ` +${day.permissions.length - 1}` : ""}
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        )}
        {previewDay && <UpcomingDayDetails day={previewDay} onClose={() => setPreviewDate(null)} />}
      </div>
    </section>
  );
}

export default MyAttendanceCalendar;
