import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "gufc.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('WAREHOUSE','SHOWROOM')),
    address TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    location_id INTEGER REFERENCES locations(id),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    category TEXT,
    unit TEXT NOT NULL DEFAULT 'cylinder',
    price REAL NOT NULL,
    cost REAL NOT NULL DEFAULT 0,
    deposit_price REAL NOT NULL DEFAULT 0,
    price_per_kg REAL NOT NULL DEFAULT 0,
    min_stock INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS customer_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_no TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    business_name TEXT,
    phone TEXT,
    address TEXT,
    category TEXT NOT NULL DEFAULT 'Solus',
    terms_days INTEGER NOT NULL DEFAULT 0,
    credit_limit REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS inventory (
    location_id INTEGER NOT NULL REFERENCES locations(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (location_id, product_id)
);

CREATE TABLE IF NOT EXISTS inventory_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT (datetime('now')),
    location_id INTEGER NOT NULL REFERENCES locations(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty_change INTEGER NOT NULL,
    type TEXT NOT NULL,
    reference TEXT,
    user_id INTEGER REFERENCES users(id),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_no TEXT UNIQUE NOT NULL,
    ts TEXT DEFAULT (datetime('now')),
    location_id INTEGER NOT NULL REFERENCES locations(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    customer_id INTEGER REFERENCES customers(id),
    sale_type TEXT NOT NULL CHECK(sale_type IN ('CASH','CREDIT')),
    subtotal REAL NOT NULL,
    discount REAL NOT NULL DEFAULT 0,
    total REAL NOT NULL,
    paid REAL NOT NULL DEFAULT 0,
    balance REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'COMPLETED' CHECK(status IN ('COMPLETED','VOIDED')),
    notes TEXT,
    is_opening_balance INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sale_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    product_name TEXT NOT NULL,
    qty INTEGER NOT NULL,
    price REAL NOT NULL,
    subtotal REAL NOT NULL,
    sale_mode TEXT NOT NULL DEFAULT 'REFILL' CHECK(sale_mode IN ('REFILL','DEPOSIT','SWAP'))
);

CREATE TABLE IF NOT EXISTS empty_cylinder_inventory (
    location_id INTEGER NOT NULL REFERENCES locations(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (location_id, product_id)
);

CREATE TABLE IF NOT EXISTS empty_cylinder_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT (datetime('now')),
    location_id INTEGER NOT NULL REFERENCES locations(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty_change INTEGER NOT NULL,
    type TEXT NOT NULL,
    reference TEXT,
    user_id INTEGER REFERENCES users(id),
    notes TEXT
);

-- Empties of OTHER brands taken in on SWAP sales, broken down per brand.
-- product_id is the size it was swapped for (e.g. Solane LPG 11kg).
CREATE TABLE IF NOT EXISTS swapped_empty_inventory (
    location_id INTEGER NOT NULL REFERENCES locations(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    brand TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (location_id, product_id, brand)
);

CREATE TABLE IF NOT EXISTS swapped_empty_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT (datetime('now')),
    location_id INTEGER NOT NULL REFERENCES locations(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    brand TEXT NOT NULL,
    qty_change INTEGER NOT NULL,
    type TEXT NOT NULL,
    reference TEXT,
    user_id INTEGER REFERENCES users(id),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS cylinder_deposits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty_on_loan INTEGER NOT NULL DEFAULT 0,
    UNIQUE(customer_id, product_id)
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    method TEXT NOT NULL,
    amount REAL NOT NULL,
    reference TEXT,
    ts TEXT DEFAULT (datetime('now')),
    user_id INTEGER REFERENCES users(id),
    payment_source TEXT NOT NULL DEFAULT 'SALE'
);

CREATE TABLE IF NOT EXISTS receivables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    invoice_no TEXT UNIQUE NOT NULL,
    invoice_date TEXT NOT NULL,
    due_date TEXT NOT NULL,
    amount REAL NOT NULL,
    paid REAL NOT NULL DEFAULT 0,
    balance REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'CURRENT'
);

CREATE TABLE IF NOT EXISTS transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_no TEXT UNIQUE NOT NULL,
    ts TEXT DEFAULT (datetime('now')),
    from_location INTEGER NOT NULL REFERENCES locations(id),
    to_location INTEGER REFERENCES locations(id),
    to_external TEXT,
    status TEXT NOT NULL DEFAULT 'RELEASED' CHECK(status IN ('RELEASED','RECEIVED','CANCELLED')),
    user_id INTEGER REFERENCES users(id),
    received_by INTEGER REFERENCES users(id),
    received_ts TEXT
);

CREATE TABLE IF NOT EXISTS transfer_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_id INTEGER NOT NULL REFERENCES transfers(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty INTEGER NOT NULL,
    cylinder_state TEXT NOT NULL DEFAULT 'FULL'
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT (datetime('now')),
    user_id INTEGER REFERENCES users(id),
    username TEXT,
    action TEXT NOT NULL,
    module TEXT NOT NULL,
    record_id TEXT,
    details TEXT
);

CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plate_number TEXT UNIQUE NOT NULL,
    vehicle_type TEXT,
    description TEXT,
    driver TEXT,
    status TEXT NOT NULL DEFAULT 'AVAILABLE' CHECK(status IN ('AVAILABLE','DISPATCHED','IN TRANSIT','MAINTENANCE','INACTIVE')),
    odometer INTEGER DEFAULT 0,
    last_maintenance_date TEXT,
    next_maintenance_date TEXT,
    registration_expiry TEXT,
    insurance_expiry TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS hauling_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hauling_no TEXT UNIQUE NOT NULL,
    scheduled_date TEXT NOT NULL,
    vehicle_id INTEGER REFERENCES vehicles(id),
    driver TEXT,
    plant_name TEXT NOT NULL,
    destination_location_id INTEGER REFERENCES locations(id),
    product_id INTEGER REFERENCES products(id),
    qty INTEGER,
    status TEXT NOT NULL DEFAULT 'SCHEDULED' CHECK(status IN ('SCHEDULED','LOADING','IN TRANSIT','COMPLETED','CANCELLED')),
    notes TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS hauling_schedule_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hauling_id INTEGER NOT NULL REFERENCES hauling_schedule(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    position TEXT,
    location_id INTEGER REFERENCES locations(id),
    linked_user_id INTEGER REFERENCES users(id),
    contact_number TEXT,
    hire_date TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','ON LEAVE','INACTIVE')),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS staff_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    shift_date TEXT NOT NULL,
    shift_start TEXT,
    shift_end TEXT,
    location_id INTEGER REFERENCES locations(id),
    notes TEXT,
    created_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS commercial_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_no TEXT UNIQUE NOT NULL,
    delivery_date TEXT DEFAULT (datetime('now')),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    location_id INTEGER NOT NULL REFERENCES locations(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    billing_type TEXT NOT NULL DEFAULT 'WIWO' CHECK(billing_type IN ('WIWO','READING','DR')),
    meter_label TEXT,
    previous_reading REAL,
    current_reading REAL,
    status TEXT NOT NULL DEFAULT 'DELIVERED' CHECK(status IN ('DELIVERED','PICKED_UP','BILLED','CANCELLED')),
    pickup_date TEXT,
    price_per_kg REAL,
    billed_amount REAL,
    sale_id INTEGER REFERENCES sales(id),
    notes TEXT,
    created_by INTEGER REFERENCES users(id),
    collected_by INTEGER REFERENCES users(id),
    billed_by INTEGER REFERENCES users(id),
    dr_ref TEXT,
    delivered_qty INTEGER,
    kg_per_cylinder REAL,
    total_residual_kg REAL
);

-- DR (manual) 50kg billing: the cylinders pulled out at delivery. Serial
-- numbers don't have to match what was delivered; only the residual LPG
-- left in each pulled-out cylinder is deducted from the bill.
CREATE TABLE IF NOT EXISTS commercial_pullouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id INTEGER NOT NULL REFERENCES commercial_deliveries(id),
    serial_no TEXT,
    gross_weight REAL,
    tare_weight REAL,
    residual_kg REAL NOT NULL DEFAULT 0
);

-- Sales Department targets: 4 tiers per month, split Domestic (11kg and
-- 1.4kg) / Commercial (50kg), for the whole business, a location, or a
-- customer. qty = cylinders.
CREATE TABLE IF NOT EXISTS sales_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL CHECK(scope IN ('OVERALL','LOCATION','CUSTOMER')),
    scope_id INTEGER NOT NULL DEFAULT 0,
    month TEXT NOT NULL,
    segment TEXT NOT NULL CHECK(segment IN ('DOMESTIC','COMMERCIAL')),
    tier INTEGER NOT NULL CHECK(tier BETWEEN 1 AND 4),
    qty INTEGER NOT NULL,
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(scope, scope_id, month, segment, tier)
);

CREATE TABLE IF NOT EXISTS commercial_delivery_cylinders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id INTEGER NOT NULL REFERENCES commercial_deliveries(id),
    cylinder_label TEXT,
    gross_weight_in REAL NOT NULL,
    tare_weight REAL NOT NULL,
    net_weight_in REAL NOT NULL,
    gross_weight_out REAL,
    net_weight_out REAL,
    consumption_weight REAL
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_key TEXT PRIMARY KEY,
    applied_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    is_builtin INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_name TEXT NOT NULL,
    module_key TEXT NOT NULL,
    PRIMARY KEY (role_name, module_key)
);

CREATE TABLE IF NOT EXISTS booked_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT UNIQUE NOT NULL,
    order_date TEXT DEFAULT (datetime('now')),
    customer_id INTEGER REFERENCES customers(id),
    customer_name_freeform TEXT,
    customer_phone TEXT,
    delivery_address TEXT,
    showroom_location_id INTEGER REFERENCES locations(id),
    source TEXT NOT NULL DEFAULT 'STAFF' CHECK(source IN ('STAFF','CUSTOMER')),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','FULFILLED','CANCELLED')),
    notes TEXT,
    created_by INTEGER REFERENCES users(id),
    fulfilled_by INTEGER REFERENCES users(id),
    fulfilled_at TEXT
);

CREATE TABLE IF NOT EXISTS booked_order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES booked_orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty INTEGER NOT NULL
);

-- Customer Portal logins: one per customer account. Kept apart from the
-- staff `users` table so a customer login can never reach staff screens.
CREATE TABLE IF NOT EXISTS customer_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER UNIQUE NOT NULL REFERENCES customers(id),
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    session_version INTEGER NOT NULL DEFAULT 1,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    last_login TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    created_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS discounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('PERCENT','FIXED')),
    value REAL NOT NULL,
    product_id INTEGER REFERENCES products(id),
    min_qty INTEGER NOT NULL DEFAULT 1,
    start_date TEXT,
    end_date TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS incident_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_no TEXT UNIQUE NOT NULL,
    ts TEXT DEFAULT (datetime('now')),
    location_id INTEGER REFERENCES locations(id),
    reported_by INTEGER REFERENCES users(id),
    category TEXT,
    severity TEXT NOT NULL DEFAULT 'LOW' CHECK(severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','RESOLVED')),
    resolution_notes TEXT,
    resolved_by INTEGER REFERENCES users(id),
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS incident_report_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incident_reports(id),
    filename TEXT NOT NULL,
    mimetype TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    file_data BLOB NOT NULL,
    uploaded_by INTEGER REFERENCES users(id),
    uploaded_at TEXT DEFAULT (datetime('now'))
);
"""


def _column_exists(conn, table, column):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return column in cols


def _users_table_has_open_role(conn):
    """True once the users.role column has no CHECK constraint at all —
    needed so custom role names (beyond the 5 built-ins) can be assigned."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    return bool(row) and "CHECK" not in row[0]


# Modules a role can be granted access to via the custom role/permission system.
# NOTE: user management, product management, and location management are
# deliberately NOT part of this list — those stay hardcoded to Admin (and
# Manager for user management) as a safety rail, so a misconfigured custom
# role can never grant itself financial/administrative control.
MODULE_KEYS = [
    "dashboard", "pos", "sales", "customers", "receivables", "inventory",
    "inventory_ledger", "cylinder_deposits", "transfers", "hauling", "vehicles",
    "employees", "staff_schedule", "kpi", "reports", "audit",
    "booked_orders", "incident_reports", "commercial_deliveries",
    "eod_report", "receivables_summary", "customer_growth",
    "stock_import", "sales_targets",
]

# Roles with Admin-level access. OPERATION_HEAD can do everything Admin can
# EXCEPT the System Reset (Go Live), which stays Admin-only.
ADMIN_LIKE_ROLES = ("ADMIN", "OPERATION_HEAD")

DEFAULT_ROLE_PERMISSIONS = {
    "ADMIN": MODULE_KEYS,  # ADMIN always has everything regardless of this table
    "OPERATION_HEAD": MODULE_KEYS,  # also always full access (minus System Reset)
    "MANAGER": [
        "dashboard", "sales", "customers", "receivables", "inventory",
        "inventory_ledger", "cylinder_deposits", "hauling", "vehicles",
        "employees", "staff_schedule", "kpi", "reports", "audit",
        "booked_orders", "incident_reports", "commercial_deliveries", "eod_report",
        "customer_growth", "sales_targets",
    ],
    "ACCOUNTING": [
        "dashboard", "sales", "customers", "receivables", "reports", "eod_report",
        "receivables_summary", "customer_growth",
    ],
    "ATTENDANT": [
        "dashboard", "pos", "sales", "customers", "receivables", "inventory",
        "cylinder_deposits", "staff_schedule", "booked_orders", "incident_reports",
        "eod_report",
    ],
    "DISPATCHER": [
        "dashboard", "inventory", "transfers", "staff_schedule", "incident_reports",
        "commercial_deliveries", "stock_import",
    ],
    "ORDER_TAKER": [
        "dashboard", "booked_orders", "customers", "incident_reports",
        "transfers", "stock_import",
    ],
    "SUPERVISOR": [
        "dashboard", "sales", "inventory", "stock_import", "staff_schedule", "incident_reports",
    ],
}


def migrate_schema():
    """Safe, idempotent migrations for databases created before this update.
    Never drops or overwrites existing data. Runs on every boot; each step is
    a no-op if already applied."""
    conn = get_db()

    # 1. New nullable columns — plain ALTER TABLE, always safe.
    if not _column_exists(conn, "products", "deposit_price"):
        conn.execute("ALTER TABLE products ADD COLUMN deposit_price REAL NOT NULL DEFAULT 0")
    if not _column_exists(conn, "products", "price_per_kg"):
        conn.execute("ALTER TABLE products ADD COLUMN price_per_kg REAL NOT NULL DEFAULT 0")
    if not _column_exists(conn, "sale_items", "sale_mode"):
        conn.execute("ALTER TABLE sale_items ADD COLUMN sale_mode TEXT NOT NULL DEFAULT 'REFILL'")
    if not _column_exists(conn, "commercial_deliveries", "billing_type"):
        conn.execute("ALTER TABLE commercial_deliveries ADD COLUMN billing_type TEXT NOT NULL DEFAULT 'WIWO'")
    if not _column_exists(conn, "commercial_deliveries", "meter_label"):
        conn.execute("ALTER TABLE commercial_deliveries ADD COLUMN meter_label TEXT")
    if not _column_exists(conn, "commercial_deliveries", "previous_reading"):
        conn.execute("ALTER TABLE commercial_deliveries ADD COLUMN previous_reading REAL")
    if not _column_exists(conn, "commercial_deliveries", "current_reading"):
        conn.execute("ALTER TABLE commercial_deliveries ADD COLUMN current_reading REAL")
    conn.commit()

    # 2. users.role needs to become a free-text field (no CHECK) so custom role
    #    names can be assigned. SQLite can't drop a CHECK constraint directly,
    #    so recreate the table and copy the data across.
    if not _users_table_has_open_role(conn):
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("""
            CREATE TABLE users_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                location_id INTEGER REFERENCES locations(id),
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            INSERT INTO users_new (id, username, password_hash, name, role, location_id, active, created_at)
            SELECT id, username, password_hash, name, role, location_id, active, created_at FROM users
        """)
        conn.execute("DROP TABLE users")
        conn.execute("ALTER TABLE users_new RENAME TO users")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

    # 3. Seed the default customer categories once, if the table is empty.
    if conn.execute("SELECT COUNT(*) c FROM customer_categories").fetchone()["c"] == 0:
        for name in ["ERTO", "MRTO", "Commercial", "DOMCOM", "Solus", "ETC"]:
            conn.execute("INSERT OR IGNORE INTO customer_categories (name) VALUES (?)", (name,))
        conn.commit()

    # 4. Seed the 5 built-in roles and their default module permissions, once.
    if conn.execute("SELECT COUNT(*) c FROM roles").fetchone()["c"] == 0:
        for role_name in DEFAULT_ROLE_PERMISSIONS:
            conn.execute("INSERT OR IGNORE INTO roles (name, is_builtin) VALUES (?, 1)", (role_name,))
        for role_name, modules in DEFAULT_ROLE_PERMISSIONS.items():
            for m in modules:
                conn.execute("INSERT OR IGNORE INTO role_permissions (role_name, module_key) VALUES (?,?)",
                             (role_name, m))
        conn.commit()

    # 5. Backfill hauling_schedule_items from any pre-existing single-product
    #    hauling rows (the table now supports multiple products per trip).
    #    Only runs once — skipped if hauling_schedule_items already has rows.
    if conn.execute("SELECT COUNT(*) c FROM hauling_schedule_items").fetchone()["c"] == 0:
        old_rows = conn.execute(
            "SELECT id, product_id, qty FROM hauling_schedule WHERE product_id IS NOT NULL AND qty IS NOT NULL"
        ).fetchall()
        for r in old_rows:
            conn.execute("INSERT INTO hauling_schedule_items (hauling_id, product_id, qty) VALUES (?,?,?)",
                         (r["id"], r["product_id"], r["qty"]))
        conn.commit()

    # 6. booked_orders.showroom_location_id needs to become nullable (a
    #    customer-submitted order has no location until an Order Taker
    #    assigns one), and needs a `source` column. SQLite can't relax a
    #    NOT NULL constraint directly, so recreate the table and copy data.
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='booked_orders'").fetchone()
    if row and "showroom_location_id INTEGER NOT NULL" in row[0]:
        conn.execute("PRAGMA foreign_keys = OFF")
        has_source = _column_exists(conn, "booked_orders", "source")
        conn.execute("""
            CREATE TABLE booked_orders_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no TEXT UNIQUE NOT NULL,
                order_date TEXT DEFAULT (datetime('now')),
                customer_id INTEGER REFERENCES customers(id),
                customer_name_freeform TEXT,
                customer_phone TEXT,
                showroom_location_id INTEGER REFERENCES locations(id),
                source TEXT NOT NULL DEFAULT 'STAFF' CHECK(source IN ('STAFF','CUSTOMER')),
                status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','FULFILLED','CANCELLED')),
                notes TEXT,
                created_by INTEGER REFERENCES users(id),
                fulfilled_by INTEGER REFERENCES users(id),
                fulfilled_at TEXT
            )
        """)
        source_col = "source" if has_source else "'STAFF'"
        conn.execute(f"""
            INSERT INTO booked_orders_new (id, order_no, order_date, customer_id, customer_name_freeform,
                customer_phone, showroom_location_id, source, status, notes, created_by, fulfilled_by, fulfilled_at)
            SELECT id, order_no, order_date, customer_id, customer_name_freeform,
                customer_phone, showroom_location_id, {source_col}, status, notes, created_by, fulfilled_by, fulfilled_at
            FROM booked_orders
        """)
        conn.execute("DROP TABLE booked_orders")
        conn.execute("ALTER TABLE booked_orders_new RENAME TO booked_orders")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
    elif not _column_exists(conn, "booked_orders", "source"):
        conn.execute("ALTER TABLE booked_orders ADD COLUMN source TEXT NOT NULL DEFAULT 'STAFF'")
        conn.commit()

    # 7. Delivery address on booked orders — simple additive column.
    if not _column_exists(conn, "booked_orders", "delivery_address"):
        conn.execute("ALTER TABLE booked_orders ADD COLUMN delivery_address TEXT")
        conn.commit()

    # 8. Grant the new commercial_deliveries module to Manager/Dispatcher by
    #    default, for databases that were already seeded before this module
    #    existed. Runs exactly once (tracked in schema_migrations) so it
    #    never fights a user who later removes this permission on purpose.
    #    Custom (non-built-in) roles are never touched.
    MIGRATION_KEY = "grant_commercial_deliveries_default"
    if not conn.execute("SELECT 1 FROM schema_migrations WHERE migration_key=?", (MIGRATION_KEY,)).fetchone():
        for role_name in ("MANAGER", "DISPATCHER"):
            if conn.execute("SELECT 1 FROM roles WHERE name=?", (role_name,)).fetchone():
                conn.execute("INSERT OR IGNORE INTO role_permissions (role_name, module_key) VALUES (?, 'commercial_deliveries')",
                             (role_name,))
        conn.execute("INSERT INTO schema_migrations (migration_key) VALUES (?)", (MIGRATION_KEY,))
        conn.commit()

    # 9. Columns for: empty-cylinder dispatch, AR-collection tagging on
    #    payments (End of Day report), and previous-arrears opening balances.
    if not _column_exists(conn, "transfer_items", "cylinder_state"):
        conn.execute("ALTER TABLE transfer_items ADD COLUMN cylinder_state TEXT NOT NULL DEFAULT 'FULL'")
    if not _column_exists(conn, "payments", "payment_source"):
        conn.execute("ALTER TABLE payments ADD COLUMN payment_source TEXT NOT NULL DEFAULT 'SALE'")
        # Receivable payments were always saved with an "AR-<invoice>"
        # reference unless the user typed their own — best-effort backfill.
        conn.execute("UPDATE payments SET payment_source='AR_COLLECTION' WHERE reference LIKE 'AR-%'")
    if not _column_exists(conn, "sales", "is_opening_balance"):
        conn.execute("ALTER TABLE sales ADD COLUMN is_opening_balance INTEGER NOT NULL DEFAULT 0")
    conn.commit()

    # 9b. Per-item discounts/promos on sale lines.
    if not _column_exists(conn, "sale_items", "discount_id"):
        conn.execute("ALTER TABLE sale_items ADD COLUMN discount_id INTEGER")
    if not _column_exists(conn, "sale_items", "discount_name"):
        conn.execute("ALTER TABLE sale_items ADD COLUMN discount_name TEXT")
    if not _column_exists(conn, "sale_items", "discount_amount"):
        conn.execute("ALTER TABLE sale_items ADD COLUMN discount_amount REAL NOT NULL DEFAULT 0")
    conn.commit()

    # 9c. Brand swaps: customer hands over an empty of another LPG brand and
    #     pays a swap fee. Record which brand came in and the fee per cylinder.
    if not _column_exists(conn, "sale_items", "swap_brand"):
        conn.execute("ALTER TABLE sale_items ADD COLUMN swap_brand TEXT")
    if not _column_exists(conn, "sale_items", "swap_fee"):
        conn.execute("ALTER TABLE sale_items ADD COLUMN swap_fee REAL NOT NULL DEFAULT 0")
    conn.commit()

    # 9e. Dispatch of swapped (other-brand) empties: which brand each line is.
    if not _column_exists(conn, "transfer_items", "swap_brand"):
        conn.execute("ALTER TABLE transfer_items ADD COLUMN swap_brand TEXT")
        conn.commit()

    # 9d. sale_items.sale_mode CHECK must allow 'SWAP'. SQLite can't alter a
    #     CHECK constraint, so recreate the table and copy the data across.
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sale_items'").fetchone()
    if row and "CHECK(sale_mode IN ('REFILL','DEPOSIT'))" in row[0]:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("""
            CREATE TABLE sale_items_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sale_id INTEGER NOT NULL REFERENCES sales(id),
                product_id INTEGER NOT NULL REFERENCES products(id),
                product_name TEXT NOT NULL,
                qty INTEGER NOT NULL,
                price REAL NOT NULL,
                subtotal REAL NOT NULL,
                sale_mode TEXT NOT NULL DEFAULT 'REFILL' CHECK(sale_mode IN ('REFILL','DEPOSIT','SWAP')),
                discount_id INTEGER,
                discount_name TEXT,
                discount_amount REAL NOT NULL DEFAULT 0,
                swap_brand TEXT,
                swap_fee REAL NOT NULL DEFAULT 0
            )
        """)
        cols = "id, sale_id, product_id, product_name, qty, price, subtotal, sale_mode, discount_id, discount_name, discount_amount, swap_brand, swap_fee"
        conn.execute(f"INSERT INTO sale_items_new ({cols}) SELECT {cols} FROM sale_items")
        conn.execute("DROP TABLE sale_items")
        conn.execute("ALTER TABLE sale_items_new RENAME TO sale_items")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

    # 10. transfers.to_location becomes nullable so empties can be dispatched
    #     to a plant (outside the system) — recreate table, copy data.
    if not _column_exists(conn, "transfers", "to_external"):
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("""
            CREATE TABLE transfers_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transfer_no TEXT UNIQUE NOT NULL,
                ts TEXT DEFAULT (datetime('now')),
                from_location INTEGER NOT NULL REFERENCES locations(id),
                to_location INTEGER REFERENCES locations(id),
                to_external TEXT,
                status TEXT NOT NULL DEFAULT 'RELEASED' CHECK(status IN ('RELEASED','RECEIVED','CANCELLED')),
                user_id INTEGER REFERENCES users(id),
                received_by INTEGER REFERENCES users(id),
                received_ts TEXT
            )
        """)
        conn.execute("""
            INSERT INTO transfers_new (id, transfer_no, ts, from_location, to_location, status, user_id, received_by, received_ts)
            SELECT id, transfer_no, ts, from_location, to_location, status, user_id, received_by, received_ts FROM transfers
        """)
        conn.execute("DROP TABLE transfers")
        conn.execute("ALTER TABLE transfers_new RENAME TO transfers")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

    # 11. New built-in ACCOUNTING role, and the End of Day report module for
    #     the roles that need it. Runs once; never touches custom roles.
    MIGRATION_KEY = "accounting_role_and_eod_report"
    if not conn.execute("SELECT 1 FROM schema_migrations WHERE migration_key=?", (MIGRATION_KEY,)).fetchone():
        conn.execute("INSERT OR IGNORE INTO roles (name, is_builtin) VALUES ('ACCOUNTING', 1)")
        for m in DEFAULT_ROLE_PERMISSIONS["ACCOUNTING"]:
            conn.execute("INSERT OR IGNORE INTO role_permissions (role_name, module_key) VALUES ('ACCOUNTING', ?)", (m,))
        for role_name in ("MANAGER", "ATTENDANT"):
            if conn.execute("SELECT 1 FROM roles WHERE name=?", (role_name,)).fetchone():
                conn.execute("INSERT OR IGNORE INTO role_permissions (role_name, module_key) VALUES (?, 'eod_report')",
                             (role_name,))
        conn.execute("INSERT INTO schema_migrations (migration_key) VALUES (?)", (MIGRATION_KEY,))
        conn.commit()

    # 12. Delivery receipts for credit / terms sales: each gets its own DR
    #     number. Backfill existing credit sales, numbered per day in order.
    if not _column_exists(conn, "sales", "dr_no"):
        conn.execute("ALTER TABLE sales ADD COLUMN dr_no TEXT")
        conn.commit()
    MIGRATION_KEY = "delivery_receipt_numbers"
    if not conn.execute("SELECT 1 FROM schema_migrations WHERE migration_key=?", (MIGRATION_KEY,)).fetchone():
        seq = {}
        for r in conn.execute("""SELECT id, ts FROM sales WHERE sale_type='CREDIT' AND is_opening_balance=0
                                  AND dr_no IS NULL ORDER BY ts, id""").fetchall():
            day = (r[1] or "")[:10].replace("-", "")
            seq[day] = seq.get(day, 0) + 1
            conn.execute("UPDATE sales SET dr_no=? WHERE id=?", (f"DR-{day}-{seq[day]:04d}", r[0]))
        conn.execute("INSERT INTO schema_migrations (migration_key) VALUES (?)", (MIGRATION_KEY,))
        conn.commit()

    # 13. Customer Growth dashboard module: grant it to the built-in Manager
    #     and Accounting roles on databases seeded before it existed. Runs once.
    MIGRATION_KEY = "grant_customer_growth_default"
    if not conn.execute("SELECT 1 FROM schema_migrations WHERE migration_key=?", (MIGRATION_KEY,)).fetchone():
        for role_name in ("MANAGER", "ACCOUNTING"):
            if conn.execute("SELECT 1 FROM roles WHERE name=?", (role_name,)).fetchone():
                conn.execute("INSERT OR IGNORE INTO role_permissions (role_name, module_key) VALUES (?, 'customer_growth')",
                             (role_name,))
        conn.execute("INSERT INTO schema_migrations (migration_key) VALUES (?)", (MIGRATION_KEY,))
        conn.commit()

    # 14. Walk-in customer name typed at checkout, and the 1% creditable
    #     withholding tax the customer deducts: (gross / 1.12) x 1%.
    if not _column_exists(conn, "sales", "walkin_name"):
        conn.execute("ALTER TABLE sales ADD COLUMN walkin_name TEXT")
        conn.commit()
    if not _column_exists(conn, "sales", "withholding_tax"):
        conn.execute("ALTER TABLE sales ADD COLUMN withholding_tax REAL NOT NULL DEFAULT 0")
        conn.commit()

    # 15. New built-in roles OPERATION_HEAD and SUPERVISOR; Import Stock and
    #     Sales Targets modules; Order Taker gets Dispatch + Import Stock.
    #     Runs once; custom roles are never touched.
    MIGRATION_KEY = "operation_head_supervisor_targets"
    if not conn.execute("SELECT 1 FROM schema_migrations WHERE migration_key=?", (MIGRATION_KEY,)).fetchone():
        for role_name in ("OPERATION_HEAD", "SUPERVISOR"):
            conn.execute("INSERT OR IGNORE INTO roles (name, is_builtin) VALUES (?, 1)", (role_name,))
            for m in DEFAULT_ROLE_PERMISSIONS[role_name]:
                conn.execute("INSERT OR IGNORE INTO role_permissions (role_name, module_key) VALUES (?,?)", (role_name, m))
        grants = {"DISPATCHER": ["stock_import"], "ORDER_TAKER": ["transfers", "stock_import"],
                  "MANAGER": ["sales_targets"]}
        for role_name, mods in grants.items():
            if conn.execute("SELECT 1 FROM roles WHERE name=?", (role_name,)).fetchone():
                for m in mods:
                    conn.execute("INSERT OR IGNORE INTO role_permissions (role_name, module_key) VALUES (?,?)", (role_name, m))
        conn.execute("INSERT INTO schema_migrations (migration_key) VALUES (?)", (MIGRATION_KEY,))
        conn.commit()

    # 16. DR (manual) billing for 50kg commercial: new columns, and the
    #     billing_type CHECK must allow 'DR' (recreate the table if needed).
    for col, typ in (("dr_ref", "TEXT"), ("delivered_qty", "INTEGER"), ("kg_per_cylinder", "REAL"),
                     ("total_residual_kg", "REAL")):
        if not _column_exists(conn, "commercial_deliveries", col):
            conn.execute(f"ALTER TABLE commercial_deliveries ADD COLUMN {col} {typ}")
    conn.commit()
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='commercial_deliveries'").fetchone()
    if row and "CHECK" in row[0] and "'DR'" not in row[0]:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("""
            CREATE TABLE commercial_deliveries_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_no TEXT UNIQUE NOT NULL,
                delivery_date TEXT DEFAULT (datetime('now')),
                customer_id INTEGER NOT NULL REFERENCES customers(id),
                location_id INTEGER NOT NULL REFERENCES locations(id),
                product_id INTEGER NOT NULL REFERENCES products(id),
                billing_type TEXT NOT NULL DEFAULT 'WIWO' CHECK(billing_type IN ('WIWO','READING','DR')),
                meter_label TEXT,
                previous_reading REAL,
                current_reading REAL,
                status TEXT NOT NULL DEFAULT 'DELIVERED' CHECK(status IN ('DELIVERED','PICKED_UP','BILLED','CANCELLED')),
                pickup_date TEXT,
                price_per_kg REAL,
                billed_amount REAL,
                sale_id INTEGER REFERENCES sales(id),
                notes TEXT,
                created_by INTEGER REFERENCES users(id),
                collected_by INTEGER REFERENCES users(id),
                billed_by INTEGER REFERENCES users(id),
                dr_ref TEXT,
                delivered_qty INTEGER,
                kg_per_cylinder REAL,
                total_residual_kg REAL
            )
        """)
        cols = ("id, delivery_no, delivery_date, customer_id, location_id, product_id, billing_type, meter_label, "
                "previous_reading, current_reading, status, pickup_date, price_per_kg, billed_amount, sale_id, notes, "
                "created_by, collected_by, billed_by, dr_ref, delivered_qty, kg_per_cylinder, total_residual_kg")
        conn.execute(f"INSERT INTO commercial_deliveries_new ({cols}) SELECT {cols} FROM commercial_deliveries")
        conn.execute("DROP TABLE commercial_deliveries")
        conn.execute("ALTER TABLE commercial_deliveries_new RENAME TO commercial_deliveries")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

    # 18. Commercial accounts: 50kg lines on a POS credit sale are delivered
    #     only (billed later in 50kg Commercial Billing). Flag + the SRP at
    #     the time, for reference.
    if not _column_exists(conn, "sale_items", "bill_later"):
        conn.execute("ALTER TABLE sale_items ADD COLUMN bill_later INTEGER NOT NULL DEFAULT 0")
    if not _column_exists(conn, "sale_items", "srp_price"):
        conn.execute("ALTER TABLE sale_items ADD COLUMN srp_price REAL")
    conn.commit()

    # 17. Supervisor can view and void sales: give the built-in SUPERVISOR
    #     role the Sales module. Runs once.
    MIGRATION_KEY = "supervisor_sales_void"
    if not conn.execute("SELECT 1 FROM schema_migrations WHERE migration_key=?", (MIGRATION_KEY,)).fetchone():
        if conn.execute("SELECT 1 FROM roles WHERE name='SUPERVISOR'").fetchone():
            conn.execute("INSERT OR IGNORE INTO role_permissions (role_name, module_key) VALUES ('SUPERVISOR', 'sales')")
        conn.execute("INSERT INTO schema_migrations (migration_key) VALUES (?)", (MIGRATION_KEY,))
        conn.commit()

    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _track_for_request(conn)
    return conn


def _track_for_request(conn):
    """Remember connections opened while handling a web request so the app can
    close them when the request ends. A route that hits an unexpected error
    before its own conn.close() would otherwise leave its transaction open and
    hold SQLite's write lock ("database is locked" on the next save)."""
    try:
        from flask import g, has_app_context
    except ImportError:
        return
    if has_app_context():
        g.setdefault("_db_conns", []).append(conn)


def close_request_connections():
    from flask import g
    for conn in g.pop("_db_conns", []):
        try:
            conn.rollback()  # no-op if the route already committed / closed
            conn.close()
        except Exception:
            pass


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    migrate_schema()
