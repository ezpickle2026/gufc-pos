import os
import functools
import csv
import io
import re
import sqlite3
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, session, send_from_directory, Response
from werkzeug.security import generate_password_hash, check_password_hash

from db import get_db, init_db
from reports import (xlsx_response, pdf_table_response, soa_pdf_response, commercial_billing_pdf, eod_pdf_response,
                     eod_xlsx_response, swapped_pdf_response, swapped_xlsx_response,
                     customer_growth_pdf_response, customer_growth_xlsx_response,
                     customer_kg_pdf_response, customer_kg_xlsx_response)

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


# Roles that see receivables (and sales) across every location rather than
# just their own assigned branch.
ALL_LOCATION_FINANCE_ROLES = ("ADMIN", "MANAGER", "ACCOUNTING")


def can_view_receivables_summary(conn, role):
    """Outstanding / overdue receivables totals on the dashboard are limited
    to Admin and Accounting by default (via the receivables_summary module,
    which Admin can grant to other roles in Manage Roles if ever needed)."""
    return has_module_access(conn, role, "receivables_summary")


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
    # Re-adding the SKU of a product that was deleted (archived) brings it back.
    old = conn.execute("SELECT id FROM products WHERE sku=? AND active=0", (d.get("sku"),)).fetchone()
    if old:
        conn.execute(
            "UPDATE products SET name=?, category=?, unit=?, price=?, cost=?, deposit_price=?, price_per_kg=?, min_stock=?, active=1 WHERE id=?",
            (d["name"], d.get("category", ""), d.get("unit", "cylinder"), float(d["price"]),
             float(d.get("cost") or 0), float(d.get("deposit_price") or 0), float(d.get("price_per_kg") or 0),
             int(d.get("min_stock") or 0), old["id"]),
        )
        audit(conn, "RESTORE", "PRODUCTS", old["id"], d["name"])
        conn.commit()
        conn.close()
        return jsonify(id=old["id"]), 201
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
            vals = (name, (row.get("category") or "").strip(), (row.get("unit") or "cylinder").strip(),
                    price, float(row.get("cost") or 0), float(row.get("deposit_price") or 0),
                    float(row.get("price_per_kg") or 0), int(row.get("min_stock") or 0))
            old = conn.execute("SELECT id FROM products WHERE sku=? AND active=0", (sku,)).fetchone()
            if old:  # bring back a deleted (archived) product with the same SKU
                conn.execute(
                    "UPDATE products SET name=?, category=?, unit=?, price=?, cost=?, deposit_price=?, price_per_kg=?, min_stock=?, active=1 WHERE id=?",
                    vals + (old["id"],),
                )
            else:
                conn.execute(
                    "INSERT INTO products (sku, name, category, unit, price, cost, deposit_price, price_per_kg, min_stock) VALUES (?,?,?,?,?,?,?,?,?)",
                    (sku,) + vals,
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


# Every table that keeps a record of a product being used.
PRODUCT_HISTORY_TABLES = [
    "sale_items", "inventory_movements", "empty_cylinder_movements", "swapped_empty_movements", "cylinder_deposits",
    "transfer_items", "hauling_schedule", "hauling_schedule_items",
    "commercial_deliveries", "booked_order_items",
]


@app.delete("/api/products/<int:pid>")
@roles_required("ADMIN")
def delete_product(pid):
    conn = get_db()
    prod = conn.execute("SELECT name FROM products WHERE id=?", (pid,)).fetchone()
    if not prod:
        conn.close()
        return jsonify(error="Product not found"), 404
    full = conn.execute("SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE product_id=?", (pid,)).fetchone()[0]
    empty = conn.execute("SELECT COALESCE(SUM(quantity),0) FROM empty_cylinder_inventory WHERE product_id=?", (pid,)).fetchone()[0]
    empty += conn.execute("SELECT COALESCE(SUM(quantity),0) FROM swapped_empty_inventory WHERE product_id=?", (pid,)).fetchone()[0]
    if full or empty:
        conn.close()
        return jsonify(error=(
            f"Can't delete — there are still {full} full and {empty} empty unit(s) of this product in stock. "
            "Adjust its stock to 0 first (Inventory → Adjust Stock)."
        )), 400
    has_history = any(
        conn.execute(f"SELECT 1 FROM {t} WHERE product_id=? LIMIT 1", (pid,)).fetchone()
        for t in PRODUCT_HISTORY_TABLES
    )
    if has_history:
        # Past sales/ledgers still point at it, so hide it instead of deleting.
        conn.execute("UPDATE products SET active=0 WHERE id=?", (pid,))
        conn.execute("UPDATE discounts SET active=0 WHERE product_id=?", (pid,))
        audit(conn, "ARCHIVE", "PRODUCTS", pid, prod["name"])
        conn.commit()
        conn.close()
        return jsonify(ok=True, archived=True)
    conn.execute("DELETE FROM inventory WHERE product_id=?", (pid,))
    conn.execute("DELETE FROM empty_cylinder_inventory WHERE product_id=?", (pid,))
    conn.execute("DELETE FROM swapped_empty_inventory WHERE product_id=?", (pid,))
    conn.execute("DELETE FROM discounts WHERE product_id=?", (pid,))
    conn.execute("DELETE FROM products WHERE id=?", (pid,))
    audit(conn, "DELETE", "PRODUCTS", pid, prod["name"])
    conn.commit()
    conn.close()
    return jsonify(ok=True, archived=False)


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
        """SELECT s.id, s.sale_no, s.ts, s.total, s.status, s.is_opening_balance, r.invoice_no
           FROM sales s LEFT JOIN receivables r ON r.sale_id = s.id
           WHERE s.customer_id=? AND s.status='COMPLETED' ORDER BY s.ts""",
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
        if s["is_opening_balance"]:
            events.append({"date": s["ts"], "type": "PREVIOUS ARREARS", "ref": s["invoice_no"] or s["sale_no"],
                           "debit": s["total"], "credit": 0, "opening": True})
        else:
            events.append({"date": s["ts"], "type": "SALE", "ref": s["sale_no"], "debit": s["total"], "credit": 0})
    for p in payments:
        events.append({"date": p["ts"], "type": "PAYMENT", "ref": f"{p['sale_no']} ({p['method']})", "debit": 0, "credit": p["amount"]})
    events.sort(key=lambda e: e["date"])

    beginning_balance = 0.0
    in_range_events = []
    for e in events:
        before_range = date_from and e["date"] < date_from
        after_range = date_to and e["date"] > date_to + " 23:59:59"
        # Uploaded previous arrears always roll into the Beginning Balance
        # (as long as they're dated within the statement period or earlier),
        # rather than appearing as a charge line on the statement.
        if e.get("opening") and not after_range:
            beginning_balance += e["debit"]
            continue
        if before_range:
            beginning_balance += e["debit"] - e["credit"]
        elif not after_range:
            in_range_events.append(e)

    running = round(beginning_balance, 2)
    out = []
    for e in in_range_events:
        running = round(running + e["debit"] - e["credit"], 2)
        out.append({**{k: v for k, v in e.items() if k != "opening"}, "balance": running})
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
@roles_required("ADMIN", "ATTENDANT", "ACCOUNTING")
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
    stock = request.args.get("stock")
    table = {"EMPTY": "empty_cylinder_movements", "SWAPPED": "swapped_empty_movements"}.get(stock, "inventory_movements")
    conn = get_db()
    sql = f"SELECT m.*, p.name product_name, l.name location_name FROM {table} m " \
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


@app.get("/api/inventory/swapped-empties")
@login_required
def get_swapped_empties():
    """Other-brand empties taken in on swap sales, per location / brand / size."""
    u = session["user"]
    location_id = request.args.get("location_id", type=int)
    if u["role"] not in ("ADMIN", "MANAGER") and location_id and location_id != u["location_id"]:
        return jsonify(error="Forbidden: not your location"), 403
    if not location_id and u["role"] not in ("ADMIN", "MANAGER"):
        location_id = u["location_id"]
    conn = get_db()
    sql = """SELECT l.name location_name, l.id location_id, p.name product_name, p.id product_id,
                     s.brand, s.quantity
              FROM swapped_empty_inventory s JOIN locations l ON l.id=s.location_id
              JOIN products p ON p.id=s.product_id WHERE s.quantity<>0"""
    params = []
    if location_id:
        sql += " AND l.id=?"; params.append(location_id)
    sql += " ORDER BY l.name, s.brand, p.name"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/inventory/swapped-empties/adjust")
@roles_required("ADMIN")
def adjust_swapped_empties():
    """Manual correction of other-brand empty counts. Logged to the ledger."""
    d = request.get_json(force=True)
    location_id = d.get("location_id")
    product_id = d.get("product_id")
    brand = (d.get("brand") or "").strip()
    reason = (d.get("reason") or "").strip()
    if not location_id or not product_id or not brand or d.get("delta") is None:
        return jsonify(error="location_id, product_id, brand and delta are required"), 400
    try:
        delta = int(d.get("delta"))
    except (TypeError, ValueError):
        return jsonify(error="delta must be a whole number (positive to add, negative to remove)"), 400
    if delta == 0:
        return jsonify(error="delta cannot be zero"), 400
    if not reason:
        return jsonify(error="A reason is required for adjustments"), 400
    conn = get_db()
    try:
        if delta > 0:
            add_swapped_empties(conn, location_id, product_id, brand, delta, "ADJUSTMENT", "MANUAL-ADMIN", reason)
        else:
            remove_swapped_empties(conn, location_id, product_id, brand, -delta, "ADJUSTMENT", "MANUAL-ADMIN", reason)
        audit(conn, "ADJUST", "SWAPPED EMPTIES", f"{location_id}:{product_id}:{brand}", f"delta={delta} reason={reason}")
        conn.commit()
    except ValueError as e:
        conn.rollback()
        conn.close()
        return jsonify(error=str(e)), 400
    conn.close()
    return jsonify(ok=True)


@app.post("/api/inventory/empties/adjust")
@roles_required("ADMIN")
def adjust_empty_cylinders():
    """Manual correction of empty-cylinder counts (physical count, damaged
    cylinders written off, etc.). Logged to the empty-cylinder ledger."""
    d = request.get_json(force=True)
    location_id = d.get("location_id")
    product_id = d.get("product_id")
    reason = (d.get("reason") or "").strip()
    if not location_id or not product_id or d.get("delta") is None:
        return jsonify(error="location_id, product_id and delta are required"), 400
    try:
        delta = int(d.get("delta"))
    except (TypeError, ValueError):
        return jsonify(error="delta must be a whole number (positive to add, negative to remove)"), 400
    if delta == 0:
        return jsonify(error="delta cannot be zero"), 400
    if not reason:
        return jsonify(error="A reason is required for empty cylinder adjustments"), 400

    conn = get_db()
    try:
        if delta > 0:
            add_empty_cylinders(conn, location_id, product_id, delta, "ADJUSTMENT", "MANUAL-ADMIN", reason)
        else:
            remove_empty_cylinders(conn, location_id, product_id, -delta, "ADJUSTMENT", "MANUAL-ADMIN", reason)
        audit(conn, "ADJUST", "EMPTY CYLINDERS", f"{location_id}:{product_id}", f"delta={delta} reason={reason}")
        conn.commit()
    except ValueError as e:
        conn.rollback()
        conn.close()
        return jsonify(error=str(e)), 400
    conn.close()
    return jsonify(ok=True)


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
    add_empty_cylinders(conn, location_id, product_id, qty, "DEPOSIT RETURN", f"CUST-{customer_id}",
                        notes=f"Cylinder returned by customer {customer_id}")
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


def _log_empty_movement(conn, location_id, product_id, qty_change, mtype, reference, notes):
    conn.execute(
        "INSERT INTO empty_cylinder_movements (location_id, product_id, qty_change, type, reference, user_id, notes) VALUES (?,?,?,?,?,?,?)",
        (location_id, product_id, qty_change, mtype, reference,
         session["user"]["id"] if session.get("user") else None, notes),
    )


def add_empty_cylinders(conn, location_id, product_id, qty, mtype="RETURNED", reference=None, notes=None):
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
    _log_empty_movement(conn, location_id, product_id, qty, mtype, reference, notes)


def remove_empty_cylinders(conn, location_id, product_id, qty, mtype="REMOVED", reference=None, notes=None,
                           allow_negative=False):
    row = conn.execute(
        "SELECT quantity FROM empty_cylinder_inventory WHERE location_id=? AND product_id=?",
        (location_id, product_id),
    ).fetchone()
    current = row["quantity"] if row else 0
    if qty > current and not allow_negative:
        raise ValueError(f"Only {current} empty cylinders on hand at this location")
    if row:
        conn.execute("UPDATE empty_cylinder_inventory SET quantity=quantity-? WHERE location_id=? AND product_id=?",
                     (qty, location_id, product_id))
    else:
        conn.execute("INSERT INTO empty_cylinder_inventory (location_id, product_id, quantity) VALUES (?,?,?)",
                     (location_id, product_id, -qty))
    _log_empty_movement(conn, location_id, product_id, -qty, mtype, reference, notes)


def _change_swapped_empties(conn, location_id, product_id, brand, delta, mtype, reference, notes,
                            allow_negative=False):
    row = conn.execute(
        "SELECT quantity FROM swapped_empty_inventory WHERE location_id=? AND product_id=? AND brand=?",
        (location_id, product_id, brand),
    ).fetchone()
    current = row["quantity"] if row else 0
    if delta < 0 and -delta > current and not allow_negative:
        raise ValueError(f"Only {current} swapped {brand} empties on hand at this location")
    if row:
        conn.execute("UPDATE swapped_empty_inventory SET quantity=quantity+? WHERE location_id=? AND product_id=? AND brand=?",
                     (delta, location_id, product_id, brand))
    else:
        conn.execute("INSERT INTO swapped_empty_inventory (location_id, product_id, brand, quantity) VALUES (?,?,?,?)",
                     (location_id, product_id, brand, delta))
    conn.execute(
        """INSERT INTO swapped_empty_movements (location_id, product_id, brand, qty_change, type, reference, user_id, notes)
           VALUES (?,?,?,?,?,?,?,?)""",
        (location_id, product_id, brand, delta, mtype, reference,
         session["user"]["id"] if session.get("user") else None, notes),
    )


def add_swapped_empties(conn, location_id, product_id, brand, qty, mtype, reference=None, notes=None):
    _change_swapped_empties(conn, location_id, product_id, brand, qty, mtype, reference, notes)


def remove_swapped_empties(conn, location_id, product_id, brand, qty, mtype, reference=None, notes=None,
                           allow_negative=False):
    _change_swapped_empties(conn, location_id, product_id, brand, -qty, mtype, reference, notes, allow_negative)


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


# ---------------------------------------------------------------- discounts & promos
# Discounts are per item: each cart line can optionally pick one preset
# discount. PERCENT = % off the line's gas price; FIXED = peso amount off per
# unit. Discounts never reduce the cylinder deposit fee on DEPOSIT sales.

def _discount_is_live(row, today=None):
    today = today or datetime.now().date().isoformat()
    if not row["active"]:
        return False
    if row["start_date"] and today < row["start_date"]:
        return False
    if row["end_date"] and today > row["end_date"]:
        return False
    return True


def compute_item_discount(disc, product_id, unit_gas_price, qty):
    """Server-side discount amount for one sale line. Raises ValueError if
    the discount can't be applied to this line."""
    if not _discount_is_live(disc):
        raise ValueError(f"Discount '{disc['name']}' is not active today")
    if disc["product_id"] and int(disc["product_id"]) != int(product_id):
        raise ValueError(f"Discount '{disc['name']}' does not apply to this product")
    if qty < (disc["min_qty"] or 1):
        raise ValueError(f"Discount '{disc['name']}' needs at least {disc['min_qty']} qty")
    gas_total = unit_gas_price * qty
    if disc["type"] == "PERCENT":
        amount = gas_total * float(disc["value"]) / 100.0
    else:
        amount = float(disc["value"]) * qty
    return round(min(max(amount, 0), gas_total), 2)


def _discount_payload(d):
    name = (d.get("name") or "").strip()
    if not name:
        raise ValueError("Name is required")
    dtype = (d.get("type") or "").upper()
    if dtype not in ("PERCENT", "FIXED"):
        raise ValueError("Type must be PERCENT or FIXED")
    value = float(d.get("value") or 0)
    if value <= 0:
        raise ValueError("Value must be greater than zero")
    if dtype == "PERCENT" and value > 100:
        raise ValueError("Percent discount cannot exceed 100")
    product_id = d.get("product_id") or None
    min_qty = int(d.get("min_qty") or 1)
    if min_qty < 1:
        raise ValueError("Minimum qty must be at least 1")
    start = (d.get("start_date") or "").strip() or None
    end = (d.get("end_date") or "").strip() or None
    for v in (start, end):
        if v:
            datetime.strptime(v, "%Y-%m-%d")
    if start and end and end < start:
        raise ValueError("End date is before start date")
    active = 1 if d.get("active", True) in (True, 1, "1", "true") else 0
    return (name, dtype, value, product_id, min_qty, start, end, active, (d.get("notes") or "").strip())


@app.get("/api/discounts")
@login_required
def list_discounts():
    """?live=1 returns only discounts usable today (for the POS)."""
    conn = get_db()
    rows = conn.execute(
        """SELECT dc.*, p.name product_name FROM discounts dc
           LEFT JOIN products p ON p.id=dc.product_id ORDER BY dc.active DESC, dc.name"""
    ).fetchall()
    conn.close()
    out = [dict(r) for r in rows]
    if request.args.get("live"):
        out = [r for r in out if _discount_is_live(r)]
    return jsonify(out)


@app.post("/api/discounts")
@roles_required("ADMIN", "MANAGER")
def create_discount():
    try:
        vals = _discount_payload(request.get_json(force=True))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO discounts (name, type, value, product_id, min_qty, start_date, end_date, active, notes)
           VALUES (?,?,?,?,?,?,?,?,?)""", vals)
    audit(conn, "CREATE", "DISCOUNTS", cur.lastrowid, f"{vals[0]} {vals[1]} {vals[2]}")
    conn.commit()
    conn.close()
    return jsonify(id=cur.lastrowid), 201


@app.put("/api/discounts/<int:did>")
@roles_required("ADMIN", "MANAGER")
def update_discount(did):
    try:
        vals = _discount_payload(request.get_json(force=True))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    conn = get_db()
    cur = conn.execute(
        """UPDATE discounts SET name=?, type=?, value=?, product_id=?, min_qty=?, start_date=?, end_date=?,
           active=?, notes=? WHERE id=?""", vals + (did,))
    if not cur.rowcount:
        conn.close()
        return jsonify(error="Discount not found"), 404
    audit(conn, "UPDATE", "DISCOUNTS", did, f"{vals[0]} {vals[1]} {vals[2]} active={vals[7]}")
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.delete("/api/discounts/<int:did>")
@roles_required("ADMIN", "MANAGER")
def delete_discount(did):
    conn = get_db()
    row = conn.execute("SELECT name FROM discounts WHERE id=?", (did,)).fetchone()
    if not row:
        conn.close()
        return jsonify(error="Discount not found"), 404
    # Past sales keep their own copy of the discount name/amount, so deleting is safe.
    conn.execute("DELETE FROM discounts WHERE id=?", (did,))
    audit(conn, "DELETE", "DISCOUNTS", did, row["name"])
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------- system reset (go live)
#
# Used once, when testing is over and the client starts real operations.
# Always wipes every transaction and zeroes stock; setup data (locations,
# products, roles, customer categories) is kept so nothing has to be
# re-encoded. Customers, other user accounts, employees/vehicles and
# discounts can optionally be wiped too. A full copy of the database is
# saved next to it before anything is deleted.

# Child tables first so foreign keys never block a delete.
RESET_TRANSACTION_TABLES = [
    "payments", "receivables", "sale_items",
    "commercial_delivery_cylinders", "commercial_deliveries",
    "sales",
    "inventory_movements", "empty_cylinder_movements", "swapped_empty_movements", "cylinder_deposits",
    "transfer_items", "transfers",
    "hauling_schedule_items", "hauling_schedule",
    "booked_order_items", "booked_orders",
    "incident_report_attachments", "incident_reports",
    "staff_schedule",
    "audit_logs",
]


def _reset_counts(conn):
    def c(sql):
        return conn.execute(sql).fetchone()[0]
    me = session["user"]["id"]
    return {
        "sales": c("SELECT COUNT(*) FROM sales"),
        "payments": c("SELECT COUNT(*) FROM payments"),
        "receivables": c("SELECT COUNT(*) FROM receivables"),
        "transfers": c("SELECT COUNT(*) FROM transfers"),
        "inventory_movements": c("SELECT COUNT(*) FROM inventory_movements"),
        "stock_on_hand": c("SELECT COALESCE(SUM(quantity),0) FROM inventory"),
        "empties_on_hand": c("SELECT COALESCE(SUM(quantity),0) FROM empty_cylinder_inventory")
                           + c("SELECT COALESCE(SUM(quantity),0) FROM swapped_empty_inventory"),
        "booked_orders": c("SELECT COUNT(*) FROM booked_orders"),
        "commercial_deliveries": c("SELECT COUNT(*) FROM commercial_deliveries"),
        "hauling": c("SELECT COUNT(*) FROM hauling_schedule"),
        "incident_reports": c("SELECT COUNT(*) FROM incident_reports"),
        "staff_schedule": c("SELECT COUNT(*) FROM staff_schedule"),
        "audit_logs": c("SELECT COUNT(*) FROM audit_logs"),
        "customers": c("SELECT COUNT(*) FROM customers"),
        "other_users": conn.execute("SELECT COUNT(*) FROM users WHERE id<>?", (me,)).fetchone()[0],
        "employees": c("SELECT COUNT(*) FROM employees"),
        "vehicles": c("SELECT COUNT(*) FROM vehicles"),
        "discounts": c("SELECT COUNT(*) FROM discounts"),
        "products": c("SELECT COUNT(*) FROM products"),
        "locations": c("SELECT COUNT(*) FROM locations"),
    }


def _backup_database(dest_path):
    """Consistent copy of the live DB (safe even while other requests run)."""
    from db import DB_PATH
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


@app.get("/api/admin/reset-summary")
@roles_required("ADMIN")
def reset_summary():
    conn = get_db()
    counts = _reset_counts(conn)
    conn.close()
    return jsonify(counts)


@app.get("/api/admin/backup")
@roles_required("ADMIN")
def download_backup():
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _backup_database(tmp)
        with open(tmp, "rb") as f:
            data = f.read()
    finally:
        os.remove(tmp)
    fname = f"gufc-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    return Response(data, mimetype="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.post("/api/admin/reset")
@roles_required("ADMIN")
def system_reset():
    from db import DB_PATH
    d = request.get_json(force=True) or {}
    if (d.get("confirm_text") or "").strip().upper() != "RESET":
        return jsonify(error='Type RESET in the confirmation box to continue'), 400
    opts = d.get("options") or {}
    wipe_customers = bool(opts.get("customers"))
    wipe_users = bool(opts.get("users"))
    wipe_staff_fleet = bool(opts.get("employees_vehicles"))
    wipe_discounts = bool(opts.get("discounts"))
    wipe_products = bool(opts.get("products"))

    me = session["user"]
    conn = get_db()
    row = conn.execute("SELECT password_hash FROM users WHERE id=?", (me["id"],)).fetchone()
    if not row or not check_password_hash(row["password_hash"], d.get("password") or ""):
        conn.close()
        return jsonify(error="Incorrect admin password"), 403

    before = _reset_counts(conn)
    conn.close()

    # Safety net: full copy of the database before anything is deleted.
    backup_path = f"{DB_PATH}.before-reset-{datetime.now().strftime('%Y%m%d-%H%M%S')}.bak"
    try:
        _backup_database(backup_path)
    except Exception as e:  # never wipe data if we couldn't back it up
        return jsonify(error=f"Could not create a backup, nothing was deleted: {e}"), 500

    conn = get_db()
    try:
        conn.execute("BEGIN")
        for t in RESET_TRANSACTION_TABLES:
            conn.execute(f"DELETE FROM {t}")
        # Stock starts at zero; real opening stock is encoded via Inventory → Adjust Stock.
        conn.execute("UPDATE inventory SET quantity=0")
        conn.execute("UPDATE empty_cylinder_inventory SET quantity=0")
        conn.execute("DELETE FROM swapped_empty_inventory")

        if wipe_staff_fleet:
            conn.execute("DELETE FROM employees")
            conn.execute("DELETE FROM vehicles")
        else:
            conn.execute("UPDATE vehicles SET status='AVAILABLE' WHERE status IN ('DISPATCHED','IN TRANSIT')")

        if wipe_users:
            conn.execute("UPDATE employees SET linked_user_id=NULL WHERE linked_user_id<>?", (me["id"],))
            conn.execute("DELETE FROM users WHERE id<>?", (me["id"],))

        if wipe_customers:
            conn.execute("DELETE FROM customers")

        if wipe_discounts:
            conn.execute("DELETE FROM discounts")

        if wipe_products:
            # All history pointing at products was cleared above.
            conn.execute("DELETE FROM inventory")
            conn.execute("DELETE FROM empty_cylinder_inventory")
            conn.execute("DELETE FROM discounts WHERE product_id IS NOT NULL")
            conn.execute("DELETE FROM products")

        # Restart auto-number counters for every emptied table.
        emptied = list(RESET_TRANSACTION_TABLES)
        if wipe_staff_fleet:
            emptied += ["employees", "vehicles"]
        if wipe_customers:
            emptied.append("customers")
        if wipe_discounts:
            emptied.append("discounts")
        if wipe_products:
            emptied.append("products")
        conn.executemany("DELETE FROM sqlite_sequence WHERE name=?", [(t,) for t in emptied])

        wiped = ["transactions", "stock"]
        if wipe_customers:
            wiped.append("customers")
        if wipe_users:
            wiped.append("other users")
        if wipe_staff_fleet:
            wiped.append("employees & vehicles")
        if wipe_discounts:
            wiped.append("discounts")
        if wipe_products:
            wiped.append("products")
        audit(conn, "RESET", "SYSTEM", None,
              f"Go-live reset: cleared {', '.join(wiped)}. "
              f"Before: {before['sales']} sales, {before['transfers']} transfers, "
              f"{before['customers']} customers. Backup: {os.path.basename(backup_path)}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify(error=f"Reset failed, nothing was changed: {e}"), 500
    conn.close()

    try:  # reclaim disk space from the deleted rows (non-critical)
        v = get_db()
        v.isolation_level = None
        v.execute("VACUUM")
        v.close()
    except Exception:
        pass

    return jsonify(ok=True, backup_file=os.path.basename(backup_path), before=before)


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
            if sale_mode not in ("REFILL", "DEPOSIT", "SWAP"):
                raise ValueError("sale_mode must be REFILL, DEPOSIT or SWAP")
            # server-side price, never trust frontend: REFILL is gas only;
            # DEPOSIT adds the cylinder deposit fee (customer keeps the tank);
            # SWAP adds a per-cylinder swap fee (customer hands over an empty
            # of another brand in exchange).
            gas_price = float(prod["price"])
            price = gas_price
            swap_brand, swap_fee = None, 0.0
            if sale_mode == "DEPOSIT":
                price += float(prod["deposit_price"])
            elif sale_mode == "SWAP":
                swap_brand = (it.get("swap_brand") or "").strip()
                if not swap_brand:
                    raise ValueError(f"Choose the LPG brand being swapped for {prod['name']}")
                try:
                    swap_fee = round(float(it.get("swap_fee") or 0), 2)
                except (TypeError, ValueError):
                    raise ValueError("Swap fee must be a number")
                if swap_fee < 0:
                    raise ValueError("Swap fee cannot be negative")
                price += swap_fee
            line_total = round(price * qty, 2)
            subtotal += line_total
            # optional per-item discount, recomputed server-side
            disc_id, disc_name, disc_amount = None, None, 0.0
            if it.get("discount_id"):
                disc = conn.execute("SELECT * FROM discounts WHERE id=?", (it["discount_id"],)).fetchone()
                if not disc:
                    raise ValueError("Selected discount no longer exists")
                disc_amount = compute_item_discount(disc, prod["id"], gas_price, qty)
                disc_id, disc_name = disc["id"], disc["name"]
            resolved_items.append((prod, qty, price, line_total, sale_mode, disc_id, disc_name, disc_amount,
                                   swap_brand, swap_fee))

        subtotal = round(subtotal, 2)
        discount = round(sum(r[7] for r in resolved_items), 2)
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
        # Credit / terms sales get a Delivery Receipt number the customer signs against.
        dr_no = next_number(conn, "DR", "sales", "dr_no") if sale_type == "CREDIT" else None

        cur = conn.execute(
            """INSERT INTO sales (sale_no, location_id, user_id, customer_id, sale_type, subtotal, discount, total, paid, balance, notes, dr_no)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sale_no, location_id, u["id"], customer_id, sale_type, subtotal, discount, total,
             paid_total, balance, d.get("notes", ""), dr_no),
        )
        sale_id = cur.lastrowid

        for (prod, qty, price, line_total, sale_mode, disc_id, disc_name, disc_amount,
             swap_brand, swap_fee) in resolved_items:
            conn.execute(
                """INSERT INTO sale_items (sale_id, product_id, product_name, qty, price, subtotal, sale_mode,
                   discount_id, discount_name, discount_amount, swap_brand, swap_fee) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sale_id, prod["id"], prod["name"], qty, price, line_total, sale_mode,
                 disc_id, disc_name, disc_amount, swap_brand, swap_fee),
            )
            mode_note = f"SWAP from {swap_brand}" if sale_mode == "SWAP" else sale_mode
            adjust_inventory(conn, location_id, prod["id"], -qty, "SALE", sale_no,
                              notes=f"Sold via {sale_no} ({mode_note})")
            if sale_mode == "REFILL":
                # Customer exchanges an empty cylinder for a full one — the empty
                # comes back into our stock, ready to be hauled to the plant for refill.
                add_empty_cylinders(conn, location_id, prod["id"], qty, "REFILL SALE",
                                    reference=sale_no, notes=f"Empty returned on refill sale {sale_no}")
            elif sale_mode == "SWAP":
                # SWAP: the customer hands over an empty of a different brand.
                # It's kept apart from our own empties, counted per brand.
                add_swapped_empties(conn, location_id, prod["id"], swap_brand, qty, "SWAP SALE",
                                    reference=sale_no, notes=f"{swap_brand} empty taken in on swap sale {sale_no}")
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
        """SELECT s.*, l.name location_name, l.address location_address, u.name cashier_name,
                  c.name customer_name, c.account_no, c.business_name customer_business_name,
                  c.address customer_address, c.phone customer_phone, c.terms_days customer_terms_days,
                  r.invoice_no, r.due_date
           FROM sales s JOIN locations l ON l.id=s.location_id JOIN users u ON u.id=s.user_id
           LEFT JOIN customers c ON c.id=s.customer_id
           LEFT JOIN receivables r ON r.sale_id=s.id WHERE s.id=?""", (sale_id,)
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
              LEFT JOIN customers c ON c.id=s.customer_id WHERE s.is_opening_balance=0"""
    params = []
    if u["role"] not in ("ADMIN", "ACCOUNTING"):
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
    void_ref = f"VOID-{sale['sale_no']}"
    for it in items:
        adjust_inventory(conn, sale["location_id"], it["product_id"], it["qty"], "ADJUSTMENT",
                          void_ref, notes="Reversal of voided sale", allow_negative=True)
        if it["sale_mode"] == "REFILL":
            # The refill sale took in an empty in exchange — take it back out.
            # Allowed to go below zero (like the full-stock reversal above) in
            # case the empty was already dispatched; a negative count flags it.
            remove_empty_cylinders(conn, sale["location_id"], it["product_id"], it["qty"], "VOID REVERSAL",
                                   void_ref, notes="Empty returned on voided refill sale", allow_negative=True)
        elif it["sale_mode"] == "SWAP" and it["swap_brand"]:
            remove_swapped_empties(conn, sale["location_id"], it["product_id"], it["swap_brand"], it["qty"],
                                   "VOID REVERSAL", void_ref, notes="Swapped empty reversed on voided sale",
                                   allow_negative=True)
        elif it["sale_mode"] == "DEPOSIT" and sale["customer_id"]:
            # DEPOSIT sale put cylinders on loan to the customer — undo that,
            # without going below zero if some were already returned.
            conn.execute(
                "UPDATE cylinder_deposits SET qty_on_loan = MAX(qty_on_loan - ?, 0) WHERE customer_id=? AND product_id=?",
                (it["qty"], sale["customer_id"], it["product_id"]),
            )
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
    sql = """SELECT r.*, c.name customer_name, c.account_no, s.sale_no, s.location_id, s.is_opening_balance
              FROM receivables r JOIN customers c ON c.id=r.customer_id JOIN sales s ON s.id=r.sale_id
              WHERE 1=1"""
    params = []
    if u["role"] not in ALL_LOCATION_FINANCE_ROLES:
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

    sql = """SELECT r.balance, r.due_date, c.id customer_id, c.name customer_name, c.account_no,
                    c.category customer_category, s.location_id
              FROM receivables r JOIN customers c ON c.id=r.customer_id JOIN sales s ON s.id=r.sale_id
              WHERE r.balance > 0.009"""
    params = []
    if u["role"] not in ALL_LOCATION_FINANCE_ROLES:
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
                                 "customer_category": r["customer_category"] or "",
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

    headers = ["Customer", "Account #", "Category", "Current", "1-30 Days", "31-60 Days", "61-90 Days", "91-120 Days", "120+ Days", "Total"]
    data = [(r["customer_name"], r["account_no"], r["customer_category"], r["current"], r["days_1_30"], r["days_31_60"],
              r["days_61_90"], r["days_91_120"], r["days_120_plus"], r["total"]) for r in customer_rows]
    data.append(("TOTAL", "", "", totals["current"], totals["days_1_30"], totals["days_31_60"],
                 totals["days_61_90"], totals["days_91_120"], totals["days_120_plus"], grand_total))
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    if fmt == "pdf":
        return pdf_table_response("Accounts Receivable Aging", headers, data, f"receivables-aging-{stamp}.pdf",
                                   subtitle=f"As of {datetime.now().strftime('%Y-%m-%d')}",
                                   col_widths=[110, 60, 60, 60, 60, 60, 60, 60, 60, 65], landscape_mode=True)
    return xlsx_response(headers, data, f"receivables-aging-{stamp}.xlsx", "AR Aging")


@app.post("/api/receivables/<int:rid>/pay")
@roles_required("ADMIN", "MANAGER", "ATTENDANT", "ACCOUNTING")
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
    if u["role"] not in ALL_LOCATION_FINANCE_ROLES and r["sale_location_id"] != u["location_id"]:
        conn.close()
        return jsonify(error="Forbidden: this receivable belongs to another location"), 403
    if amount <= 0 or amount > r["balance"] + 0.009:
        conn.close()
        return jsonify(error=f"Payment must be > 0 and <= outstanding balance ({r['balance']:.2f})"), 400
    new_paid = round(r["paid"] + amount, 2)
    new_balance = round(r["balance"] - amount, 2)
    conn.execute("UPDATE receivables SET paid=?, balance=? WHERE id=?", (new_paid, new_balance, rid))
    conn.execute(
        "INSERT INTO payments (sale_id, method, amount, reference, user_id, payment_source) VALUES (?,?,?,?,?,'AR_COLLECTION')",
        (r["sale_id"], method, amount, d.get("reference") or f"AR-{r['invoice_no']}", session["user"]["id"]),
    )
    conn.execute("UPDATE sales SET paid = paid + ?, balance = balance - ? WHERE id=?",
                 (amount, amount, r["sale_id"]))
    recompute_receivable_status(conn, rid)
    audit(conn, "PAYMENT", "RECEIVABLES", rid, f"{r['invoice_no']} +{amount} via {method}")
    conn.commit()
    conn.close()
    return jsonify(ok=True, paid=new_paid, balance=new_balance)


# ---------------------------------------------------------------- previous arrears (opening balances)
# Lets Admin/Accounting load a customer's unpaid balance from before this
# system went live. Each one is stored as a zero-item "opening balance" credit
# sale + receivable, so it flows through Receivables, payments, the Aging
# Report and the SOA exactly like any other invoice — but it's excluded from
# sales totals/reports (it isn't a sale made in this system), and on the SOA
# it rolls into the Beginning Balance instead of showing as a charge.

ARREARS_ROLES = ("ADMIN", "ACCOUNTING")


def _parse_date(value, field):
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a date in YYYY-MM-DD format")


def _create_opening_balance(conn, customer, location_id, amount, as_of, due=None, reference=None, notes=None):
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        raise ValueError("amount must be a number")
    if amount <= 0:
        raise ValueError("amount must be greater than zero")
    as_of_date = _parse_date(as_of, "as_of_date")
    if as_of_date > datetime.now().date():
        raise ValueError("as_of_date can't be in the future")
    due_date = _parse_date(due, "due_date") if due else as_of_date + timedelta(days=customer["terms_days"] or 0)
    if due_date < as_of_date:
        raise ValueError("due_date can't be before as_of_date")
    if not conn.execute("SELECT 1 FROM locations WHERE id=?", (location_id,)).fetchone():
        raise ValueError("Unknown location")

    reference = (reference or "").strip()
    if reference:
        if conn.execute("SELECT 1 FROM receivables WHERE invoice_no=?", (reference,)).fetchone():
            raise ValueError(f"Invoice/reference '{reference}' already exists")
        invoice_no = reference
    else:
        invoice_no = next_number(conn, "OB", "receivables", "invoice_no")

    sale_no = next_number(conn, "OB", "sales", "sale_no")
    note_text = "Previous arrears (opening balance)" + (f" — {notes.strip()}" if notes and notes.strip() else "")
    cur = conn.execute(
        """INSERT INTO sales (sale_no, ts, location_id, user_id, customer_id, sale_type, subtotal, discount, total,
                              paid, balance, notes, is_opening_balance)
           VALUES (?,?,?,?,?,'CREDIT',?,0,?,0,?,?,1)""",
        (sale_no, f"{as_of_date} 00:00:00", location_id, session["user"]["id"], customer["id"],
         amount, amount, amount, note_text),
    )
    sale_id = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO receivables (sale_id, customer_id, invoice_no, invoice_date, due_date, amount, paid, balance, status)
           VALUES (?,?,?,?,?,?,0,?,'CURRENT')""",
        (sale_id, customer["id"], invoice_no, str(as_of_date), str(due_date), amount, amount),
    )
    rid = cur.lastrowid
    recompute_receivable_status(conn, rid)
    audit(conn, "CREATE", "RECEIVABLES", rid,
          f"Previous arrears {invoice_no} for {customer['account_no']}: {amount:.2f} as of {as_of_date}")
    return rid, invoice_no


@app.post("/api/receivables/opening-balance")
@roles_required(*ARREARS_ROLES)
def create_opening_balance():
    d = request.get_json(force=True)
    conn = get_db()
    try:
        customer = conn.execute("SELECT * FROM customers WHERE id=?", (d.get("customer_id"),)).fetchone()
        if not customer:
            raise ValueError("Choose a customer")
        rid, invoice_no = _create_opening_balance(
            conn, customer, d.get("location_id"), d.get("amount"), d.get("as_of_date"),
            d.get("due_date"), d.get("reference"), d.get("notes"))
        conn.commit()
    except ValueError as e:
        conn.rollback(); conn.close()
        return jsonify(error=str(e)), 400
    conn.close()
    return jsonify(id=rid, invoice_no=invoice_no), 201


@app.get("/api/receivables/opening-balance/template")
@roles_required(*ARREARS_ROLES)
def opening_balance_template():
    csv_text = ("account_no,amount,as_of_date,due_date,reference,notes\n"
                "ACC-00001,15000.00,2026-09-30,2026-10-30,OLD-INV-1234,Unpaid balance from old system\n")
    return Response(csv_text, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=previous-arrears-template.csv"})


@app.post("/api/receivables/opening-balance/import")
@roles_required(*ARREARS_ROLES)
def import_opening_balances():
    """All-or-nothing: if any row has an error, nothing is saved, so a
    half-imported file can't leave balances doubled up on a retry."""
    file = request.files.get("file")
    location_id = request.form.get("location_id", type=int)
    if not file:
        return jsonify(error="No file uploaded"), 400
    if not location_id:
        return jsonify(error="Choose the location these arrears belong to"), 400
    try:
        reader = csv.DictReader(io.StringIO(decode_csv_upload(file.read())))
    except Exception as e:
        return jsonify(error=f"Could not read CSV: {e}"), 400

    conn = get_db()
    created, errors, total = 0, [], 0.0
    for i, row in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if not any(row.values()):
            continue
        try:
            account_no = row.get("account_no", "")
            if not account_no:
                raise ValueError("account_no is required")
            customer = conn.execute("SELECT * FROM customers WHERE account_no=?", (account_no,)).fetchone()
            if not customer:
                raise ValueError(f"no customer with account_no '{account_no}'")
            amount = (row.get("amount") or "").replace(",", "")
            _create_opening_balance(conn, customer, location_id, amount, row.get("as_of_date"),
                                    row.get("due_date") or None, row.get("reference"), row.get("notes"))
            created += 1
            total += float(amount)
        except ValueError as e:
            errors.append(f"Row {i}: {e}")
    if errors:
        conn.rollback()
        conn.close()
        return jsonify(created=0, errors=errors, total=0), 400
    if created:
        audit(conn, "IMPORT", "RECEIVABLES", None, f"{created} previous-arrears rows imported, total {total:.2f}")
        conn.commit()
    conn.close()
    return jsonify(created=created, errors=[], total=round(total, 2))


@app.delete("/api/receivables/<int:rid>/opening-balance")
@roles_required(*ARREARS_ROLES)
def delete_opening_balance(rid):
    """Undo a mistaken arrears upload. Only allowed while nothing has been paid on it."""
    conn = get_db()
    r = conn.execute(
        "SELECT r.*, s.is_opening_balance FROM receivables r JOIN sales s ON s.id=r.sale_id WHERE r.id=?", (rid,)
    ).fetchone()
    if not r:
        conn.close(); return jsonify(error="Not found"), 404
    if not r["is_opening_balance"]:
        conn.close(); return jsonify(error="Only previous-arrears entries can be removed here"), 400
    if r["paid"] > 0 or conn.execute("SELECT 1 FROM payments WHERE sale_id=?", (r["sale_id"],)).fetchone():
        conn.close(); return jsonify(error="Payments have already been recorded against this — it can't be removed"), 400
    conn.execute("DELETE FROM receivables WHERE id=?", (rid,))
    conn.execute("DELETE FROM sales WHERE id=?", (r["sale_id"],))
    audit(conn, "DELETE", "RECEIVABLES", rid, f"Removed previous arrears {r['invoice_no']} ({r['amount']:.2f})")
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------- transfers (dispatch-lite)

@app.get("/api/transfers")
@roles_or_module_required("ADMIN", "DISPATCHER", module="transfers")
def list_transfers():
    conn = get_db()
    rows = conn.execute(
        """SELECT t.*, lf.name from_name,
                  COALESCE(lt.name, 'Plant: ' || COALESCE(t.to_external, '')) to_name
           FROM transfers t
           JOIN locations lf ON lf.id=t.from_location LEFT JOIN locations lt ON lt.id=t.to_location
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
    """Dispatch full, empty and/or swapped (other-brand) empty cylinders from
    one location to another location, or out to a plant (to_external)."""
    d = request.get_json(force=True)
    items = d.get("items") or []
    if not items:
        return jsonify(error="No items to transfer"), 400
    from_location = d.get("from_location")
    to_location = d.get("to_location") or None
    to_external = (d.get("to_external") or "").strip() or None
    if not from_location:
        return jsonify(error="Origin location is required"), 400
    if not to_location and not to_external:
        return jsonify(error="Choose a destination location or enter the plant name"), 400
    if to_location and str(to_location) == str(from_location):
        return jsonify(error="Origin and destination must differ"), 400
    if to_location:
        to_external = None

    conn = get_db()
    try:
        transfer_no = next_number(conn, "DISP", "transfers", "transfer_no")
        cur = conn.execute(
            "INSERT INTO transfers (transfer_no, from_location, to_location, to_external, status, user_id) VALUES (?,?,?,?,?,?)",
            (transfer_no, from_location, to_location, to_external, "RELEASED", session["user"]["id"]),
        )
        tid = cur.lastrowid
        dest_label = to_external and f"plant {to_external}" or f"location {to_location}"
        for it in items:
            try:
                qty = int(it["qty"])
            except (TypeError, ValueError, KeyError):
                raise ValueError("Each item needs a whole-number quantity")
            if qty <= 0:
                raise ValueError("Quantities must be positive")
            state = (it.get("cylinder_state") or "FULL").upper()
            if state not in ("FULL", "EMPTY", "SWAPPED"):
                raise ValueError("cylinder_state must be FULL, EMPTY or SWAPPED")
            brand = (it.get("swap_brand") or "").strip() if state == "SWAPPED" else None
            if state == "SWAPPED" and not brand:
                raise ValueError("Choose the brand of the swapped empties to dispatch")
            conn.execute("INSERT INTO transfer_items (transfer_id, product_id, qty, cylinder_state, swap_brand) VALUES (?,?,?,?,?)",
                         (tid, it["product_id"], qty, state, brand))
            if state == "SWAPPED":
                remove_swapped_empties(conn, from_location, it["product_id"], brand, qty, "DISPATCH OUT", transfer_no,
                                       notes=f"{brand} empties dispatched to {dest_label}")
            elif state == "EMPTY":
                remove_empty_cylinders(conn, from_location, it["product_id"], qty, "DISPATCH OUT", transfer_no,
                                       notes=f"Empties dispatched to {dest_label}")
            else:
                adjust_inventory(conn, from_location, it["product_id"], -qty, "TRANSFER OUT", transfer_no,
                                 notes=f"Full cylinders dispatched to {dest_label}")
        audit(conn, "CREATE", "TRANSFERS", tid, transfer_no + (f" to plant {to_external}" if to_external else ""))
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
    # A dispatch to a plant leaves the system — "received" only confirms the
    # plant got the cylinders; nothing is added to any of our locations.
    if t["to_location"]:
        for it in items:
            if it["cylinder_state"] == "SWAPPED":
                add_swapped_empties(conn, t["to_location"], it["product_id"], it["swap_brand"], it["qty"],
                                    "DISPATCH IN", t["transfer_no"], notes="Swapped empties received from dispatch")
            elif it["cylinder_state"] == "EMPTY":
                add_empty_cylinders(conn, t["to_location"], it["product_id"], it["qty"], "DISPATCH IN",
                                    t["transfer_no"], notes="Empties received from dispatch")
            else:
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
    all_locations = u["role"] in ALL_LOCATION_FINANCE_ROLES
    loc_filter = "" if all_locations else "AND location_id=?"
    params = [] if all_locations else [u["location_id"]]

    today_sales = conn.execute(
        f"SELECT COALESCE(SUM(total),0) t, COUNT(*) c FROM sales WHERE status='COMPLETED' AND is_opening_balance=0 "
        f"AND date(ts)=date('now') {loc_filter}",
        params,
    ).fetchone()
    # Receivables totals are sensitive — only Admin/Accounting get them, and
    # they're withheld server-side (not just hidden in the UI).
    show_receivables = can_view_receivables_summary(conn, u["role"])
    outstanding_total = overdue_total = None
    if show_receivables:
        for r in conn.execute("SELECT id FROM receivables WHERE status!='PAID'").fetchall():
            recompute_receivable_status(conn, r["id"])
        conn.commit()
        outstanding_total = conn.execute("SELECT COALESCE(SUM(balance),0) b FROM receivables WHERE status!='PAID'").fetchone()["b"]
        overdue_total = conn.execute("SELECT COALESCE(SUM(balance),0) b FROM receivables WHERE status='OVERDUE'").fetchone()["b"]
    low_stock = conn.execute(
        """SELECT p.name, l.name location_name, i.quantity, p.min_stock
           FROM inventory i JOIN products p ON p.id=i.product_id JOIN locations l ON l.id=i.location_id
           WHERE i.quantity <= p.min_stock""" + ("" if all_locations else " AND l.id=?"),
        params,
    ).fetchall()

    # Payments by method — date-filtered (defaults to today) and scoped to the
    # user's own location for non-admins, via the sale each payment belongs to.
    date_from = request.args.get("payments_from")
    date_to = request.args.get("payments_to")
    if not date_from and not date_to:
        date_from = date_to = datetime.now().strftime("%Y-%m-%d")

    pay_sql = """SELECT p.method, COALESCE(SUM(p.amount),0) total
                  FROM payments p JOIN sales s ON s.id = p.sale_id
                  WHERE 1=1"""
    pay_params = []
    if date_from:
        pay_sql += " AND date(p.ts) >= date(?)"
        pay_params.append(date_from)
    if date_to:
        pay_sql += " AND date(p.ts) <= date(?)"
        pay_params.append(date_to)
    if u["role"] not in ("ADMIN", "MANAGER"):
        pay_sql += " AND s.location_id=?"
        pay_params.append(u["location_id"])
    pay_sql += " GROUP BY p.method ORDER BY total DESC"
    by_method = conn.execute(pay_sql, pay_params).fetchall()
    payments_total = round(sum(r["total"] for r in by_method), 2)

    conn.close()
    return jsonify(
        today_sales_total=today_sales["t"], today_sales_count=today_sales["c"],
        show_receivables=show_receivables,
        outstanding_receivables=outstanding_total, overdue_receivables=overdue_total,
        low_stock=[dict(r) for r in low_stock],
        payments_by_method=[dict(r) for r in by_method],
        payments_total=payments_total,
        payments_from=date_from, payments_to=date_to,
    )


@app.get("/api/reports/sales")
@login_required
def report_sales():
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    rows = _sales_report_rows(date_from, date_to)
    return jsonify([dict(r) for r in rows])


def _sales_report_rows(date_from, date_to):
    """One row per item sold, so Qty is its own column and each item's
    discount shows in Remarks. Sales with no item lines still get one row."""
    conn = get_db()
    sql = """SELECT s.sale_no, s.ts, l.name location_name, u.name cashier, c.name customer,
                    CASE WHEN c.id IS NULL THEN 'Walk-in' ELSE COALESCE(NULLIF(c.category,''), 'Uncategorized') END customer_category,
                    s.sale_type, s.status, s.total sale_total, s.discount sale_discount,
                    si.product_name || CASE WHEN si.sale_mode='DEPOSIT' THEN ' (deposit)'
                        WHEN si.sale_mode='SWAP' THEN ' (swap: ' || COALESCE(si.swap_brand,'') || ')' ELSE '' END item,
                    si.qty, si.price,
                    COALESCE(si.subtotal, s.subtotal) amount,
                    COALESCE(si.discount_amount, CASE WHEN si.id IS NULL THEN s.discount ELSE 0 END) discount,
                    si.discount_name
             FROM sales s JOIN locations l ON l.id=s.location_id JOIN users u ON u.id=s.user_id
             LEFT JOIN customers c ON c.id=s.customer_id
             LEFT JOIN sale_items si ON si.sale_id = s.id
             WHERE s.is_opening_balance=0"""
    params = []
    if date_from:
        sql += " AND date(s.ts) >= date(?)"; params.append(date_from)
    if date_to:
        sql += " AND date(s.ts) <= date(?)"; params.append(date_to)
    sql += " ORDER BY s.ts DESC, s.id DESC, si.id"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    out = [dict(r) for r in rows]
    for d in out:
        d["remarks"] = d["discount_name"] or ""
    # Sales made before per-item discounts carry a whole-sale discount that
    # isn't on any item line — show it on that sale's first row.
    seen = {}
    for d in out:
        seen.setdefault(d["sale_no"], []).append(d)
    for lines in seen.values():
        leftover = round((lines[0]["sale_discount"] or 0) - sum(l["discount"] or 0 for l in lines), 2)
        if leftover > 0.009 and lines[0]["item"] is not None:
            lines[0]["discount"] = round((lines[0]["discount"] or 0) + leftover, 2)
            lines[0]["remarks"] = "; ".join(x for x in (lines[0]["remarks"], "Whole-sale discount") if x)
    for d in out:
        d["net_amount"] = round((d["amount"] or 0) - (d["discount"] or 0), 2)
    return out


@app.get("/api/reports/sales/export")
@roles_or_module_required("ADMIN", "MANAGER", module="reports")
def export_sales_report():
    fmt = request.args.get("format", "xlsx")
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    rows = _sales_report_rows(date_from, date_to)
    headers = ["Sale #", "Date", "Location", "Cashier", "Customer", "Category", "Type", "Item", "Qty", "Unit Price",
               "Amount", "Discount", "Net Amount", "Status", "Remarks"]
    data = [(r["sale_no"], r["ts"], r["location_name"], r["cashier"], r["customer"] or "Walk-in",
             r["customer_category"], r["sale_type"], r["item"] or "", r["qty"] if r["qty"] is not None else "",
             r["price"] if r["price"] is not None else "", r["amount"], r["discount"], r["net_amount"],
             r["status"], r["remarks"]) for r in rows]
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    if fmt == "pdf":
        return pdf_table_response("Sales Report", headers, data, f"sales-report-{stamp}.pdf",
                                   subtitle=f"{date_from or 'All dates'} to {date_to or 'present'}",
                                   col_widths=[54, 64, 50, 44, 54, 46, 32, 76, 24, 42, 46, 40, 48, 44, 56],
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
        """SELECT r.invoice_no, c.name customer_name, c.account_no, c.category customer_category, s.sale_no, r.invoice_date, r.due_date,
                  r.amount, r.paid, r.balance, r.status
           FROM receivables r JOIN customers c ON c.id=r.customer_id JOIN sales s ON s.id=r.sale_id
           ORDER BY r.due_date ASC"""
    ).fetchall()
    conn.close()
    headers = ["Invoice #", "Customer", "Account #", "Category", "Sale #", "Invoice Date", "Due Date", "Amount", "Paid", "Balance", "Status"]
    data = [(r["invoice_no"], r["customer_name"], r["account_no"], r["customer_category"] or "", r["sale_no"], r["invoice_date"], r["due_date"],
              r["amount"], r["paid"], r["balance"], r["status"]) for r in out]
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    if fmt == "pdf":
        return pdf_table_response("Accounts Receivable", headers, data, f"receivables-{stamp}.pdf", landscape_mode=True)
    return xlsx_response(headers, data, f"receivables-{stamp}.xlsx", "Receivables")


# ---------------------------------------------------------------- swapped cylinder report
# Every other-brand cylinder taken in on a SWAP sale, plus totals per brand /
# size and what's still on hand (not yet dispatched to the plant).

def _build_swapped_report(conn, date_from=None, date_to=None, location_id=None):
    where = ["si.sale_mode = 'SWAP'", "s.status = 'COMPLETED'", "s.is_opening_balance = 0"]
    params = []
    if date_from:
        where.append("date(s.ts) >= date(?)"); params.append(date_from)
    if date_to:
        where.append("date(s.ts) <= date(?)"); params.append(date_to)
    if location_id:
        where.append("s.location_id = ?"); params.append(location_id)
    rows = conn.execute(
        f"""SELECT s.ts, s.sale_no, l.name location_name, u.name cashier, c.name customer_name,
                   COALESCE(NULLIF(si.swap_brand,''), 'Unspecified') swap_brand, si.product_name,
                   si.qty, COALESCE(si.swap_fee,0) swap_fee, ROUND(COALESCE(si.swap_fee,0) * si.qty, 2) swap_fee_total,
                   si.subtotal amount
            FROM sale_items si JOIN sales s ON s.id = si.sale_id
            JOIN locations l ON l.id = s.location_id JOIN users u ON u.id = s.user_id
            LEFT JOIN customers c ON c.id = s.customer_id
            WHERE {' AND '.join(where)} ORDER BY s.ts DESC, si.id""",
        params,
    ).fetchall()
    rows = [dict(r) for r in rows]

    summary = {}
    for r in rows:
        k = (r["swap_brand"], r["product_name"])
        sm = summary.setdefault(k, {"swap_brand": k[0], "product_name": k[1], "qty": 0, "swap_fees": 0.0, "amount": 0.0})
        sm["qty"] += r["qty"]
        sm["swap_fees"] = round(sm["swap_fees"] + r["swap_fee_total"], 2)
        sm["amount"] = round(sm["amount"] + (r["amount"] or 0), 2)
    summary = sorted(summary.values(), key=lambda x: (x["swap_brand"], x["product_name"]))

    oh_sql = """SELECT l.name location_name, s.brand, p.name product_name, s.quantity
                FROM swapped_empty_inventory s JOIN locations l ON l.id=s.location_id
                JOIN products p ON p.id=s.product_id WHERE s.quantity <> 0"""
    oh_params = []
    if location_id:
        oh_sql += " AND s.location_id = ?"; oh_params.append(location_id)
    on_hand = [dict(r) for r in conn.execute(oh_sql + " ORDER BY l.name, s.brand, p.name", oh_params).fetchall()]

    location_name = None
    if location_id:
        row = conn.execute("SELECT name FROM locations WHERE id=?", (location_id,)).fetchone()
        location_name = row["name"] if row else None
    return {
        "date_from": date_from, "date_to": date_to, "location_id": location_id, "location_name": location_name,
        "rows": rows, "summary": summary, "on_hand": on_hand,
        "totals": {"qty": sum(r["qty"] for r in rows),
                   "swap_fees": round(sum(r["swap_fee_total"] for r in rows), 2),
                   "amount": round(sum((r["amount"] or 0) for r in rows), 2),
                   "on_hand": sum(r["quantity"] for r in on_hand)},
    }


def _swapped_args():
    return request.args.get("from") or None, request.args.get("to") or None, request.args.get("location_id", type=int)


@app.get("/api/reports/swapped")
@roles_or_module_required("ADMIN", "MANAGER", module="reports")
def report_swapped():
    conn = get_db()
    data = _build_swapped_report(conn, *_swapped_args())
    conn.close()
    return jsonify(data)


@app.get("/api/reports/swapped/export")
@roles_or_module_required("ADMIN", "MANAGER", module="reports")
def export_swapped_report():
    fmt = request.args.get("format", "xlsx")
    conn = get_db()
    data = _build_swapped_report(conn, *_swapped_args())
    conn.close()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    subtitle = f"{data['date_from'] or 'All dates'} to {data['date_to'] or 'present'}"
    if data["location_name"]:
        subtitle += f" — {data['location_name']}"
    if fmt == "pdf":
        return swapped_pdf_response(data, subtitle, f"swapped-cylinders-{stamp}.pdf")
    return swapped_xlsx_response(data, subtitle, f"swapped-cylinders-{stamp}.xlsx")


# ---------------------------------------------------------------- customer growth (LPG cylinders only)
# Weekly / monthly customer growth, counting ONLY purchases of LPG cylinder
# products (refill, deposit or swap). Accessories and other items never make
# someone an active / new customer. Only registered customers can be tracked
# over time; walk-in cylinder sales are shown separately for context.
#
#   Active    = registered customers who bought >= 1 LPG cylinder in the period
#   New       = active, and this is their first-ever LPG cylinder purchase
#   Retained  = active this period AND the period before
#   Reactivated = active, not new, but did not buy the period before
#   Lapsed    = active the period before but not this period
#   Retention = retained / active the period before
#   Growth    = change in active customers vs the period before

# Same rule the POS uses to treat an item as a cylinder, minus a bare "LPG"
# match so LPG accessories (regulators, hoses) are not counted.
LPG_CYLINDER_SQL = ("(LOWER(COALESCE(p.category,'')) LIKE '%cylinder%' "
                    "OR COALESCE(p.deposit_price,0) > 0)")
GROWTH_MAX_PERIODS = 60


def _growth_periods(period, count, end_date):
    """Return [(start, end, label)] oldest first, the last one containing end_date.
    Weeks run Monday-Sunday."""
    out = []
    if period == "month":
        y, m = end_date.year, end_date.month
        for _ in range(count):
            start = end_date.replace(year=y, month=m, day=1)
            nxt = start.replace(year=y + (m == 12), month=1 if m == 12 else m + 1)
            out.append((start, nxt - timedelta(days=1), start.strftime("%b %Y")))
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    else:
        start = end_date - timedelta(days=end_date.weekday())
        for _ in range(count):
            end = start + timedelta(days=6)
            out.append((start, end, f"{start.strftime('%b %d')}–{end.strftime('%b %d, %Y')}"))
            start -= timedelta(days=7)
    return list(reversed(out))


def _pct(num, den):
    return round(num * 100.0 / den, 1) if den else None


def _build_customer_growth(conn, period="month", count=12, end_date=None, location_id=None, category=None):
    period = "week" if period == "week" else "month"
    count = max(2, min(int(count or 12), GROWTH_MAX_PERIODS))
    end_date = end_date or datetime.now().date()
    # One extra (hidden) period up front so the first shown period has a
    # "previous" to compare against.
    periods = _growth_periods(period, count + 1, end_date)
    range_start, range_end = periods[0][0], periods[-1][1]

    where = ["s.status='COMPLETED'", "s.is_opening_balance=0", "si.qty > 0", LPG_CYLINDER_SQL]
    params = []
    if location_id:
        where.append("s.location_id=?"); params.append(location_id)
    cust_where = list(where)
    cust_params = list(params)
    if category:
        cust_where.append("c.category=?"); cust_params.append(category)

    # Every LPG cylinder line bought by a registered customer, up to the end
    # of the range (earlier history is needed to know who is truly "new").
    lines = conn.execute(
        f"""SELECT s.customer_id, date(s.ts) d, si.qty,
                   ROUND(si.subtotal - COALESCE(si.discount_amount,0), 2) amount
            FROM sale_items si JOIN sales s ON s.id=si.sale_id
            JOIN products p ON p.id=si.product_id JOIN customers c ON c.id=s.customer_id
            WHERE {' AND '.join(cust_where)} AND date(s.ts) <= date(?)""",
        cust_params + [range_end.isoformat()],
    ).fetchall()

    first_buy, last_buy = {}, {}
    for r in lines:
        cid, d = r["customer_id"], r["d"]
        if cid not in first_buy or d < first_buy[cid]:
            first_buy[cid] = d
        if cid not in last_buy or d > last_buy[cid]:
            last_buy[cid] = d

    walkin = {}
    if not category:
        for r in conn.execute(
            f"""SELECT date(s.ts) d, SUM(si.qty) qty
                FROM sale_items si JOIN sales s ON s.id=si.sale_id JOIN products p ON p.id=si.product_id
                WHERE {' AND '.join(where)} AND s.customer_id IS NULL
                  AND date(s.ts) BETWEEN date(?) AND date(?) GROUP BY date(s.ts)""",
            params + [range_start.isoformat(), range_end.isoformat()],
        ).fetchall():
            walkin[r["d"]] = r["qty"]

    stats = []
    for start, end, label in periods:
        s_iso, e_iso = start.isoformat(), end.isoformat()
        active, qty, amount = {}, 0, 0.0
        for r in lines:
            if s_iso <= r["d"] <= e_iso:
                a = active.setdefault(r["customer_id"], {"qty": 0, "amount": 0.0})
                a["qty"] += r["qty"]; a["amount"] += r["amount"] or 0
                qty += r["qty"]; amount += r["amount"] or 0
        stats.append({
            "label": label, "start": s_iso, "end": e_iso, "active_map": active,
            "cylinders": qty, "amount": round(amount, 2),
            "walkin_cylinders": sum(v for d, v in walkin.items() if s_iso <= d <= e_iso),
            "customer_base": sum(1 for d in first_buy.values() if d <= e_iso),
        })

    rows = []
    for prev, cur in zip(stats, stats[1:]):
        act, pact = set(cur["active_map"]), set(prev["active_map"])
        new = {c for c in act if cur["start"] <= first_buy[c] <= cur["end"]}
        retained = act & pact
        cur["_sets"] = {"new": new, "lapsed": pact - act, "prev": prev["active_map"]}
        rows.append({
            "label": cur["label"], "start": cur["start"], "end": cur["end"],
            "active": len(act), "new": len(new), "retained": len(retained),
            "reactivated": len(act - new - retained), "lapsed": len(pact - act),
            "prev_active": len(pact),
            "growth_pct": _pct(len(act) - len(pact), len(pact)),
            "net_change": len(act) - len(pact),
            "retention_pct": _pct(len(retained), len(pact)),
            "cylinders": cur["cylinders"], "amount": cur["amount"],
            "cylinders_per_customer": round(cur["cylinders"] / len(act), 2) if act else 0,
            "walkin_cylinders": cur["walkin_cylinders"],
            "customer_base": cur["customer_base"],
        })

    latest = stats[-1]
    ids = set(latest["active_map"]) | latest["_sets"]["lapsed"]
    info = {}
    if ids:
        qmarks = ",".join("?" * len(ids))
        for r in conn.execute(f"SELECT id, account_no, name, business_name, phone, category FROM customers WHERE id IN ({qmarks})",
                              list(ids)).fetchall():
            info[r["id"]] = dict(r)

    def cust(cid, extra):
        d = dict(info.get(cid, {"id": cid, "name": "?", "account_no": "", "category": ""}))
        d.update(extra)
        return d

    new_customers = sorted(
        (cust(c, {"first_purchase": first_buy[c], "cylinders": latest["active_map"][c]["qty"],
                  "amount": round(latest["active_map"][c]["amount"], 2)}) for c in latest["_sets"]["new"]),
        key=lambda x: (x["first_purchase"], x["name"]))
    lapsed_customers = sorted(
        (cust(c, {"last_purchase": last_buy.get(c), "prev_cylinders": latest["_sets"]["prev"][c]["qty"]})
         for c in latest["_sets"]["lapsed"]),
        key=lambda x: (-x["prev_cylinders"], x["name"]))

    by_category = {}
    for c, a in latest["active_map"].items():
        cat = (info.get(c, {}).get("category") or "Uncategorized")
        b = by_category.setdefault(cat, {"category": cat, "active": 0, "new": 0, "cylinders": 0})
        b["active"] += 1; b["cylinders"] += a["qty"]
        if c in latest["_sets"]["new"]:
            b["new"] += 1
    top_customers = sorted(
        (cust(c, {"cylinders": a["qty"], "amount": round(a["amount"], 2)}) for c, a in latest["active_map"].items()),
        key=lambda x: (-x["cylinders"], x["name"]))[:10]

    products = [dict(r) for r in conn.execute(
        f"SELECT p.name, p.category FROM products p WHERE p.active=1 AND {LPG_CYLINDER_SQL} ORDER BY p.name").fetchall()]
    location_name = None
    if location_id:
        r = conn.execute("SELECT name FROM locations WHERE id=?", (location_id,)).fetchone()
        location_name = r["name"] if r else None

    return {
        "period": period, "count": count, "end_date": end_date.isoformat(),
        "location_id": location_id, "location_name": location_name, "category": category,
        "rows": rows, "current": rows[-1], "previous": rows[-2] if len(rows) > 1 else None,
        "new_customers": new_customers, "lapsed_customers": lapsed_customers,
        "by_category": sorted(by_category.values(), key=lambda x: -x["active"]),
        "top_customers": top_customers, "products_counted": products,
        "totals": {"new": sum(r["new"] for r in rows), "cylinders": sum(r["cylinders"] for r in rows),
                   "amount": round(sum(r["amount"] for r in rows), 2),
                   "walkin_cylinders": sum(r["walkin_cylinders"] for r in rows)},
    }


def _growth_args():
    end = request.args.get("end")
    try:
        end_date = datetime.strptime(end, "%Y-%m-%d").date() if end else None
    except ValueError:
        end_date = None
    return dict(period=request.args.get("period", "month"),
                count=request.args.get("count", type=int) or 12,
                end_date=end_date,
                location_id=request.args.get("location_id", type=int),
                category=(request.args.get("category") or "").strip() or None)


@app.get("/api/reports/customer-growth")
@roles_or_module_required("ADMIN", "MANAGER", module="customer_growth")
def report_customer_growth():
    conn = get_db()
    data = _build_customer_growth(conn, **_growth_args())
    conn.close()
    return jsonify(data)


@app.get("/api/reports/customer-growth/export")
@roles_or_module_required("ADMIN", "MANAGER", module="customer_growth")
def export_customer_growth():
    fmt = request.args.get("format", "xlsx")
    conn = get_db()
    data = _build_customer_growth(conn, **_growth_args())
    conn.close()
    rows = data["rows"]
    subtitle = (f"{'Weekly' if data['period'] == 'week' else 'Monthly'} · LPG cylinder purchases only · "
                f"{rows[0]['start']} to {rows[-1]['end']}")
    if data["location_name"]:
        subtitle += f" · {data['location_name']}"
    if data["category"]:
        subtitle += f" · {data['category']} customers"
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    if fmt == "pdf":
        return customer_growth_pdf_response(data, subtitle, f"customer-growth-{stamp}.pdf")
    return customer_growth_xlsx_response(data, subtitle, f"customer-growth-{stamp}.xlsx")


# ---------------------------------------------------------------- customer monthly LPG kg report
# Per customer, how many kg of LPG they bought each month on completed sales
# (voided sales and opening balances are ignored). Only LPG cylinder products
# count. A cylinder line's kg = qty x the cylinder size read from the product
# name or SKU (e.g. "Solane LPG 11kg" -> 11). 50kg commercial weight-billed
# sales already record the kg consumed as the line qty, so that is used as-is.

KG_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*-?\s*kg\b", re.IGNORECASE)
KG_REPORT_MAX_MONTHS = 24


def product_kg_size(name, sku=None):
    for text in (name, sku):
        m = KG_SIZE_RE.search(text or "")
        if m:
            return float(m.group(1))
    return None


def _month_list(start_ym, end_ym):
    y, m = start_ym
    out = []
    while (y, m) <= end_ym:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out[-KG_REPORT_MAX_MONTHS:]  # keep the most recent months


def _parse_ym(value):
    try:
        d = datetime.strptime(value, "%Y-%m")
        return d.year, d.month
    except (TypeError, ValueError):
        return None


def _build_customer_kg_report(conn, month_from=None, month_to=None, location_id=None, category=None):
    today = datetime.now().date()
    end_ym = _parse_ym(month_to) or (today.year, today.month)
    start_ym = _parse_ym(month_from)
    if not start_ym:
        y, m = end_ym
        m -= 11
        while m < 1:
            m += 12; y -= 1
        start_ym = (y, m)
    if start_ym > end_ym:
        start_ym, end_ym = end_ym, start_ym
    months = _month_list(start_ym, end_ym)
    first_day = months[0] + "-01"
    ly, lm = map(int, months[-1].split("-"))
    last_day = (datetime(ly + (lm == 12), 1 if lm == 12 else lm + 1, 1).date() - timedelta(days=1)).isoformat()

    where = ["s.status='COMPLETED'", "s.is_opening_balance=0", "si.qty > 0", LPG_CYLINDER_SQL,
             "date(s.ts) BETWEEN date(?) AND date(?)"]
    params = [first_day, last_day]
    if location_id:
        where.append("s.location_id=?"); params.append(location_id)
    if category:
        where.append("c.category=?"); params.append(category)
    lines = conn.execute(
        f"""SELECT s.customer_id, strftime('%Y-%m', s.ts) ym, si.qty, si.product_id, p.name product_name, p.sku,
                   ROUND(si.subtotal - COALESCE(si.discount_amount,0), 2) amount,
                   EXISTS(SELECT 1 FROM commercial_deliveries cd WHERE cd.sale_id = s.id) weight_billed,
                   c.account_no, c.name customer_name, c.business_name, c.category customer_category
            FROM sale_items si JOIN sales s ON s.id=si.sale_id JOIN products p ON p.id=si.product_id
            {'JOIN' if category else 'LEFT JOIN'} customers c ON c.id=s.customer_id
            WHERE {' AND '.join(where)}""",
        params,
    ).fetchall()

    customers, walkin, by_size, unknown = {}, None, {}, {}
    month_totals = {ym: 0.0 for ym in months}

    def blank(**kw):
        return dict(kw, months={ym: 0.0 for ym in months}, total_kg=0.0, cylinders=0, amount=0.0, orders=set())

    for r in lines:
        if r["weight_billed"]:
            kg, cyl = float(r["qty"]), 0
        else:
            size = product_kg_size(r["product_name"], r["sku"])
            if size is None:
                unknown[r["product_id"]] = r["product_name"]
                continue
            kg, cyl = r["qty"] * size, r["qty"]
        if r["customer_id"]:
            row = customers.get(r["customer_id"])
            if row is None:
                row = customers[r["customer_id"]] = blank(
                    customer_id=r["customer_id"], account_no=r["account_no"], name=r["customer_name"],
                    business_name=r["business_name"] or "", category=r["customer_category"] or "")
        else:
            if walkin is None:
                walkin = blank(customer_id=None, account_no="", name="Walk-in (no account)", business_name="", category="")
            row = walkin
        row["months"][r["ym"]] += kg
        row["total_kg"] += kg
        row["cylinders"] += cyl
        row["amount"] += r["amount"] or 0
        month_totals[r["ym"]] += kg
        sk = (r["customer_id"], r["product_id"])
        sz = by_size.setdefault(sk, {"customer_id": r["customer_id"], "account_no": row["account_no"], "name": row["name"],
                                     "product_name": r["product_name"].replace(" (weight-billed)", ""),
                                     "months": {ym: 0.0 for ym in months}, "total_kg": 0.0, "cylinders": 0})
        sz["months"][r["ym"]] += kg; sz["total_kg"] += kg; sz["cylinders"] += cyl

    def finish(row):
        row["months"] = {k: round(v, 2) for k, v in row["months"].items()}
        row["total_kg"] = round(row["total_kg"], 2)
        if "amount" in row:
            row["amount"] = round(row["amount"], 2)
            active = sum(1 for v in row["months"].values() if v)
            row["months_bought"] = active
            row["avg_kg_per_month"] = round(row["total_kg"] / len(months), 2)
            row.pop("orders", None)
        return row

    rows = sorted((finish(r) for r in customers.values()), key=lambda x: (-x["total_kg"], x["name"]))
    location_name = None
    if location_id:
        lr = conn.execute("SELECT name FROM locations WHERE id=?", (location_id,)).fetchone()
        location_name = lr["name"] if lr else None
    totals_customers = {ym: round(sum(r["months"][ym] for r in rows), 2) for ym in months}
    return {
        "months": months,
        "month_labels": [datetime.strptime(ym, "%Y-%m").strftime("%b %Y") for ym in months],
        "month_from": months[0], "month_to": months[-1],
        "location_id": location_id, "location_name": location_name, "category": category,
        "rows": rows,
        "walkin": finish(walkin) if walkin else None,
        "by_size": sorted((finish(r) for r in by_size.values()), key=lambda x: (x["name"], x["product_name"])),
        "totals": {
            "customers_months": totals_customers,
            "customers_kg": round(sum(r["total_kg"] for r in rows), 2),
            "customers_cylinders": sum(r["cylinders"] for r in rows),
            "customers_amount": round(sum(r["amount"] for r in rows), 2),
            "months": {k: round(v, 2) for k, v in month_totals.items()},
            "kg": round(sum(month_totals.values()), 2),
        },
        "unknown_size_products": sorted(unknown.values()),
    }


def _kg_report_args():
    return dict(month_from=request.args.get("from") or None, month_to=request.args.get("to") or None,
                location_id=request.args.get("location_id", type=int),
                category=(request.args.get("category") or "").strip() or None)


@app.get("/api/reports/customer-kg")
@roles_or_module_required("ADMIN", "MANAGER", module="customer_growth")
def report_customer_kg():
    conn = get_db()
    data = _build_customer_kg_report(conn, **_kg_report_args())
    conn.close()
    return jsonify(data)


@app.get("/api/reports/customer-kg/export")
@roles_or_module_required("ADMIN", "MANAGER", module="customer_growth")
def export_customer_kg():
    fmt = request.args.get("format", "xlsx")
    conn = get_db()
    data = _build_customer_kg_report(conn, **_kg_report_args())
    conn.close()
    subtitle = f"Completed LPG cylinder orders, in kg · {data['month_labels'][0]} to {data['month_labels'][-1]}"
    if data["location_name"]:
        subtitle += f" · {data['location_name']}"
    if data["category"]:
        subtitle += f" · {data['category']} customers"
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    if fmt == "pdf":
        return customer_kg_pdf_response(data, subtitle, f"customer-monthly-kg-{stamp}.pdf")
    return customer_kg_xlsx_response(data, subtitle, f"customer-monthly-kg-{stamp}.xlsx")


# ---------------------------------------------------------------- end of day report
# Per-attendant cash-up: everything they collected on a given day, totalled
# by payment method (Cash, GCash, Bank Transfer, ...), split between POS
# sales and collections on receivables. Attendants only ever see their own;
# Admin/Manager/Accounting can pick any staff member (or everyone).

EOD_SUPERVISOR_ROLES = ("ADMIN", "MANAGER", "ACCOUNTING")


def _build_eod_report(conn, u, report_date, user_id=None, location_id=None):
    if u["role"] not in EOD_SUPERVISOR_ROLES:
        user_id = u["id"]
        location_id = None

    where = ["date(p.ts) = date(?)", "s.status = 'COMPLETED'"]
    params = [report_date]
    if user_id:
        where.append("p.user_id = ?"); params.append(user_id)
    if location_id:
        where.append("s.location_id = ?"); params.append(location_id)
    payments = conn.execute(
        f"""SELECT p.id, p.ts, p.method, p.amount, p.reference, p.payment_source,
                   s.sale_no, s.sale_type, c.name customer_name, uu.name collected_by, l.name location_name,
                   (SELECT r.invoice_no FROM receivables r WHERE r.sale_id = s.id) invoice_no
            FROM payments p JOIN sales s ON s.id = p.sale_id
            LEFT JOIN customers c ON c.id = s.customer_id
            LEFT JOIN users uu ON uu.id = p.user_id
            JOIN locations l ON l.id = s.location_id
            WHERE {' AND '.join(where)} ORDER BY p.ts""",
        params,
    ).fetchall()

    by_method = {}
    for p in payments:
        m = by_method.setdefault(p["method"], {"method": p["method"], "sales": 0.0, "collections": 0.0,
                                                "total": 0.0, "count": 0})
        key = "collections" if p["payment_source"] == "AR_COLLECTION" else "sales"
        m[key] = round(m[key] + p["amount"], 2)
        m["total"] = round(m["total"] + p["amount"], 2)
        m["count"] += 1
    methods = sorted(by_method.values(), key=lambda m: -m["total"])
    totals = {
        "sales": round(sum(m["sales"] for m in methods), 2),
        "collections": round(sum(m["collections"] for m in methods), 2),
        "total": round(sum(m["total"] for m in methods), 2),
        "count": sum(m["count"] for m in methods),
    }

    # Sales rung up that day (by cashier), incl. what was charged to accounts.
    s_where = ["date(s.ts) = date(?)", "s.is_opening_balance = 0"]
    s_params = [report_date]
    if user_id:
        s_where.append("s.user_id = ?"); s_params.append(user_id)
    if location_id:
        s_where.append("s.location_id = ?"); s_params.append(location_id)
    sw = " AND ".join(s_where)
    sales = conn.execute(
        f"""SELECT
              COALESCE(SUM(CASE WHEN status='COMPLETED' THEN 1 END),0) count,
              COALESCE(SUM(CASE WHEN status='COMPLETED' THEN total END),0) total,
              COALESCE(SUM(CASE WHEN status='COMPLETED' THEN discount END),0) discount,
              COALESCE(SUM(CASE WHEN status='COMPLETED' AND sale_type='CASH' THEN total END),0) cash_sales,
              COALESCE(SUM(CASE WHEN status='COMPLETED' AND sale_type='CREDIT' THEN total END),0) credit_sales,
              COALESCE(SUM(CASE WHEN status='VOIDED' THEN 1 END),0) voided_count,
              COALESCE(SUM(CASE WHEN status='VOIDED' THEN total END),0) voided_total
            FROM sales s WHERE {sw}""",
        s_params,
    ).fetchone()
    paid_at_sale = conn.execute(
        f"""SELECT COALESCE(SUM(p.amount),0) a FROM payments p JOIN sales s ON s.id=p.sale_id
            WHERE {sw} AND s.status='COMPLETED' AND p.payment_source='SALE'""",
        s_params,
    ).fetchone()["a"]
    sales_summary = {k: (round(sales[k], 2) if isinstance(sales[k], float) else sales[k]) for k in sales.keys()}
    sales_summary["charged_to_accounts"] = round(sales["total"] - paid_at_sale, 2)

    items = conn.execute(
        f"""SELECT si.product_name, si.sale_mode, SUM(si.qty) qty, SUM(si.subtotal) amount
            FROM sale_items si JOIN sales s ON s.id = si.sale_id
            WHERE {sw} AND s.status='COMPLETED'
            GROUP BY si.product_name, si.sale_mode ORDER BY si.product_name""",
        s_params,
    ).fetchall()

    staff_name, location_name = "All staff", None
    if user_id:
        row = conn.execute("SELECT u.name, l.name location_name FROM users u LEFT JOIN locations l ON l.id=u.location_id WHERE u.id=?",
                           (user_id,)).fetchone()
        if row:
            staff_name, location_name = row["name"], row["location_name"]
    if location_id:
        row = conn.execute("SELECT name FROM locations WHERE id=?", (location_id,)).fetchone()
        location_name = row["name"] if row else location_name

    return {
        "date": report_date, "user_id": user_id, "staff_name": staff_name,
        "location_id": location_id, "location_name": location_name,
        "methods": methods, "totals": totals, "sales": sales_summary,
        "items": [dict(i) for i in items],
        "payments": [dict(p) for p in payments],
        "generated_by": u["name"],
    }


def _eod_args():
    report_date = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
    _parse_date(report_date, "date")
    return report_date, request.args.get("user_id", type=int), request.args.get("location_id", type=int)


@app.get("/api/reports/end-of-day")
@roles_or_module_required("ADMIN", "MANAGER", "ACCOUNTING", "ATTENDANT", module="eod_report")
def end_of_day_report():
    u = session["user"]
    try:
        report_date, user_id, location_id = _eod_args()
    except ValueError as e:
        return jsonify(error=str(e)), 400
    conn = get_db()
    data = _build_eod_report(conn, u, report_date, user_id, location_id)
    if u["role"] in EOD_SUPERVISOR_ROLES:
        data["staff"] = [dict(r) for r in conn.execute(
            "SELECT id, name, role, location_id FROM users WHERE active=1 ORDER BY name").fetchall()]
        data["can_pick_staff"] = True
    else:
        data["can_pick_staff"] = False
    conn.close()
    return jsonify(data)


@app.get("/api/reports/end-of-day/export")
@roles_or_module_required("ADMIN", "MANAGER", "ACCOUNTING", "ATTENDANT", module="eod_report")
def export_end_of_day_report():
    u = session["user"]
    try:
        report_date, user_id, location_id = _eod_args()
    except ValueError as e:
        return jsonify(error=str(e)), 400
    conn = get_db()
    data = _build_eod_report(conn, u, report_date, user_id, location_id)
    conn.close()
    slug = "".join(ch for ch in data["staff_name"].lower().replace(" ", "-") if ch.isalnum() or ch == "-")
    fname = f"end-of-day-{report_date}-{slug}"
    if request.args.get("format") == "xlsx":
        return eod_xlsx_response(data, fname + ".xlsx")
    return eod_pdf_response(data, fname + ".pdf")


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
              LEFT JOIN sales s ON s.user_id=u.id AND s.status='COMPLETED' AND s.is_opening_balance=0"""
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
