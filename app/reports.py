"""Freeze a CSV once; derive both presentation formats from those exact rows."""

import csv
import hashlib
import io
from decimal import ROUND_HALF_UP, Decimal
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.accounting import instant, rate_at

BASE_COLUMNS = ["Session", "Period", "Vehicle", "License plate", "Start", "End", "Energy kWh"]
EMPLOYER_COLUMNS = ["Rate EUR/kWh", "Amount EUR"]
ACTUAL_COLUMNS = ["Grid kWh", "Solar kWh", "Grid cost EUR", "Solar cost EUR", "Amount EUR"]
BLOCKING_FLAGS = {
    "partial_start",
    "missing_meter_baseline",
    "meter_gap",
    "meter_reset",
    "state_gap",
}


def fixed(n, places="0.001"):
    return str(Decimal(str(n)).quantize(Decimal(places), rounding=ROUND_HALF_UP))


def safe_text(value):
    text = str(value)
    return (
        "'" + text
        if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n"))
        else text
    )


def freeze_report(vehicle, sessions, month, timezone, tariffs):
    zone = ZoneInfo(timezone)
    rows = []
    for session in sessions:
        if instant(session.start).astimezone(zone).strftime("%Y-%m") != month:
            continue
        p = session.payload
        if Decimal(p["energy_kwh"]) <= 0 and not BLOCKING_FLAGS.intersection(p["flags"]):
            continue
        if not session.end:
            raise ValueError("A session in this period is still in progress.")
        if session.assignment not in {"confirmed", "default"}:
            raise ValueError("Confirm suggested vehicle assignments before exporting.")
        if BLOCKING_FLAGS.intersection(p["flags"]):
            raise ValueError("A session has incomplete meter data. Resolve it before exporting.")
        row = {
            "Session": session.id,
            "Period": month,
            "Vehicle": safe_text(vehicle.name),
            "License plate": safe_text(vehicle.plate),
            "Start": instant(session.start).astimezone(zone).isoformat(timespec="seconds"),
            "End": instant(session.end).astimezone(zone).isoformat(timespec="seconds"),
            "Energy kWh": p["energy_kwh"],
        }
        if vehicle.policy == "employer":
            tariff = rate_at(tariffs, instant(session.start))
            if tariff is None:
                raise ValueError("Add a reimbursement rate covering this session.")
            rate = tariff.reimbursement_rate
            row.update(
                {
                    "Rate EUR/kWh": rate,
                    "Amount EUR": fixed(Decimal(p["energy_kwh"]) * Decimal(rate), "0.01"),
                }
            )
        else:
            if Decimal(p["unknown_kwh"]) > 0 or "missing_tariff" in p["flags"]:
                raise ValueError("Actual-cost export needs complete grid allocation and tariffs.")
            row.update(
                {
                    "Grid kWh": fixed(p["grid_kwh"], "0.000001"),
                    "Solar kWh": fixed(p["solar_kwh"], "0.000001"),
                    "Grid cost EUR": fixed(p["grid_cost"], "0.000001"),
                    "Solar cost EUR": fixed(p["solar_cost"], "0.000001"),
                    "Amount EUR": fixed(Decimal(p["grid_cost"]) + Decimal(p["solar_cost"]), "0.01"),
                }
            )
        rows.append(row)
    if not rows:
        raise ValueError("No completed, non-zero sessions for this vehicle and month.")
    columns = BASE_COLUMNS + (EMPLOYER_COLUMNS if vehicle.policy == "employer" else ACTUAL_COLUMNS)
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    text = out.getvalue()
    snapshot = {
        "timezone": timezone,
        "vehicle": vehicle.name,
        "plate": vehicle.plate,
        "policy": vehicle.policy,
        "month": month,
        "session_count": len(rows),
        "energy_kwh": fixed(sum(Decimal(r["Energy kWh"]) for r in rows)),
        "amount_eur": fixed(sum(Decimal(r["Amount EUR"]) for r in rows), "0.01"),
        "period_rule": "Whole sessions belong to the month in which charging starts.",
        "rounding": "Round each session to cents, then sum.",
    }
    return snapshot, text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def table_from_csv(report):
    return list(csv.reader(io.StringIO(report.csv_text)))


def xlsx_bytes(report):
    wb = Workbook()
    ws = wb.active
    ws.title = "Charging sessions"
    for row in table_from_csv(report):
        ws.append(row)
    for row in ws.iter_rows():
        for cell in row:
            cell.data_type = "s"  # Treat all user-supplied cells as text, never formulas.
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="6B4F3B")
    for column in ws.columns:
        ws.column_dimensions[column[0].column_letter].width = min(
            38, max(14, max(len(str(c.value or "")) for c in column) + 2)
        )
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for row in ws.iter_rows(min_row=2, min_col=7):
        for cell in row:
            cell.value = float(Decimal(cell.value))
            cell.number_format = "0.000" if "kWh" in ws.cell(1, cell.column).value else "0.00"
    summary = wb.create_sheet("Report")
    for key, value in {
        **report.snapshot,
        "report_id": report.id,
        "csv_sha256": report.sha256,
    }.items():
        summary.append([key, str(value)])
        summary.cell(summary.max_row, 2).data_type = "s"
    summary.column_dimensions["A"].width = 22
    summary.column_dimensions["B"].width = 80
    stream = io.BytesIO()
    wb.save(stream)
    return stream.getvalue()


def pdf_bytes(report):
    stream = io.BytesIO()
    doc = SimpleDocTemplate(
        stream, pagesize=landscape(A4), rightMargin=28, leftMargin=28, topMargin=28, bottomMargin=38
    )
    styles = getSampleStyleSheet()
    title = "Charging reimbursement" if report.policy == "employer" else "Charging costs"
    story = [
        Paragraph(title, styles["Title"]),
        Paragraph(
            escape(f"{report.snapshot['vehicle']} · {report.snapshot['plate']} · {report.month}"),
            styles["Heading2"],
        ),
    ]
    source = list(csv.DictReader(io.StringIO(report.csv_text)))
    headers = ["#", "Start", "End", "Energy kWh"]
    headers += (
        ["EUR/kWh", "Amount EUR"]
        if report.policy == "employer"
        else ["Grid kWh", "Solar kWh", "Amount EUR"]
    )
    rows = [headers]
    for i, r in enumerate(source, 1):
        values = [
            str(i),
            r["Start"].replace("T", " ")[:19],
            r["End"].replace("T", " ")[:19],
            fixed(r["Energy kWh"]),
        ]
        values += (
            [r["Rate EUR/kWh"], r["Amount EUR"]]
            if report.policy == "employer"
            else [fixed(r["Grid kWh"]), fixed(r["Solar kWh"]), r["Amount EUR"]]
        )
        rows.append(values)
    table = Table(rows, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6b4f3b")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f8f3ee"), colors.white]),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#d9c5b3")),
            ]
        )
    )
    story.extend(
        [
            Spacer(1, 14),
            table,
            Spacer(1, 18),
            Paragraph(
                f"Total: {report.snapshot['energy_kwh']} kWh · EUR {report.snapshot['amount_eur']}",
                styles["Heading2"],
            ),
            Paragraph(
                escape(
                    f"Times: {report.snapshot['timezone']}. {report.snapshot['period_rule']} {report.snapshot['rounding']}"
                ),
                styles["Normal"],
            ),
        ]
    )
    if report.policy == "actual":
        story.append(
            Paragraph(
                "Solar allocation follows the house-first rule using charger power and grid import. Local supply is labelled solar; battery provenance is not tracked.",
                styles["Normal"],
            )
        )

    def footer(canvas, document):
        canvas.setFont("Helvetica", 7)
        canvas.drawString(28, 20, f"WattTally · {report.id} · CSV SHA256 {report.sha256}")
        canvas.drawRightString(812, 20, str(document.page))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()
