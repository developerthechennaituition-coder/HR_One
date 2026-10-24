// Roles permitted to open the user-management feature in the client.
const USER_MANAGEMENT_ROLES = new Set([
  "HR Admin",
  "Administrator",
  "HR Manager",
  "Employee"
]);

export function canManageUsers(user) {
  return USER_MANAGEMENT_ROLES.has(user?.role);
}

export function readStoredSession() {
  // Corrupt or missing storage is treated as a signed-out session.
  try {
    return JSON.parse(sessionStorage.getItem("hrms_session"));
  } catch {
    return null;
  }
}
