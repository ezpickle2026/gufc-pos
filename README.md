# GUFC Solane LPG POS — Working Prototype

A real, runnable point-of-sale and light operations system for Great Unli Food Corp.'s
Solane LPG distributorship. Runs as one website, works on phone / tablet / desktop
via any Chrome/Safari/Edge browser — no app install.

**Scope note:** the original brief describes a full ERP (Next.js/Postgres/Prisma,
16 modules, vehicle fleet management, expense approval chains, PDF/Excel exports,
PWA offline sync, etc.). That's a multi-week build for a team, not a one-shot prototype.
What's here is a genuinely working vertical slice of the money-critical path — the
part where correctness actually matters: **POS → split payments → inventory →
receivables → transfers → audit log** — built so you can try it today and extend
it toward the full spec. See "What to extend first" below.

## Stack (and why)

- **Backend:** Python + Flask, plain `sqlite3` (no ORM). Single dependency footprint,
  runs anywhere, easy to read top-to-bottom.
- **Database:** SQLite file for the prototype. Swappable for Postgres later — see
  "Moving to Postgres" below — schema was written to translate directly.
- **Frontend:** Vanilla HTML/CSS/JS, one page, no build step. Responsive: sidebar
  nav on desktop, bottom nav on phone/tablet. Nothing to `npm install`, nothing to
  compile — open it and it works.
- **Auth:** Server-side sessions (signed cookie), password hashing via Werkzeug
  (PBKDF2). Role-based access control enforced **server-side** on every route, not
  just hidden in the UI.
- **Deployment:** Railway-ready via `Procfile` + `gunicorn`.

This is a deliberate scope-down from the Next.js/Prisma/Postgres stack in the brief
so that everything I'm handing you is real and tested, not scaffolding. The data
model maps cleanly onto Postgres+Prisma if/when you want to migrate.

## What's new in this update

- **Customer Portal: latest rate and rate movement.** Commercial accounts (and any customer with a 50kg
  Commercial Billing) get a **My Rate** tab in the portal, and a rate card on Home. It shows the current rate
  per kg (the account rate set in Customers → Rate, or the 50kg product's standard price per kg when none is set),
  the last change (▲/▼ amount and %), the 12-month high / low and number of changes, the average rate billed this
  month vs last month, a chart of the rate over the last 12 months with the rate on each billing, the full list of
  rate changes, and the rate / kg / amount of each billing. Every change to an account rate or a product's price per
  kg is now kept in `rate_history`; rate changes made before this update are taken from the Audit Log.

- **Sales: search and full history.** Sales has a search bar (sale #, DR #, customer name / business /
  account #, walk-in name), date and status filters, and pages through **every** sale back to the first one
  (Newest first or Oldest first, 50–500 per page). It no longer stops at the latest 100.
- **Edit a completed sale (Operation Head / Admin).** **Edit** beside a completed sale changes its items,
  mode, quantities, unit prices, discounts, withholding tax, customer (e.g. link a walk-in sale to its account)
  and notes. A reason is required and the before/after is saved in the Audit Log; the sale shows *ADJUSTED*.
  Stock, empties, swapped empties and cylinder deposits are reversed and re-applied; the receivable follows the
  new amount. Payments aren't changed, so a cash sale can't be raised above what was paid, and a credit sale
  can't be lowered below what was already paid. A delivery with a 50kg billing must have its billing voided first.
- **Customer Growth counts every customer purchase.** 50kg Commercial Billings (no longer sales) count for
  their customer again, a product with a cylinder size in its name/SKU (e.g. "11kg") counts even if its
  category isn't "Cylinder", and a 50kg Commercial delivery not yet billed counts in Monthly kg / Sales Targets
  until its billing is made. **New Customers** and **Lapsed** lists are short boxes with their own scroll bar.
- **SOA: billings go to Charges; previous arrears stay in the Beginning Balance.** Dates on the SOA are now in
  Philippine time (`APP_TZ_OFFSET_HOURS`, default 8). Before, a billing made before 8 AM was dated the day
  before and, with a From date of today, was folded into the Beginning Balance instead of showing as a charge.
  The Beginning Balance now shows its parts (previous arrears, balance forward) and billings show as *BILLING*.

- **Commercial billing is no longer recorded as a sale.** Every 50kg Commercial Billing
  (DR, WIWO, Reading, and Generate Billing below) creates only the customer's **invoice**
  (Receivables, SOA, aging, portal billing). It no longer appears in the Sales list, Sales
  report, End of Day, dashboard sales or staff sales totals. Billings made before this update
  are moved out of Sales automatically.
- **Generate Billing beside the sale (Accounting only).** In **Sales**, a Commercial account's
  POS credit sale with 50kg cylinders shows a **Generate Billing** button (Accounting, Admin,
  Operation Head). Type the residual kg of the cylinders pulled out; billable kg = delivered kg
  less residual, at the account's rate. Once billed the row shows *Billed CD-…*. A wrong billing
  can be undone with **Void Billing** in 50kg Commercial Billing while it is unpaid; the POS sale
  can't be voided while its billing stands.
- **Rate per kg on Commercial accounts.** Customers → **Rate** (Accounting / Admin) sets a
  Commercial account's price per kg. Every billing for that account uses it automatically.
- **Reading billing needs a beginning reading.** The first time a meter / tank is billed,
  enter its **beginning reading** first. The previous reading is then always filled in by the
  system (last bill's current reading, or the beginning reading) and can't be typed over;
  only the current reading is entered. Changing a beginning reading is for Admin / Manager /
  Accounting.
- **Monthly LPG per Customer (kg) lists every account.** The screen, Excel and PDF now include
  all active customers (those with no purchases in the range show "-" / 0), not only those who
  bought.

- **Commercial accounts: SOA shows only what was billed.** On a **Credit / Terms** POS sale
  to a customer in the **Commercial** category, 50kg cylinders are **delivered only**: stock
  moves, the empty comes back and the Delivery Receipt prints (marked "For billing"), but the
  SRP is **not charged** — no receivable and nothing on the SOA / Account Ledger / Portal. The
  customer is charged only through **50kg Commercial Billing** (DR, WIWO or Reading). Other
  items on the same sale (e.g. 11kg) are charged normally; a cylinder deposit or swap fee on a
  50kg line is still charged. Checkout shows the amount due without the 50kg SRP and a note.
  Cash sales and non-Commercial customers are unchanged. These delivery lines are left out of
  the Monthly kg report and Sales Targets so the 50kg isn't counted twice (the billing counts it).
- **Fix old Commercial SOA** (Customers → **Fix old Commercial SOA**, Admin / Operation Head).
  One-time clean-up for credit sales made *before* the change above, which charged the 50kg SRP
  to Commercial customers. It lists every such sale (date, sale/DR, 50kg lines, total and
  balance before → after); tick the ones to fix and press Apply. The 50kg lines become
  delivery-only (SRP kept for reference), the sale total and its receivable are lowered (a
  receivable that drops to ₱0 with nothing paid is removed), so the SOA shows only what was
  billed. Stock is not touched, other items stay charged, and sales from 50kg Commercial Billing
  are never changed. A sale where the customer already paid more than the new amount due is
  **skipped** and listed to fix by hand. A full database backup
  (`<db>.before-commercial-soa-fix-<timestamp>.bak`) is saved first, and each fixed sale is
  logged in the Audit Log.
- **Delivery Receipt for Commercial accounts has no amounts.** For customers in the
  **Commercial** category the DR shows only Qty, Unit and Description (plus total quantity) —
  no unit price, amount, totals, amount paid or balance. Other customers' DRs are unchanged.

- **New role: Operation Head** (`OPERATION_HEAD`). Same access as Admin to every screen
  and module **except System Reset (Go Live)**, which stays Admin-only (menu hidden and
  blocked on the server). Safety rails: only Admin can give out the ADMIN role or change /
  disable / delete an Admin account, and only Admin or an Operation Head can give out the
  OPERATION_HEAD role or change an Operation Head's account, so nobody can raise their own access.
- **New role: Supervisor** (`SUPERVISOR`). Can **import stock from the supplier into any
  location** (new **Import Stock** page under Inventory & Dispatch, with the list of past
  imports). Can also **void sales** at any location (Sales page → Void; the void reverses
  stock, empties and the receivable, and is logged in the Audit Log). Also has Dashboard,
  Sales, Inventory, Staff Schedule and Incident Reports.
- **Order Taker** can now **Dispatch** (transfers) and **Import Stock** too.
- **Accounting** can now fully manage customers' **Portal Logins** (create, reset password,
  disable, unlock, remove), same as Admin/Manager.
- **Import Stock** is now its own module ("Import Stock from Supplier" in Manage Roles), given by
  default to Dispatcher, Order Taker and Supervisor. The Dispatch page shows the import button
  only to roles that have it.
- **Sales Department** (new menu section): **Customer Growth** (moved here) and **Sales Targets**.
  - **Set Targets**: for the overall business, a location, or a customer (MRTO & ERTO by
    default; tick "Show all categories" for others). Pick a year and fill a grid of
    **4 tiers x 12 months**, separately for **Domestic (11kg & 1.4kg)** and **Commercial (50kg)**.
    Every month can have a different quantity (cylinders). "Fill blank months from January"
    copies January's values forward. A blank cell means no target.
  - **Achievement**: pick a month to see every target (overall, each location, each customer)
    with actual cylinders vs Tier 1–4 (% of each), the tier reached, and how many more to the
    next tier. Actual = cylinders on completed sales that month (voided excluded); Commercial also
    counts 50kg commercial billings (cylinders delivered).
  - Module "Sales Targets": Admin, Operation Head and Manager by default; grantable to other roles.
- **50kg Commercial Billing — new DR billing type** (now the default). Bill right at delivery,
  even if the 50kg serial numbers don't match: enter the DR No., the number of cylinders
  delivered (and kg per cylinder, default from the product, e.g. 50), and the cylinders pulled
  out with their gross and tare weight (or the residual kg directly).
  **Billable kg = delivered x kg per cylinder − total residual of the pulled-out cylinders**,
  at the product's price per kg (Admin, Operation Head, Manager and Accounting can type a different
  rate). Saving creates the credit sale, invoice/receivable and a system DR number right away
  (status BILLED) and offers to print the Delivery Receipt. Example: 10 x 50kg delivered = 500kg,
  10 pulled out with 65kg residual in total → bill 435kg. WIWO and READING still work as before.
  The billing does not move inventory, same as WIWO/READING.


- **Customer Portal** (`/portal`). Customers log in with their own username and
  password to:
  - **Place orders**: pick products and quantities, see the estimated total, and confirm
    the delivery address. Orders land in Booked Orders marked **Portal** for staff to
    assign and fulfil, the same as other orders.
  - **Follow their orders**: Received → Confirmed (once staff assign a warehouse/showroom)
    → Completed, or Cancelled. Customers can cancel an order themselves only while it is
    still unconfirmed.
  - **See their purchases**: every receipt with items, totals, DR number and, for credit
    sales, the invoice, due date and payment status.
  - **Billing & SOA** (credit / terms accounts only): balance due, overdue amount, next due
    date, credit limit and available credit, unpaid invoices, payments received, and the
    Statement of Account for any date range with a PDF download.
  - **Change their password.**

  **Setting up a login** (Admin / Manager): Customers → **Portal Login** on the customer's
  row. Set a username and password (or press Generate), then give the customer the link,
  username and password shown on screen. The same window resets a password, disables the
  login, unlocks it or removes it. Customers with a login show a **Portal** badge.

  **Security:** portal logins are separate from staff accounts and can never open staff
  screens. Each customer only ever sees their own account. Passwords are at least 8
  characters. After 5 wrong passwords the login is locked for 15 minutes (saving the login
  in Portal Login unlocks it). Resetting the password, disabling the login or changing the
  username logs the customer out everywhere. Portal logins, orders, cancellations and SOA
  downloads are recorded in the Audit Log as `portal:<username>`.

- **Customer Growth dashboard** (Customers & Money → Customer Growth; Admin, Manager and
  Accounting by default, grantable to other roles as the "Customer Growth" module).
  Weekly (Mon–Sun) or monthly tracking of customer growth that counts **only LPG cylinder
  purchases** (refill, deposit or swap) — products whose category contains "cylinder" or
  that have a cylinder deposit price. Accessories never make someone an active or new
  customer. Voided sales and opening-balance entries are ignored. For each period it shows
  active customers, new (first-ever LPG cylinder purchase), retained, reactivated, lapsed,
  growth % and retention % vs the previous period, cylinders sold, cylinders per customer,
  LPG sales and the cumulative customer base, plus a trend chart. It also lists this
  period's new customers, lapsed customers to follow up, a breakdown by customer category
  and the top customers. Filter by location, customer category and end date. Export to
  Excel or PDF. Walk-in sales can't be tracked per customer, so they appear only as a
  walk-in cylinder count.

- **Monthly LPG per Customer (kg)** (bottom of the Customer Growth page). One row per
  customer, one column per month, showing the kg of LPG in that customer's completed
  orders (voided sales excluded), with total kg, cylinders, average kg per month and LPG
  sales. kg = cylinders × the size in the product name (e.g. 3 × "Solane LPG 11kg" = 33 kg);
  50kg commercial weight-billed sales use the actual kg billed. Pick any month range (up
  to 24 months), location and customer category, and search by customer. Walk-in sales
  are shown as one separate row. Export to Excel (with a second sheet broken down per
  customer and cylinder size) or PDF. An LPG cylinder product with no kg size in its name
  is left out and flagged on screen and in the exports.

- **Delivery Receipt for credit / terms sales.** Every CREDIT sale now gets its own
  DR number (`DR-YYYYMMDD-####`). Right after a credit sale is completed, the
  Delivery Receipt prints instead of the small cash receipt: one letter-size page
  with a Customer's Copy and an Office Copy (cut line in between). It shows the
  customer, business name, address, contact no., terms, due date, sale / invoice
  reference, the items (qty, description, unit price, amount, with refill / deposit
  / swap / discount notes), total, amount paid, balance charged to the account, and
  signature lines for Prepared by, Delivered by and Received by. Reprint it any time
  from Sales (the **DR** button, or **Print Delivery Receipt** in the sale view).
  Voided sales print with a VOID mark. Existing credit sales were given DR numbers
  automatically.

- **Swapped Cylinder Report** (Reports → Swapped Cylinder Report, Admin/Manager).
  Every other-brand cylinder taken in on a swap sale, with filters for date range
  and location. Shows totals per brand and size (qty, swap fees, sales amount),
  each swap transaction, and the swapped empties still on hand. Print, PDF and Excel.
- **Print the hauling schedule as a calendar** (Hauling Schedule → pick a month →
  Print Calendar). Prints a landscape month calendar with each trip on its date:
  hauling #, status, plant and destination, vehicle and driver, and products.
  Cancelled trips are left out unless "Include cancelled" is ticked.
- **Products Sold in the End of Day Report** now has its own section with a total
  row, on screen and in the printout, the PDF and the Excel file.

- **System Reset (Go Live)** (Reports & Admin → System Reset, Admin only). Run this
  once when testing is over, before the client starts real operations. It always
  clears every transaction (sales, payments, receivables/arrears, transfers,
  inventory ledger, cylinder deposits, booked orders, commercial deliveries,
  hauling, incident reports, staff schedule, audit log) and sets all stock to 0.
  It keeps locations, products/prices, roles, customer categories and the admin
  account doing the reset. Optional checkboxes also clear customers, all other user
  accounts (e.g. the demo logins), employees & vehicles, and discounts. Requires
  typing `RESET` plus the admin password, and saves a full backup copy of the
  database next to it (`<db>.before-reset-<timestamp>.bak`) before deleting
  anything. A "Download backup" button is also on the page.
  A **Products** checkbox also clears every product, so the real product list can
  be encoded from scratch.
- **Delete products** (Products → Delete, Admin only). The product's stock must be 0.
  A product with no history is deleted outright; one already used in sales,
  transfers or other records is hidden instead so past records stay correct.
  Adding a product again with the same SKU brings it back.

- **Discounts & Promos settings** (Reports & Admin → Discounts & Promos, Admin/Manager).
  Create named discounts as **% off** or **₱ off per unit**, for **all items** or one
  specific product, with an optional minimum qty and start/end dates. In POS each
  cart line has an optional discount dropdown; the server recomputes every discount
  (never trusts the browser), and the receipt and sale view show it per line.
  Discounts apply to the gas price only, never the cylinder deposit fee. The old
  free-typed whole-sale discount box was replaced by these per-item discounts.
- **Sales Report is now per item.** One row per item sold with its own **Qty**
  column, plus Unit Price, Amount, Discount, Net Amount, and a **Remarks** column
  showing the discount/promo name applied (on screen, Excel and PDF).
- **Customer Category in reports.** The Sales Report, Accounts Receivable report and
  AR Aging report now show each customer's category (ERTO, MRTO, Commercial, DOMCOM,
  Solus, ...). Sales with no customer account show as **Walk-in**.

- **Dispatch empty cylinders** — a dispatch can now carry several lines, each
  marked **Full** or **Empty**. Empties can go to another location (added to
  that location's empties when it's marked Received) or out to a **plant** for
  refilling. Only empties can go to a plant. Empty-cylinder stock now has its
  own ledger (Inv. Ledger → "Empty Cylinders").
- **Adjust empty cylinder inventory** — Admin can add or remove empties
  (physical count, write-offs) from the Inventory page. A reason is required
  and every change is logged.
- **Receivables totals limited to Admin & Accounting** — the Outstanding /
  Overdue Receivables cards are withheld server-side for everyone else. A new
  built-in **ACCOUNTING** role (all-location receivables, payments, SOA,
  reports) has been added. Access is the `receivables_summary` module in
  Manage Roles.
- **End of Day Report** — each attendant's collections for the day, totalled
  by payment method (Cash, GCash, Bank Transfer, ...) and split into POS
  sales vs. AR collections, plus a sales summary, items sold, and every
  payment received. Print / PDF / Excel. Attendants see only their own;
  Admin/Manager/Accounting can choose any staff member or location.
- **Upload previous arrears** — Admin/Accounting can enter a customer's unpaid
  balance from before the system went live, one at a time or by CSV
  (`account_no, amount, as_of_date[, due_date, reference, notes]`). These act
  like normal invoices (payments, aging, overdue status). They are not counted
  as sales, and they roll into the **Beginning Balance** on the Statement of
  Account. If any row in the CSV has an error, nothing is saved. An unpaid
  entry can be removed to fix a mistake.

- **Two commercial billing types** — the 50kg Commercial module now supports:
  - **WIWO** (Weight In / Weight Out) — the existing flow: weigh cylinders in
    at delivery, weigh out at pickup, bill the difference (actual consumption)
  - **READING** — meter-based billing: enter the previous and current meter
    reading, and consumption is billed at the client's rate. The previous
    reading auto-fills from that customer's last bill, like a utility meter.
    Since both readings are captured together, these skip the pickup step and
    go straight to ready-to-bill.
- **Printable and PDF-exportable billing** — every commercial billing record
  (both types) now has a **Print** button and a **PDF** export, producing a
  branded document with the full weight/reading breakdown, rate, and total.

- **Incident Report attachments** — attach photos, PDFs, Excel/CSV, or Word
  docs (up to 10 files per report, 10MB each) when filing a report or any
  time after. Images and PDFs preview inline in the browser; other types
  download. Only the person who filed the report (or Admin/Manager) can add
  attachments; deleting one is Admin/Manager only. Files are stored in the
  database itself, so they survive redeploys automatically without needing a
  separate storage volume.
- **Printable Incident Reports** — a "Print" button on any report generates
  a clean, branded printout with the full report and resolution details.

- **50kg Commercial Weight-Billing** — a dedicated module for commercial LPG
  accounts billed by actual consumption rather than a fixed price:
  1. Dispatcher weighs cylinders in at delivery (Gross Weight, Tare Weight —
     Net Weight is calculated automatically), for one or many cylinders in a
     single delivery, with a running total
  2. Later, at pickup/exchange, weigh-out readings are recorded per cylinder;
     the system computes the residual and the actual amount consumed
     (delivered − residual) automatically
  3. Admin/Manager ("the accountant") generates an invoice from the total
     consumed weight × a price-per-kg rate — this creates a normal credit
     sale and receivable, so it shows up in Receivables, the Aging Report,
     and Statements of Account exactly like any other sale
  New "Price per KG" field on Products sets the default rate; it can be
  overridden per invoice. Validates that gross weight always exceeds tare,
  and that residual weight can never exceed what was delivered.

- **Accounts Receivable Aging Report** — standard aging buckets (Current,
  1-30, 31-60, 61-90, 91-120, 120+ days past due), grouped by customer with
  totals, accessible via an "Aging Report" button right on the Receivables
  page. Exportable as Excel or landscape PDF (Admin/Manager). Attendants see
  it too, correctly scoped to just their own location.

- **Public customer order form** — a standalone page at `/order` (no login
  needed) where customers can place an order themselves: pick products, an
  optional preferred warehouse/showroom, leave their name and phone number.
  It lands in Booked Orders as an unassigned "Online" order for an Order
  Taker (or Admin/Manager) to confirm and route to a specific location — or
  it's pre-filled if the customer picked a preference. Includes a copyable
  share link right on the Booked Orders page, basic input validation, and a
  honeypot field to deter simple bots.
- **Multi-product Booked Orders and Hauling Schedule** — both now support
  adding several products to a single order/trip instead of just one
- **CSV import fixed for Excel-saved files** — files saved by Excel on
  Windows often use a different text encoding than the web expects, which
  broke import when a name had an accented character (e.g. "Peña"). Import
  now handles this automatically.

- **Bug fixes**: vehicle creation was crashing when the odometer field was left
  empty — fixed, and while tracking it down I found and fixed the same fragile
  pattern in Products, Customers, and the **POS discount field** (a real crash
  risk mid-checkout) before you hit it there too
- **Receivables are now location-scoped** — attendants only see and can pay
  down receivables for their own location; Admin/Manager still see everything
- **Sales report PDF now exports in landscape** (verified against the actual
  PDF page dimensions, not just the code that requests it)
- **Manager can now add and edit users** (previously view-only), with full
  parity to Admin — including creating, editing, disabling, and deleting Admin
  accounts, at your explicit request
- **Custom roles** — Admin/Manager can create new roles from scratch (e.g.
  "Senior Cashier") and tick which modules that role can access, under
  **Manage Roles**. This is a real permission system, not cosmetic — a custom
  role ticked for POS access can actually complete sales, not just see the menu
  item. Users/Products/Locations management stay hardcoded to Admin (+Manager
  for Users) regardless of what's ticked, as a safety rail — see the note
  inside the Manage Roles screen
- **New Order Taker role + Booked Orders module** — an Order Taker books an
  order and assigns it to a specific showroom; that showroom's attendants see
  it in their queue and can mark it fulfilled (but not cancel — only
  Admin/Manager/Order Taker can cancel); other showrooms never see it
- **Incident Reports module** — any role can file one, auto-scoped to their
  own location; non-Admin/Manager users only see their own location's reports;
  only Admin/Manager can resolve one
- **Sidebar reorganized** into grouped sections (Operations / Customers & Money
  / Inventory & Dispatch / Fleet & Staff / Reports & Admin), and now driven by
  the same permission system above — a custom role only sees the modules it's
  actually been granted

**Database migration:** another schema change this round — a free-text user
role column (so custom role names work), plus new tables for roles,
permissions, booked orders, and incident reports. Same as before: it migrates
itself automatically and safely, tested against a simulated copy of your
actual current production database before I touched anything.

- **Refill vs. Deposit LPG sales** — the POS now distinguishes gas-only refills
  (customer exchanges an empty cylinder, which is tracked back into stock as an
  "empty" ready to haul to the plant) from Deposit/New sales (customer takes a
  full cylinder with no exchange, and the cylinder is tracked as on loan to them
  until they bring it back). Each product now has a configurable deposit fee.
- **Supplier stock import** — record inventory received from a supplier/plant
  directly from the Dispatch page, with supplier name and reference number
- **Manual inventory adjustment** — Admin can correct stock counts with a
  required reason, logged to the audit trail
- **Customers**: CSV import with a downloadable template, admin-only create/
  edit/delete (other roles are view-only), manageable categories, safe delete
  guard (blocked if the customer has sales history — deactivate instead)
- **Products**: CSV import/template, editable price/cost/deposit fee (LPG
  pricing fluctuates — this no longer requires a code change)
- **Locations & Users**: full edit/delete for Admins, with the same safe-delete
  guards (a location or user with real activity on record can't be hard-deleted
  — disable/deactivate instead)
- **Sales report** now shows the actual items purchased per transaction
- **Manager role** — a new role that can view (but not edit) most operational
  data: dashboard, sales, customers, receivables, inventory, reports, exports,
  audit log, plus full access to the new modules below
- **Vehicles** — fleet list with maintenance/registration/insurance dates (Admin
  + Manager)
- **Hauling Schedule** — plan and track trips to the supplier plant (Admin +
  Manager)
- **Employees** — basic HR records, optionally linked to a login account (Admin
  + Manager)
- **Staff Schedule** — a shift list (not a drag-and-drop calendar — see note
  below) that Admin/Manager can build and everyone can view
- **Employee KPI report** — sales performance per cashier (transactions, total
  sales, average sale, items sold), built from data already tracked by the
  POS — no extra data entry required (Admin + Manager)

**Honest scope note on the staffing/KPI ask:** a real drag-and-drop staffing
calendar and a fully custom KPI dashboard are each a meaningful project on
their own — what's shipped here is a working, functional list-based schedule
and a KPI report built from real sales data, which covers the substance of the
request without pretending to be a polished calendar UI. That's a good next
increment if it's worth the investment.

**Database migration:** this update changes the schema (new tables, a couple
of new columns, and a widened user-role constraint to add MANAGER). The app
migrates itself automatically and safely on first boot after the update — it
was tested against a simulated copy of a populated production database and
confirmed to preserve all existing data. You don't need to do anything by hand,
but if you're running on Railway with a persistent volume (recommended setup),
your existing data will carry forward untouched.

## What's actually implemented (and tested)

- Login/logout, sessions, **4 roles** (Admin, Manager, Attendant, Dispatcher)
  with server-side permission checks on every endpoint (not just hidden buttons)
- Locations (warehouse/showroom) with **per-location inventory** — never combined;
  Admin can create, edit, and delete (delete is blocked with a clear message if
  the location has real activity on record — deactivate instead)
- Products with server-side pricing (POS always re-fetches price from the DB —
  the browser's price is never trusted); historical sales keep the price at time
  of sale even if you change it later. Admin can edit price/cost/deposit fee
  anytime, create/edit/delete, and bulk-import via CSV with a downloadable template
- Customers with manageable categories, payment terms, credit limits. Admin-only
  create/edit/delete (other roles are view-only); CSV import with template;
  delete is blocked if the customer has sales history — deactivate instead
- **POS**: search, cart, quantity edit, discount, customer select, cash or credit
  sale, **split payments across multiple methods**, automatic inventory decrement,
  automatic receivable creation on credit balance, printable receipt with your logo
- **Refill vs. Deposit LPG sales**: refill sales price gas-only and return an empty
  cylinder to inventory automatically; deposit/new sales add the cylinder deposit
  fee and track the cylinder as on loan to the customer until returned
- **Brand swap sales**: choose *Swap (other brand)* on a cylinder line in the cart,
  pick the LPG brand the customer is handing over (Petron Gasul, Shellane,
  Prycegas, etc. or type another), and optionally type a swapping fee per cylinder
  (leave blank for no fee) — it's added to the line price and the total. The brand and fee show on the receipt,
  sale details and sales report. Swapped-in empties of other brands are not added
  to our empty-cylinder stock.
- Server-side validation: can't oversell inventory, can't exceed a customer's
  credit limit, can't complete a cash sale for less than the total, discounts can't
  exceed subtotal
- Accounts receivable: auto due-date calc from terms, status (CURRENT/DUE SOON/
  OVERDUE/PARTIALLY PAID/PAID), partial/full payment posting that updates the
  linked sale too
- **Account Ledger & Statement of Account (SOA)**: per-customer running balance
  on-screen with date filtering, plus a downloadable PDF statement
- Inventory transfers between locations (released→received), with duplicate-receive
  prevention; **supplier stock import** with supplier name/reference tracking;
  **manual inventory adjustment** (Admin) with a required reason
- **Inventory Ledger**: every stock movement, filterable by location/product/type
- Void sale (Admin) reverses inventory and keeps the record — never deletes;
  **view/reprint** any past sale's full receipt
- Full audit log (login/logout, sales, payments, inventory, transfers, and more),
  filterable by module, visible to Admin + Manager
- Users: Admin can create, edit (including password reset), disable/enable, and
  delete (delete is blocked if the user has real activity on record — disable
  instead)
- Dashboard: today's sales, outstanding/overdue receivables, low-stock alerts,
  payments-by-method
- **Reports**: sales (itemized, date-filtered), inventory, receivables aging —
  each exportable as real **.xlsx (Excel)** or **.pdf**, not print-to-PDF workarounds
- **Vehicles**, **Hauling Schedule**, **Employees**, **Staff Schedule**, and an
  **Employee KPI report** (Admin + Manager) — see "Honest scope note" above
- Mobile-responsive throughout, print-friendly receipts and reports

## What's stubbed or left out (be aware before you rely on this)

- Expense management and expense approval workflow — not built
- Cashier shift open/close with cash-count variance — not built
- Barcode scanning — not built
- Notifications (low stock alerts, overdue accounts, etc. beyond the dashboard) — not built
- A true drag-and-drop staffing calendar — the current Staff Schedule is a
  filterable list, not a calendar widget (see "Honest scope note" above)
- Fuel logs / cost-per-km tracking on vehicles — only maintenance/registration/
  insurance dates are tracked, not fuel consumption
- Password-change-on-first-login — not enforced (do it manually — see checklist)

## Running it locally

```bash
cd gufc-pos
pip install -r requirements.txt --break-system-packages   # or use a venv
python3 seed.py       # creates gufc.db and demo data — safe to run once
python3 app.py         # dev server on http://localhost:5000
```

Open `http://localhost:5000` in a browser (or on your phone if it's on the same
network — use your machine's LAN IP instead of localhost).

### Demo logins (seeded)

| Username    | Password       | Role       | Location            |
|-------------|----------------|------------|----------------------|
| admin       | ChangeMe123!   | Admin      | all                  |
| mgarcia     | Manager123!    | Manager    | all (view + oversight) |
| otaker      | Order123!      | Order Taker | books orders for any showroom |
| jsoliven    | Attendant123!  | Attendant  | Soliven Showroom     |
| mmadamba    | Attendant123!  | Attendant  | Madamba Warehouse    |
| pdispatch   | Dispatch123!   | Dispatcher | Madamba Warehouse    |
| aaccounting | Accounting123! | Accounting | (all locations)      |
| opshead     | OpsHead123!    | Operation Head | all (everything except System Reset) |
| supervisor  | Supervisor123! | Supervisor | Madamba Warehouse (imports stock to any location) |

**Change these before any real use** — create new users from the Admin → Users
screen and disable the seeded ones.

## Deploying to Railway

1. Push this folder to a GitHub repo, create a new Railway project from it.
2. Railway builds the app from the `Dockerfile` (Python 3.13 + `requirements.txt`).
   This avoids Railpack downloading its build tools from GitHub on every deploy.
3. Set environment variables in the Railway dashboard:
   - `SESSION_SECRET` — a long random string (`python3 -c "import secrets; print(secrets.token_hex(32))"`)
4. The container starts `gunicorn app:app`; `gunicorn.conf.py` picks up Railway's `PORT`.
   Leave Railway's custom Start Command empty (a command containing `$PORT` fails there).
5. **Important:** SQLite on Railway's default filesystem is ephemeral — it resets
   on redeploy. For anything beyond a demo, either attach a Railway volume and set
   `DB_PATH=/data/gufc.db`, or migrate to Postgres (next section).
6. After first deploy, log in as `admin`, create real users/locations/products,
   and disable the seeded accounts.

## Moving to Postgres (recommended before real money moves through this)

The schema in `db.py` is intentionally close to standard SQL — swapping `sqlite3`
for `psycopg2`/SQLAlchemy and adjusting a handful of syntax differences (autoincrement
→ `SERIAL`, `datetime('now')` → `now()`, etc.) is a contained job. This is the
first thing to do before this touches real transactions, because SQLite doesn't
give you the concurrent-write safety a POS with multiple simultaneous cashiers
needs.

## What I'd extend first (priority order)

1. **Postgres + a real host volume or managed DB.** SQLite is fine for one cashier
   testing on one device; it is not fine for two showrooms hitting the same
   database concurrently.
2. **Cashier shift open/close with cash reconciliation** — you called this out
   explicitly in the brief (section 33) and it's the kind of thing that prevents
   till disputes. Straightforward add: a `cashier_sessions` table + two new routes.
3. **Force password change on first login** for the seeded/admin-created accounts.
4. **CSV export on Reports and Receivables** — low effort, high daily value for
   whoever reconciles the books.
5. **Vehicle + expense modules** — same CRUD+audit pattern already established
   here, just new tables/routes/views following the pattern in `app.py`.

## Project structure

```
gufc-pos/
  app.py              Flask app: all API routes, POS transaction logic, RBAC
  db.py               SQLite schema + connection helper
  seed.py             Demo data (locations, products, users, customers, stock)
  requirements.txt
  Procfile             Railway/gunicorn start command
  .env.example
  static/
    index.html         App shell (login screen, sidebar, bottom nav)
    css/style.css       GUFC blue/green responsive theme
    js/app.js           All frontend logic (SPA router, POS, admin views)
```

## Production security checklist before real use

- [ ] Change/remove all seeded demo accounts
- [ ] Set a real `SESSION_SECRET` (not the dev default)
- [ ] Serve over HTTPS (Railway gives you this by default)
- [ ] Move off SQLite to Postgres with a persistent volume/managed DB
- [ ] Add rate limiting on `/api/login` (not present in this prototype)
- [ ] Review the "what's stubbed" list above against what you actually need live
