const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const CALENDAR_LEGEND = [
  { key: "present", label: "Present", background: "#d1e7dd", text: "#0f5132" },
  { key: "wfh", label: "Work From Home", background: "#cff4fc", text: "#055160" },
  { key: "absent", label: "Absent", background: "#f8d7da", text: "#842029" },
  { key: "holiday", label: "Holiday", background: "#e2d9f3", text: "#432874" },
  { key: "leave", label: "Leave", background: "#ffe5d0", text: "#984c0c" },
  { key: "weeklyoff", label: "Weekly Off", background: "#e9ecef", text: "#495057" },
  { key: "notmarked", label: "Not Marked", background: "#fff3cd", text: "#664d03" },
  { key: "other", label: "Upcoming / Not Applicable", background: "#f8f9fa", text: "#6c757d" },
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
  return "other";
};

function MyAttendanceCalendar({ year, month, maxYear, maxMonth, calendar, loading, selectedDate, onSelectDate, onMonthChange }) {
  const days = calendar?.days || [];
  const leadingBlanks = days.length ? new Date(Date.UTC(year, month - 1, 1)).getUTCDay() : 0;
  const atLatestMonth = year > maxYear || (year === maxYear && month >= maxMonth);
  const shiftMonth = (delta) => {
    const index = year * 12 + (month - 1) + delta;
    onMonthChange(Math.floor(index / 12), (index % 12) + 1);
  };
  const years = Array.from({ length: 6 }, (_, index) => maxYear - 5 + index);

  return (
    <section className="card border-0 shadow-sm mb-4" aria-label="Monthly attendance calendar">
      <div className="card-body">
        <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-3">
          <h4 className="mb-0">Monthly Attendance Calendar</h4>
          <div className="d-flex flex-wrap align-items-center gap-2">
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => shiftMonth(-1)} disabled={loading} aria-label="Previous month">
              <i className="bi bi-chevron-left" aria-hidden="true" />
            </button>
            <select className="form-select form-select-sm w-auto" aria-label="Month" value={month} disabled={loading}
              onChange={(event) => onMonthChange(year, Number(event.target.value))}>
              {MONTH_NAMES.map((name, index) => (
                <option key={name} value={index + 1} disabled={year === maxYear && index + 1 > maxMonth}>{name}</option>
              ))}
            </select>
            <select className="form-select form-select-sm w-auto" aria-label="Year" value={year} disabled={loading}
              onChange={(event) => {
                const nextYear = Number(event.target.value);
                onMonthChange(nextYear, nextYear === maxYear ? Math.min(month, maxMonth) : month);
              }}>
              {years.map((item) => <option key={item} value={item}>{item}</option>)}
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
        </div>

        {calendar && !calendar.weekly_off_configured && (
          <div className="alert alert-light border py-2 small">Weekly off days have not been configured by HR Admin, so no day is shown as Weekly Off.</div>
        )}
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
                  const colors = LEGEND_BY_KEY[dayStyleKey(day)];
                  const selected = day.attendance_date === selectedDate;
                  const label = colors.key === "other" ? day.status : colors.label;
                  return (
                    <button
                      key={day.attendance_date}
                      type="button"
                      onClick={() => !day.is_future && onSelectDate(day.attendance_date)}
                      disabled={day.is_future}
                      aria-pressed={selected}
                      aria-label={`${day.attendance_date}: ${label}`}
                      title={[label, day.check_in_time && `In ${day.check_in_time}`, day.check_out_time && `Out ${day.check_out_time}`, day.request && `Request ${day.request.request_number} ${day.request.status}`].filter(Boolean).join(" · ")}
                      style={{
                        minHeight: 64, textAlign: "left", padding: "6px 8px", borderRadius: 8,
                        border: selected ? "2px solid #0d6efd" : "1px solid rgba(0,0,0,0.12)",
                        backgroundColor: colors.background, color: colors.text,
                        opacity: day.is_future ? 0.55 : 1, cursor: day.is_future ? "default" : "pointer",
                      }}
                    >
                      <div className="d-flex justify-content-between align-items-start">
                        <span className="fw-semibold">{Number(day.attendance_date.slice(8, 10))}</span>
                        {day.request && <span className="badge text-bg-warning" style={{ fontSize: 9 }}>Req</span>}
                      </div>
                      <div style={{ fontSize: 11 }}>{day.is_future ? "—" : label}</div>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

export default MyAttendanceCalendar;
