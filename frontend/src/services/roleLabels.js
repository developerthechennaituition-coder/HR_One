// Display names for stored role identifiers. The stored value stays the same
// (permissions and role checks depend on it); only the label differs.
// "HR Manager" is shown as "HOD" (Head of Department).
const ROLE_LABELS = {
  "hr manager": "HOD",
};

export function roleLabel(role) {
  const value = String(role || "").trim();
  return ROLE_LABELS[value.toLowerCase()] || value;
}
