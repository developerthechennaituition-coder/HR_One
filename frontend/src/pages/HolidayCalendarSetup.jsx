import { HRONE_API_BASE } from "../config/api.js";
import { isWeeklyOff, useCompanyWeeklyOffs, weeklyOffLabel } from "../services/weeklyOff";
import { useEffect, useMemo, useState } from "react";
import { authenticatedFetch as fetch } from "../services/authenticatedFetch";

// A calendar belongs to a company/location and contains holidays and employee assignments.
const API_ROOT = HRONE_API_BASE + "/api";

const emptyCalendar = {
  company_id: "",
  location: "",
  calendar_name: "",
  calendar_year: new Date().getFullYear(),
  country: "India",
  state: "Tamil Nadu",
  description: "",
  religion: "",
};

const emptyHoliday = {
  holiday_date: "",
  holiday_name: "",
  holiday_type: "Public",
  applicable_religion: "",
  applicable_religions: [],
  applicable_employee_groups: [],
  applicable_employee_groups_text: "",
  applicability: "All Employees",
  is_company_holiday: false,
  description: "",
  is_optional: false,
};
const countryOptions = [
  { name: "Not specified", code: "XX", disabled: true },
  { name: "India", code: "IN" },
  { name: "United States", code: "US" },
  { name: "United Kingdom", code: "GB" },
  { name: "Australia", code: "AU" },
];
const indiaStates = [
  "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
  "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
  "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
  "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
  "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
  "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu",
  "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
];
const monthNames = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

const readError = async (response) => {
  try {
    const error = await response.json();
    return error.detail || "The request could not be completed.";
  } catch {
    return "The request could not be completed.";
  }
};

function HolidayCalendarSetup({ currentUser }) {
  const weeklyOffDays = useCompanyWeeklyOffs();
  const [companies, setCompanies] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [calendars, setCalendars] = useState([]);
  const [holidays, setHolidays] = useState([]);
  const [assignments, setAssignments] = useState([]);
  const [selectedCalendarId, setSelectedCalendarId] = useState("");
  const [calendarSearch, setCalendarSearch] = useState("");
  const [calendarForm, setCalendarForm] = useState(emptyCalendar);
  const [holidayForm, setHolidayForm] = useState(emptyHoliday);
  const [editingCalendarId, setEditingCalendarId] = useState(null);
  const [editingHolidayId, setEditingHolidayId] = useState(null);
  const [isCalendarFormOpen, setIsCalendarFormOpen] = useState(false);
  const [isHolidayFormOpen, setIsHolidayFormOpen] = useState(false);
  const [isAssignFormOpen, setIsAssignFormOpen] = useState(false);
  const [assignmentEmployeeId, setAssignmentEmployeeId] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [feedback, setFeedback] = useState({ type: "", message: "" });
  const [suggestedHolidays, setSuggestedHolidays] = useState([]);
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);
  // Religions come from employee records; years are limited by the backend.
  const [options, setOptions] = useState({ religions: [], allowed_years: [], existing_calendars: [] });
  // In-page confirmation (optionally asking for a reason) instead of browser prompts.
  const [confirmDialog, setConfirmDialog] = useState(null);
  const [confirmReason, setConfirmReason] = useState("");
  const [confirmBusy, setConfirmBusy] = useState(false);
  const canManage = ["hr admin", "administrator"].includes(
    String(currentUser?.role || "").trim().toLowerCase(),
  );

  const selectedCalendar = calendars.find(
    (calendar) => calendar.id === selectedCalendarId,
  );
  const isReligionCalendar = Boolean(selectedCalendar?.religion_key);
  const calendarTitle = (calendar) =>
    calendar.religion ? `${calendar.religion} · ${calendar.calendar_year}` : `${calendar.calendar_year} · Company-wide`;
  const duplicateCalendar = !editingCalendarId && calendarForm.religion
    ? calendars.find((calendar) =>
        String(calendar.religion || "").toLowerCase() === calendarForm.religion.toLowerCase() &&
        Number(calendar.calendar_year) === Number(calendarForm.calendar_year))
    : null;

  const askConfirm = (dialog) => {
    setConfirmReason("");
    setConfirmDialog(dialog);
  };

  const runConfirm = async () => {
    if (!confirmDialog) return;
    if (confirmDialog.requireReason && confirmReason.trim().length < 3) return;
    setConfirmBusy(true);
    try {
      await confirmDialog.onConfirm(confirmReason.trim());
      setConfirmDialog(null);
    } finally {
      setConfirmBusy(false);
    }
  };

  const loadOptions = async () => {
    if (!canManage) return;
    try {
      const response = await fetch(`${API_ROOT}/holiday-calendars/options`);
      if (response.ok) setOptions(await response.json());
    } catch {
      // The form explains that religions could not be loaded.
    }
  };
  const companyName = (companyId) =>
    companies.find((company) => company.id === companyId)?.company ||
    "Unknown company";
  const employeeName = (employeeId) =>
    employees.find((employee) => employee.id === employeeId)?.employee_name ||
    "Unknown employee";

  const loadPublicHolidaySuggestions = async () => {
    if (!selectedCalendar) {
      setFeedback({ type: "danger", message: "Save a calendar before requesting holiday suggestions." });
      return;
    }
    const country = countryOptions.find(
      (item) => item.name === selectedCalendar.country,
    );
    if (!country) {
      setFeedback({ type: "danger", message: "Select a supported country for public holiday suggestions." });
      return;
    }
    setLoadingSuggestions(true);
    try {
      const response = await window.fetch(
        `https://date.nager.at/api/v3/PublicHolidays/${selectedCalendar.calendar_year}/${country.code}`,
      );
      if (!response.ok) throw new Error(`Holiday provider returned ${response.status}.`);
      const data = await response.json();
      const stateCodeByName = { "Tamil Nadu": "TN" };
      const expectedCode = stateCodeByName[selectedCalendar.state];
      const suggestions = data
        .filter((item) => !expectedCode || !item.counties?.length ||
          item.counties.some((county) => county.endsWith(`-${expectedCode}`)))
        .map((item) => ({
          key: `${item.date}:${item.name}`,
          holiday_date: item.date,
          holiday_name: item.localName || item.name,
          holiday_type: "Public",
          applicability: "All Employees",
          applicable_religions: [],
          applicable_religion: null,
          is_company_holiday: false,
          is_optional: false,
          description: item.name === item.localName ? "" : item.name,
          selected: false,
        }));
      setSuggestedHolidays(suggestions);
      setFeedback({
        type: "success",
        message: `${suggestions.length} public holiday suggestions loaded. Review and select the company holidays to add as draft entries.`,
      });
    } catch (error) {
      setFeedback({
        type: "danger",
        message: error.message || "Unable to load public holiday suggestions.",
      });
    } finally {
      setLoadingSuggestions(false);
    }
  };

  const saveSelectedSuggestions = async () => {
    if (!selectedCalendar) return;
    const selected = suggestedHolidays.filter((item) => item.selected);
    if (!selected.length) {
      setFeedback({ type: "danger", message: "Select at least one suggestion." });
      return;
    }
    setIsSaving(true);
    const failures = [];
    const saved = [];
    for (const item of selected) {
      const payload = Object.fromEntries(
        Object.entries(item).filter(([field]) => field !== "key" && field !== "selected"),
      );
      try {
        const response = await fetch(
          `${API_ROOT}/holiday-calendars/${selectedCalendar.id}/holidays`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...payload, calendar_id: selectedCalendar.id }),
          },
        );
        if (!response.ok) failures.push(`${payload.holiday_name}: ${await readError(response)}`);
        else saved.push(await response.json());
      } catch (error) {
        failures.push(`${payload.holiday_name}: ${error.message}`);
      }
    }
    setHolidays((current) => [...current, ...saved].sort(
      (left, right) => left.holiday_date.localeCompare(right.holiday_date),
    ));
    setSuggestedHolidays((current) => current.filter((item) => !item.selected));
    setFeedback(failures.length
      ? { type: "danger", message: `${saved.length} saved; ${failures.join("; ")}` }
      : { type: "success", message: `${saved.length} suggestions saved as draft holidays.` });
    setIsSaving(false);
  };

  // Load collections needed by the calendar list and assignment panel.
  const loadData = async () => {
    setIsLoading(true);
    try {
      const [
        companyResponse,
        employeeResponse,
        calendarResponse,
        assignmentResponse,
      ] = await Promise.all([
        fetch(`${API_ROOT}/companies`),
        fetch(`${API_ROOT}/employees`),
        fetch(`${API_ROOT}/holiday-calendars`),
        fetch(`${API_ROOT}/employee-holiday-calendars`),
      ]);
      const responses = [
        companyResponse,
        employeeResponse,
        calendarResponse,
        assignmentResponse,
      ];
      const failedResponse = responses.find((response) => !response.ok);
      if (failedResponse) throw new Error(await readError(failedResponse));
      const [companyData, employeeData, calendarData, assignmentData] =
        await Promise.all(responses.map((response) => response.json()));
      setCompanies(companyData);
      setEmployees(employeeData);
      setCalendars(calendarData);
      setAssignments(assignmentData);
      setSelectedCalendarId((currentId) =>
        currentId && calendarData.some((calendar) => calendar.id === currentId)
          ? currentId
          : calendarData[0]?.id || "",
      );
    } catch (error) {
      setFeedback({
        type: "danger",
        message: error.message || "Unable to load holiday calendars.",
      });
    } finally {
      setIsLoading(false);
    }
  };

  // Reload holiday entries whenever the selected calendar changes.
  const loadHolidays = async (calendarId) => {
    if (!calendarId) {
      setHolidays([]);
      return;
    }
    try {
      const response = await fetch(
        `${API_ROOT}/holiday-calendars/${calendarId}/holidays`,
      );
      if (!response.ok) throw new Error(await readError(response));
      setHolidays(await response.json());
    } catch (error) {
      setFeedback({
        type: "danger",
        message: error.message || "Unable to load holidays.",
      });
    }
  };

  useEffect(() => {
    loadData();
    loadOptions();
  }, []);

  useEffect(() => {
    loadHolidays(selectedCalendarId);
  }, [selectedCalendarId]);

  useEffect(() => {
    if (!feedback.message) return undefined;
    const timer = window.setTimeout(
      () => setFeedback({ type: "", message: "" }),
      4500,
    );
    return () => window.clearTimeout(timer);
  }, [feedback.message]);

  const filteredCalendars = useMemo(() => {
    const search = calendarSearch.trim().toLowerCase();
    return calendars.filter(
      (calendar) =>
        !search ||
        `${calendar.calendar_name} ${calendar.religion || ""} ${calendar.calendar_year} ${calendar.location} ${companyName(calendar.company_id)}`
          .toLowerCase()
          .includes(search),
    );
  }, [calendarSearch, calendars, companies]);

  const calendarAssignments = assignments.filter(
    (assignment) => assignment.calendar_id === selectedCalendarId,
  );
  const holidaysByDate = new Map(holidays.map((holiday) => [holiday.holiday_date.slice(0, 10), holiday]));
  const renderCalendarMonth = (year, monthIndex) => {
    const offset = new Date(year, monthIndex, 1).getDay();
    const count = new Date(year, monthIndex + 1, 0).getDate();
    return (
      <div className="border rounded p-2 h-100" key={monthIndex}>
        <h6 className="text-center">{monthNames[monthIndex]} {year}</h6>
        <div className="d-grid" style={{ gridTemplateColumns: "repeat(7, 1fr)", gap: 2 }}>
          {["S", "M", "T", "W", "T", "F", "S"].map((day, index) => (
            <small className="text-center text-muted" key={`${day}-${index}`}>{day}</small>
          ))}
          {Array.from({ length: offset }, (_, index) => (
            <span aria-hidden="true" key={`blank-${index}`} />
          ))}
          {Array.from({ length: count }, (_, index) => {
            const day = index + 1;
            const key = `${year}-${String(monthIndex + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
            const holiday = holidaysByDate.get(key);
            const weekend = isWeeklyOff(weeklyOffDays, new Date(year, monthIndex, day));
            const localToday = new Date();
            const isToday = localToday.getFullYear() === year &&
              localToday.getMonth() === monthIndex && localToday.getDate() === day;
            return (
              <span
                className={`text-center rounded ${holiday?.is_company_holiday ? "bg-success text-white" : holiday ? "bg-info-subtle" : weekend ? "text-danger" : ""} ${isToday ? "border border-primary border-2" : ""}`}
                key={day}
                title={holiday?.holiday_name || ""}
              >
                {day}
              </span>
            );
          })}
        </div>
      </div>
    );
  };
  const availableEmployees = employees.filter(
    (employee) =>
      !assignments.some(
        (assignment) =>
          assignment.employee_id === employee.id && assignment.is_active,
      ),
  );

  const openCalendarForm = (calendar = null) => {
    setEditingCalendarId(calendar?.id || null);
    setCalendarForm(
      calendar
        ? { ...calendar, religion: calendar.religion || "", description: calendar.description || "" }
        : {
            ...emptyCalendar,
            company_id: companies[0]?.id || "",
            location: companies[0]?.location || "",
            calendar_year: options.allowed_years[0] || new Date().getFullYear(),
          },
    );
    setIsCalendarFormOpen(true);
    setFeedback({ type: "", message: "" });
  };

  const openHolidayForm = (holiday = null) => {
    setEditingHolidayId(holiday?.id || null);
    setHolidayForm(
      holiday
        ? {
            ...holiday,
            holiday_date: holiday.holiday_date.slice(0, 10),
            applicable_religions: holiday.applicable_religions || [],
            applicable_employee_groups: holiday.applicable_employee_groups || [],
            applicable_employee_groups_text: (holiday.applicable_employee_groups || []).join(", "),
          }
        : emptyHoliday,
    );
    setIsHolidayFormOpen(true);
    setFeedback({ type: "", message: "" });
  };

  const handleCalendarChange = (event) => {
    const { name, value } = event.target;
    setCalendarForm((current) => ({ ...current, [name]: value }));
  };

  const handleHolidayChange = (event) => {
    const { name, value, type, checked } = event.target;
    setHolidayForm((current) => ({
      ...current,
      [name]: type === "checkbox" ? checked : value,
      ...(name === "holiday_type" && value !== "Religion"
        ? { applicable_religion: "" }
        : {}),
    }));
  };

  // Validate and persist a calendar; the API enforces company/location rules.
  const saveCalendar = async (event) => {
    event.preventDefault();
    if (
      !calendarForm.company_id ||
      !calendarForm.location.trim() ||
      !calendarForm.calendar_name.trim() ||
      !calendarForm.calendar_year ||
      (!editingCalendarId && !calendarForm.religion)
    ) {
      setFeedback({
        type: "danger",
        message: "Religion, company, location, calendar name and year are required.",
      });
      return;
    }
    if (duplicateCalendar) {
      setFeedback({ type: "danger", message: `A ${duplicateCalendar.religion} calendar for ${duplicateCalendar.calendar_year} already exists. Open it to make changes.` });
      return;
    }
    setIsSaving(true);
    try {
      const response = await fetch(
        editingCalendarId
          ? `${API_ROOT}/holiday-calendars/${editingCalendarId}`
          : `${API_ROOT}/holiday-calendars`,
        {
          method: editingCalendarId ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...calendarForm,
            calendar_year: Number(calendarForm.calendar_year),
            description: calendarForm.description || null,
            religion: calendarForm.religion || null,
          }),
        },
      );
      if (!response.ok) throw new Error(await readError(response));
      const savedCalendar = await response.json();
      setCalendars((current) =>
        editingCalendarId
          ? current.map((calendar) =>
              calendar.id === editingCalendarId ? savedCalendar : calendar,
            )
          : [savedCalendar, ...current],
      );
      setSelectedCalendarId(savedCalendar.id);
      setFeedback({
        type: "success",
        message: editingCalendarId
          ? `${savedCalendar.calendar_name} updated successfully.`
          : `${savedCalendar.calendar_name} created as a draft. Add holidays, then publish it.`,
      });
      setIsCalendarFormOpen(false);
      loadOptions();
    } catch (error) {
      setFeedback({
        type: "danger",
        message: error.message || "Unable to save holiday calendar.",
      });
    } finally {
      setIsSaving(false);
    }
  };

  const saveHoliday = async (event) => {
    event.preventDefault();
    if (
      !selectedCalendar ||
      !holidayForm.holiday_date ||
      !holidayForm.holiday_name.trim()
    ) {
      setFeedback({
        type: "danger",
        message: "Select a calendar and enter the holiday date and name.",
      });
      return;
    }
    if (!holidayForm.holiday_date.startsWith(String(selectedCalendar.calendar_year))) {
      setFeedback({ type: "danger", message: `The holiday date must be in ${selectedCalendar.calendar_year}.` });
      return;
    }
    if (editingHolidayId && selectedCalendar.status === "Published") {
      askConfirm({
        title: "Update published holiday",
        message: `Employees${isReligionCalendar ? ` of the ${selectedCalendar.religion} religion` : ""} will be notified of this change.`,
        confirmLabel: "Save changes",
        requireReason: true,
        onConfirm: (reason) => submitHoliday(reason),
      });
      return;
    }
    if (!editingHolidayId && selectedCalendar.status === "Published") {
      askConfirm({
        title: "Add holiday to published calendar",
        message: `${holidayForm.holiday_name.trim()} becomes an official holiday immediately and employees${isReligionCalendar ? ` of the ${selectedCalendar.religion} religion` : ""} will be notified.`,
        confirmLabel: "Add holiday",
        onConfirm: () => submitHoliday(null),
      });
      return;
    }
    await submitHoliday(null);
  };

  const submitHoliday = async (reason) => {
    setIsSaving(true);
    try {
      const religionType = holidayForm.holiday_type === "Religion";
      const payload = {
        ...holidayForm,
        calendar_id: selectedCalendar.id,
        description: holidayForm.description || null,
        // Holidays in a religion calendar apply to that whole religion.
        applicability: isReligionCalendar
          ? "All Employees"
          : religionType
            ? "Selected Religions"
            : holidayForm.applicability || "All Employees",
        applicable_religions: isReligionCalendar
          ? [selectedCalendar.religion]
          : religionType || holidayForm.applicability === "Selected Religions"
            ? (holidayForm.applicable_religion || "").split(",").map((item) => item.trim()).filter(Boolean)
            : [],
        applicable_employee_groups: (holidayForm.applicable_employee_groups_text || "")
          .split(",").map((item) => item.trim()).filter(Boolean),
        is_company_holiday: Boolean(holidayForm.is_company_holiday),
      };
      delete payload.applicable_employee_groups_text;
      const response = await fetch(
        editingHolidayId
          ? `${API_ROOT}/holidays/${editingHolidayId}${reason ? `?reason=${encodeURIComponent(reason)}` : ""}`
          : `${API_ROOT}/holiday-calendars/${selectedCalendar.id}/holidays`,
        {
          method: editingHolidayId ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      if (!response.ok) throw new Error(await readError(response));
      const savedHoliday = await response.json();
      setHolidays((current) =>
        editingHolidayId
          ? current.map((holiday) =>
              holiday.id === editingHolidayId ? savedHoliday : holiday,
            )
          : [...current, savedHoliday].sort((left, right) =>
              left.holiday_date.localeCompare(right.holiday_date),
            ),
      );
      setFeedback({
        type: "success",
        message: `${savedHoliday.holiday_name} ${editingHolidayId ? "updated" : "added"} successfully.`,
      });
      setIsHolidayFormOpen(false);
    } catch (error) {
      setFeedback({
        type: "danger",
        message: error.message || "Unable to save holiday.",
      });
    } finally {
      setIsSaving(false);
    }
  };

  const deactivateCalendar = () => {
    if (!selectedCalendar) return;
    askConfirm({
      title: "Delete draft calendar",
      message: `Delete the draft ${selectedCalendar.calendar_name}? It is hidden from active calendars and kept for audit.`,
      confirmLabel: "Delete draft",
      variant: "danger",
      onConfirm: performDeactivateCalendar,
    });
  };

  const performDeactivateCalendar = async () => {
    try {
      const response = await fetch(
        `${API_ROOT}/holiday-calendars/${selectedCalendar.id}`,
        { method: "DELETE" },
      );
      if (!response.ok) throw new Error(await readError(response));
      const remaining = calendars.filter(
        (calendar) => calendar.id !== selectedCalendar.id,
      );
      setCalendars(remaining);
      setSelectedCalendarId(remaining[0]?.id || "");
      setFeedback({
        type: "success",
        message: `${selectedCalendar.calendar_name} deleted. It is kept for audit history.`,
      });
      loadOptions();
    } catch (error) {
      setFeedback({
        type: "danger",
        message: error.message || "Unable to deactivate calendar.",
      });
    }
  };

  const publishCalendar = () => {
    if (!selectedCalendar) return;
    const liveCount = holidays.filter((item) => item.status !== "Cancelled").length;
    askConfirm({
      title: "Publish holiday calendar",
      message: isReligionCalendar
        ? `Publish ${calendarTitle(selectedCalendar)} with ${liveCount} holiday(s)? It applies to every employee whose religion is ${selectedCalendar.religion}, appears in their attendance calendar and they are notified.`
        : `Publish the ${selectedCalendar.calendar_year} holiday calendar? Employees will be able to view eligible holidays.`,
      confirmLabel: "Publish",
      variant: "success",
      onConfirm: performPublishCalendar,
    });
  };

  const performPublishCalendar = async () => {
    try {
      const response = await fetch(
        `${API_ROOT}/holiday-calendars/${selectedCalendar.id}/publish`,
        { method: "POST" },
      );
      if (!response.ok) throw new Error(await readError(response));
      const published = await response.json();
      setCalendars((current) => current.map((item) =>
        item.id === published.id ? published : item,
      ));
      setHolidays((current) => current.map((item) =>
        item.status === "Cancelled" ? item : { ...item, status: "Published" },
      ));
      setFeedback({ type: "success", message: `${calendarTitle(published)} published successfully.` });
      loadOptions();
    } catch (error) {
      setFeedback({ type: "danger", message: error.message || "Unable to publish the calendar." });
    }
  };

  const deactivateHoliday = (holiday) => {
    const published = selectedCalendar?.status === "Published";
    askConfirm({
      title: "Delete holiday",
      message: published
        ? `Remove ${holiday.holiday_name} from this published calendar? It becomes a working day and affected employees are notified. Attendance already recorded is not changed.`
        : `Remove ${holiday.holiday_name} from this draft calendar?`,
      confirmLabel: "Delete holiday",
      variant: "danger",
      requireReason: published,
      onConfirm: (reason) => performDeactivateHoliday(holiday, reason),
    });
  };

  const performDeactivateHoliday = async (holiday, reason) => {
    try {
      const response = await fetch(
        `${API_ROOT}/holidays/${holiday.id}${reason ? `?reason=${encodeURIComponent(reason)}` : ""}`,
        { method: "DELETE" },
      );
      if (!response.ok) throw new Error(await readError(response));
      setHolidays((current) => current.map((item) =>
        item.id === holiday.id ? { ...item, status: "Cancelled" } : item,
      ));
      setFeedback({
        type: "success",
        message: `${holiday.holiday_name} removed successfully.`,
      });
    } catch (error) {
      setFeedback({
        type: "danger",
        message: error.message || "Unable to remove holiday.",
      });
    }
  };

  const assignEmployee = async (event) => {
    event.preventDefault();
    if (!assignmentEmployeeId || !selectedCalendar) return;
    try {
      const response = await fetch(`${API_ROOT}/employee-holiday-calendars`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          employee_id: assignmentEmployeeId,
          calendar_id: selectedCalendar.id,
        }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const assignment = await response.json();
      setAssignments((current) => [assignment, ...current]);
      setAssignmentEmployeeId("");
      setIsAssignFormOpen(false);
      setFeedback({
        type: "success",
        message: "Employee assigned to the holiday calendar.",
      });
    } catch (error) {
      setFeedback({
        type: "danger",
        message: error.message || "Unable to assign employee.",
      });
    }
  };

  const unassignEmployee = (assignment) => {
    askConfirm({
      title: "Remove assignment",
      message: `Remove ${employeeName(assignment.employee_id)} from this calendar?`,
      confirmLabel: "Remove",
      variant: "danger",
      onConfirm: () => performUnassignEmployee(assignment),
    });
  };

  const performUnassignEmployee = async (assignment) => {
    try {
      const response = await fetch(
        `${API_ROOT}/employee-holiday-calendars/${assignment.id}`,
        { method: "DELETE" },
      );
      if (!response.ok) throw new Error(await readError(response));
      setAssignments((current) =>
        current.filter((item) => item.id !== assignment.id),
      );
      setFeedback({ type: "success", message: "Employee assignment removed." });
    } catch (error) {
      setFeedback({
        type: "danger",
        message: error.message || "Unable to remove assignment.",
      });
    }
  };

  return (
    <section className="holiday-page">
      <div className="holiday-heading d-flex flex-wrap justify-content-between align-items-start gap-3 mb-4">
        <div>
          <p className="eyebrow mb-1">Organization Setup</p>
          <h2 className="mb-1">Holiday Calendar</h2>
          <p className="text-muted mb-0">
            Create one holiday calendar per religion and year. Published holidays apply automatically to employees of that religion.
          </p>
        </div>
        {canManage && <button
          type="button"
          className="btn btn-primary"
          onClick={() => openCalendarForm()}
        >
          <i className="bi bi-calendar-plus me-2" />
          Add calendar
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

      <div className="row g-4 align-items-start">
        <div className="col-xl-4">
          <div className="card holiday-card border-0 shadow-sm">
            <div className="card-body p-0">
              <div className="holiday-list-header p-4">
                <h5 className="mb-1">Calendars</h5>
                <small className="text-muted">
                  {calendars.length} active calendars
                </small>
                <input
                  className="form-control mt-3"
                  type="search"
                  placeholder="Search calendars..."
                  aria-label="Search calendars"
                  value={calendarSearch}
                  onChange={(event) => setCalendarSearch(event.target.value)}
                />
              </div>
              <div className="holiday-calendar-list">
                {isLoading ? (
                  <p className="text-muted text-center p-4 mb-0">
                    Loading calendars...
                  </p>
                ) : filteredCalendars.length === 0 ? (
                  <p className="text-muted text-center p-4 mb-0">
                    No calendars available.
                  </p>
                ) : (
                  filteredCalendars.map((calendar) => (
                    <button
                      type="button"
                      className={`holiday-calendar-item ${calendar.id === selectedCalendarId ? "active" : ""}`}
                      key={calendar.id}
                      onClick={() => {
                        setSelectedCalendarId(calendar.id);
                        setIsHolidayFormOpen(false);
                      }}
                    >
                      <span className="holiday-calendar-icon">
                        <i className="bi bi-calendar3" />
                      </span>
                      <span className="holiday-calendar-copy">
                        <strong>{calendar.calendar_name}</strong>
                        <small>{calendarTitle(calendar)}</small>
                        <small>
                          <span className={`badge ${calendar.status === "Published" ? "text-bg-success" : "text-bg-secondary"} me-1`}>
                            {calendar.status || "Draft"}
                          </span>
                          {companyName(calendar.company_id)} · {calendar.location}
                        </small>
                      </span>
                      <i className="bi bi-chevron-right" />
                    </button>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>

        <div className="col-xl-8">
          {selectedCalendar ? (
            <>
              <div className="card holiday-card border-0 shadow-sm mb-4">
                <div className="card-body p-4">
                  <div className="d-flex flex-wrap justify-content-between align-items-start gap-3">
                    <div>
                      <p className="eyebrow mb-1">
                        {calendarTitle(selectedCalendar)}
                      </p>
                      <h4 className="mb-1">{selectedCalendar.calendar_name}</h4>
                      <p className="text-muted mb-0">
                        <i className="bi bi-building me-2" />
                        {companyName(selectedCalendar.company_id)}{" "}
                        <span className="mx-2">·</span>
                        <i className="bi bi-geo-alt me-1" />
                        {selectedCalendar.location}
                      </p>
                      {selectedCalendar.description && (
                        <p className="small text-muted mt-2 mb-0">
                          {selectedCalendar.description}
                        </p>
                      )}
                      {selectedCalendar.published_at && (
                        <p className="small text-muted mt-1 mb-0">
                          Published {new Date(selectedCalendar.published_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" })}
                          {selectedCalendar.updated_at ? ` · Last edited ${new Date(selectedCalendar.updated_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" })}` : ""}
                        </p>
                      )}
                    </div>
                    <div className="holiday-actions d-flex flex-wrap align-items-center gap-2">
                      <span className={`badge ${selectedCalendar.status === "Published" ? "text-bg-success" : "text-bg-secondary"}`}>
                        {selectedCalendar.status || "Draft"}
                      </span>
                      {canManage && selectedCalendar.status !== "Published" && <button
                        type="button"
                        className="btn btn-sm btn-success"
                        onClick={publishCalendar}
                        disabled={isSaving}
                      >
                        Publish
                      </button>}
                      {canManage && <button
                        type="button"
                        className="btn btn-sm btn-outline-primary"
                        onClick={() => openCalendarForm(selectedCalendar)}
                      >
                        <i className="bi bi-pencil me-1" />
                        Edit
                      </button>}
                      {canManage && selectedCalendar.status !== "Published" && <button
                        type="button"
                        className="btn btn-sm btn-outline-danger"
                        onClick={deactivateCalendar}
                      >
                        <i className="bi bi-trash3 me-1" />
                        Delete draft
                      </button>}
                    </div>
                  </div>
                </div>
              </div>
              <div className="card holiday-card border-0 shadow-sm mb-4">
                <div className="card-body p-0">
                  <div className="holiday-section-header d-flex flex-wrap justify-content-between align-items-center gap-3 p-4">
                    <div>
                      <h5 className="mb-1">Holiday entries</h5>
                      <small className="text-muted">
                        {holidays.length} holidays in this calendar
                      </small>
                    </div>
                    {canManage && (selectedCalendar.status !== "Published" || isReligionCalendar) && (
                      <div className="d-flex flex-wrap gap-2">
                        {selectedCalendar.status !== "Published" && <button
                          type="button"
                          className="btn btn-outline-primary btn-sm"
                          onClick={loadPublicHolidaySuggestions}
                          disabled={loadingSuggestions}
                        >
                          {loadingSuggestions ? "Loading suggestions..." : "Suggest public holidays"}
                        </button>}
                        <button
                          type="button"
                          className="btn btn-primary btn-sm"
                          onClick={() => openHolidayForm()}
                        >
                          <i className="bi bi-plus-lg me-1" />
                          Add holiday
                        </button>
                      </div>
                    )}
                  </div>
                  {suggestedHolidays.length > 0 && (
                    <div className="border rounded mx-4 mb-3 p-3">
                      <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-2">
                        <strong>Review public holiday suggestions</strong>
                        <button
                          type="button"
                          className="btn btn-sm btn-primary"
                          onClick={saveSelectedSuggestions}
                          disabled={isSaving}
                        >
                          Add selected as draft
                        </button>
                      </div>
                      <p className="small text-muted">
                        Suggestions from Nager.Date are not automatically company holidays. Select only entries that company policy approves.
                      </p>
                      <div className="d-flex flex-column gap-2">
                        {suggestedHolidays.map((suggestion) => (
                          <label className="form-check" key={suggestion.key}>
                            <input
                              className="form-check-input"
                              type="checkbox"
                              checked={suggestion.selected}
                              onChange={(event) => setSuggestedHolidays((current) =>
                                current.map((item) => item.key === suggestion.key
                                  ? { ...item, selected: event.target.checked }
                                  : item,
                                ),
                              )}
                            />
                            <span className="form-check-label">
                              {suggestion.holiday_date} · {suggestion.holiday_name}
                            </span>
                          </label>
                        ))}
                      </div>
                    </div>
                  )}
                  {isHolidayFormOpen && (
                    <form
                      className="holiday-inline-form p-4"
                      onSubmit={saveHoliday}
                    >
                      <div className="row g-3">
                        <div className="col-md-4">
                          <label className="form-label" htmlFor="holiday-date">
                            Date
                          </label>
                          <input
                            id="holiday-date"
                            className="form-control"
                            type="date"
                            min={[new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" }), `${selectedCalendar.calendar_year}-01-01`].sort()[1]}
                            max={`${selectedCalendar.calendar_year}-12-31`}
                            name="holiday_date"
                            value={holidayForm.holiday_date}
                            onChange={handleHolidayChange}
                          />
                        </div>
                        <div className="col-md-8">
                          <label className="form-label" htmlFor="holiday-name">
                            Holiday name
                          </label>
                          <input
                            id="holiday-name"
                            className="form-control"
                            name="holiday_name"
                            value={holidayForm.holiday_name}
                            onChange={handleHolidayChange}
                          />
                        </div>
                        <div className="col-md-4">
                          <label className="form-label" htmlFor="holiday-type">
                            Type
                          </label>
                          <select
                            id="holiday-type"
                            className="form-select"
                            name="holiday_type"
                            value={holidayForm.holiday_type}
                            onChange={handleHolidayChange}
                          >
                            <option>Public</option>
                            <option>Company</option>
                            <option>Optional</option>
                            <option>Religion</option>
                          </select>
                        </div>
                        {isReligionCalendar ? (
                          <div className="col-md-8">
                            <span className="form-label d-block">Applies to</span>
                            <div className="form-control bg-light">All employees whose religion is {selectedCalendar.religion}</div>
                          </div>
                        ) : <>
                        <div className="col-md-4">
                          <label className="form-label" htmlFor="holiday-applicability">
                            Applicability
                          </label>
                          <select
                            id="holiday-applicability"
                            className="form-select"
                            name="applicability"
                            value={holidayForm.holiday_type === "Religion" ? "Selected Religions" : holidayForm.applicability}
                            onChange={handleHolidayChange}
                            disabled={holidayForm.holiday_type === "Religion"}
                          >
                            <option>All Employees</option>
                            <option>Selected Religions</option>
                            <option>Selected Employee Groups</option>
                          </select>
                        </div>
                        {(holidayForm.holiday_type === "Religion" || holidayForm.applicability === "Selected Religions") && (
                          <div className="col-md-6">
                            <label className="form-label" htmlFor="holiday-religion">
                              Applicable religions
                            </label>
                            <input
                              id="holiday-religion"
                              className="form-control"
                              name="applicable_religion"
                              value={holidayForm.applicable_religion || ""}
                              onChange={handleHolidayChange}
                              placeholder="Comma-separated, e.g. Hindu, Christian"
                            />
                          </div>
                        )}
                        {holidayForm.applicability === "Selected Employee Groups" && (
                          <div className="col-md-6">
                            <label className="form-label" htmlFor="holiday-groups">
                              Applicable employee groups
                            </label>
                            <input
                              id="holiday-groups"
                              className="form-control"
                              name="applicable_employee_groups_text"
                              value={holidayForm.applicable_employee_groups_text || ""}
                              onChange={handleHolidayChange}
                              placeholder="Comma-separated department, designation, or team"
                            />
                          </div>
                        )}
                        </>}
                        <div className="col-md-8">
                          <label
                            className="form-label"
                            htmlFor="holiday-description"
                          >
                            Details
                          </label>
                          <input
                            id="holiday-description"
                            className="form-control"
                            name="description"
                            value={holidayForm.description}
                            onChange={handleHolidayChange}
                          />
                        </div>
                        <div className="col-12">
                          <label className="form-check form-check-inline">
                            <input
                              className="form-check-input"
                              type="checkbox"
                              name="is_company_holiday"
                              checked={Boolean(holidayForm.is_company_holiday)}
                              onChange={handleHolidayChange}
                            />
                            <span className="form-check-label">
                              Company holiday
                            </span>
                          </label>
                          <label className="form-check form-check-inline">
                            <input
                              className="form-check-input"
                              type="checkbox"
                              name="is_optional"
                              checked={holidayForm.is_optional}
                              onChange={handleHolidayChange}
                            />
                            <span className="form-check-label">
                              Optional holiday
                            </span>
                          </label>
                        </div>
                      </div>
                      <div className="d-flex justify-content-end gap-2 mt-3">
                        <button
                          type="button"
                          className="btn btn-outline-secondary btn-sm"
                          onClick={() => setIsHolidayFormOpen(false)}
                        >
                          Cancel
                        </button>
                        <button
                          type="submit"
                          className="btn btn-primary btn-sm"
                          disabled={isSaving}
                        >
                          {isSaving ? "Saving..." : "Save holiday"}
                        </button>
                      </div>
                    </form>
                  )}
                  <div className="table-responsive">
                    <table className="holiday-table table align-middle mb-0">
                      <thead>
                        <tr>
                          <th>Date</th>
                          <th>Holiday</th>
                          <th>Type</th>
                          <th>Details</th>
                          <th className="text-end">Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {holidays.length === 0 ? (
                          <tr>
                            <td
                              colSpan="5"
                              className="text-center text-muted py-5"
                            >
                              No holidays added to this calendar.
                            </td>
                          </tr>
                        ) : (
                          holidays.map((holiday) => (
                            <tr key={holiday.id}>
                              <td className="text-nowrap">
                                <strong>
                                  {new Date(
                                    `${holiday.holiday_date}T00:00:00`,
                                  ).toLocaleDateString(undefined, {
                                    month: "short",
                                    day: "numeric",
                                  })}
                                </strong>
                                <small className="d-block text-muted">
                                  {holiday.holiday_date.slice(0, 4)}
                                </small>
                              </td>
                              <td style={{ minWidth: 160, overflowWrap: "break-word" }}>
                                <strong>{holiday.holiday_name}</strong>
                                {holiday.is_optional && (
                                  <span className="badge text-bg-light ms-2">
                                    Optional
                                  </span>
                                )}
                              </td>
                              <td>
                                <span className="holiday-type">
                                  {holiday.holiday_type}
                                </span>
                              </td>
                              <td className="text-muted">
                                {holiday.description || "—"}
                                <small className="d-block">
                                  {holiday.status || "Draft"} · {isReligionCalendar ? `${selectedCalendar.religion} employees` : holiday.applicability || "All Employees"}
                                  {holiday.is_company_holiday ? " · Company holiday" : ""}
                                </small>
                              </td>
                              <td className="text-end text-nowrap">
                                {canManage && holiday.status !== "Cancelled" && <>
                                <button
                                  type="button"
                                  className="btn btn-sm btn-link text-primary"
                                  onClick={() => openHolidayForm(holiday)}
                                  aria-label={`Edit ${holiday.holiday_name}`}
                                >
                                  <i className="bi bi-pencil-square" />
                                </button>
                                <button
                                  type="button"
                                  className="btn btn-sm btn-link text-danger"
                                  onClick={() => deactivateHoliday(holiday)}
                                  aria-label={`Remove ${holiday.holiday_name}`}
                                >
                                  <i className="bi bi-trash3" />
                                </button>
                                </>}
                              </td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
              <div className="card holiday-card border-0 shadow-sm mb-4">
                <div className="card-body p-4">
                  <div className="d-flex flex-wrap justify-content-between align-items-center gap-3 mb-3">
                    <div>
                      <p className="eyebrow mb-1">Year at a glance</p>
                      <h5 className="mb-0">
                        {selectedCalendar.calendar_year} · {selectedCalendar.state || selectedCalendar.location}, {selectedCalendar.country || "India"}
                      </h5>
                    </div>
                    <div className="d-flex flex-wrap gap-3 small">
                      <span><i className="bi bi-square-fill text-success me-1" />Company holiday</span>
                      <span><i className="bi bi-square-fill text-info me-1" />Public holiday</span>
                      <span><i className="bi bi-circle-fill text-danger me-1" />{weeklyOffLabel(weeklyOffDays)}</span>
                      <span><i className="bi bi-square border border-primary border-2 me-1" />Today</span>
                    </div>
                  </div>
                  <div className="row row-cols-1 row-cols-md-2 row-cols-xl-3 g-3">
                    {monthNames.map((_, monthIndex) =>
                      renderCalendarMonth(Number(selectedCalendar.calendar_year), monthIndex),
                    )}
                  </div>
                </div>
              </div>
              {isReligionCalendar ? (
                <div className="card holiday-card border-0 shadow-sm">
                  <div className="card-body p-4">
                    <h5 className="mb-1">Who this calendar applies to</h5>
                    <p className="text-muted mb-0">
                      Every active employee whose religion is <strong>{selectedCalendar.religion}</strong> in Employee Master.
                      {selectedCalendar.status === "Published"
                        ? " Their attendance calendar shows these holidays, and they are notified when holidays are added, changed or removed."
                        : " Nothing is visible to employees until the calendar is published."}
                    </p>
                  </div>
                </div>
              ) : (
              <div className="card holiday-card border-0 shadow-sm">
                <div className="card-body p-0">
                  <div className="holiday-section-header d-flex flex-wrap justify-content-between align-items-center gap-3 p-4">
                    <div>
                      <h5 className="mb-1">Employee assignments</h5>
                      <small className="text-muted">
                        {calendarAssignments.length} employees assigned
                      </small>
                    </div>
                    {canManage && selectedCalendar.status !== "Published" && <button
                      type="button"
                      className="btn btn-outline-primary btn-sm"
                      onClick={() => setIsAssignFormOpen((current) => !current)}
                    >
                      <i className="bi bi-person-plus me-1" />
                      Assign employee
                    </button>}
                  </div>
                  {canManage && isAssignFormOpen && (
                    <form
                      className="holiday-inline-form p-4"
                      onSubmit={assignEmployee}
                    >
                      <label
                        className="form-label"
                        htmlFor="assignment-employee"
                      >
                        Employee
                      </label>
                      <div className="d-flex gap-2">
                        <select
                          id="assignment-employee"
                          className="form-select"
                          value={assignmentEmployeeId}
                          onChange={(event) =>
                            setAssignmentEmployeeId(event.target.value)
                          }
                        >
                          <option value="">Select employee</option>
                          {availableEmployees.map((employee) => (
                            <option key={employee.id} value={employee.id}>
                              {employee.employee_name} · {employee.employee_id}
                            </option>
                          ))}
                        </select>
                        <button
                          type="submit"
                          className="btn btn-primary text-nowrap"
                        >
                          Assign
                        </button>
                      </div>
                    </form>
                  )}
                  <div className="assignment-list">
                    {calendarAssignments.length === 0 ? (
                      <p className="text-muted text-center py-4 mb-0">
                        No employees assigned yet.
                      </p>
                    ) : (
                      calendarAssignments.map((assignment) => (
                        <div className="assignment-row" key={assignment.id}>
                          <span className="assignment-avatar">
                            {employeeName(assignment.employee_id).charAt(0)}
                          </span>
                          <span>
                            <strong>
                              {employeeName(assignment.employee_id)}
                            </strong>
                            <small>
                              Assigned{" "}
                              {new Date(
                                assignment.assigned_at,
                              ).toLocaleDateString()}
                            </small>
                          </span>
                          {canManage && <button
                            type="button"
                            className="btn btn-sm btn-link text-danger ms-auto"
                            onClick={() => unassignEmployee(assignment)}
                            aria-label={`Remove ${employeeName(assignment.employee_id)} assignment`}
                          >
                            <i className="bi bi-person-dash" />
                          </button>}
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </div>
              )}
            </>
          ) : (
            <div className="card holiday-card border-0 shadow-sm">
              <div className="empty-holiday p-5">
                <i className="bi bi-calendar2-x" />
                <h5>No holiday calendar selected</h5>
                <p className="text-muted">
                  Create a holiday calendar for a religion and year to start adding
                  holidays.
                </p>
                {canManage && <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => openCalendarForm()}
                >
                  Create calendar
                </button>}
              </div>
            </div>
          )}
        </div>
      </div>

      {isCalendarFormOpen && (
        <div className="card holiday-form-card border-0 shadow-sm mt-4">
          <div className="card-body p-4">
            <div className="d-flex justify-content-between align-items-center mb-4">
              <div>
                <p className="eyebrow mb-1">
                  {editingCalendarId ? "Update calendar" : "New calendar"}
                </p>
                <h5 className="mb-0">
                  {editingCalendarId
                    ? "Edit Holiday Calendar"
                    : "Create Holiday Calendar"}
                </h5>
              </div>
              <button
                type="button"
                className="btn-close"
                aria-label="Close calendar form"
                onClick={() => setIsCalendarFormOpen(false)}
              />
            </div>
            <form onSubmit={saveCalendar}>
              <div className="row g-3">
                <div className="col-md-6">
                  <label className="form-label" htmlFor="calendar-religion">
                    Religion
                  </label>
                  <select
                    id="calendar-religion"
                    className="form-select"
                    name="religion"
                    value={calendarForm.religion || ""}
                    onChange={handleCalendarChange}
                    disabled={Boolean(editingCalendarId) && (selectedCalendar?.status === "Published" || !selectedCalendar?.religion_key)}
                    required={!editingCalendarId}
                  >
                    <option value="">{editingCalendarId && !calendarForm.religion ? "Company-wide (existing calendar)" : "Select religion"}</option>
                    {[...new Set([...options.religions, ...(calendarForm.religion ? [calendarForm.religion] : [])])].map((religion) => (
                      <option key={religion} value={religion}>{religion}</option>
                    ))}
                  </select>
                  <div className="form-text">
                    {options.religions.length
                      ? "Only religions assigned to active employees are listed."
                      : "No employee has a religion recorded yet. Add it in Employee Master first."}
                  </div>
                </div>
                <div className="col-md-6">
                  <label className="form-label" htmlFor="calendar-year">
                    Calendar year
                  </label>
                  <select
                    id="calendar-year"
                    className="form-select"
                    name="calendar_year"
                    value={calendarForm.calendar_year}
                    onChange={handleCalendarChange}
                    disabled={Boolean(editingCalendarId) && selectedCalendar?.status === "Published"}
                  >
                    {[...new Set([...options.allowed_years, Number(calendarForm.calendar_year)])].filter(Boolean).map((year) => (
                      <option key={year} value={year} disabled={!options.allowed_years.includes(year)}>{year}</option>
                    ))}
                  </select>
                  <div className="form-text">
                    {options.allowed_years.length
                      ? `Calendars can be created only for ${options.allowed_years[0]} (current year) and ${options.allowed_years[1]} (next year).`
                      : "Allowed years could not be loaded."}
                    {editingCalendarId && selectedCalendar?.status === "Published" ? " The religion and year of a published calendar are fixed." : ""}
                  </div>
                </div>
                {duplicateCalendar && (
                  <div className="col-12">
                    <div className="alert alert-warning d-flex flex-wrap align-items-center justify-content-between gap-2 py-2 mb-0">
                      <span>A {duplicateCalendar.religion} calendar for {duplicateCalendar.calendar_year} already exists ({duplicateCalendar.status || "Draft"}).</span>
                      <button
                        type="button"
                        className="btn btn-sm btn-outline-dark"
                        onClick={() => { setSelectedCalendarId(duplicateCalendar.id); setIsCalendarFormOpen(false); }}
                      >
                        Open existing calendar
                      </button>
                    </div>
                  </div>
                )}
                <div className="col-md-6">
                  <label className="form-label" htmlFor="calendar-country">
                    Country
                  </label>
                  <select
                    id="calendar-country"
                    className="form-select"
                    name="country"
                    value={calendarForm.country}
                    onChange={(event) => setCalendarForm((current) => ({
                      ...current,
                      country: event.target.value,
                      state: "",
                    }))}
                  >
                    {countryOptions.map((item) => (
                      <option key={item.code} value={item.name} disabled={item.disabled}>{item.name}</option>
                    ))}
                  </select>
                </div>
                <div className="col-md-6">
                  <label className="form-label" htmlFor="calendar-state">
                    State / region
                  </label>
                  {calendarForm.country === "India" ? (
                    <select
                      id="calendar-state"
                      className="form-select"
                      name="state"
                      value={calendarForm.state || ""}
                      onChange={handleCalendarChange}
                      required
                    >
                      <option value="">Select state / union territory</option>
                      {indiaStates.map((state) => <option key={state}>{state}</option>)}
                    </select>
                  ) : (
                    <input
                      id="calendar-state"
                      className="form-control"
                      name="state"
                      value={calendarForm.state || ""}
                      onChange={handleCalendarChange}
                      placeholder="State / region"
                    />
                  )}
                </div>
                <div className="col-md-6">
                  <label className="form-label" htmlFor="calendar-company">
                    Company / legal entity
                  </label>
                  <select
                    id="calendar-company"
                    className="form-select"
                    name="company_id"
                    value={calendarForm.company_id}
                    onChange={handleCalendarChange}
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
                  <label className="form-label" htmlFor="calendar-location">
                    Location
                  </label>
                  <input
                    id="calendar-location"
                    className="form-control"
                    name="location"
                    value={calendarForm.location}
                    onChange={handleCalendarChange}
                    placeholder="Must match company location"
                  />
                </div>
                <div className="col-md-6">
                  <label className="form-label" htmlFor="calendar-name">
                    Calendar name
                  </label>
                  <input
                    id="calendar-name"
                    className="form-control"
                    name="calendar_name"
                    value={calendarForm.calendar_name}
                    onChange={handleCalendarChange}
                    placeholder="e.g. Hindu Festival Holidays"
                  />
                </div>
                <div className="col-12">
                  <label className="form-label" htmlFor="calendar-description">
                    Description
                  </label>
                  <textarea
                    id="calendar-description"
                    className="form-control"
                    name="description"
                    rows="2"
                    value={calendarForm.description}
                    onChange={handleCalendarChange}
                  />
                </div>
              </div>
              <div className="d-flex justify-content-end gap-2 mt-4">
                <button
                  type="button"
                  className="btn btn-outline-secondary"
                  onClick={() => setIsCalendarFormOpen(false)}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isSaving || Boolean(duplicateCalendar)}
                >
                  {isSaving ? "Saving..." : editingCalendarId ? "Save calendar" : "Create draft calendar"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {confirmDialog && (
        <div className="modal d-block" tabIndex="-1" role="dialog" aria-modal="true" aria-labelledby="holiday-confirm-title"
          style={{ backgroundColor: "rgba(15, 23, 42, 0.45)" }}>
          <div className="modal-dialog modal-dialog-centered">
            <div className="modal-content">
              <div className="modal-header">
                <h5 className="modal-title" id="holiday-confirm-title">{confirmDialog.title}</h5>
                <button type="button" className="btn-close" aria-label="Close" onClick={() => setConfirmDialog(null)} disabled={confirmBusy} />
              </div>
              <div className="modal-body">
                <p className="mb-2">{confirmDialog.message}</p>
                {confirmDialog.requireReason && (
                  <>
                    <label className="form-label" htmlFor="holiday-confirm-reason">Reason (shown to employees)</label>
                    <textarea id="holiday-confirm-reason" className="form-control" rows="2" maxLength={500}
                      value={confirmReason} onChange={(event) => setConfirmReason(event.target.value)} autoFocus />
                    {confirmReason.trim().length < 3 && <div className="form-text">Enter at least 3 characters.</div>}
                  </>
                )}
              </div>
              <div className="modal-footer flex-wrap">
                <button type="button" className="btn btn-outline-secondary" onClick={() => setConfirmDialog(null)} disabled={confirmBusy}>Cancel</button>
                <button type="button" className={`btn btn-${confirmDialog.variant || "primary"}`} onClick={runConfirm}
                  disabled={confirmBusy || (confirmDialog.requireReason && confirmReason.trim().length < 3)}>
                  {confirmBusy ? "Working..." : confirmDialog.confirmLabel || "Confirm"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

export default HolidayCalendarSetup;
