
from fastapi.responses import StreamingResponse
from fastapi import HTTPException
from app.db.mongodb import db


def _company_snapshot() -> dict:
    company = (
        db["companies"].find_one({"organization_singleton_key": "primary"})
        or db["companies"].find_one({})
        or {}
    )
    return {
        "name": company.get("legal_entity") or company.get("company") or "HRMS",
        "address": company.get("location") or company.get("branch") or "",
    }


def _amount_words_indian(amount: float) -> str:
    ones = [
        "Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven",
        "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen",
        "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen",
    ]
    tens = [
        "", "", "Twenty", "Thirty", "Forty", "Fifty",
        "Sixty", "Seventy", "Eighty", "Ninety",
    ]

    def under_thousand(number: int) -> str:
        parts = []
        if number >= 100:
            parts.extend([ones[number // 100], "Hundred"])
            number %= 100
        if number >= 20:
            parts.append(tens[number // 10])
            number %= 10
        if number:
            parts.append(ones[number])
        return " ".join(parts)

    number = max(0, int(round(float(amount) * 100)))
    rupees, paise = divmod(number, 100)

    if rupees == 0:
        rupee_words = "Zero"
    else:
        parts = []
        for divisor, label in (
            (10_000_000_000, "Kharab"),
            (1_000_000_000, "Arab"),
            (10_000_000, "Crore"),
            (100_000, "Lakh"),
            (1_000, "Thousand"),
        ):
            value, rupees = divmod(rupees, divisor)
            if value:
                parts.append(f"{under_thousand(value)} {label}")
        if rupees:
            parts.append(under_thousand(rupees))
        rupee_words = " ".join(parts)

    result = f"Indian Rupee {rupee_words}"
    if paise:
        result += f" and {under_thousand(paise)} Paise"
    return result + " Only"


def _payslip_pdf_response(row: dict, inline: bool = False):
    from io import BytesIO
    from pathlib import Path
    from datetime import datetime
    from xml.sax.saxutils import escape

    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_RIGHT, TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
            Image,
        )
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="PDF generation requires reportlab. Install backend requirements.",
        )

    # ---------------------------------------------------------
    # REUSABLE ROUNDED-BORDER TABLE
    # ---------------------------------------------------------
    class RoundedBorderTable(Table):
        """
        ReportLab table with a rounded outer border.

        The table contents and internal TableStyle rules are drawn
        normally. The rounded outline is drawn on top afterward.
        """

        def __init__(
            self,
            *args,
            corner_radius=8,
            border_color=None,
            border_width=0.6,
            **kwargs,
        ):
            super().__init__(*args, **kwargs)
            self.corner_radius = corner_radius
            self.rounded_border_color = border_color or colors.HexColor("#D5D5D5")
            self.rounded_border_width = border_width

        def draw(self):
            super().draw()

            self.canv.saveState()
            self.canv.setStrokeColor(self.rounded_border_color)
            self.canv.setLineWidth(self.rounded_border_width)

            width = self._width
            height = self._height
            radius = min(self.corner_radius, width / 2, height / 2)

            self.canv.roundRect(
                0,
                0,
                width,
                height,
                radius,
                stroke=1,
                fill=0,
            )
            self.canv.restoreState()

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=17 * mm,
        leftMargin=17 * mm,
        topMargin=15 * mm,
        bottomMargin=18 * mm,
        title="Employee Payslip",
    )

    page_width = A4[0] - doc.leftMargin - doc.rightMargin

    # ---------------------------------------------------------
    # COLORS
    # ---------------------------------------------------------
    dark = colors.HexColor("#292929")
    muted = colors.HexColor("#777777")
    border = colors.HexColor("#D5D5D5")
    green = colors.HexColor("#70B985")
    light_green = colors.HexColor("#EAF9EF")
    light_grey = colors.HexColor("#F7F7F8")
    white = colors.white

    # ---------------------------------------------------------
    # REGISTER UNICODE FONT FOR INDIAN RUPEE SYMBOL
    # ---------------------------------------------------------
    backend_dir = Path(__file__).resolve().parents[1]

    font_candidates = [
        (
            backend_dir / "assets" / "fonts" / "NotoSans-Regular.ttf",
            backend_dir / "assets" / "fonts" / "NotoSans-Bold.ttf",
        ),
        (
            Path(r"C:\Windows\Fonts\Nirmala.ttf"),
            Path(r"C:\Windows\Fonts\NirmalaB.ttf"),
        ),
        (
            Path(r"C:\Windows\Fonts\arial.ttf"),
            Path(r"C:\Windows\Fonts\arialbd.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
        ),
    ]

    regular_font = "Helvetica"
    bold_font = "Helvetica-Bold"
    rupee_supported = False

    for regular_path, bold_path in font_candidates:
        try:
            if not regular_path.is_file():
                continue

            pdfmetrics.registerFont(TTFont("PayslipUnicode", str(regular_path)))
            regular_font = "PayslipUnicode"
            bold_font = "Helvetica-Bold"

            if bold_path.is_file():
                pdfmetrics.registerFont(
                    TTFont("PayslipUnicodeBold", str(bold_path))
                )
                bold_font = "PayslipUnicodeBold"

            if pdfmetrics.stringWidth("\u20B9", regular_font, 12) > 0:
                rupee_supported = True

            if rupee_supported:
                break
        except Exception:
            continue

    currency_code = str(row.get("currency") or "INR").upper()

    def money(value):
        try:
            amount = float(value or 0)
        except (TypeError, ValueError):
            amount = 0.0

        if currency_code == "INR":
            symbol = "\u20B9 " if rupee_supported else "Rs. "
        else:
            symbol = currency_code + " "

        return f"{symbol}{amount:,.2f}"

    # ---------------------------------------------------------
    # STYLES
    # ---------------------------------------------------------
    styles = getSampleStyleSheet()

    normal = ParagraphStyle(
        "PayslipNormal",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=8.5,
        leading=12,
        textColor=dark,
    )

    bold = ParagraphStyle("PayslipBold", parent=normal, fontName=bold_font)

    label = ParagraphStyle(
        "PayslipLabel",
        parent=normal,
        fontSize=8.5,
        textColor=muted,
    )

    small = ParagraphStyle(
        "PayslipSmall",
        parent=normal,
        fontSize=7.5,
        leading=10,
        textColor=muted,
    )

    section = ParagraphStyle(
        "PayslipSection",
        parent=bold,
        fontSize=8.5,
        leading=11,
    )

    money_right = ParagraphStyle(
        "PayslipMoneyRight",
        parent=normal,
        alignment=TA_RIGHT,
    )

    money_bold = ParagraphStyle(
        "PayslipMoneyBold",
        parent=bold,
        alignment=TA_RIGHT,
    )

    money_large = ParagraphStyle(
        "PayslipMoneyLarge",
        parent=bold,
        fontSize=16,
        leading=21,
    )

    footer = ParagraphStyle(
        "PayslipFooter",
        parent=small,
        alignment=TA_CENTER,
    )

    def safe(value, default="—"):
        if value is None or str(value).strip() == "":
            value = default
        return escape(str(value))

    def p(value, style=normal):
        return Paragraph(safe(value), style)

    def first_value(*values, default=""):
        for value in values:
            if value is not None and str(value).strip():
                return value
        return default

    def display_date(value):
        if not value:
            return "—"

        if isinstance(value, datetime):
            return value.strftime("%d/%m/%Y")

        text = str(value).strip()

        for fmt in (
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%d/%m/%Y",
        ):
            try:
                candidate = text[:26] if "%f" in fmt else text
                return datetime.strptime(candidate, fmt).strftime("%d/%m/%Y")
            except ValueError:
                continue

        return text[:10]

    def display_period(value):
        if not value:
            return "—"

        text = str(value).strip()
        for fmt in ("%Y-%m", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).strftime("%B %Y")
            except ValueError:
                continue

        return text

    # ---------------------------------------------------------
    # PAYSLIP DATA
    # ---------------------------------------------------------
    company = row.get("company_snapshot") or _company_snapshot() or {}
    company_name = company.get("name") or company.get("company_name") or "THE CHENNAI TUITION LLM"
    company_address = company.get("address") or ""

    period_raw = str(row.get("period") or "")
    period_display = display_period(period_raw)

    employee_name = row.get("employee_name") or "—"
    employee_id = row.get("employee_id") or "—"
    designation = first_value(row.get("designation"), default="")
    department = first_value(row.get("department"), default="")

    pay_date = display_date(
        first_value(
            row.get("pay_date"),
            row.get("payment_date"),
            row.get("updated_at"),
            default="",
        )
    )

    paid_days = row.get("paid_days", 0)
    lop_days = row.get("lop_days", 0)

    salary_snapshot = row.get("salary_snapshot") or {}
    saved_profile = {}
    if not all((
        row.get("bank_name") or salary_snapshot.get("bank_name"),
        row.get("bank_account_number") or salary_snapshot.get("bank_account_number") or row.get("bank_account"),
        row.get("pan_card_number") or salary_snapshot.get("pan_card_number") or row.get("pan") or row.get("pan_number"),
    )):
        saved_profile = db["employee_salary_profiles"].find_one(
            {"employee_id": employee_id},
            {"bank_name": 1, "bank_account_number": 1, "pan_card_number": 1},
        ) or {}
    bank_name = first_value(row.get("bank_name"), salary_snapshot.get("bank_name"), saved_profile.get("bank_name"), default="")
    bank_account = first_value(
        row.get("bank_account_number"),
        salary_snapshot.get("bank_account_number"),
        row.get("bank_account"),
        saved_profile.get("bank_account_number"),
        default="",
    )

    pan = first_value(
        row.get("pan_card_number"),
        salary_snapshot.get("pan_card_number"),
        row.get("pan"),
        row.get("pan_number"),
        saved_profile.get("pan_card_number"),
        default="",
    )

    earnings = row.get("earnings") or []
    deductions = row.get("deductions") or []

    def total_items(items):
        total = 0.0
        for item in items:
            try:
                total += float(item.get("amount") or 0)
            except (TypeError, ValueError, AttributeError):
                continue
        return total

    gross = row.get("gross_earnings")
    if gross is None:
        gross = total_items(earnings)

    total_deductions = row.get("total_deductions")
    if total_deductions is None:
        total_deductions = total_items(deductions)

    net_salary = row.get("net_salary")
    if net_salary is None:
        net_salary = float(gross or 0) - float(total_deductions or 0)

    try:
        net_salary = float(net_salary or 0)
    except (TypeError, ValueError):
        net_salary = 0.0

    story = []

    # ---------------------------------------------------------
    # 1. COMPANY HEADER
    # ---------------------------------------------------------
     

    # COMPANY LOGO
    backend_dir = Path(__file__).resolve().parents[1]

    logo_cell = Paragraph(
        "<b>TCT</b>",
        ParagraphStyle(
            "LogoFallback",
            parent=normal,
            alignment=TA_CENTER,
            fontName=bold_font,
            fontSize=9,
        ),
    )

    logo_path = (
        company.get("logo_path")
        or "backend/assets/images/TCT_Company logo.jpg"
    )

    logo_file = Path(logo_path)

    # Resolve relative paths from the project root or backend folder.
    if not logo_file.is_absolute():
        project_root_file = backend_dir.parent / logo_file
        backend_relative_file = backend_dir / logo_file

        if project_root_file.is_file():
            logo_file = project_root_file
        elif backend_relative_file.is_file():
            logo_file = backend_relative_file
        else:
            # Use the known default location inside backend/assets.
            logo_file = (
                backend_dir / "assets" / "images" / "TCT_Company logo.jpg"
            )

    if logo_file.is_file():
        try:
            logo_cell = Image(
                str(logo_file),
                width=17 * mm,
                height=17 * mm,
                kind="proportional",
            )
        except (OSError, TypeError, ValueError):
            pass


    company_block = [
        Paragraph(
            safe(company_name),
            ParagraphStyle(
                "CompanyName",
                parent=bold,
                fontSize=12,
                leading=15,
            ),
        ),
        Paragraph(safe(company_address, ""), small),
    ]

    month_block = [
        Paragraph(
            "Payslip For the Month",
            ParagraphStyle(
                "MonthLabel",
                parent=small,
                alignment=TA_RIGHT,
            ),
        ),
        Paragraph(
            safe(period_display),
            ParagraphStyle(
                "MonthValue",
                parent=bold,
                fontSize=8,
                alignment=TA_RIGHT,
            ),
        ),
    ]

    header = Table(
        [[logo_cell, company_block, month_block]],
        colWidths=[
            21 * mm,
            page_width - 21 * mm - 48 * mm,
            48 * mm,
        ],
    )
    header.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ])
    )

    story.extend([
        header,
        Table(
            [[""]],
            colWidths=[page_width],
            rowHeights=[0.5],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), border),
            ]),
        ),
        Spacer(1, 5 * mm),
    ])

    # ---------------------------------------------------------
    # 2. EMPLOYEE SUMMARY
    # ---------------------------------------------------------
    summary_table = Table(
        [
            [p("Employee Name", label), p(":", label), p(employee_name,bold)],
            [p("Employee ID", label), p(":", label), p(employee_id,bold)],
            [p("Pay Period", label), p(":", label), p(period_display,bold)],
            [p("Pay Date", label), p(":", label), p(pay_date,bold)],
        ],
        colWidths=[31 * mm, 5 * mm, 30 * mm],
    )
    summary_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ])
    )

    summary_heading = Paragraph(
        "EMPLOYEE SUMMARY",
        ParagraphStyle(
            "SummaryHeading",
            parent=section,
            textColor=muted,
        ),
    )

    # ---------------------------------------------------------
    # 3. TOP-RIGHT NET PAY PANEL
    # ---------------------------------------------------------
    net_top = Table(
        [
            [Paragraph("Total Net Pay", small)],
            [Paragraph(money(net_salary), money_large)],
        ],
        colWidths=[59 * mm],
    )
    net_top.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), light_green),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ])
    )

    net_days = Table(
        [
            [p("Paid Days", label), p(":", label), p(paid_days,bold)],
            [p("LOP Days", label), p(":", label), p(lop_days,bold)],
        ],
        colWidths=[30 * mm, 5 * mm, 24 * mm],
        hAlign="LEFT",
    )
    net_days.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEABOVE", (0, 0), (-1, 0), 0.5, border),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ])
    )

    net_panel = RoundedBorderTable(
        [[net_top], [net_days]],
        colWidths=[59 * mm],
        corner_radius=8,
        border_color=border,
        border_width=0.6,
    )
    net_panel.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ])
    )

    summary_layout = Table(
        [[
            [summary_heading, Spacer(1, 2 * mm), summary_table],
            net_panel,
        ]],
        colWidths=[page_width - 63 * mm, 59 * mm],
    )
    summary_layout.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ])
    )

    story.extend([summary_layout, Spacer(1, 5 * mm)])

    
     
     
    # ---------------------------------------------------------
    # 4. EMPLOYEE DETAILS
    # ---------------------------------------------------------
    detail_rows = []

    if department or designation:
        detail_rows.append([
            p("Department", label) if department else "",
            p(":", label) if department else "",
            p(department, bold) if department else "",
            p("Designation", label) if designation else "",
            p(":", label) if designation else "",
            p(designation, bold) if designation else "",
        ])

    if bank_name or bank_account:
        detail_rows.append([
            p("Bank Name", label) if bank_name else "",
            p(":", label) if bank_name else "",
            p(bank_name, bold) if bank_name else "",
            p("Bank Account Number", label) if bank_account else "",
            p(":", label) if bank_account else "",
            p(bank_account, bold) if bank_account else "",
        ])

    if pan:
        detail_rows.append([
            p("PAN", label),
            p(":", label),
            p(pan, bold),
            "",
            "",
            "",
        ])

    if detail_rows:
        # Divide the available width into two balanced groups.
        # Each group contains: label, colon, value.
        left_label_width = 22 * mm
        colon_width = 3 * mm
        right_label_width = 25 * mm

        value_width = (
            page_width
            - left_label_width
            - colon_width
            - right_label_width
            - colon_width
        ) / 2

        # Keep the two groups aligned and use the full available width.
        details = Table(
            detail_rows,
            colWidths=[
                left_label_width,
                colon_width,
                value_width,
                right_label_width,
                colon_width,
                value_width,
            ],
            hAlign="LEFT",
        )

        details.setStyle(
            TableStyle([
                ("LINEABOVE", (0, 0), (-1, 0), 0.5, border),

                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),

                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),

                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ])
        )

        story.extend([
            details,
            Spacer(1, 5 * mm),
        ])


    # ---------------------------------------------------------
    # 5. EARNINGS AND DEDUCTIONS
    # ---------------------------------------------------------
    left_rows = [
        [
            Paragraph("EARNINGS", section),
            Paragraph("AMOUNT", money_bold),
        ]
    ]

    for item in earnings:
        left_rows.append([
            p(item.get("name") or "Earning"),
            Paragraph(money(item.get("amount", 0)), money_bold),
        ])

    right_rows = [
        [
            Paragraph("DEDUCTIONS", section),
            Paragraph("AMOUNT", money_bold),
        ]
    ]

    for item in deductions:
        right_rows.append([
            p(item.get("name") or "Deduction"),
            Paragraph(money(item.get("amount", 0)), money_bold),
        ])

    detail_count = max(len(left_rows), len(right_rows))

    while len(left_rows) < detail_count:
        left_rows.append(["", ""])

    while len(right_rows) < detail_count:
        right_rows.append(["", ""])

    left_rows.append([
        Paragraph("Gross Earnings", bold),
        Paragraph(money(gross), money_bold),
    ])

    right_rows.append([
        Paragraph("Total Deductions", bold),
        Paragraph(money(total_deductions), money_bold),
    ])

    combined_rows = [
        left_rows[i] + right_rows[i]
        for i in range(len(left_rows))
    ]

    half_width = page_width / 2

    earnings_table = RoundedBorderTable(
        combined_rows,
        colWidths=[
            half_width * 0.62,
            half_width * 0.38,
            half_width * 0.62,
            half_width * 0.38,
        ],
        repeatRows=1,
        corner_radius=8,
        border_color=border,
        border_width=0.6,
    )

    total_row = len(combined_rows) - 1

    earnings_table.setStyle(
        TableStyle([
            # The rounded outer border is drawn by RoundedBorderTable.
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, border),
            ("BACKGROUND", (0, 0), (-1, 0), white),
            ("BACKGROUND", (0, total_row), (-1, total_row), light_grey),
            ("LINEABOVE", (0, total_row), (-1, total_row), 0.5, border),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("ALIGN", (3, 0), (3, -1), "RIGHT"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ])
    )

    story.extend([earnings_table, Spacer(1, 7 * mm)])



    # ---------------------------------------------------------
    # 6. TOTAL NET PAYABLE
    # ---------------------------------------------------------
    net_payable = RoundedBorderTable(
        [
            [
                Paragraph("TOTAL NET PAYABLE", bold),
                Paragraph(money(net_salary), money_bold),
            ],
            [
                Paragraph("Gross Earnings - Total Deductions", small),
                "",
            ],
        ],
        colWidths=[page_width - 59 * mm, 59 * mm],
        rowHeights=[20, 18],
        corner_radius=8,
        border_color=border,
        border_width=0.6,
    )

    net_payable.setStyle(
        TableStyle([
            # Background colors
            ("BACKGROUND", (0, 0), (0, 1), white),
            ("BACKGROUND", (1, 0), (1, 1), light_green),

            # Description occupies the full width of the second row
            ("SPAN", (0, 1), (1, 1)),

            # Vertical alignment
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),

            # Horizontal padding
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),

            # Row-specific vertical padding
            ("TOPPADDING", (0, 0), (-1, 0), 7),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
            ("TOPPADDING", (0, 1), (-1, 1), 3),
            ("BOTTOMPADDING", (0, 1), (-1, 1), 5),
        ])
    )

    story.extend([
        net_payable,
        Spacer(1, 7 * mm),
    ])




    # ---------------------------------------------------------
    # 7. AMOUNT IN WORDS AND FOOTER
    # ---------------------------------------------------------
    amount_words = _amount_words_indian(net_salary)

    story.extend([
        Paragraph(
            "Amount in Words : <b>" + escape(str(amount_words)) + "</b>",
            ParagraphStyle(
                "AmountWords",
                #parent=normal,
                alignment=TA_RIGHT,
                fontSize=8,
                leading=12,
            ),
        ),
        Spacer(1, 5 * mm),
        Table(
            [[""]],
            colWidths=[page_width],
            rowHeights=[0.5],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), border),
            ]),
        ),
        Spacer(1, 8 * mm),
        Paragraph("— This is a system-generated document. —", footer),
    ])


    # ---------------------------------------------------------
    # 8. GENERATE PDF
    # ---------------------------------------------------------
    doc.build(story)
    buffer.seek(0)

    filename_period = (
        period_raw.replace("/", "-").replace("\\", "-")
        if period_raw
        else "payslip"
    )

    filename_employee = (
        str(row.get("employee_id") or "employee")
        .replace("/", "-")
        .replace("\\", "-")
        .replace('"', "")
    )

    filename = f"Payslip_{filename_employee}_{filename_period}.pdf"
    disposition = "inline" if inline else "attachment"

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"'
        },
    )
