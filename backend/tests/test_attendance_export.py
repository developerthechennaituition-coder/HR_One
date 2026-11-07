from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from openpyxl import load_workbook

MODULE_PATH = Path(__file__).parents[1] / "app" / "attendance_export.py"
SPEC = spec_from_file_location("attendance_export_under_test", MODULE_PATH)
attendance_export = module_from_spec(SPEC)
SPEC.loader.exec_module(attendance_export)


def _report(name, user_id, date_text="2026-10-01"):
    return {
        "info": {
            "name": name, "user_id": user_id,
            "employee_code": "E005", "employee_id": f"EMP-{user_id}",
            "department_name": "Engineering",
        },
        "employee": {"name": name},
        "rows": [{
            "attendance_date": date_text,
            "day": "Thursday",
            "scheduled_work_start_time": "09:00",
            "scheduled_work_end_time": "18:00",
            "check_in_text": "09:00 AM",
            "check_out_text": "06:00 PM",
            "working_minutes": 480,
            "break_minutes": 30,
            "total_duration_minutes": 510,
            "break_details": "Lunch 01:00 PM - 01:30 PM",
            "permissions": [{"start_time": "11:00", "end_time": "11:30"}],
            "permission_total_minutes": 30,
            "permission_total_text": "00h 30m",
            "status": "Present",
        }],
    }


def test_all_employees_are_in_one_sheet_with_employee_sections():
    output = attendance_export.build_multi_employee_attendance_workbook(
        [_report("Test Employee", "005"), _report("Second Employee", "006", "2026-10-02")],
        company_name="Test Company", month=10, year=2026,
        generated_on="02-Nov-2026 06:00 PM",
    )
    workbook = load_workbook(output, data_only=True)
    assert workbook.sheetnames == ["Attendance Report"]
    sheet = workbook["Attendance Report"]

    assert sheet.freeze_panes == "A3"
    assert sheet["A1"].value == "Test Company - DAILY ATTENDANCE REPORT"
    assert "Reporting Month: October 2026" in sheet["A2"].value
    assert sheet["A4"].value == "EMPLOYEE 1: Test Employee"
    assert sheet["A5"].value == "EMPLOYEE DETAILS"
    assert sheet["A6"].value == "Employee Name"
    assert sheet["B6"].value == "Test Employee"
    assert sheet["E6"].value == "Employee ID"

    first_header_row = next(r for r in range(1, sheet.max_row + 1)
                            if sheet.cell(r, 1).value == "S.No.")
    headers = [sheet.cell(first_header_row, col).value for col in range(1, 22)]
    assert headers == [
        "S.No.", "Date", "Day", "In Time", "Out Time", "Total Duration",
        "Net Working Time", "Break Duration", "Working Intervals", "Break Details", "Permission Time",
        "Attendance Type", "Attendance Source", "Status", "Half-Day Present",
        "First Half Status", "Second Half Status",
        "Early Going By", "Late Going By", "Holiday / Leave Details", "Remarks",
    ]
    assert sheet.cell(first_header_row + 1, 2).value == "01-Oct-2026"
    assert sheet.cell(first_header_row + 1, 6).value == "8h 30m"
    assert sheet.cell(first_header_row + 1, 7).value == "8h 00m"
    assert sheet.cell(first_header_row + 1, 8).value == "0h 30m"
    assert sheet.cell(first_header_row + 1, 11).value == "00h 30m\nPermission Count: 1"
    assert sheet.cell(first_header_row + 1, 13).value == "—"
    assert "Permission Count" not in headers
    assert sheet.cell(first_header_row + 1, 9).value == "—"

    second_section = next(r for r in range(1, sheet.max_row + 1)
                          if sheet.cell(r, 1).value == "EMPLOYEE 2: Second Employee")
    assert sheet.cell(second_section + 2, 2).value == "Second Employee"
    assert "MONTHLY ATTENDANCE SUMMARY - Test Employee" in [
        sheet.cell(r, 1).value for r in range(1, sheet.max_row + 1)
    ]
    assert "MONTHLY ATTENDANCE SUMMARY - Second Employee" in [
        sheet.cell(r, 1).value for r in range(1, sheet.max_row + 1)
    ]
    assert sheet.print_title_rows == "$1:$2"


def test_duplicate_and_special_employee_names_remain_as_sections_in_one_sheet():
    output = attendance_export.build_multi_employee_attendance_workbook(
        [_report("Same Name", "001"), _report("Same Name", "002"),
         _report("A/Very:Long*Employee[Name] That Exceeds Limit", "003")],
        company_name="Test Company", month=10, year=2026,
        generated_on="02-Nov-2026 06:00 PM",
    )
    workbook = load_workbook(output, data_only=True)
    assert workbook.sheetnames == ["Attendance Report"]
    values = [workbook.active.cell(r, 1).value for r in range(1, workbook.active.max_row + 1)]
    assert "EMPLOYEE 1: Same Name" in values
    assert "EMPLOYEE 2: Same Name" in values
    assert any(str(value).startswith("EMPLOYEE 3: A/Very:Long") for value in values)


def test_half_day_composite_statuses_count_both_portions():
    metrics = dict(item for group in attendance_export._summary_metrics([
        {"status": "1/2 Present + 1/2 Absent"},
        {"status": "1/2 Present + 1/2 Medical Leave", "leave": {"is_medical": True}},
    ]) for item in group)
    assert metrics["Present Days"] == 1.0
    assert metrics["Absent Days"] == 0.5
    assert metrics["Medical Leave"] == 0.5


def test_holiday_leave_details_column_only_contains_real_holiday_or_leave_data():
    rows = [
        {
            "attendance_date": "2026-10-01", "status": "Present",
            "attendance_type": "OFFICE", "attendance_category": "Office",
            "attendance_source_label": "Manual", "leave_type_code": "CL",
            "holiday_name": "", "working_minutes": 480,
        },
        {
            "attendance_date": "2026-10-02", "status": "Holiday",
            "attendance_type": "—", "attendance_category": "—",
            "attendance_source_label": "—", "holiday_name": "Deepavali",
        },
        {
            "attendance_date": "2026-10-03", "status": "Leave",
            "leave": {
                "details": "Casual Leave (CL) · Full Day",
                "leave_type_code": "CL", "leave_type_name": "Casual Leave",
                "day_part": "full_day", "day_part_label": "Full Day",
            },
        },
        {
            "attendance_date": "2026-10-04", "status": "Weekly Off",
            "attendance_category": "—", "holiday_name": "",
        },
        {
            "attendance_date": "2026-10-05", "status": "Not Marked",
            "attendance_category": "—", "holiday_name": "",
        },
    ]
    report = {
        "info": {"name": "Employee", "employee_id": "EMP-1", "employee_code": "E001", "department_name": "Operations"},
        "employee": {"name": "Employee"},
        "rows": rows,
    }
    output = attendance_export.build_multi_employee_attendance_workbook(
        [report], company_name="Test Company", month=10, year=2026,
        generated_on="04-Nov-2026 11:00 AM",
    )
    sheet = load_workbook(output, data_only=True).active
    header_row = next(r for r in range(1, sheet.max_row + 1) if sheet.cell(r, 1).value == "S.No.")
    # Column 20 is Holiday / Leave Details; column 13 is Status.
    actual = [sheet.cell(header_row + offset, 20).value for offset in range(1, 6)]
    assert actual == ["—", "Deepavali", "Casual Leave (CL) · Full Day", "—", "—"]
    assert [sheet.cell(header_row + offset, 14).value for offset in range(1, 6)] == [
        "Present", "Holiday", "Leave", "Weekly Off", "Not Marked"
    ]


def test_attendance_type_is_readable_and_column_values_stay_in_their_columns():
    report = _report("Employee", "001")
    report["rows"][0].update({
        "attendance_type": "WFH", "attendance_source_label": "WFH (Face + GPS)",
        "status": "Present", "attendance_category": "Work From Home",
        "holiday_name": "", "leave_type_code": "",
        "permission_total_text": "00 Hours 30 Minutes",
        "remarks": "Approved by HR",
    })
    output = attendance_export.build_multi_employee_attendance_workbook(
        [report], company_name="Test Company", month=10, year=2026,
        generated_on="04-Nov-2026 11:00 AM",
    )
    sheet = load_workbook(output, data_only=True).active
    header_row = next(r for r in range(1, sheet.max_row + 1) if sheet.cell(r, 1).value == "S.No.")
    data = [sheet.cell(header_row + 1, col).value for col in range(1, 22)]
    assert data[0] == 1
    assert data[1] == "01-Oct-2026"
    assert data[5] == "8h 30m"  # Total Duration
    assert data[6] == "8h 00m"  # Net Working Time
    assert data[7] == "0h 30m"  # Break Duration
    assert data[10] == "00 Hours 30 Minutes\nPermission Count: 1"  # Permission Time
    assert data[11] == "Work From Home"  # Attendance Type
    assert data[12] == "WFH (Face + GPS)"  # Attendance Source
    assert data[13] == "Present"  # Status
    assert data[19] == "—"  # Holiday / Leave Details
    assert data[20] == "Approved by HR"  # Remarks


def test_employee_working_details_and_half_day_columns_are_exported():
    report = _report("Schedule Employee", "010")
    report["working_details"] = {
        "working_type": "Part Time",
        "scheduled_work_duration": 360,
        "half_day_working_duration": 180,
        "off_day_time": "18:00",
    }
    report["rows"][0].update({
        "working_type": "Part Time",
        "scheduled_work_start_time": "10:00",
        "scheduled_work_end_time": "16:00",
        "scheduled_work_duration": 360,
        "half_day_working_duration": 180,
        "half_day_present": "Yes – First Half",
        "first_half_status": "Present",
        "second_half_status": "Absent",
        "break_details": "Tea Break: 11:00 AM – 11:15 AM (0h 15m)\nTotal Break Duration: 0h 15m",
    })
    output = attendance_export.build_multi_employee_attendance_workbook(
        [report], company_name="Test Company", month=10, year=2026,
        generated_on="04-Nov-2026 11:00 AM",
    )
    sheet = load_workbook(output, data_only=True).active
    values = [sheet.cell(r, c).value for r in range(1, sheet.max_row + 1) for c in range(1, 22)]
    assert "Working Type" in values
    assert "Total Scheduled Working Duration" in values
    assert "Half-Day Working Duration" in values
    assert "Half Day Time" in values
    assert "Off Day Time" not in values
    header_row = next(r for r in range(1, sheet.max_row + 1) if sheet.cell(r, 1).value == "S.No.")
    data = [sheet.cell(header_row + 1, col).value for col in range(1, 22)]
    assert data[14] == "Yes – First Half"
    assert data[15] == "Present"
    assert data[16] == "Absent"
    assert "B1:" in data[9]
    assert "Total Break Duration" not in data[9]
    assert sheet.row_dimensions[header_row + 1].height >= 30


def test_monthly_summary_counts_each_half_as_half_day():
    metrics = dict(item for group in attendance_export._summary_metrics([
        {
            "status": "Present", "total_duration_minutes": 180,
            "working_minutes": 165, "break_minutes": 15,
            "half_day_present": "Yes – First Half",
            "first_half_status": "Present", "second_half_status": "Absent",
        },
        {
            "status": "Leave", "total_duration_minutes": 180,
            "working_minutes": 180, "break_minutes": 0,
            "half_day_present": "Yes – Second Half",
            "first_half_status": "Leave", "second_half_status": "Present",
            "leave": {"is_medical": False, "days": 0.5},
        },
        {
            "status": "Present", "total_duration_minutes": 120,
            "working_minutes": 120, "break_minutes": 0,
            "half_day_present": "No",
            "first_half_status": "Not Finalized", "second_half_status": "Not Finalized",
        },
    ]) for item in group)
    assert metrics["Total Duration"] == "8h 00m"
    assert metrics["Net Working Time"] == "7h 45m"
    assert metrics["Break Time"] == "0h 15m"
    assert metrics["Present Days"] == 1.0
    assert metrics["Absent Days"] == 0.5
    assert metrics["Leave Days"] == 0.5
    assert metrics["Half-Day Present"] == 2


def test_working_and_break_intervals_are_numbered_and_include_duration():
    report = _report("Intervals Employee", "011")
    report["rows"][0].update({
        "working_intervals": [
            {"from_time": "2026-10-01T09:00:00+05:30", "to_time": "2026-10-01T12:00:00+05:30", "duration_minutes": 180},
            {"from_time": "2026-10-01T13:00:00+05:30", "to_time": "2026-10-01T17:00:00+05:30", "duration_minutes": 240},
        ],
        "break_intervals": [
            {"from_time": "2026-10-01T12:00:00+05:30", "to_time": "2026-10-01T12:30:00+05:30", "duration_minutes": 30},
        ],
    })
    output = attendance_export.build_multi_employee_attendance_workbook(
        [report], company_name="Test Company", month=10, year=2026,
        generated_on="04-Nov-2026 11:00 AM",
    )
    sheet = load_workbook(output, data_only=True).active
    header_row = next(r for r in range(1, sheet.max_row + 1) if sheet.cell(r, 1).value == "S.No.")
    headers = [sheet.cell(header_row, col).value for col in range(1, 22)]
    row = [sheet.cell(header_row + 1, col).value for col in range(1, 22)]
    assert headers.index("Working Intervals") < headers.index("Break Details")
    assert headers.index("Working Intervals") == headers.index("Break Duration") + 1
    assert "WI1:" in row[headers.index("Working Intervals")]
    assert "WI2:" in row[headers.index("Working Intervals")]
    assert "(3h 00m)" in row[headers.index("Working Intervals")]
    assert "B1:" in row[headers.index("Break Details")]
    assert "(0h 30m)" in row[headers.index("Break Details")]



def test_working_and_break_intervals_are_multiline_wrapped_24_hour_times():
    report = _report("Intervals Employee", "007")
    report["rows"][0].update({
        "check_in_text": "2026-10-10T09:00:00+05:30",
        "check_out_text": "2026-10-10T18:00:00+05:30",
        "working_intervals": [
            {"from_time": "2026-10-10T09:00:00+05:30", "to_time": "2026-10-10T12:00:00+05:30", "duration": "3h 00m"},
            {"from_time": "2026-10-10T13:00:00+05:30", "to_time": "2026-10-10T15:00:00+05:30", "duration": "2h 00m"},
            {"from_time": "2026-10-10T15:30:00+05:30", "to_time": "2026-10-10T18:00:00+05:30", "duration": "2h 30m"},
        ],
        "break_intervals": [
            {"from_time": "2026-10-10T12:00:00+05:30", "to_time": "2026-10-10T13:00:00+05:30", "duration": "1h 00m"},
            {"from_time": "2026-10-10T15:00:00+05:30", "to_time": "2026-10-10T15:30:00+05:30", "duration": "0h 30m"},
        ],
    })
    output = attendance_export.build_multi_employee_attendance_workbook(
        [report], company_name="Test Company", month=10, year=2026,
        generated_on="10-Oct-2026 06:00 PM",
    )
    sheet = load_workbook(output, data_only=True).active
    header = next(r for r in range(1, sheet.max_row + 1) if sheet.cell(r, 1).value == "S.No.")
    row = header + 1
    assert sheet.cell(row, 4).value == "09:00:00"
    assert sheet.cell(row, 5).value == "18:00:00"
    work_lines = sheet.cell(row, 9).value.splitlines()
    break_lines = sheet.cell(row, 10).value.splitlines()
    assert len(work_lines) == 3
    assert work_lines[0].startswith("WI1: 09:00:00 - 12:00:00")
    assert work_lines[1].startswith("WI2: 13:00:00 - 15:00:00")
    assert work_lines[2].startswith("WI3: 15:30:00 - 18:00:00")
    assert len(break_lines) == 2
    assert break_lines[0].startswith("B1: 12:00:00 - 13:00:00")
    assert break_lines[1].startswith("B2: 15:00:00 - 15:30:00")
    assert sheet.cell(row, 9).alignment.wrap_text is True
    assert sheet.cell(row, 10).alignment.wrap_text is True
    assert sheet.cell(row, 9).alignment.vertical == "top"
    assert sheet.row_dimensions[row].height >= 66
