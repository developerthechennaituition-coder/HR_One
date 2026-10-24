// Shared API base URL for desktop and mobile devices.
// Configure VITE_API_BASE_URL in frontend/.env.local for LAN/mobile access.

export const HRONE_API_BASE = (
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000"
).replace(/\/+$/, "");

export default HRONE_API_BASE;

