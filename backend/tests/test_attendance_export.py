from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from openpyxl import load_workbook

MODULE_PATH = Path(__file__).parents[1] / "app" / "attendance_export.py"
SPEC = spec_from_file_location("attendance_export_under_test", MODULE_PATH)
attendance_export = module_from_spec(SPEC)
SPEC.loader.exec_module(attendance_export)


def test_employee_sheet_freezes_identity_and_omits_identity_daily_columns():
    rows = [{
        "attendance_date": "2026-10-01",
        "scheduled_work_start_time": "09:00",
        "scheduled_work_end_time": "18:00",
        "check_in_text": "09:00 AM",
        "check_out_text": "06:00 PM",
        "working_minutes": 480,
        "break_minutes": 30,
        "total_duration_minutes": 510,
        "permissions": [],
        "permission_total_minutes": 0,
        "status": "Present",
    }]
    output = attendance_export.build_multi_employee_attendance_workbook(
        [{
            "info": {
                "name": "Test Employee",
                "employee_code": "E005",
                "employee_id": "EMP005",
                "department_name": "Engineering",
            },
            "employee": {"name": "Test Employee"},
            "rows": rows,
        }],
        company_name="Test Company",
        month=10,
        year=2026,
        generated_on="02-Nov-2026 06:00 PM",
    )
    workbook = load_workbook(output, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]

    assert sheet["A1"].value == "HRMS - DAILY ATTENDANCE REPORT - October 2026"
    assert sheet.freeze_panes == "A7"
    headers = [sheet.cell(6, col).value for col in range(1, 19)]
    assert "Employee ID" not in headers
    assert "Emp Code" not in headers
    assert "Employee Name" not in headers
    assert sheet["B7"].value.date() == date(2026, 10, 1)


def test_half_day_composite_statuses_count_both_portions():
    metrics = dict(attendance_export._summary_metrics([
        {"status": "1/2 Present + 1/2 Absent"},
        {"status": "1/2 Present + 1/2 Medical Leave", "leave": {"is_medical": True}},
    ]))
    assert metrics["Present Days"] == 1.0
    assert metrics["Absent Days"] == 0.5
    assert metrics["Medical Leave"] == 0.5
