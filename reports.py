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


def commercial_billing_pdf(delivery, cylinders, filename):
    """One-record invoice-style PDF for a commercial 50kg WIWO or READING
    billing — as opposed to pdf_table_response, which is for many-row
    tabular reports."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=36, bottomMargin=36, leftMargin=36, rightMargin=36)
    styles = getSampleStyleSheet()
    flow = _header_flowables(
        "Commercial LPG Billing" + (" — Weight In / Weight Out" if delivery["billing_type"] == "WIWO" else " — Meter Reading"),
        subtitle=f"{delivery['delivery_no']} — {delivery['delivery_date']}",
    )

    info_rows = [
        ["Customer", f"{delivery['customer_name']} ({delivery['account_no']})"],
        ["Location", delivery["location_name"]],
        ["Product", delivery["product_name"]],
        ["Status", delivery["status"]],
    ]
    info_table = Table(info_rows, colWidths=[100, 380])
    info_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    flow.append(info_table)
    flow.append(Spacer(1, 14))

    if delivery["billing_type"] == "WIWO":
        headers = ["Cylinder", "Gross In", "Tare", "Net In", "Gross Out", "Net Out", "Consumed"]
        data = [headers] + [[
            c["cylinder_label"] or "—", c["gross_weight_in"], c["tare_weight"], c["net_weight_in"],
            c["gross_weight_out"] if c["gross_weight_out"] is not None else "—",
            c["net_weight_out"] if c["net_weight_out"] is not None else "—",
            c["consumption_weight"] if c["consumption_weight"] is not None else "—",
        ] for c in cylinders]
        table = Table(data, repeatRows=1, colWidths=[90, 65, 60, 60, 65, 60, 65])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BLUE), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GRAY]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e6ea")),
        ]))
        flow.append(table)
        total_consumption = round(sum(c["consumption_weight"] or 0 for c in cylinders), 2)
    else:
        reading_rows = [
            ["Meter / Tank", delivery["meter_label"] or "—"],
            ["Previous Reading", delivery["previous_reading"]],
            ["Current Reading", delivery["current_reading"]],
        ]
        reading_table = Table(reading_rows, colWidths=[150, 330])
        reading_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e6ea")),
        ]))
        flow.append(reading_table)
        total_consumption = round((delivery["current_reading"] or 0) - (delivery["previous_reading"] or 0), 2)

    flow.append(Spacer(1, 14))
    unit = "kg" if delivery["billing_type"] == "WIWO" else "unit(s)"
    summary_rows = [["Total Consumption", f"{total_consumption} {unit}"]]
    if delivery["price_per_kg"]:
        summary_rows.append(["Rate", f"PHP {delivery['price_per_kg']:,.2f} per {unit[:-1] if unit.endswith('s') else unit}"])
    if delivery["billed_amount"]:
        summary_rows.append(["Billed Amount", f"PHP {delivery['billed_amount']:,.2f}"])
    summary_table = Table(summary_rows, colWidths=[150, 200])
    summary_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, -1), (1, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (1, -1), (1, -1), GREEN),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    flow.append(summary_table)

    if delivery["notes"]:
        flow.append(Spacer(1, 14))
        flow.append(Paragraph(f"<b>Notes:</b> {delivery['notes']}", styles["Normal"]))

    doc.build(flow)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/pdf")


def _eod_title(data):
    sub = f"{data['staff_name']}"
    if data.get("location_name"):
        sub += f" — {data['location_name']}"
    return f"End of Day Report — {data['date']}", sub


def eod_pdf_response(data, filename):
    """Attendant cash-up: totals by payment method, sales summary, items sold,
    and the full list of payments received, with a signature block."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=36, bottomMargin=36, leftMargin=36, rightMargin=36)
    styles = getSampleStyleSheet()
    h3 = ParagraphStyle("H3GUFC", parent=styles["Heading3"], textColor=BLUE, spaceBefore=10)
    title, sub = _eod_title(data)
    flow = _header_flowables(title, subtitle=sub)

    def styled(table_data, widths, total_row=False, right_from=1):
        t = Table(table_data, repeatRows=1, colWidths=widths)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ALIGN", (right_from, 1), (-1, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GRAY]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e6ea")),
        ]
        if total_row:
            style += [("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                      ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e8f5ee"))]
        t.setStyle(TableStyle(style))
        return t

    m = lambda n: f"{(n or 0):,.2f}"

    flow.append(Paragraph("Collections by Payment Method", h3))
    rows = [["Method", "# Payments", "POS Sales", "AR Collections", "Total"]]
    for r in data["methods"]:
        rows.append([r["method"], str(r["count"]), m(r["sales"]), m(r["collections"]), m(r["total"])])
    t = data["totals"]
    rows.append(["TOTAL", str(t["count"]), m(t["sales"]), m(t["collections"]), m(t["total"])])
    flow.append(styled(rows, [140, 70, 100, 100, 100], total_row=True))

    s = data["sales"]
    flow.append(Paragraph("Sales Summary", h3))
    summary = [["", "Amount"],
               [f"Completed sales ({s['count']})", m(s["total"])],
               ["  Cash sales", m(s["cash_sales"])],
               ["  Credit / terms sales", m(s["credit_sales"])],
               ["  Charged to accounts (unpaid)", m(s["charged_to_accounts"])],
               ["Discounts given", m(s["discount"])],
               [f"Voided sales ({s['voided_count']})", m(s["voided_total"])]]
    flow.append(styled(summary, [300, 120]))

    if data["items"]:
        flow.append(Paragraph("Items Sold", h3))
        rows = [["Product", "Type", "Qty", "Amount"]]
        for i in data["items"]:
            rows.append([i["product_name"], i["sale_mode"], str(i["qty"]), m(i["amount"])])
        flow.append(styled(rows, [210, 80, 60, 100], right_from=2))

    flow.append(Paragraph("Payments Received", h3))
    rows = [["Time", "Sale / Invoice", "Customer", "Type", "Method", "Reference", "Amount"]]
    for p in data["payments"]:
        rows.append([p["ts"][11:16], p["invoice_no"] if p["payment_source"] == "AR_COLLECTION" and p["invoice_no"] else p["sale_no"],
                     (p["customer_name"] or "Walk-in")[:22],
                     "AR" if p["payment_source"] == "AR_COLLECTION" else "POS",
                     p["method"], (p["reference"] or "")[:18], m(p["amount"])])
    if len(rows) == 1:
        rows.append(["—", "No payments received", "", "", "", "", ""])
    flow.append(styled(rows, [40, 100, 110, 35, 70, 90, 70], right_from=6))

    flow.append(Spacer(1, 30))
    sig = Table([["______________________________", "______________________________"],
                 [f"Prepared by: {data['staff_name']}", "Received / verified by"]],
                colWidths=[260, 260])
    sig.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    flow.append(sig)

    doc.build(flow)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/pdf")


def eod_xlsx_response(data, filename):
    wb = Workbook()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="0B3D66", end_color="0B3D66", fill_type="solid")

    def header(ws, row):
        ws.append(row)
        for cell in ws[ws.max_row]:
            cell.font = header_font
            cell.fill = header_fill

    title, sub = _eod_title(data)
    ws = wb.active
    ws.title = "Summary"
    ws.append([title]); ws["A1"].font = Font(bold=True, size=14)
    ws.append([sub])
    ws.append([])
    header(ws, ["Method", "# Payments", "POS Sales", "AR Collections", "Total"])
    for r in data["methods"]:
        ws.append([r["method"], r["count"], r["sales"], r["collections"], r["total"]])
    t = data["totals"]
    ws.append(["TOTAL", t["count"], t["sales"], t["collections"], t["total"]])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    ws.append([])
    s = data["sales"]
    header(ws, ["Sales Summary", "Amount"])
    for label, val in [(f"Completed sales ({s['count']})", s["total"]), ("Cash sales", s["cash_sales"]),
                       ("Credit / terms sales", s["credit_sales"]),
                       ("Charged to accounts (unpaid)", s["charged_to_accounts"]),
                       ("Discounts given", s["discount"]), (f"Voided sales ({s['voided_count']})", s["voided_total"])]:
        ws.append([label, val])
    for col, width in zip("ABCDE", (32, 12, 14, 16, 14)):
        ws.column_dimensions[col].width = width

    ws2 = wb.create_sheet("Payments")
    header(ws2, ["Date/Time", "Sale #", "Invoice #", "Customer", "Type", "Method", "Reference", "Collected By", "Amount"])
    for p in data["payments"]:
        ws2.append([p["ts"], p["sale_no"], p["invoice_no"] or "", p["customer_name"] or "Walk-in",
                    "AR Collection" if p["payment_source"] == "AR_COLLECTION" else "POS Sale",
                    p["method"], p["reference"] or "", p["collected_by"] or "", p["amount"]])
    for col, width in zip("ABCDEFGHI", (19, 20, 20, 26, 14, 14, 18, 20, 12)):
        ws2.column_dimensions[col].width = width

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
