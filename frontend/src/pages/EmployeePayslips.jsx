import { useEffect, useMemo, useState } from "react";
import { downloadMyPayslip, getMyPayslips, viewMyPayslip } from "../services/payrollApi";

const money = (value, currency = "INR") =>
  `${currency} ${Number(value || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function Icon({ name }) {
  return <i aria-hidden="true" className={`bi bi-${name}`} />;
}

export default function EmployeePayslips({ session }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [viewUrl, setViewUrl] = useState("");
  const [selected, setSelected] = useState(null);
  const [busyId, setBusyId] = useState("");

  useEffect(() => {
    let alive = true;
    getMyPayslips()
      .then((data) => { if (alive) setRows(Array.isArray(data) ? data : []); })
      .catch((e) => { if (alive) setError(e.message || "Unable to load your payslips."); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  useEffect(() => () => { if (viewUrl) URL.revokeObjectURL(viewUrl); }, [viewUrl]);

  const visibleRows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((row) => !q || `${row.period} ${row.status} ${row.employee_id}`.toLowerCase().includes(q));
  }, [rows, search]);

  async function handleView(row) {
    setBusyId(row.id); setError("");
    try {
      if (viewUrl) URL.revokeObjectURL(viewUrl);
      const url = await viewMyPayslip(row.id);
      setViewUrl(url); setSelected(row);
    } catch (e) {
      setError(e.message || "Unable to preview the payslip.");
    } finally { setBusyId(""); }
  }

  async function handleDownload(row) {
    setBusyId(row.id); setError("");
    try { await downloadMyPayslip(row.id, row.employee_name, row.employee_id,row.period); }
    catch (e) { setError(e.message || "Unable to download the payslip."); }
    finally { setBusyId(""); }
  }

  return (
    <main className="payroll-page">
      <header className="payroll-page-header">
        <div>
          <div className="payroll-eyebrow"><Icon name="file-earmark-pdf" /> HR ONE / PAYROLL</div>
          <h1>My Payslips</h1>
          <p>View your own finalized salary, attendance summary and payslip PDF.</p>
        </div>
      </header>

      {error && <div className="payroll-alert payroll-alert-error" role="alert"><Icon name="exclamation-circle-fill" /> <span>{error}</span></div>}

      <section className="payroll-summary-grid" aria-label="My salary summary">
        <article className="payroll-summary-card payroll-summary-blue"><div className="payroll-summary-icon"><Icon name="file-earmark-check" /></div><div className="payroll-summary-copy"><span>Available Payslips</span><strong>{rows.length}</strong></div></article>
        <article className="payroll-summary-card payroll-summary-green"><div className="payroll-summary-icon"><Icon name="wallet2" /></div><div className="payroll-summary-copy"><span>Latest Net Salary</span><strong>{rows[0] ? money(rows[0].net_salary, rows[0].currency) : "—"}</strong></div></article>
        <article className="payroll-summary-card payroll-summary-purple"><div className="payroll-summary-icon"><Icon name="calendar2-check" /></div><div className="payroll-summary-copy"><span>Latest Paid Days</span><strong>{rows[0]?.paid_days ?? "—"}</strong><small>LOP: {rows[0]?.lop_days ?? "—"} day(s)</small></div></article>
      </section>

      <section className="payroll-panel">
        <div className="payroll-section-heading"><div><h2><Icon name="file-earmark-pdf" /> Your Payslip History</h2><p>Only payslips associated with your signed-in employee account are displayed.</p></div></div>
        <div className="payroll-toolbar">
          <label className="payroll-search"><Icon name="search" /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search month or year…" /></label>
          <span className="payroll-result-count">{visibleRows.length} payslip(s)</span>
        </div>
        {loading ? <div className="payroll-empty"><Icon name="arrow-repeat" /><strong>Loading your payslips…</strong></div> :
          <div className="payroll-table-wrap"><table className="payroll-table">
            <thead><tr>{["Pay Period", "Working Days", "Present Days", "Paid Leave", "Unpaid Leave", "Absent Days", "Paid Days", "LOP Days", "Gross Earnings", "Deductions", "Net Salary", "Status", "Actions"].map((label) => <th key={label}>{label}</th>)}</tr></thead>
            <tbody>
              {visibleRows.map((row) => <tr key={row.id}>
                <td className="payroll-id">{row.period}</td><td>{row.working_days ?? 0}</td><td>{row.present_days ?? 0}</td><td>{row.paid_leave_days ?? 0}</td><td>{row.unpaid_leave_days ?? 0}</td><td>{row.absent_days ?? 0}</td><td>{row.paid_days ?? 0}</td><td>{row.lop_days ?? 0}</td>
                <td>{money(row.gross_earnings, row.currency)}</td><td>{money(row.total_deductions, row.currency)}</td><td className="payroll-net">{money(row.net_salary, row.currency)}</td>
                <td><span className="payroll-status payroll-status-paid">{row.status}</span></td>
                <td><div className="payroll-row-actions"><button type="button" className="payroll-button payroll-button-outline" disabled={busyId === row.id} onClick={() => handleView(row)}><Icon name="eye" /> View</button><button type="button" className="payroll-button payroll-button-primary" disabled={busyId === row.id} onClick={() => handleDownload(row)}><Icon name="download" /> PDF</button></div></td>
              </tr>)}
              {!visibleRows.length && <tr><td colSpan="13" className="payroll-empty"><Icon name="file-earmark-x" /><strong>{loading ? "Loading…" : "No payslips available yet"}</strong><span>Approved and paid payslips will appear here.</span></td></tr>}
            </tbody>
          </table></div>}
      </section>

      {viewUrl && selected && <div className="payroll-modal-backdrop" role="presentation" onClick={(event) => { if (event.target === event.currentTarget) { URL.revokeObjectURL(viewUrl); setViewUrl(""); setSelected(null); } }}>
        <section className="payroll-payslip-modal" role="dialog" aria-modal="true" aria-label={`Payslip ${selected.period}`}>
          <header><div><h2><Icon name="file-earmark-pdf" /> Payslip — {selected.period}</h2><p>{session?.user?.name || selected.employee_id}</p></div><button type="button" className="payroll-button payroll-button-outline" onClick={() => { URL.revokeObjectURL(viewUrl); setViewUrl(""); setSelected(null); }}><Icon name="x-lg" /> Close</button></header>
          <iframe title={`Payslip PDF ${selected.period}`} src={viewUrl} />
        </section>
      </div>}
    </main>
  );
}
