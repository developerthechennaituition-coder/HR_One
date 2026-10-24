import { HRONE_API_BASE } from "../config/api.js";
import { useEffect, useMemo, useState } from "react";
import { authenticatedFetch as fetch } from "../services/authenticatedFetch";

// Company-scoped hierarchy for units, departments, teams, and designations.
const API_ROOT = HRONE_API_BASE + "/api";

const entityConfig = {
  businessUnits: {
    label: "Business Unit",
    plural: "Business Units",
    endpoint: "business-units",
    nameKey: "business_unit_name",
    nameLabel: "Business unit name",
    icon: "bi-diagram-3",
  },
  departments: {
    label: "Department",
    plural: "Departments",
    endpoint: "departments",
    nameKey: "department_name",
    nameLabel: "Department name",
    icon: "bi-diagram-2",
  },
  teams: {
    label: "Team",
    plural: "Teams",
    endpoint: "teams",
    nameKey: "team_name",
    nameLabel: "Team name",
    icon: "bi-people",
  },
  designations: {
    label: "Designation",
    plural: "Designations",
    endpoint: "designations",
    nameKey: "designation_name",
    nameLabel: "Designation name",
    icon: "bi-person-badge",
  },
};

const emptyForm = {
  company_id: "",
  business_unit_name: "",
  department_name: "",
  parent_department_id: "",
  team_name: "",
  department_id: "",
  designation_name: "",
  description: "",
  manager_employee_id: "",
};

const readError = async (response) => {
  try {
    const error = await response.json();
    return error.detail || "The request could not be completed.";
  } catch {
    return "The request could not be completed.";
  }
};

function OrganizationSetup({ currentUser }) {
  const [records, setRecords] = useState({
    businessUnits: [],
    departments: [],
    teams: [],
    designations: [],
  });
  const [companies, setCompanies] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [activeType, setActiveType] = useState("departments");
  const [selectedCompany, setSelectedCompany] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [formValues, setFormValues] = useState(emptyForm);
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [feedback, setFeedback] = useState({ type: "", message: "" });
  const [employeeHierarchy, setEmployeeHierarchy] = useState([]);
  const [hierarchySearch, setHierarchySearch] = useState("");
  const [departmentFilter, setDepartmentFilter] = useState("");
  const [designationFilter, setDesignationFilter] = useState("");
  const [circularEmployeeIds, setCircularEmployeeIds] = useState([]);
  const canManage = ["hr admin", "administrator"].includes(
    String(currentUser?.role || "").trim().toLowerCase(),
  );

  const config = entityConfig[activeType];

  // Load related collections together so selectors and hierarchy stay in sync.
  const loadData = async () => {
    setIsLoading(true);
    try {
      const endpoints = [
        "companies",
        "employees",
        "organization/hierarchy",
        ...Object.values(entityConfig).map((item) => item.endpoint),
      ];
      const responses = await Promise.all(
        endpoints.map((endpoint) => fetch(`${API_ROOT}/${endpoint}`)),
      );
      const failedResponse = responses.find((response) => !response.ok);
      if (failedResponse) throw new Error(await readError(failedResponse));
      const [
        companyData,
        employeeData,
        hierarchyData,
        businessUnitData,
        departmentData,
        teamData,
        designationData,
      ] = await Promise.all(responses.map((response) => response.json()));
      setCompanies(companyData);
      setEmployees(employeeData);
      setEmployeeHierarchy(hierarchyData.employees || []);
      setCircularEmployeeIds(hierarchyData.circular_reporting_employee_ids || []);
      setRecords({
        businessUnits: businessUnitData,
        departments: departmentData,
        teams: teamData,
        designations: designationData,
      });
      if (!selectedCompany && companyData[0])
        setSelectedCompany(companyData[0].id);
    } catch (error) {
      setFeedback({
        type: "danger",
        message: error.message || "Unable to load organization data.",
      });
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  useEffect(() => {
    if (!feedback.message) return undefined;
    const timer = window.setTimeout(
      () => setFeedback({ type: "", message: "" }),
      4000,
    );
    return () => window.clearTimeout(timer);
  }, [feedback.message]);

  const companyName = (companyId) =>
    companies.find((company) => company.id === companyId)?.company ||
    "Unassigned company";
  const employeeName = (employeeId) => {
    const employee = employees.find((item) => item.id === employeeId);
    return employee
      ? `${employee.employee_name} (${employee.employee_id})`
      : "No manager assigned";
  };

  const filteredRecords = useMemo(() => {
    const normalizedSearch = searchTerm.trim().toLowerCase();
    return records[activeType].filter((record) => {
      const matchesCompany =
        !selectedCompany || record.company_id === selectedCompany;
      const matchesSearch =
        !normalizedSearch ||
        record[config.nameKey].toLowerCase().includes(normalizedSearch);
      return matchesCompany && matchesSearch;
    });
  }, [activeType, config.nameKey, records, searchTerm, selectedCompany]);

  const departmentsForCompany = records.departments.filter(
    (department) => department.company_id === selectedCompany,
  );
  const teamsForCompany = records.teams.filter(
    (team) => team.company_id === selectedCompany,
  );
  const businessUnitsForCompany = records.businessUnits.filter(
    (unit) => unit.company_id === selectedCompany,
  );

  const openAddForm = (type = activeType) => {
    setActiveType(type);
    setEditingId(null);
    setFormValues({
      ...emptyForm,
      company_id: selectedCompany || companies[0]?.id || "",
    });
    setFeedback({ type: "", message: "" });
    setIsFormOpen(true);
  };

  const openEditForm = (record) => {
    setEditingId(record.id);
    setFormValues({ ...emptyForm, ...record });
    setFeedback({ type: "", message: "" });
    setIsFormOpen(true);
  };

  const closeForm = () => {
    setEditingId(null);
    setFormValues(emptyForm);
    setIsFormOpen(false);
  };

  const handleChange = (event) => {
    const { name, value } = event.target;
    setFormValues((current) => ({ ...current, [name]: value }));
    setFeedback({ type: "", message: "" });
  };

  // Validate the selected entity, then choose POST or PUT from editingId.
  const handleSubmit = async (event) => {
    event.preventDefault();
    const name = formValues[config.nameKey].trim();
    if (
      !formValues.company_id ||
      !name ||
      (activeType === "teams" && !formValues.department_id)
    ) {
      setFeedback({
        type: "danger",
        message: "Choose a company and complete all required fields.",
      });
      return;
    }
    setIsSaving(true);
    const payload = { ...formValues, [config.nameKey]: name };
    Object.keys(payload).forEach((key) => {
      if (payload[key] === "") payload[key] = null;
    });
    payload.company_id = formValues.company_id;
    try {
      const response = await fetch(
        editingId
          ? `${API_ROOT}/${config.endpoint}/${editingId}`
          : `${API_ROOT}/${config.endpoint}`,
        {
          method: editingId ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      if (!response.ok) throw new Error(await readError(response));
      const savedRecord = await response.json();
      setRecords((current) => ({
        ...current,
        [activeType]: editingId
          ? current[activeType].map((record) =>
              record.id === editingId ? savedRecord : record,
            )
          : [savedRecord, ...current[activeType]],
      }));
      setSelectedCompany(savedRecord.company_id);
      setFeedback({
        type: "success",
        message: `${name} ${config.label.toLowerCase()} ${editingId ? "updated" : "saved"} successfully.`,
      });
      closeForm();
    } catch (error) {
      setFeedback({
        type: "danger",
        message:
          error.message || `Unable to save ${config.label.toLowerCase()}.`,
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleDeactivate = async (record) => {
    const name = record[config.nameKey];
    if (
      !window.confirm(
        `Deactivate ${name}? This record will be hidden from active organization data.`,
      )
    )
      return;
    try {
      const response = await fetch(
        `${API_ROOT}/${config.endpoint}/${record.id}`,
        { method: "DELETE" },
      );
      if (!response.ok) throw new Error(await readError(response));
      setRecords((current) => ({
        ...current,
        [activeType]: current[activeType].filter(
          (item) => item.id !== record.id,
        ),
      }));
      setFeedback({
        type: "success",
        message: `${name} deactivated successfully.`,
      });
      if (editingId === record.id) closeForm();
    } catch (error) {
      setFeedback({
        type: "danger",
        message: error.message || "Unable to deactivate record.",
      });
    }
  };

  const renderFormFields = () => (
    <div className="row g-3">
      <div className="col-md-6">
        <label className="form-label" htmlFor="organization-company">
          Company / legal entity
        </label>
        <select
          id="organization-company"
          className="form-select"
          name="company_id"
          value={formValues.company_id}
          onChange={handleChange}
        >
          <option value="">Select company</option>
          {companies.map((company) => (
            <option key={company.id} value={company.id}>
              {company.company} - {company.legal_entity}
            </option>
          ))}
        </select>
      </div>
      <div className="col-md-6">
        <label className="form-label" htmlFor="organization-name">
          {config.nameLabel}
        </label>
        <input
          id="organization-name"
          className="form-control"
          name={config.nameKey}
          value={formValues[config.nameKey]}
          onChange={handleChange}
          autoFocus
        />
      </div>
      {activeType === "departments" && (
        <div className="col-md-6">
          <label className="form-label" htmlFor="parent-department">
            Reports to department
          </label>
          <select
            id="parent-department"
            className="form-select"
            name="parent_department_id"
            value={formValues.parent_department_id || ""}
            onChange={handleChange}
          >
            <option value="">Top-level department</option>
            {records.departments
              .filter(
                (department) =>
                  department.company_id === formValues.company_id &&
                  department.id !== editingId,
              )
              .map((department) => (
                <option key={department.id} value={department.id}>
                  {department.department_name}
                </option>
              ))}
          </select>
        </div>
      )}
      {activeType === "teams" && (
        <div className="col-md-6">
          <label className="form-label" htmlFor="team-department">
            Reports to department
          </label>
          <select
            id="team-department"
            className="form-select"
            name="department_id"
            value={formValues.department_id || ""}
            onChange={handleChange}
          >
            <option value="">Select department</option>
            {records.departments
              .filter(
                (department) => department.company_id === formValues.company_id,
              )
              .map((department) => (
                <option key={department.id} value={department.id}>
                  {department.department_name}
                </option>
              ))}
          </select>
        </div>
      )}
      {activeType !== "designations" && (
        <div className="col-md-6">
          <label className="form-label" htmlFor="organization-manager">
            Reporting manager
          </label>
          <select
            id="organization-manager"
            className="form-select"
            name="manager_employee_id"
            value={formValues.manager_employee_id || ""}
            onChange={handleChange}
          >
            <option value="">No manager assigned</option>
            {employees.map((employee) => (
              <option key={employee.id} value={employee.id}>
                {employee.employee_name} - {employee.employee_id}
              </option>
            ))}
          </select>
        </div>
      )}
      {activeType === "designations" && (
        <div className="col-12">
          <label className="form-label" htmlFor="designation-description">
            Description
          </label>
          <textarea
            id="designation-description"
            className="form-control"
            name="description"
            rows="3"
            value={formValues.description || ""}
            onChange={handleChange}
          />
        </div>
      )}
    </div>
  );

  // Recursively render parent departments, teams, and child departments.
  const renderHierarchy = (parentId = null, depth = 0) =>
    departmentsForCompany
      .filter(
        (department) => (department.parent_department_id || null) === parentId,
      )
      .map((department) => (
        <div
          className="hierarchy-node"
          style={{ marginLeft: `${depth * 20}px` }}
          key={department.id}
        >
          <div className="hierarchy-item">
            <span className="hierarchy-icon">
              <i className="bi bi-diagram-2" />
            </span>
            <div>
              <strong>{department.department_name}</strong>
              <small>{employeeName(department.manager_employee_id)}</small>
            </div>
            <span className="hierarchy-kind">Department</span>
          </div>
          {teamsForCompany
            .filter((team) => team.department_id === department.id)
            .map((team) => (
              <div
                className="hierarchy-item hierarchy-team"
                style={{ marginLeft: "20px" }}
                key={team.id}
              >
                <span className="hierarchy-icon">
                  <i className="bi bi-people" />
                </span>
                <div>
                  <strong>{team.team_name}</strong>
                  <small>{employeeName(team.manager_employee_id)}</small>
                </div>
                <span className="hierarchy-kind">Team</span>
              </div>
            ))}
          {renderHierarchy(department.id, depth + 1)}
        </div>
      ));

  const allHierarchyEmployees = (nodes) =>
    nodes.flatMap((node) => [node, ...allHierarchyEmployees(node.children || [])]);
  const flatEmployees = allHierarchyEmployees(employeeHierarchy);
  const employeeDepartments = [...new Set(
    flatEmployees.map((item) => item.department).filter(Boolean),
  )].sort();
  const employeeDesignations = [...new Set(
    flatEmployees.map((item) => item.designation).filter(Boolean),
  )].sort();
  const normalizedHierarchySearch = hierarchySearch.trim().toLowerCase();
  const renderEmployeeHierarchy = (nodes) => nodes.map((employee) => {
    const children = renderEmployeeHierarchy(employee.children || []);
    const employeeText = [
      employee.name,
      employee.employee_id,
      employee.department,
      employee.designation,
    ].filter(Boolean).join(" ").toLowerCase();
    const matchesSearch = !normalizedHierarchySearch ||
      employeeText.includes(normalizedHierarchySearch);
    const matchesDepartment = !departmentFilter ||
      employee.department === departmentFilter;
    const matchesDesignation = !designationFilter ||
      employee.designation === designationFilter;
    if ((!matchesSearch || !matchesDepartment || !matchesDesignation) && !children.length) {
      return null;
    }
    return (
      <details
        className="border rounded p-2 mb-2"
        key={employee.id}
        open={Boolean(normalizedHierarchySearch || departmentFilter || designationFilter)}
      >
        <summary className="d-flex flex-wrap align-items-center gap-2">
          <strong>{employee.name || "Unnamed employee"}</strong>
          <span className="text-muted">{employee.employee_id}</span>
          <span className="badge text-bg-light">{employee.department || "Department not set"}</span>
          <span className="badge text-bg-light">{employee.designation || "Designation not set"}</span>
        </summary>
        {employee.reporting_manager_missing && (
          <p className="small text-warning mb-2 mt-2">
            Reporting manager {employee.reporting_manager_id} is missing from the active employee list.
          </p>
        )}
        {employee.reporting_relationship_invalid === "circular" && (
          <p className="small text-danger mb-2 mt-2">
            Circular reporting relationship detected; this employee is shown outside the invalid cycle.
          </p>
        )}
        {children.length > 0 && <div className="ms-4 mt-2">{children}</div>}
      </details>
    );
  }).filter(Boolean);

  return (
    <section className="organization-page">
      <div className="organization-heading d-flex flex-wrap justify-content-between align-items-start gap-3 mb-4">
        <div>
          <p className="eyebrow mb-1">Organization Setup</p>
          <h2 className="mb-1">Organization Structure</h2>
          <p className="text-muted mb-0">
            Build reporting lines and keep your teams easy to navigate.
          </p>
        </div>
        {canManage && <button
          type="button"
          className="btn btn-primary"
          onClick={() => openAddForm()}
        >
          <i className="bi bi-plus-lg me-2" />
          Add {config.label}
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

      <div className="organization-summary row g-3 mb-4">
        {Object.entries(entityConfig).map(([type, item]) => (
          <button
            type="button"
            className={`col-sm-6 col-xl-3 organization-stat ${activeType === type ? "selected" : ""}`}
            key={type}
            onClick={() => {
              setActiveType(type);
              setIsFormOpen(false);
            }}
          >
            <span className="stat-icon">
              <i className={`bi ${item.icon}`} />
            </span>
            <span>
              <strong>{records[type].length}</strong>
              <small>{item.plural}</small>
            </span>
            <i className="bi bi-arrow-up-right ms-auto" />
          </button>
        ))}
      </div>

      <div className="row g-4 align-items-start">
        <div className="col-xl-7">
          <div className="card organization-card border-0 shadow-sm">
            <div className="card-body p-0">
              <div className="organization-list-head d-flex flex-wrap align-items-center justify-content-between gap-3 p-4">
                <div>
                  <h5 className="mb-1">{config.plural}</h5>
                  <small className="text-muted">
                    {filteredRecords.length} active records
                  </small>
                </div>
                <div className="organization-filters d-flex gap-2">
                  <select
                    aria-label="Filter by company"
                    className="form-select"
                    value={selectedCompany}
                    onChange={(event) => setSelectedCompany(event.target.value)}
                  >
                    <option value="">All companies</option>
                    {companies.map((company) => (
                      <option key={company.id} value={company.id}>
                        {company.company}
                      </option>
                    ))}
                  </select>
                  <input
                    aria-label={`Search ${config.plural}`}
                    className="form-control"
                    type="search"
                    placeholder="Search..."
                    value={searchTerm}
                    onChange={(event) => setSearchTerm(event.target.value)}
                  />
                </div>
              </div>
              <div className="table-responsive">
                <table className="organization-table table table-hover align-middle mb-0">
                  <thead>
                    <tr>
                      <th>{config.label}</th>
                      <th>Company</th>
                      <th>Reports to</th>
                      <th className="text-end">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {isLoading ? (
                      <tr>
                        <td colSpan="4" className="text-center text-muted py-5">
                          Loading organization data...
                        </td>
                      </tr>
                    ) : filteredRecords.length === 0 ? (
                      <tr>
                        <td colSpan="4" className="text-center text-muted py-5">
                          No active {config.plural.toLowerCase()} found.
                        </td>
                      </tr>
                    ) : (
                      filteredRecords.map((record) => (
                        <tr key={record.id}>
                          <td>
                            <strong>{record[config.nameKey]}</strong>
                            {activeType === "designations" &&
                              record.description && (
                                <small className="d-block text-muted text-truncate organization-description">
                                  {record.description}
                                </small>
                              )}
                          </td>
                          <td>{companyName(record.company_id)}</td>
                          <td>
                            {activeType === "departments"
                              ? departmentsForCompany.find(
                                  (item) =>
                                    item.id === record.parent_department_id,
                                )?.department_name || "Top level"
                              : activeType === "teams"
                                ? departmentsForCompany.find(
                                    (item) => item.id === record.department_id,
                                  )?.department_name || "Department unavailable"
                                : activeType === "designations"
                                  ? "Organization-wide"
                                  : employeeName(record.manager_employee_id)}
                          </td>
                          <td className="text-end text-nowrap">
                            {canManage && <button
                              type="button"
                              className="btn btn-sm btn-link text-primary"
                              onClick={() => openEditForm(record)}
                              aria-label={`Edit ${record[config.nameKey]}`}
                            >
                              <i className="bi bi-pencil-square" />
                            </button>}
                            {canManage && <button
                              type="button"
                              className="btn btn-sm btn-link text-danger"
                              onClick={() => handleDeactivate(record)}
                              aria-label={`Deactivate ${record[config.nameKey]}`}
                            >
                              <i className="bi bi-trash3" />
                            </button>}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
        <div className="col-xl-5">
          <div className="card organization-card hierarchy-card border-0 shadow-sm">
            <div className="card-body p-4">
              <div className="d-flex justify-content-between align-items-start mb-4">
                <div>
                  <p className="eyebrow mb-1">Reporting lines</p>
                  <h5 className="mb-1">Hierarchy view</h5>
                  <small className="text-muted">
                    {selectedCompany
                      ? companyName(selectedCompany)
                      : "Select a company to begin"}
                  </small>
                </div>
                <i className="bi bi-share hierarchy-heading-icon" />
              </div>
              {selectedCompany ? (
                <div className="hierarchy-list">
                  {businessUnitsForCompany.map((unit) => (
                    <div className="hierarchy-unit" key={unit.id}>
                      <div className="hierarchy-item hierarchy-root">
                        <span className="hierarchy-icon">
                          <i className="bi bi-diagram-3" />
                        </span>
                        <div>
                          <strong>{unit.business_unit_name}</strong>
                          <small>
                            {employeeName(unit.manager_employee_id)}
                          </small>
                        </div>
                        <span className="hierarchy-kind">Business Unit</span>
                      </div>
                    </div>
                  ))}
                  {renderHierarchy()}
                  {!businessUnitsForCompany.length &&
                    !departmentsForCompany.length && (
                      <div className="empty-hierarchy">
                        <i className="bi bi-diagram-3" />
                        <p className="mb-0">No reporting structure yet.</p>
                        {canManage && <button
                          type="button"
                          className="btn btn-sm btn-outline-primary mt-3"
                          onClick={() => openAddForm("departments")}
                        >
                          Add first department
                        </button>}
                      </div>
                    )}
                </div>
              ) : (
                <div className="empty-hierarchy">
                  <i className="bi bi-building" />
                  <p className="mb-0">
                    Create a company first to see its structure.
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
      <div className="card border-0 shadow-sm mt-4">
        <div className="card-body p-4">
          <div className="d-flex flex-wrap justify-content-between align-items-start gap-3 mb-3">
            <div>
              <p className="eyebrow mb-1">Employee reporting</p>
              <h5 className="mb-1">Organization chart</h5>
              <p className="text-muted mb-0">
                {flatEmployees.length} active employees · expand or collapse managers to inspect reporting lines.
              </p>
            </div>
            {circularEmployeeIds.length > 0 && (
              <span className="badge text-bg-danger">
                {circularEmployeeIds.length} circular reporting relationship(s)
              </span>
            )}
          </div>
          <div className="row g-2 mb-3">
            <div className="col-lg-6">
              <input
                type="search"
                className="form-control"
                aria-label="Search employees"
                placeholder="Search name, employee ID, department or designation"
                value={hierarchySearch}
                onChange={(event) => setHierarchySearch(event.target.value)}
              />
            </div>
            <div className="col-md-6 col-lg-3">
              <select
                className="form-select"
                aria-label="Filter by department"
                value={departmentFilter}
                onChange={(event) => setDepartmentFilter(event.target.value)}
              >
                <option value="">All departments</option>
                {employeeDepartments.map((item) => <option key={item}>{item}</option>)}
              </select>
            </div>
            <div className="col-md-6 col-lg-3">
              <select
                className="form-select"
                aria-label="Filter by designation"
                value={designationFilter}
                onChange={(event) => setDesignationFilter(event.target.value)}
              >
                <option value="">All designations</option>
                {employeeDesignations.map((item) => <option key={item}>{item}</option>)}
              </select>
            </div>
          </div>
          {employeeHierarchy.length ? (
            renderEmployeeHierarchy(employeeHierarchy)
          ) : (
            <div className="alert alert-info mb-0">
              No active employee reporting relationships are available.
            </div>
          )}
        </div>
      </div>
      {isFormOpen && (
        <div className="organization-form card border-0 shadow-sm mt-4">
          <div className="card-body p-4">
            <div className="d-flex justify-content-between align-items-center mb-4">
              <div>
                <p className="eyebrow mb-1">
                  {editingId ? "Update record" : "New record"}
                </p>
                <h5 className="mb-0">
                  {editingId ? `Edit ${config.label}` : `Add ${config.label}`}
                </h5>
              </div>
              <button
                type="button"
                className="btn-close"
                aria-label="Close form"
                onClick={closeForm}
              />
            </div>
            <form onSubmit={handleSubmit}>
              {renderFormFields()}
              <div className="d-flex justify-content-end gap-2 mt-4">
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
                  {isSaving ? "Saving..." : "Save changes"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </section>
  );
}

export default OrganizationSetup;
