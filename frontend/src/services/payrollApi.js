import { HRONE_API_BASE } from "../config/api.js";
import { authenticatedFetch } from "./authenticatedFetch";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || HRONE_API_BASE;
const BASE = `${API_BASE_URL}/api/payroll`;

async function request(path, options = {}) {
  const response = await authenticatedFetch(`${BASE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Payroll request failed.");
  return data;
}

export const getPayrollEmployees = () => request("/employees");
export const getSalaryProfiles = () => request("/salary-profiles");
export const saveSalaryProfile = (employeeId, payload) =>
  request(`/salary-profiles/${encodeURIComponent(employeeId)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
export const getPayrollRecords = (month, year) => request(`/records?month=${month}&year=${year}`);
export const calculatePayroll = (payload) => request('/calculate', { method: 'POST', body: JSON.stringify(payload) });
export const updatePayrollStatus = (recordId, action, payment_reference = '') => request(`/records/${encodeURIComponent(recordId)}/action`, { method: 'POST', body: JSON.stringify({ action, payment_reference }) });
export const getPayslips = (month, year) => request(`/payslips?month=${month}&year=${year}`);
export async function downloadPayslip(recordId, employeeName,employeeId,period) {
  const response = await authenticatedFetch(`${BASE}/payslips/${encodeURIComponent(recordId)}/pdf`);
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.detail || 'Payslip download failed.'); }
  const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `Payslip_${period}_${employeeId}_${employeeName}.pdf`; link.click(); URL.revokeObjectURL(url);
}


export const getAttendanceSummary = (month, year) =>
  request(`/attendance-summary?month=${encodeURIComponent(month)}&year=${encodeURIComponent(year)}`);

export const getMyPayslips = () => request("/my-payslips");
// On-screen payslip content (HR Admin: any Paid record; employee: own record only).
export const getPayslipDetail = (recordId) => request(`/payslips/${encodeURIComponent(recordId)}`);
export const getMyPayslipDetail = (recordId) => request(`/my-payslips/${encodeURIComponent(recordId)}`);

async function getMyPayslipPdf(recordId, inline = false) {
  const response = await authenticatedFetch(
    `${BASE}/my-payslips/${encodeURIComponent(recordId)}/pdf?inline=${inline ? "true" : "false"}`,
  );
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "Payslip PDF request failed.");
  }
  return response.blob();
}

export async function downloadMyPayslip(recordId,employeeName,employeeId,period) {
  const blob = await getMyPayslipPdf(recordId, false);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `Payslip_${period}_${employeeId}_${employeeName}.pdf`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function viewMyPayslip(recordId) {
  const blob = await getMyPayslipPdf(recordId, true);
  return URL.createObjectURL(blob);
}

export async function viewPayslip(recordId) {
  const response = await authenticatedFetch(
    `${BASE}/payslips/${encodeURIComponent(recordId)}/pdf?inline=true`,
  );
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "Payslip preview failed.");
  }
  return URL.createObjectURL(await response.blob());
}
