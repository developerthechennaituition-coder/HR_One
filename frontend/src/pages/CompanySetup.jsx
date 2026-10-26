import { HRONE_API_BASE } from "../config/api.js";
import { useEffect, useState } from "react";
import { authenticatedFetch as fetch } from "../services/authenticatedFetch";

// Company is the parent master referenced by organization and holiday features.
const API_URL = HRONE_API_BASE + "/api/companies";

const emptyCompany = {
  company: "",
  legalEntity: "",
  branch: "",
  location: "",
  branch_code: "",
  employee_ids: [],
};

const companyFields = [
  ["company", "Company"],
  ["legalEntity", "Legal Entity"],
  ["branch", "Branch"],
  ["location", "Location"],
  ["branch_code", "Branch Code"],
];

function CompanySetup({ currentUser }) {
  const [companies, setCompanies] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [employeeDropdownOpen, setEmployeeDropdownOpen] = useState(false);
  const [employeeSearchTerm, setEmployeeSearchTerm] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [formValues, setFormValues] = useState(emptyCompany);
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [feedback, setFeedback] = useState({ type: "", message: "" });
  const canManage = ["hr admin", "administrator"].includes(
    String(currentUser?.role || "").trim().toLowerCase(),
  );

  const setError = (message) => setFeedback({ type: "danger", message });

  const readError = async (response) => {
    try {
      const error = await response.json();
      return error.detail || "The request could not be completed.";
    } catch {
      return "The request could not be completed.";
    }
  };

  const toFormCompany = (company) => ({
    company: company.company,
    legalEntity: company.legal_entity,
    branch: company.branch,
    location: company.location,
    branch_code: company.branch_code || "",
    employee_ids: company.employee_ids || [],
  });

  // Fetch active companies when this feature is opened.
  useEffect(() => {
    const loadCompanies = async () => {
      try {
        const [response, employeeResponse, usersResponse] = await Promise.all([
          fetch(API_URL),
          fetch(HRONE_API_BASE + "/api/employees"),
          fetch(HRONE_API_BASE + "/api/users"),
        ]);
        if (!response.ok) {
          throw new Error(await readError(response));
        }
        const data = await response.json();
        const employeeData = employeeResponse.ok ? await employeeResponse.json() : [];
        const userData = usersResponse.ok ? await usersResponse.json() : [];

        // Employee Master stores user accounts, while some HRMS installations also
        // maintain separate records in the employees collection. Include both sources.
        // Use each record's Mongo id as the assignment key because the backend stores
        // branch employee_ids against these stable record identifiers.
        const employeeRecords = [
          ...(Array.isArray(employeeData) ? employeeData : []),
          ...(Array.isArray(userData) ? userData.filter((user) =>
            user?.is_active !== false &&
            (user?.employee_id || String(user?.role || "").trim().toLowerCase() === "employee")
          ).map((user) => ({
            ...user,
            employee_name: user.employee_name || user.name,
            email: user.email || user.work_email,
          })) : []),
        ];
        const uniqueEmployees = new Map();
        employeeRecords.forEach((employee) => {
          const id = String(employee.id || employee._id || "").trim();
          if (!id) return;
          const existing = uniqueEmployees.get(id);
          uniqueEmployees.set(id, {
            ...existing,
            ...employee,
            id,
            employee_name: employee.employee_name || employee.name || existing?.employee_name || "Unnamed employee",
          });
        });
        setEmployees([...uniqueEmployees.values()]);
        setCompanies(
          (Array.isArray(data) ? data : []).map((company) => ({
            ...company,
            ...toFormCompany(company),
            employee_ids: Array.isArray(company.employee_ids) ? company.employee_ids.map(String) : [],
          })),
        );
      } catch (error) {
        setError(error.message || "Unable to load companies.");
      } finally {
        setIsLoading(false);
      }
    };

    loadCompanies();
  }, []);

  useEffect(() => {
    if (!feedback.message) {
      return undefined;
    }

    const feedbackTimer = window.setTimeout(() => {
      setFeedback({ type: "", message: "" });
    }, 3000);

    return () => window.clearTimeout(feedbackTimer);
  }, [feedback.message]);

  const filteredCompanies = companies.filter((company) =>
    String(company.company || "").toLowerCase().includes(searchTerm.toLowerCase()),
  );

  // Employees assigned to any other active branch are unavailable here.
  // Employees already assigned to the branch currently being edited remain selectable.
  const assignedToOtherBranches = new Set(
    companies
      .filter((company) => company.id !== editingId && company.is_active !== false)
      .flatMap((company) => (company.employee_ids || []).map(String)),
  );
  const selectedEmployeeIds = (formValues.employee_ids || []).map(String);
  const availableEmployees = employees.filter((employee) => {
    const employeeId = String(employee.id || employee._id || "").trim();
    return employeeId && (
      selectedEmployeeIds.includes(employeeId) ||
      !assignedToOtherBranches.has(employeeId)
    );
  });

  // Add and edit share one form; editingId selects POST versus PUT on submit.
  const openAddForm = () => {
    setEditingId(null);
    setFormValues(emptyCompany);
    setEmployeeSearchTerm("");
    setEmployeeDropdownOpen(false);
    setFeedback({ type: "", message: "" });
    setIsFormOpen(true);
  };

  const openEditForm = (company) => {
    setEditingId(company.id);
    setFormValues(toFormCompany(company));
    setEmployeeSearchTerm("");
    setEmployeeDropdownOpen(false);
    setFeedback({ type: "", message: "" });
    setIsFormOpen(true);
  };

  const closeForm = () => {
    setEditingId(null);
    setFormValues(emptyCompany);
    setEmployeeSearchTerm("");
    setEmployeeDropdownOpen(false);
    setFeedback({ type: "", message: "" });
    setIsFormOpen(false);
  };

  const handleChange = (event) => {
    const { name, value } = event.target;
    setFormValues((currentValues) => ({ ...currentValues, [name]: value }));
    setFeedback({ type: "", message: "" });
  };

  const handleSubmit = async (event) => {
    event.preventDefault();

    const hasEmptyField = ["company", "legalEntity", "branch", "location"].some(
      (name) => !String(formValues[name] || "").trim(),
    );
    if (hasEmptyField) {
      setFeedback({
        type: "danger",
        message: "Please enter all company details.",
      });
      return;
    }

    setIsSaving(true);
    const payload = {
      company: formValues.company.trim(),
      legal_entity: formValues.legalEntity.trim(),
      branch: formValues.branch.trim(),
      location: formValues.location.trim(),
      branch_code: formValues.branch_code.trim(),
      employee_ids: formValues.employee_ids || [],
    };

    try {
      const response = await fetch(
        editingId ? `${API_URL}/${editingId}` : API_URL,
        {
          method: editingId ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      if (!response.ok) {
        throw new Error(await readError(response));
      }

      const savedCompany = await response.json();
      const companyForState = {
        ...savedCompany,
        ...toFormCompany(savedCompany),
      };
      setCompanies((currentCompanies) =>
        editingId
          ? currentCompanies.map((company) =>
              company.id === editingId ? companyForState : company,
            )
          : [...currentCompanies, companyForState],
      );
      setFeedback({
        type: "success",
        message: `${payload.company} company entity ${editingId ? "updated" : "saved"} successfully.`,
      });
      setEditingId(null);
      setFormValues(emptyCompany);
      setIsFormOpen(false);
    } catch (error) {
      setError(error.message || "Unable to save company.");
    } finally {
      setIsSaving(false);
    }
  };

  const handleDeactivate = async (companyToDeactivate) => {
    const shouldDeactivate = window.confirm(
      `Are you sure you want to deactivate ${companyToDeactivate.company}? It will be hidden from active companies, not permanently deleted.`,
    );

    if (!shouldDeactivate) {
      return;
    }

    try {
      const response = await fetch(`${API_URL}/${companyToDeactivate.id}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }

      setCompanies((currentCompanies) =>
        currentCompanies.filter(
          (company) => company.id !== companyToDeactivate.id,
        ),
      );
      setEditingId(null);
      setFormValues(emptyCompany);
      setIsFormOpen(false);
      setFeedback({
        type: "success",
        message: `${companyToDeactivate.company} company entity deactivated successfully.`,
      });
    } catch (error) {
      setError(error.message || "Unable to deactivate company.");
    }
  };

  return (
    <section className="company-setup-page">
      <div className="company-setup-toolbar d-flex flex-wrap justify-content-between align-items-center gap-3 mb-4">
        <h2 className="mb-0">Company / Legal Entity Setup</h2>
        {canManage && <button
          type="button"
          className="btn btn-primary company-add-button"
          onClick={openAddForm}
        >
          + Add Branch / Legal Entity
        </button>}
      </div>

      {feedback.message && (
        <div
          className={`alert alert-${feedback.type}`}
          role="alert"
          aria-live="polite"
        >
          {feedback.message}
        </div>
      )}

      {isFormOpen && (
        <div className="card border-0 shadow-sm mb-4">
          <div className="card-body p-4">
            <h5 className="card-title mb-4">
              {editingId === null ? "Add Branch / Legal Entity" : "Edit Branch / Legal Entity"}
            </h5>

            <form onSubmit={handleSubmit}>
              <div className="row g-3">
                {companyFields.map(([name, label]) => (
                  <div className="col-md-6" key={name}>
                    <label htmlFor={name} className="form-label">
                      {label}
                    </label>
                    <input
                      id={name}
                      name={name}
                      type="text"
                      className="form-control"
                      value={formValues[name]}
                      onChange={handleChange}
                    />
                  </div>
                ))}
              </div>
              <div className="mt-3">
                <label className="form-label" id="branch-employees-label">Employees assigned to this branch</label>
                <div className="position-relative" style={{ maxWidth: "100%" }}>
                  <button
                    id="branch-employees"
                    type="button"
                    className="form-select text-start d-flex align-items-center justify-content-between"
                    aria-labelledby="branch-employees-label"
                    aria-haspopup="listbox"
                    aria-expanded={employeeDropdownOpen}
                    onClick={() => setEmployeeDropdownOpen((open) => !open)}
                    style={{ minHeight: 44, backgroundImage: "none" }}
                  >
                    <span className="text-truncate">
                      {(formValues.employee_ids || []).length === 0
                        ? "Select one or more employees"
                        : `${formValues.employee_ids.length} employee${formValues.employee_ids.length === 1 ? "" : "s"} selected`}
                    </span>
                    <i className={`bi ${employeeDropdownOpen ? "bi-chevron-up" : "bi-chevron-down"} ms-2`} aria-hidden="true" />
                  </button>
                  {employeeDropdownOpen && (
                    <div className="dropdown-menu show w-100 p-2 shadow" style={{ maxHeight: 320, overflowY: "auto", zIndex: 1050 }}>
                      <div className="p-1 mb-2">
                        <input
                          type="search"
                          className="form-control form-control-sm"
                          value={employeeSearchTerm}
                          onChange={(event) => setEmployeeSearchTerm(event.target.value)}
                          placeholder="Search by employee name or ID..."
                          aria-label="Search employees"
                          autoFocus
                        />
                      </div>
                      {(formValues.employee_ids || []).length > 0 && (
                        <div className="d-flex justify-content-between align-items-center px-2 pb-2 mb-1 border-bottom">
                          <small className="text-muted">{formValues.employee_ids.length} selected</small>
                          <button
                            type="button"
                            className="btn btn-link btn-sm p-0 text-decoration-none"
                            onClick={() => setFormValues((current) => ({ ...current, employee_ids: [] }))}
                          >Clear all</button>
                        </div>
                      )}
                      {availableEmployees
                        .filter((employee) => {
                          const term = employeeSearchTerm.trim().toLowerCase();
                          const name = employee.employee_name || employee.name || employee.employee_id || "Unnamed employee";
                          return !term || `${name} ${employee.employee_id || ""}`.toLowerCase().includes(term);
                        })
                        .map((employee) => {
                          const employeeId = String(employee.id || employee._id || "").trim();
                          const selected = (formValues.employee_ids || []).map(String).includes(employeeId);
                          const employeeName = employee.employee_name || employee.name || "Unnamed employee";
                          return (
                            <label key={employeeId} className="dropdown-item rounded d-flex align-items-start gap-2 py-2" style={{ whiteSpace: "normal", cursor: "pointer" }}>
                              <input
                                className="form-check-input mt-1 flex-shrink-0"
                                type="checkbox"
                                checked={selected}
                                onChange={() => setFormValues((current) => {
                                  const selectedIds = current.employee_ids || [];
                                  return {
                                    ...current,
                                    employee_ids: selected
                                      ? selectedIds.filter((id) => id !== employeeId)
                                      : [...selectedIds, employeeId],
                                  };
                                })}
                              />
                              <span className="d-flex flex-column">
                                <span>{employeeName}</span>
                                <small className="text-muted">{employee.employee_id || "No employee ID"}</small>
                              </span>
                            </label>
                          );
                        })}
                      {availableEmployees.filter((employee) => {
                        const term = employeeSearchTerm.trim().toLowerCase();
                        const name = employee.employee_name || employee.name || employee.employee_id || "Unnamed employee";
                        return !term || `${name} ${employee.employee_id || ""}`.toLowerCase().includes(term);
                      }).length === 0 && (
                        <div className="text-muted small text-center py-3">
                           {employees.length === 0
                             ? "No active employees were returned by Employee Master. Check that employee accounts are active and reload this page."
                             : "No available employees match your search. Employees assigned to another branch are excluded."}
                         </div>
                      )}
                    </div>
                  )}
                </div>
                {(formValues.employee_ids || []).length > 0 && (
                  <div className="d-flex flex-wrap gap-2 mt-2">
                    {formValues.employee_ids.map((employeeId) => {
                      const employee = employees.find((item) => String(item.id || item._id || "") === String(employeeId));
                      const name = employee?.employee_name || employee?.name || employee?.employee_id || "Selected employee";
                      return (
                        <span key={employeeId} className="badge rounded-pill text-bg-light border d-inline-flex align-items-center gap-2 py-2 px-3">
                          {name}
                          <button type="button" className="btn-close" style={{ fontSize: "0.55rem" }} aria-label={`Remove ${name}`} onClick={() => setFormValues((current) => ({ ...current, employee_ids: (current.employee_ids || []).filter((id) => id !== employeeId) }))} />
                        </span>
                      );
                    })}
                  </div>
                )}
                <div className="form-text">Open the dropdown and check one or more employees. You can select multiple employees without holding Ctrl.</div>
              </div>

              <div className="company-form-actions d-flex justify-content-end gap-2 mt-4">
                <button
                  type="button"
                  className="btn btn-outline-secondary"
                  onClick={closeForm}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isSaving}
                >
                  {isSaving ? "Saving..." : "Save"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <div className="card border-0 shadow-sm">
        <div className="card-body">
          <div className="mb-4">
            <label htmlFor="company-search" className="visually-hidden">
              Search company
            </label>
            <input
              id="company-search"
              type="search"
              className="form-control"
              placeholder="Search company..."
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
            />
          </div>

          <div className="company-table-wrapper table-responsive">
            <table className="company-table table table-hover align-middle mb-0">
              <thead>
                <tr>
                  <th scope="col">Company</th>
                  <th scope="col">Legal Entity</th>
                  <th scope="col">Branch</th>
                  <th scope="col">Location</th>
                  <th scope="col">Branch Code</th>
                  <th scope="col">Employees</th>
                  <th scope="col">Action</th>
                </tr>
              </thead>
              <tbody>
                {isLoading ? (
                  <tr>
                    <td colSpan="7" className="text-center text-muted py-4">
                      Loading companies...
                    </td>
                  </tr>
                ) : filteredCompanies.length === 0 ? (
                  <tr>
                    <td colSpan="7" className="text-center text-muted py-4">
                      No companies available
                    </td>
                  </tr>
                ) : (
                  filteredCompanies.map((company) => {
                    return (
                      <tr key={company.id}>
                        <td>{company.company}</td>
                        <td>{company.legalEntity}</td>
                        <td>{company.branch}</td>
                        <td>{company.location}</td>
                        <td>{company.branch_code || "—"}</td>
                        <td>{(company.employee_ids || []).length}</td>
                        <td className="text-nowrap">
                          {canManage && <button
                            type="button"
                            className="btn btn-sm btn-primary rounded-pill px-3 me-2"
                            onClick={() => openEditForm(company)}
                          >
                            Edit
                          </button>}
                          {canManage && <button
                            type="button"
                            className="btn btn-sm btn-outline-danger rounded-pill px-3"
                            onClick={() => handleDeactivate(company)}
                          >
                            Deactivate
                          </button>}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
}

export default CompanySetup;
