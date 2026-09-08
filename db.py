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
    notes TEXT
);

CREATE TABLE IF NOT EXISTS sale_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    product_name TEXT NOT NULL,
    qty INTEGER NOT NULL,
    price REAL NOT NULL,
    subtotal REAL NOT NULL,
    sale_mode TEXT NOT NULL DEFAULT 'REFILL' CHECK(sale_mode IN ('REFILL','DEPOSIT'))
);

CREATE TABLE IF NOT EXISTS empty_cylinder_inventory (
    location_id INTEGER NOT NULL REFERENCES locations(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (location_id, product_id)
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
    user_id INTEGER REFERENCES users(id)
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
    to_location INTEGER NOT NULL REFERENCES locations(id),
    status TEXT NOT NULL DEFAULT 'RELEASED' CHECK(status IN ('RELEASED','RECEIVED','CANCELLED')),
    user_id INTEGER REFERENCES users(id),
    received_by INTEGER REFERENCES users(id),
    received_ts TEXT
);

CREATE TABLE IF NOT EXISTS transfer_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_id INTEGER NOT NULL REFERENCES transfers(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty INTEGER NOT NULL
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
    "booked_orders", "incident_reports",
]

DEFAULT_ROLE_PERMISSIONS = {
    "ADMIN": MODULE_KEYS,  # ADMIN always has everything regardless of this table
    "MANAGER": [
        "dashboard", "sales", "customers", "receivables", "inventory",
        "inventory_ledger", "cylinder_deposits", "hauling", "vehicles",
        "employees", "staff_schedule", "kpi", "reports", "audit",
        "booked_orders", "incident_reports",
    ],
    "ATTENDANT": [
        "dashboard", "pos", "sales", "customers", "receivables", "inventory",
        "cylinder_deposits", "staff_schedule", "booked_orders", "incident_reports",
    ],
    "DISPATCHER": [
        "dashboard", "inventory", "transfers", "staff_schedule", "incident_reports",
    ],
    "ORDER_TAKER": [
        "dashboard", "booked_orders", "customers", "incident_reports",
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
    if not _column_exists(conn, "sale_items", "sale_mode"):
        conn.execute("ALTER TABLE sale_items ADD COLUMN sale_mode TEXT NOT NULL DEFAULT 'REFILL'")
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

    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    migrate_schema()
