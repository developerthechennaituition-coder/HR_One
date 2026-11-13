"""Excel workbook rendering for HRMS daily attendance exports.

This module contains presentation-only logic. Attendance calculation, permissions,
leave, employee lookup and authorization remain in attendance.py. All selected
employees are rendered as clearly separated sections in one worksheet.
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
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", raw)
    if match:
        hour, minute, second = int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59:
            return f"{hour:02d}:{minute:02d}:{second:02d}"
    for clock_format in ("%I:%M:%S %p", "%I:%M %p"):
        try:
            return datetime.strptime(raw.upper(), clock_format).strftime("%H:%M:%S")
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            from zoneinfo import ZoneInfo
            parsed = parsed.astimezone(ZoneInfo("Asia/Kolkata"))
        return parsed.strftime("%H:%M:%S")
    except ValueError:
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
    if "attendance issue" in text:
        return "FFE699"
    if "half-day present" in text:
        return "DDEBF7"
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



def _holiday_leave_details_text(item: dict[str, Any]) -> str:
    """Return only genuine holiday/leave information for the dedicated column.

    Attendance category (Office/WFH), attendance source, and stale leave codes on
    non-leave records must never leak into this column. The authoritative status
    and the resolved ``leave`` / ``holiday_name`` fields come from
    ``_complete_monthly_report``.
    """
    status = str(item.get("status") or "").strip().casefold()
    holiday_name = str(item.get("holiday_name") or "").strip()
    leave = item.get("leave") if isinstance(item.get("leave"), dict) else {}

    if "holiday" in status:
        return holiday_name or "Holiday"

    # Handles full-day, half-day and composite status labels such as
    # "1/2 Present + 1/2 Medical Leave".
    is_leave_status = "leave" in status or bool(leave)
    if not is_leave_status:
        return "—"

    details = str(leave.get("details") or "").strip()
    if details:
        return details

    leave_name = str(leave.get("leave_type_name") or leave.get("leave_type") or "").strip()
    leave_code = str(leave.get("leave_type_code") or "").strip()
    if not leave_name and "medical leave" in status:
        leave_name = "Medical Leave"
    elif not leave_name:
        leave_name = "Leave"
    label = f"{leave_name} ({leave_code})" if leave_code and leave_code.casefold() not in leave_name.casefold() else leave_name
    part = str(leave.get("day_part_label") or "").strip()
    if not part:
        raw_part = str(leave.get("day_part") or "").strip().casefold()
        part = "Half Day" if raw_part in {"first_half", "second_half", "half_day"} or "1/2" in status or "half" in status else ("Full Day" if raw_part == "full_day" else "")
    return f"{label} · {part}" if part and part.casefold() not in label.casefold() else label


def _attendance_type_text(value: Any) -> str:
    """Convert stored attendance type codes into readable report labels."""
    raw = str(value or "").strip()
    normalized = raw.casefold()
    if normalized in {"wfh", "work from home"}:
        return "Work From Home"
    if normalized in {"office", "office attendance"}:
        return "Office"
    return raw or "—"

def _summary_metrics(rows: list[dict[str, Any]]) -> list[list[tuple[str, Any]]]:
    """Four vertically stacked summary groups, matching the requested report layout."""
    working = sum(int(r.get("working_minutes") or 0) for r in rows)
    breaks = sum(int(r.get("break_minutes") or 0) for r in rows)
    elapsed = sum(int(r.get("total_duration_minutes") or 0) for r in rows)
    permission = sum(int(r.get("permission_total_minutes") or 0) for r in rows)
    permission_count = sum(len(r.get("permissions") or []) for r in rows)
    counts = {"Present Days": 0.0, "Absent Days": 0.0, "Leave Days": 0.0,
              "Medical Leave": 0.0, "Holidays": 0.0, "Weekly Off": 0.0, "Attendance Issue": 0.0}
    for row in rows:
        first = str(row.get("first_half_status") or "").casefold()
        second = str(row.get("second_half_status") or "").casefold()
        if first == "not finalized" or second == "not finalized":
            continue
        if first and second and first not in {"not marked", "not applicable"} and second not in {"not marked", "not applicable"}:
            for half in (first, second):
                if half == "present": counts["Present Days"] += .5
                elif half == "absent": counts["Absent Days"] += .5
                elif half == "attendance issue": counts["Attendance Issue"] += .5
                elif half == "leave": counts["Leave Days"] += .5
                elif half == "medical leave": counts["Medical Leave"] += .5
                elif half == "holiday": counts["Holidays"] += .5
                elif half in {"weekly off", "weekly-off", "week off"}: counts["Weekly Off"] += .5
        else:
            status = str(row.get("status") or "").casefold()
            if "1/2 present" in status or "half present" in status: counts["Present Days"] += .5
            elif status == "present": counts["Present Days"] += 1
            if "1/2 absent" in status or "half absent" in status: counts["Absent Days"] += .5
            elif status == "absent": counts["Absent Days"] += 1
            if status == "attendance issue": counts["Attendance Issue"] += 1
            if "1/2 medical leave" in status or "half medical leave" in status: counts["Medical Leave"] += .5
            elif "medical leave" in status: counts["Medical Leave"] += 1
            if ("1/2 leave" in status or "half leave" in status) and "medical leave" not in status: counts["Leave Days"] += .5
            elif status == "leave": counts["Leave Days"] += 1
            if status == "holiday": counts["Holidays"] += 1
            if status in {"weekly off", "weekly-off", "week off"}: counts["Weekly Off"] += 1
    half_count = sum(1 for row in rows if str(row.get("half_day_present") or "No").startswith("Yes"))
    first_count = sum(1 for row in rows if str(row.get("half_day_present") or "").endswith("First Half"))
    second_count = sum(1 for row in rows if str(row.get("half_day_present") or "").endswith("Second Half"))
    return [
        [("Net Working Time", _duration_text(working)), ("Break Time", _duration_text(breaks)), ("Total Duration", _duration_text(elapsed))],
        [("Present Days", counts["Present Days"]), ("Absent Days", counts["Absent Days"]), ("Leave Days", counts["Leave Days"]), ("Holidays", counts["Holidays"])],
        [("Weekly Off", counts["Weekly Off"]), ("Medical Leave", counts["Medical Leave"]), ("Permission Count", permission_count), ("Permission Duration", _duration_text(permission))],
        [("Half-Day Present", half_count), ("First Half Present", first_count), ("Second Half Present", second_count), ("Issue Count", counts["Attendance Issue"])],
    ]

def build_multi_employee_attendance_workbook(
    employee_reports: list[dict[str, Any]],
    *,
    company_name: str,
    month: int,
    year: int,
    generated_on: str,
) -> BytesIO:
    """Build one worksheet containing employee-wise attendance sections."""
    month_name = date(year, month, 1).strftime("%B")
    workbook = Workbook()
    ws = workbook.active
    ws.title = "Attendance Report"
    ws.sheet_view.showGridLines = False

    daily_headers = [
        "S.No.", "Date", "Day", "In Time", "Out Time", "Total Duration",
        "Net Working Time", "Break Duration", "Working Intervals", "Break Details", "Permission Time",
        "Attendance Type", "Attendance Source", "Status", "Half-Day Present",
        "First Half Status", "Second Half Status",
        "Early Going By", "Late Going By", "Holiday / Leave Details", "Remarks",
    ]
    widths = [8, 15, 13, 14, 14, 16, 18, 16, 34, 34, 22, 18, 22, 19, 22, 18, 18, 17, 17, 27, 34]
    last_col = len(daily_headers)
    thin = Side(style="thin", color=BORDER)

    def style_pair(row: int, group: int, label: str, value: Any) -> None:
        start_col = 1 + group * 4
        label_cell = ws.cell(row, start_col, label)
        label_cell.font = Font(bold=True, color=NAVY, size=9)
        label_cell.fill = PatternFill("solid", fgColor=BLUE)
        label_cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.merge_cells(start_row=row, start_column=start_col + 1,
                       end_row=row, end_column=start_col + 3)
        value_cell = ws.cell(row, start_col + 1, value if value not in (None, "") else "—")
        value_cell.font = Font(color="263445", size=9, bold=True)
        value_cell.fill = PatternFill("solid", fgColor=PALE)
        value_cell.alignment = Alignment(vertical="center", wrap_text=True)
        for col in range(start_col, start_col + 4):
            ws.cell(row, col).border = Border(bottom=thin)

    # Only the common report title and reporting metadata are frozen globally.
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    title = ws.cell(1, 1, f"{company_name or 'HRMS'} - DAILY ATTENDANCE REPORT")
    title.font = Font(name="Aptos Display", size=16, bold=True, color=WHITE)
    title.fill = PatternFill("solid", fgColor=NAVY)
    title.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 34

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=last_col)
    subtitle = ws.cell(2, 1, f"Reporting Month: {month_name} {year}  |  Generated On: {generated_on}")
    subtitle.font = Font(size=10, italic=True, color=NAVY)
    subtitle.fill = PatternFill("solid", fgColor=PALE)
    subtitle.alignment = Alignment(horizontal="right", vertical="center")
    ws.row_dimensions[2].height = 23
    ws.freeze_panes = "A3"

    current_row = 4
    for employee_index, report in enumerate(employee_reports, 1):
        info = report.get("info") or {}
        employee = report.get("employee") or {}
        rows = report.get("rows") or []
        name = info.get("name") or employee.get("name") or "Employee"
        employee_id = info.get("employee_id") or employee.get("employee_id") or ""
        employee_code = info.get("employee_code") or employee.get("employee_code") or ""
        department = info.get("department_name") or employee.get("department") or "—"

        # Employee section divider makes each employee's block easy to identify.
        ws.merge_cells(start_row=current_row, start_column=1,
                       end_row=current_row, end_column=last_col)
        section = ws.cell(current_row, 1, f"EMPLOYEE {employee_index}: {name}")
        section.font = Font(bold=True, color=WHITE, size=11)
        section.fill = PatternFill("solid", fgColor="305496")
        section.alignment = Alignment(vertical="center")
        ws.row_dimensions[current_row].height = 25
        current_row += 1

        ws.merge_cells(start_row=current_row, start_column=1,
                       end_row=current_row, end_column=last_col)
        details_heading = ws.cell(current_row, 1, "EMPLOYEE DETAILS")
        details_heading.font = Font(bold=True, color=NAVY, size=10)
        details_heading.fill = PatternFill("solid", fgColor=BLUE)
        details_heading.alignment = Alignment(vertical="center")
        ws.row_dimensions[current_row].height = 21
        current_row += 1

        schedule_pairs = {
            (str(r.get("scheduled_work_start_time") or "").strip(),
             str(r.get("scheduled_work_end_time") or "").strip())
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

        working_details = report.get("working_details") or {}
        schedule_work_types = {str(r.get("working_type") or "").strip() for r in rows if r.get("working_type")}
        if len(schedule_work_types) > 1:
            working_type = "Varies by effective schedule"
        elif len(schedule_work_types) == 1:
            working_type = next(iter(schedule_work_types))
        else:
            working_type = working_details.get("working_type") or "—"
        schedule_durations = {int(r.get("scheduled_work_duration") or 0) for r in rows if int(r.get("scheduled_work_duration") or 0) > 0}
        half_day_durations = {int(r.get("half_day_working_duration") or 0) for r in rows if int(r.get("half_day_working_duration") or 0) > 0}
        office_duration = _duration_text(next(iter(schedule_durations))) if len(schedule_durations) == 1 else ("Varies by effective schedule" if len(schedule_durations) > 1 else _duration_text(working_details.get("scheduled_work_duration") or 0))
        half_day_duration = _duration_text(next(iter(half_day_durations))) if len(half_day_durations) == 1 else ("Varies by effective schedule" if len(half_day_durations) > 1 else _duration_text(working_details.get("half_day_working_duration") or 0))
        details = [
            ("Employee Name", name), ("Employee ID", employee_id or "—"),
            ("Emp Code", employee_code or "—"), ("Department", department),
            ("Reporting Month", f"{month_name} {year}"), ("Generated On", generated_on),
            ("Working Type", working_type), ("Scheduled In Time", schedule_in),
            ("Scheduled Out Time", schedule_out), ("Total Scheduled Working Duration", office_duration),
            ("Half-Day Working Duration", half_day_duration),
            ("Half Day Time", _duration_text(next(iter(half_day_durations))) if len(half_day_durations) == 1 else half_day_duration),
        ]
        for idx, (label, value) in enumerate(details):
            style_pair(current_row + idx // 4, idx % 4, label, value)
        for offset in range(3):
            ws.row_dimensions[current_row + offset].height = 25
        current_row += 3

        # Each employee block has its own column heading row; employee identity is
        # deliberately not repeated in any attendance data row.
        header_row = current_row
        for col, header in enumerate(daily_headers, 1):
            cell = ws.cell(header_row, col, header)
            cell.font = Font(bold=True, color=WHITE, size=9)
            cell.fill = PatternFill("solid", fgColor=NAVY)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = Border(bottom=thin)
        ws.row_dimensions[header_row].height = 34
        current_row += 1
        first_data_row = current_row

        serial = 0
        for item in rows:
            day_value = str(item.get("attendance_date") or "")[:10]
            try:
                day = date.fromisoformat(day_value)
            except (TypeError, ValueError):
                continue
            serial += 1
            permissions = item.get("permissions") or []
            permission_minutes = int(item.get("permission_total_minutes") or 0)
            permission_text = item.get("permission_total_text") or (_duration_text(permission_minutes) if permission_minutes else "—")
            if len(permissions) > 0:
                permission_text = f"{permission_text}\nPermission Count: {len(permissions)}"
            in_time = item.get("check_in_text") or item.get("first_check_in") or ""
            out_time = item.get("check_out_text") or item.get("last_check_out") or ""
            interval_lines = []
            for interval_index, interval in enumerate(item.get("working_intervals") or [], 1):
                begin = _time_text(interval.get("from_time"))
                finish = _time_text(interval.get("to_time"))
                if begin != "—" or finish != "—":
                    duration = interval.get("duration") or _duration_text(interval.get("duration_minutes") or 0)
                    interval_lines.append(f"WI{interval_index}: {begin} - {finish} ({duration})")
            break_lines = []
            for break_index, interval in enumerate(item.get("break_intervals") or [], 1):
                begin = _time_text(interval.get("from_time"))
                finish = _time_text(interval.get("to_time"))
                duration = interval.get("duration") or _duration_text(interval.get("duration_minutes") or 0)
                if begin != "—" or finish != "—":
                    break_lines.append(f"B{break_index}: {begin} - {finish} ({duration})")
            if not break_lines:
                break_lines = []
                for index, line in enumerate(
                    (line.strip() for line in str(item.get("break_details") or "").splitlines()
                     if "total break duration:" not in line.casefold()), 1
                ):
                    # Strip legacy descriptive labels such as "Tea Break:" or
                    # "Break 1:", but never mistake the colon in a clock time
                    # (11:30 AM) for a label separator.
                    if re.match(r"^(?:break\\s*\d+|.*\\bbreak)\\s*:", line, re.IGNORECASE):
                        line = line.split(":", 1)[1].strip()
                    break_lines.append(f"B{index}: {line}")
            date_text = day.strftime("%d-%b-%Y") + (" ⚠" if item.get("has_attendance_issue") else "")
            values = [
                serial, date_text, item.get("day") or day.strftime("%A"),
                _time_text(in_time) if in_time else "—",
                _time_text(out_time) if out_time else "—",
                _duration_text(int(item.get("total_duration_minutes") or 0)),
                _duration_text(int(item.get("working_minutes") or 0)),
                _duration_text(int(item.get("break_minutes") or 0)),
                "\n".join(interval_lines) or "—",
                "\n".join(break_lines) or "No break intervals recorded",
                permission_text,
                _attendance_type_text(item.get("attendance_type")),
                item.get("attendance_source_label") or item.get("attendance_source") or "—",
                item.get("status") or "Not Marked",
                item.get("half_day_present") or "No",
                item.get("first_half_status") or item.get("status") or "Not Marked",
                item.get("second_half_status") or item.get("status") or "Not Marked",
                item.get("early_going_by") or "—",
                item.get("late_going_by") or "—",
                _holiday_leave_details_text(item),
                item.get("remarks") or "—",
            ]
            for col, value in enumerate(values, 1):
                cell = ws.cell(current_row, col, value)
                cell.alignment = Alignment(
                    vertical="top" if col in (9, 10) else "center",
                    horizontal="left" if col in (9, 10, 20, 21) else "center",
                    wrap_text=col in (9, 10, 11, 20, 21),
                )
                cell.border = Border(bottom=thin)
                if serial % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor=PALE)
                if col == 2:
                    cell.number_format = "DD-MMM-YYYY"
                if col in (14, 15, 16, 17):
                    fill = _status_fill(value)
                    if fill:
                        cell.fill = PatternFill("solid", fgColor=fill)
            multiline_counts = [str(values[index] or "").count("\n") + 1 for index in (8, 9, 16, 19, 20)]
            ws.row_dimensions[current_row].height = max(30, min(180, 22 * max(multiline_counts or [1])))
            current_row += 1

        last_data_row = current_row - 1

        # Unresolved Attendance Issue dates (check-in without check-out) for this employee.
        issue_rows = [
            item for item in rows
            if str(item.get("status") or "").strip().casefold() == "attendance issue"
        ]
        current_row += 1
        ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=last_col)
        issue_title = ws.cell(
            current_row, 1,
            f"ATTENDANCE ISSUE DETAILS - {name}  (Unresolved issue dates: {len(issue_rows)})",
        )
        issue_title.font = Font(bold=True, color="664D03", size=10)
        issue_title.fill = PatternFill("solid", fgColor="FFE699")
        issue_title.alignment = Alignment(vertical="center")
        ws.row_dimensions[current_row].height = 24
        current_row += 1
        if not issue_rows:
            ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=last_col)
            ws.cell(current_row, 1, "No unresolved attendance issues for this month.").font = Font(italic=True, size=9)
            current_row += 1
        else:
            issue_headers = [(1, 1, "S.No."), (2, 2, "Date"), (3, 3, "Day"), (4, 4, "In Time"),
                             (5, 5, "Out Time"), (6, 9, "Issue"), (10, 13, "Attendance Request / Resolution")]
            for start_col, end_col, header in issue_headers:
                if end_col > start_col:
                    ws.merge_cells(start_row=current_row, start_column=start_col, end_row=current_row, end_column=end_col)
                head = ws.cell(current_row, start_col, header)
                head.font = Font(bold=True, color="664D03", size=9)
                head.fill = PatternFill("solid", fgColor="FFF2CC")
                head.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            current_row += 1
            for issue_number, item in enumerate(issue_rows, 1):
                in_time = item.get("check_in_text") or item.get("first_check_in") or item.get("check_in_time") or ""
                request_status = item.get("request_status")
                resolution = (
                    f"Attendance Request {request_status} - awaiting HR Admin approval with the missing check-out time"
                    if request_status in {"Pending", "Under Review"}
                    else "No attendance request submitted - HR Admin must add the check-out through an approved Attendance Request"
                )
                cells = [
                    (1, 1, issue_number), (2, 2, str(item.get("attendance_date"))[:10]), (3, 3, item.get("day") or ""),
                    (4, 4, _time_text(in_time) if in_time else "—"), (5, 5, "Missing"),
                    (6, 9, "Check-in recorded but no check-out; not counted as Present"),
                    (10, 13, resolution),
                ]
                for start_col, end_col, value in cells:
                    if end_col > start_col:
                        ws.merge_cells(start_row=current_row, start_column=start_col, end_row=current_row, end_column=end_col)
                    cell = ws.cell(current_row, start_col, value)
                    cell.font = Font(size=9)
                    cell.alignment = Alignment(vertical="center", horizontal="left" if end_col > start_col else "center", wrap_text=True)
                    for col in range(start_col, end_col + 1):
                        ws.cell(current_row, col).border = Border(bottom=thin)
                ws.row_dimensions[current_row].height = 30
                current_row += 1

        # Keep the existing monthly summary for each employee, in the same sheet.
        current_row += 1
        summary_start = current_row
        ws.merge_cells(start_row=summary_start, start_column=1,
                       end_row=summary_start, end_column=last_col)
        summary_title = ws.cell(summary_start, 1, f"MONTHLY ATTENDANCE SUMMARY - {name}")
        summary_title.font = Font(bold=True, color=WHITE, size=10)
        summary_title.fill = PatternFill("solid", fgColor=NAVY)
        summary_title.alignment = Alignment(vertical="center")
        ws.row_dimensions[summary_start].height = 24

        summary_groups = _summary_metrics(rows)
        summary_rows = max((len(group) for group in summary_groups), default=0)
        for group_index, group in enumerate(summary_groups):
            group_col = 1 + group_index * 4
            for row_index, (label, value) in enumerate(group):
                target_row = summary_start + 1 + row_index
                ws.merge_cells(start_row=target_row, start_column=group_col,
                               end_row=target_row, end_column=group_col + 1)
                ws.merge_cells(start_row=target_row, start_column=group_col + 2,
                               end_row=target_row, end_column=group_col + 3)
                label_cell = ws.cell(target_row, group_col, label)
                value_cell = ws.cell(target_row, group_col + 2, value)
                label_cell.font = Font(bold=True, color=NAVY, size=9)
                label_cell.fill = PatternFill("solid", fgColor=BLUE)
                value_cell.font = Font(bold=True, color="263445", size=9)
                value_cell.fill = PatternFill("solid", fgColor=PALE)
                label_cell.alignment = value_cell.alignment = Alignment(vertical="center", wrap_text=True)
                for col in range(group_col, group_col + 4):
                    ws.cell(target_row, col).border = Border(bottom=thin)
                ws.row_dimensions[target_row].height = 24
        current_row = summary_start + 1 + summary_rows + 1

    if not employee_reports:
        ws.merge_cells(start_row=4, start_column=1, end_row=4, end_column=last_col)
        ws["A4"] = "No employees selected for this attendance report."
        ws["A4"].alignment = Alignment(horizontal="center", vertical="center")

    for col, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = width

    # One common report heading is frozen; employee-specific section details and
    # headings repeat in each block and on printed pages as applicable.
    ws.print_title_rows = "1:2"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_margins.left = 0.25
    ws.page_margins.right = 0.25
    ws.page_margins.top = 0.5
    ws.page_margins.bottom = 0.5
    ws.print_area = f"A1:{get_column_letter(last_col)}{max(1, ws.max_row)}"
    ws.sheet_properties.outlinePr.summaryBelow = True

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output
