"""Builds docs/GUFC-POS_User_Manual.pdf — the role-based user handbook.

Run from the repo root:  python3 docs/manual/build_manual.py
Requires: reportlab (pip install reportlab)
"""
import os
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, KeepTogether, NextPageTemplate, PageBreak,
    PageTemplate, Paragraph, Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGO = os.path.join(ROOT, "static", "images", "logo.png")
OUT = os.path.join(ROOT, "docs", "GUFC-POS_User_Manual.pdf")

# ---- Fonts (DejaVu has the peso sign; fall back to Helvetica) ----------------
FONT, FONT_B = "Helvetica", "Helvetica-Bold"
for path_r, path_b in [("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]:
    if os.path.exists(path_r) and os.path.exists(path_b):
        pdfmetrics.registerFont(TTFont("DejaVu", path_r))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", path_b))
        pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold",
                                      italic="DejaVu", boldItalic="DejaVu-Bold")
        FONT, FONT_B = "DejaVu", "DejaVu-Bold"
PESO = "₱" if FONT == "DejaVu" else "PHP "

# ---- Palette (GUFC blue / green) ---------------------------------------------
BLUE = colors.HexColor("#0B4EA2")
BLUE_DARK = colors.HexColor("#08376F")
GREEN = colors.HexColor("#1E9E4A")
LIGHT = colors.HexColor("#EEF3FA")
TIP_BG = colors.HexColor("#E9F7EE")
WARN_BG = colors.HexColor("#FFF4E0")
WARN = colors.HexColor("#C77700")
GREY = colors.HexColor("#5B6573")
RULE = colors.HexColor("#D5DCE6")

ss = getSampleStyleSheet()
S = {
    "body": ParagraphStyle("body", parent=ss["Normal"], fontName=FONT, fontSize=10, leading=14.5,
                           spaceAfter=6),
    "small": ParagraphStyle("small", fontName=FONT, fontSize=8.5, leading=11.5, textColor=GREY),
    "cell": ParagraphStyle("cell", fontName=FONT, fontSize=8.8, leading=11.5),
    "cellb": ParagraphStyle("cellb", fontName=FONT_B, fontSize=8.8, leading=11.5, textColor=colors.white),
    "h1": ParagraphStyle("h1", fontName=FONT_B, fontSize=21, leading=26, textColor=BLUE_DARK,
                         spaceAfter=4),
    "h1sub": ParagraphStyle("h1sub", fontName=FONT, fontSize=11, leading=15, textColor=GREY,
                            spaceAfter=14),
    "h2": ParagraphStyle("h2", fontName=FONT_B, fontSize=13.5, leading=18, textColor=BLUE,
                         spaceBefore=12, spaceAfter=5, keepWithNext=1),
    "h3": ParagraphStyle("h3", fontName=FONT_B, fontSize=11, leading=15, textColor=BLUE_DARK,
                         spaceBefore=8, spaceAfter=3, keepWithNext=1),
    "step": ParagraphStyle("step", fontName=FONT, fontSize=10, leading=14, leftIndent=18,
                           firstLineIndent=-14, spaceAfter=3),
    "bullet": ParagraphStyle("bullet", fontName=FONT, fontSize=10, leading=14, leftIndent=14,
                             firstLineIndent=-10, spaceAfter=2),
    "box": ParagraphStyle("box", fontName=FONT, fontSize=9.5, leading=13.5),
    "toc0": ParagraphStyle("toc0", fontName=FONT_B, fontSize=11, leading=17, leftIndent=0),
    "toc1": ParagraphStyle("toc1", fontName=FONT, fontSize=9.5, leading=14, leftIndent=16,
                           textColor=GREY),
}


# ---- Doc template with TOC hooks ---------------------------------------------
class ManualDoc(BaseDocTemplate):
    def __init__(self, filename, **kw):
        super().__init__(filename, **kw)
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="f")
        self.addPageTemplates([
            PageTemplate(id="cover", frames=[frame], onPage=draw_cover),
            PageTemplate(id="body", frames=[frame], onPage=draw_body),
        ])

    def afterFlowable(self, f):
        if isinstance(f, Paragraph) and getattr(f, "toc_level", None) is not None:
            key = "h%d" % id(f)
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(f.getPlainText(), key, level=f.toc_level)
            self.notify("TOCEntry", (f.toc_level, f.getPlainText(), self.page, key))


def draw_cover(c, doc):
    w, h = A4
    c.saveState()
    c.setFillColor(BLUE_DARK)
    c.rect(0, h - 110 * mm, w, 110 * mm, stroke=0, fill=1)
    c.setFillColor(GREEN)
    c.rect(0, h - 113 * mm, w, 3 * mm, stroke=0, fill=1)
    if os.path.exists(LOGO):
        c.setFillColor(colors.white)
        c.circle(w / 2, h - 42 * mm, 25 * mm, stroke=0, fill=1)
        c.drawImage(LOGO, w / 2 - 17 * mm, h - 59 * mm, 34 * mm, 34 * mm, mask="auto")
    c.setFillColor(colors.white)
    c.setFont(FONT_B, 13)
    c.drawCentredString(w / 2, h - 78 * mm, "GREAT UNLI FOOD CORP.")
    c.setFont(FONT, 10.5)
    c.drawCentredString(w / 2, h - 85 * mm, "Solane LPG Distributor")
    c.setFillColor(BLUE_DARK)
    c.setFont(FONT_B, 30)
    c.drawCentredString(w / 2, h - 140 * mm, "GUFC-POS")
    c.setFont(FONT_B, 19)
    c.drawCentredString(w / 2, h - 152 * mm, "User Manual & Role Handbook")
    c.setFont(FONT, 11)
    c.setFillColor(GREY)
    c.drawCentredString(w / 2, h - 162 * mm,
                        "How each role uses the Point-of-Sale and Operations system")
    # role chips
    roles = ["Administrator", "Manager", "Accounting", "Attendant", "Dispatcher", "Order Taker"]
    chip_w, gap = 27 * mm, 3 * mm
    total = len(roles) * chip_w + (len(roles) - 1) * gap
    x = (w - total) / 2
    for r in roles:
        c.setFillColor(LIGHT)
        c.roundRect(x, h - 182 * mm, chip_w, 9 * mm, 4, stroke=0, fill=1)
        c.setFillColor(BLUE)
        c.setFont(FONT_B, 8)
        c.drawCentredString(x + chip_w / 2, h - 178.6 * mm, r)
        x += chip_w + gap
    c.setFont(FONT, 9)
    c.setFillColor(GREY)
    c.drawCentredString(w / 2, 30 * mm, "Edition: " + date.today().strftime("%B %Y"))
    c.drawCentredString(w / 2, 24 * mm, "For internal use by GUFC staff. Keep your password private.")
    c.restoreState()


def draw_body(c, doc):
    w, h = A4
    c.saveState()
    c.setStrokeColor(BLUE)
    c.setLineWidth(1.2)
    c.line(doc.leftMargin, h - 14 * mm, w - doc.rightMargin, h - 14 * mm)
    c.setFont(FONT_B, 8.5)
    c.setFillColor(BLUE_DARK)
    c.drawString(doc.leftMargin, h - 12 * mm, "GUFC-POS  |  User Manual & Role Handbook")
    c.setFont(FONT, 8.5)
    c.setFillColor(GREY)
    c.drawRightString(w - doc.rightMargin, h - 12 * mm, "Great Unli Food Corp.")
    c.setStrokeColor(RULE)
    c.setLineWidth(0.6)
    c.line(doc.leftMargin, 13 * mm, w - doc.rightMargin, 13 * mm)
    c.drawRightString(w - doc.rightMargin, 9 * mm, "Page %d" % doc.page)
    c.drawString(doc.leftMargin, 9 * mm, "Internal use only")
    c.restoreState()


# ---- Content helpers ----------------------------------------------------------
story = []


def P(text, style="body"):
    story.append(Paragraph(text, S[style]))


def chapter(title, subtitle=None, new_page=True):
    if new_page:
        story.append(PageBreak())
    p = Paragraph(title, S["h1"])
    p.toc_level = 0
    story.append(p)
    if subtitle:
        story.append(Paragraph(subtitle, S["h1sub"]))


def section(title):
    p = Paragraph(title, S["h2"])
    p.toc_level = 1
    story.append(p)


def sub(title):
    story.append(Paragraph(title, S["h3"]))


def steps(items):
    for i, t in enumerate(items, 1):
        story.append(Paragraph(f"<b>{i}.</b>&nbsp;&nbsp;{t}", S["step"]))
    story.append(Spacer(1, 4))


def bullets(items):
    for t in items:
        story.append(Paragraph(f"•&nbsp;&nbsp;{t}", S["bullet"]))
    story.append(Spacer(1, 4))


def callout(kind, text):
    bg, edge, label = {
        "tip": (TIP_BG, GREEN, "TIP"),
        "warn": (WARN_BG, WARN, "IMPORTANT"),
        "note": (LIGHT, BLUE, "NOTE"),
    }[kind]
    t = Table([[Paragraph(f"<b>{label}:</b> {text}", S["box"])]], colWidths=[None])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LINEBEFORE", (0, 0), (0, -1), 3, edge),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(Spacer(1, 3))
    story.append(t)
    story.append(Spacer(1, 7))


def table(header, rows, widths, align_center_from=None):
    data = [[Paragraph(h, S["cellb"]) for h in header]]
    for r in rows:
        data.append([Paragraph(str(c), S["cell"]) for c in r])
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if align_center_from is not None:
        style.append(("ALIGN", (align_center_from, 0), (-1, -1), "CENTER"))
    t.setStyle(TableStyle(style))
    story.append(t)
    story.append(Spacer(1, 8))


def role_card(role, sees, location, summary):
    rows = [
        [Paragraph("<b>Built-in role</b>", S["cell"]), Paragraph(role, S["cell"])],
        [Paragraph("<b>Location scope</b>", S["cell"]), Paragraph(location, S["cell"])],
        [Paragraph("<b>Menu items you see</b>", S["cell"]), Paragraph(sees, S["cell"])],
        [Paragraph("<b>Your job in the system</b>", S["cell"]), Paragraph(summary, S["cell"])],
    ]
    t = Table(rows, colWidths=[42 * mm, None])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.8, BLUE),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))


def checklist(title, items):
    rows = [[Paragraph("☐", S["cell"]), Paragraph(i, S["cell"])] for i in items]
    t = Table(rows, colWidths=[8 * mm, None])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(KeepTogether([Paragraph(title, S["h3"]), t, Spacer(1, 6)]))


# =============================================================================
# CONTENT
# =============================================================================
story.append(NextPageTemplate("body"))
story.append(PageBreak())

# ---- Contents ----------------------------------------------------------------
story.append(Paragraph("Contents", S["h1"]))
story.append(Spacer(1, 6))
toc = TableOfContents()
toc.levelStyles = [S["toc0"], S["toc1"]]
toc.dotsMinLevel = 0
story.append(toc)

# ---- 1. Introduction ---------------------------------------------------------
chapter("1. Introduction", "What GUFC-POS is, who it is for, and how to use this handbook")
section("1.1 About the system")
P("GUFC-POS is the point-of-sale and operations system of Great Unli Food Corp.'s Solane LPG "
  "distributorship. It runs as a website, so it works on a phone, tablet or computer through any "
  "modern browser (Chrome, Safari or Edge). Nothing needs to be installed.")
P("It covers the money-critical path of the business: <b>selling LPG at the counter, "
  "collecting split payments, keeping per-location stock, tracking customer credit "
  "(receivables), moving stock between locations, and billing commercial accounts</b>. Every "
  "important action is written to an audit log.")
section("1.2 How this handbook is organised")
P("Chapter 2 covers what <b>everyone</b> needs: signing in, finding your way around, and the "
  "features every role shares. Chapters 3 to 8 are written <b>one per role</b>. Read Chapter 2, "
  "then go straight to the chapter for your role. The appendices hold a glossary, a list of "
  "common messages and what they mean, and printable daily checklists.")
table(["Chapter", "Role", "Typical user"], [
    ["3", "Attendant", "Cashier at a showroom or warehouse counter"],
    ["4", "Dispatcher", "Warehouse dispatch / delivery staff"],
    ["5", "Order Taker", "Staff taking phone, chat and online orders"],
    ["6", "Accounting", "Billing, collections and statements"],
    ["7", "Manager", "Operations manager / supervisor"],
    ["8", "Administrator", "Owner or system administrator"],
], [22 * mm, 38 * mm, None])
callout("note", "Your company may also create <b>custom roles</b> (for example “Senior "
        "Cashier”). A custom role sees only the modules the Administrator ticked for it. If you "
        "have a custom role, read the chapters for the modules you can see in your menu.")

section("1.3 Role and access summary")
P("A tick (✓) means the role has the module by default. The server enforces these rules on "
  "every action, not just by hiding buttons. “View” means read-only.")
Y, V, N = "✓", "View", "–"
matrix = [
    ["Dashboard", Y, Y, Y, Y, Y, Y],
    ["POS (make sales)", Y, N, N, Y, N, N],
    ["Sales (history / void)", "Y + Void", V, V, "Own loc.", N, N],
    ["End of Day Report", Y, Y, Y, "Own", N, N],
    ["Booked Orders", Y, Y, N, "Own loc.", N, Y],
    ["Customers", "Edit", V, V, V, N, V],
    ["Receivables & payments", Y, Y, Y, "Own loc.", N, N],
    ["Receivables totals (cards)", Y, N, Y, N, N, N],
    ["Previous arrears upload", Y, N, Y, N, N, N],
    ["Cylinder Deposits", Y, V, N, Y, N, N],
    ["Inventory", "Y + Adjust", V, N, "Own loc.", "Own loc.", N],
    ["Inventory Ledger", Y, Y, N, N, N, N],
    ["Dispatch / Transfers", Y, N, N, N, Y, N],
    ["Hauling Schedule", Y, Y, N, N, N, N],
    ["50kg Commercial Billing", Y, "Y + Invoice", N, N, Y, N],
    ["Vehicles / Employees", Y, Y, N, N, N, N],
    ["Staff Schedule", "Edit", "Edit", N, V, V, N],
    ["Employee KPI", Y, Y, N, N, N, N],
    ["Reports (Excel / PDF)", Y, Y, Y, N, N, N],
    ["Audit Log", Y, Y, N, N, N, N],
    ["Incident Reports", "Y + Resolve", "Y + Resolve", N, "File", "File", "File"],
    ["Users / Manage Roles", Y, Y, N, N, N, N],
    ["Discounts & Promos", Y, Y, N, N, N, N],
    ["Products / Locations", Y, N, N, N, N, N],
    ["System Reset (Go Live)", Y, N, N, N, N, N],
]
table(["Module", "Admin", "Mgr.", "Acctg.", "Attend.", "Disp.", "Order Taker"],
      matrix, [48 * mm, 22 * mm, 22 * mm, 18 * mm, 20 * mm, 20 * mm, 24 * mm], align_center_from=1)

# ---- 2. Getting started ------------------------------------------------------
chapter("2. Getting Started (All Roles)", "Signing in, the screen layout, and shared features")
section("2.1 Signing in and out")
steps([
    "Open the GUFC-POS web address your Administrator gave you in Chrome, Safari or Edge.",
    "Type your <b>username</b> and <b>password</b> and tap <b>Log In</b>.",
    "You land on the first screen your role can use (usually the <b>Dashboard</b>).",
    "When you finish your shift, tap <b>Log Out</b>. Sign-ins and sign-outs are recorded in the audit log.",
])
callout("warn", "Never share your login. Every sale, payment and stock movement is stamped with the "
        "user who did it. If you forget your password, ask an Administrator or Manager to reset it "
        "under <b>Users → Edit</b>.")
section("2.2 Finding your way around")
bullets([
    "<b>On a computer:</b> the menu is a sidebar on the left, grouped into <i>Operations</i>, "
    "<i>Customers &amp; Money</i>, <i>Inventory &amp; Dispatch</i>, <i>Fleet &amp; Staff</i> and "
    "<i>Reports &amp; Admin</i>.",
    "<b>On a phone or tablet:</b> the same menu appears as a bottom navigation bar.",
    "You only see the menu items your role is allowed to use. If something in this manual is "
    "missing from your menu, your role does not have it.",
    "Green buttons confirm an action (Save, Complete Sale). Red buttons remove or cancel.",
    "Messages appear briefly at the top/bottom of the screen: green for success, red for an error "
    "explaining what to fix.",
])
section("2.3 Dashboard")
P("The Dashboard shows today's sales, low-stock alerts and payments by method. Accounting and "
  "Administrators also see the <b>Outstanding</b> and <b>Overdue Receivables</b> cards; other roles "
  "do not. Use the date shortcuts (<b>Today, Last 7 Days, Last 30 Days, This Month</b>) or pick "
  "dates and tap <b>Apply</b>.")
section("2.4 Staff Schedule")
P("Open <b>Staff Schedule</b> to see your shifts. Use the filters and tap <b>Filter</b>. Only "
  "Administrators and Managers can add (<b>+ Add Shift</b>) or remove shifts.")
section("2.5 Filing an Incident Report")
P("Any role can report a problem: a leaking cylinder, a short delivery, a customer complaint, a "
  "vehicle issue or an accident.")
steps([
    "Open <b>Incident Reports</b> and tap <b>+ Report Incident</b>.",
    "Fill in what happened. The report is automatically tagged with your location.",
    "Attach evidence if you have it: photos, PDFs, Excel/CSV or Word files (up to 10 files, 10 MB each).",
    "Tap <b>Submit Report</b>. You can add more attachments later with <b>View → Upload</b>.",
    "Use <b>Print</b> for a branded paper copy. An Administrator or Manager will <b>Resolve</b> it.",
])
callout("note", "You see only the reports for your own location. Only Administrators and "
        "Managers can resolve reports or delete attachments.")

# ---- 3. Attendant ------------------------------------------------------------
chapter("3. Attendant (Cashier)", "Selling at the counter, collecting payments and closing the day")
role_card(
    "ATTENDANT",
    "Dashboard, POS, Sales, End of Day Report, Booked Orders, Customers (view), Receivables, "
    "Cylinder Deposits, Inventory, Staff Schedule, Incident Reports",
    "Your assigned location only. You cannot see or sell from another showroom's stock.",
    "Ring up sales, accept payments, post payments on customer accounts, fulfil booked orders, "
    "handle cylinder returns and close your day with the End of Day Report.",
)
section("3.1 Making a sale (POS)")
steps([
    "Open <b>POS</b>. Products show the stock available at <b>your</b> location.",
    "Search for a product and tap it to add it to the <b>Cart</b>. Change the quantity in the cart "
    "line; tap <b>×</b> to remove a line.",
    "For each LPG cylinder line, choose the sale type in the small dropdown (see 3.2): "
    "<b>Refill</b>, <b>New/Deposit</b> or <b>Swap</b>.",
    "If a promo applies, pick it from the line's discount dropdown (see 3.3).",
    "Check the <b>Total</b> and tap <b>Checkout</b>.",
    "In the Checkout window choose the <b>Customer</b> (or leave <i>Walk-in / no account</i>) and the "
    "<b>Sale Type</b>: <b>Cash Sale</b> or <b>Credit / Terms Sale</b>.",
    "Enter the payment(s). One Cash line for the full total is added for you. Change the method "
    "(Cash, GCash, Bank Transfer, Maya, Cheque, Other) and type a <b>Ref #</b> for non-cash payments.",
    "Tap <b>Complete Sale</b>. The receipt opens for printing, stock is reduced automatically, and "
    "any unpaid balance on a credit sale becomes a receivable.",
])
callout("tip", "<b>Split payments:</b> tap <b>+ Add Payment</b> to take, for example, "
        f"{PESO}500 cash plus {PESO}650 GCash. Watch <i>Paid so far</i> and <i>Balance</i> update "
        "as you type.")
section("3.2 Refill, New/Deposit and Swap")
table(["Sale type", "When to use it", "What the system does"], [
    ["<b>Refill</b> (exchange empty)", "Customer brings back an empty Solane cylinder.",
     "Charges gas price only. The empty goes back into your location's empty-cylinder stock."],
    ["<b>New / Deposit</b>", "Customer takes a full cylinder without returning one.",
     "Adds the product's cylinder deposit fee. The cylinder is tracked as <i>on loan</i> to the "
     "customer under Cylinder Deposits."],
    ["<b>Swap</b> (other brand)", "Customer hands over an empty of another brand (Petron Gasul, "
     "Shellane, Prycegas, etc.).",
     "Pick the brand (or type another) and an optional swapping fee per cylinder, then tap "
     "<b>Apply Swap</b>. The fee is added to the price. The brand and fee print on the receipt."],
], [35 * mm, 55 * mm, None])
section("3.3 Discounts and promos")
bullets([
    "Each cart line has an optional discount dropdown listing the active promos for that product.",
    "A promo may need a minimum quantity. If you see <i>qty below promo minimum</i>, the discount is "
    "not applied.",
    "Discounts apply to the gas price only, never to the cylinder deposit fee.",
    "The server recalculates every discount, so the receipt always shows the correct amount.",
])
section("3.4 Credit (terms) sales")
P("Choose <b>Credit / Terms Sale</b> and select the customer account. You may take a partial "
  "payment now or nothing at all. The system blocks the sale if it would push the customer over "
  "their <b>credit limit</b>. The due date is set from the customer's payment terms.")
callout("warn", "A <b>Cash Sale</b> cannot be completed for less than the total. If the customer "
        "cannot pay in full, it must be a credit sale to a customer account.")
section("3.5 Sales history and reprints")
P("Open <b>Sales</b> to see recent sales at your location. Tap <b>View</b> to see the full sale and "
  "<b>Reprint Receipt</b> if the customer needs another copy. Only an Administrator can "
  "<b>Void</b> a sale. Ask your supervisor if a sale was entered by mistake.")
section("3.6 Collecting payment on an account")
steps([
    "Open <b>Receivables</b>. You see only your location's open balances.",
    "Find the invoice and tap <b>Record Payment</b>.",
    "Enter the amount, method and reference number, then tap <b>Save Payment</b>. Partial payments "
    "are allowed. The status updates (PARTIALLY PAID or PAID).",
])
P("Tap <b>Aging Report</b> to see how overdue your location's accounts are. From "
  "<b>Customers → Ledger / SOA</b> you can view a customer's running balance and download a PDF "
  "Statement of Account.")
section("3.7 Cylinder deposit returns")
steps([
    "When a customer returns a cylinder they took on deposit, open <b>Cylinder Deposits</b>.",
    "Find the customer's deposit in the list and tap <b>Record Return</b>.",
    "Enter the <b>Quantity Returned</b> and the <b>Receiving Location</b>.",
    "Tap <b>Confirm Return</b>. The cylinder is no longer on loan to the customer.",
])
section("3.8 Fulfilling booked orders")
P("Orders booked by an Order Taker (or by customers online) and assigned to your location appear "
  "in <b>Booked Orders</b>. When the customer has been served, tap <b>Mark Fulfilled</b>. Attendants "
  "cannot cancel orders. Ask the Order Taker or a Manager.")
section("3.9 Checking stock")
P("<b>Inventory</b> shows full and empty cylinder counts at your location. If the numbers do not "
  "match what is on the floor, tell the Administrator. Only an Administrator can adjust stock.")
section("3.10 End of Day Report")
steps([
    "At closing, open <b>End of Day Report</b>. It shows <b>your own</b> collections for the date.",
    "Check <b>Total Collected</b>, <b>From POS Sales</b>, <b>AR Collections</b> and "
    "<b>Charged to Accounts</b>.",
    "Compare <b>Collections by Payment Method</b> with the cash in your drawer and your GCash, "
    "bank and cheque receipts.",
    "Print it, or export it as PDF or Excel, and hand it in with your cash.",
])

# ---- 4. Dispatcher -----------------------------------------------------------
chapter("4. Dispatcher", "Moving stock, receiving supplier deliveries and weighing commercial cylinders")
role_card(
    "DISPATCHER",
    "Dashboard, Inventory, Dispatch, 50kg Commercial Billing, Staff Schedule, Incident Reports",
    "Your assigned warehouse for inventory views. Dispatches move stock between any locations.",
    "Record every cylinder that leaves or arrives, receive supplier stock, and capture weights or "
    "meter readings for commercial accounts.",
)
section("4.1 Sending a dispatch / transfer")
steps([
    "Open <b>Dispatch</b> and tap <b>+ New Transfer / Dispatch</b>.",
    "Choose the <b>from</b> location and the <b>destination</b>: another location, or a <b>plant</b> "
    "for refilling.",
    "Add a line for each product with <b>+ Add Item</b>. Mark each line <b>Full</b> or <b>Empty</b> "
    "and enter the quantity.",
    "Tap <b>Dispatch</b>. Stock leaves the source location immediately and the transfer shows as "
    "released.",
])
callout("note", "Only <b>empty</b> cylinders can be sent to a plant. The system will not let you "
        "dispatch more than the location has in stock.")
section("4.2 Receiving a transfer")
P("When the truck arrives at the destination, find the transfer in <b>Dispatch</b> and tap "
  "<b>Mark Received</b>. Full cylinders are added to the destination's stock; empties are added to "
  "its empty-cylinder count. A transfer can only be received once.")
section("4.3 Importing stock from a supplier")
steps([
    "Tap <b>+ Import Stock from Supplier</b> on the Dispatch page.",
    "Choose the receiving location, the product and the quantity.",
    "Enter the <b>supplier name</b> and the <b>reference number</b> (delivery receipt / invoice no.).",
    "Tap <b>Record Received Stock</b>.",
])
section("4.4 50kg Commercial Billing: WIWO (weight in / weight out)")
P("Used for commercial customers billed by the actual kilograms they consumed.")
steps([
    "Open <b>50kg Commercial Billing</b> and tap <b>+ New Commercial Billing</b>. Choose the "
    "customer and billing type <b>WIWO</b>.",
    "For each cylinder delivered tap <b>+ Add Cylinder</b> and enter its label, <b>Gross Weight</b> "
    "and <b>Tare Weight</b>. Net weight is calculated for you, with a running total.",
    "Tap <b>Save</b>. Status becomes <b>DELIVERED</b>.",
    "At pickup or exchange, tap <b>Record Pickup</b> and enter the weigh-out reading for each "
    "cylinder. The system computes the residual gas and the actual consumption.",
    "Tap <b>Save Pickup</b>. Status becomes <b>PICKED_UP</b>, ready for a Manager to invoice.",
])
callout("warn", "Gross weight must always be more than tare weight, and the residual can never be "
        "more than what was delivered. The system rejects readings that break these rules. "
        "Re-weigh instead of guessing.")
section("4.5 50kg Commercial Billing: READING (meter)")
steps([
    "Tap <b>+ New Commercial Billing</b>, choose the customer and billing type <b>READING</b>.",
    "The <b>previous reading</b> fills in from the customer's last bill. Enter the "
    "<b>current reading</b>.",
    "Tap <b>Save</b>. Because both readings are captured, it goes straight to ready-to-bill.",
])
P("You can <b>View</b>, <b>Print</b> or download a <b>PDF</b> of any billing record, and "
  "<b>Cancel</b> one that has not been invoiced yet.")

# ---- 5. Order Taker ----------------------------------------------------------
chapter("5. Order Taker", "Booking orders and routing them to the right showroom")
role_card(
    "ORDER_TAKER",
    "Dashboard, Booked Orders, Customers (view), Incident Reports",
    "All showrooms: you decide which location fulfils each order.",
    "Take orders by phone, chat or walk-in, confirm online orders, assign them to a showroom, and "
    "keep the queue clean.",
)
section("5.1 Booking an order")
steps([
    "Open <b>Booked Orders</b> and tap <b>+ Book Order</b>.",
    "Select an <b>Existing Customer</b>, or type the caller's name, phone number and delivery address.",
    "Add each product with <b>+ Add Product</b> and enter the quantity. One order can have several products.",
    "Choose the <b>showroom/warehouse</b> that will serve the order.",
    "Tap <b>Book Order</b>. Only that location's attendants see it in their queue.",
])
section("5.2 Handling online orders")
P("Customers can order through the public order page (<b>/order</b>) without logging in. These "
  "orders arrive in Booked Orders marked <b>Online</b>.")
bullets([
    "If the customer chose a preferred location, it is already filled in. Check it and call the customer to confirm.",
    "If not, tap <b>Assign</b> and choose the location. Use <b>Reassign</b> to move it to another showroom.",
    "Tap <b>Copy Link</b> to copy the order-page link and share it on Facebook, Viber or SMS.",
])
section("5.3 Fulfilling and cancelling")
P("The showroom marks an order <b>Fulfilled</b> when the customer is served. You can also "
  "<b>Fulfill</b> or <b>Cancel</b> orders yourself, for example when a customer calls to cancel "
  "or an order is a duplicate. Attendants cannot cancel orders, so cancellations come to you.")
callout("tip", "Check the queue several times a day. An order that has been open too long usually "
        "means the showroom has no stock or could not reach the customer. Call them before reassigning.")

# ---- 6. Accounting -----------------------------------------------------------
chapter("6. Accounting", "Receivables, collections, statements and financial reports")
role_card(
    "ACCOUNTING",
    "Dashboard (incl. receivables totals), Sales, End of Day Report, Customers (view), "
    "Receivables, Reports",
    "All locations.",
    "Own the customer balances: post collections, load previous arrears, send Statements of "
    "Account, check each cashier's End of Day Report and run financial reports.",
)
section("6.1 Monitoring receivables")
P("The Dashboard shows the <b>Outstanding</b> and <b>Overdue Receivables</b> totals. Only "
  "Accounting and Administrators see these. In <b>Receivables</b> each invoice has a status:")
table(["Status", "Meaning"], [
    ["CURRENT", "Not yet due."],
    ["DUE SOON", "Due date is close. A good time for a reminder call."],
    ["OVERDUE", "Past due date and still unpaid."],
    ["PARTIALLY PAID", "Some payment received; a balance remains."],
    ["PAID", "Fully settled."],
], [40 * mm, None])
P("Tap <b>Aging Report</b> to group balances into Current, 1–30, 31–60, 61–90, "
  "91–120 and 120+ days past due, by customer, with each customer's category. Export it to "
  "Excel or PDF.")
section("6.2 Posting a payment")
steps([
    "In <b>Receivables</b>, find the invoice and tap <b>Record Payment</b>.",
    "Enter the amount, payment method and reference (OR no., cheque no., bank ref.).",
    "Tap <b>Save Payment</b>. The linked sale and the customer's ledger update immediately.",
])
section("6.3 Loading previous arrears (go-live balances)")
P("Unpaid balances from before the system went live are entered as <b>previous arrears</b>. They "
  "behave like normal invoices (payments, aging, overdue status) but are <b>not</b> counted as "
  "sales, and they roll into the <b>Beginning Balance</b> on the Statement of Account.")
bullets([
    "<b>One at a time:</b> tap <b>+ Add Previous Arrears</b>, choose the customer, amount and as-of date, then <b>Save</b>.",
    "<b>In bulk:</b> tap <b>Upload Previous Arrears (CSV)</b>. Columns: "
    "<font face='Courier'>account_no, amount, as_of_date[, due_date, reference, notes]</font>.",
    "If any CSV row has an error, <b>nothing</b> is saved. Fix the file and upload again.",
    "An unpaid arrears entry entered by mistake can be removed with <b>Remove</b>.",
])
section("6.4 Statements of Account")
P("Open <b>Customers</b>, find the account and tap <b>Ledger / SOA</b>. Set the date range to see "
  "the running balance, then download the <b>PDF Statement</b> to send to the customer.")
section("6.5 End of Day review")
P("Open <b>End of Day Report</b> and choose any <b>Staff</b> member and <b>Location</b> (or all). "
  "Match each attendant's <i>Collections by Payment Method</i> to the cash turned over and the "
  "GCash/bank deposits. Print or export to PDF/Excel for filing.")
section("6.6 Reports")
P("<b>Reports</b> provides the Sales Report (one row per item with qty, unit price, amount, "
  "discount, net amount, customer category and promo remarks), the Inventory report and the "
  "Receivables reports. Set the date range, tap <b>Run</b>, then export to <b>Excel</b> or <b>PDF</b>.")

# ---- 7. Manager --------------------------------------------------------------
chapter("7. Manager", "Oversight of every location, staff, fleet, promos and commercial invoicing")
role_card(
    "MANAGER",
    "Dashboard, Sales, End of Day Report, Booked Orders, Customers (view), Receivables, Cylinder "
    "Deposits (view), Inventory (view), Inv. Ledger, Hauling Schedule, 50kg Commercial Billing, "
    "Vehicles, Employees, Staff Schedule, Employee KPI, Reports, Audit Log, Incident Reports, "
    "Users, Manage Roles, Discounts &amp; Promos",
    "All locations.",
    "Supervise daily operations, invoice commercial accounts, plan hauling, manage staff and "
    "users, run promos and resolve incidents.",
)
section("7.1 Daily oversight")
bullets([
    "<b>Dashboard</b> for today's sales and low-stock alerts across all locations.",
    "<b>Sales</b> and <b>End of Day Report</b> for any attendant or location.",
    "<b>Inventory</b> and <b>Inv. Ledger</b> to trace every stock movement by location, product and "
    "type, including the separate <i>Empty Cylinders</i> ledger.",
    "<b>Audit Log</b> to see who did what and when, filterable by module.",
])
section("7.2 Invoicing commercial accounts")
steps([
    "Open <b>50kg Commercial Billing</b>. Records in <b>PICKED_UP</b> (WIWO) or ready-to-bill (READING) "
    "status show a <b>Generate Invoice</b> button.",
    f"Check the consumption. The rate defaults to the product's <b>Price per KG</b>; you can override it for this invoice.",
    "Tap <b>Generate Invoice</b>. This creates a normal credit sale and receivable, so it appears in "
    "Receivables, the Aging Report and the customer's Statement of Account.",
    "Print or download the PDF billing document for the customer.",
])
section("7.3 Hauling schedule and vehicles")
P("In <b>Hauling Schedule</b> tap <b>+ Schedule Hauling</b> to plan a trip to the supplier plant: "
  "scheduled date, plant name, vehicle, driver, destination location and products (<b>+ Add Product</b> for several). In <b>Vehicles</b> keep "
  "each truck's maintenance, registration and insurance dates up to date with <b>+ New Vehicle</b> "
  "and <b>Edit</b>.")
section("7.4 Staff: employees, schedule and KPI")
bullets([
    "<b>Employees</b>: keep HR records with <b>+ New Employee</b>; optionally link one to a login account.",
    "<b>Staff Schedule</b>: build shifts with <b>+ Add Shift</b>. Everyone can view their own.",
    "<b>Employee KPI</b>: transactions, total sales, average sale and items sold per cashier. Tap "
    "<b>Run</b> for a date range. No extra data entry is needed.",
])
section("7.5 Users and roles")
steps([
    "<b>Users → + New User</b>: enter name, username, password, role and (for attendants and "
    "dispatchers) the location. Tap <b>Create User</b>.",
    "<b>Edit</b> to change details or reset a password. <b>Disable</b> a user who leaves instead of "
    "deleting them, so their history stays intact.",
    "<b>Manage Roles → + Create Role</b> to make a custom role and tick the modules it can use.",
])
callout("warn", "Products, Locations and System Reset remain Administrator-only no matter which "
        "modules a custom role is given.")
section("7.6 Discounts and promos")
steps([
    "Open <b>Discounts &amp; Promos</b> and tap <b>+ New Discount / Promo</b>.",
    f"Name it, choose <b>% off</b> or <b>{PESO} off per unit</b>, and apply it to <b>all items</b> or one product.",
    "Optionally set a minimum quantity and start/end dates, then <b>Save</b>.",
    "Cashiers can now pick it in the POS cart. The Sales Report shows the promo name under Remarks.",
])
section("7.7 Incidents and booked orders")
P("Review <b>Incident Reports</b> from every location. Open one with <b>View</b> and tap "
  "<b>Resolve</b> to record the resolution. In <b>Booked Orders</b> you can assign, reassign, "
  "fulfil or cancel any order.")

# ---- 8. Administrator --------------------------------------------------------
chapter("8. Administrator", "Full control: setup, master data, corrections and going live")
role_card(
    "ADMIN",
    "Every module, plus Products, Locations and System Reset (Go Live)",
    "All locations. On POS, choose which location you are selling from.",
    "Set up and maintain the system: locations, products and prices, customers, users and roles. "
    "Correct stock, void sales, and perform the one-time go-live reset.",
)
P("Administrators can do everything in Chapters 3 to 7. This chapter covers the tasks only an "
  "Administrator can do.")
section("8.1 Locations")
P("<b>Locations → + New Location</b>: enter the name, type (Warehouse or Showroom) and address. "
  "Stock is kept separately for each location and is never combined. A location with activity "
  "cannot be deleted; deactivate it instead.")
section("8.2 Products and prices")
steps([
    "<b>Products → + New Product</b>: SKU, name, category, unit, <b>price</b>, cost, minimum "
    "stock, <b>cylinder deposit fee</b> and <b>Price per KG</b> (for commercial billing).",
    "LPG prices change often. Use <b>Edit</b> to update a price at any time. Past sales keep the "
    "price at the time of sale.",
    "For many products at once use <b>Import CSV</b> (download the template first).",
    "<b>Delete</b> requires the product's stock to be 0. A product already used in sales is hidden "
    "rather than erased, and comes back if you add the same SKU again.",
])
section("8.3 Customers")
P("Only Administrators can create, edit or delete customers. Use <b>+ New Customer</b> and set the "
  "<b>category</b> (ERTO, MRTO, Commercial, DOMCOM, Solus...), <b>payment terms</b> (days) and "
  "<b>credit limit</b>. Use <b>Manage Categories</b> to maintain the category list and "
  "<b>Import CSV</b> for bulk loading. A customer with sales history cannot be deleted; deactivate "
  "them instead.")
section("8.4 Stock corrections")
bullets([
    "<b>Inventory → + Adjust Stock</b>: correct full-cylinder counts after a physical count.",
    "<b>+ Adjust Empties</b>: add or remove empty Solane cylinders (write-offs, counts).",
    "<b>+ Adjust Swapped Empties</b>: correct the count of other-brand empties received in swaps.",
    "A <b>reason</b> is always required, and every adjustment is logged in the audit trail.",
])
section("8.5 Voiding a sale")
P("In <b>Sales</b>, tap <b>Void</b> on the wrong sale. The stock is returned and the sale is kept on "
  "record as voided. It is never deleted. Re-enter the correct sale on the POS if needed.")
section("8.6 System Reset (Go Live)")
P("Run this <b>once</b>, when testing is finished and before real operations start.")
bullets([
    "<b>Always cleared:</b> all sales, payments, receivables/arrears, transfers, inventory ledger, "
    "cylinder deposits, booked orders, commercial deliveries, hauling, incident reports, staff "
    "schedule and audit log. All stock is set to 0.",
    "<b>Kept:</b> locations, products and prices, roles, customer categories and your admin account.",
    "<b>Optional checkboxes</b> also clear customers, other user accounts (e.g. demo logins), "
    "employees &amp; vehicles, discounts, and products.",
])
steps([
    "Tap <b>Download backup first</b> and keep the file safe.",
    "Tick the optional items you want cleared.",
    "Type <b>RESET</b> and your admin password, then tap <b>Reset system now</b>. A backup copy of "
    "the database is also saved automatically on the server.",
    "Load real data: opening stock (Import Stock from Supplier or Adjust Stock), customers, and "
    "previous arrears.",
])
callout("warn", "System Reset cannot be undone from inside the app. Make sure everyone has stopped "
        "using the system and the backup has downloaded before you confirm.")
section("8.7 Security checklist")
bullets([
    "Disable or delete the demo accounts (admin, mgarcia, otaker, jsoliven, mmadamba, pdispatch, "
    "aaccounting) and change the admin password.",
    "Give each person their own login. Never share accounts.",
    "Disable users on the day they leave the company.",
    "Review the <b>Audit Log</b> regularly for voids, stock adjustments and logins at odd hours.",
])

# ---- Appendices --------------------------------------------------------------
chapter("Appendix A. Glossary")
table(["Term", "Meaning"], [
    ["AR / Receivable", "Money a customer owes from a credit sale or previous arrears."],
    ["Aging", "Grouping unpaid balances by how many days past due they are."],
    ["Booked Order", "An order taken in advance and assigned to a showroom to fulfil."],
    ["Cylinder Deposit", "Fee charged when a customer takes a cylinder without returning an empty; "
     "the cylinder is tracked as on loan."],
    ["Dispatch / Transfer", "Moving full or empty cylinders between locations or to a plant."],
    ["DOMCOM, ERTO, MRTO, Solus", "Customer categories used for pricing and reporting."],
    ["Empties", "Empty Solane cylinders held at a location, waiting to be hauled for refill."],
    ["EOD Report", "End of Day Report: collections per attendant, by payment method."],
    ["Hauling", "A trip to the supplier plant to exchange empties for full cylinders."],
    ["Previous Arrears", "Balances from before go-live. Not counted as sales."],
    ["READING", "Meter-based commercial billing (current minus previous reading)."],
    ["Refill", "Gas-only sale where the customer exchanges an empty cylinder."],
    ["SOA", "Statement of Account: a customer's transactions and running balance."],
    ["Swap", "Customer exchanges an empty of another brand for a full Solane cylinder."],
    ["Tare weight", "Weight of the empty cylinder itself."],
    ["Void", "Cancelling a sale while keeping it on record and returning the stock."],
    ["WIWO", "Weight In / Weight Out commercial billing based on actual kg consumed."],
], [45 * mm, None])

chapter("Appendix B. Common Messages and What To Do")
table(["You see...", "Why", "What to do"], [
    ["Not enough stock", "The location has fewer units than you are selling or dispatching.",
     "Check Inventory. Request a transfer, or ask the Admin to correct the count."],
    ["Credit limit exceeded", "The sale would push the customer over their limit.",
     "Take a larger payment now, collect old balances first, or ask the Admin to raise the limit."],
    ["Payment less than total (cash sale)", "A cash sale must be paid in full.",
     "Collect the full amount or switch to a credit sale on a customer account."],
    ["qty below promo minimum", "The promo needs a higher quantity.",
     "Increase the quantity or choose another discount."],
    ["Gross must exceed tare / residual too high", "A weight reading is impossible.",
     "Re-weigh the cylinder and enter the correct figure."],
    ["CSV error (nothing saved)", "One or more rows in the uploaded file are invalid.",
     "Read the row numbers in the message, fix the file, upload again."],
    ["Cannot delete: has activity", "Records are linked to this item.",
     "Disable or deactivate it instead."],
    ["Menu item missing / not allowed", "Your role does not have that module.",
     "Ask an Administrator or Manager if you need access."],
    ["Already received", "The transfer was marked received earlier.",
     "No action needed. Check the transfer history."],
], [42 * mm, 55 * mm, None])

chapter("Appendix C. Daily Checklists")
P("Print this page and keep it at each station.", "small")
checklist("Attendant: opening", [
    "Sign in with your own account.",
    "Check Inventory for your location; report any mismatch.",
    "Check Booked Orders assigned to your showroom.",
])
checklist("Attendant: closing", [
    "Fulfil served Booked Orders.",
    "Run End of Day Report; match cash, GCash, bank and cheque totals.",
    "Print/export the report and hand it in with the cash. Log out.",
])
checklist("Dispatcher", [
    "Record every outgoing load as a Dispatch (Full / Empty lines).",
    "Mark arriving transfers Received.",
    "Record supplier deliveries with supplier name and reference no.",
    "Capture commercial weigh-ins, pickups and meter readings the same day.",
])
checklist("Order Taker", [
    "Assign all new Online orders to a showroom.",
    "Follow up orders open longer than expected; cancel duplicates.",
])
checklist("Accounting", [
    "Review each attendant's End of Day Report against deposits.",
    "Post collections in Receivables.",
    "Review the Aging Report; follow up OVERDUE accounts and send SOAs.",
])
checklist("Manager / Administrator", [
    "Review Dashboard, low-stock alerts and the Audit Log.",
    "Invoice PICKED_UP / ready-to-bill commercial records.",
    "Resolve open Incident Reports; confirm tomorrow's Staff Schedule and Hauling.",
    "Update product prices when Solane prices change.",
])


# ---- Build -------------------------------------------------------------------
def build():
    doc = ManualDoc(OUT, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                    topMargin=22 * mm, bottomMargin=20 * mm,
                    title="GUFC-POS User Manual & Role Handbook",
                    author="Great Unli Food Corp.",
                    subject="Role-based user manual for the GUFC-POS system")
    doc.multiBuild(story)
    print("Wrote", OUT)


if __name__ == "__main__":
    build()
