import { HRONE_API_BASE } from "./config/api.js";
const LOGOUT_API_URL = HRONE_API_BASE + "/api/auth/logout";

export async function logout(onLogout) {
  try {
    const session = JSON.parse(
      sessionStorage.getItem("hrms_session") || "null",
    );

    if (session?.token) {
      await fetch(LOGOUT_API_URL, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.token}`,
        },
      });
    }
  } catch (error) {
    console.warn("Unable to notify the server about logout:", error);
  } finally {
    sessionStorage.removeItem("hrms_session");
    window.history.replaceState({}, "", "/login");
    onLogout?.();
  }
}
