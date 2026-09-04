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

- **Excel & PDF export** on Sales, Inventory, and Receivables reports (real .xlsx via
  openpyxl, real .pdf via reportlab — not print-to-PDF workarounds)
- **Manual inventory adjustment** (Admin only) with a required reason, logged to the
  audit trail; optional override to allow the result to go negative
- **Inventory Ledger** page (Admin only) — every stock movement, filterable by
  location/product
- **Audit log filtering** — switch between All / Sales / Inventory activity
- **Statement of Account (SOA)** — downloadable PDF per customer with beginning
  balance, dated entries, running balance, ending balance
- **Account Ledger** — on-screen version of the same, with a date-range filter,
  accessible via "Ledger / SOA" on each customer row
- **Sale detail view + reprint** — "View" on any row in Sales opens the full
  breakdown and lets you reprint the original receipt
- **Your logo** is now on the login screen, sidebar, and printed receipts

## What's actually implemented (and tested)

- Login/logout, sessions, 3 roles (Admin, Attendant, Dispatcher) with server-side
  permission checks on every endpoint (not just hidden buttons)
- Locations (warehouse/showroom) with **per-location inventory** — never combined
- Products with server-side pricing (POS always re-fetches price from the DB —
  the browser's price is never trusted); historical sales keep the price at time
  of sale even if you change it later
- Customers with categories (ERTO/MRTO/Commercial/DOMCOM/Solus/ETC), payment terms,
  credit limits
- **POS**: search, cart, quantity edit, discount, customer select, cash or credit
  sale, **split payments across multiple methods**, automatic inventory decrement,
  automatic receivable creation on credit balance, printable receipt
- Server-side validation: can't oversell inventory, can't exceed a customer's
  credit limit, can't complete a cash sale for less than the total, discounts can't
  exceed subtotal
- Accounts receivable: auto due-date calc from terms, status (CURRENT/DUE SOON/
  OVERDUE/PARTIALLY PAID/PAID), partial/full payment posting that updates the
  linked sale too
- Inventory transfers between locations (request→released→received), with
  duplicate-receive prevention
- Void sale (admin only) reverses inventory and keeps the record — never deletes
- Full audit log (login/logout, sale created/voided, payment posted, product/user/
  location created, transfer created/received) — visible to Admin only
- Dashboard: today's sales, outstanding/overdue receivables, low-stock alerts,
  payments-by-method
- Reports: sales (date-filtered), inventory snapshot
- Mobile-responsive throughout, print-friendly receipt

## What's stubbed or left out (be aware before you rely on this)

- Vehicle monitoring, expense management, and expense approval workflow — not built
- CSV/Excel/PDF export — not built (reports render as HTML tables only)
- Cashier shift open/close with cash-count variance — not built
- Barcode scanning — not built
- Notifications — not built
- Customer statement / PDF invoice — not built (receipt print exists; statement
  view does not)
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
