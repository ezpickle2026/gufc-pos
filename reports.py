"""Report export helpers: build XLSX and PDF files in-memory and return them as
Flask responses. Kept separate from app.py so the transaction-logic file stays
focused on business rules."""
import io
import os
from xml.sax.saxutils import escape as _xml_escape
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


def soa_pdf_response(customer, entries, beginning_balance, ending_balance, filename,
                     previous_arrears=None, balance_forward=None):
    """Statement of Account: chronological invoice/payment ledger with running balance."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=36, bottomMargin=36, leftMargin=36, rightMargin=36)
    styles = getSampleStyleSheet()
    flow = _header_flowables(
        "Statement of Account",
        subtitle=f"{customer['name']} ({customer['account_no']}) — {customer.get('business_name') or ''}",
    )
    flow.append(Paragraph(f"<b>Beginning Balance: PHP {beginning_balance:,.2f}</b>", styles["Normal"]))
    if previous_arrears is not None and (previous_arrears or balance_forward):
        detail = f"Previous arrears: PHP {previous_arrears:,.2f}"
        if balance_forward:
            detail += f" &nbsp;&nbsp; Balance forward (before this period): PHP {balance_forward:,.2f}"
        flow.append(Paragraph(detail, ParagraphStyle("BegDetail", parent=styles["Normal"], fontSize=8,
                                                     textColor=colors.HexColor("#555555"))))
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
    type_title = {"WIWO": " — Weight In / Weight Out", "DR": " — DR Billing"}.get(delivery["billing_type"], " — Meter Reading")
    flow = _header_flowables(
        "Commercial LPG Billing" + type_title,
        subtitle=f"{delivery['delivery_no']} — {delivery['delivery_date']}",
    )

    info_rows = [
        ["Customer", f"{delivery['customer_name']} ({delivery['account_no']})"],
        ["Location", delivery["location_name"]],
        ["Product", delivery["product_name"]],
        ["Status", delivery["status"]],
    ]
    if delivery["billing_type"] == "DR":
        info_rows.insert(0, ["DR No.", (delivery.get("dr_ref") or "—") + (f"  (system {delivery['sale_dr_no']})" if delivery.get("sale_dr_no") else "")])
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
    elif delivery["billing_type"] == "DR":
        delivered_kg = round((delivery["delivered_qty"] or 0) * (delivery["kg_per_cylinder"] or 0), 2)
        dr_rows = [
            ["Delivered", f"{delivery['delivered_qty']} cylinder(s) x {delivery['kg_per_cylinder']} kg = {delivered_kg} kg"],
            ["Pulled out", f"{len(cylinders)} cylinder(s), residual {delivery['total_residual_kg'] or 0} kg"],
        ]
        dr_table = Table(dr_rows, colWidths=[150, 330])
        dr_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e6ea")),
        ]))
        flow.append(dr_table)
        if cylinders:
            flow.append(Spacer(1, 10))
            data = [["Pulled-out Serial No.", "Gross Wt", "Tare", "Residual (kg)"]] + [[
                c["serial_no"] or "—",
                c["gross_weight"] if c["gross_weight"] is not None else "—",
                c["tare_weight"] if c["tare_weight"] is not None else "—",
                c["residual_kg"],
            ] for c in cylinders]
            table = Table(data, repeatRows=1, colWidths=[160, 90, 90, 100])
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), BLUE), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GRAY]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e6ea")),
            ]))
            flow.append(table)
        total_consumption = round(delivered_kg - (delivery["total_residual_kg"] or 0), 2)
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
    unit = "kg" if delivery["billing_type"] in ("WIWO", "DR") else "unit(s)"
    summary_rows = [["Billable (delivered - residual)" if delivery["billing_type"] == "DR" else "Total Consumption",
                     f"{total_consumption} {unit}"]]
    if delivery["price_per_kg"]:
        summary_rows.append(["Rate", f"PHP {delivery['price_per_kg']:,.2f} per {unit[:-1] if unit.endswith('s') else unit}"])
    if delivery["billed_amount"]:
        summary_rows.append(["Billed Amount", f"PHP {delivery['billed_amount']:,.2f}"])
    summary_table = Table(summary_rows, colWidths=[210, 200])
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
        flow.append(Paragraph("Products Sold", h3))
        rows = [["Product", "Type", "Qty", "Amount"]]
        for i in data["items"]:
            rows.append([i["product_name"], i["sale_mode"], str(i["qty"]), m(i["amount"])])
        rows.append(["TOTAL", "", str(sum(i["qty"] for i in data["items"])),
                     m(sum(i["amount"] or 0 for i in data["items"]))])
        flow.append(styled(rows, [210, 80, 60, 100], total_row=True, right_from=2))
    else:
        flow.append(Paragraph("Products Sold", h3))
        flow.append(Paragraph("No products sold.", styles["Normal"]))

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

    ws.append([])
    header(ws, ["Products Sold", "Type", "Qty", "Amount"])
    for i in data["items"]:
        ws.append([i["product_name"], i["sale_mode"], i["qty"], i["amount"]])
    ws.append(["TOTAL", "", sum(i["qty"] for i in data["items"]), round(sum(i["amount"] or 0 for i in data["items"]), 2)])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)

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


def _styled_table(table_data, widths, total_row=False, right_from=None):
    t = Table(table_data, repeatRows=1, colWidths=widths)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GRAY]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e6ea")),
    ]
    if right_from is not None:
        style.append(("ALIGN", (right_from, 1), (-1, -1), "RIGHT"))
    if total_row:
        style += [("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                  ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e8f5ee"))]
    t.setStyle(TableStyle(style))
    return t


def swapped_pdf_response(data, subtitle, filename):
    """Swapped Cylinder Report: per-brand/size summary, every swap line, and
    other-brand empties still on hand."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter), topMargin=36, bottomMargin=36, leftMargin=36, rightMargin=36)
    styles = getSampleStyleSheet()
    h3 = ParagraphStyle("H3GUFC", parent=styles["Heading3"], textColor=BLUE, spaceBefore=10)
    flow = _header_flowables("Swapped Cylinder Report", subtitle)
    m = lambda n: f"{(n or 0):,.2f}"
    t = data["totals"]

    flow.append(Paragraph("Summary by Brand / Size", h3))
    rows = [["Brand Swapped", "Size (swapped for)", "Qty", "Swap Fees", "Sales Amount"]]
    for r in data["summary"]:
        rows.append([r["swap_brand"], r["product_name"], str(r["qty"]), m(r["swap_fees"]), m(r["amount"])])
    rows.append(["TOTAL", "", str(t["qty"]), m(t["swap_fees"]), m(t["amount"])])
    flow.append(_styled_table(rows, [150, 180, 60, 90, 100], total_row=True, right_from=2))

    flow.append(Paragraph("Swap Transactions", h3))
    rows = [["Date", "Sale #", "Location", "Cashier", "Customer", "Brand", "Size", "Qty", "Fee / Unit", "Swap Fees", "Amount"]]
    for r in data["rows"]:
        rows.append([r["ts"][:16], r["sale_no"], r["location_name"][:17], r["cashier"][:16], (r["customer_name"] or "Walk-in")[:18],
                     r["swap_brand"][:12], r["product_name"][:18], str(r["qty"]), m(r["swap_fee"]), m(r["swap_fee_total"]), m(r["amount"])])
    if len(rows) == 1:
        rows.append(["—", "No swap sales in this period", "", "", "", "", "", "", "", "", ""])
    flow.append(_styled_table(rows, [68, 92, 80, 74, 82, 56, 82, 26, 46, 50, 64], right_from=7))

    flow.append(Paragraph("Swapped Empties On Hand (not yet dispatched)", h3))
    rows = [["Location", "Brand", "Size (swapped for)", "Qty on Hand"]]
    for r in data["on_hand"]:
        rows.append([r["location_name"], r["brand"], r["product_name"], str(r["quantity"])])
    rows.append(["TOTAL", "", "", str(t["on_hand"])])
    flow.append(_styled_table(rows, [150, 120, 180, 80], total_row=True, right_from=3))

    doc.build(flow)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/pdf")


def swapped_xlsx_response(data, subtitle, filename):
    wb = Workbook()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="0B3D66", end_color="0B3D66", fill_type="solid")

    def header(ws, row):
        ws.append(row)
        for cell in ws[ws.max_row]:
            cell.font = header_font
            cell.fill = header_fill

    def bold_last(ws):
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)

    t = data["totals"]
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Swapped Cylinder Report"]); ws["A1"].font = Font(bold=True, size=14)
    ws.append([subtitle])
    ws.append([])
    header(ws, ["Brand Swapped", "Size (swapped for)", "Qty", "Swap Fees", "Sales Amount"])
    for r in data["summary"]:
        ws.append([r["swap_brand"], r["product_name"], r["qty"], r["swap_fees"], r["amount"]])
    ws.append(["TOTAL", "", t["qty"], t["swap_fees"], t["amount"]]); bold_last(ws)
    ws.append([])
    header(ws, ["On Hand: Location", "Brand", "Size (swapped for)", "Qty on Hand"])
    for r in data["on_hand"]:
        ws.append([r["location_name"], r["brand"], r["product_name"], r["quantity"]])
    ws.append(["TOTAL", "", "", t["on_hand"]]); bold_last(ws)
    for col, width in zip("ABCDE", (26, 24, 24, 14, 14)):
        ws.column_dimensions[col].width = width

    ws2 = wb.create_sheet("Swap Transactions")
    header(ws2, ["Date/Time", "Sale #", "Location", "Cashier", "Customer", "Brand", "Size", "Qty",
                 "Fee / Unit", "Swap Fees", "Amount"])
    for r in data["rows"]:
        ws2.append([r["ts"], r["sale_no"], r["location_name"], r["cashier"], r["customer_name"] or "Walk-in",
                    r["swap_brand"], r["product_name"], r["qty"], r["swap_fee"], r["swap_fee_total"], r["amount"]])
    for col, width in zip("ABCDEFGHIJK", (19, 20, 18, 18, 26, 14, 22, 8, 12, 12, 12)):
        ws2.column_dimensions[col].width = width

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _growth_fmt_pct(v):
    return "—" if v is None else f"{v:+.1f}%"


def customer_growth_pdf_response(data, subtitle, filename):
    """Customer Growth (LPG cylinders only): period-by-period table, plus the
    new and lapsed customers of the latest period."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter), topMargin=36, bottomMargin=36, leftMargin=36, rightMargin=36)
    styles = getSampleStyleSheet()
    h3 = ParagraphStyle("H3GUFC", parent=styles["Heading3"], textColor=BLUE, spaceBefore=10)
    flow = _header_flowables("Customer Growth Report", subtitle)
    m = lambda n: f"{(n or 0):,.2f}"
    cur = data["current"]

    flow.append(Paragraph(f"By {'Week' if data['period'] == 'week' else 'Month'}", h3))
    rows = [["Period", "Active", "New", "Retained", "Reactivated", "Lapsed", "Net", "Growth", "Retention",
             "Cylinders", "Cyl / Cust", "Walk-in Cyl", "Sales", "Customer Base"]]
    for r in data["rows"]:
        rows.append([r["label"], r["active"], r["new"], r["retained"], r["reactivated"], r["lapsed"],
                     f"{r['net_change']:+d}", _growth_fmt_pct(r["growth_pct"]),
                     "—" if r["retention_pct"] is None else f"{r['retention_pct']:.1f}%",
                     r["cylinders"], f"{r['cylinders_per_customer']:.2f}", r["walkin_cylinders"], m(r["amount"]),
                     r["customer_base"]])
    t = data["totals"]
    rows.append(["TOTAL", "", t["new"], "", "", "", "", "", "", t["cylinders"], "", t["walkin_cylinders"], m(t["amount"]), ""])
    rows = [[str(c) for c in r] for r in rows]
    flow.append(_styled_table(rows, [118, 40, 36, 46, 56, 42, 34, 46, 50, 48, 46, 52, 70, 60], total_row=True, right_from=1))

    flow.append(Paragraph(f"New Customers — {cur['label']} ({len(data['new_customers'])})", h3))
    rows = [["Account #", "Customer", "Business", "Category", "Phone", "First LPG Purchase", "Cylinders", "Amount"]]
    for c in data["new_customers"]:
        rows.append([c["account_no"], c["name"][:28], (c.get("business_name") or "")[:28], c.get("category") or "",
                     c.get("phone") or "", c["first_purchase"], str(c["cylinders"]), m(c["amount"])])
    if len(rows) == 1:
        rows.append(["—", "No new customers in this period", "", "", "", "", "", ""])
    flow.append(_styled_table(rows, [70, 140, 140, 70, 80, 90, 50, 70], right_from=6))

    flow.append(Paragraph(f"Lapsed Customers — bought last period, not in {cur['label']} ({len(data['lapsed_customers'])})", h3))
    rows = [["Account #", "Customer", "Business", "Category", "Phone", "Last LPG Purchase", "Cylinders Last Period"]]
    for c in data["lapsed_customers"]:
        rows.append([c["account_no"], c["name"][:28], (c.get("business_name") or "")[:28], c.get("category") or "",
                     c.get("phone") or "", c["last_purchase"] or "", str(c["prev_cylinders"])])
    if len(rows) == 1:
        rows.append(["—", "No lapsed customers", "", "", "", "", ""])
    flow.append(_styled_table(rows, [70, 150, 150, 70, 90, 90, 90], right_from=6))

    names = ", ".join(p["name"] for p in data["products_counted"]) or "none"
    flow.append(Spacer(1, 10))
    flow.append(Paragraph(f"Products counted (LPG cylinders): {names}",
                          ParagraphStyle("Note", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#6b7785"))))
    doc.build(flow)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/pdf")


def customer_growth_xlsx_response(data, subtitle, filename):
    wb = Workbook()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="0B3D66", end_color="0B3D66", fill_type="solid")

    def header(ws, row):
        ws.append(row)
        for cell in ws[ws.max_row]:
            cell.font = header_font
            cell.fill = header_fill

    ws = wb.active
    ws.title = "Growth"
    ws.append(["Customer Growth Report"]); ws["A1"].font = Font(bold=True, size=14)
    ws.append([subtitle])
    ws.append(["Products counted: " + (", ".join(p["name"] for p in data["products_counted"]) or "none")])
    ws.append([])
    header(ws, ["Period", "Start", "End", "Active Customers", "New", "Retained", "Reactivated", "Lapsed", "Net Change",
                "Growth %", "Retention %", "Cylinders Sold", "Cylinders / Customer", "Walk-in Cylinders",
                "LPG Sales", "Customer Base (cumulative)"])
    for r in data["rows"]:
        ws.append([r["label"], r["start"], r["end"], r["active"], r["new"], r["retained"], r["reactivated"], r["lapsed"],
                   r["net_change"], r["growth_pct"], r["retention_pct"], r["cylinders"], r["cylinders_per_customer"],
                   r["walkin_cylinders"], r["amount"], r["customer_base"]])
    t = data["totals"]
    ws.append(["TOTAL", "", "", "", t["new"], "", "", "", "", "", "", t["cylinders"], "", t["walkin_cylinders"], t["amount"], ""])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for i, width in enumerate((26, 12, 12, 10, 8, 10, 12, 8, 11, 10, 11, 10, 12, 12, 14, 14)):
        ws.column_dimensions[chr(65 + i)].width = width

    cur = data["current"]["label"]
    ws2 = wb.create_sheet("New Customers")
    ws2.append([f"New customers — {cur}"]); ws2["A1"].font = Font(bold=True)
    header(ws2, ["Account #", "Customer", "Business", "Category", "Phone", "First LPG Purchase", "Cylinders", "Amount"])
    for c in data["new_customers"]:
        ws2.append([c["account_no"], c["name"], c.get("business_name") or "", c.get("category") or "",
                    c.get("phone") or "", c["first_purchase"], c["cylinders"], c["amount"]])
    ws3 = wb.create_sheet("Lapsed Customers")
    ws3.append([f"Bought LPG last period but not in {cur}"]); ws3["A1"].font = Font(bold=True)
    header(ws3, ["Account #", "Customer", "Business", "Category", "Phone", "Last LPG Purchase", "Cylinders Last Period"])
    for c in data["lapsed_customers"]:
        ws3.append([c["account_no"], c["name"], c.get("business_name") or "", c.get("category") or "",
                    c.get("phone") or "", c["last_purchase"] or "", c["prev_cylinders"]])
    for w in (ws2, ws3):
        for col, width in zip("ABCDEFGH", (12, 28, 28, 14, 16, 18, 12, 12)):
            w.column_dimensions[col].width = width

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _kg(v):
    return f"{v:,.2f}".rstrip("0").rstrip(".") if v else "-"


def customer_kg_pdf_response(data, subtitle, filename):
    """Customer Monthly LPG (kg): one row per customer, one column per month."""
    from reportlab.lib.pagesizes import legal
    months = data["months"]
    pagesize = landscape(legal) if len(months) > 12 else landscape(letter)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=pagesize, topMargin=30, bottomMargin=30, leftMargin=24, rightMargin=24)
    styles = getSampleStyleSheet()
    flow = _header_flowables("Customer Monthly LPG Report (kg)", subtitle)
    t = data["totals"]
    short = [datetime.strptime(ym, "%Y-%m").strftime("%b %y") for ym in months]
    cell_style = ParagraphStyle("KgCell", parent=styles["Normal"], fontSize=7, leading=8.5)

    rows = [["#", "Customer", "Category"] + short + ["Total kg", "Cyl.", "Avg kg/mo"]]
    for i, r in enumerate(data["rows"], 1):
        label = _xml_escape(r["name"]) + (f" <font color='#6b7785'>({_xml_escape(r['account_no'])})</font>" if r["account_no"] else "")
        rows.append([str(i), Paragraph(label, cell_style), (r["category"] or "")[:12]] + [_kg(r["months"][ym]) for ym in months]
                    + [_kg(r["total_kg"]), str(r["cylinders"]), _kg(r["avg_kg_per_month"])])
    if not data["rows"]:
        rows.append(["", "No completed LPG orders by customers in this period", ""] + [""] * len(months) + ["", "", ""])
    rows.append(["", "TOTAL", ""] + [_kg(t["customers_months"][ym]) for ym in months]
                + [_kg(t["customers_kg"]), str(t["customers_cylinders"]), ""])
    extra_rows = 0
    if data["walkin"]:
        w = data["walkin"]
        rows.append(["", "Walk-in (no account)", ""] + [_kg(w["months"][ym]) for ym in months]
                    + [_kg(w["total_kg"]), str(w["cylinders"]), ""])
        rows.append(["", "GRAND TOTAL", ""] + [_kg(t["months"][ym]) for ym in months] + [_kg(t["kg"]), "", ""])
        extra_rows = 2

    avail = pagesize[0] - 48
    fixed = 18 + 52 + 48 + 30 + 44
    mw = min(60, max(30, (avail - fixed - 150) / max(1, len(months))))
    cust_w = avail - fixed - mw * len(months)
    widths = [18, cust_w, 52] + [mw] * len(months) + [48, 30, 44]
    tbl = Table(rows, repeatRows=1, colWidths=widths)
    total_idx = len(rows) - 1 - extra_rows
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BLUE), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GRAY]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e6ea")),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (-3, 1), (-3, -1), "Helvetica-Bold"),
        ("FONTNAME", (0, total_idx), (-1, total_idx), "Helvetica-Bold"),
        ("BACKGROUND", (0, total_idx), (-1, total_idx), colors.HexColor("#e8f5ee")),
    ]
    if extra_rows:
        style += [("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                  ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e3ecf5"))]
    tbl.setStyle(TableStyle(style))
    flow.append(tbl)

    note = ParagraphStyle("Note", parent=styles["Normal"], fontSize=7.5, textColor=colors.HexColor("#6b7785"))
    flow.append(Spacer(1, 8))
    flow.append(Paragraph("kg = cylinders × cylinder size (e.g. 11kg). 50kg commercial weight-billed sales use the "
                          "actual kg billed. Only completed sales of LPG cylinder products are included; voided sales "
                          "are excluded. Cyl. = number of cylinders (weight-billed kg not included).", note))
    if data["unknown_size_products"]:
        flow.append(Paragraph("Not included — no kg size in the product name: " + ", ".join(data["unknown_size_products"]), note))
    doc.build(flow)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/pdf")


def customer_kg_xlsx_response(data, subtitle, filename):
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="0B3D66", end_color="0B3D66", fill_type="solid")
    months, labels, t = data["months"], data["month_labels"], data["totals"]

    def header(ws, row):
        ws.append(row)
        for cell in ws[ws.max_row]:
            cell.font = header_font
            cell.fill = header_fill
        return ws.max_row

    def bold_last(ws):
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)

    def kg_format(ws, first_col, last_col, first_row):
        for row in ws.iter_rows(min_row=first_row, min_col=first_col, max_col=last_col):
            for cell in row:
                if isinstance(cell.value, (int, float)):
                    cell.number_format = "#,##0.00"

    ws = wb.active
    ws.title = "Per Customer (kg)"
    ws.append(["Customer Monthly LPG Report (kg)"]); ws["A1"].font = Font(bold=True, size=14)
    ws.append([subtitle])
    ws.append([])
    hdr = header(ws, ["Account #", "Customer", "Business Name", "Category"] + labels
                 + ["Total kg", "Cylinders", "Avg kg / Month", "Months Bought", "LPG Sales"])
    for r in data["rows"]:
        ws.append([r["account_no"], r["name"], r["business_name"], r["category"]] + [r["months"][ym] for ym in months]
                  + [r["total_kg"], r["cylinders"], r["avg_kg_per_month"], r["months_bought"], r["amount"]])
    ws.append(["", "TOTAL — CUSTOMERS", "", ""] + [t["customers_months"][ym] for ym in months]
              + [t["customers_kg"], t["customers_cylinders"], "", "", t["customers_amount"]]); bold_last(ws)
    if data["walkin"]:
        w = data["walkin"]
        ws.append(["", "Walk-in (no account)", "", ""] + [w["months"][ym] for ym in months]
                  + [w["total_kg"], w["cylinders"], "", "", w["amount"]])
        ws.append(["", "GRAND TOTAL", "", ""] + [t["months"][ym] for ym in months] + [t["kg"]]); bold_last(ws)
    n = 4 + len(months)
    kg_format(ws, 5, n + 1, hdr + 1)
    kg_format(ws, n + 3, n + 3, hdr + 1)
    kg_format(ws, n + 5, n + 5, hdr + 1)
    for i, width in enumerate([12, 28, 26, 13] + [11] * len(months) + [12, 10, 13, 13, 14], 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = ws.cell(row=hdr + 1, column=5)
    ws.append([])
    ws.append(["kg = cylinders x cylinder size (e.g. 11kg). 50kg commercial weight-billed sales use the actual kg billed. "
               "Only completed sales of LPG cylinder products are included."])
    if data["unknown_size_products"]:
        ws.append(["Not included — no kg size in the product name: " + ", ".join(data["unknown_size_products"])])

    ws2 = wb.create_sheet("Per Customer & Size")
    hdr2 = header(ws2, ["Account #", "Customer", "Product"] + labels + ["Total kg", "Cylinders"])
    for r in data["by_size"]:
        ws2.append([r["account_no"], r["name"], r["product_name"]] + [r["months"][ym] for ym in months]
                   + [r["total_kg"], r["cylinders"]])
    kg_format(ws2, 4, 4 + len(months), hdr2 + 1)
    for i, width in enumerate([12, 28, 26] + [11] * len(months) + [12, 10], 1):
        ws2.column_dimensions[get_column_letter(i)].width = width
    ws2.freeze_panes = "D2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
