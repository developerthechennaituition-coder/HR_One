import { useEffect, useMemo, useState } from "react";
import { getPayrollEmployees, getSalaryProfiles, saveSalaryProfile } from "../services/payrollApi";

const monthStart = () => `${new Date().getFullYear()}-${String(new Date().getMonth() + 1).padStart(2, "0")}-01`;
const defaultDeductions = () => [
  { name: "PF", amount: 0, percentage: 0, calculation_type: "fixed", category: "deduction", included_in_monthly_salary: false },
  { name: "Income Tax", amount: 0, percentage: 0, calculation_type: "fixed", category: "deduction", included_in_monthly_salary: false },
  { name: "Medical Insurance", amount: 0, percentage: 0, calculation_type: "fixed", category: "deduction", included_in_monthly_salary: false, fixed_amount: false },
];
const emptyForm = (employeeId = "") => ({
  employee_id: employeeId, monthly_salary: "", bank_name: "", bank_account_number: "", pan_card_number: "", currency: "INR",
  effective_from: monthStart(), components: defaultDeductions(), attendance_deduction_enabled: true,
  attendance_deduction_basis: "working_days", attendance_deduction_amount_per_day: "0", notes: "",
});
const inputStyle = { width: "100%", padding: "9px 10px", border: "1px solid #d5dbe5", borderRadius: 7, boxSizing: "border-box", background: "#fff", color: "#182230" };
const labelStyle = { display: "block", fontSize: 13, fontWeight: 600, marginBottom: 6, color: "#344054" };
const panelStyle = { background: "#fff", border: "1px solid #e4e7ec", borderRadius: 12, padding: 20, boxShadow: "0 1px 2px #1018280d", minWidth: 0 };
const money = (value, currency = "INR") => `${currency} ${Number(value || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function EmployeeSalarySetup() {
  const [employees, setEmployees] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [form, setForm] = useState(emptyForm());
  const [draft, setDraft] = useState({ name: "", category: "earning", calculation_type: "fixed", amount: "", percentage: "", included_in_monthly_salary: false, fixed_amount: false });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [employeeSearch, setEmployeeSearch] = useState("");
  const [departmentFilter, setDepartmentFilter] = useState("");
  const [activePanel, setActivePanel] = useState("");

  const departments = useMemo(() => [...new Set(employees.map((e) => String(e.department || "").trim()).filter(Boolean))].sort(), [employees]);
  const departmentEmployees = useMemo(() => departmentFilter ? employees.filter((e) => String(e.department || "").trim() === departmentFilter) : employees, [employees, departmentFilter]);
  const selectedEmployee = employees.find((e) => e.employee_id === form.employee_id);
  const selectedProfile = profiles.find((p) => p.employee_id === form.employee_id);
  const componentAmount = (item) => item.calculation_type === "percentage"
    ? Number(form.monthly_salary || 0) * Number(item.percentage || 0) / 100
    : Number(item.amount || 0);
  const earnings = form.components.filter((c) => c.category === "earning");
  const deductions = form.components.filter((c) => c.category === "deduction");
  const breakdown = earnings.filter((c) => c.included_in_monthly_salary);
  const includedTotal = breakdown.reduce((sum, c) => sum + componentAmount(c), 0);
  const extraEarnings = earnings.filter((c) => !c.included_in_monthly_salary).reduce((sum, c) => sum + componentAmount(c), 0);
  const totalDeductions = deductions.reduce((sum, c) => sum + componentAmount(c), 0);
  const grossEstimate = (breakdown.length ? includedTotal : Number(form.monthly_salary || 0)) + extraEarnings;
  const netEstimate = Math.max(0, grossEstimate - totalDeductions);

  async function loadData() {
    setLoading(true); setError("");
    try {
      const [employeeRows, profileRows] = await Promise.all([getPayrollEmployees(), getSalaryProfiles()]);
      setEmployees(employeeRows); setProfiles(profileRows);
      setForm((current) => current.employee_id ? current : emptyForm(employeeRows[0]?.employee_id || ""));
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }
  useEffect(() => { loadData(); }, []);

  function chooseEmployee(employeeId) {
    const profile = profiles.find((item) => item.employee_id === employeeId);
    setForm(profile ? {
      ...emptyForm(employeeId), ...profile,
      monthly_salary: String(profile.monthly_salary ?? ""), bank_name: profile.bank_name || "", bank_account_number: profile.bank_account_number || "", pan_card_number: profile.pan_card_number || "",
      effective_from: profile.effective_from || monthStart(),
      components: (() => {
        const savedComponents = (profile.components || []).filter((c) => !/\b(?:ot|overtime|over\s*time)\b/i.test(String(c.name || ""))).map((c) => ({
          ...c, amount: String(c.amount ?? 0), percentage: String(c.percentage ?? 0), calculation_type: c.calculation_type || "fixed",
          included_in_monthly_salary: Boolean(c.included_in_monthly_salary), fixed_amount: Boolean(c.fixed_amount),
        }));
        const existingNames = new Set(savedComponents.map((c) => String(c.name || "").trim().toLowerCase()));
        return [...savedComponents, ...defaultDeductions().filter((c) => !existingNames.has(c.name.toLowerCase()))];
      })(),
      attendance_deduction_enabled: profile.attendance_deduction_enabled !== false,
      attendance_deduction_basis: profile.attendance_deduction_basis || "working_days",
      attendance_deduction_amount_per_day: String(profile.attendance_deduction_amount_per_day ?? 0), notes: profile.notes || "",
    } : emptyForm(employeeId));
    setError(""); setMessage(""); setActivePanel("");
  }
  function setField(name, value) { setForm((current) => ({ ...current, [name]: value })); }
  function addComponent() {
    if (!draft.name.trim()) { setError("Enter a component name."); return; }
    const value = draft.calculation_type === "percentage" ? Number(draft.percentage) : Number(draft.amount);
    if (!Number.isFinite(value) || value < 0 || (draft.calculation_type === "percentage" && value > 1000)) { setError("Enter a valid non-negative amount or percentage."); return; }
    setForm((current) => ({ ...current, components: [...current.components, { name: draft.name.trim(), category: draft.category, calculation_type: draft.calculation_type, amount: draft.calculation_type === "fixed" ? value : 0, percentage: draft.calculation_type === "percentage" ? value : 0, included_in_monthly_salary: draft.category === "earning" && draft.included_in_monthly_salary }] }));
    setDraft({ name: "", category: draft.category, calculation_type: "fixed", amount: "", percentage: "", included_in_monthly_salary: false, fixed_amount: false }); setError("");
  }
  function updateComponent(index, changes) { setForm((current) => ({ ...current, components: current.components.map((c, i) => i === index ? { ...c, ...changes } : c) })); }
  async function handleSubmit(event) {
    event.preventDefault(); setSaving(true); setError(""); setMessage("");
    try {
      if (form.pan_card_number && !/^[A-Z]{5}[0-9]{4}[A-Z]$/i.test(form.pan_card_number.trim())) throw new Error("Enter PAN in the format ABCDE1234F.");
      if (breakdown.length && Math.abs(includedTotal - Number(form.monthly_salary || 0)) > 0.01) throw new Error("Salary breakdown components marked as included must total the Monthly Salary.");
      const payload = { ...form, monthly_salary: Number(form.monthly_salary), bank_name: form.bank_name.trim(), bank_account_number: form.bank_account_number.trim(), pan_card_number: form.pan_card_number.trim().toUpperCase(), attendance_deduction_amount_per_day: Number(form.attendance_deduction_amount_per_day || 0), components: form.components.filter((c) => !/\b(?:ot|overtime|over\s*time)\b/i.test(String(c.name || ""))).map((c) => ({ ...c, amount: Number(c.amount || 0), percentage: Number(c.percentage || 0) })) };
      const saved = await saveSalaryProfile(form.employee_id, payload);
      setProfiles((current) => [...current.filter((p) => p.employee_id !== saved.employee_id), saved].sort((a, b) => String(a.employee_name || a.employee_id).localeCompare(String(b.employee_name || b.employee_id))));
      setMessage("Employee salary details saved successfully.");
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  }
  const filteredEmployees = departmentEmployees.filter((e) => [e.employee_id, e.name, e.department, e.designation].some((v) => String(v || "").toLowerCase().includes(employeeSearch.trim().toLowerCase())));
  if (loading) return <div style={{ padding: 24 }}>Loading employee salary setup…</div>;

  return <div style={{ padding: "22px 24px", maxWidth: 1320, margin: "0 auto", color: "#182230" }}>
    <header style={{ marginBottom: 22 }}>
      <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 1, color: "#1769c2", textTransform: "uppercase" }}><i className="bi bi-cash-stack" style={{ marginRight: 7 }} />Payroll / Salary Administration</div>
      <h2 style={{ margin: "7px 0", fontSize: 25, color: "#142d4e", fontWeight: 750 }}><i className="bi bi-person-vcard" style={{ marginRight: 9, color: "#1769d2" }} />Employee Salary Setup</h2>
      <p style={{ margin: 0, color: "#667085" }}>Configure employee bank details, monthly salary structure, earnings, deductions and payroll rules.</p>
    </header>
    {error && <div role="alert" style={{ background: "#fff1f0", color: "#b42318", padding: 12, borderRadius: 8, marginBottom: 14 }}>{error}</div>}
    {message && <div role="status" style={{ background: "#ecfdf3", color: "#027a48", padding: 12, borderRadius: 8, marginBottom: 14 }}>{message}</div>}
    {!employees.length ? <div style={panelStyle}>No employees with an employee ID were found. Add employees in Employee Master first.</div> : <form className="salary-setup-form" onSubmit={handleSubmit} style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.55fr) minmax(280px, .85fr)", gap: 18, alignItems: "start" }}>
      <div style={{ display: "grid", gap: 18 }}>
        <section style={panelStyle}>
          <h3 style={{ margin: "0 0 16px", fontSize: 17, color: "#193a60" }}>Employee and Salary Details</h3>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(210px,1fr))", gap: 14 }}>
            <label><span style={labelStyle}>Department</span><select value={departmentFilter} onChange={(e) => { setDepartmentFilter(e.target.value); if (e.target.value && selectedEmployee?.department !== e.target.value) chooseEmployee(""); }} style={inputStyle}><option value="">All Departments</option>{departments.map((d) => <option key={d}>{d}</option>)}</select></label>
            <label><span style={labelStyle}>Employee *</span><select required value={form.employee_id} onChange={(e) => chooseEmployee(e.target.value)} style={inputStyle}><option value="">Select employee</option>{departmentEmployees.map((e) => <option key={e.employee_id} value={e.employee_id}>{e.name} ({e.employee_id})</option>)}</select></label>
            <label><span style={labelStyle}>Monthly Salary (INR) *</span><input required type="number" min="0.01" step="0.01" value={form.monthly_salary} onChange={(e) => setField("monthly_salary", e.target.value)} style={inputStyle} placeholder="Enter monthly salary" /></label>
            <label><span style={labelStyle}>Currency</span><select value={form.currency} onChange={(e) => setField("currency", e.target.value)} style={inputStyle}><option value="INR">INR - Indian Rupee</option><option value="USD">USD - US Dollar</option><option value="EUR">EUR - Euro</option></select></label>
            <label><span style={labelStyle}>Effective From *</span><input required type="month" value={String(form.effective_from || "").slice(0, 7)} onChange={(e) => setField("effective_from", e.target.value ? `${e.target.value}-01` : "")} style={inputStyle} /></label>
            <label><span style={labelStyle}>Bank Name</span><input autoComplete="off" maxLength={120} value={form.bank_name} onChange={(e) => setField("bank_name", e.target.value)} style={inputStyle} placeholder="Enter bank name" /></label>
            <label><span style={labelStyle}>Bank Account Number</span><input autoComplete="off" maxLength={40} value={form.bank_account_number} onChange={(e) => setField("bank_account_number", e.target.value)} style={inputStyle} placeholder="Enter bank account number" /></label>
            <label><span style={labelStyle}>PAN Card Number</span><input autoComplete="off" maxLength={10} value={form.pan_card_number} onChange={(e) => setField("pan_card_number", e.target.value.toUpperCase())} style={inputStyle} placeholder="ABCDE1234F" /></label>
          </div>
          {selectedEmployee && <div style={{ marginTop: 14, padding: 12, borderRadius: 8, background: "#f8fafc", color: "#475467", fontSize: 13 }}>Department: <b>{selectedEmployee.department || "Not specified"}</b> · Designation: <b>{selectedEmployee.designation || "Not specified"}</b></div>}
        </section>
        <section style={panelStyle}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}><div><h3 style={{ margin: 0, fontSize: 17, color: "#193a60" }}>Salary Structure</h3><p style={{ margin: "5px 0 14px", color: "#667085", fontSize: 13 }}>Use the information icon for salary breakdown, plus for additional earnings, and minus for deductions.</p></div></div>
          <div style={{ display: "flex", gap: 9, flexWrap: "wrap", marginBottom: 15 }}>
            <button type="button" onClick={() => setActivePanel(activePanel === "breakdown" ? "" : "breakdown")} style={actionButton("#eaf2ff", "#1d4ed8")}><span style={roundIcon}>ⓘ</span> Salary breakdown</button>
            <button type="button" onClick={() => { setDraft({ name: "", category: "earning", calculation_type: "fixed", amount: "", percentage: "", included_in_monthly_salary: false }); setActivePanel(activePanel === "earning" ? "" : "earning"); }} style={actionButton("#e8f8ef", "#067647")}><span style={roundIcon}>＋</span> Additional earning</button>
            <button type="button" onClick={() => { setDraft({ name: "", category: "deduction", calculation_type: "fixed", amount: "0", percentage: "0", included_in_monthly_salary: false }); setActivePanel(activePanel === "deduction" ? "" : "deduction"); }} style={actionButton("#fff0ef", "#b42318")}><span style={roundIcon}>−</span> Add deduction</button>
          </div>
          {activePanel && <div style={{ padding: 14, marginBottom: 16, border: "1px solid #dbe4f0", borderRadius: 10, background: "#f9fbff" }}>
            {activePanel === "breakdown" ? <><strong>Monthly salary breakdown</strong><p style={{ margin: "6px 0 10px", fontSize: 13, color: "#667085" }}>Add Basic, HRA, TA and Special Allowance as earnings, select “Included in monthly salary”, and ensure their total equals the monthly salary.</p><button type="button" onClick={() => { setDraft({ name: "Basic", category: "earning", calculation_type: "percentage", amount: "", percentage: "", included_in_monthly_salary: true }); setActivePanel("earning"); }} style={actionButton("#eaf2ff", "#1d4ed8")}>＋ Add salary component</button></> : <>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(140px,1fr))", gap: 10, alignItems: "end" }}>
                <label><span style={labelStyle}>Component name</span><input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} style={inputStyle} placeholder={activePanel === "deduction" ? "e.g. PF" : "e.g. Shift allowance"} /></label>
                <label><span style={labelStyle}>Calculation</span><select value={draft.calculation_type} onChange={(e) => setDraft({ ...draft, calculation_type: e.target.value })} style={inputStyle}><option value="fixed">Fixed amount</option><option value="percentage">Percentage of monthly salary</option></select></label>
                {draft.calculation_type === "percentage" ? <label><span style={labelStyle}>Percentage (%)</span><input type="number" min="0" max="1000" step="0.01" value={draft.percentage} onChange={(e) => setDraft({ ...draft, percentage: e.target.value })} style={inputStyle} /></label> : <label><span style={labelStyle}>Amount</span><input type="number" min="0" step="0.01" value={draft.amount} onChange={(e) => setDraft({ ...draft, amount: e.target.value })} style={inputStyle} /></label>}
                {activePanel === "earning" && <label style={{ display: "flex", gap: 8, alignItems: "center", paddingBottom: 9, fontSize: 13 }}><input type="checkbox" checked={draft.included_in_monthly_salary} onChange={(e) => setDraft({ ...draft, included_in_monthly_salary: e.target.checked })} /> Included in monthly salary</label>}
                <label style={{ display: "flex", gap: 8, alignItems: "center", paddingBottom: 9, fontSize: 13 }}><input type="checkbox" checked={Boolean(draft.fixed_amount)} onChange={(e) => setDraft({ ...draft, fixed_amount: e.target.checked, calculation_type: e.target.checked ? "fixed" : draft.calculation_type, amount: e.target.checked && draft.calculation_type === "percentage" ? String((Number(form.monthly_salary || 0) * Number(draft.percentage || 0) / 100).toFixed(2)) : draft.amount, percentage: e.target.checked ? "0" : draft.percentage })} /> Fixed Amount (lock configured value)</label>
                <button type="button" onClick={addComponent} style={{ ...actionButton(activePanel === "deduction" ? "#fff0ef" : "#e8f8ef", activePanel === "deduction" ? "#b42318" : "#067647"), minHeight: 38 }}>Add component</button>
              </div>
            </>}</div>}
          {form.components.length > 0 && <div style={{ overflowX: "auto" }}><table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}><thead><tr style={{ background: "#f8fafc", textAlign: "left" }}><th style={cell}>Component</th><th style={cell}>Type</th><th style={cell}>Calculation</th><th style={{ ...cell, textAlign: "right" }}>Calculated amount</th><th style={cell}>Included in salary</th><th style={cell}>Fixed amount</th><th style={cell}></th></tr></thead><tbody>{form.components.map((c, i) => <tr key={`${c.name}-${i}`} style={{ borderTop: "1px solid #eaecf0" }}><td style={cell}><input disabled={Boolean(c.fixed_amount)} aria-label="Component name" value={c.name} onChange={(e) => updateComponent(i, { name: e.target.value })} style={{ ...inputStyle, minWidth: 115 }} /></td><td style={cell}><select value={c.category} onChange={(e) => updateComponent(i, { category: e.target.value, included_in_monthly_salary: e.target.value === "earning" && c.included_in_monthly_salary })} style={inputStyle}><option value="earning">Earning</option><option value="deduction">Deduction</option></select></td><td style={cell}><select disabled={Boolean(c.fixed_amount)} value={c.calculation_type || "fixed"} onChange={(e) => updateComponent(i, { calculation_type: e.target.value })} style={inputStyle}><option value="fixed">Amount: {money(c.amount, form.currency)}</option><option value="percentage">{Number(c.percentage || 0)}%</option></select>{c.calculation_type === "percentage" ? <input disabled={Boolean(c.fixed_amount)} aria-label="Percentage" type="number" min="0" max="1000" step="0.01" value={c.percentage} onChange={(e) => updateComponent(i, { percentage: e.target.value })} style={{ ...inputStyle, marginTop: 5 }} /> : <input disabled={Boolean(c.fixed_amount)} aria-label="Fixed amount" type="number" min="0" step="0.01" value={c.amount} onChange={(e) => updateComponent(i, { amount: e.target.value })} style={{ ...inputStyle, marginTop: 5 }} />}</td><td style={{ ...cell, textAlign: "right", whiteSpace: "nowrap" }}>{money(componentAmount(c), form.currency)}</td><td style={cell}>{c.category === "earning" ? <input aria-label="Included in monthly salary" type="checkbox" checked={Boolean(c.included_in_monthly_salary)} onChange={(e) => updateComponent(i, { included_in_monthly_salary: e.target.checked })} /> : "—"}</td><td style={cell}><input type="checkbox" aria-label={`Lock ${c.name} amount`} checked={Boolean(c.fixed_amount)} onChange={(e) => updateComponent(i, { fixed_amount: e.target.checked, ...(e.target.checked && c.calculation_type === "percentage" ? { calculation_type: "fixed", amount: componentAmount(c), percentage: 0 } : {}) })} /></td><td style={cell}><button type="button" disabled={Boolean(c.fixed_amount)} title={c.fixed_amount ? "Unlock this component before removing it" : `Remove ${c.name}`} onClick={() => setField("components", form.components.filter((_, j) => i !== j))} style={{ border: 0, background: "transparent", color: "#b42318", cursor: "pointer" }} aria-label={`Remove ${c.name}`}><i className="bi bi-trash3" /></button></td></tr>)}</tbody></table></div>}
          {breakdown.length > 0 && <div style={{ marginTop: 10, fontSize: 13, color: Math.abs(includedTotal - Number(form.monthly_salary || 0)) > .01 ? "#b42318" : "#067647" }}>Included salary components: <b>{money(includedTotal, form.currency)}</b> / Monthly salary {money(form.monthly_salary, form.currency)} {Math.abs(includedTotal - Number(form.monthly_salary || 0)) > .01 ? "— totals must match before saving." : "— matched."}</div>}
          <label style={{ display: "block", marginTop: 16 }}><span style={labelStyle}>Notes for HR / Payroll</span><textarea rows={3} maxLength={1000} value={form.notes} onChange={(e) => setField("notes", e.target.value)} style={{ ...inputStyle, resize: "vertical" }} /></label>
        </section>
        <section style={panelStyle}>
          <h3 style={{ margin: "0 0 14px", fontSize: 17, color: "#193a60" }}>Attendance-based Deduction</h3>
          <label style={{ display: "flex", gap: 9, alignItems: "center", fontSize: 14, marginBottom: 14 }}><input type="checkbox" checked={form.attendance_deduction_enabled} onChange={(e) => setField("attendance_deduction_enabled", e.target.checked)} /> Enable attendance-based deductions</label>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(210px,1fr))", gap: 12 }}><label><span style={labelStyle}>Deduction basis</span><select disabled={!form.attendance_deduction_enabled} value={form.attendance_deduction_basis} onChange={(e) => setField("attendance_deduction_basis", e.target.value)} style={inputStyle}><option value="working_days">Working days</option><option value="calendar_days">Calendar days</option><option value="fixed_amount">Fixed amount per absent day</option></select></label><label><span style={labelStyle}>Amount per absent day</span><input disabled={!form.attendance_deduction_enabled || form.attendance_deduction_basis !== "fixed_amount"} type="number" min="0" step="0.01" value={form.attendance_deduction_amount_per_day} onChange={(e) => setField("attendance_deduction_amount_per_day", e.target.value)} style={inputStyle} /></label></div>
        </section>
      </div>
      <aside style={{ display: "grid", gap: 16 }}>
        <section style={{ ...panelStyle, background: "#f8fafc" }}><h3 style={{ margin: "0 0 14px", fontSize: 17, color: "#193a60" }}>Salary Summary</h3>{[["Monthly salary", money(form.monthly_salary, form.currency)], ["Included salary breakdown", money(breakdown.length ? includedTotal : Number(form.monthly_salary || 0), form.currency)], ["Additional earnings", money(extraEarnings, form.currency)], ["Gross earnings estimate", money(grossEstimate, form.currency)], ["Total deductions", money(totalDeductions, form.currency)]].map(([label, value]) => <div key={label} style={{ display: "flex", justifyContent: "space-between", gap: 10, margin: "12px 0", fontSize: 13 }}><span>{label}</span><b style={{ textAlign: "right" }}>{value}</b></div>)}<hr style={{ border: 0, borderTop: "1px solid #d0d5dd" }} /><div style={{ display: "flex", justifyContent: "space-between", gap: 10, marginTop: 12, fontSize: 15 }}><b>Net payable estimate</b><b style={{ color: "#067647" }}>{money(netEstimate, form.currency)}</b></div><p style={{ color: "#667085", fontSize: 12, lineHeight: 1.5 }}>Final payroll may vary based on attendance, approved leave, and payroll period rules.</p></section>
        <button type="submit" disabled={saving || !form.employee_id} style={{ padding: "12px 16px", border: 0, borderRadius: 8, background: saving ? "#98a2b3" : "#2457c5", color: "white", fontWeight: 700, cursor: saving ? "wait" : "pointer" }}>{saving ? "Saving…" : "Save Salary Details"}</button>
      </aside>
    </form>}
    {!!employees.length && <section className="payroll-panel salary-profile-list-panel" style={{ marginTop: 20 }}><div className="payroll-section-heading"><div><h2><i className="bi bi-people-fill" style={{ marginRight: 7 }} />Employee Salary Profiles</h2><p>Review saved salary details and select an employee to update their profile.</p></div><span className="payroll-result-count">{profiles.length} saved profile(s) / {employees.length} employee(s)</span></div><label className="payroll-search"><i className="bi bi-search" aria-hidden="true" /><input value={employeeSearch} onChange={(e) => setEmployeeSearch(e.target.value)} placeholder="Search employee ID, name, department or designation…" /></label><div className="payroll-table-wrap" style={{ marginTop: 13 }}><table className="payroll-table"><thead><tr><th>Employee ID</th><th>Employee Name</th><th>Department</th><th>Designation</th><th>Monthly Salary</th><th>Effective From</th><th>Status</th><th>Action</th></tr></thead><tbody>{filteredEmployees.map((e) => { const p = profiles.find((x) => x.employee_id === e.employee_id); return <tr key={e.employee_id}><td className="payroll-id">{e.employee_id}</td><td>{e.name || "—"}</td><td>{e.department || "—"}</td><td>{e.designation || "—"}</td><td>{p ? money(p.monthly_salary, p.currency || "INR") : "—"}</td><td>{p?.effective_from || "—"}</td><td><span className={`payroll-status ${p ? "payroll-status-approved" : "payroll-status-pending"}`}>{p ? "Configured" : "Not configured"}</span></td><td><button type="button" className="payroll-button payroll-button-outline" onClick={() => { chooseEmployee(e.employee_id); window.scrollTo({ top: 0, behavior: "smooth" }); }}><i className="bi bi-pencil-square" /> {p ? "Edit" : "Set salary"}</button></td></tr>; })}{!filteredEmployees.length && <tr><td colSpan="8" className="payroll-empty">No employees match this search.</td></tr>}</tbody></table></div></section>}
  </div>;
}
const cell = { padding: 8, verticalAlign: "middle" };
const roundIcon = { display: "inline-grid", placeItems: "center", width: 22, height: 22, borderRadius: "50%", background: "#fff", marginRight: 4, fontWeight: 800 };
function actionButton(background, color) { return { display: "inline-flex", alignItems: "center", gap: 5, border: "1px solid #e4e7ec", borderRadius: 8, padding: "8px 11px", background, color, fontWeight: 700, cursor: "pointer" }; }
