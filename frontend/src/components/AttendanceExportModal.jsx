import { useEffect, useMemo, useState } from "react";
import { HRONE_API_BASE } from "../config/api.js";

const API = `${HRONE_API_BASE}/api/attendance`;

function currentMonthParts() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return { month: Number(values.month), year: Number(values.year) };
}

export default function AttendanceExportModal({ departments = [], getHeaders, onClose }) {
  const current = useMemo(currentMonthParts, []);
  const [departmentId, setDepartmentId] = useState("all");
  const [month, setMonth] = useState(String(current.month));
  const [year, setYear] = useState(String(current.year));
  const [search, setSearch] = useState("");
  const [employees, setEmployees] = useState([]);
  const [selected, setSelected] = useState([]);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError("");
      setSelected([]);
      try {
        const query = new URLSearchParams({
          month, year, department_id: departmentId,
        });
        const response = await fetch(`${API}/export-options?${query}`, {
          headers: getHeaders(),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data?.detail || "Unable to load export employees.");
        if (!cancelled) setEmployees(Array.isArray(data.employees) ? data.employees : []);
      } catch (err) {
        if (!cancelled) {
          setEmployees([]);
          setError(err.message || "Unable to load export employees.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [departmentId, month, year, getHeaders]);

  const visibleEmployees = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return employees;
    return employees.filter((employee) =>
      [employee.name, employee.employee_id, employee.employee_code]
        .some((value) => String(value || "").toLowerCase().includes(query))
    );
  }, [employees, search]);

  const eligibleVisible = visibleEmployees.filter((employee) => employee.eligible);
  const selectedVisibleCount = eligibleVisible.filter((employee) => selected.includes(employee.user_id)).length;
  const allVisibleSelected = eligibleVisible.length > 0 && selectedVisibleCount === eligibleVisible.length;
  const partiallySelected = selectedVisibleCount > 0 && !allVisibleSelected;

  const toggleEmployee = (id, checked) => {
    setSelected((previous) => checked
      ? [...new Set([...previous, id])]
      : previous.filter((value) => value !== id));
  };

  const toggleAllVisible = (checked) => {
    const visibleIds = new Set(eligibleVisible.map((employee) => employee.user_id));
    setSelected((previous) => checked
      ? [...new Set([...previous, ...visibleIds])]
      : previous.filter((id) => !visibleIds.has(id)));
  };

  const generate = async () => {
    if (!selected.length) {
      setError("Select at least one eligible employee.");
      return;
    }
    setGenerating(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`${API}/export.xlsx`, {
        method: "POST",
        headers: { ...getHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({
          department_id: departmentId,
          month: Number(month),
          year: Number(year),
          employee_ids: selected,
        }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data?.detail || "Unable to generate the attendance Excel report.");
      }
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") || "";
      const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1]
        || `Daily_Attendance_Report_${month}_${year}.xlsx`;
      const objectUrl = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(objectUrl);
      setNotice(`Attendance report generated for ${selected.length} employee(s).`);
    } catch (err) {
      setError(err.message || "Unable to generate the attendance Excel report.");
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="modal d-block" role="dialog" aria-modal="true" aria-labelledby="attendance-export-title"
      style={{ backgroundColor: "rgba(0,0,0,.5)", zIndex: 1080 }}>
      <div className="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable">
        <div className="modal-content border-0 shadow">
          <div className="modal-header">
            <div>
              <h5 className="modal-title" id="attendance-export-title">Export Attendance</h5>
              <small className="text-muted">Select a department, reporting month and eligible employees.</small>
            </div>
            <button type="button" className="btn-close" onClick={onClose} aria-label="Close" />
          </div>
          <div className="modal-body">
            {error && <div className="alert alert-danger py-2" role="alert">{error}</div>}
            {notice && <div className="alert alert-success py-2" role="status">{notice}</div>}
            <div className="row g-3 mb-3">
              <div className="col-md-4">
                <label className="form-label" htmlFor="attendance-export-department">Department</label>
                <select id="attendance-export-department" className="form-select" value={departmentId}
                  onChange={(event) => setDepartmentId(event.target.value)}>
                  <option value="all">All Departments</option>
                  {departments.map((item) => {
                    const id = String(item.id || item._id || "");
                    const name = item.department_name || item.name || id;
                    return <option key={id} value={id}>{name}</option>;
                  })}
                </select>
              </div>
              <div className="col-md-3">
                <label className="form-label" htmlFor="attendance-export-month">Attendance Month</label>
                <select id="attendance-export-month" className="form-select" value={month}
                  onChange={(event) => setMonth(event.target.value)}>
                  {Array.from({ length: 12 }, (_, index) => {
                    const value = String(index + 1);
                    return <option key={value} value={value}>{new Date(2000, index, 1).toLocaleString("en", { month: "long" })}</option>;
                  })}
                </select>
              </div>
              <div className="col-md-2">
                <label className="form-label" htmlFor="attendance-export-year">Year</label>
                <input id="attendance-export-year" className="form-control" type="number" min="2000" max={current.year}
                  value={year} onChange={(event) => setYear(event.target.value)} />
              </div>
              <div className="col-md-3">
                <label className="form-label" htmlFor="attendance-export-search">Search Employee</label>
                <input id="attendance-export-search" className="form-control" value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Name, HRMS ID or ESSL code" />
              </div>
            </div>
            <div className="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
              <div className="form-check">
                <input className="form-check-input" type="checkbox" id="attendance-export-select-all"
                  checked={allVisibleSelected} ref={(element) => { if (element) element.indeterminate = partiallySelected; }}
                  onChange={(event) => toggleAllVisible(event.target.checked)}
                  disabled={loading || eligibleVisible.length === 0} />
                <label className="form-check-label fw-semibold" htmlFor="attendance-export-select-all">Select All Eligible Employees</label>
              </div>
              <span className="small text-muted">{selected.length} selected · {employees.filter((item) => item.eligible).length} eligible</span>
            </div>
            <div className="table-responsive border rounded" style={{ maxHeight: "48vh" }}>
              <table className="table table-sm table-hover align-middle mb-0">
                <thead className="table-light sticky-top">
                  <tr><th style={{ width: 48 }}>Select</th><th>Employee Name</th><th>Employee ID (HRMS)</th><th>Emp Code (ESSL)</th><th>Eligibility</th></tr>
                </thead>
                <tbody>
                  {loading && <tr><td colSpan="5" className="text-center py-4">Loading employees…</td></tr>}
                  {!loading && visibleEmployees.map((employee) => (
                    <tr key={employee.user_id}>
                      <td>
                        <input className="form-check-input" type="checkbox"
                          checked={selected.includes(employee.user_id)}
                          disabled={!employee.eligible}
                          onChange={(event) => toggleEmployee(employee.user_id, event.target.checked)}
                          aria-label={`Select ${employee.name}`} />
                      </td>
                      <td className="fw-semibold">{employee.name || "Unnamed employee"}</td>
                      <td className="small text-break">{employee.employee_id}</td>
                      <td>{employee.employee_code || "—"}</td>
                      <td>{employee.eligible
                        ? <span className="badge text-bg-success">Eligible</span>
                        : <span className="small text-muted">{employee.eligibility_message || "Not eligible"}</span>}</td>
                    </tr>
                  ))}
                  {!loading && !visibleEmployees.length && <tr><td colSpan="5" className="text-center text-muted py-4">No employees match the selected filters.</td></tr>}
                </tbody>
              </table>
            </div>
            <small className="text-muted d-block mt-2">Employees who joined after the selected month or left before it are disabled. Historical employees who worked during the selected month remain selectable.</small>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-outline-secondary" onClick={onClose} disabled={generating}>Close</button>
            <button type="button" className="btn btn-primary" onClick={generate} disabled={generating || loading || !selected.length}>
              {generating ? <><span className="spinner-border spinner-border-sm me-2" />Generating…</> : <><i className="bi bi-file-earmark-excel me-2" />Generate Excel ({selected.length})</>}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
