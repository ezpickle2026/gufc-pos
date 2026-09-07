"""Report export helpers: build XLSX and PDF files in-memory and return them as
Flask responses. Kept separate from app.py so the transaction-logic file stays
focused on business rules."""
import io
import os
from datetime import datetime

from flask import send_file
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage

LOGO_PATH = os.path.join(os.path.dirname(__file__), "static", "images", "logo.png")
BLUE = colors.HexColor("#0b3d66")
GREEN = colors.HexColor("#1e9e5a")
LIGHT_GRAY = colors.HexColor("#f4f6f8")


def xlsx_response(headers, rows, filename, sheet_title="Report"):
    """rows: list of tuples/lists matching headers order."""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title[:31] or "Report"
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="0B3D66", end_color="0B3D66", fill_type="solid")
    for row in rows:
        ws.append(list(row))
    for col in ws.columns:
        width = max((len(str(c.value)) if c.value is not None else 0) for c in col) + 2
        ws.column_dimensions[col[0].column_letter].width = min(max(width, 10), 40)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf, as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _header_flowables(title, subtitle=None):
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleGUFC", parent=styles["Title"], textColor=BLUE, fontSize=16)
    sub_style = ParagraphStyle("SubGUFC", parent=styles["Normal"], textColor=colors.HexColor("#6b7785"), fontSize=9)
    flow = []
    if os.path.exists(LOGO_PATH):
        try:
            flow.append(RLImage(LOGO_PATH, width=0.7 * inch, height=0.7 * inch))
        except Exception:
            pass
    flow.append(Paragraph("GREAT UNLI FOOD CORP.", title_style))
    flow.append(Paragraph("Solane LPG Distributor", sub_style))
    flow.append(Paragraph(title, ParagraphStyle("H2", parent=styles["Heading2"], textColor=BLUE)))
    if subtitle:
        flow.append(Paragraph(subtitle, sub_style))
    flow.append(Paragraph(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}", sub_style))
    flow.append(Spacer(1, 14))
    return flow


def pdf_table_response(title, headers, rows, filename, subtitle=None, col_widths=None, landscape_mode=False):
    buf = io.BytesIO()
    page_size = landscape(letter) if landscape_mode else letter
    doc = SimpleDocTemplate(buf, pagesize=page_size, topMargin=36, bottomMargin=36, leftMargin=36, rightMargin=36)
    flow = _header_flowables(title, subtitle)

    data = [headers] + [[str(c) for c in row] for row in rows]
    table = Table(data, repeatRows=1, colWidths=col_widths)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GRAY]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e6ea")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    flow.append(table)
    doc.build(flow)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/pdf")


def soa_pdf_response(customer, entries, beginning_balance, ending_balance, filename):
    """Statement of Account: chronological invoice/payment ledger with running balance."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=36, bottomMargin=36, leftMargin=36, rightMargin=36)
    styles = getSampleStyleSheet()
    flow = _header_flowables(
        "Statement of Account",
        subtitle=f"{customer['name']} ({customer['account_no']}) — {customer.get('business_name') or ''}",
    )
    flow.append(Paragraph(f"Beginning Balance: PHP {beginning_balance:,.2f}", styles["Normal"]))
    flow.append(Spacer(1, 8))

    headers = ["Date", "Type", "Reference", "Charges", "Payments", "Balance"]
    data = [headers]
    for e in entries:
        data.append([
            e["date"], e["type"], e["ref"],
            f"{e['debit']:,.2f}" if e["debit"] else "",
            f"{e['credit']:,.2f}" if e["credit"] else "",
            f"{e['balance']:,.2f}",
        ])
    table = Table(data, repeatRows=1, colWidths=[70, 60, 100, 75, 75, 75])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GRAY]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e6ea")),
    ]))
    flow.append(table)
    flow.append(Spacer(1, 10))
    flow.append(Paragraph(
        f"<b>Ending Balance: PHP {ending_balance:,.2f}</b>",
        ParagraphStyle("End", parent=styles["Normal"], textColor=GREEN, fontSize=12),
    ))
    doc.build(flow)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/pdf")
