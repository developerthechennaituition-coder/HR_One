import { formatAttendanceCount } from "../services/attendanceCalendarStatus";

// Monthly Present / Absent / Leave (count + %), Holiday and Attendance Issue (count only).
// Uses the same card design and grid as Team Member Details -> Attendance Record
// (.team-attendance-summary-card), so both screens look identical.
const CARDS = [
  { key: "present", label: "Present", className: "present" },
  { key: "absent", label: "Absent", className: "absent" },
  { key: "leave", label: "Leave", className: "leave" },
  { key: "holiday", label: "Holidays", className: "holiday" },
  { key: "issue", label: "Attendance Issue", className: "issue" },
];

function AttendanceStatusSummary({ counts, percentages }) {
  return (
    <div className="row g-3 mt-1 mb-4" aria-label="Monthly attendance summary">
      {CARDS.map(({ key, label, className }) => (
        <div className="col-6 col-md-4 col-lg" key={key}>
          <div className={`team-attendance-summary-card ${className}`.trim()}>
            <span>{label}</span>
            <strong>{formatAttendanceCount(counts?.[key] || 0)}</strong>
            <small>{key === "holiday" || key === "issue" ? "" : `${percentages?.[key] ?? "0.0"}%`}</small>
          </div>
        </div>
      ))}
    </div>
  );
}

export default AttendanceStatusSummary;
