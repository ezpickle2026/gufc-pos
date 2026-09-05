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
    role TEXT NOT NULL CHECK(role IN ('ADMIN','MANAGER','ATTENDANT','DISPATCHER')),
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
"""


def _column_exists(conn, table, column):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return column in cols


def _users_table_has_manager_role(conn):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    return bool(row) and "MANAGER" in row[0]


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

    # 2. users.role CHECK constraint needs MANAGER added. SQLite can't ALTER a
    #    CHECK constraint directly, so recreate the table and copy the data.
    if not _users_table_has_manager_role(conn):
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("""
            CREATE TABLE users_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('ADMIN','MANAGER','ATTENDANT','DISPATCHER')),
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
