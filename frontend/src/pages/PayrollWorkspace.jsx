import { useCallback, useEffect, useMemo, useState } from "react";
import PayslipViewer from "../components/PayslipViewer";
import Pagination from "../components/Pagination";
import {
  calculatePayroll,
  getAttendanceSummary,
  getPayrollEmployees,
  getPayrollRecords,
  getPayslips,
  getSalaryProfiles,
  updatePayrollStatus,
  downloadPayslip,
  getPayslipDetail,
  viewPayslip,
} from "../services/payrollApi";
const currentDate = new Date();
const MONTHS = Array.from({ length: 12 }, (_, index) => ({
  value: index + 1,
  label: new Date(2000, index, 1).toLocaleString("en-IN", { month: "long" }),
}));
const money = (value, currency = "INR") =>
  `${currency} ${Number(value || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
function Icon({ name, className = "" }) {
  return <i aria-hidden="true" className={`bi bi-${name} ${className}`.trim()} />;
}
function SummaryCard({ icon, label, value, tone = "blue", hint }) {
  return (
    <article className={`payroll-summary-card payroll-summary-${tone}`}>
      <div className="payroll-summary-icon"><Icon name={icon} /></div>
      <div className="payroll-summary-copy">
        <span>{label}</span>
        <strong>{value}</strong>
        {hint && <small>{hint}</small>}
      </div>
    </article>
  );
}
function StatusBadge({ status }) {
  const key = String(status || "Pending").toLowerCase().replace(/[^a-z]+/g, "-");
  return <span className={`payroll-status payroll-status-${key}`}>{status || "Pending"}</span>;
}
export default function PayrollWorkspace({ mode = "monthly", onNavigate }) {
  const [month, setMonth] = useState(currentDate.getMonth() + 1);
  const [year, setYear] = useState(currentDate.getFullYear());
  const [employees, setEmployees] = useState([]);
  const [records, setRecords] = useState([]);
  const [slips, setSlips] = useState([]);
  const [salaryProfiles, setSalaryProfiles] = useState([]);
  const [attendanceSummary, setAttendanceSummary] = useState([]);
  const [paidDays, setPaidDays] = useState({});
  const [lopDays, setLopDays] = useState({});
  const [overtimeAmounts, setOvertimeAmounts] = useState({});
  const [paymentRef, setPaymentRef] = useState({});
  const [search, setSearch] = useState("");
  const [departmentFilter, setDepartmentFilter] = useState("");
  // Paid-day Review is the source of truth for Monthly Payroll Records filters.
  const [reviewSearch, setReviewSearch] = useState("");
  const [reviewDepartment, setReviewDepartment] = useState("");
  const [reviewEmployeeFilter, setReviewEmployeeFilter] = useState("");
  const [recordSearch, setRecordSearch] = useState("");
  const [recordDepartment, setRecordDepartment] = useState("");
  const [recordEmployeeFilter, setRecordEmployeeFilter] = useState("");
  const [payslipEmployeeFilter, setPayslipEmployeeFilter] = useState("");
  const [detailsEmployeeId, setDetailsEmployeeId] = useState("");
  const [reviewStatusFilter, setReviewStatusFilter] = useState("");
  const [recordStatusFilter, setRecordStatusFilter] = useState("");
  const [employeePage, setEmployeePage] = useState(1);
  const [recordPage, setRecordPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [previewRow, setPreviewRow] = useState(null);
  const [selectedEmployeeIds, setSelectedEmployeeIds] = useState([]);
  const [reviewRow, setReviewRow] = useState(null);
  const refresh = useCallback(async () => {
    setError("");
    try {
      const [employeeRows, payrollRows, payslipRows, profileRows, attendanceRows] = await Promise.all([
        getPayrollEmployees(),
        getPayrollRecords(month, year),
        getPayslips(month, year),
        getSalaryProfiles(),
        getAttendanceSummary(month, year),
      ]);
      setEmployees(Array.isArray(employeeRows) ? employeeRows : []);
      const safePayrollRows = Array.isArray(payrollRows) ? payrollRows : [];
      setRecords(safePayrollRows);
      const alreadyCalculated = new Set(safePayrollRows.map((row) => row.employee_id));
      setSelectedEmployeeIds((current) => current.filter((id) => !alreadyCalculated.has(id)));
      setSlips(Array.isArray(payslipRows) ? payslipRows : []);
      setSalaryProfiles(Array.isArray(profileRows) ? profileRows : []);
      const safeAttendance = Array.isArray(attendanceRows) ? attendanceRows : [];
      setAttendanceSummary(safeAttendance);
      setPaidDays(Object.fromEntries(safeAttendance.map((row) => [row.employee_id, String(row.paid_days ?? "")])));
      setLopDays(Object.fromEntries(safeAttendance.map((row) => [row.employee_id, String(row.lop_days ?? "")])));
      setOvertimeAmounts((current) => Object.fromEntries(safeAttendance.map((row) => [row.employee_id, current[row.employee_id] ?? String(safePayrollRows.find((payrollRow) => payrollRow.employee_id === row.employee_id)?.overtime_amount ?? "0")] )));
    } catch (requestError) {
      setError(requestError.message || "Unable to load payroll data.");
    }
  }, [month, year]);
  useEffect(() => { setOvertimeAmounts({}); }, [month, year]);
  useEffect(() => { refresh(); }, [refresh]);
  const activeEmployees = useMemo(
    () => employees.filter((employee) => String(employee.status || "active").toLowerCase() !== "inactive"),
    [employees],
  );
  const departmentOptions = [...new Set([
    ...employees.map((row) => row.department),
    ...records.map((row) => row.department),
    ...slips.map((row) => row.department),
  ].map((value) => String(value || "").trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  const reviewEmployeeOptions = useMemo(() => activeEmployees
    .filter((employee) => !reviewDepartment || String(employee.department || "").trim() === reviewDepartment)
    .sort((a, b) => String(a.name || a.employee_id).localeCompare(String(b.name || b.employee_id))),
  [activeEmployees, reviewDepartment]);
  const recordEmployeeOptions = useMemo(() => employees
    .filter((employee) => !recordDepartment || String(employee.department || "").trim() === recordDepartment)
    .sort((a, b) => String(a.name || a.employee_id).localeCompare(String(b.name || b.employee_id))),
  [employees, recordDepartment]);
  const payslipEmployeeOptions = useMemo(() => employees
    .filter((employee) => !departmentFilter || String(employee.department || "").trim() === departmentFilter)
    .sort((a, b) => String(a.name || a.employee_id).localeCompare(String(b.name || b.employee_id))),
  [employees, departmentFilter]);
  // Status shown in the Paid-Day Review "Review & Calculate" column (same values as the cell badge).
  const reviewStatusOf = useCallback((employeeId) => {
    const record = records.find((row) => row.employee_id === employeeId);
    if (record) return String(record.status || "Calculated");
    return salaryProfiles.some((profile) => profile.employee_id === employeeId) ? "Pending" : "Salary setup required";
  }, [records, salaryProfiles]);
  const reviewStatusOptions = useMemo(() => {
    const base = ["Pending", "Salary setup required", "Calculated", "Approved", "Processed", "Paid"];
    const extra = records.map((row) => String(row.status || "").trim()).filter((value) => value && !base.includes(value));
    return [...base, ...new Set(extra)];
  }, [records]);
  const recordStatusOptions = useMemo(() => {
    const base = ["Calculated", "Approved", "Processed", "Paid"];
    const extra = records.map((row) => String(row.status || "").trim()).filter((value) => value && !base.includes(value));
    return [...base, ...new Set(extra)];
  }, [records]);
  const visibleEmployees = useMemo(() => {
    const query = reviewSearch.trim().toLowerCase();
    return activeEmployees.filter((employee) =>
      (!query || [employee.employee_id, employee.name]
        .some((value) => String(value || "").toLowerCase().includes(query))) &&
      (!reviewDepartment || String(employee.department || "").trim() === reviewDepartment) &&
      (!reviewEmployeeFilter || employee.employee_id === reviewEmployeeFilter) &&
      (!reviewStatusFilter || reviewStatusOf(employee.employee_id) === reviewStatusFilter),
    );
  }, [activeEmployees, reviewSearch, reviewDepartment, reviewEmployeeFilter, reviewStatusFilter, reviewStatusOf]);
  const visibleRows = useMemo(() => {
    const rows = mode === "payslips" ? slips : records;
    const query = (mode === "monthly" ? recordSearch : search).trim().toLowerCase();
    const department = mode === "monthly" ? recordDepartment : departmentFilter;
    const employeeFilter = mode === "monthly" ? recordEmployeeFilter : payslipEmployeeFilter;
    return rows.filter((row) =>
      (!query || [row.employee_id, row.employee_name]
        .some((value) => String(value || "").toLowerCase().includes(query))) &&
      (!department || String(row.department || "").trim() === department) &&
      (!employeeFilter || row.employee_id === employeeFilter) &&
      (mode !== "monthly" || !recordStatusFilter || String(row.status || "") === recordStatusFilter),
    );
  }, [mode, records, slips, search, departmentFilter, recordSearch, recordDepartment, recordStatusFilter, recordEmployeeFilter, payslipEmployeeFilter]);
  const pageSize = 10;
  const pageEmployees = visibleEmployees.slice((employeePage - 1) * pageSize, employeePage * pageSize);
  const pageRows = visibleRows.slice((recordPage - 1) * pageSize, recordPage * pageSize);
  useEffect(() => { setEmployeePage(1); }, [reviewSearch, reviewDepartment, reviewEmployeeFilter, reviewStatusFilter, mode]);
  useEffect(() => { setRecordPage(1); }, [search, departmentFilter, recordSearch, recordDepartment, recordStatusFilter, recordEmployeeFilter, payslipEmployeeFilter, mode]);
  const reviewFiltersActive = Boolean(reviewSearch || reviewDepartment || reviewEmployeeFilter || reviewStatusFilter);
  const recordFiltersActive = Boolean(recordSearch || recordDepartment || recordEmployeeFilter || recordStatusFilter);
  const resetReviewFilters = () => { setReviewSearch(""); setReviewDepartment(""); setReviewEmployeeFilter(""); setReviewStatusFilter(""); setEmployeePage(1); };
  const resetRecordFilters = () => {
    setReviewSearch(""); setReviewDepartment(""); setReviewEmployeeFilter(""); setReviewStatusFilter("");
    setRecordSearch(""); setRecordDepartment(""); setRecordEmployeeFilter(""); setRecordStatusFilter("");
    setRecordPage(1);
  };
  // Keep Monthly Payroll Records aligned with the filters selected in Paid-day Review.
  // This also keeps employee selection dependent on the selected department.
  useEffect(() => {
    setRecordSearch(reviewSearch);
    setRecordDepartment(reviewDepartment);
    setRecordEmployeeFilter(reviewEmployeeFilter);
    setRecordStatusFilter(reviewStatusFilter);
    setRecordPage(1);
  }, [reviewSearch, reviewDepartment, reviewEmployeeFilter, reviewStatusFilter]);

  const resetPayslipFilters = () => {
    setSearch("");
    setDepartmentFilter("");
    setPayslipEmployeeFilter("");
    setRecordPage(1);
  };
  const detailsEmployee = detailsEmployeeId ? employees.find((row) => row.employee_id === detailsEmployeeId) : null;
  const paidCount = records.filter((row) => row.status === "Paid").length;
  const calculatedCount = records.filter((row) => row.status === "Calculated").length;
  const profileCount = salaryProfiles.filter((profile) => profile.employee_id).length;
  const totalNet = records.reduce((sum, row) => sum + Number(row.net_salary || 0), 0);
  const title = mode === "payslips" ? "Payslip Management" : mode === "dashboard" ? "Payroll Dashboard" : "Monthly Payroll Processing";
  const periodKey = `${year}-${String(month).padStart(2, "0")}`;
  async function runCalculation() {
    setBusy(true); setError(""); setMessage("");
    try {
      const selectedEligibleIds = selectedEmployeeIds.filter((id) =>
        salaryProfiles.some((profile) => profile.employee_id === id && String(profile.effective_from || "").slice(0, 7) <= periodKey)
        && !records.some((row) => row.employee_id === id)
      );
      if (!selectedEligibleIds.length) {
        setError("Select at least one employee with an effective salary profile who has not been calculated for this month.");
        return;
      }
      const payloadDays = {};
      const payloadLop = {};
      const payloadOvertime = {};
      activeEmployees.filter((employee) => selectedEligibleIds.includes(employee.employee_id)).forEach((employee) => {
        const id = employee.employee_id;
        if (paidDays[id] !== undefined && paidDays[id] !== "") payloadDays[id] = Number(paidDays[id]);
        if (lopDays[id] !== undefined && lopDays[id] !== "") payloadLop[id] = Number(lopDays[id]);
        if (overtimeAmounts[id] !== undefined && overtimeAmounts[id] !== "") payloadOvertime[id] = Number(overtimeAmounts[id]);
      });
      const result = await calculatePayroll({ month: Number(month), year: Number(year), employee_ids: selectedEligibleIds, paid_days: payloadDays, lop_days: payloadLop, overtime_amounts: payloadOvertime });
      setMessage(`Calculated ${result.calculated || 0} selected employee payroll record(s). ${(result.errors || []).length} employee(s) need attention.`);
      if (result.errors?.length) setError(result.errors.map((item) => `${item.employee_id}: ${item.error}`).join(" · "));
      setSelectedEmployeeIds([]);
      await refresh();
    } catch (requestError) {
      setError(requestError.message || "Payroll calculation failed.");
    } finally { setBusy(false); }
  }
  function toggleEmployeeSelection(employeeId, checked) {
    setSelectedEmployeeIds((current) => checked
      ? [...new Set([...current, employeeId])]
      : current.filter((id) => id !== employeeId));
  }
  function toggleVisibleSelection(checked) {
    const eligibleIds = visibleEmployees
      .filter((employee) => salaryProfiles.some((profile) => profile.employee_id === employee.employee_id && String(profile.effective_from || "").slice(0, 7) <= periodKey) && !records.some((row) => row.employee_id === employee.employee_id))
      .map((employee) => employee.employee_id);
    setSelectedEmployeeIds((current) => checked
      ? [...new Set([...current, ...eligibleIds])]
      : current.filter((id) => !eligibleIds.includes(id)));
  }
  async function handleStatusAction(row, actionName) {
    setError(""); setMessage("");
    try {
      const reference = actionName === "paid" ? (paymentRef[row.id] || "").trim() : "";
      if (actionName === "paid" && !reference) {
        setError("Enter the confirmed payment reference before marking payroll as paid.");
        return;
      }
      await updatePayrollStatus(row.id, actionName, reference);
      setMessage(actionName === "disapprove" ? `Payroll for ${row.employee_id} was disapproved and removed for recalculation.` : `Payroll ${actionName} action completed for ${row.employee_id}.`);
      setReviewRow(null);
      await refresh();
    } catch (requestError) {
      setError(requestError.message || "Unable to update payroll status.");
    }
  }
  function handlePreviewPayslip(row) {
    setError("");
    setPreviewRow(row);
  }
  // Stable callbacks for the payslip viewer (HR Admin-only endpoints).
  const previewRecordId = previewRow ? previewRow.payroll_record_id || previewRow.id : "";
  const loadPreviewDetail = useCallback(() => getPayslipDetail(previewRecordId), [previewRecordId]);
  const loadPreviewPdf = useCallback(() => viewPayslip(previewRecordId), [previewRecordId]);
  const closePreview = useCallback(() => setPreviewRow(null), []);
  return (
    <main className="payroll-page">
      <header className="payroll-page-header">
        <div>
          <div className="payroll-eyebrow"><Icon name="cash-stack" /> HRMS / PAYROLL</div>
          <h1>{title}</h1>
          <p>{mode === "dashboard" ? "Monitor salary setup, monthly processing and payslip progress." : mode === "payslips" ? "Find and download finalized employee payslips." : "Review paid days, calculate salary and follow the payroll approval workflow."}</p>
        </div>
        <div className="payroll-period-controls">
          <label className="payroll-field-label" htmlFor="payroll-month">Month
            <select id="payroll-month" value={month} onChange={(event) => setMonth(Number(event.target.value))}>
              {MONTHS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </label>
          <label className="payroll-field-label" htmlFor="payroll-year">Year
            <select id="payroll-year" value={year} onChange={(event) => setYear(Number(event.target.value))}>
              {[year - 1, year, year + 1].map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
        </div>
      </header>
      {error && <div className="payroll-alert payroll-alert-error" role="alert"><Icon name="exclamation-circle-fill" /> <span>{error}</span></div>}
      {message && <div className="payroll-alert payroll-alert-success" role="status"><Icon name="check-circle-fill" /> <span>{message}</span></div>}
      <section className="payroll-summary-grid" aria-label="Payroll summary">
        <SummaryCard icon="people-fill" label="Active Employees" value={activeEmployees.length} tone="green" />
        <SummaryCard icon="person-check-fill" label="Salary Profiles Completed" value={profileCount} tone="blue" hint={`${Math.max(activeEmployees.length - profileCount, 0)} employees need setup`} />
        <SummaryCard icon="hourglass-split" label="Pending Review" value={calculatedCount} tone="orange" />
        <SummaryCard icon="wallet2" label="Total Net Pay" value={money(totalNet)} tone="purple" hint={`${paidCount} marked paid`} />
      </section>
      {mode === "dashboard" && <>
        <section className="payroll-quick-actions">
          <div className="payroll-section-heading"><div><h2>Quick Actions</h2><p>Continue with a payroll task.</p></div></div>
          <div className="payroll-action-grid">
            <button type="button" className="payroll-action-card" onClick={() => onNavigate?.("employee-salary-setup")}><span className="payroll-action-icon blue"><Icon name="person-vcard-fill" /></span><span><strong>Employee Salary Setup</strong><small>Create or update employee salary profiles</small></span><Icon name="arrow-up-right" className="payroll-action-arrow" /></button>
            <button type="button" className="payroll-action-card" onClick={() => onNavigate?.("monthly-payroll")}><span className="payroll-action-icon green"><Icon name="calculator-fill" /></span><span><strong>Process Monthly Payroll</strong><small>Review paid days and calculate payroll</small></span><Icon name="arrow-up-right" className="payroll-action-arrow" /></button>
            <button type="button" className="payroll-action-card" onClick={() => onNavigate?.("payslip-management")}><span className="payroll-action-icon purple"><Icon name="file-earmark-pdf-fill" /></span><span><strong>Payslip Management</strong><small>View and download finalized payslips</small></span><Icon name="arrow-up-right" className="payroll-action-arrow" /></button>
          </div>
        </section>
        <section className="payroll-panel">
          <div className="payroll-section-heading"><div><h2>Recent Payroll Records</h2><p>{MONTHS.find((item) => item.value === month)?.label} {year}</p></div><button type="button" className="payroll-button payroll-button-outline" onClick={() => onNavigate?.("monthly-payroll")}>View monthly payroll <Icon name="arrow-right" /></button></div>
          <PayrollTable rows={records.slice(0, 8)} mode="dashboard" money={money} onAction={handleStatusAction} onReview={setReviewRow} paymentRef={paymentRef} setPaymentRef={setPaymentRef} />
        </section>
      </>}
      {mode === "monthly" && <>
        <section className="payroll-panel payroll-review-panel">
          <div className="payroll-section-heading"><div><h2><Icon name="calendar2-check" /> Paid-day review</h2><p>Confirm attendance values before calculating salaries.</p></div><span className="payroll-info-chip"><Icon name="info-circle" /> Company attendance rules must be verified</span></div>
          <div className="payroll-alert payroll-alert-warning"><Icon name="info-circle" /><span>Payable days = Present + Approved Paid Leave + Eligible Holidays + Weekly Offs + Medical Leave. Not Applicable days set by HR Admin are added to Loss of Pay. Salary is prorated in the backend as Monthly Salary ÷ Calendar Days in the selected month × Payable Days. Unpaid leave and absences are not payable; avoid counting the same date twice.</span></div>
          <div className="payroll-toolbar"><label className="payroll-search"><Icon name="search" /><input type="search" aria-label="Filter paid-day review by employee" value={reviewSearch} onChange={(event) => setReviewSearch(event.target.value)} placeholder="Search employee ID, name" /></label><select className="form-select" aria-label="Filter paid-day review by department" value={reviewDepartment} onChange={(event) => { setReviewDepartment(event.target.value); setReviewEmployeeFilter(""); }} style={{ maxWidth: 240 }}>
            <option value="">All Departments</option>
            {departmentOptions.map((department) => <option key={department} value={department}>{department}</option>)}
          </select><select className="form-select" aria-label="Filter paid-day review by employee" value={reviewEmployeeFilter} onChange={(event) => setReviewEmployeeFilter(event.target.value)} style={{ maxWidth: 260 }}>
            <option value="">All Employees</option>
            {reviewEmployeeOptions.map((employee) => <option key={employee.employee_id} value={employee.employee_id}>{employee.name || employee.employee_id} ({employee.employee_id})</option>)}
          </select><button type="button" className="payroll-button payroll-button-outline" onClick={resetReviewFilters} disabled={!reviewFiltersActive} title="Clear the employee, department and status filters"><Icon name="arrow-counterclockwise" /> Reset</button><span className="payroll-result-count">{visibleEmployees.length} employees · {attendanceSummary.length} salary profiles with effective dates</span></div>
          <div className="payroll-table-wrap"><table className="payroll-table"><thead><tr><th><input type="checkbox" aria-label="Select all eligible employees" checked={visibleEmployees.filter((employee) => salaryProfiles.some((profile) => profile.employee_id === employee.employee_id && String(profile.effective_from || "").slice(0, 7) <= periodKey) && !records.some((row) => row.employee_id === employee.employee_id)).length > 0 && visibleEmployees.filter((employee) => salaryProfiles.some((profile) => profile.employee_id === employee.employee_id && String(profile.effective_from || "").slice(0, 7) <= periodKey) && !records.some((row) => row.employee_id === employee.employee_id)).every((employee) => selectedEmployeeIds.includes(employee.employee_id))} onChange={(event) => toggleVisibleSelection(event.target.checked)} /></th>{["Employee ID", "Employee Name", "Department", "View Details", "Payable Days (review)", "LOP Days (review)", "OT Amount (₹)", "Estimated Salary"].map((label) => <th key={label}>{label}</th>)}<th className="payroll-th-filter"><span>Review &amp; Calculate</span><select className="payroll-column-filter" aria-label="Filter by Review & Calculate status" value={reviewStatusFilter} onChange={(event) => setReviewStatusFilter(event.target.value)}><option value="">All statuses</option>{reviewStatusOptions.map((status) => <option key={status} value={status}>{status}</option>)}</select></th></tr></thead><tbody>
            {pageEmployees.map((employee) => { const attendance = attendanceSummary.find((row) => row.employee_id === employee.employee_id); const profile = salaryProfiles.find((row) => row.employee_id === employee.employee_id); const payrollRecord = records.find((row) => row.employee_id === employee.employee_id); const isCalculated = Boolean(payrollRecord); const canCalculate = Boolean(profile) && String(profile.effective_from || "").slice(0, 7) <= periodKey && !isCalculated; const daysInMonth = new Date(Number(year), Number(month), 0).getDate(); const payableDays = Number(paidDays[employee.employee_id] ?? attendance?.paid_days ?? 0); const estimatedSalary = Number(profile?.monthly_salary || 0) * (profile?.attendance_deduction_enabled === false ? 1 : Math.max(0, Math.min(daysInMonth, payableDays)) / daysInMonth) + Number(overtimeAmounts[employee.employee_id] || 0); return <tr key={employee.employee_id}><td>{isCalculated ? <Icon name="check-circle-fill" className="payroll-calculated-check" /> : canCalculate ? <input type="checkbox" aria-label={`Select ${employee.employee_id} for payroll calculation`} checked={selectedEmployeeIds.includes(employee.employee_id)} onChange={(event) => toggleEmployeeSelection(employee.employee_id, event.target.checked)} /> : <span title="Configure an effective salary profile first">—</span>}</td><td className="payroll-id">{employee.employee_id}</td><td>{employee.name || "—"}</td><td>{employee.department || "—"}</td><td><button type="button" className="payroll-button payroll-button-outline" disabled={!profile} title={profile ? "View attendance details" : "Salary setup required"} aria-label={`View attendance details for ${employee.employee_id}`} onClick={() => setDetailsEmployeeId(employee.employee_id)}><Icon name="eye" /> View Details</button></td><td><input aria-label={`Payable days for ${employee.employee_id}`} className="payroll-number-input" type="number" min="0" max={daysInMonth} step="0.5" placeholder="No profile" value={paidDays[employee.employee_id] ?? ""} onChange={(event) => setPaidDays((state) => ({ ...state, [employee.employee_id]: event.target.value }))} /></td><td><input aria-label={`LOP days for ${employee.employee_id}`} className="payroll-number-input" type="number" min="0" max={daysInMonth} step="0.5" placeholder="0" value={lopDays[employee.employee_id] ?? ""} onChange={(event) => setLopDays((state) => ({ ...state, [employee.employee_id]: event.target.value }))} /></td><td><input aria-label={`Overtime amount for ${employee.employee_id}`} className="payroll-number-input payroll-overtime-input" type="number" min="0" max="999999999.99" step="0.01" placeholder="0.00" value={overtimeAmounts[employee.employee_id] ?? "0"} onChange={(event) => setOvertimeAmounts((state) => ({ ...state, [employee.employee_id]: event.target.value }))} /></td><td className="payroll-net">{profile ? money(estimatedSalary, profile.currency || "INR") : "—"}</td><td>{isCalculated ? <span className="payroll-status payroll-status-calculated">{payrollRecord.status}</span> : <span className="payroll-status payroll-status-pending">{profile ? "Pending" : "Salary setup required"}</span>}</td></tr>; })}
            {!visibleEmployees.length && <tr><td colSpan="10" className="payroll-empty"><Icon name="people" /><strong>No employees found</strong><span>Try a different search or add employees in Employee Master.</span></td></tr>}
          </tbody></table></div>
          <Pagination page={employeePage} totalItems={visibleEmployees.length} pageSize={pageSize} onPageChange={setEmployeePage} />
          <div className="payroll-panel-footer"><span>Estimated salary uses the calendar-day proration rule; final amount is calculated and validated by the backend.</span><button disabled={busy || !selectedEmployeeIds.length} type="button" className="payroll-button payroll-button-primary" onClick={runCalculation}><Icon name={busy ? "arrow-repeat" : "calculator"} /> {busy ? "Calculating…" : `Review & Calculate Payroll (${selectedEmployeeIds.length})`}</button></div>
        </section>
        <section className="payroll-panel"><div className="payroll-section-heading"><div><h2>Monthly Payroll Records</h2><p>Approve, process and confirm payment for calculated records.</p></div></div><div className="payroll-toolbar"><label className="payroll-search"><Icon name="search" /><input type="search" aria-label="Filter payroll records by employee" value={recordSearch} onChange={(event) => { setRecordSearch(event.target.value); setReviewSearch(event.target.value); }} placeholder="Search employee ID, name" /></label><select className="form-select" aria-label="Filter payroll records by department" value={recordDepartment} onChange={(event) => { setRecordDepartment(event.target.value); setReviewDepartment(event.target.value); setRecordEmployeeFilter(""); setReviewEmployeeFilter(""); }} style={{ maxWidth: 240 }}>
            <option value="">All Departments</option>
            {departmentOptions.map((department) => <option key={department} value={department}>{department}</option>)}
          </select><select className="form-select" aria-label="Filter payroll records by employee" value={recordEmployeeFilter} onChange={(event) => { setRecordEmployeeFilter(event.target.value); setReviewEmployeeFilter(event.target.value); }} style={{ maxWidth: 260 }}>
            <option value="">All Employees</option>
            {recordEmployeeOptions.map((employee) => <option key={employee.employee_id} value={employee.employee_id}>{employee.name || employee.employee_id} ({employee.employee_id})</option>)}
          </select><button type="button" className="payroll-button payroll-button-outline" onClick={resetRecordFilters} disabled={!recordFiltersActive} title="Clear the employee, department and status filters"><Icon name="arrow-counterclockwise" /> Reset</button><span className="payroll-result-count">{visibleRows.length} records</span></div><PayrollTable statusFilter={recordStatusFilter} onStatusFilter={(value) => { setRecordStatusFilter(value); setReviewStatusFilter(value); }} statusOptions={recordStatusOptions} rows={pageRows} mode="monthly" money={money} onAction={handleStatusAction} onReview={setReviewRow} paymentRef={paymentRef} setPaymentRef={setPaymentRef} />
        <Pagination page={recordPage} totalItems={visibleRows.length} pageSize={pageSize} onPageChange={setRecordPage} /></section>
      </>}
      {mode === "payslips" && <section className="payroll-panel">
        <div className="payroll-section-heading"><div><h2><Icon name="file-earmark-pdf" /> Generated Payslips</h2><p>Only available payslips for the selected payroll period are shown.</p></div></div>
        <div className="payroll-toolbar"><label className="payroll-search"><Icon name="search" /><input type="search" aria-label="Search payroll data" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search employee ID, name" /></label><select className="form-select" aria-label="Filter by department" value={departmentFilter} onChange={(event) => { setDepartmentFilter(event.target.value); setPayslipEmployeeFilter(""); }} style={{ maxWidth: 240 }}>
            <option value="">All Departments</option>
            {departmentOptions.map((department) => <option key={department} value={department}>{department}</option>)}
          </select><select className="form-select" aria-label="Filter payslips by employee" value={payslipEmployeeFilter} onChange={(event) => setPayslipEmployeeFilter(event.target.value)} style={{ maxWidth: 260 }}>
            <option value="">All Employees</option>
            {payslipEmployeeOptions.map((employee) => <option key={employee.employee_id} value={employee.employee_id}>{employee.name || employee.employee_id} ({employee.employee_id})</option>)}
          </select><button type="button" className="payroll-button payroll-button-outline" onClick={resetPayslipFilters} disabled={!search && !departmentFilter && !payslipEmployeeFilter} title="Clear the search, department and employee filters"><Icon name="arrow-counterclockwise" /> Reset</button><span className="payroll-result-count">{visibleRows.length} payslips</span></div>
        <div className="payroll-table-wrap"><table className="payroll-table"><thead><tr>{["Employee ID", "Employee Name", "Department", "Gross Earnings", "Deductions", "Net Salary", "Status", "Action"].map((label) => <th key={label}>{label}</th>)}</tr></thead><tbody>
          {pageRows.map((row) => <tr key={row.id || row.payroll_record_id}><td className="payroll-id">{row.employee_id}</td><td>{row.employee_name || "—"}</td><td>{row.department || "—"}</td><td>{money(row.gross_earnings, row.currency)}</td><td>{money(row.total_deductions, row.currency)}</td><td className="payroll-net">{money(row.net_salary, row.currency)}</td><td><StatusBadge status={row.status} /></td><td><div className="payroll-row-actions"><button type="button" className="payroll-button payroll-button-outline" disabled={!(["Paid", "Available"].includes(String(row.status || "").trim())) || busy} title="View payslip PDF" onClick={() => handlePreviewPayslip(row)}><Icon name="eye" /> View</button><button type="button" className="payroll-button payroll-button-primary" disabled={!(["Paid", "Available"].includes(String(row.status || "").trim()))} title={!(["Paid", "Available"].includes(String(row.status || "").trim())) ? "Payslip download is available after payment is confirmed" : "Download payslip PDF"} onClick={() => downloadPayslip(row.payroll_record_id || row.id, row.employee_name, row.employee_id, row.period).catch((requestError) => setError(requestError.message || "Unable to download payslip."))}><Icon name="download" /> Download PDF</button></div></td></tr>)}
          {!visibleRows.length && <tr><td colSpan="8" className="payroll-empty"><Icon name="file-earmark-x" /><strong>No payslips found</strong><span>Choose another month or confirm payroll has been marked as paid.</span></td></tr>}
        </tbody></table></div>
        <Pagination page={recordPage} totalItems={visibleRows.length} pageSize={pageSize} onPageChange={setRecordPage} />
      </section>}
      {previewRow && (
        <PayslipViewer
          period={previewRow.period}
          subtitle={`${previewRow.employee_name || ""} · ${previewRow.employee_id}`}
          loadDetail={loadPreviewDetail}
          loadPdfUrl={loadPreviewPdf}
          onDownload={() => downloadPayslip(previewRecordId, previewRow.employee_name, previewRow.employee_id, previewRow.period)}
          onClose={closePreview}
        />
      )}
      {detailsEmployee && (() => {
        const attendance = attendanceSummary.find((row) => row.employee_id === detailsEmployee.employee_id);
        const value = (field) => attendance?.[field] ?? "—";
        return <div className="payroll-modal-backdrop" role="presentation" onClick={(event) => { if (event.target === event.currentTarget) setDetailsEmployeeId(""); }}>
          <section className="payroll-review-modal" role="dialog" aria-modal="true" aria-label={`Attendance details for ${detailsEmployee.name || detailsEmployee.employee_id}`}>
            <header className="payroll-review-modal-header">
              <div><h2><Icon name="calendar2-check" /> Attendance Details</h2><p>{detailsEmployee.name || detailsEmployee.employee_id} · {detailsEmployee.employee_id} · {MONTHS.find((item) => item.value === month)?.label} {year}</p></div>
              <button type="button" className="payroll-button payroll-button-outline" onClick={() => setDetailsEmployeeId("")}><Icon name="x-lg" /> Close</button>
            </header>
            <div className="payroll-review-detail-grid">
              <div><span>Department</span><strong>{detailsEmployee.department || "—"}</strong></div>
              <div><span>Present</span><strong>{value("present_days")}</strong></div>
              <div><span>Paid Leave</span><strong>{value("paid_leave_days")}</strong></div>
              <div><span>Unpaid Leave</span><strong>{value("unpaid_leave_days")}</strong></div>
              <div><span>Holidays</span><strong>{value("holiday_days")}</strong></div>
              <div><span>Weekly Off</span><strong>{value("weekly_off_days")}</strong></div>
              <div><span>Medical Leave</span><strong>{value("medical_leave_days")}</strong></div>
              <div><span>Absent</span><strong>{value("absent_days")}</strong></div>
              <div><span>Working Days</span><strong>{value("working_days")}</strong></div>
              <div><span>Payable Days (Review)</span><strong>{paidDays[detailsEmployee.employee_id] || value("paid_days")}</strong></div>
              <div><span>LOP Days (Review)</span><strong>{lopDays[detailsEmployee.employee_id] || value("lop_days")}</strong></div>
            </div>
            {!attendance && <div className="payroll-alert payroll-alert-warning"><Icon name="info-circle" /><span>No attendance summary is available for this employee in the selected month.</span></div>}
          </section>
        </div>;
      })()}
      {reviewRow && <div className="payroll-modal-backdrop" role="presentation" onClick={(event) => { if (event.target === event.currentTarget) setReviewRow(null); }}>
        <section className="payroll-review-modal" role="dialog" aria-modal="true" aria-label={`Review payroll for ${reviewRow.employee_name || reviewRow.employee_id}`}>
          <header className="payroll-review-modal-header">
            <div><h2><Icon name="clipboard-check" /> Payroll Review</h2><p>{reviewRow.employee_name || reviewRow.employee_id} · {reviewRow.period}</p></div>
            <button type="button" className="payroll-button payroll-button-outline" onClick={() => setReviewRow(null)}><Icon name="x-lg" /> Close</button>
          </header>
          <div className="payroll-review-detail-grid">
            <div><span>Employee ID</span><strong>{reviewRow.employee_id}</strong></div>
            <div><span>Department</span><strong>{reviewRow.department || "—"}</strong></div>
            <div><span>Designation</span><strong>{reviewRow.designation || "—"}</strong></div>
            <div><span>Payroll period</span><strong>{reviewRow.period}</strong></div>
            <div><span>Monthly salary</span><strong>{money(reviewRow.monthly_salary, reviewRow.currency)}</strong></div>
            <div><span>Paid days / Calendar days</span><strong>{reviewRow.paid_days ?? "—"} / {reviewRow.calendar_days ?? "—"}</strong></div>
            <div><span>Present days</span><strong>{reviewRow.present_days ?? "—"}</strong></div>
            <div><span>Paid leave days</span><strong>{reviewRow.paid_leave_days ?? "—"}</strong></div>
            <div><span>Unpaid leave days</span><strong>{reviewRow.unpaid_leave_days ?? "—"}</strong></div>
            <div><span>Holiday days</span><strong>{reviewRow.holiday_days ?? "—"}</strong></div>
            <div><span>Weekly off days</span><strong>{reviewRow.weekly_off_days ?? "—"}</strong></div>
            <div><span>Medical leave days</span><strong>{reviewRow.medical_leave_days ?? "—"}</strong></div>
            <div><span>Not applicable days (LOP)</span><strong>{reviewRow.not_applicable_days ?? "—"}</strong></div>
            <div><span>Absent / LOP days</span><strong>{reviewRow.absent_days ?? "—"} / {reviewRow.lop_days ?? "—"}</strong></div>
            <div><span>Working days</span><strong>{reviewRow.working_days ?? "—"}</strong></div>
            <div><span>Bank name</span><strong>{reviewRow.bank_name || reviewRow.salary_snapshot?.bank_name || "—"}</strong></div>
            <div><span>Bank account number</span><strong>{reviewRow.bank_account_number || reviewRow.salary_snapshot?.bank_account_number || "—"}</strong></div>
            <div><span>PAN</span><strong>{reviewRow.pan_card_number || reviewRow.salary_snapshot?.pan_card_number || "—"}</strong></div>
          </div>
          <div className="payroll-review-breakdown">
            <h3>Earnings</h3>
            <table className="payroll-table"><thead><tr><th>Component</th><th>Amount</th></tr></thead><tbody>{(reviewRow.earnings || []).map((item, index) => <tr key={`${item.name}-${index}`}><td>{item.name}</td><td>{money(item.amount, reviewRow.currency)}</td></tr>)}<tr><th>Gross earnings</th><th>{money(reviewRow.gross_earnings, reviewRow.currency)}</th></tr></tbody></table>
            <h3>Deductions</h3>
            <table className="payroll-table"><thead><tr><th>Component</th><th>Amount</th></tr></thead><tbody>{(reviewRow.deductions || []).map((item, index) => <tr key={`${item.name}-${index}`}><td>{item.name}</td><td>{money(item.amount, reviewRow.currency)}</td></tr>)}<tr><th>Total deductions</th><th>{money(reviewRow.total_deductions, reviewRow.currency)}</th></tr></tbody></table>
            <div className="payroll-review-net"><span>Net payable</span><strong>{money(reviewRow.net_salary, reviewRow.currency)}</strong></div>
          </div>
          <footer className="payroll-review-actions">
            <button type="button" className="payroll-button payroll-button-outline" disabled={busy} onClick={() => handleStatusAction(reviewRow, "disapprove")}><Icon name="x-circle" /> Disapprove & Recalculate</button>
            <button type="button" className="payroll-button payroll-button-primary" disabled={busy} onClick={() => handleStatusAction(reviewRow, "approve")}><Icon name="check-circle" /> Approve Payroll</button>
          </footer>
        </section>
      </div>}
    </main>
  );
}
function PayrollTable({ statusFilter, onStatusFilter, statusOptions = [], rows, mode, money: formatMoney, onAction, onReview, paymentRef, setPaymentRef }) {
  return <div className="payroll-table-wrap"><table className="payroll-table"><thead><tr>{["Employee ID", "Employee Name", "Department", "Gross Earnings", "Deductions", "Net Salary"].map((label) => <th key={label}>{label}</th>)}{onStatusFilter ? <th className="payroll-th-filter"><span>Status</span><select className="payroll-column-filter" aria-label="Filter by status" value={statusFilter} onChange={(event) => onStatusFilter(event.target.value)}><option value="">All statuses</option>{statusOptions.map((status) => <option key={status} value={status}>{status}</option>)}</select></th> : <th>Status</th>}<th>Actions</th></tr></thead><tbody>
    {rows.map((row) => <tr key={row.id || `${row.employee_id}-${row.month}-${row.year}`}><td className="payroll-id">{row.employee_id}</td><td>{row.employee_name || "—"}</td><td>{row.department || "—"}</td><td>{formatMoney(row.gross_earnings, row.currency)}</td><td>{formatMoney(row.total_deductions, row.currency)}</td><td className="payroll-net">{formatMoney(row.net_salary, row.currency)}</td><td><StatusBadge status={row.status} />{row.status === "Calculated" && row.attendance_outdated && <div className="small text-danger mt-1" title={row.attendance_outdated_reason || ""}>Attendance changed — disapprove &amp; recalculate</div>}</td><td><div className="payroll-row-actions">
      {mode !== "dashboard" && row.status === "Calculated" && <button type="button" className="payroll-button payroll-button-outline" onClick={() => onReview?.(row)}><Icon name="eye" /> Review</button>}
      {mode !== "dashboard" && row.status === "Approved" && <button type="button" className="payroll-button payroll-button-primary" onClick={() => onAction(row, "process")}><Icon name="gear-wide-connected" /> Process</button>}
      {mode !== "dashboard" && row.status === "Processed" && <><input className="payroll-payment-ref" aria-label={`Payment reference for ${row.employee_id}`} placeholder="Payment reference" value={paymentRef[row.id] || ""} onChange={(event) => setPaymentRef((state) => ({ ...state, [row.id]: event.target.value }))} /><button type="button" className="payroll-button payroll-button-primary" onClick={() => onAction(row, "paid")}><Icon name="check-circle" /> Mark Paid</button></>}
      {mode === "dashboard" && <StatusBadge status={row.status} />}
    </div></td></tr>)}
    {!rows.length && <tr><td colSpan="8" className="payroll-empty"><Icon name="inbox" /><strong>No payroll records yet</strong><span>Salary and payroll records will appear here when available.</span></td></tr>}
  </tbody></table></div>;
}
