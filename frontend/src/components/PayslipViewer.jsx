import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

const Icon = ({ name }) => <i className={`bi bi-${name}`} aria-hidden="true" />;

const ATTENDANCE_LABELS = [
  ["calendar_days", "Calendar days"], ["paid_days", "Paid days"], ["lop_days", "LOP days"],
  ["working_days", "Working days"], ["present_days", "Present"], ["paid_leave_days", "Paid leave"],
  ["unpaid_leave_days", "Unpaid leave"], ["absent_days", "Absent"], ["holiday_days", "Holidays"],
  ["weekly_off_days", "Weekly off"], ["medical_leave_days", "Medical leave"], ["not_applicable_days", "Not applicable"],
];

// Mobile browsers cannot render a PDF inside an iframe, so phones/tablets draw
// the PDF pages with PDF.js; desktop keeps the browser's built-in PDF viewer.
const prefersNativePdf = () =>
  typeof window !== "undefined" && window.matchMedia?.("(min-width: 992px) and (pointer: fine)").matches;

const ZOOM_STEPS = [1, 1.5, 2, 3];

// Renders every page of the PDF onto canvases sized to the pane width.
function PdfPages({ url, title }) {
  const paneRef = useRef(null);
  const pagesRef = useRef(null);
  const [doc, setDoc] = useState(null);
  const [error, setError] = useState("");
  const [width, setWidth] = useState(0);
  const [zoom, setZoom] = useState(0);

  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(paneRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let active = true;
    let task;
    let loaded;
    (async () => {
      const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
      const { default: workerSrc } = await import("pdfjs-dist/legacy/build/pdf.worker.min.mjs?url");
      pdfjs.GlobalWorkerOptions.workerSrc = workerSrc;
      task = pdfjs.getDocument(url);
      loaded = await task.promise;
      if (active) setDoc(loaded);
    })().catch((err) => { if (active) setError(err?.message || "Unable to display the PDF."); });
    return () => { active = false; task?.destroy(); };
  }, [url]);

  useEffect(() => {
    const holder = pagesRef.current;
    if (!doc || !width || !holder) return undefined;
    let active = true;
    const tasks = [];
    (async () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const canvases = [];
      for (let number = 1; number <= doc.numPages; number += 1) {
        const page = await doc.getPage(number);
        if (!active) return;
        const scale = ((width - 16) / page.getViewport({ scale: 1 }).width) * ZOOM_STEPS[zoom];
        const viewport = page.getViewport({ scale: scale * ratio });
        const canvas = document.createElement("canvas");
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        canvas.style.width = `${Math.floor(viewport.width / ratio)}px`;
        canvas.setAttribute("role", "img");
        canvas.setAttribute("aria-label", `${title} — page ${number} of ${doc.numPages}`);
        const task = page.render({ canvas, canvasContext: canvas.getContext("2d"), viewport });
        tasks.push(task);
        await task.promise;
        canvases.push(canvas);
      }
      if (active) holder.replaceChildren(...canvases);
    })().catch((err) => {
      if (active && err?.name !== "RenderingCancelledException") setError(err?.message || "Unable to display the PDF.");
    });
    return () => { active = false; tasks.forEach((task) => task.cancel()); };
  }, [doc, width, zoom, title]);

  return (
    <div className="payslip-pdf-canvas-pane" ref={paneRef}>
      {error ? <div className="payslip-message payslip-message-error">{error}</div> : (
        <>
          {!doc && <div className="payslip-message">Loading PDF preview…</div>}
          <div className="payslip-pdf-pages" ref={pagesRef} />
          {doc && (
            <div className="payslip-pdf-zoom" role="group" aria-label="PDF zoom">
              <button type="button" onClick={() => setZoom((value) => Math.max(0, value - 1))} disabled={zoom === 0} aria-label="Zoom out"><Icon name="zoom-out" /></button>
              <span aria-live="polite">{ZOOM_STEPS[zoom] * 100}%</span>
              <button type="button" onClick={() => setZoom((value) => Math.min(ZOOM_STEPS.length - 1, value + 1))} disabled={zoom === ZOOM_STEPS.length - 1} aria-label="Zoom in"><Icon name="zoom-in" /></button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

const money = (value, currency = "INR") =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency: currency || "INR", maximumFractionDigits: 2 }).format(Number(value || 0));

const periodLabel = (period) => {
  const [year, month] = String(period || "").split("-").map(Number);
  return year && month ? new Date(Date.UTC(year, month - 1, 1)).toLocaleDateString("en-IN", { month: "long", year: "numeric", timeZone: "UTC" }) : period || "—";
};

const dateLabel = (iso) => {
  if (!iso) return "—";
  const [year, month, day] = String(iso).slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
};

function PayslipViewer({ period, subtitle, loadDetail, loadPdfUrl, onDownload, onClose }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const [view, setView] = useState("pdf");
  const [nativePdf] = useState(prefersNativePdf);
  const [pdfUrl, setPdfUrl] = useState("");
  const [pdfError, setPdfError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const closeRef = useRef(null);
  const pdfUrlRef = useRef("");

  useEffect(() => {
    let active = true;
    loadDetail()
      .then((data) => { if (active) setDetail(data); })
      .catch((err) => { if (active) setError(err.message || "Unable to load the payslip."); });
    return () => { active = false; };
  }, [loadDetail]);

  useEffect(() => {
    if (view !== "pdf" || pdfUrlRef.current) return undefined;
    let active = true;
    loadPdfUrl()
      .then((url) => {
        if (!active) { URL.revokeObjectURL(url); return; }
        pdfUrlRef.current = url;
        setPdfUrl(url);
      })
      .catch((err) => { if (active) setPdfError(err.message || "Unable to load the PDF preview."); });
    return () => { active = false; };
  }, [view, loadPdfUrl]);

  // Lock background scrolling (the app scrolls inside .main-content) and support Escape.
  useEffect(() => {
    const scroller = document.querySelector(".main-content");
    const previous = [document.body.style.overflow, scroller?.style.overflow];
    document.body.style.overflow = "hidden";
    if (scroller) scroller.style.overflow = "hidden";
    closeRef.current?.focus();
    const onKey = (event) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previous[0];
      if (scroller) scroller.style.overflow = previous[1] || "";
      document.removeEventListener("keydown", onKey);
      if (pdfUrlRef.current) URL.revokeObjectURL(pdfUrlRef.current);
    };
  }, [onClose]);

  const download = async () => {
    setDownloading(true);
    try {
      await onDownload();
    } catch (err) {
      setError(err.message || "Unable to download the payslip.");
    } finally {
      setDownloading(false);
    }
  };

  const currency = detail?.currency || "INR";
  const attendance = detail?.attendance || {};
  const employeeRows = detail ? [
    ["Employee name", detail.employee_name], ["Employee ID", detail.employee_id],
    ["Designation", detail.designation], ["Department", detail.department],
    ["Pay period", periodLabel(detail.period)], ["Pay date", dateLabel(detail.pay_date)],
    ["Bank name", detail.bank_name], ["Bank account number", detail.bank_account_number],
    ["PAN", detail.pan_card_number], ["Payment reference", detail.payment_reference],
  ].filter(([, value]) => value !== undefined && value !== null && String(value).trim() !== "") : [];

  const sheet = detail && (
    <article className="payslip-sheet">
      <div className="payslip-sheet-head">
        <div>
          <h3>{detail.company?.name || detail.company?.company_name}</h3>
          {detail.company?.address && <p>{detail.company.address}</p>}
        </div>
        <div className="payslip-sheet-period">
          <span>Payslip for the month of</span>
          <strong>{periodLabel(detail.period)}</strong>
        </div>
      </div>

      <div className="payslip-net-card">
        <span>Total net pay</span>
        <strong>{money(detail.net_salary, currency)}</strong>
        <small>{detail.net_salary_in_words}</small>
      </div>

      <section aria-label="Employee details">
        <h4>Employee details</h4>
        <dl className="payslip-details">
          {employeeRows.map(([label, value]) => (
            <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
          ))}
        </dl>
      </section>

      {Object.keys(attendance).length > 0 && (
        <section aria-label="Attendance summary">
          <h4>Attendance summary</h4>
          <dl className="payslip-attendance">
            {ATTENDANCE_LABELS.filter(([key]) => attendance[key] !== undefined).map(([key, label]) => (
              <div key={key}><dt>{label}</dt><dd>{attendance[key]}</dd></div>
            ))}
          </dl>
        </section>
      )}

      <div className="payslip-components">
        {[["Earnings", detail.earnings, "Gross earnings", detail.gross_earnings], ["Deductions", detail.deductions, "Total deductions", detail.total_deductions]].map(([title, items, totalLabel, totalValue]) => (
          <section key={title} className="payslip-component-card" aria-label={title}>
            <h4>{title}</h4>
            <ul>
              {(items || []).length ? items.map((item, index) => (
                <li key={`${item.name}-${index}`}><span>{item.name}</span><span className="payslip-amount">{money(item.amount, currency)}</span></li>
              )) : <li className="text-muted"><span>None</span><span /></li>}
            </ul>
            <div className="payslip-component-total"><span>{totalLabel}</span><span className="payslip-amount">{money(totalValue, currency)}</span></div>
          </section>
        ))}
      </div>

      <div className="payslip-net-row">
        <div><strong>Total net payable</strong><small>Gross earnings − Total deductions</small></div>
        <span className="payslip-amount">{money(detail.net_salary, currency)}</span>
      </div>
      <p className="payslip-footer">This is a system-generated payslip.</p>
    </article>
  );

  return (
    <div className="payroll-modal-backdrop" role="presentation" onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="payroll-payslip-modal" role="dialog" aria-modal="true" aria-label={`Payslip ${period}`}>
        <header>
          <div className="payslip-modal-title">
            <h2><Icon name="file-earmark-text" /> Payslip — {periodLabel(period)}</h2>
            <p>{subtitle}</p>
          </div>
          <div className="payslip-modal-actions">
            <div className="payslip-view-toggle" role="group" aria-label="Payslip view">
              <button type="button" className={view === "summary" ? "active" : ""} aria-pressed={view === "summary"} onClick={() => setView("summary")}>Payslip</button>
              <button type="button" className={view === "pdf" ? "active" : ""} aria-pressed={view === "pdf"} onClick={() => setView("pdf")}>PDF</button>
            </div>
            <button type="button" className="payroll-button payroll-button-primary" onClick={download} disabled={downloading} aria-label="Download PDF">
              <Icon name="download" /> {downloading ? "Downloading…" : <>Download<span className="d-none d-sm-inline"> PDF</span></>}
            </button>
            <button type="button" className="payroll-button payroll-button-outline" onClick={() => window.print()} disabled={!detail}>
              <Icon name="printer" /> Print
            </button>
            <button type="button" ref={closeRef} className="payroll-button payroll-button-outline" onClick={onClose}>
              <Icon name="x-lg" /> Close
            </button>
          </div>
        </header>

        {view === "pdf" && (
          <div className="payslip-pdf-pane">
            {pdfError ? <div className="payslip-message payslip-message-error">{pdfError}</div>
              : !pdfUrl ? <div className="payslip-message">Loading PDF preview…</div>
                : nativePdf ? <iframe title={`Payslip PDF ${period}`} src={pdfUrl} />
                  : <PdfPages url={pdfUrl} title={`Payslip PDF ${period}`} />}
          </div>
        )}

        <div className={`payslip-view-body ${view === "pdf" ? "payslip-screen-hidden" : ""}`}>
          {error && <div className="payslip-message payslip-message-error" role="alert">{error}</div>}
          {!detail && !error && <div className="payslip-message">Loading payslip…</div>}
          {sheet}
        </div>
      </section>
      {/* Printed from outside the app shell, whose fixed-height layout would clip it to one page. */}
      {sheet && createPortal(<div className="payslip-print-root">{sheet}</div>, document.body)}
    </div>
  );
}

export default PayslipViewer;
