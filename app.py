import os
import functools
import csv
import io
import sqlite3
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, session, send_from_directory, Response
from werkzeug.security import generate_password_hash, check_password_hash

from db import get_db, init_db
from reports import xlsx_response, pdf_table_response, soa_pdf_response, commercial_billing_pdf

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

def decode_csv_upload(raw_bytes):
    """CSV files exported from Excel on Windows are very often saved as
    Windows-1252 (or plain Latin-1), not UTF-8 — especially once a name or
    address has an accented character (Ñ, é, etc.) in it. Try UTF-8 first
    since that's the common/correct case, then fall back to the encodings
    Excel actually uses. latin-1 never raises (it maps every byte), so it's
    the guaranteed-to-succeed last resort.
    """
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("latin-1")


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


def has_module_access(conn, role, module_key):
    """ADMIN always has every module. Everyone else is governed by the
    role_permissions table, which Admin/Manager can edit per role."""
    if role == "ADMIN":
        return True
    row = conn.execute(
        "SELECT 1 FROM role_permissions WHERE role_name=? AND module_key=?", (role, module_key)
    ).fetchone()
    return row is not None


def roles_or_module_required(*roles, module=None):
    """Access is granted if the user's role is in the explicit `roles` list
    (the original hardcoded behavior — never regresses), OR if their role has
    been granted `module` access via the custom role/permission system. This
    is what lets a custom role (e.g. a Manager-created 'Junior Cashier' role)
    meaningfully use a module rather than just seeing it in the sidebar."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            if "user" not in session:
                return jsonify(error="Not authenticated"), 401
            u = session["user"]
            if u["role"] in roles:
                return fn(*a, **kw)
            if module:
                conn = get_db()
                allowed = has_module_access(conn, u["role"], module)
                conn.close()
                if allowed:
                    return fn(*a, **kw)
            return jsonify(error="Forbidden: insufficient permissions"), 403
        return wrapper
    return deco


def get_permitted_modules(conn, role):
    if role == "ADMIN":
        from db import MODULE_KEYS
        return MODULE_KEYS
    rows = conn.execute("SELECT module_key FROM role_permissions WHERE role_name=?", (role,)).fetchall()
    return [r["module_key"] for r in rows]


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
    modules = get_permitted_modules(conn, user["role"])
    conn.commit()
    conn.close()
    return jsonify(user=user, permitted_modules=modules)


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
    user = session.get("user")
    if not user:
        return jsonify(user=None)
    conn = get_db()
    modules = get_permitted_modules(conn, user["role"])
    conn.close()
    return jsonify(user=user, permitted_modules=modules)


# ---------------------------------------------------------------- roles & permissions

@app.get("/api/roles")
@roles_required("ADMIN", "MANAGER")
def list_roles():
    conn = get_db()
    rows = conn.execute("SELECT * FROM roles ORDER BY is_builtin DESC, name").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/roles")
@roles_required("ADMIN", "MANAGER")
def create_role():
    d = request.get_json(force=True)
    name = (d.get("name") or "").strip()
    if not name:
        return jsonify(error="Role name is required"), 400
    if name.upper() in ("ADMIN",):
        return jsonify(error="That name is reserved"), 400
    conn = get_db()
    try:
        conn.execute("INSERT INTO roles (name, is_builtin) VALUES (?, 0)", (name,))
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify(error="A role with that name already exists"), 400
    audit(conn, "CREATE", "ROLES", None, name)
    conn.commit()
    conn.close()
    return jsonify(ok=True), 201


@app.delete("/api/roles/<path:name>")
@roles_required("ADMIN", "MANAGER")
def delete_role(name):
    conn = get_db()
    role = conn.execute("SELECT * FROM roles WHERE name=?", (name,)).fetchone()
    if not role:
        conn.close()
        return jsonify(error="Not found"), 404
    if role["is_builtin"]:
        conn.close()
        return jsonify(error="Built-in roles can't be deleted"), 400
    in_use = conn.execute("SELECT COUNT(*) c FROM users WHERE role=?", (name,)).fetchone()["c"]
    if in_use:
        conn.close()
        return jsonify(error=f"{in_use} user(s) still have this role — reassign them first"), 400
    conn.execute("DELETE FROM roles WHERE name=?", (name,))
    conn.execute("DELETE FROM role_permissions WHERE role_name=?", (name,))
    audit(conn, "DELETE", "ROLES", None, name)
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.get("/api/roles/<path:name>/permissions")
@roles_required("ADMIN", "MANAGER")
def get_role_permissions(name):
    from db import MODULE_KEYS
    conn = get_db()
    if name == "ADMIN":
        conn.close()
        return jsonify(module_keys=MODULE_KEYS, all_modules=MODULE_KEYS, locked=True)
    rows = conn.execute("SELECT module_key FROM role_permissions WHERE role_name=?", (name,)).fetchall()
    conn.close()
    return jsonify(module_keys=[r["module_key"] for r in rows], all_modules=MODULE_KEYS, locked=False)


@app.put("/api/roles/<path:name>/permissions")
@roles_required("ADMIN", "MANAGER")
def set_role_permissions(name):
    from db import MODULE_KEYS
    if name == "ADMIN":
        return jsonify(error="Admin always has full access and can't be restricted"), 400
    d = request.get_json(force=True)
    modules = [m for m in (d.get("module_keys") or []) if m in MODULE_KEYS]
    conn = get_db()
    role = conn.execute("SELECT * FROM roles WHERE name=?", (name,)).fetchone()
    if not role:
        conn.close()
        return jsonify(error="Role not found"), 404
    conn.execute("DELETE FROM role_permissions WHERE role_name=?", (name,))
    for m in modules:
        conn.execute("INSERT INTO role_permissions (role_name, module_key) VALUES (?,?)", (name, m))
    audit(conn, "UPDATE", "ROLES", None, f"{name}: {', '.join(modules)}")
    conn.commit()
    conn.close()
    return jsonify(ok=True)


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
            "INSERT INTO products (sku, name, category, unit, price, cost, deposit_price, price_per_kg, min_stock) VALUES (?,?,?,?,?,?,?,?,?)",
            (d["sku"], d["name"], d.get("category", ""), d.get("unit", "cylinder"),
             float(d["price"]), float(d.get("cost") or 0), float(d.get("deposit_price") or 0),
             float(d.get("price_per_kg") or 0), int(d.get("min_stock") or 0)),
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
    csv_text = "sku,name,category,unit,price,cost,deposit_price,price_per_kg,min_stock\nLPG-11KG,Solane LPG 11kg,LPG Cylinder,cylinder,1150,950,1500,0,50\n"
    return Response(csv_text, mimetype="text/csv",
                     headers={"Content-Disposition": "attachment; filename=products-template.csv"})


@app.post("/api/products/import")
@roles_required("ADMIN")
def products_csv_import():
    file = request.files.get("file")
    if not file:
        return jsonify(error="No file uploaded"), 400
    try:
        text = decode_csv_upload(file.read())
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
                "INSERT INTO products (sku, name, category, unit, price, cost, deposit_price, price_per_kg, min_stock) VALUES (?,?,?,?,?,?,?,?,?)",
                (sku, name, (row.get("category") or "").strip(), (row.get("unit") or "cylinder").strip(),
                 price, float(row.get("cost") or 0), float(row.get("deposit_price") or 0),
                 float(row.get("price_per_kg") or 0), int(row.get("min_stock") or 0)),
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
        "UPDATE products SET name=?, category=?, unit=?, price=?, cost=?, deposit_price=?, price_per_kg=?, min_stock=? WHERE id=?",
        (d["name"], d.get("category", ""), d.get("unit", "cylinder"), float(d["price"]),
         float(d.get("cost") or 0), float(d.get("deposit_price") or 0), float(d.get("price_per_kg") or 0),
         int(d.get("min_stock") or 0), pid),
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
         d.get("category", "Solus"), int(d.get("terms_days") or 0), float(d.get("credit_limit") or 0)),
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
         d.get("category", "Solus"), int(d.get("terms_days") or 0), float(d.get("credit_limit") or 0),
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
        text = decode_csv_upload(file.read())
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


# ---------------------------------------------------------------- booked orders
# Order Taker books an order and assigns it to a showroom; that showroom's
# attendants see it in their queue. Creating an order is intentionally NOT
# governed by the generic module-permission system (unlike viewing) — only
# Admin/Manager/Order Taker can ever create one, regardless of what modules
# a custom role has been ticked for, so this can't be opened up by accident.

@app.get("/api/booked-orders")
@roles_or_module_required("ADMIN", "MANAGER", "ORDER_TAKER", module="booked_orders")
def list_booked_orders():
    u = session["user"]
    conn = get_db()
    sql = """SELECT bo.*, c.name customer_name, l.name showroom_name, creator.name created_by_name
              FROM booked_orders bo LEFT JOIN customers c ON c.id=bo.customer_id
              LEFT JOIN locations l ON l.id=bo.showroom_location_id
              LEFT JOIN users creator ON creator.id=bo.created_by WHERE 1=1"""
    params = []
    if u["role"] == "ORDER_TAKER":
        # Order Takers see orders they personally booked, plus every
        # customer-submitted order that still needs a location assigned.
        sql += " AND (bo.created_by=? OR bo.showroom_location_id IS NULL)"
        params.append(u["id"])
    elif u["role"] not in ("ADMIN", "MANAGER"):
        sql += " AND bo.showroom_location_id=?"
        params.append(u["location_id"])
    sql += " ORDER BY bo.order_date DESC"
    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        items = conn.execute(
            "SELECT boi.*, p.name product_name FROM booked_order_items boi JOIN products p ON p.id=boi.product_id WHERE order_id=?",
            (r["id"],),
        ).fetchall()
        out.append({**dict(r), "items": [dict(i) for i in items]})
    conn.close()
    return jsonify(out)


@app.post("/api/booked-orders")
@roles_required("ADMIN", "MANAGER", "ORDER_TAKER")
def create_booked_order():
    d = request.get_json(force=True)
    items = d.get("items") or []
    if not items:
        return jsonify(error="At least one item is required"), 400
    conn = get_db()
    order_no = next_number(conn, "ORD", "booked_orders", "order_no")
    cur = conn.execute(
        """INSERT INTO booked_orders (order_no, customer_id, customer_name_freeform, customer_phone,
               delivery_address, showroom_location_id, source, notes, created_by) VALUES (?,?,?,?,?,?,?,?,?)""",
        (order_no, d.get("customer_id") or None, d.get("customer_name_freeform", ""),
         d.get("customer_phone", ""), d.get("delivery_address", ""), d.get("showroom_location_id") or None, "STAFF",
         d.get("notes", ""), session["user"]["id"]),
    )
    order_id = cur.lastrowid
    for it in items:
        conn.execute("INSERT INTO booked_order_items (order_id, product_id, qty) VALUES (?,?,?)",
                     (order_id, it["product_id"], int(it["qty"])))
    audit(conn, "CREATE", "BOOKED_ORDERS", order_id, order_no)
    conn.commit()
    conn.close()
    return jsonify(id=order_id, order_no=order_no), 201


@app.put("/api/booked-orders/<int:oid>")
@roles_or_module_required("ADMIN", "MANAGER", "ORDER_TAKER", module="booked_orders")
def update_booked_order(oid):
    u = session["user"]
    d = request.get_json(force=True)
    conn = get_db()
    order = conn.execute("SELECT * FROM booked_orders WHERE id=?", (oid,)).fetchone()
    if not order:
        conn.close()
        return jsonify(error="Not found"), 404

    new_status = d.get("status", order["status"])
    if u["role"] not in ("ADMIN", "MANAGER", "ORDER_TAKER"):
        # Attendants: can only mark orders at their own showroom as fulfilled.
        if order["showroom_location_id"] != u["location_id"]:
            conn.close()
            return jsonify(error="Forbidden: this order belongs to another showroom"), 403
        if new_status != "FULFILLED":
            conn.close()
            return jsonify(error="Attendants can only mark orders as fulfilled"), 403
        conn.execute("UPDATE booked_orders SET status=?, fulfilled_by=?, fulfilled_at=datetime('now') WHERE id=?",
                     (new_status, u["id"], oid))
    else:
        # Admin/Manager/Order Taker can also (re)assign which location the
        # order goes to — this is how a customer-submitted, unassigned order
        # gets routed to a specific warehouse or showroom.
        new_location = d.get("showroom_location_id", order["showroom_location_id"])
        conn.execute("UPDATE booked_orders SET status=?, notes=?, delivery_address=?, showroom_location_id=? WHERE id=?",
                     (new_status, d.get("notes", order["notes"]), d.get("delivery_address", order["delivery_address"]),
                      new_location or None, oid))
        if new_status == "FULFILLED" and order["status"] != "FULFILLED":
            conn.execute("UPDATE booked_orders SET fulfilled_by=?, fulfilled_at=datetime('now') WHERE id=?",
                         (u["id"], oid))
    audit(conn, "UPDATE", "BOOKED_ORDERS", oid, f"status={new_status}")
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------- public order form
# No login required. A customer places an order here; it lands in Booked
# Orders as an unassigned, customer-sourced order for an Order Taker (or
# Admin/Manager) to route to a specific warehouse or showroom.

@app.get("/api/public/locations")
def public_locations():
    conn = get_db()
    rows = conn.execute("SELECT id, name, type FROM locations WHERE status='ACTIVE' ORDER BY name").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.get("/api/public/products")
def public_products():
    conn = get_db()
    rows = conn.execute("SELECT id, name, unit, price FROM products WHERE active=1 ORDER BY name").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/public/orders")
def submit_public_order():
    d = request.get_json(force=True)
    # Honeypot: a legitimate browser never fills this hidden field in.
    if (d.get("website") or "").strip():
        return jsonify(order_no="ORD-00000000-0000"), 201  # pretend success, create nothing

    name = (d.get("customer_name") or "").strip()
    phone = (d.get("customer_phone") or "").strip()
    address = (d.get("delivery_address") or "").strip()
    items = d.get("items") or []
    if not name or not phone:
        return jsonify(error="Name and phone number are required"), 400
    if not address:
        return jsonify(error="Delivery address is required"), 400
    if not items:
        return jsonify(error="Add at least one product"), 400
    if len(items) > 20:
        return jsonify(error="Too many line items"), 400

    conn = get_db()
    try:
        resolved = []
        for it in items:
            prod = conn.execute("SELECT id FROM products WHERE id=? AND active=1", (it.get("product_id"),)).fetchone()
            if not prod:
                conn.close()
                return jsonify(error="One of the selected products is no longer available"), 400
            qty = int(it.get("qty") or 0)
            if qty <= 0 or qty > 500:
                conn.close()
                return jsonify(error="Quantity must be between 1 and 500"), 400
            resolved.append((prod["id"], qty))

        preferred_location = d.get("preferred_location_id") or None
        if preferred_location:
            loc = conn.execute("SELECT id FROM locations WHERE id=? AND status='ACTIVE'", (preferred_location,)).fetchone()
            if not loc:
                preferred_location = None

        order_no = next_number(conn, "ORD", "booked_orders", "order_no")
        cur = conn.execute(
            """INSERT INTO booked_orders (order_no, customer_name_freeform, customer_phone, delivery_address,
                   showroom_location_id, source, notes) VALUES (?,?,?,?,?,?,?)""",
            (order_no, name[:200], phone[:50], address[:500], preferred_location, "CUSTOMER", (d.get("notes") or "")[:500]),
        )
        order_id = cur.lastrowid
        for product_id, qty in resolved:
            conn.execute("INSERT INTO booked_order_items (order_id, product_id, qty) VALUES (?,?,?)",
                         (order_id, product_id, qty))
        audit(conn, "CREATE", "BOOKED_ORDERS", order_id, f"{order_no} (public submission)")
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify(error=f"Could not submit order: {e}"), 400
    conn.close()
    return jsonify(order_no=order_no), 201


# ---------------------------------------------------------------- incident reports
# Any logged-in role can file one for their own location; Admin/Manager see
# and resolve everything, everyone else only sees their own location's reports.

ALLOWED_ATTACHMENT_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "pdf", "xls", "xlsx", "csv", "doc", "docx"}
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB per file
MAX_ATTACHMENTS_PER_INCIDENT = 10

PREVIEWABLE_MIMETYPES = {
    "image/png", "image/jpeg", "image/gif", "application/pdf",
}

EXTENSION_MIMETYPES = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
    "pdf": "application/pdf",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _can_view_incident(u, report_row):
    if u["role"] in ("ADMIN", "MANAGER"):
        return True
    return report_row["location_id"] == u["location_id"] or report_row["reported_by"] == u["id"]


@app.get("/api/incident-reports")
@login_required
def list_incident_reports():
    u = session["user"]
    conn = get_db()
    sql = """SELECT ir.*, l.name location_name, reporter.name reporter_name, resolver.name resolver_name,
                     (SELECT COUNT(*) FROM incident_report_attachments a WHERE a.incident_id=ir.id) attachment_count
              FROM incident_reports ir LEFT JOIN locations l ON l.id=ir.location_id
              LEFT JOIN users reporter ON reporter.id=ir.reported_by
              LEFT JOIN users resolver ON resolver.id=ir.resolved_by WHERE 1=1"""
    params = []
    if u["role"] not in ("ADMIN", "MANAGER"):
        sql += " AND (ir.location_id=? OR ir.reported_by=?)"
        params += [u["location_id"], u["id"]]
    sql += " ORDER BY ir.ts DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/incident-reports")
@login_required
def create_incident_report():
    d = request.get_json(force=True)
    u = session["user"]
    description = (d.get("description") or "").strip()
    if not description:
        return jsonify(error="Description is required"), 400
    location_id = d.get("location_id") if u["role"] in ("ADMIN", "MANAGER") else u["location_id"]
    conn = get_db()
    report_no = next_number(conn, "INC", "incident_reports", "report_no")
    cur = conn.execute(
        """INSERT INTO incident_reports (report_no, location_id, reported_by, category, severity, description)
           VALUES (?,?,?,?,?,?)""",
        (report_no, location_id, u["id"], d.get("category", "Other"), d.get("severity", "LOW"), description),
    )
    audit(conn, "CREATE", "INCIDENT_REPORTS", cur.lastrowid, report_no)
    conn.commit()
    conn.close()
    return jsonify(id=cur.lastrowid, report_no=report_no), 201


@app.get("/api/incident-reports/<int:iid>")
@login_required
def get_incident_report(iid):
    u = session["user"]
    conn = get_db()
    row = conn.execute(
        """SELECT ir.*, l.name location_name, reporter.name reporter_name, resolver.name resolver_name
           FROM incident_reports ir LEFT JOIN locations l ON l.id=ir.location_id
           LEFT JOIN users reporter ON reporter.id=ir.reported_by
           LEFT JOIN users resolver ON resolver.id=ir.resolved_by WHERE ir.id=?""",
        (iid,),
    ).fetchone()
    if not row or not _can_view_incident(u, row):
        conn.close()
        return jsonify(error="Not found"), 404
    conn.close()
    return jsonify(dict(row))


@app.put("/api/incident-reports/<int:iid>/resolve")
@roles_required("ADMIN", "MANAGER")
def resolve_incident_report(iid):
    d = request.get_json(force=True)
    conn = get_db()
    report = conn.execute("SELECT report_no FROM incident_reports WHERE id=?", (iid,)).fetchone()
    if not report:
        conn.close()
        return jsonify(error="Not found"), 404
    conn.execute(
        "UPDATE incident_reports SET status='RESOLVED', resolution_notes=?, resolved_by=?, resolved_at=datetime('now') WHERE id=?",
        (d.get("resolution_notes", ""), session["user"]["id"], iid),
    )
    audit(conn, "RESOLVE", "INCIDENT_REPORTS", iid, report["report_no"])
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.get("/api/incident-reports/<int:iid>/attachments")
@login_required
def list_incident_attachments(iid):
    u = session["user"]
    conn = get_db()
    report = conn.execute("SELECT * FROM incident_reports WHERE id=?", (iid,)).fetchone()
    if not report or not _can_view_incident(u, report):
        conn.close()
        return jsonify(error="Not found"), 404
    rows = conn.execute(
        """SELECT a.id, a.filename, a.mimetype, a.size_bytes, a.uploaded_at, up.name uploaded_by_name
           FROM incident_report_attachments a LEFT JOIN users up ON up.id=a.uploaded_by
           WHERE a.incident_id=? ORDER BY a.uploaded_at""",
        (iid,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/incident-reports/<int:iid>/attachments")
@login_required
def upload_incident_attachments(iid):
    u = session["user"]
    conn = get_db()
    report = conn.execute("SELECT * FROM incident_reports WHERE id=?", (iid,)).fetchone()
    if not report:
        conn.close()
        return jsonify(error="Not found"), 404
    # Only the person who filed it, or Admin/Manager, can attach evidence to it.
    if u["role"] not in ("ADMIN", "MANAGER") and report["reported_by"] != u["id"]:
        conn.close()
        return jsonify(error="Forbidden: only the reporter or Admin/Manager can add attachments"), 403

    files = request.files.getlist("files")
    if not files:
        conn.close()
        return jsonify(error="No files uploaded"), 400

    existing_count = conn.execute(
        "SELECT COUNT(*) c FROM incident_report_attachments WHERE incident_id=?", (iid,)
    ).fetchone()["c"]
    if existing_count + len(files) > MAX_ATTACHMENTS_PER_INCIDENT:
        conn.close()
        return jsonify(error=f"Too many attachments — max {MAX_ATTACHMENTS_PER_INCIDENT} per report"), 400

    saved, errors = [], []
    for f in files:
        filename = f.filename or "file"
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
            errors.append(f"{filename}: file type not allowed (allowed: {', '.join(sorted(ALLOWED_ATTACHMENT_EXTENSIONS))})")
            continue
        data = f.read()
        if len(data) > MAX_ATTACHMENT_SIZE:
            errors.append(f"{filename}: exceeds 10 MB limit")
            continue
        if len(data) == 0:
            errors.append(f"{filename}: empty file")
            continue
        mimetype = EXTENSION_MIMETYPES.get(ext, f.content_type or "application/octet-stream")
        cur = conn.execute(
            """INSERT INTO incident_report_attachments (incident_id, filename, mimetype, size_bytes, file_data, uploaded_by)
               VALUES (?,?,?,?,?,?)""",
            (iid, filename[:255], mimetype, len(data), data, u["id"]),
        )
        saved.append({"id": cur.lastrowid, "filename": filename})

    if saved:
        audit(conn, "ATTACH", "INCIDENT_REPORTS", iid, f"{len(saved)} file(s) attached: {', '.join(s['filename'] for s in saved)}")
        conn.commit()
    conn.close()
    return jsonify(saved=saved, errors=errors), (201 if saved else 400)


@app.get("/api/incident-attachments/<int:aid>")
@login_required
def download_incident_attachment(aid):
    u = session["user"]
    conn = get_db()
    row = conn.execute(
        """SELECT a.*, ir.location_id, ir.reported_by FROM incident_report_attachments a
           JOIN incident_reports ir ON ir.id=a.incident_id WHERE a.id=?""",
        (aid,),
    ).fetchone()
    if not row or not _can_view_incident(u, row):
        conn.close()
        return jsonify(error="Not found"), 404
    data, filename, mimetype = row["file_data"], row["filename"], row["mimetype"]
    conn.close()
    disposition = "inline" if mimetype in PREVIEWABLE_MIMETYPES else "attachment"
    return Response(
        data, mimetype=mimetype,
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@app.delete("/api/incident-attachments/<int:aid>")
@roles_required("ADMIN", "MANAGER")
def delete_incident_attachment(aid):
    conn = get_db()
    row = conn.execute("SELECT incident_id, filename FROM incident_report_attachments WHERE id=?", (aid,)).fetchone()
    if not row:
        conn.close()
        return jsonify(error="Not found"), 404
    conn.execute("DELETE FROM incident_report_attachments WHERE id=?", (aid,))
    audit(conn, "DELETE_ATTACHMENT", "INCIDENT_REPORTS", row["incident_id"], row["filename"])
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------- inventory

@app.get("/api/inventory")
@roles_or_module_required("ADMIN", "MANAGER", "ATTENDANT", "DISPATCHER", module="inventory")
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
@roles_or_module_required("ADMIN", "MANAGER", "ATTENDANT", module="cylinder_deposits")
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
@roles_or_module_required("ADMIN", "ATTENDANT", module="pos")
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
    discount = float(d.get("discount") or 0)

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
@roles_or_module_required("ADMIN", "MANAGER", "ATTENDANT", module="sales")
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
    u = session["user"]
    conn = get_db()
    rows = conn.execute("SELECT id FROM receivables").fetchall()
    for r in rows:
        recompute_receivable_status(conn, r["id"])
    conn.commit()
    sql = """SELECT r.*, c.name customer_name, c.account_no, s.sale_no, s.location_id
              FROM receivables r JOIN customers c ON c.id=r.customer_id JOIN sales s ON s.id=r.sale_id
              WHERE 1=1"""
    params = []
    if u["role"] not in ("ADMIN", "MANAGER"):
        sql += " AND s.location_id=?"
        params.append(u["location_id"])
    sql += " ORDER BY r.due_date ASC"
    out = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in out])


AGING_BUCKETS = ["current", "days_1_30", "days_31_60", "days_61_90", "days_91_120", "days_120_plus"]


def _aging_bucket_for(days_overdue):
    if days_overdue <= 0:
        return "current"
    if days_overdue <= 30:
        return "days_1_30"
    if days_overdue <= 60:
        return "days_31_60"
    if days_overdue <= 90:
        return "days_61_90"
    if days_overdue <= 120:
        return "days_91_120"
    return "days_120_plus"


def _build_aging_report(conn, u):
    rows = conn.execute("SELECT id FROM receivables WHERE status != 'PAID'").fetchall()
    for r in rows:
        recompute_receivable_status(conn, r["id"])
    conn.commit()

    sql = """SELECT r.balance, r.due_date, c.id customer_id, c.name customer_name, c.account_no, s.location_id
              FROM receivables r JOIN customers c ON c.id=r.customer_id JOIN sales s ON s.id=r.sale_id
              WHERE r.balance > 0.009"""
    params = []
    if u["role"] not in ("ADMIN", "MANAGER"):
        sql += " AND s.location_id=?"
        params.append(u["location_id"])
    rows = conn.execute(sql, params).fetchall()

    today = datetime.now().date()
    by_customer = {}
    totals = {b: 0.0 for b in AGING_BUCKETS}
    for r in rows:
        due = datetime.strptime(r["due_date"], "%Y-%m-%d").date()
        days_overdue = (today - due).days
        bucket = _aging_bucket_for(days_overdue)
        key = r["customer_id"]
        if key not in by_customer:
            by_customer[key] = {"customer_name": r["customer_name"], "account_no": r["account_no"],
                                 **{b: 0.0 for b in AGING_BUCKETS}, "total": 0.0}
        by_customer[key][bucket] = round(by_customer[key][bucket] + r["balance"], 2)
        by_customer[key]["total"] = round(by_customer[key]["total"] + r["balance"], 2)
        totals[bucket] = round(totals[bucket] + r["balance"], 2)

    customer_rows = sorted(by_customer.values(), key=lambda x: -x["total"])
    grand_total = round(sum(totals.values()), 2)
    return customer_rows, totals, grand_total


@app.get("/api/reports/receivables-aging")
@login_required
def receivables_aging_report():
    conn = get_db()
    customer_rows, totals, grand_total = _build_aging_report(conn, session["user"])
    conn.close()
    return jsonify(rows=customer_rows, totals=totals, grand_total=grand_total)


@app.get("/api/reports/receivables-aging/export")
@roles_or_module_required("ADMIN", "MANAGER", module="reports")
def export_receivables_aging():
    fmt = request.args.get("format", "xlsx")
    conn = get_db()
    customer_rows, totals, grand_total = _build_aging_report(conn, session["user"])
    conn.close()

    headers = ["Customer", "Account #", "Current", "1-30 Days", "31-60 Days", "61-90 Days", "91-120 Days", "120+ Days", "Total"]
    data = [(r["customer_name"], r["account_no"], r["current"], r["days_1_30"], r["days_31_60"],
              r["days_61_90"], r["days_91_120"], r["days_120_plus"], r["total"]) for r in customer_rows]
    data.append(("TOTAL", "", totals["current"], totals["days_1_30"], totals["days_31_60"],
                 totals["days_61_90"], totals["days_91_120"], totals["days_120_plus"], grand_total))
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    if fmt == "pdf":
        return pdf_table_response("Accounts Receivable Aging", headers, data, f"receivables-aging-{stamp}.pdf",
                                   subtitle=f"As of {datetime.now().strftime('%Y-%m-%d')}",
                                   col_widths=[110, 60, 60, 60, 60, 60, 65, 60, 65], landscape_mode=True)
    return xlsx_response(headers, data, f"receivables-aging-{stamp}.xlsx", "AR Aging")


@app.post("/api/receivables/<int:rid>/pay")
@roles_required("ADMIN", "MANAGER", "ATTENDANT")
def pay_receivable(rid):
    d = request.get_json(force=True)
    amount = float(d["amount"])
    method = d.get("method", "Cash")
    u = session["user"]
    conn = get_db()
    r = conn.execute(
        "SELECT r.*, s.location_id sale_location_id FROM receivables r JOIN sales s ON s.id=r.sale_id WHERE r.id=?",
        (rid,),
    ).fetchone()
    if not r:
        conn.close()
        return jsonify(error="Not found"), 404
    if u["role"] not in ("ADMIN", "MANAGER") and r["sale_location_id"] != u["location_id"]:
        conn.close()
        return jsonify(error="Forbidden: this receivable belongs to another location"), 403
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
@roles_or_module_required("ADMIN", "DISPATCHER", module="transfers")
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
@roles_or_module_required("ADMIN", "DISPATCHER", module="transfers")
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
@roles_or_module_required("ADMIN", "DISPATCHER", module="transfers")
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
@roles_or_module_required("ADMIN", "MANAGER", module="reports")
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
                                   col_widths=[75, 70, 70, 65, 75, 160, 45, 55, 55, 55, 55, 55, 55],
                                   landscape_mode=True)
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
@roles_or_module_required("ADMIN", "MANAGER", module="reports")
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
@roles_or_module_required("ADMIN", "MANAGER", module="reports")
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
@roles_required("ADMIN", "MANAGER")
def create_user():
    d = request.get_json(force=True)
    u = session["user"]
    role = (d.get("role") or "").strip()
    conn = get_db()
    role_exists = conn.execute("SELECT 1 FROM roles WHERE name=?", (role,)).fetchone()
    if not role_exists:
        conn.close()
        return jsonify(error=f"'{role}' is not a recognized role — create it first under Manage Roles"), 400
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, name, role, location_id) VALUES (?,?,?,?,?)",
            (d["username"].strip().lower(), generate_password_hash(d["password"]), d["name"], role,
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
@roles_required("ADMIN", "MANAGER")
def update_user(uid):
    d = request.get_json(force=True)
    u = session["user"]
    conn = get_db()
    existing = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not existing:
        conn.close()
        return jsonify(error="Not found"), 404
    new_role = d.get("role", existing["role"])
    if new_role != existing["role"]:
        role_exists = conn.execute("SELECT 1 FROM roles WHERE name=?", (new_role,)).fetchone()
        if not role_exists:
            conn.close()
            return jsonify(error=f"'{new_role}' is not a recognized role — create it first under Manage Roles"), 400
    new_password = d.get("password")
    if new_password:
        conn.execute(
            "UPDATE users SET name=?, role=?, location_id=?, password_hash=? WHERE id=?",
            (d.get("name", existing["name"]), new_role, d.get("location_id"), generate_password_hash(new_password), uid),
        )
        audit(conn, "UPDATE", "USERS", uid, "details + password reset")
    else:
        conn.execute(
            "UPDATE users SET name=?, role=?, location_id=? WHERE id=?",
            (d.get("name", existing["name"]), new_role, d.get("location_id"), uid),
        )
        audit(conn, "UPDATE", "USERS", uid, "details updated")
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.post("/api/users/<int:uid>/disable")
@roles_required("ADMIN", "MANAGER")
def disable_user(uid):
    conn = get_db()
    conn.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
    audit(conn, "DISABLE", "USERS", uid)
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.post("/api/users/<int:uid>/enable")
@roles_required("ADMIN", "MANAGER")
def enable_user(uid):
    conn = get_db()
    conn.execute("UPDATE users SET active=1 WHERE id=?", (uid,))
    audit(conn, "ENABLE", "USERS", uid)
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.delete("/api/users/<int:uid>")
@roles_required("ADMIN", "MANAGER")
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
@roles_or_module_required("ADMIN", "MANAGER", module="audit")
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
@roles_or_module_required("ADMIN", "MANAGER", module="vehicles")
def list_vehicles():
    conn = get_db()
    rows = conn.execute("SELECT * FROM vehicles ORDER BY plate_number").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/vehicles")
@roles_or_module_required("ADMIN", "MANAGER", module="vehicles")
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
             d.get("status", "AVAILABLE"), int(d.get("odometer") or 0), d.get("last_maintenance_date") or None,
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
@roles_or_module_required("ADMIN", "MANAGER", module="vehicles")
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
         int(d.get("odometer") or 0), d.get("last_maintenance_date") or None, d.get("next_maintenance_date") or None,
         d.get("registration_expiry") or None, d.get("insurance_expiry") or None, d.get("notes", ""), vid),
    )
    audit(conn, "UPDATE", "VEHICLES", vid)
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.delete("/api/vehicles/<int:vid>")
@roles_or_module_required("ADMIN", "MANAGER", module="vehicles")
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
@roles_or_module_required("ADMIN", "MANAGER", module="hauling")
def list_hauling():
    conn = get_db()
    rows = conn.execute(
        """SELECT h.*, v.plate_number, l.name destination_name
           FROM hauling_schedule h LEFT JOIN vehicles v ON v.id=h.vehicle_id
           LEFT JOIN locations l ON l.id=h.destination_location_id
           ORDER BY h.scheduled_date DESC"""
    ).fetchall()
    out = []
    for h in rows:
        items = conn.execute(
            "SELECT hsi.*, p.name product_name FROM hauling_schedule_items hsi JOIN products p ON p.id=hsi.product_id WHERE hauling_id=?",
            (h["id"],),
        ).fetchall()
        out.append({**dict(h), "items": [dict(i) for i in items]})
    conn.close()
    return jsonify(out)


@app.post("/api/hauling")
@roles_or_module_required("ADMIN", "MANAGER", module="hauling")
def create_hauling():
    d = request.get_json(force=True)
    items = d.get("items") or []
    if not d.get("scheduled_date") or not (d.get("plant_name") or "").strip():
        return jsonify(error="scheduled_date and plant_name are required"), 400
    if not items:
        return jsonify(error="At least one product is required"), 400
    conn = get_db()
    hauling_no = next_number(conn, "HAUL", "hauling_schedule", "hauling_no")
    cur = conn.execute(
        """INSERT INTO hauling_schedule (hauling_no, scheduled_date, vehicle_id, driver, plant_name,
               destination_location_id, status, notes, created_by)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (hauling_no, d["scheduled_date"], d.get("vehicle_id") or None, d.get("driver", ""),
         d["plant_name"].strip(), d.get("destination_location_id") or None,
         d.get("status", "SCHEDULED"), d.get("notes", ""), session["user"]["id"]),
    )
    hauling_id = cur.lastrowid
    for it in items:
        qty = int(it["qty"])
        if qty <= 0:
            conn.rollback()
            conn.close()
            return jsonify(error="Quantities must be positive"), 400
        conn.execute("INSERT INTO hauling_schedule_items (hauling_id, product_id, qty) VALUES (?,?,?)",
                     (hauling_id, it["product_id"], qty))
    audit(conn, "CREATE", "HAULING", hauling_id, hauling_no)
    conn.commit()
    conn.close()
    return jsonify(id=hauling_id, hauling_no=hauling_no), 201


@app.put("/api/hauling/<int:hid>")
@roles_or_module_required("ADMIN", "MANAGER", module="hauling")
def update_hauling(hid):
    d = request.get_json(force=True)
    conn = get_db()
    existing = conn.execute("SELECT * FROM hauling_schedule WHERE id=?", (hid,)).fetchone()
    if not existing:
        conn.close()
        return jsonify(error="Not found"), 404
    conn.execute(
        """UPDATE hauling_schedule SET scheduled_date=?, vehicle_id=?, driver=?, plant_name=?,
               destination_location_id=?, status=?, notes=? WHERE id=?""",
        (d.get("scheduled_date", existing["scheduled_date"]), d.get("vehicle_id", existing["vehicle_id"]) or None,
         d.get("driver", existing["driver"]), d.get("plant_name", existing["plant_name"]).strip(),
         d.get("destination_location_id", existing["destination_location_id"]) or None,
         d.get("status", existing["status"]), d.get("notes", existing["notes"]), hid),
    )
    if d.get("items") is not None:
        conn.execute("DELETE FROM hauling_schedule_items WHERE hauling_id=?", (hid,))
        for it in d["items"]:
            qty = int(it["qty"])
            if qty <= 0:
                conn.rollback()
                conn.close()
                return jsonify(error="Quantities must be positive"), 400
            conn.execute("INSERT INTO hauling_schedule_items (hauling_id, product_id, qty) VALUES (?,?,?)",
                         (hid, it["product_id"], qty))
    audit(conn, "UPDATE", "HAULING", hid, existing["hauling_no"])
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.delete("/api/hauling/<int:hid>")
@roles_or_module_required("ADMIN", "MANAGER", module="hauling")
def delete_hauling(hid):
    conn = get_db()
    try:
        conn.execute("DELETE FROM hauling_schedule_items WHERE hauling_id=?", (hid,))
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


# ---------------------------------------------------------------- commercial 50kg weight billing
# For commercial 50kg accounts: dispatcher weighs cylinders in (gross/tare ->
# net) at delivery, weighs them out again at pickup/exchange, and the system
# computes actual LPG consumed as the difference — that's the billable
# weight. Billing (Admin/Manager, standing in for "the accountant") converts
# that into a normal credit sale + receivable, reusing all the existing AR
# machinery (aging report, SOA, payments) rather than building a parallel one.

@app.get("/api/commercial-deliveries")
@roles_or_module_required("ADMIN", "MANAGER", "DISPATCHER", module="commercial_deliveries")
def list_commercial_deliveries():
    u = session["user"]
    conn = get_db()
    sql = """SELECT cd.*, c.name customer_name, c.account_no, l.name location_name, p.name product_name,
                     creator.name created_by_name
              FROM commercial_deliveries cd JOIN customers c ON c.id=cd.customer_id
              JOIN locations l ON l.id=cd.location_id JOIN products p ON p.id=cd.product_id
              LEFT JOIN users creator ON creator.id=cd.created_by WHERE 1=1"""
    params = []
    if u["role"] not in ("ADMIN", "MANAGER"):
        sql += " AND cd.location_id=?"
        params.append(u["location_id"])
    sql += " ORDER BY cd.delivery_date DESC"
    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        if r["billing_type"] == "READING":
            total_net_in = None
            total_consumption = round(r["current_reading"] - r["previous_reading"], 2) \
                if r["current_reading"] is not None and r["previous_reading"] is not None else None
            cyls = []
        else:
            cyls = conn.execute("SELECT * FROM commercial_delivery_cylinders WHERE delivery_id=?", (r["id"],)).fetchall()
            total_net_in = round(sum(c["net_weight_in"] for c in cyls), 2)
            total_consumption = round(sum(c["consumption_weight"] or 0 for c in cyls), 2) if r["status"] in ("PICKED_UP", "BILLED") else None
            cyls = [dict(c) for c in cyls]
        out.append({**dict(r), "cylinders": cyls,
                    "total_net_weight_in": total_net_in, "total_consumption_weight": total_consumption})
    conn.close()
    return jsonify(out)


@app.get("/api/commercial-deliveries/last-reading")
@roles_or_module_required("ADMIN", "MANAGER", "DISPATCHER", module="commercial_deliveries")
def last_meter_reading():
    """Auto-suggest the starting reading for a new READING-type bill: the
    ending reading from that customer's last one, like a utility company."""
    customer_id = request.args.get("customer_id", type=int)
    meter_label = request.args.get("meter_label", "")
    if not customer_id:
        return jsonify(error="customer_id is required"), 400
    conn = get_db()
    row = conn.execute(
        """SELECT current_reading FROM commercial_deliveries
           WHERE customer_id=? AND billing_type='READING' AND current_reading IS NOT NULL
                 AND (meter_label=? OR ?='')
           ORDER BY id DESC LIMIT 1""",
        (customer_id, meter_label, meter_label),
    ).fetchone()
    conn.close()
    return jsonify(previous_reading=row["current_reading"] if row else None)


@app.post("/api/commercial-deliveries")
@roles_or_module_required("ADMIN", "MANAGER", "DISPATCHER", module="commercial_deliveries")
def create_commercial_delivery():
    d = request.get_json(force=True)
    u = session["user"]
    location_id = u["location_id"] if u["role"] not in ("ADMIN", "MANAGER") else d.get("location_id")
    billing_type = d.get("billing_type", "WIWO")
    if billing_type not in ("WIWO", "READING"):
        return jsonify(error="billing_type must be WIWO or READING"), 400
    if not d.get("customer_id") or not d.get("product_id") or not location_id:
        return jsonify(error="customer_id, product_id and location_id are required"), 400

    conn = get_db()

    if billing_type == "WIWO":
        cylinders = d.get("cylinders") or []
        if not cylinders:
            conn.close()
            return jsonify(error="At least one cylinder is required"), 400
        resolved = []
        for cyl in cylinders:
            try:
                gw = float(cyl.get("gross_weight_in"))
                tw = float(cyl.get("tare_weight"))
            except (TypeError, ValueError):
                conn.close()
                return jsonify(error="Gross weight and tare weight must be numbers"), 400
            if gw <= 0 or tw <= 0:
                conn.close()
                return jsonify(error="Gross weight and tare weight must be positive"), 400
            if gw <= tw:
                conn.close()
                return jsonify(error=f"Gross weight ({gw}) must be greater than tare weight ({tw}) for each cylinder"), 400
            resolved.append((cyl.get("cylinder_label", ""), gw, tw, round(gw - tw, 2)))

        delivery_no = next_number(conn, "CD", "commercial_deliveries", "delivery_no")
        cur = conn.execute(
            """INSERT INTO commercial_deliveries (delivery_no, customer_id, location_id, product_id, billing_type, notes, created_by)
               VALUES (?,?,?,?,?,?,?)""",
            (delivery_no, d["customer_id"], location_id, d["product_id"], "WIWO", d.get("notes", ""), u["id"]),
        )
        delivery_id = cur.lastrowid
        for label, gw, tw, nw in resolved:
            conn.execute(
                """INSERT INTO commercial_delivery_cylinders (delivery_id, cylinder_label, gross_weight_in, tare_weight, net_weight_in)
                   VALUES (?,?,?,?,?)""",
                (delivery_id, label, gw, tw, nw),
            )
        total_nw = round(sum(r[3] for r in resolved), 2)
        audit(conn, "CREATE", "COMMERCIAL_DELIVERIES", delivery_id, f"{delivery_no} (WIWO): {len(resolved)} cylinder(s), {total_nw}kg total")
        conn.commit()
        conn.close()
        return jsonify(id=delivery_id, delivery_no=delivery_no, total_net_weight_in=total_nw), 201

    else:  # READING
        try:
            previous_reading = float(d.get("previous_reading"))
            current_reading = float(d.get("current_reading"))
        except (TypeError, ValueError):
            conn.close()
            return jsonify(error="previous_reading and current_reading must be numbers"), 400
        if previous_reading < 0 or current_reading < 0:
            conn.close()
            return jsonify(error="Readings can't be negative"), 400
        if current_reading < previous_reading:
            conn.close()
            return jsonify(error=f"Current reading ({current_reading}) can't be less than the previous reading ({previous_reading})"), 400

        delivery_no = next_number(conn, "CD", "commercial_deliveries", "delivery_no")
        # Both readings are captured in one step (no physical pickup to wait
        # for), so this goes straight to PICKED_UP — ready to bill.
        cur = conn.execute(
            """INSERT INTO commercial_deliveries (delivery_no, customer_id, location_id, product_id, billing_type,
                   meter_label, previous_reading, current_reading, status, pickup_date, notes, created_by, collected_by)
               VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),?,?,?)""",
            (delivery_no, d["customer_id"], location_id, d["product_id"], "READING",
             d.get("meter_label", ""), previous_reading, current_reading, "PICKED_UP",
             d.get("notes", ""), u["id"], u["id"]),
        )
        delivery_id = cur.lastrowid
        consumption = round(current_reading - previous_reading, 2)
        audit(conn, "CREATE", "COMMERCIAL_DELIVERIES", delivery_id,
              f"{delivery_no} (READING): {previous_reading} -> {current_reading} = {consumption} consumed")
        conn.commit()
        conn.close()
        return jsonify(id=delivery_id, delivery_no=delivery_no, consumption=consumption), 201


@app.get("/api/commercial-deliveries/<int:did>")
@roles_or_module_required("ADMIN", "MANAGER", "DISPATCHER", module="commercial_deliveries")
def get_commercial_delivery(did):
    conn = get_db()
    row = conn.execute(
        """SELECT cd.*, c.name customer_name, c.account_no, l.name location_name, p.name product_name
           FROM commercial_deliveries cd JOIN customers c ON c.id=cd.customer_id
           JOIN locations l ON l.id=cd.location_id JOIN products p ON p.id=cd.product_id WHERE cd.id=?""",
        (did,),
    ).fetchone()
    if not row:
        conn.close()
        return jsonify(error="Not found"), 404
    cyls = conn.execute("SELECT * FROM commercial_delivery_cylinders WHERE delivery_id=?", (did,)).fetchall()
    conn.close()
    total_consumption = None
    if row["billing_type"] == "READING":
        if row["current_reading"] is not None and row["previous_reading"] is not None:
            total_consumption = round(row["current_reading"] - row["previous_reading"], 2)
    elif row["status"] in ("PICKED_UP", "BILLED"):
        total_consumption = round(sum(c["consumption_weight"] or 0 for c in cyls), 2)
    return jsonify({**dict(row), "cylinders": [dict(c) for c in cyls], "total_consumption_weight": total_consumption})


@app.get("/api/commercial-deliveries/<int:did>/export/pdf")
@roles_or_module_required("ADMIN", "MANAGER", "DISPATCHER", module="commercial_deliveries")
def export_commercial_delivery_pdf(did):
    conn = get_db()
    row = conn.execute(
        """SELECT cd.*, c.name customer_name, c.account_no, l.name location_name, p.name product_name
           FROM commercial_deliveries cd JOIN customers c ON c.id=cd.customer_id
           JOIN locations l ON l.id=cd.location_id JOIN products p ON p.id=cd.product_id WHERE cd.id=?""",
        (did,),
    ).fetchone()
    if not row:
        conn.close()
        return jsonify(error="Not found"), 404
    cyls = conn.execute("SELECT * FROM commercial_delivery_cylinders WHERE delivery_id=?", (did,)).fetchall()
    conn.close()
    fname = f"{row['delivery_no']}.pdf"
    return commercial_billing_pdf(dict(row), [dict(c) for c in cyls], fname)


@app.put("/api/commercial-deliveries/<int:did>/pickup")
@roles_or_module_required("ADMIN", "MANAGER", "DISPATCHER", module="commercial_deliveries")
def record_commercial_pickup(did):
    """Weigh-out: record the residual weight for each cylinder. Consumption
    (billable weight) = net weight delivered - net weight residual."""
    d = request.get_json(force=True)
    cylinders = d.get("cylinders") or []

    conn = get_db()
    delivery = conn.execute("SELECT * FROM commercial_deliveries WHERE id=?", (did,)).fetchone()
    if not delivery:
        conn.close()
        return jsonify(error="Not found"), 404
    if delivery["billing_type"] != "WIWO":
        conn.close()
        return jsonify(error="Reading-based billing doesn't use a separate pickup step — both readings are entered together"), 400
    if not cylinders:
        conn.close()
        return jsonify(error="At least one cylinder weigh-out reading is required"), 400
    if delivery["status"] != "DELIVERED":
        conn.close()
        return jsonify(error=f"This delivery is already {delivery['status']} — pickup can't be recorded again"), 400

    existing_cyls = {c["id"]: c for c in conn.execute(
        "SELECT * FROM commercial_delivery_cylinders WHERE delivery_id=?", (did,)
    ).fetchall()}

    for cyl in cylinders:
        cid = cyl.get("id")
        row = existing_cyls.get(cid)
        if not row:
            conn.close()
            return jsonify(error=f"Cylinder {cid} does not belong to this delivery"), 400
        try:
            gw_out = float(cyl.get("gross_weight_out"))
        except (TypeError, ValueError):
            conn.close()
            return jsonify(error="Gross weight out must be a number for every cylinder"), 400
        if gw_out < 0:
            conn.close()
            return jsonify(error="Gross weight out can't be negative"), 400
        nw_out = round(gw_out - row["tare_weight"], 2)
        if nw_out < 0:
            conn.close()
            return jsonify(error=(
                f"Cylinder '{row['cylinder_label'] or cid}': weight-out ({gw_out}kg) is less than its tare "
                f"weight ({row['tare_weight']}kg) — check the reading."
            )), 400
        consumption = round(row["net_weight_in"] - nw_out, 2)
        if consumption < 0:
            conn.close()
            return jsonify(error=(
                f"Cylinder '{row['cylinder_label'] or cid}': residual weight ({nw_out}kg) exceeds what was "
                f"delivered ({row['net_weight_in']}kg) — check the reading."
            )), 400
        conn.execute(
            "UPDATE commercial_delivery_cylinders SET gross_weight_out=?, net_weight_out=?, consumption_weight=? WHERE id=?",
            (gw_out, nw_out, consumption, cid),
        )

    if len(cylinders) != len(existing_cyls):
        conn.close()
        return jsonify(error="A weigh-out reading is required for every cylinder in this delivery"), 400

    conn.execute("UPDATE commercial_deliveries SET status='PICKED_UP', pickup_date=datetime('now'), collected_by=? WHERE id=?",
                 (session["user"]["id"], did))
    audit(conn, "PICKUP", "COMMERCIAL_DELIVERIES", did, delivery["delivery_no"])
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.post("/api/commercial-deliveries/<int:did>/bill")
@roles_required("ADMIN", "MANAGER")
def bill_commercial_delivery(did):
    """The accountant's step: convert consumed weight into a credit sale +
    receivable at a price per kg. Deliberately restricted to Admin/Manager
    only (not extensible via the custom role/module permission system, same
    as other financial-generating actions) since this creates real billing."""
    d = request.get_json(force=True)
    conn = get_db()
    delivery = conn.execute("SELECT * FROM commercial_deliveries WHERE id=?", (did,)).fetchone()
    if not delivery:
        conn.close()
        return jsonify(error="Not found"), 404
    if delivery["status"] != "PICKED_UP":
        conn.close()
        return jsonify(error=f"Delivery must be picked up (weighed out) before billing — currently {delivery['status']}"), 400

    product = conn.execute("SELECT * FROM products WHERE id=?", (delivery["product_id"],)).fetchone()
    price_per_kg = d.get("price_per_kg")
    price_per_kg = float(price_per_kg) if price_per_kg is not None else float(product["price_per_kg"] or 0)
    if price_per_kg <= 0:
        conn.close()
        return jsonify(error="A positive price per kg is required (set a default on the product, or enter one now)"), 400

    cyls = conn.execute("SELECT * FROM commercial_delivery_cylinders WHERE delivery_id=?", (did,)).fetchall()
    if delivery["billing_type"] == "READING":
        total_consumption = round(delivery["current_reading"] - delivery["previous_reading"], 2)
    else:
        total_consumption = round(sum(c["consumption_weight"] or 0 for c in cyls), 2)
    if total_consumption <= 0:
        conn.close()
        return jsonify(error="Total consumption is zero — nothing to bill"), 400
    billed_amount = round(total_consumption * price_per_kg, 2)

    customer = conn.execute("SELECT * FROM customers WHERE id=?", (delivery["customer_id"],)).fetchone()
    u = session["user"]
    if delivery["billing_type"] == "READING":
        billing_desc = (f"Commercial meter billing for {delivery['delivery_no']} ({delivery['meter_label'] or 'meter'}): "
                         f"{delivery['previous_reading']} -> {delivery['current_reading']} = {total_consumption} "
                         f"@ {money_fmt(price_per_kg)}")
        item_label = f"{product['name']} (meter reading)"
    else:
        billing_desc = f"Commercial weight billing for {delivery['delivery_no']}: {total_consumption}kg @ {money_fmt(price_per_kg)}/kg"
        item_label = f"{product['name']} (weight-billed)"
    try:
        sale_no = next_number(conn, "SALE", "sales", "sale_no")
        cur = conn.execute(
            """INSERT INTO sales (sale_no, location_id, user_id, customer_id, sale_type, subtotal, discount, total, paid, balance, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (sale_no, delivery["location_id"], u["id"], delivery["customer_id"], "CREDIT",
             billed_amount, 0, billed_amount, 0, billed_amount, billing_desc),
        )
        sale_id = cur.lastrowid
        conn.execute(
            "INSERT INTO sale_items (sale_id, product_id, product_name, qty, price, subtotal, sale_mode) VALUES (?,?,?,?,?,?,?)",
            (sale_id, product["id"], item_label, total_consumption, price_per_kg, billed_amount, "REFILL"),
        )
        terms_days = customer["terms_days"] or 0
        invoice_date = datetime.now().date()
        due_date = invoice_date + timedelta(days=terms_days)
        invoice_no = next_number(conn, "INV", "receivables", "invoice_no")
        conn.execute(
            """INSERT INTO receivables (sale_id, customer_id, invoice_no, invoice_date, due_date, amount, paid, balance, status)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (sale_id, delivery["customer_id"], invoice_no, str(invoice_date), str(due_date), billed_amount, 0, billed_amount, "CURRENT"),
        )
        conn.execute(
            """UPDATE commercial_deliveries SET status='BILLED', price_per_kg=?, billed_amount=?, sale_id=?, billed_by=? WHERE id=?""",
            (price_per_kg, billed_amount, sale_id, u["id"], did),
        )
        audit(conn, "BILL", "COMMERCIAL_DELIVERIES", did,
              f"{delivery['delivery_no']} -> {sale_no}: {total_consumption}kg @ {price_per_kg}/kg = {billed_amount}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify(error=f"Could not generate invoice: {e}"), 500
    conn.close()
    return jsonify(sale_id=sale_id, sale_no=sale_no, total_consumption=total_consumption,
                    price_per_kg=price_per_kg, billed_amount=billed_amount), 201


@app.post("/api/commercial-deliveries/<int:did>/cancel")
@roles_or_module_required("ADMIN", "MANAGER", "DISPATCHER", module="commercial_deliveries")
def cancel_commercial_delivery(did):
    conn = get_db()
    delivery = conn.execute("SELECT * FROM commercial_deliveries WHERE id=?", (did,)).fetchone()
    if not delivery:
        conn.close()
        return jsonify(error="Not found"), 404
    if delivery["status"] == "BILLED":
        conn.close()
        return jsonify(error="This delivery has already been billed and can't be cancelled"), 400
    conn.execute("UPDATE commercial_deliveries SET status='CANCELLED' WHERE id=?", (did,))
    audit(conn, "CANCEL", "COMMERCIAL_DELIVERIES", did, delivery["delivery_no"])
    conn.commit()
    conn.close()
    return jsonify(ok=True)


def money_fmt(n):
    return f"\u20b1{n:,.2f}"


# ---------------------------------------------------------------- employees (Admin + Manager)

@app.get("/api/employees")
@roles_or_module_required("ADMIN", "MANAGER", module="employees")
def list_employees():
    conn = get_db()
    rows = conn.execute(
        "SELECT e.*, l.name location_name FROM employees e LEFT JOIN locations l ON l.id=e.location_id ORDER BY e.name"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/employees")
@roles_or_module_required("ADMIN", "MANAGER", module="employees")
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
@roles_or_module_required("ADMIN", "MANAGER", module="employees")
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
@roles_or_module_required("ADMIN", "MANAGER", module="employees")
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
@roles_or_module_required("ADMIN", "MANAGER", module="kpi")
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


@app.get("/order")
def public_order_page():
    return send_from_directory("static", "order.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
