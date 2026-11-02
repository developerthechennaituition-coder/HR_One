"""Excel workbook rendering for HRMS daily attendance exports.

This module deliberately contains presentation-only logic. Attendance calculation,
permissions, leave, employee lookup and authorization remain in attendance.py.
Each employee receives an individual worksheet so their identity/schedule header
can remain frozen while their daily attendance rows are scrolled.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


NAVY = "17365D"
BLUE = "D9EAF7"
PALE = "F3F6FA"
WHITE = "FFFFFF"
GREEN = "E2F0D9"
RED = "F4CCCC"
ORANGE = "FCE4D6"
PURPLE = "E4DFEC"
BORDER = "D9E1F2"


def _duration_text(minutes: Any) -> str:
    try:
        total = max(0, int(minutes or 0))
    except (TypeError, ValueError):
        total = 0
    return f"{total // 60}h {total % 60:02d}m"


def _time_text(value: Any) -> str:
    if value in (None, "", "—", "-"):
        return "—"
    raw = str(value).strip()
    # Preserve already formatted clock strings and convert 24-hour HH:MM values.
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", raw)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return datetime(2000, 1, 1, hour, minute).strftime("%I:%M %p")
    return raw


def _sheet_title(name: str, used: set[str]) -> str:
    cleaned = re.sub(r'[\\/*?:\[\]]', " ", str(name or "Employee")).strip()
    cleaned = cleaned[:31] or "Employee"
    candidate = cleaned
    suffix = 2
    while candidate.casefold() in used:
        tail = f" ({suffix})"
        candidate = f"{cleaned[:31-len(tail)]}{tail}"
        suffix += 1
    used.add(candidate.casefold())
    return candidate


def _status_fill(status: Any) -> str | None:
    text = str(status or "").casefold()
    if "present" in text:
        return GREEN
    if "absent" in text:
        return RED
    if "leave" in text:
        return ORANGE
    if "holiday" in text or "weekly off" in text:
        return BLUE
    return None


def _merge_value_pair(ws, row: int, start_col: int, label: str, value: Any,
                      label_fill: str = BLUE, value_fill: str = PALE) -> None:
    label_end = start_col
    value_start = start_col + 1
    value_end = start_col + 4
    ws.merge_cells(start_row=row, start_column=label_end, end_row=row, end_column=label_end)
    ws.merge_cells(start_row=row, start_column=value_start, end_row=row, end_column=value_end)
    label_cell = ws.cell(row, label_end, label)
    label_cell.font = Font(bold=True, color=NAVY, size=9)
    label_cell.fill = PatternFill("solid", fgColor=label_fill)
    label_cell.alignment = Alignment(vertical="center", wrap_text=True)
    value_cell = ws.cell(row, value_start, value if value not in (None, "") else "—")
    value_cell.font = Font(color="263445", size=9, bold=True)
    value_cell.fill = PatternFill("solid", fgColor=value_fill)
    value_cell.alignment = Alignment(vertical="center", wrap_text=True)
    for col in range(start_col, value_end + 1):
        ws.cell(row, col).border = Border(bottom=Side(style="thin", color=BORDER))


def _summary_metrics(rows: list[dict[str, Any]]) -> list[tuple[str, Any]]:
    working = sum(int(r.get("working_minutes") or 0) for r in rows)
    breaks = sum(int(r.get("break_minutes") or 0) for r in rows)
    elapsed = sum(int(r.get("total_duration_minutes") or 0) for r in rows)
    permission = sum(int(r.get("permission_total_minutes") or 0) for r in rows)
    permission_count = sum(len(r.get("permissions") or []) for r in rows)

    counts = {
        "Present Days": 0.0,
        "Absent Days": 0.0,
        "Leave Days": 0.0,
        "Medical Leave": 0.0,
        "Holidays": 0.0,
        "Weekly Off": 0.0,
    }
    for row in rows:
        status = str(row.get("status") or "Not Marked").casefold()
        leave = row.get("leave") or {}

        # Statuses may combine two half-day portions (for example,
        # "1/2 Present + 1/2 Absent"), so each category is evaluated independently.
        if "1/2 present" in status or "half present" in status:
            counts["Present Days"] += 0.5
        elif status == "present":
            counts["Present Days"] += 1

        if "1/2 absent" in status or "half absent" in status:
            counts["Absent Days"] += 0.5
        elif status == "absent":
            counts["Absent Days"] += 1

        if "1/2 medical leave" in status or "half medical leave" in status:
            counts["Medical Leave"] += 0.5
        elif "medical leave" in status or leave.get("is_medical"):
            counts["Medical Leave"] += float(leave.get("days") or 1)

        if (("1/2 leave" in status or "half leave" in status)
                and "medical leave" not in status):
            counts["Leave Days"] += 0.5
        elif status == "leave":
            counts["Leave Days"] += float(leave.get("days") or 1)

        if status == "holiday":
            counts["Holidays"] += 1
        if status in {"weekly off", "weekly-off", "week off"}:
            counts["Weekly Off"] += 1

    return [
        ("Total Duration", _duration_text(elapsed)),
        ("Net Working Time", _duration_text(working)),
        ("Break Time", _duration_text(breaks)),
        ("Present Days", counts["Present Days"]),
        ("Absent Days", counts["Absent Days"]),
        ("Leave Days", counts["Leave Days"]),
        ("Medical Leave", counts["Medical Leave"]),
        ("Holidays", counts["Holidays"]),
        ("Weekly Off", counts["Weekly Off"]),
        ("Permission Count", permission_count),
        ("Permission Duration", _duration_text(permission)),
    ]


def build_multi_employee_attendance_workbook(
    employee_reports: list[dict[str, Any]],
    *,
    company_name: str,
    month: int,
    year: int,
    generated_on: str,
) -> BytesIO:
    """Build one worksheet containing all selected employees' attendance.

    Each employee has a clearly marked section. Employee identity is also
    included in each attendance row so it remains identifiable while scrolling
    and can be filtered/sorted without losing employee context.
    """
    month_name = date(year, month, 1).strftime("%B")
    workbook = Workbook()
    ws = workbook.active
    ws.title = "Daily Attendance"
    ws.sheet_view.showGridLines = False

    daily_headers = [
        "S.No.", "Employee Name", "Employee ID", "Emp Code", "Department",
        "Date", "Day", "In Time", "Out Time", "Working Duration",
        "Break Duration", "Total Duration", "Break Details",
        "Permission Start Time", "Permission End Time", "Permission Duration",
        "Permission Count", "Attendance Type", "Attendance Source", "Status",
        "Holiday / Leave Details", "Remarks",
    ]
    widths = [
        8, 25, 17, 14, 20, 15, 13, 14, 14, 17, 16, 16, 34,
        20, 20, 19, 16, 17, 19, 20, 25, 32,
    ]
    last_col = len(daily_headers)
    thin = Side(style="thin", color=BORDER)
    row_num = 1

    # Single report title, shown only once at the top of the worksheet.
    ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=last_col)
    title = ws.cell(row_num, 1, f"HRMS - DAILY ATTENDANCE REPORT - {month_name} {year}")
    title.font = Font(size=16, bold=True, color=WHITE)
    title.fill = PatternFill("solid", fgColor=NAVY)
    title.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[row_num].height = 34
    row_num += 1

    ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=last_col)
    subtitle = ws.cell(row_num, 1, f"{company_name or 'HRMS'}  |  Generated On: {generated_on}")
    subtitle.font = Font(size=10, italic=True, color=NAVY)
    subtitle.fill = PatternFill("solid", fgColor=PALE)
    subtitle.alignment = Alignment(horizontal="right", vertical="center")
    ws.row_dimensions[row_num].height = 23
    row_num += 2

    first_daily_row = None
    last_daily_row = None

    for employee_index, report in enumerate(employee_reports, 1):
        info = report["info"]
        employee = report["employee"]
        rows = report["rows"]
        name = info.get("name") or employee.get("name") or "Employee"
        employee_id = info.get("employee_id") or employee.get("employee_id") or ""
        employee_code = info.get("employee_code") or employee.get("employee_code") or ""
        department = info.get("department_name") or employee.get("department") or "—"

        # Employee section heading and metadata. The same identity values are
        # repeated in the daily data rows below to keep each scrolled row clear.
        ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=last_col)
        section = ws.cell(row_num, 1, f"EMPLOYEE {employee_index:03d}  |  {name}  |  ID: {employee_id or '—'}  |  CODE: {employee_code or '—'}")
        section.font = Font(bold=True, color=WHITE, size=11)
        section.fill = PatternFill("solid", fgColor="305496")
        section.alignment = Alignment(vertical="center")
        ws.row_dimensions[row_num].height = 27
        row_num += 1

        # Horizontal employee metadata and schedule block.
        metadata = [
            ("Employee Name", name),
            ("Employee ID", employee_id or "—"),
            ("Emp Code", employee_code or "—"),
            ("Department", department),
            ("Reporting Month", f"{month_name} {year}"),
            ("Generated On", generated_on),
        ]
        for idx, (label, value) in enumerate(metadata):
            group = idx % 3
            target_row = row_num + idx // 3
            _merge_value_pair(ws, target_row, 1 + group * 7, label, value)
        ws.row_dimensions[row_num].height = 25
        ws.row_dimensions[row_num + 1].height = 25
        row_num += 2

        schedule_pairs = {
            (
                str(r.get("scheduled_work_start_time") or "").strip(),
                str(r.get("scheduled_work_end_time") or "").strip(),
            )
            for r in rows
            if r.get("scheduled_work_start_time") or r.get("scheduled_work_end_time")
        }
        schedule_pairs.discard(("", ""))
        if len(schedule_pairs) == 1:
            schedule_start, schedule_end = next(iter(schedule_pairs))
            schedule_in, schedule_out = _time_text(schedule_start), _time_text(schedule_end)
        elif len(schedule_pairs) > 1:
            schedule_in = schedule_out = "Varies by effective schedule"
        else:
            schedule_in = schedule_out = "Not configured"
        _merge_value_pair(ws, row_num, 1, "S. In Time", schedule_in)
        _merge_value_pair(ws, row_num, 8, "S. Out Time", schedule_out)
        ws.row_dimensions[row_num].height = 25
        row_num += 1

        header_row = row_num
        for col, header in enumerate(daily_headers, 1):
            cell = ws.cell(header_row, col, header)
            cell.font = Font(bold=True, color=WHITE, size=9)
            cell.fill = PatternFill("solid", fgColor=NAVY)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = Border(bottom=thin)
        ws.row_dimensions[header_row].height = 34
        row_num += 1

        employee_first_row = row_num
        for serial, item in enumerate(rows, 1):
            day = date.fromisoformat(str(item["attendance_date"])[:10])
            permissions = item.get("permissions") or []
            permission_starts = "\n".join(
                str(p.get("start_text") or p.get("start_time") or "—") for p in permissions
            )
            permission_ends = "\n".join(
                str(p.get("end_text") or p.get("end_time") or "—") for p in permissions
            )
            permission_minutes = int(item.get("permission_total_minutes") or 0)
            leave = item.get("leave") or {}
            leave_holiday = (
                leave.get("leave_type") or leave.get("type")
                or item.get("holiday_name") or item.get("leave_type_code") or ""
            )
            values = [
                serial, name, employee_id or "—", employee_code or "—", department,
                day, day.strftime("%A"),
                item.get("check_in_text") if item.get("check_in_text") not in (None, "—") else "",
                item.get("check_out_text") if item.get("check_out_text") not in (None, "—") else "",
                int(item.get("working_minutes") or 0) / 1440,
                int(item.get("break_minutes") or 0) / 1440,
                int(item.get("total_duration_minutes") or 0) / 1440,
                item.get("break_details") or "No qualifying breaks recorded",
                permission_starts or "—", permission_ends or "—",
                permission_minutes / 1440, len(permissions),
                item.get("attendance_type") or "—",
                item.get("attendance_source_label") or item.get("attendance_source") or "—",
                item.get("status") or "Not Marked",
                leave_holiday or "—", item.get("remarks") or "—",
            ]
            for col, value in enumerate(values, 1):
                cell = ws.cell(row_num, col, value)
                cell.alignment = Alignment(
                    vertical="center",
                    horizontal="center" if col not in (2, 3, 4, 5, 13, 14, 15, 21, 22) else "left",
                    wrap_text=col in (2, 5, 13, 14, 15, 21, 22),
                )
                cell.border = Border(bottom=thin)
                if serial % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor=PALE)
                if col == 6:
                    cell.number_format = "DD-MMM-YYYY"
                if col in (10, 11, 12, 16):
                    cell.number_format = "[h]:mm"
                if col == 20:
                    fill = _status_fill(value)
                    if fill:
                        cell.fill = PatternFill("solid", fgColor=fill)
            ws.row_dimensions[row_num].height = 30
            row_num += 1

        if rows:
            if first_daily_row is None:
                first_daily_row = employee_first_row
            last_daily_row = row_num - 1

        # Monthly summary for this employee, before the next employee section.
        summary_start = row_num + 1
        ws.merge_cells(start_row=summary_start, start_column=1, end_row=summary_start, end_column=last_col)
        summary_title = ws.cell(summary_start, 1, f"MONTHLY SUMMARY - {name}")
        summary_title.font = Font(bold=True, color=WHITE, size=10)
        summary_title.fill = PatternFill("solid", fgColor=NAVY)
        summary_title.alignment = Alignment(vertical="center")
        ws.row_dimensions[summary_start].height = 24

        metrics = _summary_metrics(rows)
        for index, (label, value) in enumerate(metrics):
            target_row = summary_start + 1 + index // 4
            group_col = 1 + (index % 4) * 5
            ws.merge_cells(start_row=target_row, start_column=group_col, end_row=target_row, end_column=group_col + 2)
            ws.merge_cells(start_row=target_row, start_column=group_col + 3, end_row=target_row, end_column=group_col + 4)
            label_cell = ws.cell(target_row, group_col, label)
            value_cell = ws.cell(target_row, group_col + 3, value)
            label_cell.font = Font(bold=True, color=NAVY, size=9)
            label_cell.fill = PatternFill("solid", fgColor=BLUE)
            value_cell.font = Font(bold=True, color="263445", size=9)
            value_cell.fill = PatternFill("solid", fgColor=PALE)
            label_cell.alignment = value_cell.alignment = Alignment(vertical="center", wrap_text=True)
            for col in range(group_col, group_col + 5):
                ws.cell(target_row, col).border = Border(bottom=thin)
            ws.row_dimensions[target_row].height = 24

        row_num = summary_start + 1 + (len(metrics) - 1) // 4 + 2

    if not employee_reports:
        ws.cell(row_num, 1, "No employees selected for this attendance report.")

    for col, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = width

    # Keep the report title and employee identity columns visible during
    # horizontal navigation; employee identity is also present on every row.
    ws.freeze_panes = "F4"
    if first_daily_row and last_daily_row:
        ws.auto_filter.ref = f"A{first_daily_row}:V{last_daily_row}"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = "1:2"
    ws.print_area = f"A1:V{max(1, ws.max_row)}"
    ws.sheet_properties.outlinePr.summaryBelow = True

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output
