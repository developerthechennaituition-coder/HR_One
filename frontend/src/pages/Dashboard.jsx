import { HRONE_API_BASE } from "../config/api.js";
import { useEffect, useState } from "react";
/* import "./Dashboard.css"; */

const API = HRONE_API_BASE;
const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const dateLabel = () => new Intl.DateTimeFormat("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(new Date());
const greeting = () => new Date().getHours() < 12 ? "Good morning" : new Date().getHours() < 17 ? "Good afternoon" : "Good evening";
const num = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;

function Tile({ icon, label, value, note, color, onClick }) {
  return <button type="button" className={`hrdash-tile ${color}`} onClick={onClick} disabled={!onClick}>
    <span className="hrdash-tile-icon"><i className={`bi ${icon}`} /></span>
    <span className="hrdash-tile-body"><small>{label}</small><strong>{value}</strong><em>{note}</em></span>
    <i className="bi bi-arrow-up-right hrdash-tile-arrow" />
  </button>;
}

export default function Dashboard({ session, user, onNavigate }) {
  const stored = (() => { try { return JSON.parse(sessionStorage.getItem("hrms_session") || "null"); } catch { return null; } })();
  const token = session?.token || stored?.token || "";
  const me = user || session?.user || stored?.user || {};
  const role = String(me.role || "").trim().toLowerCase();
  const admin = role === "hr admin" || role === "administrator";
  const hrAdmin = role === "hr admin";
  const manager = role === "hr manager";
  const [loading, setLoading] = useState(true);
  const [employees, setEmployees] = useState([]);
  const [users, setUsers] = useState([]);
  const [attendance, setAttendance] = useState([]);
  const [leave, setLeave] = useState({});
  const [team, setTeam] = useState([]);
  const [error, setError] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let alive = true;
    async function fetchJson(path) {
      const res = await fetch(`${API}${path}`, { headers: { Accept: "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) } });
      if (!res.ok) throw new Error(path);
      return res.json();
    }
    async function load() {
      setLoading(true); setError(false);
      const requests = [fetchJson(`/api/leave/dashboard?year=${new Date().getFullYear()}`).catch(() => { if (alive) setError(true); return null; })];
      if (admin) {
        requests.push(fetchJson("/api/employees").catch(() => { if (alive) setError(true); return []; }));
        requests.push(fetchJson("/api/users").catch(() => { if (alive) setError(true); return []; }));
        requests.push(fetchJson(`/api/attendance?attendance_date=${today()}`).catch(() => { if (alive) setError(true); return []; }));
      }
      if (manager) requests.push(fetchJson("/api/reporting-manager/team-members/list").catch(() => { if (alive) setError(true); return []; }));
      const data = await Promise.all(requests);
      if (!alive) return;
      setLeave(data[0]?.summary || {});
      if (admin) { setEmployees(Array.isArray(data[1]) ? data[1] : []); setUsers(Array.isArray(data[2]) ? data[2] : []); setAttendance(Array.isArray(data[3]) ? data[3] : []); }
      if (manager) { const t = data[1]; setTeam(Array.isArray(t) ? t : Array.isArray(t?.team_members) ? t.team_members : Array.isArray(t?.members) ? t.members : []); }
      setLoading(false);
    }
    load(); return () => { alive = false; };
  }, [token, admin, manager, refresh]);

  const name = me.name || me.full_name || me.employee_name || "there";
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
  const activeEmployees = users.filter((e) => e.is_active !== false && String(e.status || "active").toLowerCase() !== "inactive").length;
  const activeUsers = users.filter((u) => u.is_active !== false && String(u.status || "active").toLowerCase() !== "inactive").length;
  const present = attendance.filter((a) => { const s = String(a.status || a.attendance_status || "").toLowerCase(); return s === "present" || s === "late" || Boolean(a.check_in || a.check_in_time); }).length;
  const quickLinks = admin ? [
    ["bi-people", "Employee Directory", "Manage employee records", "employees", "blue"],
    ["bi-calendar2-check", "Daily Attendance", "Review today's attendance", "attendance", "green"],
    ["bi-calendar2-week", "Leave Management", "Review leave requests", "leave", "purple"],
    ...(hrAdmin ? [["bi-cash-stack", "Payroll Dashboard", "Salary and payroll overview", "payroll-dashboard", "orange"]] : []),
  ] : manager ? [
    ["bi-people", "My Team", "View your team members", "team-members", "blue"],
    ["bi-calendar2-check", "Attendance", "Open attendance records", "attendance", "green"],
    ["bi-calendar2-week", "Leave Requests", "Review leave information", "leave", "purple"],
    ["bi-person-circle", "My Profile", "Review your personal details", "profile", "orange"],
  ] : [
    ["bi-calendar2-check", "My Attendance", "Review attendance records", "attendance", "green"],
    ["bi-calendar2-week", "My Leave", "Apply and track leave", "leave", "purple"],
    ["bi-file-earmark-pdf", "My Payslips", "View and download payslips", "payslip-management", "orange"],
    ["bi-person-circle", "My Profile", "Review personal details", "profile", "blue"],
  ];
  const cards = admin ? [
    ["bi-people-fill", "Active employees", loading ? "—" : activeEmployees, "Employee records", "blue", "employees"],
    ["bi-person-check-fill", "Active accounts", loading ? "—" : activeUsers, "System users", "green", "users"],
    ["bi-calendar-check-fill", "Present today", loading ? "—" : present, "Today's attendance", "purple", "attendance"],
    ["bi-hourglass-split", "Pending leave", loading ? "—" : num(leave.pending), "Awaiting decision", "orange", "leave"],
  ] : manager ? [
    ["bi-people-fill", "Team members", loading ? "—" : team.length, "Your reporting team", "blue", "team-members"],
    ["bi-calendar2-week", "Leave requests", loading ? "—" : num(leave.total_requests), "Submitted this year", "purple", "leave"],
    ["bi-hourglass-split", "Pending leave", loading ? "—" : num(leave.pending), "Awaiting decision", "orange", "leave"],
    ["bi-check-circle-fill", "Approved requests", loading ? "—" : num(leave.approved), "This year", "green", "leave"],
  ] : [
    ["bi-calendar2-week", "My leave requests", loading ? "—" : num(leave.total_requests), "Submitted this year", "blue", "leave"],
    ["bi-hourglass-split", "Pending requests", loading ? "—" : num(leave.pending), "Awaiting decision", "orange", "leave"],
    ["bi-check-circle-fill", "Approved requests", loading ? "—" : num(leave.approved), "This year", "green", "leave"],
    ["bi-file-earmark-pdf-fill", "Salary payslips", "View", "Open your payslips", "purple", "payslip-management"],
  ];

  return <main className="hrdash-page">
    <section className="hrdash-hero"><div className="hrdash-orbit" /><div className="hrdash-hero-copy">
      <div className="hrdash-eyebrow"><span /> HRMS WORKSPACE</div>
      <h1>{greeting()}, {name.split(" ")[0]} {name.split(" ")[1]} {name.split(" ")[2]}<b>✦</b></h1>
      <p>{admin ? "Here’s your workforce overview. Stay on top of people, attendance and leave." : manager ? "Here’s what’s happening with your team and your workday." : "Your personal workspace for attendance, leave and salary information."}</p>
      <div className="hrdash-date"><i className="bi bi-calendar3" /> {dateLabel()}</div>
    </div><div className="hrdash-avatar">{initials || "U"}</div></section>

    <div className="hrdash-heading"><div><small>AT A GLANCE</small><h2>{admin ? "Workforce overview" : manager ? "Team overview" : "Your overview"}</h2></div><button type="button" onClick={() => setRefresh((n) => n + 1)} disabled={loading}><i className={`bi bi-arrow-clockwise ${loading ? "hrdash-spin" : ""}`} /> Refresh</button></div>
    <section className="hrdash-metrics">{cards.map(([icon, label, value, note, color, page]) => <Tile key={label} icon={icon} label={label} value={value} note={note} color={color} onClick={() => onNavigate(page)} />)}</section>

    <section className="hrdash-bottom">
      <div className="hrdash-panel"><div className="hrdash-panel-heading"><div><small>GET THINGS DONE</small><h3>Quick access</h3></div><span><i className="bi bi-lightning-charge" /></span></div>
        <div className="hrdash-links">{quickLinks.map(([icon, title, detail, page, color]) => <button key={page} type="button" onClick={() => onNavigate(page)}><span className={`hrdash-link-icon ${color}`}><i className={`bi ${icon}`} /></span><span className="hrdash-link-copy"><strong>{title}</strong><small>{detail}</small></span><i className="bi bi-chevron-right" /></button>)}</div>
      </div>
      <div className="hrdash-panel"><div className="hrdash-panel-heading"><div><small>YOUR WORKSPACE</small><h3>{admin ? "People operations" : manager ? "Team & approvals" : "Personal essentials"}</h3></div><span><i className="bi bi-stars" /></span></div>
        <div className="hrdash-highlight"><span><i className={`bi ${admin ? "bi-buildings" : manager ? "bi-people" : "bi-person-check"}`} /></span><div><strong>{admin ? "A clear view of your workforce" : manager ? "Keep your team moving" : "Everything you need, in one place"}</strong><p>{admin ? "Manage employee records, daily attendance, leave and payroll from your workspace." : manager ? "Stay informed about your team, leave and personal profile." : "Track attendance, follow leave requests and access your payslips securely."}</p></div></div>
        <div className="hrdash-secure"><span><i className="bi bi-shield-check" /></span><div><strong>Role-based workspace</strong><small>Information and actions are tailored to your account.</small></div></div>
        {error && !loading && <div className="hrdash-message"><i className="bi bi-info-circle" /> Some live figures could not be loaded. Confirm the backend is running and your session is active.</div>}
        {!error && !loading && <div className="hrdash-message success"><i className="bi bi-check-circle" /> Dashboard information refreshed from HRMS.</div>}
      </div>
    </section><footer className="hrdash-footer">HRMS<span>•</span> Human Resource Management System</footer>
  </main>;
}
