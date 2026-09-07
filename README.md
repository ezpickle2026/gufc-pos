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

**Change these before any real use** — create new users from the Admin → Users
screen and disable the seeded ones.

## Deploying to Railway

1. Push this folder to a GitHub repo, create a new Railway project from it.
2. Railway auto-detects Python and installs `requirements.txt`.
3. Set environment variables in the Railway dashboard:
   - `SESSION_SECRET` — a long random string (`python3 -c "import secrets; print(secrets.token_hex(32))"`)
4. Railway runs the `Procfile` (`gunicorn app:app`) automatically.
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
