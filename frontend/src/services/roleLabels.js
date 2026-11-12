// Display names for stored role identifiers. The stored value stays the same
// (permissions and role checks depend on it); only the label differs.
// The product shows exactly three roles: HR Admin, HOD and Employee.
// "HR Manager" is shown as "HOD" (Head of Department); legacy admin-type
// values are shown as "HR Admin" and legacy manager-type values as "HOD".
export const ROLE_OPTIONS = ["HR Admin", "HOD", "Employee"];

const ROLE_LABELS = {
  "hr manager": "HOD",
  "hr_manager": "HOD",
  "manager": "HOD",
  "hod": "HOD",
  "administrator": "HR Admin",
  "super admin": "HR Admin",
  "admin": "HR Admin",
  "hr_admin": "HR Admin",
  "hr admin": "HR Admin",
  "employee": "Employee",
};

export function roleLabel(role) {
  const value = String(role || "").trim();
  return ROLE_LABELS[value.toLowerCase()] || value;
}
