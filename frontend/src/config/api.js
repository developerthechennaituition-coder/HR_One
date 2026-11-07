// Shared API base URL for desktop and mobile devices.
//
// Development (npm run dev): requests use the page's own
// origin ("/api/..."), and the Vite proxy in vite.config.js forwards them to the
// FastAPI backend over plain HTTP. This works identically for
// http://localhost:5173 and https://<LAN-IP>:5173, avoids mixed-content and
// certificate problems, and never sends a TLS handshake to the HTTP backend
// (which is what logs "Invalid HTTP request received").
//
// Set VITE_API_BASE_URL (frontend/.env.local) only when the backend is hosted
// on a different origin, e.g. https://api.example.com.

const configuredBase = (import.meta.env.VITE_API_BASE_URL || "").trim();

export const HRONE_API_BASE = (
  configuredBase ||
  (import.meta.env.DEV ? "" : "http://127.0.0.1:8000")
).replace(/\/+$/, "");

export default HRONE_API_BASE;
