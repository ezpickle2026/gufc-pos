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
    role TEXT NOT NULL CHECK(role IN ('ADMIN','ATTENDANT','DISPATCHER')),
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
    min_stock INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
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
    subtotal REAL NOT NULL
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
"""


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
