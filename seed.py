"""Seed the database with GUFC demo data. Safe to re-run (skips if data exists)."""
from werkzeug.security import generate_password_hash
from db import get_db, init_db


def run():
    init_db()
    conn = get_db()

    if conn.execute("SELECT COUNT(*) c FROM locations").fetchone()["c"] > 0:
        print("Already seeded. Skipping.")
        conn.close()
        return

    loc = {}
    for name, typ, addr in [
        ("Madamba Warehouse", "WAREHOUSE", "Madamba St."),
        ("San Mateo Warehouse", "WAREHOUSE", "San Mateo, Rizal"),
        ("Soliven Showroom", "SHOWROOM", "Soliven Ave."),
    ]:
        cur = conn.execute("INSERT INTO locations (name, type, address) VALUES (?,?,?)", (name, typ, addr))
        loc[name] = cur.lastrowid

    prod = {}
    for sku, name, cat, price, cost, minstock in [
        ("LPG-11KG", "Solane LPG 11kg", "LPG Cylinder", 1150.00, 950.00, 50),
        ("LPG-1.4KG", "Solane Sakto 1.4kg", "LPG Cylinder", 220.00, 170.00, 30),
        ("LPG-50KG", "Solane LPG 50kg", "LPG Cylinder", 4800.00, 4200.00, 10),
    ]:
        cur = conn.execute(
            "INSERT INTO products (sku, name, category, unit, price, cost, min_stock) VALUES (?,?,?,?,?,?,?)",
            (sku, name, cat, "cylinder", price, cost, minstock),
        )
        prod[sku] = cur.lastrowid

    stock = {
        "Madamba Warehouse": {"LPG-11KG": 500, "LPG-1.4KG": 200, "LPG-50KG": 60},
        "San Mateo Warehouse": {"LPG-11KG": 300, "LPG-1.4KG": 150, "LPG-50KG": 40},
        "Soliven Showroom": {"LPG-11KG": 75, "LPG-1.4KG": 40, "LPG-50KG": 8},
    }
    for lname, items in stock.items():
        for sku, qty in items.items():
            conn.execute("INSERT INTO inventory (location_id, product_id, quantity) VALUES (?,?,?)",
                         (loc[lname], prod[sku], qty))
            conn.execute(
                "INSERT INTO inventory_movements (location_id, product_id, qty_change, type, reference, notes) VALUES (?,?,?,?,?,?)",
                (loc[lname], prod[sku], qty, "INBOUND", "SEED-INITIAL", "Initial seed stock"),
            )

    users = [
        ("admin", "ChangeMe123!", "System Administrator", "ADMIN", None),
        ("jsoliven", "Attendant123!", "Juana Soliven", "ATTENDANT", loc["Soliven Showroom"]),
        ("mmadamba", "Attendant123!", "Mario Madamba", "ATTENDANT", loc["Madamba Warehouse"]),
        ("pdispatch", "Dispatch123!", "Pedro Reyes", "DISPATCHER", loc["Madamba Warehouse"]),
    ]
    for uname, pw, name, role, lid in users:
        conn.execute(
            "INSERT INTO users (username, password_hash, name, role, location_id) VALUES (?,?,?,?,?)",
            (uname, generate_password_hash(pw), name, role, lid),
        )

    customers = [
        ("Juan Dela Cruz", "", "0917-000-0001", "ERTO", 0, 0),
        ("ABC Restaurant", "ABC Restaurant Corp.", "0917-000-0002", "Commercial", 30, 50000),
        ("XYZ Eatery", "XYZ Eatery Inc.", "0917-000-0003", "Commercial", 15, 20000),
        ("Maria Santos (Retail)", "", "0917-000-0004", "Solus", 0, 0),
        ("Green Homes Condo (DOMCOM)", "Green Homes Property Mgmt.", "0917-000-0005", "DOMCOM", 30, 30000),
    ]
    for i, (name, biz, phone, cat, terms, limit) in enumerate(customers, start=1):
        conn.execute(
            "INSERT INTO customers (account_no, name, business_name, phone, category, terms_days, credit_limit) VALUES (?,?,?,?,?,?,?)",
            (f"ACC-{i:05d}", name, biz, phone, cat, terms, limit),
        )

    conn.commit()
    conn.close()
    print("Seed complete.")
    print("Login  admin / ChangeMe123!  (change this immediately)")
    print("Login  jsoliven / Attendant123!  (Soliven Showroom attendant)")
    print("Login  mmadamba / Attendant123!  (Madamba Warehouse attendant)")
    print("Login  pdispatch / Dispatch123!  (dispatcher)")


if __name__ == "__main__":
    run()
