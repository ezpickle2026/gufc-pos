import os
import functools
import csv
import io
import sqlite3
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, session, send_from_directory, Response
from werkzeug.security import generate_password_hash, check_password_hash

from db import get_db, init_db
from reports import xlsx_response, pdf_table_response, soa_pdf_response

app = Flask(__name__, static_folder="static", static_url_path="")
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-change-me")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

init_db()

# Auto-seed demo data on first boot. This means you do NOT need Railway's
# Shell/terminal to run seed.py manually — the app seeds itself the first
# time it starts against an empty database, then never touches it again.
_boot_conn = get_db()
if _boot_conn.execute("SELECT COUNT(*) c FROM locations").fetchone()["c"] == 0:
    import seed
    seed.run()
_boot_conn.close()

# ---------------------------------------------------------------- helpers

def audit(conn, action, module, record_id=None, details=None):
    user = session.get("user")
    conn.execute(
        "INSERT INTO audit_logs (user_id, username, action, module, record_id, details) VALUES (?,?,?,?,?,?)",
        (user["id"] if user else None, user["username"] if user else "system",
         action, module, str(record_id) if record_id is not None else None, details),
    )


def next_number(conn, prefix, table, col):
    today = datetime.now().strftime("%Y%m%d")
    like = f"{prefix}-{today}-%"
    row = conn.execute(
        f"SELECT {col} FROM {table} WHERE {col} LIKE ? ORDER BY {col} DESC LIMIT 1", (like,)
    ).fetchone()
    seq = 1
    if row:
        seq = int(row[col].split("-")[-1]) + 1
    return f"{prefix}-{today}-{seq:04d}"


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if "user" not in session:
            return jsonify(error="Not authenticated"), 401
        return fn(*a, **kw)
    return wrapper


def roles_required(*roles):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            if "user" not in session:
                return jsonify(error="Not authenticated"), 401
            if session["user"]["role"] not in roles:
                return jsonify(error="Forbidden: insufficient permissions"), 403
            return fn(*a, **kw)
        return wrapper
    return deco


def location_scope(row_location_id):
    """Non-admin users may only touch their own assigned location."""
    u = session["user"]
    if u["role"] == "ADMIN":
        return True
    return u["location_id"] == row_location_id


# ---------------------------------------------------------------- auth

@app.post("/api/login")
def login():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
    if not row or not check_password_hash(row["password_hash"], password):
        conn.close()
        return jsonify(error="Invalid username or password"), 401
    user = {
        "id": row["id"], "username": row["username"], "name": row["name"],
        "role": row["role"], "location_id": row["location_id"],
    }
    session["user"] = user
    audit(conn, "LOGIN", "AUTH", row["id"])
    conn.commit()
    conn.close()
    return jsonify(user=user)


@app.post("/api/logout")
@login_required
def logout():
    conn = get_db()
    audit(conn, "LOGOUT", "AUTH", session["user"]["id"])
    conn.commit()
    conn.close()
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    return jsonify(user=session.get("user"))


# ---------------------------------------------------------------- locations

@app.get("/api/locations")
@login_required
def list_locations():
    conn = get_db()
    rows = conn.execute("SELECT * FROM locations ORDER BY name").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/locations")
@roles_required("ADMIN")
def create_location():
    d = request.get_json(force=True)
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO locations (name, type, address) VALUES (?,?,?)",
        (d["name"], d["type"], d.get("address", "")),
    )
    audit(conn, "CREATE", "LOCATIONS", cur.lastrowid, d["name"])
    conn.commit()
    conn.close()
    return jsonify(id=cur.lastrowid), 201


@app.put("/api/locations/<int:lid>")
@roles_required("ADMIN")
def update_location(lid):
    d = request.get_json(force=True)
    conn = get_db()
    existing = conn.execute("SELECT id FROM locations WHERE id=?", (lid,)).fetchone()
    if not existing:
        conn.close()
        return jsonify(error="Not found"), 404
    conn.execute(
        "UPDATE locations SET name=?, type=?, address=?, status=? WHERE id=?",
        (d["name"], d["type"], d.get("address", ""), d.get("status", "ACTIVE"), lid),
    )
    audit(conn, "UPDATE", "LOCATIONS", lid, d["name"])
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.delete("/api/locations/<int:lid>")
@roles_required("ADMIN")
def delete_location(lid):
    conn = get_db()
    blockers = []
    sale_count = conn.execute("SELECT COUNT(*) c FROM sales WHERE location_id=?", (lid,)).fetchone()["c"]
    if sale_count:
        blockers.append(f"{sale_count} sale(s)")
    user_count = conn.execute("SELECT COUNT(*) c FROM users WHERE location_id=?", (lid,)).fetchone()["c"]
    if user_count:
        blockers.append(f"{user_count} user(s) assigned")
    inv_count = conn.execute("SELECT COALESCE(SUM(quantity),0) c FROM inventory WHERE location_id=?", (lid,)).fetchone()["c"]
    if inv_count:
        blockers.append(f"{inv_count} unit(s) of inventory")
    if blockers:
        conn.close()
        return jsonify(error=(
            f"Can't delete this location — it still has {', '.join(blockers)} on record. "
            "Set its status to INACTIVE instead (edit the location)."
        )), 400
    try:
        cur = conn.execute("DELETE FROM locations WHERE id=?", (lid,))
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify(error="Can't delete — this location still has related records (users, sales, or inventory) and can't be deleted — set its status to INACTIVE instead."), 400
    if cur.rowcount == 0:
        conn.close()
        return jsonify(error="Not found"), 404
    audit(conn, "DELETE", "LOCATIONS", lid)
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------- products

@app.get("/api/products")
@login_required
def list_products():
    conn = get_db()
    rows = conn.execute("SELECT * FROM products WHERE active=1 ORDER BY name").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/products")
@roles_required("ADMIN")
def create_product():
    d = request.get_json(force=True)
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO products (sku, name, category, unit, price, cost, deposit_price, min_stock) VALUES (?,?,?,?,?,?,?,?)",
            (d["sku"], d["name"], d.get("category", ""), d.get("unit", "cylinder"),
             float(d["price"]), float(d.get("cost", 0)), float(d.get("deposit_price", 0)), int(d.get("min_stock", 0))),
        )
    except Exception as e:
        conn.close()
        return jsonify(error=f"Could not create product: {e}"), 400
    audit(conn, "CREATE", "PRODUCTS", cur.lastrowid, d["name"])
    conn.commit()
    conn.close()
    return jsonify(id=cur.lastrowid), 201


@app.get("/api/products/template")
@roles_required("ADMIN")
def products_csv_template():
    csv_text = "sku,name,category,unit,price,cost,deposit_price,min_stock\nLPG-11KG,Solane LPG 11kg,LPG Cylinder,cylinder,1150,950,1500,50\n"
    return Response(csv_text, mimetype="text/csv",
                     headers={"Content-Disposition": "attachment; filename=products-template.csv"})


@app.post("/api/products/import")
@roles_required("ADMIN")
def products_csv_import():
    file = request.files.get("file")
    if not file:
        return jsonify(error="No file uploaded"), 400
    try:
        text = file.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
    except Exception as e:
        return jsonify(error=f"Could not read CSV: {e}"), 400

    conn = get_db()
    created, errors = 0, []
    for i, row in enumerate(reader, start=2):
        try:
            sku = (row.get("sku") or "").strip()
            name = (row.get("name") or "").strip()
            if not sku or not name:
                raise ValueError("sku and name are required")
            price = float(row.get("price") or 0)
            conn.execute(
                "INSERT INTO products (sku, name, category, unit, price, cost, deposit_price, min_stock) VALUES (?,?,?,?,?,?,?,?)",
                (sku, name, (row.get("category") or "").strip(), (row.get("unit") or "cylinder").strip(),
                 price, float(row.get("cost") or 0), float(row.get("deposit_price") or 0), int(row.get("min_stock") or 0)),
            )
            created += 1
        except Exception as e:
            errors.append(f"Row {i}: {e}")
    if created:
        audit(conn, "IMPORT", "PRODUCTS", None, f"{created} products imported, {len(errors)} errors")
        conn.commit()
    conn.close()
    return jsonify(created=created, errors=errors)


@app.put("/api/products/<int:pid>")
@roles_required("ADMIN")
def update_product(pid):
    d = request.get_json(force=True)
    conn = get_db()
    conn.execute(
        "UPDATE products SET name=?, category=?, unit=?, price=?, cost=?, deposit_price=?, min_stock=? WHERE id=?",
        (d["name"], d.get("category", ""), d.get("unit", "cylinder"), float(d["price"]),
         float(d.get("cost", 0)), float(d.get("deposit_price", 0)), int(d.get("min_stock", 0)), pid),
    )
    audit(conn, "UPDATE", "PRODUCTS", pid, f"price={d['price']} cost={d.get('cost',0)} deposit_price={d.get('deposit_price',0)}")
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------- customers

@app.get("/api/customers")
@login_required
def list_customers():
    q = request.args.get("q", "").strip()
    conn = get_db()
    if q:
        like = f"%{q}%"
        rows = conn.execute(
            "SELECT * FROM customers WHERE name LIKE ? OR business_name LIKE ? OR account_no LIKE ? OR phone LIKE ? ORDER BY name",
            (like, like, like, like),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM customers ORDER BY name").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


def _next_account_no(conn):
    row = conn.execute(
        "SELECT account_no FROM customers WHERE account_no LIKE 'ACC-%' ORDER BY CAST(SUBSTR(account_no,5) AS INTEGER) DESC LIMIT 1"
    ).fetchone()
    n = (int(row["account_no"].split("-")[1]) + 1) if row else 1
    return f"ACC-{n:05d}"


@app.post("/api/customers")
@roles_required("ADMIN")
def create_customer():
    d = request.get_json(force=True)
    if not (d.get("name") or "").strip():
        return jsonify(error="Customer name is required"), 400
    conn = get_db()
    account_no = _next_account_no(conn)
    cur = conn.execute(
        """INSERT INTO customers (account_no, name, business_name, phone, address, category, terms_days, credit_limit)
           VALUES (?,?,?,?,?,?,?,?)""",
        (account_no, d["name"], d.get("business_name", ""), d.get("phone", ""), d.get("address", ""),
         d.get("category", "Solus"), int(d.get("terms_days", 0)), float(d.get("credit_limit", 0))),
    )
    audit(conn, "CREATE", "CUSTOMERS", cur.lastrowid, d["name"])
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return jsonify(id=cid, account_no=account_no), 201


@app.put("/api/customers/<int:cid>")
@roles_required("ADMIN")
def update_customer(cid):
    d = request.get_json(force=True)
    if not (d.get("name") or "").strip():
        return jsonify(error="Customer name is required"), 400
    conn = get_db()
    existing = conn.execute("SELECT id FROM customers WHERE id=?", (cid,)).fetchone()
    if not existing:
        conn.close()
        return jsonify(error="Not found"), 404
    conn.execute(
        """UPDATE customers SET name=?, business_name=?, phone=?, address=?, category=?, terms_days=?, credit_limit=?, status=?
           WHERE id=?""",
        (d["name"], d.get("business_name", ""), d.get("phone", ""), d.get("address", ""),
         d.get("category", "Solus"), int(d.get("terms_days", 0)), float(d.get("credit_limit", 0)),
         d.get("status", "ACTIVE"), cid),
    )
    audit(conn, "UPDATE", "CUSTOMERS", cid, d["name"])
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.delete("/api/customers/<int:cid>")
@roles_required("ADMIN")
def delete_customer(cid):
    conn = get_db()
    sale_count = conn.execute("SELECT COUNT(*) c FROM sales WHERE customer_id=?", (cid,)).fetchone()["c"]
    if sale_count > 0:
        conn.close()
        return jsonify(error=(
            f"This customer has {sale_count} sale(s) on record and can't be deleted, since that would "
            "break the sales/receivables history. Set their status to INACTIVE instead (edit the customer)."
        )), 400
    try:
        cur = conn.execute("DELETE FROM customers WHERE id=?", (cid,))
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify(error="Can't delete — this customer still has related records and can't be deleted — set their status to INACTIVE instead."), 400
    if cur.rowcount == 0:
        conn.close()
        return jsonify(error="Not found"), 404
    audit(conn, "DELETE", "CUSTOMERS", cid)
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.get("/api/customers/template")
@roles_required("ADMIN")
def customers_csv_template():
    csv_text = ("name,business_name,phone,address,category,terms_days,credit_limit\n"
                "Sample Restaurant,Sample Restaurant Corp.,0917-123-4567,123 Main St.,Commercial,30,50000\n")
    return Response(csv_text, mimetype="text/csv",
                     headers={"Content-Disposition": "attachment; filename=customers-template.csv"})


@app.post("/api/customers/import")
@roles_required("ADMIN")
def customers_csv_import():
    file = request.files.get("file")
    if not file:
        return jsonify(error="No file uploaded"), 400
    try:
        text = file.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
    except Exception as e:
        return jsonify(error=f"Could not read CSV: {e}"), 400

    conn = get_db()
    created, errors = 0, []
    for i, row in enumerate(reader, start=2):
        try:
            name = (row.get("name") or "").strip()
            if not name:
                raise ValueError("name is required")
            account_no = _next_account_no(conn)
            conn.execute(
                """INSERT INTO customers (account_no, name, business_name, phone, address, category, terms_days, credit_limit)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (account_no, name, (row.get("business_name") or "").strip(), (row.get("phone") or "").strip(),
                 (row.get("address") or "").strip(), (row.get("category") or "Solus").strip(),
                 int(row.get("terms_days") or 0), float(row.get("credit_limit") or 0)),
            )
            created += 1
        except Exception as e:
            errors.append(f"Row {i}: {e}")
    if created:
        audit(conn, "IMPORT", "CUSTOMERS", None, f"{created} customers imported, {len(errors)} errors")
        conn.commit()
    conn.close()
    return jsonify(created=created, errors=errors)


@app.get("/api/customer-categories")
@login_required
def list_customer_categories():
    conn = get_db()
    rows = conn.execute("SELECT * FROM customer_categories ORDER BY name").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/customer-categories")
@roles_required("ADMIN")
def create_customer_category():
    d = request.get_json(force=True)
    name = (d.get("name") or "").strip()
    if not name:
        return jsonify(error="Category name is required"), 400
    conn = get_db()
    try:
        cur = conn.execute("INSERT INTO customer_categories (name) VALUES (?)", (name,))
    except Exception as e:
        conn.close()
        return jsonify(error=f"Could not create category (may already exist): {e}"), 400
    audit(conn, "CREATE", "CUSTOMER_CATEGORIES", cur.lastrowid, name)
    conn.commit()
    conn.close()
    return jsonify(id=cur.lastrowid), 201


@app.delete("/api/customer-categories/<int:catid>")
@roles_required("ADMIN")
def delete_customer_category(catid):
    conn = get_db()
    cat = conn.execute("SELECT name FROM customer_categories WHERE id=?", (catid,)).fetchone()
    if not cat:
        conn.close()
        return jsonify(error="Not found"), 404
    in_use = conn.execute("SELECT COUNT(*) c FROM customers WHERE category=?", (cat["name"],)).fetchone()["c"]
    if in_use > 0:
        conn.close()
        return jsonify(error=f"{in_use} customer(s) still use this category — reassign them first"), 400
    conn.execute("DELETE FROM customer_categories WHERE id=?", (catid,))
    audit(conn, "DELETE", "CUSTOMER_CATEGORIES", catid, cat["name"])
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.get("/api/customers/<int:cid>")
@login_required
def get_customer(cid):
    conn = get_db()
    cust = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    if not cust:
        conn.close()
        return jsonify(error="Not found"), 404
    recv = conn.execute("SELECT * FROM receivables WHERE customer_id=? ORDER BY invoice_date DESC", (cid,)).fetchall()
    sales = conn.execute("SELECT * FROM sales WHERE customer_id=? ORDER BY ts DESC LIMIT 50", (cid,)).fetchall()
    conn.close()
    return jsonify(customer=dict(cust), receivables=[dict(r) for r in recv], sales=[dict(s) for s in sales])


def build_customer_ledger(conn, cid, date_from=None, date_to=None):
    """Chronological charges (sales) + payments for a customer, with running balance.
    Used for both the on-screen Account Ledger and the printable Statement of Account."""
    sales = conn.execute(
        "SELECT id, sale_no, ts, total, status FROM sales WHERE customer_id=? AND status='COMPLETED' ORDER BY ts",
        (cid,),
    ).fetchall()
    payments = conn.execute(
        """SELECT p.id, p.ts, p.amount, p.method, p.reference, s.sale_no
           FROM payments p JOIN sales s ON s.id = p.sale_id
           WHERE s.customer_id=? ORDER BY p.ts""",
        (cid,),
    ).fetchall()

    events = []
    for s in sales:
        events.append({"date": s["ts"], "type": "SALE", "ref": s["sale_no"], "debit": s["total"], "credit": 0})
    for p in payments:
        events.append({"date": p["ts"], "type": "PAYMENT", "ref": f"{p['sale_no']} ({p['method']})", "debit": 0, "credit": p["amount"]})
    events.sort(key=lambda e: e["date"])

    beginning_balance = 0.0
    in_range_events = []
    for e in events:
        before_range = date_from and e["date"] < date_from
        after_range = date_to and e["date"] > date_to + " 23:59:59"
        if before_range:
            beginning_balance += e["debit"] - e["credit"]
        elif not after_range:
            in_range_events.append(e)

    running = beginning_balance
    out = []
    for e in in_range_events:
        running = round(running + e["debit"] - e["credit"], 2)
        out.append({**e, "balance": running})
    return out, round(beginning_balance, 2), running


@app.get("/api/customers/<int:cid>/ledger")
@login_required
def customer_ledger(cid):
    conn = get_db()
    cust = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    if not cust:
        conn.close()
        return jsonify(error="Not found"), 404
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    entries, beginning, ending = build_customer_ledger(conn, cid, date_from, date_to)
    conn.close()
    return jsonify(customer=dict(cust), entries=entries, beginning_balance=beginning, ending_balance=ending)


@app.get("/api/customers/<int:cid>/statement/export")
@roles_required("ADMIN", "ATTENDANT")
def customer_statement_export(cid):
    conn = get_db()
    cust = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    if not cust:
        conn.close()
        return jsonify(error="Not found"), 404
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    entries, beginning, ending = build_customer_ledger(conn, cid, date_from, date_to)
    conn.close()
    fname = f"SOA-{cust['account_no']}-{datetime.now().strftime('%Y%m%d')}.pdf"
    return soa_pdf_response(dict(cust), entries, beginning, ending, fname)


# ---------------------------------------------------------------- inventory

@app.get("/api/inventory")
@login_required
def get_inventory():
    location_id = request.args.get("location_id", type=int)
    u = session["user"]
    if u["role"] != "ADMIN" and location_id and location_id != u["location_id"]:
        return jsonify(error="Forbidden: not your location"), 403
    if not location_id and u["role"] != "ADMIN":
        location_id = u["location_id"]

    conn = get_db()
    sql = """SELECT p.id product_id, p.sku, p.name, p.min_stock, p.price,
                     l.id location_id, l.name location_name,
                     COALESCE(i.quantity,0) quantity
              FROM products p
              CROSS JOIN locations l
              LEFT JOIN inventory i ON i.product_id=p.id AND i.location_id=l.id
              WHERE p.active=1"""
    params = []
    if location_id:
        sql += " AND l.id=?"
        params.append(location_id)
    sql += " ORDER BY l.name, p.name"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.get("/api/inventory/movements")
@login_required
def inventory_movements():
    product_id = request.args.get("product_id", type=int)
    location_id = request.args.get("location_id", type=int)
    mtype = request.args.get("type")
    conn = get_db()
    sql = "SELECT m.*, p.name product_name, l.name location_name FROM inventory_movements m " \
          "JOIN products p ON p.id=m.product_id JOIN locations l ON l.id=m.location_id WHERE 1=1"
    params = []
    if product_id:
        sql += " AND m.product_id=?"; params.append(product_id)
    if location_id:
        sql += " AND m.location_id=?"; params.append(location_id)
    if mtype:
        sql += " AND m.type=?"; params.append(mtype)
    sql += " ORDER BY m.ts DESC LIMIT 200"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.get("/api/inventory/empties")
@login_required
def get_empty_cylinders():
    u = session["user"]
    location_id = request.args.get("location_id", type=int)
    if u["role"] not in ("ADMIN", "MANAGER") and location_id and location_id != u["location_id"]:
        return jsonify(error="Forbidden: not your location"), 403
    if not location_id and u["role"] not in ("ADMIN", "MANAGER"):
        location_id = u["location_id"]
    conn = get_db()
    sql = """SELECT l.name location_name, l.id location_id, p.name product_name, p.id product_id,
                     COALESCE(e.quantity,0) quantity
              FROM products p CROSS JOIN locations l
              LEFT JOIN empty_cylinder_inventory e ON e.product_id=p.id AND e.location_id=l.id
              WHERE p.active=1"""
    params = []
    if location_id:
        sql += " AND l.id=?"; params.append(location_id)
    sql += " ORDER BY l.name, p.name"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/inventory/inbound")
@roles_required("ADMIN", "DISPATCHER")
def record_inbound_stock():
    """Import stock received from a supplier/plant into a location."""
    d = request.get_json(force=True)
    location_id = d.get("location_id")
    product_id = d.get("product_id")
    qty = d.get("qty")
    supplier = (d.get("supplier") or "").strip()
    reference = (d.get("reference") or "").strip()
    if not location_id or not product_id or not qty:
        return jsonify(error="location_id, product_id and qty are required"), 400
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        return jsonify(error="qty must be a whole number"), 400
    if qty <= 0:
        return jsonify(error="qty must be positive"), 400
    if not supplier:
        return jsonify(error="Supplier name is required"), 400

    conn = get_db()
    ref = reference or f"SUPPLIER-{supplier}"
    adjust_inventory(conn, location_id, product_id, qty, "INBOUND", ref,
                      notes=f"Received from {supplier}" + (f" (ref: {reference})" if reference else ""))
    audit(conn, "INBOUND", "INVENTORY", f"{location_id}:{product_id}", f"+{qty} from {supplier}")
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.get("/api/cylinder-deposits")
@login_required
def list_cylinder_deposits():
    conn = get_db()
    rows = conn.execute(
        """SELECT cd.*, c.name customer_name, c.account_no, p.name product_name
           FROM cylinder_deposits cd JOIN customers c ON c.id=cd.customer_id JOIN products p ON p.id=cd.product_id
           WHERE cd.qty_on_loan > 0 ORDER BY c.name"""
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/cylinder-deposits/return")
@roles_required("ADMIN", "ATTENDANT")
def return_cylinder_deposit():
    """Customer brings back a previously-deposited cylinder (empty)."""
    d = request.get_json(force=True)
    customer_id = d.get("customer_id")
    product_id = d.get("product_id")
    qty = d.get("qty")
    u = session["user"]
    location_id = u["location_id"] if u["role"] != "ADMIN" else d.get("location_id")
    if not customer_id or not product_id or not qty or not location_id:
        return jsonify(error="customer_id, product_id, qty and location_id are required"), 400
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        return jsonify(error="qty must be a whole number"), 400
    if qty <= 0:
        return jsonify(error="qty must be positive"), 400

    conn = get_db()
    row = conn.execute(
        "SELECT qty_on_loan FROM cylinder_deposits WHERE customer_id=? AND product_id=?", (customer_id, product_id)
    ).fetchone()
    on_loan = row["qty_on_loan"] if row else 0
    if qty > on_loan:
        conn.close()
        return jsonify(error=f"Customer only has {on_loan} cylinder(s) on deposit for this product"), 400

    conn.execute("UPDATE cylinder_deposits SET qty_on_loan=qty_on_loan-? WHERE customer_id=? AND product_id=?",
                 (qty, customer_id, product_id))
    add_empty_cylinders(conn, location_id, product_id, qty, notes=f"Cylinder returned by customer {customer_id}")
    audit(conn, "RETURN", "INVENTORY", f"{customer_id}:{product_id}", f"{qty} cylinder(s) returned")
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.post("/api/inventory/adjust")
@roles_required("ADMIN")
def manual_inventory_adjust():
    d = request.get_json(force=True)
    location_id = d.get("location_id")
    product_id = d.get("product_id")
    delta = d.get("delta")
    reason = (d.get("reason") or "").strip()
    allow_negative = bool(d.get("allow_negative", False))
    if not location_id or not product_id or delta is None:
        return jsonify(error="location_id, product_id and delta are required"), 400
    try:
        delta = int(delta)
    except (TypeError, ValueError):
        return jsonify(error="delta must be a whole number (positive to add, negative to remove)"), 400
    if delta == 0:
        return jsonify(error="delta cannot be zero"), 400
    if not reason:
        return jsonify(error="A reason is required for manual inventory adjustments"), 400

    conn = get_db()
    try:
        adjust_inventory(conn, location_id, product_id, delta, "ADJUSTMENT", "MANUAL-ADMIN",
                          notes=reason, allow_negative=allow_negative)
        audit(conn, "ADJUST", "INVENTORY", f"{location_id}:{product_id}",
              f"delta={delta} reason={reason}")
        conn.commit()
    except ValueError as e:
        conn.rollback()
        conn.close()
        return jsonify(error=str(e)), 400
    conn.close()
    return jsonify(ok=True)


def add_empty_cylinders(conn, location_id, product_id, qty, reference=None, notes=None):
    row = conn.execute(
        "SELECT quantity FROM empty_cylinder_inventory WHERE location_id=? AND product_id=?",
        (location_id, product_id),
    ).fetchone()
    if row:
        conn.execute("UPDATE empty_cylinder_inventory SET quantity=quantity+? WHERE location_id=? AND product_id=?",
                     (qty, location_id, product_id))
    else:
        conn.execute("INSERT INTO empty_cylinder_inventory (location_id, product_id, quantity) VALUES (?,?,?)",
                     (location_id, product_id, qty))


def remove_empty_cylinders(conn, location_id, product_id, qty):
    row = conn.execute(
        "SELECT quantity FROM empty_cylinder_inventory WHERE location_id=? AND product_id=?",
        (location_id, product_id),
    ).fetchone()
    current = row["quantity"] if row else 0
    if qty > current:
        raise ValueError(f"Only {current} empty cylinders on hand at this location")
    conn.execute("UPDATE empty_cylinder_inventory SET quantity=quantity-? WHERE location_id=? AND product_id=?",
                 (qty, location_id, product_id))


def record_cylinder_deposit(conn, customer_id, product_id, qty):
    row = conn.execute(
        "SELECT qty_on_loan FROM cylinder_deposits WHERE customer_id=? AND product_id=?",
        (customer_id, product_id),
    ).fetchone()
    if row:
        conn.execute("UPDATE cylinder_deposits SET qty_on_loan=qty_on_loan+? WHERE customer_id=? AND product_id=?",
                     (qty, customer_id, product_id))
    else:
        conn.execute("INSERT INTO cylinder_deposits (customer_id, product_id, qty_on_loan) VALUES (?,?,?)",
                     (customer_id, product_id, qty))


def adjust_inventory(conn, location_id, product_id, delta, mtype, reference, notes=None, allow_negative=False):
    row = conn.execute(
        "SELECT quantity FROM inventory WHERE location_id=? AND product_id=?", (location_id, product_id)
    ).fetchone()
    current = row["quantity"] if row else 0
    new_qty = current + delta
    if new_qty < 0 and not allow_negative:
        raise ValueError("Insufficient inventory")
    if row:
        conn.execute("UPDATE inventory SET quantity=? WHERE location_id=? AND product_id=?",
                     (new_qty, location_id, product_id))
    else:
        conn.execute("INSERT INTO inventory (location_id, product_id, quantity) VALUES (?,?,?)",
                     (location_id, product_id, new_qty))
    conn.execute(
        "INSERT INTO inventory_movements (location_id, product_id, qty_change, type, reference, user_id, notes) VALUES (?,?,?,?,?,?,?)",
        (location_id, product_id, delta, mtype, reference, session["user"]["id"] if session.get("user") else None, notes),
    )


# ---------------------------------------------------------------- POS / sales

@app.post("/api/sales")
@roles_required("ADMIN", "ATTENDANT")
def create_sale():
    d = request.get_json(force=True)
    u = session["user"]
    location_id = u["location_id"] if u["role"] != "ADMIN" else d.get("location_id")
    if not location_id:
        return jsonify(error="No location specified"), 400

    items = d.get("items") or []
    if not items:
        return jsonify(error="Cart is empty"), 400
    payments = d.get("payments") or []
    sale_type = d.get("sale_type", "CASH")
    customer_id = d.get("customer_id")
    discount = float(d.get("discount", 0))

    conn = get_db()
    try:
        subtotal = 0.0
        resolved_items = []
        for it in items:
            prod = conn.execute("SELECT * FROM products WHERE id=?", (it["product_id"],)).fetchone()
            if not prod:
                raise ValueError(f"Unknown product {it['product_id']}")
            qty = int(it["qty"])
            if qty <= 0:
                raise ValueError("Quantity must be positive")
            sale_mode = it.get("sale_mode", "REFILL")
            if sale_mode not in ("REFILL", "DEPOSIT"):
                raise ValueError("sale_mode must be REFILL or DEPOSIT")
            # server-side price, never trust frontend: REFILL is gas only;
            # DEPOSIT adds the cylinder deposit fee (customer keeps the tank).
            price = float(prod["price"])
            if sale_mode == "DEPOSIT":
                price += float(prod["deposit_price"])
            line_total = round(price * qty, 2)
            subtotal += line_total
            resolved_items.append((prod, qty, price, line_total, sale_mode))

        total = round(subtotal - discount, 2)
        if total < 0:
            raise ValueError("Discount exceeds subtotal")

        paid_total = round(sum(float(p["amount"]) for p in payments), 2)

        if sale_type == "CASH" and paid_total < total:
            raise ValueError("Cash sale payments do not cover the total. Use CREDIT for partial payment.")

        # credit sale: check customer terms + credit limit
        customer = None
        if customer_id:
            customer = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if sale_type == "CREDIT":
            if not customer:
                raise ValueError("Credit sale requires a customer with approved terms")
            outstanding = conn.execute(
                "SELECT COALESCE(SUM(balance),0) b FROM receivables WHERE customer_id=? AND status!='PAID'",
                (customer_id,),
            ).fetchone()["b"]
            projected = outstanding + (total - paid_total)
            if customer["credit_limit"] > 0 and projected > customer["credit_limit"]:
                raise ValueError(
                    f"Credit limit exceeded: outstanding+this sale = {projected:.2f}, limit = {customer['credit_limit']:.2f}"
                )

        balance = round(total - paid_total, 2)
        sale_no = next_number(conn, "SALE", "sales", "sale_no")

        cur = conn.execute(
            """INSERT INTO sales (sale_no, location_id, user_id, customer_id, sale_type, subtotal, discount, total, paid, balance, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (sale_no, location_id, u["id"], customer_id, sale_type, subtotal, discount, total,
             paid_total, balance, d.get("notes", "")),
        )
        sale_id = cur.lastrowid

        for prod, qty, price, line_total, sale_mode in resolved_items:
            conn.execute(
                "INSERT INTO sale_items (sale_id, product_id, product_name, qty, price, subtotal, sale_mode) VALUES (?,?,?,?,?,?,?)",
                (sale_id, prod["id"], prod["name"], qty, price, line_total, sale_mode),
            )
            adjust_inventory(conn, location_id, prod["id"], -qty, "SALE", sale_no,
                              notes=f"Sold via {sale_no} ({sale_mode})")
            if sale_mode == "REFILL":
                # Customer exchanges an empty cylinder for a full one — the empty
                # comes back into our stock, ready to be hauled to the plant for refill.
                add_empty_cylinders(conn, location_id, prod["id"], qty,
                                     reference=sale_no, notes=f"Empty returned on refill sale {sale_no}")
            else:
                # DEPOSIT: customer takes a full cylinder with no exchange — no
                # empty comes back, and the cylinder is now outstanding on loan
                # to them (tracked so we know our total cylinders in circulation).
                if customer_id:
                    record_cylinder_deposit(conn, customer_id, prod["id"], qty)

        for p in payments:
            conn.execute(
                "INSERT INTO payments (sale_id, method, amount, reference, user_id) VALUES (?,?,?,?,?)",
                (sale_id, p["method"], float(p["amount"]), p.get("reference", ""), u["id"]),
            )

        if balance > 0.009:
            if sale_type != "CREDIT":
                raise ValueError("Balance remaining but sale is not marked CREDIT")
            terms_days = customer["terms_days"] or 0
            invoice_date = datetime.now().date()
            due_date = invoice_date + timedelta(days=terms_days)
            invoice_no = next_number(conn, "INV", "receivables", "invoice_no")
            conn.execute(
                """INSERT INTO receivables (sale_id, customer_id, invoice_no, invoice_date, due_date, amount, paid, balance, status)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (sale_id, customer_id, invoice_no, str(invoice_date), str(due_date), total, paid_total, balance,
                 "CURRENT"),
            )

        audit(conn, "CREATE", "SALES", sale_id, f"{sale_no} total={total} paid={paid_total} balance={balance}")
        conn.commit()
    except ValueError as e:
        conn.rollback()
        conn.close()
        return jsonify(error=str(e)), 400
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify(error=f"Unable to complete sale: {e}"), 500

    result = get_sale_detail(sale_id)
    conn.close()
    return jsonify(result), 201


def get_sale_detail(sale_id):
    conn = get_db()
    sale = conn.execute(
        """SELECT s.*, l.name location_name, u.name cashier_name, c.name customer_name, c.account_no
           FROM sales s JOIN locations l ON l.id=s.location_id JOIN users u ON u.id=s.user_id
           LEFT JOIN customers c ON c.id=s.customer_id WHERE s.id=?""", (sale_id,)
    ).fetchone()
    items = conn.execute("SELECT * FROM sale_items WHERE sale_id=?", (sale_id,)).fetchall()
    pays = conn.execute("SELECT * FROM payments WHERE sale_id=?", (sale_id,)).fetchall()
    conn.close()
    return {"sale": dict(sale), "items": [dict(i) for i in items], "payments": [dict(p) for p in pays]}


@app.get("/api/sales")
@login_required
def list_sales():
    u = session["user"]
    conn = get_db()
    sql = """SELECT s.*, l.name location_name, u2.name cashier_name, c.name customer_name
              FROM sales s JOIN locations l ON l.id=s.location_id JOIN users u2 ON u2.id=s.user_id
              LEFT JOIN customers c ON c.id=s.customer_id WHERE 1=1"""
    params = []
    if u["role"] != "ADMIN":
        sql += " AND s.location_id=?"
        params.append(u["location_id"])
    sql += " ORDER BY s.ts DESC LIMIT 100"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.get("/api/sales/<int:sale_id>")
@login_required
def get_sale(sale_id):
    return jsonify(get_sale_detail(sale_id))


@app.post("/api/sales/<int:sale_id>/void")
@roles_required("ADMIN")
def void_sale(sale_id):
    conn = get_db()
    sale = conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    if not sale:
        conn.close()
        return jsonify(error="Not found"), 404
    if sale["status"] == "VOIDED":
        conn.close()
        return jsonify(error="Already voided"), 400
    items = conn.execute("SELECT * FROM sale_items WHERE sale_id=?", (sale_id,)).fetchall()
    for it in items:
        adjust_inventory(conn, sale["location_id"], it["product_id"], it["qty"], "ADJUSTMENT",
                          f"VOID-{sale['sale_no']}", notes="Reversal of voided sale", allow_negative=True)
    conn.execute("UPDATE sales SET status='VOIDED' WHERE id=?", (sale_id,))
    conn.execute("UPDATE receivables SET status='PAID', balance=0 WHERE sale_id=? ", (sale_id,))
    audit(conn, "VOID", "SALES", sale_id, f"Voided {sale['sale_no']}")
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------- receivables

def recompute_receivable_status(conn, rid):
    r = conn.execute("SELECT * FROM receivables WHERE id=?", (rid,)).fetchone()
    if not r:
        return
    today = datetime.now().date()
    due = datetime.strptime(r["due_date"], "%Y-%m-%d").date()
    if r["balance"] <= 0.009:
        status = "PAID"
    elif r["paid"] > 0:
        status = "PARTIALLY PAID" if today <= due else "OVERDUE"
    elif today > due:
        status = "OVERDUE"
    elif (due - today).days <= 5:
        status = "DUE SOON"
    else:
        status = "CURRENT"
    conn.execute("UPDATE receivables SET status=? WHERE id=?", (status, rid))


@app.get("/api/receivables")
@login_required
def list_receivables():
    conn = get_db()
    rows = conn.execute("SELECT id FROM receivables").fetchall()
    for r in rows:
        recompute_receivable_status(conn, r["id"])
    conn.commit()
    out = conn.execute(
        """SELECT r.*, c.name customer_name, c.account_no, s.sale_no
           FROM receivables r JOIN customers c ON c.id=r.customer_id JOIN sales s ON s.id=r.sale_id
           ORDER BY r.due_date ASC"""
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in out])


@app.post("/api/receivables/<int:rid>/pay")
@roles_required("ADMIN", "ATTENDANT")
def pay_receivable(rid):
    d = request.get_json(force=True)
    amount = float(d["amount"])
    method = d.get("method", "Cash")
    conn = get_db()
    r = conn.execute("SELECT * FROM receivables WHERE id=?", (rid,)).fetchone()
    if not r:
        conn.close()
        return jsonify(error="Not found"), 404
    if amount <= 0 or amount > r["balance"] + 0.009:
        conn.close()
        return jsonify(error=f"Payment must be > 0 and <= outstanding balance ({r['balance']:.2f})"), 400
    new_paid = round(r["paid"] + amount, 2)
    new_balance = round(r["balance"] - amount, 2)
    conn.execute("UPDATE receivables SET paid=?, balance=? WHERE id=?", (new_paid, new_balance, rid))
    conn.execute(
        "INSERT INTO payments (sale_id, method, amount, reference, user_id) VALUES (?,?,?,?,?)",
        (r["sale_id"], method, amount, d.get("reference", f"AR-{r['invoice_no']}"), session["user"]["id"]),
    )
    conn.execute("UPDATE sales SET paid = paid + ?, balance = balance - ? WHERE id=?",
                 (amount, amount, r["sale_id"]))
    recompute_receivable_status(conn, rid)
    audit(conn, "PAYMENT", "RECEIVABLES", rid, f"{r['invoice_no']} +{amount} via {method}")
    conn.commit()
    conn.close()
    return jsonify(ok=True, paid=new_paid, balance=new_balance)


# ---------------------------------------------------------------- transfers (dispatch-lite)

@app.get("/api/transfers")
@login_required
def list_transfers():
    conn = get_db()
    rows = conn.execute(
        """SELECT t.*, lf.name from_name, lt.name to_name FROM transfers t
           JOIN locations lf ON lf.id=t.from_location JOIN locations lt ON lt.id=t.to_location
           ORDER BY t.ts DESC LIMIT 100"""
    ).fetchall()
    out = []
    for t in rows:
        items = conn.execute(
            "SELECT ti.*, p.name product_name FROM transfer_items ti JOIN products p ON p.id=ti.product_id WHERE transfer_id=?",
            (t["id"],),
        ).fetchall()
        out.append({**dict(t), "items": [dict(i) for i in items]})
    conn.close()
    return jsonify(out)


@app.post("/api/transfers")
@roles_required("ADMIN", "DISPATCHER")
def create_transfer():
    d = request.get_json(force=True)
    items = d.get("items") or []
    if not items:
        return jsonify(error="No items to transfer"), 400
    conn = get_db()
    try:
        transfer_no = next_number(conn, "DISP", "transfers", "transfer_no")
        cur = conn.execute(
            "INSERT INTO transfers (transfer_no, from_location, to_location, status, user_id) VALUES (?,?,?,?,?)",
            (transfer_no, d["from_location"], d["to_location"], "RELEASED", session["user"]["id"]),
        )
        tid = cur.lastrowid
        for it in items:
            qty = int(it["qty"])
            conn.execute("INSERT INTO transfer_items (transfer_id, product_id, qty) VALUES (?,?,?)",
                         (tid, it["product_id"], qty))
            adjust_inventory(conn, d["from_location"], it["product_id"], -qty, "TRANSFER OUT", transfer_no)
        audit(conn, "CREATE", "TRANSFERS", tid, transfer_no)
        conn.commit()
    except ValueError as e:
        conn.rollback(); conn.close()
        return jsonify(error=str(e)), 400
    conn.close()
    return jsonify(id=tid, transfer_no=transfer_no), 201


@app.post("/api/transfers/<int:tid>/receive")
@roles_required("ADMIN", "DISPATCHER")
def receive_transfer(tid):
    conn = get_db()
    t = conn.execute("SELECT * FROM transfers WHERE id=?", (tid,)).fetchone()
    if not t:
        conn.close(); return jsonify(error="Not found"), 404
    if t["status"] != "RELEASED":
        conn.close(); return jsonify(error=f"Transfer already {t['status']}"), 400
    items = conn.execute("SELECT * FROM transfer_items WHERE transfer_id=?", (tid,)).fetchall()
    for it in items:
        adjust_inventory(conn, t["to_location"], it["product_id"], it["qty"], "TRANSFER IN", t["transfer_no"])
    conn.execute("UPDATE transfers SET status='RECEIVED', received_by=?, received_ts=datetime('now') WHERE id=?",
                 (session["user"]["id"], tid))
    audit(conn, "RECEIVE", "TRANSFERS", tid, t["transfer_no"])
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------- dashboard & reports

@app.get("/api/dashboard")
@login_required
def dashboard():
    u = session["user"]
    conn = get_db()
    loc_filter = "" if u["role"] == "ADMIN" else "AND location_id=?"
    params = [] if u["role"] == "ADMIN" else [u["location_id"]]

    today_sales = conn.execute(
        f"SELECT COALESCE(SUM(total),0) t, COUNT(*) c FROM sales WHERE status='COMPLETED' AND date(ts)=date('now') {loc_filter}",
        params,
    ).fetchone()
    outstanding = conn.execute("SELECT COALESCE(SUM(balance),0) b FROM receivables WHERE status!='PAID'").fetchone()
    overdue = conn.execute("SELECT COALESCE(SUM(balance),0) b FROM receivables WHERE status='OVERDUE'").fetchone()
    low_stock = conn.execute(
        """SELECT p.name, l.name location_name, i.quantity, p.min_stock
           FROM inventory i JOIN products p ON p.id=i.product_id JOIN locations l ON l.id=i.location_id
           WHERE i.quantity <= p.min_stock""" + (" AND l.id=?" if u["role"] != "ADMIN" else ""),
        params,
    ).fetchall()
    by_method = conn.execute(
        "SELECT method, COALESCE(SUM(amount),0) total FROM payments GROUP BY method"
    ).fetchall()
    conn.close()
    return jsonify(
        today_sales_total=today_sales["t"], today_sales_count=today_sales["c"],
        outstanding_receivables=outstanding["b"], overdue_receivables=overdue["b"],
        low_stock=[dict(r) for r in low_stock],
        payments_by_method=[dict(r) for r in by_method],
    )


@app.get("/api/reports/sales")
@login_required
def report_sales():
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    rows = _sales_report_rows(date_from, date_to)
    return jsonify([dict(r) for r in rows])


def _sales_report_rows(date_from, date_to):
    conn = get_db()
    sql = """SELECT s.sale_no, s.ts, l.name location_name, u.name cashier, c.name customer,
                    s.sale_type, s.subtotal, s.discount, s.total, s.paid, s.balance, s.status,
                    (SELECT GROUP_CONCAT(si.product_name || ' x' || si.qty ||
                            CASE WHEN si.sale_mode='DEPOSIT' THEN ' (deposit)' ELSE '' END, '; ')
                     FROM sale_items si WHERE si.sale_id = s.id) items_summary
             FROM sales s JOIN locations l ON l.id=s.location_id JOIN users u ON u.id=s.user_id
             LEFT JOIN customers c ON c.id=s.customer_id WHERE 1=1"""
    params = []
    if date_from:
        sql += " AND date(s.ts) >= date(?)"; params.append(date_from)
    if date_to:
        sql += " AND date(s.ts) <= date(?)"; params.append(date_to)
    sql += " ORDER BY s.ts DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


@app.get("/api/reports/sales/export")
@roles_required("ADMIN", "MANAGER")
def export_sales_report():
    fmt = request.args.get("format", "xlsx")
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    rows = _sales_report_rows(date_from, date_to)
    headers = ["Sale #", "Date", "Location", "Cashier", "Customer", "Items Purchased", "Type", "Subtotal", "Discount", "Total", "Paid", "Balance", "Status"]
    data = [(r["sale_no"], r["ts"], r["location_name"], r["cashier"], r["customer"] or "Walk-in",
              r["items_summary"] or "", r["sale_type"], r["subtotal"], r["discount"], r["total"],
              r["paid"], r["balance"], r["status"]) for r in rows]
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    if fmt == "pdf":
        return pdf_table_response("Sales Report", headers, data, f"sales-report-{stamp}.pdf",
                                   subtitle=f"{date_from or 'All dates'} to {date_to or 'present'}",
                                   col_widths=[70, 65, 60, 55, 60, 110, 35, 45, 40, 45, 40, 45, 45])
    return xlsx_response(headers, data, f"sales-report-{stamp}.xlsx", "Sales Report")


@app.get("/api/reports/inventory")
@login_required
def report_inventory():
    conn = get_db()
    rows = conn.execute(
        """SELECT l.name location_name, p.name product_name, p.sku, i.quantity, p.min_stock
           FROM inventory i JOIN products p ON p.id=i.product_id JOIN locations l ON l.id=i.location_id
           ORDER BY l.name, p.name"""
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.get("/api/reports/inventory/export")
@roles_required("ADMIN", "MANAGER")
def export_inventory_report():
    fmt = request.args.get("format", "xlsx")
    conn = get_db()
    rows = conn.execute(
        """SELECT l.name location_name, p.sku, p.name product_name, i.quantity, p.min_stock
           FROM inventory i JOIN products p ON p.id=i.product_id JOIN locations l ON l.id=i.location_id
           ORDER BY l.name, p.name"""
    ).fetchall()
    conn.close()
    headers = ["Location", "SKU", "Product", "Qty on Hand", "Min Stock", "Status"]
    data = [(r["location_name"], r["sku"], r["product_name"], r["quantity"], r["min_stock"],
              "LOW STOCK" if r["quantity"] <= r["min_stock"] else "OK") for r in rows]
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    if fmt == "pdf":
        return pdf_table_response("Inventory Report", headers, data, f"inventory-report-{stamp}.pdf")
    return xlsx_response(headers, data, f"inventory-report-{stamp}.xlsx", "Inventory Report")


@app.get("/api/reports/receivables/export")
@roles_required("ADMIN", "MANAGER")
def export_receivables_report():
    fmt = request.args.get("format", "xlsx")
    conn = get_db()
    rows = conn.execute("SELECT id FROM receivables").fetchall()
    for r in rows:
        recompute_receivable_status(conn, r["id"])
    conn.commit()
    out = conn.execute(
        """SELECT r.invoice_no, c.name customer_name, c.account_no, s.sale_no, r.invoice_date, r.due_date,
                  r.amount, r.paid, r.balance, r.status
           FROM receivables r JOIN customers c ON c.id=r.customer_id JOIN sales s ON s.id=r.sale_id
           ORDER BY r.due_date ASC"""
    ).fetchall()
    conn.close()
    headers = ["Invoice #", "Customer", "Account #", "Sale #", "Invoice Date", "Due Date", "Amount", "Paid", "Balance", "Status"]
    data = [(r["invoice_no"], r["customer_name"], r["account_no"], r["sale_no"], r["invoice_date"], r["due_date"],
              r["amount"], r["paid"], r["balance"], r["status"]) for r in out]
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    if fmt == "pdf":
        return pdf_table_response("Accounts Receivable", headers, data, f"receivables-{stamp}.pdf")
    return xlsx_response(headers, data, f"receivables-{stamp}.xlsx", "Receivables")


# ---------------------------------------------------------------- users (admin)

@app.get("/api/users")
@roles_required("ADMIN", "MANAGER")
def list_users():
    conn = get_db()
    rows = conn.execute(
        "SELECT u.id, u.username, u.name, u.role, u.active, u.location_id, l.name location_name FROM users u "
        "LEFT JOIN locations l ON l.id=u.location_id ORDER BY u.name"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/users")
@roles_required("ADMIN")
def create_user():
    d = request.get_json(force=True)
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, name, role, location_id) VALUES (?,?,?,?,?)",
            (d["username"].strip().lower(), generate_password_hash(d["password"]), d["name"], d["role"],
             d.get("location_id")),
        )
    except Exception as e:
        conn.close()
        return jsonify(error=f"Could not create user: {e}"), 400
    audit(conn, "CREATE", "USERS", cur.lastrowid, d["username"])
    conn.commit()
    conn.close()
    return jsonify(id=cur.lastrowid), 201


@app.put("/api/users/<int:uid>")
@roles_required("ADMIN")
def update_user(uid):
    d = request.get_json(force=True)
    conn = get_db()
    existing = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not existing:
        conn.close()
        return jsonify(error="Not found"), 404
    new_password = d.get("password")
    if new_password:
        conn.execute(
            "UPDATE users SET name=?, role=?, location_id=?, password_hash=? WHERE id=?",
            (d.get("name", existing["name"]), d.get("role", existing["role"]),
             d.get("location_id"), generate_password_hash(new_password), uid),
        )
        audit(conn, "UPDATE", "USERS", uid, "details + password reset")
    else:
        conn.execute(
            "UPDATE users SET name=?, role=?, location_id=? WHERE id=?",
            (d.get("name", existing["name"]), d.get("role", existing["role"]), d.get("location_id"), uid),
        )
        audit(conn, "UPDATE", "USERS", uid, "details updated")
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.post("/api/users/<int:uid>/disable")
@roles_required("ADMIN")
def disable_user(uid):
    conn = get_db()
    conn.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
    audit(conn, "DISABLE", "USERS", uid)
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.post("/api/users/<int:uid>/enable")
@roles_required("ADMIN")
def enable_user(uid):
    conn = get_db()
    conn.execute("UPDATE users SET active=1 WHERE id=?", (uid,))
    audit(conn, "ENABLE", "USERS", uid)
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.delete("/api/users/<int:uid>")
@roles_required("ADMIN")
def delete_user(uid):
    if uid == session["user"]["id"]:
        return jsonify(error="You can't delete your own account while logged in as it"), 400
    conn = get_db()
    sale_count = conn.execute("SELECT COUNT(*) c FROM sales WHERE user_id=?", (uid,)).fetchone()["c"]
    if sale_count > 0:
        conn.close()
        return jsonify(error=(
            f"This user has {sale_count} sale(s) on record and can't be deleted, since that would "
            "break the sales history. Disable the account instead."
        )), 400
    try:
        cur = conn.execute("DELETE FROM users WHERE id=?", (uid,))
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify(error=(
            "This user has other activity on record (audit log entries, payments, inventory movements, "
            "or similar) and can't be permanently deleted — disable the account instead."
        )), 400
    if cur.rowcount == 0:
        conn.close()
        return jsonify(error="Not found"), 404
    audit(conn, "DELETE", "USERS", uid)
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------- audit log

@app.get("/api/audit-logs")
@roles_required("ADMIN", "MANAGER")
def get_audit_logs():
    module = request.args.get("module")  # SALES, INVENTORY, or omitted for all
    conn = get_db()
    if module:
        rows = conn.execute("SELECT * FROM audit_logs WHERE module=? ORDER BY ts DESC LIMIT 300", (module,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM audit_logs ORDER BY ts DESC LIMIT 300").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------- vehicles (Admin + Manager)

@app.get("/api/vehicles")
@roles_required("ADMIN", "MANAGER")
def list_vehicles():
    conn = get_db()
    rows = conn.execute("SELECT * FROM vehicles ORDER BY plate_number").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/vehicles")
@roles_required("ADMIN", "MANAGER")
def create_vehicle():
    d = request.get_json(force=True)
    if not (d.get("plate_number") or "").strip():
        return jsonify(error="Plate number is required"), 400
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO vehicles (plate_number, vehicle_type, description, driver, status, odometer,
                   last_maintenance_date, next_maintenance_date, registration_expiry, insurance_expiry, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (d["plate_number"].strip(), d.get("vehicle_type", ""), d.get("description", ""), d.get("driver", ""),
             d.get("status", "AVAILABLE"), int(d.get("odometer", 0)), d.get("last_maintenance_date") or None,
             d.get("next_maintenance_date") or None, d.get("registration_expiry") or None,
             d.get("insurance_expiry") or None, d.get("notes", "")),
        )
    except Exception as e:
        conn.close()
        return jsonify(error=f"Could not create vehicle: {e}"), 400
    audit(conn, "CREATE", "VEHICLES", cur.lastrowid, d["plate_number"])
    conn.commit()
    conn.close()
    return jsonify(id=cur.lastrowid), 201


@app.put("/api/vehicles/<int:vid>")
@roles_required("ADMIN", "MANAGER")
def update_vehicle(vid):
    d = request.get_json(force=True)
    conn = get_db()
    existing = conn.execute("SELECT id FROM vehicles WHERE id=?", (vid,)).fetchone()
    if not existing:
        conn.close()
        return jsonify(error="Not found"), 404
    conn.execute(
        """UPDATE vehicles SET vehicle_type=?, description=?, driver=?, status=?, odometer=?,
               last_maintenance_date=?, next_maintenance_date=?, registration_expiry=?, insurance_expiry=?, notes=?
           WHERE id=?""",
        (d.get("vehicle_type", ""), d.get("description", ""), d.get("driver", ""), d.get("status", "AVAILABLE"),
         int(d.get("odometer", 0)), d.get("last_maintenance_date") or None, d.get("next_maintenance_date") or None,
         d.get("registration_expiry") or None, d.get("insurance_expiry") or None, d.get("notes", ""), vid),
    )
    audit(conn, "UPDATE", "VEHICLES", vid)
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.delete("/api/vehicles/<int:vid>")
@roles_required("ADMIN", "MANAGER")
def delete_vehicle(vid):
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM vehicles WHERE id=?", (vid,))
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify(error="Can't delete — this vehicle is referenced by a hauling schedule entry and can't be deleted — remove those first."), 400
    if cur.rowcount == 0:
        conn.close()
        return jsonify(error="Not found"), 404
    audit(conn, "DELETE", "VEHICLES", vid)
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------- hauling schedule (Admin + Manager)

@app.get("/api/hauling")
@roles_required("ADMIN", "MANAGER")
def list_hauling():
    conn = get_db()
    rows = conn.execute(
        """SELECT h.*, v.plate_number, l.name destination_name, p.name product_name
           FROM hauling_schedule h LEFT JOIN vehicles v ON v.id=h.vehicle_id
           LEFT JOIN locations l ON l.id=h.destination_location_id LEFT JOIN products p ON p.id=h.product_id
           ORDER BY h.scheduled_date DESC"""
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/hauling")
@roles_required("ADMIN", "MANAGER")
def create_hauling():
    d = request.get_json(force=True)
    if not d.get("scheduled_date") or not (d.get("plant_name") or "").strip():
        return jsonify(error="scheduled_date and plant_name are required"), 400
    conn = get_db()
    hauling_no = next_number(conn, "HAUL", "hauling_schedule", "hauling_no")
    cur = conn.execute(
        """INSERT INTO hauling_schedule (hauling_no, scheduled_date, vehicle_id, driver, plant_name,
               destination_location_id, product_id, qty, status, notes, created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (hauling_no, d["scheduled_date"], d.get("vehicle_id") or None, d.get("driver", ""),
         d["plant_name"].strip(), d.get("destination_location_id") or None, d.get("product_id") or None,
         d.get("qty") or None, d.get("status", "SCHEDULED"), d.get("notes", ""), session["user"]["id"]),
    )
    audit(conn, "CREATE", "HAULING", cur.lastrowid, hauling_no)
    conn.commit()
    conn.close()
    return jsonify(id=cur.lastrowid, hauling_no=hauling_no), 201


@app.put("/api/hauling/<int:hid>")
@roles_required("ADMIN", "MANAGER")
def update_hauling(hid):
    d = request.get_json(force=True)
    conn = get_db()
    existing = conn.execute("SELECT hauling_no FROM hauling_schedule WHERE id=?", (hid,)).fetchone()
    if not existing:
        conn.close()
        return jsonify(error="Not found"), 404
    conn.execute(
        """UPDATE hauling_schedule SET scheduled_date=?, vehicle_id=?, driver=?, plant_name=?,
               destination_location_id=?, product_id=?, qty=?, status=?, notes=? WHERE id=?""",
        (d["scheduled_date"], d.get("vehicle_id") or None, d.get("driver", ""), d["plant_name"].strip(),
         d.get("destination_location_id") or None, d.get("product_id") or None, d.get("qty") or None,
         d.get("status", "SCHEDULED"), d.get("notes", ""), hid),
    )
    audit(conn, "UPDATE", "HAULING", hid, existing["hauling_no"])
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.delete("/api/hauling/<int:hid>")
@roles_required("ADMIN", "MANAGER")
def delete_hauling(hid):
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM hauling_schedule WHERE id=?", (hid,))
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify(error="Can't delete — this hauling schedule entry has related records and can't be deleted."), 400
    if cur.rowcount == 0:
        conn.close()
        return jsonify(error="Not found"), 404
    audit(conn, "DELETE", "HAULING", hid)
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------- employees (Admin + Manager)

@app.get("/api/employees")
@roles_required("ADMIN", "MANAGER")
def list_employees():
    conn = get_db()
    rows = conn.execute(
        "SELECT e.*, l.name location_name FROM employees e LEFT JOIN locations l ON l.id=e.location_id ORDER BY e.name"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/employees")
@roles_required("ADMIN", "MANAGER")
def create_employee():
    d = request.get_json(force=True)
    if not (d.get("name") or "").strip():
        return jsonify(error="Name is required"), 400
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO employees (name, position, location_id, linked_user_id, contact_number, hire_date, status, notes)
           VALUES (?,?,?,?,?,?,?,?)""",
        (d["name"].strip(), d.get("position", ""), d.get("location_id") or None, d.get("linked_user_id") or None,
         d.get("contact_number", ""), d.get("hire_date") or None, d.get("status", "ACTIVE"), d.get("notes", "")),
    )
    audit(conn, "CREATE", "EMPLOYEES", cur.lastrowid, d["name"])
    conn.commit()
    conn.close()
    return jsonify(id=cur.lastrowid), 201


@app.put("/api/employees/<int:eid>")
@roles_required("ADMIN", "MANAGER")
def update_employee(eid):
    d = request.get_json(force=True)
    conn = get_db()
    existing = conn.execute("SELECT id FROM employees WHERE id=?", (eid,)).fetchone()
    if not existing:
        conn.close()
        return jsonify(error="Not found"), 404
    conn.execute(
        """UPDATE employees SET name=?, position=?, location_id=?, linked_user_id=?, contact_number=?,
               hire_date=?, status=?, notes=? WHERE id=?""",
        (d["name"].strip(), d.get("position", ""), d.get("location_id") or None, d.get("linked_user_id") or None,
         d.get("contact_number", ""), d.get("hire_date") or None, d.get("status", "ACTIVE"), d.get("notes", ""), eid),
    )
    audit(conn, "UPDATE", "EMPLOYEES", eid, d["name"])
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.delete("/api/employees/<int:eid>")
@roles_required("ADMIN", "MANAGER")
def delete_employee(eid):
    conn = get_db()
    sched_count = conn.execute("SELECT COUNT(*) c FROM staff_schedule WHERE employee_id=?", (eid,)).fetchone()["c"]
    if sched_count:
        conn.close()
        return jsonify(error=f"This employee has {sched_count} schedule entr(y/ies) on record — remove those first"), 400
    try:
        cur = conn.execute("DELETE FROM employees WHERE id=?", (eid,))
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify(error="Can't delete — this employee has related records and can't be deleted."), 400
    if cur.rowcount == 0:
        conn.close()
        return jsonify(error="Not found"), 404
    audit(conn, "DELETE", "EMPLOYEES", eid)
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------- staff schedule
# Admin/Manager create and edit; everyone logged in can view (staff checking their own shifts).

@app.get("/api/staff-schedule")
@login_required
def list_staff_schedule():
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    conn = get_db()
    sql = """SELECT ss.*, e.name employee_name, l.name location_name FROM staff_schedule ss
              JOIN employees e ON e.id=ss.employee_id LEFT JOIN locations l ON l.id=ss.location_id WHERE 1=1"""
    params = []
    if date_from:
        sql += " AND ss.shift_date >= ?"; params.append(date_from)
    if date_to:
        sql += " AND ss.shift_date <= ?"; params.append(date_to)
    sql += " ORDER BY ss.shift_date, ss.shift_start"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/staff-schedule")
@roles_required("ADMIN", "MANAGER")
def create_staff_schedule():
    d = request.get_json(force=True)
    if not d.get("employee_id") or not d.get("shift_date"):
        return jsonify(error="employee_id and shift_date are required"), 400
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO staff_schedule (employee_id, shift_date, shift_start, shift_end, location_id, notes, created_by)
           VALUES (?,?,?,?,?,?,?)""",
        (d["employee_id"], d["shift_date"], d.get("shift_start", ""), d.get("shift_end", ""),
         d.get("location_id") or None, d.get("notes", ""), session["user"]["id"]),
    )
    audit(conn, "CREATE", "STAFF_SCHEDULE", cur.lastrowid)
    conn.commit()
    conn.close()
    return jsonify(id=cur.lastrowid), 201


@app.delete("/api/staff-schedule/<int:sid>")
@roles_required("ADMIN", "MANAGER")
def delete_staff_schedule(sid):
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM staff_schedule WHERE id=?", (sid,))
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify(error="Can't delete — this schedule entry has related records and can't be deleted."), 400
    if cur.rowcount == 0:
        conn.close()
        return jsonify(error="Not found"), 404
    audit(conn, "DELETE", "STAFF_SCHEDULE", sid)
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------- employee KPI (Admin + Manager)
# Built from real, already-tracked data: each cashier's own sales. No separate
# data-entry step required — pulls straight from the sales table.

@app.get("/api/reports/employee-kpi")
@roles_required("ADMIN", "MANAGER")
def employee_kpi():
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    conn = get_db()
    sql = """SELECT u.id user_id, u.name, u.role, l.name location_name,
                     COUNT(s.id) transaction_count, COALESCE(SUM(s.total),0) total_sales,
                     COALESCE(AVG(s.total),0) avg_sale_value,
                     COALESCE(SUM((SELECT SUM(qty) FROM sale_items si WHERE si.sale_id=s.id)),0) total_items_sold
              FROM users u LEFT JOIN locations l ON l.id=u.location_id
              LEFT JOIN sales s ON s.user_id=u.id AND s.status='COMPLETED'"""
    params = []
    conds = []
    if date_from:
        conds.append("date(s.ts) >= date(?)"); params.append(date_from)
    if date_to:
        conds.append("date(s.ts) <= date(?)"); params.append(date_to)
    if conds:
        sql += " AND " + " AND ".join(conds)
    sql += " WHERE u.role IN ('ATTENDANT','ADMIN') GROUP BY u.id ORDER BY total_sales DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------- static frontend

@app.get("/")
def index():
    return send_from_directory("static", "index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
