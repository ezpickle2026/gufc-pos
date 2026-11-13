import os
import functools
import csv
import io
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import Flask, request, jsonify, session, send_from_directory, Response, g, has_request_context
from werkzeug.security import generate_password_hash, check_password_hash

from db import get_db, init_db, close_request_connections, ADMIN_LIKE_ROLES
from reports import (xlsx_response, pdf_table_response, soa_pdf_response, commercial_billing_pdf, eod_pdf_response,
                     eod_xlsx_response, swapped_pdf_response, swapped_xlsx_response,
                     customer_growth_pdf_response, customer_growth_xlsx_response,
                     customer_kg_pdf_response, customer_kg_xlsx_response)

app = Flask(__name__, static_folder="static", static_url_path="")
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-change-me")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")


@app.teardown_request
def _close_db_connections(exc=None):
    close_request_connections()


@app.errorhandler(Exception)
def _api_error(e):
    """Unexpected errors on API calls come back as JSON with the real reason,
    so the screen shows it instead of a bare "Request failed"."""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        if request.path.startswith("/api/"):
            return jsonify(error=e.description or e.name), e.code
        return e
    app.logger.exception("Unhandled error on %s %s", request.method, request.path)
    if not request.path.startswith("/api/"):
        return "Internal Server Error", 500
    if isinstance(e, sqlite3.IntegrityError):
        msg = ("Could not save: a selected record (location, product, customer or user) no longer exists "
               "or a value is not allowed. Refresh the page (F5) and try again. Details: " + str(e))
    elif isinstance(e, sqlite3.OperationalError) and "locked" in str(e).lower():
        msg = "The database is busy. Please wait a moment and try again."
    else:
        msg = f"Server error: {type(e).__name__}: {e}"
    return jsonify(error=msg), 500

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


def record_rate_change(conn, old, new, customer_id=None, product_id=None):
    """Keep a Commercial account's rate per kg (customer_id) or a product's
    default price per kg (product_id) in rate_history when it changes, for
    the rate movement shown in the customer portal."""
    old = float(old) if old not in (None, "") and float(old) > 0 else None
    new = float(new) if new not in (None, "") and float(new) > 0 else None
    if old == new or (old is not None and new is not None and abs(old - new) < 0.0001):
        return
    user = session.get("user") if has_request_context() else None
    conn.execute(
        "INSERT INTO rate_history (customer_id, product_id, old_rate, new_rate, changed_by, username) VALUES (?,?,?,?,?,?)",
        (customer_id, None if customer_id else product_id, old, new,
         user["id"] if user else None, user["username"] if user else "system"),
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


COMMERCIAL_CATEGORY = "commercial"


def is_commercial_account(customer):
    """Customers in the "Commercial" category are charged for 50kg through
    50kg Commercial Billing, not at SRP on the POS credit sale."""
    return (customer["category"] or "").strip().lower() == COMMERCIAL_CATEGORY


def is_commercial_cylinder(prod):
    size = product_kg_size(prod["name"], prod["sku"])
    return size is not None and abs(size - 50.0) < 0.01


# ---- valve swaps (11kg A/S <-> POL)
# The same cylinder size comes as two products, one per valve (e.g.
# "Solane LPG 11kg A/S" and "Solane LPG 11kg POL"). A valve swap is a SWAP
# line where the customer's empty has the OTHER valve:
#   VALVE_OTHER  - empty of another brand  -> Swapped Empties (per brand),
#                  counted under the product of the empty's size + valve
#   VALVE_SOLANE - our own Solane empty    -> our Empty Cylinders stock of
#                  the other-valve product
# A plain SWAP (swap_type empty) is the existing same-valve other-brand swap.
VALVE_SWAP_TYPES = ("VALVE_OTHER", "VALVE_SOLANE")
VALVE_AS_RE = re.compile(r"(?<![A-Za-z0-9])(?:[Aa]\s*/\s*[Ss]|AS)(?![A-Za-z0-9])")
VALVE_POL_RE = re.compile(r"(?<![A-Za-z0-9])POL(?![A-Za-z0-9])", re.IGNORECASE)


def product_valve(prod):
    """'A/S', 'POL' or None, read from the product name (then the SKU)."""
    for text in (prod["name"], prod["sku"]):
        t = text or ""
        if VALVE_POL_RE.search(t):
            return "POL"
        if VALVE_AS_RE.search(t):
            return "A/S"
    return None


def _strip_valve(name):
    n = VALVE_POL_RE.sub(" ", VALVE_AS_RE.sub(" ", name or ""))
    return " ".join(n.lower().split())


def valve_counterpart(conn, prod, products=None):
    """The active product of the same size with the other valve, or None."""
    valve = product_valve(prod)
    size = product_kg_size(prod["name"], prod["sku"])
    if not valve or size is None:
        return None
    want = "POL" if valve == "A/S" else "A/S"
    if products is None:
        products = conn.execute("SELECT * FROM products WHERE active=1").fetchall()
    cands = [p for p in products if p["id"] != prod["id"] and product_valve(p) == want
             and product_kg_size(p["name"], p["sku"]) == size]
    if len(cands) > 1:  # prefer the one whose name differs only by the valve
        same = [p for p in cands if _strip_valve(p["name"]) == _strip_valve(prod["name"])]
        cands = same or cands
    return cands[0] if cands else None


def resolve_swap(conn, prod, it, line=""):
    """Validates a SWAP line. Returns (swap_brand, swap_fee, swap_type,
    swap_empty_product_id, swap_valve). Raises ValueError."""
    swap_type = (it.get("swap_type") or "").strip().upper() or None
    if swap_type and swap_type not in VALVE_SWAP_TYPES:
        raise ValueError(f"{line}Unknown swap type {swap_type}")
    swap_brand = (it.get("swap_brand") or "").strip()
    empty_pid, swap_valve = None, None
    if swap_type:
        pair = valve_counterpart(conn, prod)
        if not pair:
            raise ValueError(f"{line}{prod['name']} has no A/S or POL counterpart product, so it can't be "
                             "swapped A/S to POL. Use the product names with A/S and POL for the same size.")
        empty_pid = pair["id"]
        swap_valve = f"{product_valve(pair)} to {product_valve(prod)}"
        if swap_type == "VALVE_SOLANE":
            swap_brand = "Solane"
        elif swap_brand.lower() == "solane":
            raise ValueError(f"{line}For a Solane empty, choose Swap A/S-POL (Solane)")
    if not swap_brand:
        raise ValueError(f"{line}Choose the LPG brand being swapped for {prod['name']}")
    raw = it.get("swap_fee")
    try:
        swap_fee = round(float(raw), 2) if raw not in (None, "") else 0.0
    except (TypeError, ValueError):
        raise ValueError(f"{line}Swap fee must be a number")
    if swap_fee < 0:
        raise ValueError(f"{line}Swap fee cannot be negative")
    return swap_brand, swap_fee, swap_type, empty_pid, swap_valve


def swap_label(it):
    """How a swap line reads on receipts and reports."""
    if it["swap_type"] == "VALVE_SOLANE":
        return f"Solane {it['swap_valve'] or 'A/S-POL'}"
    if it["swap_type"] == "VALVE_OTHER":
        return f"{it['swap_brand'] or ''} {it['swap_valve'] or 'A/S-POL'}".strip()
    return it["swap_brand"] or ""


def swap_empty_in(conn, location_id, product_id, swap_brand, swap_type, empty_pid, qty, mtype, ref, notes):
    """The empty the customer handed over on a swap line comes into stock."""
    if swap_type == "VALVE_SOLANE":
        add_empty_cylinders(conn, location_id, empty_pid, qty, mtype, reference=ref, notes=notes)
    elif swap_brand:
        add_swapped_empties(conn, location_id, empty_pid or product_id, swap_brand, qty, mtype,
                            reference=ref, notes=notes)


def swap_empty_reverse(conn, location_id, it, mtype, ref, notes):
    """Undo swap_empty_in for a stored sale_items row (void / edit)."""
    if it["swap_type"] == "VALVE_SOLANE" and it["swap_empty_product_id"]:
        remove_empty_cylinders(conn, location_id, it["swap_empty_product_id"], it["qty"], mtype, ref,
                               notes=notes, allow_negative=True)
    elif it["swap_brand"]:
        remove_swapped_empties(conn, location_id, it["swap_empty_product_id"] or it["product_id"], it["swap_brand"],
                               it["qty"], mtype, ref, notes=notes, allow_negative=True)


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if "user" not in session:
            return jsonify(error="Not authenticated"), 401
        return fn(*a, **kw)
    return wrapper


def is_admin_like(role):
    """ADMIN and OPERATION_HEAD. Operation Head has Admin-level access to
    everything except the System Reset (Go Live), which uses admin_only."""
    return role in ADMIN_LIKE_ROLES


def _with_operation_head(roles):
    """Any route open to ADMIN is open to OPERATION_HEAD too."""
    roles = tuple(roles)
    if "ADMIN" in roles and "OPERATION_HEAD" not in roles:
        roles += ("OPERATION_HEAD",)
    return roles


def admin_only(fn):
    """Strictly the ADMIN role — not Operation Head. Used for System Reset."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if "user" not in session:
            return jsonify(error="Not authenticated"), 401
        if session["user"]["role"] != "ADMIN":
            return jsonify(error="Forbidden: only the Admin account can do this"), 403
        return fn(*a, **kw)
    return wrapper


def roles_required(*roles):
    roles = _with_operation_head(roles)
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
    if is_admin_like(role):
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
    roles = _with_operation_head(roles)
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
    if is_admin_like(role):
        from db import MODULE_KEYS
        return MODULE_KEYS
    rows = conn.execute("SELECT module_key FROM role_permissions WHERE role_name=?", (role,)).fetchall()
    return [r["module_key"] for r in rows]


# Roles that see receivables (and sales) across every location rather than
# just their own assigned branch.
ALL_LOCATION_FINANCE_ROLES = ("ADMIN", "OPERATION_HEAD", "MANAGER", "ACCOUNTING")


def can_view_receivables_summary(conn, role):
    """Outstanding / overdue receivables totals on the dashboard are limited
    to Admin and Accounting by default (via the receivables_summary module,
    which Admin can grant to other roles in Manage Roles if ever needed)."""
    return has_module_access(conn, role, "receivables_summary")


def location_scope(row_location_id):
    """Non-admin users may only touch their own assigned location."""
    u = session["user"]
    if is_admin_like(u["role"]):
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
    session.pop("user", None)
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
    if is_admin_like(name):
        conn.close()
        return jsonify(module_keys=MODULE_KEYS, all_modules=MODULE_KEYS, locked=True)
    rows = conn.execute("SELECT module_key FROM role_permissions WHERE role_name=?", (name,)).fetchall()
    conn.close()
    return jsonify(module_keys=[r["module_key"] for r in rows], all_modules=MODULE_KEYS, locked=False)


@app.put("/api/roles/<path:name>/permissions")
@roles_required("ADMIN", "MANAGER")
def set_role_permissions(name):
    from db import MODULE_KEYS
    if is_admin_like(name):
        return jsonify(error=f"{name} always has full access and can't be restricted"), 400
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
    out = []
    for r in rows:
        pair = valve_counterpart(None, r, rows)
        out.append({**dict(r), "valve": product_valve(r), "valve_pair_id": pair["id"] if pair else None,
                    "valve_pair_valve": product_valve(pair) if pair else None})
    return jsonify(out)


@app.post("/api/products")
@roles_required("ADMIN")
def create_product():
    d = request.get_json(force=True)
    conn = get_db()
    # Re-adding the SKU of a product that was deleted (archived) brings it back.
    old = conn.execute("SELECT id, price_per_kg FROM products WHERE sku=? AND active=0", (d.get("sku"),)).fetchone()
    if old:
        record_rate_change(conn, old["price_per_kg"], d.get("price_per_kg"), product_id=old["id"])
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
        record_rate_change(conn, None, d.get("price_per_kg"), product_id=cur.lastrowid)
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
            old = conn.execute("SELECT id, price_per_kg FROM products WHERE sku=? AND active=0", (sku,)).fetchone()
            if old:  # bring back a deleted (archived) product with the same SKU
                conn.execute(
                    "UPDATE products SET name=?, category=?, unit=?, price=?, cost=?, deposit_price=?, price_per_kg=?, min_stock=?, active=1 WHERE id=?",
                    vals + (old["id"],),
                )
                record_rate_change(conn, old["price_per_kg"], vals[6], product_id=old["id"])
            else:
                cur = conn.execute(
                    "INSERT INTO products (sku, name, category, unit, price, cost, deposit_price, price_per_kg, min_stock) VALUES (?,?,?,?,?,?,?,?,?)",
                    (sku,) + vals,
                )
                record_rate_change(conn, None, vals[6], product_id=cur.lastrowid)
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
    prev = conn.execute("SELECT price_per_kg FROM products WHERE id=?", (pid,)).fetchone()
    if prev:
        record_rate_change(conn, prev["price_per_kg"], d.get("price_per_kg"), product_id=pid)
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
        conn.execute("UPDATE promo_packages SET active=0 WHERE id IN "
                     "(SELECT package_id FROM promo_package_items WHERE product_id=?)", (pid,))
        audit(conn, "ARCHIVE", "PRODUCTS", pid, prod["name"])
        conn.commit()
        conn.close()
        return jsonify(ok=True, archived=True)
    conn.execute("DELETE FROM inventory WHERE product_id=?", (pid,))
    conn.execute("DELETE FROM empty_cylinder_inventory WHERE product_id=?", (pid,))
    conn.execute("DELETE FROM swapped_empty_inventory WHERE product_id=?", (pid,))
    conn.execute("DELETE FROM discounts WHERE product_id=?", (pid,))
    conn.execute("UPDATE promo_packages SET active=0 WHERE id IN "
                 "(SELECT package_id FROM promo_package_items WHERE product_id=?)", (pid,))
    conn.execute("DELETE FROM promo_package_items WHERE product_id=?", (pid,))
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
    base = """SELECT c.*, ca.username portal_username, ca.active portal_active
               FROM customers c LEFT JOIN customer_accounts ca ON ca.customer_id = c.id"""
    if q:
        like = f"%{q}%"
        rows = conn.execute(
            base + " WHERE c.name LIKE ? OR c.business_name LIKE ? OR c.account_no LIKE ? OR c.phone LIKE ? ORDER BY c.name",
            (like, like, like, like),
        ).fetchall()
    else:
        rows = conn.execute(base + " ORDER BY c.name").fetchall()
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
        conn.execute("DELETE FROM customer_accounts WHERE customer_id=?", (cid,))
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


# Sale / payment times are stored in UTC (SQLite datetime('now')), but the
# business runs on Philippine time. The SOA compares and shows dates in local
# time; otherwise a billing made before 8 AM landed on the previous day — and,
# with a From date of today, went into the Beginning Balance instead of
# Charges.
APP_TZ_OFFSET_HOURS = float(os.environ.get("APP_TZ_OFFSET_HOURS", "8"))


def _local_ts(ts):
    if not ts:
        return ts
    try:
        dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ts
    return (dt + timedelta(hours=APP_TZ_OFFSET_HOURS)).strftime("%Y-%m-%d %H:%M:%S")


def build_customer_ledger(conn, cid, date_from=None, date_to=None, breakdown=False):
    """Chronological charges (sales) + payments for a customer, with running balance.
    Used for both the on-screen Account Ledger and the printable Statement of Account."""
    sales = conn.execute(
        """SELECT s.id, s.sale_no, s.ts, s.total, s.status, s.is_opening_balance, s.is_billing, r.invoice_no
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
            # Arrears are stored on their as-of date (already local time).
            events.append({"date": s["ts"], "type": "PREVIOUS ARREARS", "ref": s["invoice_no"] or s["sale_no"],
                           "debit": s["total"], "credit": 0, "opening": True})
        elif abs(s["total"] or 0) < 0.005:
            # Nothing charged (e.g. a Commercial account's 50kg delivery,
            # billed separately) — keep it off the SOA.
            continue
        else:
            events.append({"date": _local_ts(s["ts"]), "type": "BILLING" if s["is_billing"] else "SALE",
                           "ref": s["invoice_no"] if s["is_billing"] and s["invoice_no"] else s["sale_no"],
                           "debit": s["total"], "credit": 0})
    for p in payments:
        events.append({"date": _local_ts(p["ts"]), "type": "PAYMENT", "ref": f"{p['sale_no']} ({p['method']})", "debit": 0, "credit": p["amount"]})
    events.sort(key=lambda e: e["date"])

    previous_arrears = 0.0   # uploaded arrears: always the Beginning Balance
    balance_forward = 0.0    # charges less payments dated before the From date
    in_range_events = []
    for e in events:
        before_range = date_from and e["date"] < date_from
        after_range = date_to and e["date"] > date_to + " 23:59:59"
        # Uploaded previous arrears always roll into the Beginning Balance
        # (as long as they're dated within the statement period or earlier),
        # rather than appearing as a charge line on the statement. New
        # billings and sales are never folded into it: in the period they
        # are Charges.
        if e.get("opening"):
            if not after_range:
                previous_arrears += e["debit"]
            continue
        if before_range:
            balance_forward += e["debit"] - e["credit"]
        elif not after_range:
            in_range_events.append(e)

    beginning_balance = round(previous_arrears + balance_forward, 2)
    running = beginning_balance
    out = []
    for e in in_range_events:
        running = round(running + e["debit"] - e["credit"], 2)
        out.append({**{k: v for k, v in e.items() if k != "opening"}, "balance": running})
    if breakdown:
        return out, beginning_balance, running, {"previous_arrears": round(previous_arrears, 2),
                                                 "balance_forward": round(balance_forward, 2)}
    return out, beginning_balance, running


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
    entries, beginning, ending, parts = build_customer_ledger(conn, cid, date_from, date_to, breakdown=True)
    conn.close()
    return jsonify(customer=dict(cust), entries=entries, beginning_balance=beginning, ending_balance=ending, **parts)


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
    entries, beginning, ending, parts = build_customer_ledger(conn, cid, date_from, date_to, breakdown=True)
    conn.close()
    fname = f"SOA-{cust['account_no']}-{datetime.now().strftime('%Y%m%d')}.pdf"
    return soa_pdf_response(dict(cust), entries, beginning, ending, fname, **parts)


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
    elif u["role"] not in ("ADMIN", "OPERATION_HEAD", "MANAGER"):
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
    if u["role"] not in ("ADMIN", "OPERATION_HEAD", "MANAGER", "ORDER_TAKER"):
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


# ---------------------------------------------------------------- customer portal
# Customers log in at /portal to place orders, follow their order status, see
# their purchases and, for credit / terms accounts, their billing and SOA.
#
# Security model:
#  - Portal logins live in `customer_accounts`, separate from staff `users`,
#    and the portal session is stored under session["customer"]. Every staff
#    route checks session["user"], so a customer login can never reach them.
#  - Every portal query is scoped to the logged-in customer_id taken from the
#    session, never from the request.
#  - 5 wrong passwords lock the login for 15 minutes. Resetting the password
#    or disabling the login bumps session_version, which ends open sessions.

PORTAL_MAX_FAILED = 5
PORTAL_LOCK_MINUTES = 15
PORTAL_MIN_PASSWORD = 8
PORTAL_MAX_OPEN_ORDERS = 10


def _portal_audit(conn, action, record_id=None, details=None, username=None):
    acct = session.get("customer") or {}
    conn.execute(
        "INSERT INTO audit_logs (user_id, username, action, module, record_id, details) VALUES (?,?,?,?,?,?)",
        (None, f"portal:{username or acct.get('username', '?')}", action, "CUSTOMER_PORTAL",
         str(record_id) if record_id is not None else None, details),
    )


def portal_required(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        sess = session.get("customer")
        if not sess:
            return jsonify(error="Please log in"), 401
        conn = get_db()
        row = conn.execute(
            """SELECT ca.id account_id, ca.username, ca.active, ca.session_version, c.*
               FROM customer_accounts ca JOIN customers c ON c.id = ca.customer_id WHERE ca.id=?""",
            (sess.get("account_id"),),
        ).fetchone()
        conn.close()
        if (not row or not row["active"] or row["status"] != "ACTIVE"
                or row["session_version"] != sess.get("session_version")):
            session.pop("customer", None)
            return jsonify(error="Your session has ended. Please log in again."), 401
        g.portal = dict(row)
        return fn(*a, **kw)
    return wrapper


def _portal_is_credit(conn, cust):
    if (cust["terms_days"] or 0) > 0 or (cust["credit_limit"] or 0) > 0:
        return True
    return conn.execute("SELECT 1 FROM receivables WHERE customer_id=? LIMIT 1", (cust["id"],)).fetchone() is not None


def _portal_profile(conn, cust):
    keys = ("id", "account_no", "name", "business_name", "phone", "address", "category", "terms_days", "credit_limit")
    out = {k: cust[k] for k in keys}
    out["username"] = cust["username"]
    out["is_credit"] = _portal_is_credit(conn, cust)
    out["has_rate"] = is_commercial_account(cust) or conn.execute(
        "SELECT 1 FROM commercial_deliveries WHERE customer_id=? AND status!='CANCELLED' LIMIT 1",
        (cust["id"],)).fetchone() is not None
    return out


# ---- staff side: create / reset / disable a customer's portal login

@app.get("/api/customers/<int:cid>/portal-account")
@roles_required("ADMIN", "MANAGER", "ACCOUNTING")
def get_portal_account(cid):
    conn = get_db()
    cust = conn.execute("SELECT id, account_no, name FROM customers WHERE id=?", (cid,)).fetchone()
    if not cust:
        conn.close()
        return jsonify(error="Not found"), 404
    acct = conn.execute(
        "SELECT username, active, last_login, locked_until, failed_attempts, created_at FROM customer_accounts WHERE customer_id=?",
        (cid,)).fetchone()
    conn.close()
    locked = bool(acct and acct["locked_until"] and acct["locked_until"] > datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return jsonify(customer=dict(cust), account=dict(acct) if acct else None, locked=locked,
                   suggested_username=cust["account_no"].lower())


@app.post("/api/customers/<int:cid>/portal-account")
@roles_required("ADMIN", "MANAGER", "ACCOUNTING")
def save_portal_account(cid):
    d = request.get_json(force=True) or {}
    username = (d.get("username") or "").strip().lower()
    password = d.get("password") or ""
    active = 1 if d.get("active", True) else 0
    if not re.fullmatch(r"[a-z0-9._@-]{3,60}", username):
        return jsonify(error="Username must be 3-60 characters: letters, numbers, . _ - or @ (no spaces)"), 400
    if password and len(password) < PORTAL_MIN_PASSWORD:
        return jsonify(error=f"Password must be at least {PORTAL_MIN_PASSWORD} characters"), 400
    conn = get_db()
    if not conn.execute("SELECT 1 FROM customers WHERE id=?", (cid,)).fetchone():
        conn.close()
        return jsonify(error="Customer not found"), 404
    taken = conn.execute("SELECT customer_id FROM customer_accounts WHERE username=?", (username,)).fetchone()
    if taken and taken["customer_id"] != cid:
        conn.close()
        return jsonify(error="That username is already used by another customer"), 400
    existing = conn.execute("SELECT * FROM customer_accounts WHERE customer_id=?", (cid,)).fetchone()
    if not existing:
        if not password:
            conn.close()
            return jsonify(error="Set a password for the new portal login"), 400
        conn.execute(
            "INSERT INTO customer_accounts (customer_id, username, password_hash, active, created_by) VALUES (?,?,?,?,?)",
            (cid, username, generate_password_hash(password), active, session["user"]["id"]))
        audit(conn, "CREATE", "CUSTOMER_PORTAL", cid, f"portal login '{username}' created")
    else:
        # Ending open sessions on a password reset, a disable, or a username change.
        bump = 1 if (password or not active or username != existing["username"]) else 0
        conn.execute(
            """UPDATE customer_accounts SET username=?, active=?, session_version=session_version+?,
                   failed_attempts=0, locked_until=NULL WHERE customer_id=?""",
            (username, active, bump, cid))
        if password:
            conn.execute("UPDATE customer_accounts SET password_hash=? WHERE customer_id=?",
                         (generate_password_hash(password), cid))
        audit(conn, "UPDATE", "CUSTOMER_PORTAL", cid,
              f"portal login '{username}'" + (" password reset" if password else "")
              + ("" if active else " disabled"))
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.delete("/api/customers/<int:cid>/portal-account")
@roles_required("ADMIN", "MANAGER", "ACCOUNTING")
def delete_portal_account(cid):
    conn = get_db()
    cur = conn.execute("DELETE FROM customer_accounts WHERE customer_id=?", (cid,))
    if cur.rowcount:
        audit(conn, "DELETE", "CUSTOMER_PORTAL", cid, "portal login removed")
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ---- portal: auth

@app.post("/api/portal/login")
def portal_login():
    d = request.get_json(force=True) or {}
    username = (d.get("username") or "").strip().lower()
    password = d.get("password") or ""
    generic = "Invalid username or password"
    conn = get_db()
    acct = conn.execute(
        """SELECT ca.*, c.status customer_status FROM customer_accounts ca
           JOIN customers c ON c.id = ca.customer_id WHERE ca.username=?""", (username,)).fetchone()
    if not acct:
        conn.close()
        check_password_hash(generate_password_hash("x"), password)  # similar timing either way
        return jsonify(error=generic), 401
    now = datetime.now()
    if acct["locked_until"] and acct["locked_until"] > now.strftime("%Y-%m-%d %H:%M:%S"):
        conn.close()
        return jsonify(error=f"Too many wrong passwords. Try again after {acct['locked_until'][11:16]}, "
                             "or ask GUFC to reset your password."), 429
    if not check_password_hash(acct["password_hash"], password):
        fails = acct["failed_attempts"] + 1
        locked_until = None
        if fails >= PORTAL_MAX_FAILED:
            locked_until = (now + timedelta(minutes=PORTAL_LOCK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
            fails = 0
            _portal_audit(conn, "LOCKED", acct["customer_id"], "too many failed logins", username=username)
        conn.execute("UPDATE customer_accounts SET failed_attempts=?, locked_until=? WHERE id=?",
                     (fails, locked_until, acct["id"]))
        conn.commit()
        conn.close()
        return jsonify(error=generic), 401
    if not acct["active"] or acct["customer_status"] != "ACTIVE":
        conn.close()
        return jsonify(error="This portal login is disabled. Please contact GUFC."), 403
    conn.execute("UPDATE customer_accounts SET failed_attempts=0, locked_until=NULL, last_login=datetime('now') WHERE id=?",
                 (acct["id"],))
    session["customer"] = {"account_id": acct["id"], "customer_id": acct["customer_id"],
                           "username": acct["username"], "session_version": acct["session_version"]}
    _portal_audit(conn, "LOGIN", acct["customer_id"])
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.post("/api/portal/logout")
def portal_logout():
    session.pop("customer", None)
    return jsonify(ok=True)


@app.get("/api/portal/me")
@portal_required
def portal_me():
    conn = get_db()
    out = _portal_profile(conn, g.portal)
    conn.close()
    return jsonify(out)


@app.post("/api/portal/change-password")
@portal_required
def portal_change_password():
    d = request.get_json(force=True) or {}
    new = d.get("new_password") or ""
    if len(new) < PORTAL_MIN_PASSWORD:
        return jsonify(error=f"New password must be at least {PORTAL_MIN_PASSWORD} characters"), 400
    conn = get_db()
    row = conn.execute("SELECT password_hash FROM customer_accounts WHERE id=?", (g.portal["account_id"],)).fetchone()
    if not check_password_hash(row["password_hash"], d.get("current_password") or ""):
        conn.close()
        return jsonify(error="Current password is incorrect"), 400
    conn.execute("UPDATE customer_accounts SET password_hash=?, session_version=session_version+1 WHERE id=?",
                 (generate_password_hash(new), g.portal["account_id"]))
    _portal_audit(conn, "CHANGE_PASSWORD", g.portal["id"])
    conn.commit()
    ver = conn.execute("SELECT session_version FROM customer_accounts WHERE id=?", (g.portal["account_id"],)).fetchone()[0]
    conn.close()
    session["customer"] = {**session["customer"], "session_version": ver}  # stay logged in here only
    return jsonify(ok=True)


# ---- portal: ordering and order status

def _portal_order_status(o):
    if o["status"] == "CANCELLED":
        return "Cancelled", "Your order was cancelled."
    if o["status"] == "FULFILLED":
        return "Completed", f"Completed on {(o['fulfilled_at'] or '')[:10]}."
    if o["showroom_location_id"]:
        return "Confirmed", f"Confirmed and being prepared by {o['location_name'] or 'our team'}."
    return "Received", "We received your order and will confirm it shortly."


@app.get("/api/portal/products")
@portal_required
def portal_products():
    conn = get_db()
    rows = conn.execute("SELECT id, name, unit, price, category FROM products WHERE active=1 ORDER BY name").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.get("/api/portal/orders")
@portal_required
def portal_orders():
    conn = get_db()
    rows = conn.execute(
        """SELECT bo.id, bo.order_no, bo.order_date, bo.status, bo.showroom_location_id, bo.delivery_address,
                  bo.notes, bo.source, bo.fulfilled_at, l.name location_name
           FROM booked_orders bo LEFT JOIN locations l ON l.id = bo.showroom_location_id
           WHERE bo.customer_id=? ORDER BY bo.order_date DESC, bo.id DESC LIMIT 200""",
        (g.portal["id"],)).fetchall()
    out = []
    for o in rows:
        items = conn.execute(
            """SELECT p.name product_name, boi.qty, p.price FROM booked_order_items boi
               JOIN products p ON p.id = boi.product_id WHERE boi.order_id=?""", (o["id"],)).fetchall()
        label, detail = _portal_order_status(o)
        out.append({**dict(o), "status_label": label, "status_detail": detail,
                    "can_cancel": o["status"] == "PENDING" and not o["showroom_location_id"],
                    "items": [dict(i) for i in items],
                    "estimated_total": round(sum(i["qty"] * (i["price"] or 0) for i in items), 2)})
    conn.close()
    return jsonify(out)


@app.post("/api/portal/orders")
@portal_required
def portal_place_order():
    d = request.get_json(force=True) or {}
    items = d.get("items") or []
    address = (d.get("delivery_address") or "").strip() or (g.portal["address"] or "")
    if not items:
        return jsonify(error="Add at least one product"), 400
    if len(items) > 20:
        return jsonify(error="Too many line items"), 400
    if not address:
        return jsonify(error="Delivery address is required"), 400
    conn = get_db()
    open_orders = conn.execute("SELECT COUNT(*) FROM booked_orders WHERE customer_id=? AND status='PENDING'",
                               (g.portal["id"],)).fetchone()[0]
    if open_orders >= PORTAL_MAX_OPEN_ORDERS:
        conn.close()
        return jsonify(error=f"You already have {open_orders} open orders. Please wait for them to be completed, "
                             "or call us."), 400
    merged = {}
    for it in items:
        try:
            pid, qty = int(it.get("product_id")), int(it.get("qty") or 0)
        except (TypeError, ValueError):
            conn.close()
            return jsonify(error="Invalid product or quantity"), 400
        if qty <= 0 or qty > 500:
            conn.close()
            return jsonify(error="Quantity must be between 1 and 500"), 400
        if not conn.execute("SELECT 1 FROM products WHERE id=? AND active=1", (pid,)).fetchone():
            conn.close()
            return jsonify(error="One of the selected products is no longer available. Please reload the page."), 400
        merged[pid] = merged.get(pid, 0) + qty
    order_no = next_number(conn, "ORD", "booked_orders", "order_no")
    cur = conn.execute(
        """INSERT INTO booked_orders (order_no, customer_id, customer_phone, delivery_address, source, notes)
           VALUES (?,?,?,?,?,?)""",
        (order_no, g.portal["id"], g.portal["phone"] or "", address[:500], "CUSTOMER",
         ("[Customer Portal] " + (d.get("notes") or "").strip())[:500]))
    for pid, qty in merged.items():
        conn.execute("INSERT INTO booked_order_items (order_id, product_id, qty) VALUES (?,?,?)", (cur.lastrowid, pid, qty))
    audit_detail = f"{order_no} placed in customer portal"
    _portal_audit(conn, "CREATE_ORDER", cur.lastrowid, audit_detail)
    conn.commit()
    conn.close()
    return jsonify(order_no=order_no), 201


@app.post("/api/portal/orders/<int:oid>/cancel")
@portal_required
def portal_cancel_order(oid):
    conn = get_db()
    o = conn.execute("SELECT * FROM booked_orders WHERE id=? AND customer_id=?", (oid, g.portal["id"])).fetchone()
    if not o:
        conn.close()
        return jsonify(error="Order not found"), 404
    if o["status"] != "PENDING" or o["showroom_location_id"]:
        conn.close()
        return jsonify(error="This order is already confirmed and can't be cancelled online. Please call us."), 400
    conn.execute("UPDATE booked_orders SET status='CANCELLED' WHERE id=?", (oid,))
    _portal_audit(conn, "CANCEL_ORDER", oid, f"{o['order_no']} cancelled by customer")
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ---- portal: purchases, billing and SOA

@app.get("/api/portal/purchases")
@portal_required
def portal_purchases():
    date_from, date_to = request.args.get("from") or None, request.args.get("to") or None
    sql = """SELECT s.id, s.sale_no, s.dr_no, s.ts, s.sale_type, s.status, s.subtotal, s.discount, s.total,
                    s.paid, s.balance, l.name location_name, r.invoice_no, r.due_date, r.status invoice_status
             FROM sales s JOIN locations l ON l.id = s.location_id LEFT JOIN receivables r ON r.sale_id = s.id
             WHERE s.customer_id=? AND s.is_opening_balance=0 AND s.is_billing=0"""
    params = [g.portal["id"]]
    if date_from:
        sql += " AND date(s.ts) >= date(?)"; params.append(date_from)
    if date_to:
        sql += " AND date(s.ts) <= date(?)"; params.append(date_to)
    conn = get_db()
    rows = conn.execute(sql + " ORDER BY s.ts DESC, s.id DESC LIMIT 500", params).fetchall()
    out = []
    for s in rows:
        items = conn.execute(
            """SELECT product_name, qty, price, subtotal, sale_mode, COALESCE(discount_amount,0) discount, swap_brand
               FROM sale_items WHERE sale_id=? ORDER BY id""", (s["id"],)).fetchall()
        out.append({**dict(s), "items": [dict(i) for i in items]})
    conn.close()
    done = [s for s in out if s["status"] == "COMPLETED"]
    return jsonify(purchases=out, totals={"count": len(done), "amount": round(sum(s["total"] for s in done), 2),
                                          "cylinders": sum(i["qty"] for s in done for i in s["items"]
                                                           if float(i["qty"]).is_integer())})


@app.get("/api/portal/billing")
@portal_required
def portal_billing():
    conn = get_db()
    cid = g.portal["id"]
    for r in conn.execute("SELECT id FROM receivables WHERE customer_id=? AND balance > 0.009", (cid,)).fetchall():
        recompute_receivable_status(conn, r["id"])
    conn.commit()
    invoices = [dict(r) for r in conn.execute(
        """SELECT r.id, r.invoice_no, r.invoice_date, r.due_date, r.amount, r.paid, r.balance, r.status,
                  s.sale_no, s.dr_no, s.is_opening_balance
           FROM receivables r JOIN sales s ON s.id = r.sale_id
           WHERE r.customer_id=? AND s.status='COMPLETED' ORDER BY r.due_date DESC, r.id DESC""", (cid,)).fetchall()]
    payments = [dict(r) for r in conn.execute(
        """SELECT p.ts, p.amount, p.method, p.reference, s.sale_no, r.invoice_no
           FROM payments p JOIN sales s ON s.id = p.sale_id LEFT JOIN receivables r ON r.sale_id = s.id
           WHERE s.customer_id=? AND s.sale_type='CREDIT' ORDER BY p.ts DESC LIMIT 100""", (cid,)).fetchall()]
    is_credit = _portal_is_credit(conn, g.portal)
    conn.close()
    today = datetime.now().date().isoformat()
    open_inv = [i for i in invoices if i["balance"] > 0.009]
    outstanding = round(sum(i["balance"] for i in open_inv), 2)
    overdue = round(sum(i["balance"] for i in open_inv if i["due_date"] < today), 2)
    next_due = min((i for i in open_inv if i["due_date"] >= today), key=lambda i: i["due_date"], default=None)
    limit = g.portal["credit_limit"] or 0
    return jsonify(
        is_credit=is_credit, terms_days=g.portal["terms_days"], credit_limit=limit,
        available_credit=round(max(limit - outstanding, 0), 2) if limit else None,
        outstanding=outstanding, overdue=overdue, open_count=len(open_inv),
        next_due={"invoice_no": next_due["invoice_no"], "due_date": next_due["due_date"], "balance": next_due["balance"]}
        if next_due else None,
        invoices=invoices, payments=payments)


# ---- portal: latest rate per kg and rate movement
#
# A Commercial account is billed per kg at the rate Accounting set on the
# account; without one, at the 50kg product's default price per kg (see
# _commercial_rate). The customer's "effective rate" timeline merges both
# histories from rate_history, so a change in either shows as a movement.

def _portal_rate_product(conn, cid):
    row = conn.execute(
        """SELECT p.* FROM commercial_deliveries cd JOIN products p ON p.id = cd.product_id
           WHERE cd.customer_id=? AND cd.status!='CANCELLED'
           GROUP BY p.id ORDER BY COUNT(*) DESC, MAX(cd.id) DESC LIMIT 1""", (cid,)).fetchone()
    if row:
        return row
    for p in conn.execute("SELECT * FROM products WHERE active=1 ORDER BY price_per_kg > 0 DESC, id").fetchall():
        if is_commercial_cylinder(p):
            return p
    return None


def _rate_movements(conn, cid, product_id):
    rows = conn.execute(
        """SELECT id, customer_id, product_id, new_rate, changed_at FROM rate_history
           WHERE customer_id=? OR (customer_id IS NULL AND product_id=?)
           ORDER BY changed_at, id""", (cid, product_id or -1)).fetchall()
    cust_rate = prod_rate = last = None
    out = []
    for r in rows:
        if r["customer_id"]:
            cust_rate = r["new_rate"]
        else:
            prod_rate = r["new_rate"]
        eff = cust_rate or prod_rate
        src = "ACCOUNT" if cust_rate else "STANDARD"
        if eff is None or (last is not None and abs(eff - last) < 0.0001):
            if out and eff is not None:
                out[-1]["source"] = src  # same rate, now from the other source
            continue
        out.append({"date": _local_ts(r["changed_at"]), "old_rate": last, "new_rate": round(eff, 4), "source": src,
                    "change": round(eff - last, 4) if last is not None else None,
                    "change_pct": round((eff - last) / last * 100, 2) if last else None})
        last = eff
    return out


@app.get("/api/portal/rates")
@portal_required
def portal_rates():
    cid = g.portal["id"]
    conn = get_db()
    product = _portal_rate_product(conn, cid)
    acct_rate = g.portal["commercial_rate"] if (g.portal.get("commercial_rate") or 0) > 0 else None
    std_rate = float(product["price_per_kg"]) if product and (product["price_per_kg"] or 0) > 0 else None
    current = acct_rate or std_rate
    movements = _rate_movements(conn, cid, product["id"] if product else None)
    billings = [dict(r) for r in conn.execute(
        """SELECT cd.delivery_no, cd.delivery_date, cd.billing_type, cd.price_per_kg, cd.billed_amount,
                  p.name product_name, r.invoice_no
           FROM commercial_deliveries cd JOIN products p ON p.id = cd.product_id
           LEFT JOIN receivables r ON r.sale_id = cd.sale_id
           WHERE cd.customer_id=? AND cd.status='BILLED' AND cd.price_per_kg > 0
           ORDER BY cd.delivery_date DESC, cd.id DESC LIMIT 100""", (cid,)).fetchall()]
    conn.close()
    for b in billings:
        b["delivery_date"] = _local_ts(b["delivery_date"])
        b["kg"] = round((b["billed_amount"] or 0) / b["price_per_kg"], 2)

    last = movements[-1] if movements else None
    if current and last and abs(last["new_rate"] - current) > 0.0001:
        last = None  # history doesn't reach the current rate; don't show a stale change
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=APP_TZ_OFFSET_HOURS)
    year_ago = (now_local - timedelta(days=365)).strftime("%Y-%m-%d")
    recent = [m for m in movements if (m["date"] or "") >= year_ago]
    # The rate in effect at the start of the 12 months counts toward high / low.
    before = [m for m in movements if (m["date"] or "") < year_ago]
    span = ([before[-1]["new_rate"]] if before else []) + [m["new_rate"] for m in recent]
    if current:
        span.append(current)
    month = now_local.strftime("%Y-%m")

    def avg_billed(prefix):
        rows = [b for b in billings if (b["delivery_date"] or "").startswith(prefix)]
        kg = sum(b["kg"] for b in rows)
        return round(sum(b["billed_amount"] or 0 for b in rows) / kg, 4) if kg else None

    first_of_month = datetime.strptime(month + "-01", "%Y-%m-%d")
    prev_month = (first_of_month - timedelta(days=1)).strftime("%Y-%m")
    return jsonify(
        current={"rate": current, "source": "ACCOUNT" if acct_rate else ("STANDARD" if std_rate else None),
                 "product_name": product["name"] if product else None,
                 "since": last["date"] if last else None,
                 "previous_rate": last["old_rate"] if last else None,
                 "change": last["change"] if last else None,
                 "change_pct": last["change_pct"] if last else None},
        summary={"changes_12m": sum(1 for m in recent if m["old_rate"] is not None),
                 "high_12m": max(span) if span else None, "low_12m": min(span) if span else None,
                 "avg_billed_this_month": avg_billed(month), "avg_billed_last_month": avg_billed(prev_month),
                 "this_month": month, "last_month": prev_month},
        movements=list(reversed(movements)), billings=billings)


def _portal_soa_args():
    return request.args.get("from") or None, request.args.get("to") or None


@app.get("/api/portal/soa")
@portal_required
def portal_soa():
    conn = get_db()
    entries, beginning, ending, parts = build_customer_ledger(conn, g.portal["id"], *_portal_soa_args(), breakdown=True)
    conn.close()
    return jsonify(entries=entries, beginning_balance=beginning, ending_balance=ending, **parts)


@app.get("/api/portal/soa/export")
@portal_required
def portal_soa_export():
    conn = get_db()
    entries, beginning, ending, parts = build_customer_ledger(conn, g.portal["id"], *_portal_soa_args(), breakdown=True)
    cust = dict(conn.execute("SELECT * FROM customers WHERE id=?", (g.portal["id"],)).fetchone())
    _portal_audit(conn, "DOWNLOAD_SOA", g.portal["id"])
    conn.commit()
    conn.close()
    fname = f"SOA-{cust['account_no']}-{datetime.now().strftime('%Y%m%d')}.pdf"
    return soa_pdf_response(cust, entries, beginning, ending, fname, **parts)


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
    if u["role"] in ("ADMIN", "OPERATION_HEAD", "MANAGER"):
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
    if u["role"] not in ("ADMIN", "OPERATION_HEAD", "MANAGER"):
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
    location_id = d.get("location_id") if u["role"] in ("ADMIN", "OPERATION_HEAD", "MANAGER") else u["location_id"]
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
    if u["role"] not in ("ADMIN", "OPERATION_HEAD", "MANAGER") and report["reported_by"] != u["id"]:
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
    if not is_admin_like(u["role"]) and location_id and location_id != u["location_id"]:
        return jsonify(error="Forbidden: not your location"), 403
    if not location_id and not is_admin_like(u["role"]):
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


LEDGER_STOCKS = {
    "FULL": ("inventory_movements", "inventory", "Full"),
    "EMPTY": ("empty_cylinder_movements", "empty_cylinder_inventory", "Empty"),
    "SWAPPED": ("swapped_empty_movements", "swapped_empty_inventory", "Swapped"),
}


def _ledger_query(args):
    """Movements of full cylinders, empties and swapped (other-brand) empties,
    one stock or all of them together, filtered and with the balance after
    each line. The balance is anchored on the count on hand now, so it always
    ends at what the Inventory page shows."""
    stock = (args.get("stock") or "ALL").upper()
    stocks = [stock] if stock in LEDGER_STOCKS else list(LEDGER_STOCKS)
    parts = []
    for key in stocks:
        mtable, itable, label = LEDGER_STOCKS[key]
        brand = "m.brand" if key == "SWAPPED" else "NULL"
        brand_join = " AND i.brand=m.brand" if key == "SWAPPED" else ""
        parts.append(f"""
            SELECT '{key}' stock, '{label}' stock_label, m.id, m.ts, m.location_id, m.product_id, {brand} brand,
                   m.qty_change, m.type, m.reference, m.notes, m.user_id,
                   COALESCE((SELECT i.quantity FROM {itable} i WHERE i.location_id=m.location_id
                             AND i.product_id=m.product_id{brand_join}), 0)
                   - SUM(m.qty_change) OVER (PARTITION BY m.location_id, m.product_id, {brand})
                   + SUM(m.qty_change) OVER (PARTITION BY m.location_id, m.product_id, {brand}
                                              ORDER BY m.ts, m.id ROWS UNBOUNDED PRECEDING) balance
            FROM {mtable} m""")
    tz = f"'{APP_TZ_OFFSET_HOURS:+g} hours'"
    sql = ("SELECT x.*, p.name product_name, l.name location_name, u.name user_name FROM ("
           + " UNION ALL ".join(parts) +
           ") x JOIN products p ON p.id=x.product_id JOIN locations l ON l.id=x.location_id "
           "LEFT JOIN users u ON u.id=x.user_id WHERE 1=1")
    params = []
    for col, name in (("x.product_id", "product_id"), ("x.location_id", "location_id")):
        v = args.get(name, type=int)
        if v:
            sql += f" AND {col}=?"; params.append(v)
    if args.get("type"):
        sql += " AND x.type=?"; params.append(args.get("type"))
    if args.get("date_from"):
        sql += f" AND date(x.ts, {tz}) >= date(?)"; params.append(args.get("date_from"))
    if args.get("date_to"):
        sql += f" AND date(x.ts, {tz}) <= date(?)"; params.append(args.get("date_to"))
    q = (args.get("q") or "").strip()
    if q:
        sql += " AND (x.reference LIKE ? OR x.notes LIKE ? OR x.type LIKE ? OR x.brand LIKE ?)"
        params += [f"%{q}%"] * 4
    direction = "ASC" if (args.get("order") or "").lower() == "asc" else "DESC"
    sql += f" ORDER BY x.ts {direction}, x.stock, x.id {direction}"
    return sql, params


def _ledger_row(r):
    d = dict(r)
    d["ts"] = _local_ts(d["ts"])
    for k in ("qty_change", "balance"):
        if isinstance(d[k], float):
            d[k] = round(d[k], 3)
            if d[k].is_integer():
                d[k] = int(d[k])
    return d


@app.get("/api/inventory/movements")
@login_required
def inventory_movements():
    limit = max(1, min(request.args.get("limit", 100, type=int), 1000))
    offset = max(0, request.args.get("offset", 0, type=int))
    sql, params = _ledger_query(request.args)
    conn = get_db()
    total = conn.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
    rows = conn.execute(sql + " LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
    conn.close()
    return jsonify(rows=[_ledger_row(r) for r in rows], total=total, offset=offset, limit=limit)


@app.get("/api/inventory/movements/export")
@login_required
def export_inventory_movements():
    sql, params = _ledger_query(request.args)
    conn = get_db()
    rows = [_ledger_row(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    headers = ["Date", "Stock", "Location", "Product", "Brand", "Change", "Balance", "Type", "Reference", "Notes", "User"]
    data = [(r["ts"], r["stock_label"], r["location_name"], r["product_name"], r["brand"] or "", r["qty_change"],
             r["balance"], r["type"], r["reference"] or "", r["notes"] or "", r["user_name"] or "") for r in rows]
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return xlsx_response(headers, data, f"inventory-ledger-{stamp}.xlsx", "Inventory Ledger")


@app.get("/api/inventory/empties")
@login_required
def get_empty_cylinders():
    u = session["user"]
    location_id = request.args.get("location_id", type=int)
    if u["role"] not in ("ADMIN", "OPERATION_HEAD", "MANAGER") and location_id and location_id != u["location_id"]:
        return jsonify(error="Forbidden: not your location"), 403
    if not location_id and u["role"] not in ("ADMIN", "OPERATION_HEAD", "MANAGER"):
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
    if u["role"] not in ("ADMIN", "OPERATION_HEAD", "MANAGER") and location_id and location_id != u["location_id"]:
        return jsonify(error="Forbidden: not your location"), 403
    if not location_id and u["role"] not in ("ADMIN", "OPERATION_HEAD", "MANAGER"):
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
    if not reason:
        return jsonify(error="A reason is required for adjustments"), 400
    conn = get_db()
    problem = _check_location_product(conn, location_id, product_id)
    if problem:
        conn.close()
        return jsonify(error=problem), 400
    try:
        row = conn.execute("SELECT quantity FROM swapped_empty_inventory WHERE location_id=? AND product_id=? AND brand=?",
                           (location_id, product_id, brand)).fetchone()
        delta, target = _parse_adjust_delta(d.get("delta"), row["quantity"] if row else 0)
        if delta > 0:
            add_swapped_empties(conn, location_id, product_id, brand, delta, "ADJUSTMENT", "MANUAL-ADMIN", reason)
        else:
            remove_swapped_empties(conn, location_id, product_id, brand, -delta, "ADJUSTMENT", "MANUAL-ADMIN", reason)
        if target is not None:
            conn.execute("UPDATE swapped_empty_inventory SET quantity=? WHERE location_id=? AND product_id=? AND brand=?",
                         (target, location_id, product_id, brand))
        audit(conn, "ADJUST", "SWAPPED EMPTIES", f"{location_id}:{product_id}:{brand}", f"delta={delta} reason={reason}")
        conn.commit()
    except ValueError as e:
        conn.rollback()
        conn.close()
        return jsonify(error=str(e)), 400
    conn.close()
    return jsonify(ok=True)


def _parse_adjust_delta(raw, current):
    """Quantity change for a manual adjustment. Normally a whole number; a
    decimal is accepted only when it turns a fractional count (left behind by
    an old kg-based line) back into a whole one, e.g. 55.9 -> 55 with -0.9.
    Returns (delta, target) where target is the whole count to store exactly,
    or None for an ordinary whole-number change."""
    try:
        delta = float(raw)
    except (TypeError, ValueError):
        raise ValueError("delta must be a number (positive to add, negative to remove)")
    if abs(delta) < 1e-9:
        raise ValueError("delta cannot be zero")
    current = float(current or 0)
    if float(current).is_integer():
        if not float(delta).is_integer():
            raise ValueError("delta must be a whole number (positive to add, negative to remove)")
        return int(delta), None
    target = current + delta
    if abs(target - round(target)) > 0.001:
        raise ValueError(f"The count here is {round(current, 3):g}. Use a change that lands on a whole number "
                         f"(e.g. {round(int(current) - current, 3):g} to make it {int(current)}, "
                         f"or {round(int(current) + 1 - current, 3):g} to make it {int(current) + 1})")
    target = int(round(target))
    return round(target - current, 6), target


def _check_location_product(conn, location_id, product_id):
    """Return an error message if the location / product picked on screen no
    longer exists (e.g. products were deleted or re-encoded after the page was
    opened), else None."""
    if not conn.execute("SELECT 1 FROM locations WHERE id=?", (location_id,)).fetchone():
        return "That location no longer exists. Refresh the page (F5) and pick it again."
    p = conn.execute("SELECT active FROM products WHERE id=?", (product_id,)).fetchone()
    if not p:
        return "That product no longer exists (the product list may have changed). Refresh the page (F5) and pick it again."
    if not p["active"]:
        return "That product has been deleted. Pick another product."
    return None


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
    if not reason:
        return jsonify(error="A reason is required for empty cylinder adjustments"), 400

    conn = get_db()
    problem = _check_location_product(conn, location_id, product_id)
    if problem:
        conn.close()
        return jsonify(error=problem), 400
    try:
        row = conn.execute("SELECT quantity FROM empty_cylinder_inventory WHERE location_id=? AND product_id=?",
                           (location_id, product_id)).fetchone()
        delta, target = _parse_adjust_delta(d.get("delta"), row["quantity"] if row else 0)
        if delta > 0:
            add_empty_cylinders(conn, location_id, product_id, delta, "ADJUSTMENT", "MANUAL-ADMIN", reason)
        else:
            remove_empty_cylinders(conn, location_id, product_id, -delta, "ADJUSTMENT", "MANUAL-ADMIN", reason,
                                   allow_negative=target is not None and target >= 0)
        if target is not None:
            conn.execute("UPDATE empty_cylinder_inventory SET quantity=? WHERE location_id=? AND product_id=?",
                         (target, location_id, product_id))
        audit(conn, "ADJUST", "EMPTY CYLINDERS", f"{location_id}:{product_id}", f"delta={delta} reason={reason}")
        conn.commit()
    except ValueError as e:
        conn.rollback()
        conn.close()
        return jsonify(error=str(e)), 400
    conn.close()
    return jsonify(ok=True)


@app.get("/api/inventory/inbound")
@roles_or_module_required("ADMIN", "DISPATCHER", module="stock_import")
def list_inbound_stock():
    """Recent stock imports from suppliers, all locations."""
    location_id = request.args.get("location_id", type=int)
    conn = get_db()
    sql = """SELECT m.id, m.ts, m.qty_change qty, m.reference, m.notes, p.name product_name, l.name location_name,
                    u.name user_name
             FROM inventory_movements m JOIN products p ON p.id=m.product_id
             JOIN locations l ON l.id=m.location_id LEFT JOIN users u ON u.id=m.user_id
             WHERE m.type='INBOUND'"""
    params = []
    if location_id:
        sql += " AND m.location_id=?"; params.append(location_id)
    sql += " ORDER BY m.ts DESC, m.id DESC LIMIT 200"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/inventory/inbound")
@roles_or_module_required("ADMIN", "DISPATCHER", module="stock_import")
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
    problem = _check_location_product(conn, location_id, product_id)
    if problem:
        conn.close()
        return jsonify(error=problem), 400
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
    location_id = u["location_id"] if not is_admin_like(u["role"]) else d.get("location_id")
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
    if not reason:
        return jsonify(error="A reason is required for manual inventory adjustments"), 400

    conn = get_db()
    problem = _check_location_product(conn, location_id, product_id)
    if problem:
        conn.close()
        return jsonify(error=problem), 400
    try:
        row = conn.execute("SELECT quantity FROM inventory WHERE location_id=? AND product_id=?",
                           (location_id, product_id)).fetchone()
        delta, target = _parse_adjust_delta(delta, row["quantity"] if row else 0)
        adjust_inventory(conn, location_id, product_id, delta, "ADJUSTMENT", "MANUAL-ADMIN",
                          notes=reason, allow_negative=allow_negative or (target is not None and target >= 0))
        if target is not None:
            conn.execute("UPDATE inventory SET quantity=? WHERE location_id=? AND product_id=?",
                         (target, location_id, product_id))
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
        raise ValueError(f"Insufficient inventory: only {current} {_product_name(conn, product_id)} in stock here")
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


def compute_withholding_tax(gross):
    """1% creditable withholding tax on a VAT-inclusive sale: gross / 1.12 x 1%."""
    return round(float(gross) / 1.12 * 0.01, 2)


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


# ---------------------------------------------------------------- package promos
# A package is several products sold together for one package price, e.g.
# "11kg Refill + Stove" for 2,500. At the POS the cashier adds the package;
# the sale records one line per product (each comes out of stock, empties /
# deposits follow its mode) at the regular price, and the package savings
# are shown as that line's discount under the package name.

PACKAGE_MODES = ("REFILL", "DEPOSIT")


def _package_components(conn, package_id):
    return conn.execute(
        """SELECT pi.product_id, pi.qty, pi.sale_mode, p.name product_name, p.price, p.deposit_price, p.active
           FROM promo_package_items pi JOIN products p ON p.id=pi.product_id
           WHERE pi.package_id=? ORDER BY pi.id""", (package_id,)).fetchall()


def _component_unit_price(c):
    return round(float(c["price"]) + (float(c["deposit_price"] or 0) if c["sale_mode"] == "DEPOSIT" else 0), 2)


def _package_out(conn, pkg):
    comps = [dict(c) | {"unit_price": _component_unit_price(c)} for c in _package_components(conn, pkg["id"])]
    regular = round(sum(c["unit_price"] * c["qty"] for c in comps), 2)
    return {**dict(pkg), "items": comps, "regular_price": regular, "savings": round(regular - pkg["price"], 2),
            "live": _discount_is_live(pkg)}


def _package_payload(conn, d):
    name = (d.get("name") or "").strip()
    if not name:
        raise ValueError("Package name is required")
    try:
        price = round(float(d.get("price")), 2)
    except (TypeError, ValueError):
        raise ValueError("Package price must be a number")
    if price <= 0:
        raise ValueError("Package price must be greater than zero")
    start = (d.get("start_date") or "").strip() or None
    end = (d.get("end_date") or "").strip() or None
    for v in (start, end):
        if v:
            datetime.strptime(v, "%Y-%m-%d")
    if start and end and end < start:
        raise ValueError("End date is before start date")
    comps, seen = [], set()
    regular = 0.0
    for n, it in enumerate(d.get("items") or [], start=1):
        prod = conn.execute("SELECT * FROM products WHERE id=? AND active=1", (it.get("product_id"),)).fetchone()
        if not prod:
            raise ValueError(f"Item {n}: choose a product")
        try:
            qty = int(it.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty < 1:
            raise ValueError(f"Item {n}: quantity must be at least 1")
        mode = (it.get("sale_mode") or "REFILL").upper()
        if mode not in PACKAGE_MODES:
            raise ValueError(f"Item {n}: mode must be REFILL or DEPOSIT")
        if (prod["id"], mode) in seen:
            raise ValueError(f"{prod['name']} is listed twice — put the total quantity on one line")
        seen.add((prod["id"], mode))
        regular += (float(prod["price"]) + (float(prod["deposit_price"] or 0) if mode == "DEPOSIT" else 0)) * qty
        comps.append((prod["id"], qty, mode))
    if not comps:
        raise ValueError("Add at least one product to the package")
    if price > round(regular, 2) + 0.009:
        raise ValueError(f"Package price ({price:,.2f}) is more than the regular price of its items ({regular:,.2f})")
    active = 1 if d.get("active", True) in (True, 1, "1", "true") else 0
    return (name, price, start, end, active, (d.get("notes") or "").strip()), comps


@app.get("/api/packages")
@login_required
def list_packages():
    """?live=1 returns only packages usable today (for the POS)."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM promo_packages ORDER BY active DESC, name").fetchall()
    out = [_package_out(conn, r) for r in rows]
    conn.close()
    if request.args.get("live"):
        out = [r for r in out if r["live"] and r["items"] and all(c["active"] for c in r["items"])]
    return jsonify(out)


def _save_package(pid=None):
    conn = get_db()
    try:
        vals, comps = _package_payload(conn, request.get_json(force=True) or {})
        if pid:
            cur = conn.execute("UPDATE promo_packages SET name=?, price=?, start_date=?, end_date=?, active=?, notes=? "
                               "WHERE id=?", vals + (pid,))
            if not cur.rowcount:
                conn.close()
                return jsonify(error="Package not found"), 404
            conn.execute("DELETE FROM promo_package_items WHERE package_id=?", (pid,))
        else:
            pid = conn.execute("INSERT INTO promo_packages (name, price, start_date, end_date, active, notes) "
                               "VALUES (?,?,?,?,?,?)", vals).lastrowid
        for product_id, qty, mode in comps:
            conn.execute("INSERT INTO promo_package_items (package_id, product_id, qty, sale_mode) VALUES (?,?,?,?)",
                         (pid, product_id, qty, mode))
        audit(conn, "UPDATE" if request.method == "PUT" else "CREATE", "PACKAGES", pid,
              f"{vals[0]} price={vals[1]:.2f} items=" + ", ".join(f"{q}x#{p} {m}" for p, q, m in comps))
        conn.commit()
    except ValueError as e:
        conn.rollback(); conn.close()
        return jsonify(error=str(e)), 400
    conn.close()
    return jsonify(id=pid), (200 if request.method == "PUT" else 201)


@app.post("/api/packages")
@roles_required("ADMIN", "MANAGER")
def create_package():
    return _save_package()


@app.put("/api/packages/<int:pid>")
@roles_required("ADMIN", "MANAGER")
def update_package(pid):
    return _save_package(pid)


@app.delete("/api/packages/<int:pid>")
@roles_required("ADMIN", "MANAGER")
def delete_package(pid):
    conn = get_db()
    row = conn.execute("SELECT name FROM promo_packages WHERE id=?", (pid,)).fetchone()
    if not row:
        conn.close()
        return jsonify(error="Package not found"), 404
    # Past sales keep their own copy of the package name and amounts.
    conn.execute("DELETE FROM promo_package_items WHERE package_id=?", (pid,))
    conn.execute("DELETE FROM promo_packages WHERE id=?", (pid,))
    audit(conn, "DELETE", "PACKAGES", pid, row["name"])
    conn.commit()
    conn.close()
    return jsonify(ok=True)


def _expand_package(conn, it, lock):
    """Turns one package cart line into its product lines. Returns
    (lines, lock) where each line is (prod, qty, unit_price, sale_mode,
    savings) and lock is what a For Delivery order keeps."""
    try:
        n = int(it.get("qty"))
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        raise ValueError("Quantity must be positive")
    if lock and lock.get("package_id"):
        name, price, comps = lock["package_name"], float(lock["package_price"]), lock["components"]
    else:
        pkg = conn.execute("SELECT * FROM promo_packages WHERE id=?", (it.get("package_id"),)).fetchone()
        if not pkg:
            raise ValueError("Selected package no longer exists")
        if not _discount_is_live(pkg):
            raise ValueError(f"Package '{pkg['name']}' is not active today")
        rows = _package_components(conn, pkg["id"])
        if not rows:
            raise ValueError(f"Package '{pkg['name']}' has no items")
        name, price = pkg["name"], float(pkg["price"])
        comps = [{"product_id": c["product_id"], "qty": c["qty"], "sale_mode": c["sale_mode"],
                  "unit_price": _component_unit_price(c)} for c in rows]
    regular = round(sum(c["unit_price"] * c["qty"] for c in comps), 2)
    if price > regular + 0.009:
        raise ValueError(f"Package '{name}' costs more than its items at today's prices — update the package")
    savings_total = round((regular - price) * n, 2)
    lines, given = [], 0.0
    for idx, c in enumerate(comps):
        prod = conn.execute("SELECT * FROM products WHERE id=?", (c["product_id"],)).fetchone()
        if not prod:
            raise ValueError(f"A product in package '{name}' no longer exists")
        line_regular = c["unit_price"] * c["qty"] * n
        if idx == len(comps) - 1:
            save = round(savings_total - given, 2)
        else:
            save = round(savings_total * (line_regular / (regular * n)) if regular else 0, 2)
            given = round(given + save, 2)
        lines.append((prod, c["qty"] * n, float(c["unit_price"]), c["sale_mode"], save))
    new_lock = {"package_id": it.get("package_id") or (lock or {}).get("package_id"), "package_name": name,
                "package_price": price, "components": comps}
    return lines, new_lock


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
    "pending_sales", "payments", "receivables", "sale_items",
    "commercial_delivery_cylinders", "commercial_pullouts", "commercial_deliveries",
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
@admin_only
def reset_summary():
    conn = get_db()
    counts = _reset_counts(conn)
    conn.close()
    return jsonify(counts)


@app.get("/api/admin/backup")
@admin_only
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
@admin_only
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
            conn.execute("DELETE FROM customer_accounts")
            conn.execute("DELETE FROM customers")

        if wipe_discounts:
            conn.execute("DELETE FROM discounts")
            conn.execute("DELETE FROM promo_package_items")
            conn.execute("DELETE FROM promo_packages")

        if wipe_products:
            # All history pointing at products was cleared above.
            conn.execute("DELETE FROM inventory")
            conn.execute("DELETE FROM empty_cylinder_inventory")
            conn.execute("DELETE FROM discounts WHERE product_id IS NOT NULL")
            conn.execute("DELETE FROM promo_package_items")
            conn.execute("UPDATE promo_packages SET active=0")
            conn.execute("DELETE FROM products")

        # Restart auto-number counters for every emptied table.
        emptied = list(RESET_TRANSACTION_TABLES)
        if wipe_staff_fleet:
            emptied += ["employees", "vehicles"]
        if wipe_customers:
            emptied += ["customer_accounts", "customers"]
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

def _price_sale_items(conn, items, customer, sale_type, locked=None, locks_out=None):
    """Server-side pricing for a POS cart. `locked` (For Delivery) carries
    the per-line prices and discounts captured when the order was taken;
    pass a list as `locks_out` to get those for a new order (one per cart
    line). A cart line with a package_id is a package promo: it becomes one
    resolved line per product in the package."""
    if not items:
        raise ValueError("Cart is empty")
    # Commercial accounts: 50kg cylinders on a credit sale are only
    # delivered here (stock moves, DR prints); the customer is charged
    # later through 50kg Commercial Billing, so the SRP never hits their
    # receivables / SOA.
    commercial_delivery = bool(sale_type == "CREDIT" and customer and is_commercial_account(customer))
    subtotal = 0.0
    resolved_items = []
    for idx, it in enumerate(items):
        lock = locked[idx] if locked else None
        if it.get("package_id") or (lock and lock.get("package_id")):
            lines, pkg_lock = _expand_package(conn, it, lock)
            pkg = (pkg_lock["package_id"], pkg_lock["package_name"])
            for prod, qty, unit, mode, save in lines:
                line_total = round(unit * qty, 2)
                subtotal += line_total
                resolved_items.append((prod, qty, unit, line_total, mode, None, pkg[1], save,
                                       None, 0.0, False, None, None, None, None, pkg))
            if locks_out is not None:
                locks_out.append(pkg_lock)
            continue
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
        gas_price = float(lock["gas_price"]) if lock else float(prod["price"])
        bill_later = commercial_delivery and is_commercial_cylinder(prod)
        srp_price = gas_price if bill_later else None
        if bill_later:
            gas_price = 0.0  # charged later in 50kg Commercial Billing
        price = gas_price
        swap_brand, swap_fee, swap_type, empty_pid, swap_valve = None, 0.0, None, None, None
        if sale_mode == "DEPOSIT":
            price += float(lock["deposit_price"]) if lock else float(prod["deposit_price"])
        elif sale_mode == "SWAP":
            swap_brand, swap_fee, swap_type, empty_pid, swap_valve = resolve_swap(conn, prod, it)
            price += swap_fee
        line_total = round(price * qty, 2)
        subtotal += line_total
        # optional per-item discount, recomputed server-side
        disc_id, disc_name, disc_amount = None, None, 0.0
        if lock and not bill_later:
            # For Delivery: keep the discount agreed when the order was taken,
            # even if the promo has ended by the delivery day.
            if lock.get("discount_amount"):
                disc_id, disc_name = lock.get("discount_id"), lock.get("discount_name")
                disc_amount = round(float(lock["discount_amount"]), 2)
        elif it.get("discount_id") and not bill_later:
            disc = conn.execute("SELECT * FROM discounts WHERE id=?", (it["discount_id"],)).fetchone()
            if not disc:
                raise ValueError("Selected discount no longer exists")
            disc_amount = compute_item_discount(disc, prod["id"], gas_price, qty)
            disc_id, disc_name = disc["id"], disc["name"]
        resolved_items.append((prod, qty, price, line_total, sale_mode, disc_id, disc_name, disc_amount,
                               swap_brand, swap_fee, bill_later, srp_price, swap_type, empty_pid, swap_valve, None))
        if locks_out is not None:
            locks_out.append({"gas_price": float(prod["price"]), "deposit_price": float(prod["deposit_price"]),
                              "discount_id": disc_id, "discount_name": disc_name, "discount_amount": disc_amount})

    subtotal = round(subtotal, 2)
    discount = round(sum(r[7] for r in resolved_items), 2)
    total = round(subtotal - discount, 2)
    if total < 0:
        raise ValueError("Discount exceeds subtotal")
    return resolved_items, subtotal, discount, total


def _record_sale(conn, u, location_id, d, locked=None, notes=None):
    """Records a completed POS sale (stock out, payments, receivable) inside
    the caller's transaction and returns its id. Raises ValueError."""
    items = d.get("items") or []
    payments = d.get("payments") or []
    sale_type = d.get("sale_type", "CASH")
    if sale_type not in ("CASH", "CREDIT"):
        raise ValueError("sale_type must be CASH or CREDIT")
    customer_id = d.get("customer_id") or None
    # Walk-in buyers have no account; the cashier may type their name.
    walkin_name = None if customer_id else ((d.get("walkin_name") or "").strip()[:120] or None)
    apply_wht = bool(d.get("withholding_tax"))
    if notes is None:
        notes = d.get("notes", "")
    customer = None
    if customer_id:
        customer = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not customer:
            raise ValueError("Customer not found")
    resolved_items, subtotal, discount, total = _price_sale_items(conn, items, customer, sale_type, locked)
    # 1% expanded withholding tax: the customer pays the gross less
    # (gross / 1.12) x 1%, and hands over a BIR Form 2307 for it.
    withholding_tax = compute_withholding_tax(total) if apply_wht else 0.0
    amount_due = round(total - withholding_tax, 2)

    paid_total = round(sum(float(p["amount"]) for p in payments), 2)

    if sale_type == "CASH" and paid_total < amount_due:
        raise ValueError("Cash sale payments do not cover the total. Use CREDIT for partial payment.")

    if any(r[10] for r in resolved_items) and paid_total > amount_due + 0.009:
        raise ValueError(f"Payment is more than the amount due ({amount_due:.2f}). The 50kg cylinders for a "
                         "Commercial account are charged later in 50kg Commercial Billing, not here.")

    # credit sale: check customer terms + credit limit
    if sale_type == "CREDIT":
        if not customer:
            raise ValueError("Credit sale requires a customer with approved terms")
        outstanding = conn.execute(
            "SELECT COALESCE(SUM(balance),0) b FROM receivables WHERE customer_id=? AND status!='PAID'",
            (customer_id,),
        ).fetchone()["b"]
        projected = outstanding + (amount_due - paid_total)
        if customer["credit_limit"] > 0 and projected > customer["credit_limit"]:
            raise ValueError(
                f"Credit limit exceeded: outstanding+this sale = {projected:.2f}, limit = {customer['credit_limit']:.2f}"
            )

    balance = round(amount_due - paid_total, 2)
    sale_no = next_number(conn, "SALE", "sales", "sale_no")
    # Credit / terms sales get a Delivery Receipt number the customer signs against.
    dr_no = next_number(conn, "DR", "sales", "dr_no") if sale_type == "CREDIT" else None

    cur = conn.execute(
        """INSERT INTO sales (sale_no, location_id, user_id, customer_id, sale_type, subtotal, discount, total, paid, balance,
                              notes, dr_no, walkin_name, withholding_tax)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (sale_no, location_id, u["id"], customer_id, sale_type, subtotal, discount, total,
         paid_total, balance, notes, dr_no, walkin_name, withholding_tax),
    )
    sale_id = cur.lastrowid

    for (prod, qty, price, line_total, sale_mode, disc_id, disc_name, disc_amount,
         swap_brand, swap_fee, bill_later, srp_price, swap_type, empty_pid, swap_valve, pkg) in resolved_items:
        conn.execute(
            """INSERT INTO sale_items (sale_id, product_id, product_name, qty, price, subtotal, sale_mode,
               discount_id, discount_name, discount_amount, swap_brand, swap_fee, bill_later, srp_price,
               swap_type, swap_empty_product_id, swap_valve, package_id, package_name)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sale_id, prod["id"], prod["name"], qty, price, line_total, sale_mode,
             disc_id, disc_name, disc_amount, swap_brand, swap_fee, 1 if bill_later else 0, srp_price,
             swap_type, empty_pid, swap_valve, pkg[0] if pkg else None, pkg[1] if pkg else None),
        )
        swap_txt = swap_label({"swap_type": swap_type, "swap_brand": swap_brand, "swap_valve": swap_valve})
        mode_note = f"SWAP from {swap_txt}" if sale_mode == "SWAP" else sale_mode
        if pkg:
            mode_note += f", package {pkg[1]}"
        adjust_inventory(conn, location_id, prod["id"], -qty, "SALE", sale_no,
                          notes=f"Sold via {sale_no} ({mode_note})")
        if sale_mode == "REFILL":
            # Customer exchanges an empty cylinder for a full one — the empty
            # comes back into our stock, ready to be hauled to the plant for refill.
            add_empty_cylinders(conn, location_id, prod["id"], qty, "REFILL SALE",
                                reference=sale_no, notes=f"Empty returned on refill sale {sale_no}")
        elif sale_mode == "SWAP":
            # SWAP: the customer hands over an empty of a different brand
            # (kept apart from our own empties, counted per brand) or, on an
            # A/S-POL Solane swap, our own empty of the other valve.
            swap_empty_in(conn, location_id, prod["id"], swap_brand, swap_type, empty_pid, qty, "SWAP SALE",
                          sale_no, f"{swap_txt} empty taken in on swap sale {sale_no}")
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
            (sale_id, customer_id, invoice_no, str(invoice_date), str(due_date), amount_due, paid_total, balance,
             "CURRENT"),
        )

    audit(conn, "CREATE", "SALES", sale_id, f"{sale_no} total={total} wht={withholding_tax} paid={paid_total} balance={balance}")
    return sale_id


@app.post("/api/sales")
@roles_or_module_required("ADMIN", "ATTENDANT", module="pos")
def create_sale():
    d = request.get_json(force=True)
    u = session["user"]
    location_id = u["location_id"] if not is_admin_like(u["role"]) else d.get("location_id")
    if not location_id:
        return jsonify(error="No location specified"), 400
    if not (d.get("items") or []):
        return jsonify(error="Cart is empty"), 400

    conn = get_db()
    try:
        sale_id = _record_sale(conn, u, location_id, d)
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


# ---------------------------------------------------------------- For Delivery
# An order rung up at the POS that goes out later (tomorrow, next week). It
# is kept here with its prices locked; no stock, payment or receivable moves.
# "Mark Delivered" records the real sale on the delivery day, with whatever
# payment is collected then (or on terms), so the End of Day report and the
# sales reports count it on the day it was actually delivered.

PENDING_SALE_FIELDS = ("customer_id", "walkin_name", "sale_type", "withholding_tax", "notes", "items")


def _product_name(conn, product_id):
    row = conn.execute("SELECT name FROM products WHERE id=?", (product_id,)).fetchone()
    return row["name"] if row else f"#{product_id}"


def _pending_location_ok(u, row):
    return is_admin_like(u["role"]) or row["location_id"] == u["location_id"]


def _pending_sale_out(conn, r):
    payload = json.loads(r["payload"] or "{}")
    raw_items = payload.get("items") or []
    locked = payload.get("locked") or []
    names = {}
    if raw_items:
        ids = [it["product_id"] for it in raw_items if it.get("product_id")] or [0]
        names = {p["id"]: p["name"] for p in conn.execute(
            f"SELECT id, name FROM products WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()}
    items = []
    for idx, it in enumerate(raw_items):
        lock = locked[idx] if idx < len(locked) else {}
        if lock.get("package_id"):
            items.append({"package_id": lock["package_id"], "qty": it.get("qty"), "sale_mode": "PACKAGE",
                          "product_name": f"PACKAGE: {lock['package_name']} (" + ", ".join(
                              f"{c['qty']}x {_product_name(conn, c['product_id'])}" for c in lock["components"]) + ")",
                          "gas_price": lock["package_price"],
                          "discount_name": None, "discount_amount": 0})
            continue
        items.append({**{k: it.get(k) for k in ("product_id", "qty", "sale_mode", "swap_brand", "swap_fee",
                                                 "swap_type")},
                      "product_name": names.get(it["product_id"], f"#{it['product_id']}"),
                      "gas_price": lock.get("gas_price"), "discount_name": lock.get("discount_name"),
                      "discount_amount": lock.get("discount_amount") or 0})
    out = dict(r)
    out.pop("payload", None)
    out["items"] = items
    out["ts"] = _local_ts(r["ts"])
    out["delivered_at"] = _local_ts(r["delivered_at"])
    out["cancelled_at"] = _local_ts(r["cancelled_at"])
    return out


@app.post("/api/pending-sales")
@roles_or_module_required("ADMIN", "ATTENDANT", module="pos")
def create_pending_sale():
    d = request.get_json(force=True)
    u = session["user"]
    location_id = u["location_id"] if not is_admin_like(u["role"]) else d.get("location_id")
    if not location_id:
        return jsonify(error="No location specified"), 400
    sale_type = d.get("sale_type", "CASH")
    if sale_type not in ("CASH", "CREDIT"):
        return jsonify(error="sale_type must be CASH or CREDIT"), 400
    customer_id = d.get("customer_id") or None
    conn = get_db()
    try:
        customer = None
        if customer_id:
            customer = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
            if not customer:
                raise ValueError("Customer not found")
        if sale_type == "CREDIT" and not customer:
            raise ValueError("Credit sale requires a customer with approved terms")
        locks = []
        resolved, subtotal, discount, total = _price_sale_items(conn, d.get("items") or [], customer, sale_type,
                                                                locks_out=locks)
        wht = compute_withholding_tax(total) if d.get("withholding_tax") else 0.0
        payload = {k: d.get(k) for k in PENDING_SALE_FIELDS}
        payload["customer_id"] = customer_id
        payload["walkin_name"] = None if customer_id else ((d.get("walkin_name") or "").strip()[:120] or None)
        payload["items"] = [{"package_id": int(it["package_id"]), "qty": int(it["qty"])} if it.get("package_id") else
                            {**{k: it[k] for k in ("discount_id", "swap_brand", "swap_fee", "swap_type")
                                if it.get(k) is not None},
                             "product_id": it["product_id"], "qty": int(it["qty"]),
                             "sale_mode": it.get("sale_mode") or "REFILL"} for it in d.get("items")]
        payload["locked"] = locks
        pending_no = next_number(conn, "FD", "pending_sales", "pending_no")
        cur = conn.execute(
            """INSERT INTO pending_sales (pending_no, location_id, created_by, customer_id, walkin_name, sale_type,
                   withholding_tax, payload, total, amount_due, delivery_address, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pending_no, location_id, u["id"], customer_id, payload["walkin_name"], sale_type,
             1 if d.get("withholding_tax") else 0, json.dumps(payload), total, round(total - wht, 2),
             (d.get("delivery_address") or "").strip()[:300] or None, (d.get("notes") or "").strip() or None),
        )
        pid = cur.lastrowid
        audit(conn, "CREATE", "PENDING_SALES", pid, f"{pending_no} for delivery, total={total}")
        conn.commit()
        row = conn.execute(
            """SELECT ps.*, COALESCE(c.name, ps.walkin_name) customer_name, c.account_no, l.name location_name,
                      cu.name created_by_name
               FROM pending_sales ps JOIN locations l ON l.id=ps.location_id JOIN users cu ON cu.id=ps.created_by
               LEFT JOIN customers c ON c.id=ps.customer_id WHERE ps.id=?""", (pid,)).fetchone()
        out = _pending_sale_out(conn, row)
    except ValueError as e:
        conn.rollback()
        conn.close()
        return jsonify(error=str(e)), 400
    conn.close()
    return jsonify(out), 201


@app.get("/api/pending-sales")
@roles_or_module_required("ADMIN", "MANAGER", "ATTENDANT", "ACCOUNTING", module="pos")
def list_pending_sales():
    u = session["user"]
    where, params = [], []
    status = (request.args.get("status") or "PENDING").upper()
    if status in ("PENDING", "DELIVERED", "CANCELLED"):
        where.append("ps.status=?"); params.append(status)
    if not is_admin_like(u["role"]) and u["role"] not in ("MANAGER", "ACCOUNTING"):
        where.append("ps.location_id=?"); params.append(u["location_id"])
    conn = get_db()
    rows = conn.execute(
        f"""SELECT ps.*, COALESCE(c.name, ps.walkin_name) customer_name, c.account_no, l.name location_name,
                   cu.name created_by_name, du.name delivered_by_name, xu.name cancelled_by_name, s.sale_no
            FROM pending_sales ps JOIN locations l ON l.id=ps.location_id JOIN users cu ON cu.id=ps.created_by
            LEFT JOIN customers c ON c.id=ps.customer_id LEFT JOIN users du ON du.id=ps.delivered_by
            LEFT JOIN users xu ON xu.id=ps.cancelled_by LEFT JOIN sales s ON s.id=ps.sale_id
            {"WHERE " + " AND ".join(where) if where else ""}
            ORDER BY ps.ts {"ASC" if status == "PENDING" else "DESC"} LIMIT 500""", params).fetchall()
    out = [_pending_sale_out(conn, r) for r in rows]
    conn.close()
    return jsonify(out)


@app.post("/api/pending-sales/<int:pid>/deliver")
@roles_or_module_required("ADMIN", "ATTENDANT", module="pos")
def deliver_pending_sale(pid):
    """Delivered: records the sale today with the payment collected now."""
    d = request.get_json(force=True) or {}
    u = session["user"]
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT ps.*, cu.name created_by_name FROM pending_sales ps "
                           "JOIN users cu ON cu.id=ps.created_by WHERE ps.id=?", (pid,)).fetchone()
        if not row:
            raise LookupError("Not found")
        if not _pending_location_ok(u, row):
            raise PermissionError("This order belongs to another location")
        if row["status"] != "PENDING":
            raise ValueError(f"{row['pending_no']} is already {row['status'].lower()}")
        payload = json.loads(row["payload"])
        sale_type = d.get("sale_type") or row["sale_type"]
        sale_d = {
            "items": payload["items"], "customer_id": payload.get("customer_id"),
            "walkin_name": payload.get("walkin_name"), "sale_type": sale_type,
            "withholding_tax": d.get("withholding_tax", bool(row["withholding_tax"])),
            "payments": [p for p in (d.get("payments") or []) if float(p.get("amount") or 0) > 0],
        }
        note = f"For Delivery {row['pending_no']} (taken {_local_ts(row['ts'])[:10]} by {row['created_by_name']})"
        if row["notes"]:
            note += f" - {row['notes']}"
        sale_id = _record_sale(conn, u, row["location_id"], sale_d, locked=payload.get("locked"), notes=note)
        conn.execute("UPDATE pending_sales SET status='DELIVERED', sale_id=?, delivered_at=datetime('now'), "
                     "delivered_by=? WHERE id=?", (sale_id, u["id"], pid))
        audit(conn, "DELIVER", "PENDING_SALES", pid, f"{row['pending_no']} delivered as sale #{sale_id}")
        conn.commit()
    except LookupError as e:
        conn.rollback(); conn.close()
        return jsonify(error=str(e)), 404
    except PermissionError as e:
        conn.rollback(); conn.close()
        return jsonify(error=str(e)), 403
    except ValueError as e:
        conn.rollback(); conn.close()
        return jsonify(error=str(e)), 400
    except Exception as e:
        conn.rollback(); conn.close()
        return jsonify(error=f"Unable to complete delivery: {e}"), 500
    conn.close()
    return jsonify(get_sale_detail(sale_id)), 201


@app.post("/api/pending-sales/<int:pid>/cancel")
@roles_or_module_required("ADMIN", "ATTENDANT", module="pos")
def cancel_pending_sale(pid):
    d = request.get_json(force=True) or {}
    u = session["user"]
    reason = (d.get("reason") or "").strip()
    if not reason:
        return jsonify(error="Please give a reason for cancelling"), 400
    conn = get_db()
    row = conn.execute("SELECT * FROM pending_sales WHERE id=?", (pid,)).fetchone()
    if not row:
        conn.close(); return jsonify(error="Not found"), 404
    if not _pending_location_ok(u, row):
        conn.close(); return jsonify(error="This order belongs to another location"), 403
    if row["status"] != "PENDING":
        conn.close(); return jsonify(error=f"{row['pending_no']} is already {row['status'].lower()}"), 400
    conn.execute("UPDATE pending_sales SET status='CANCELLED', cancelled_at=datetime('now'), cancelled_by=?, "
                 "cancel_reason=? WHERE id=?", (u["id"], reason[:300], pid))
    audit(conn, "CANCEL", "PENDING_SALES", pid, f"{row['pending_no']}: {reason[:200]}")
    conn.commit()
    conn.close()
    return jsonify(ok=True)


def get_sale_detail(sale_id):
    conn = get_db()
    sale = conn.execute(
        """SELECT s.*, l.name location_name, l.address location_address, u.name cashier_name,
                  COALESCE(c.name, s.walkin_name) customer_name, c.account_no, c.business_name customer_business_name,
                  c.address customer_address, c.phone customer_phone, c.terms_days customer_terms_days, c.category customer_category,
                  r.invoice_no, r.due_date, eu.name edited_by_name
           FROM sales s JOIN locations l ON l.id=s.location_id JOIN users u ON u.id=s.user_id
           LEFT JOIN customers c ON c.id=s.customer_id LEFT JOIN users eu ON eu.id=s.edited_by
           LEFT JOIN receivables r ON r.sale_id=s.id WHERE s.id=?""", (sale_id,)
    ).fetchone()
    items = conn.execute("SELECT * FROM sale_items WHERE sale_id=?", (sale_id,)).fetchall()
    pays = conn.execute("SELECT * FROM payments WHERE sale_id=?", (sale_id,)).fetchall()
    conn.close()
    items = [{**dict(i), "swap_label": swap_label(i) if i["sale_mode"] == "SWAP" else None} for i in items]
    return {"sale": dict(sale), "items": items, "payments": [dict(p) for p in pays]}


SALES_PAGE_SIZE_MAX = 500


@app.get("/api/sales")
@roles_or_module_required("ADMIN", "MANAGER", "ATTENDANT", module="sales")
def list_sales():
    """Every sale from the very first one, a page at a time. ?q= searches the
    sale #, DR #, customer name / business / account # and walk-in name;
    ?from= / ?to= limit the date; ?order=oldest lists from the first sale."""
    u = session["user"]
    conn = get_db()
    where = ["s.is_opening_balance=0", "s.is_billing=0"]
    params = []
    # Supervisor sees (and can void) sales at every location.
    if u["role"] not in ("ADMIN", "OPERATION_HEAD", "ACCOUNTING", "SUPERVISOR"):
        where.append("s.location_id=?")
        params.append(u["location_id"])
    q = (request.args.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        where.append("(s.sale_no LIKE ? OR COALESCE(s.dr_no,'') LIKE ? OR COALESCE(c.name,'') LIKE ? "
                     "OR COALESCE(c.business_name,'') LIKE ? OR COALESCE(c.account_no,'') LIKE ? "
                     "OR COALESCE(s.walkin_name,'') LIKE ?)")
        params += [like] * 6
    for arg, op in (("from", ">="), ("to", "<=")):
        v = (request.args.get(arg) or "").strip()
        if v:
            where.append(f"date(s.ts) {op} date(?)")
            params.append(v)
    status = (request.args.get("status") or "").strip().upper()
    if status in ("COMPLETED", "VOIDED"):
        where.append("s.status=?"); params.append(status)
    page_size = max(1, min(request.args.get("page_size", type=int) or 100, SALES_PAGE_SIZE_MAX))
    page = max(1, request.args.get("page", type=int) or 1)
    order = "ASC" if (request.args.get("order") or "").lower() == "oldest" else "DESC"
    joins = """FROM sales s JOIN locations l ON l.id=s.location_id JOIN users u2 ON u2.id=s.user_id
              LEFT JOIN customers c ON c.id=s.customer_id LEFT JOIN users eu ON eu.id=s.edited_by"""
    cond = "WHERE " + " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN s.status='COMPLETED' THEN s.total END),0) amt "
                         f"{joins} {cond}", params).fetchone()
    sql = f"""SELECT s.*, l.name location_name, u2.name cashier_name, COALESCE(c.name, s.walkin_name) customer_name,
                     c.account_no customer_account_no, s.customer_id IS NULL is_walkin,
                     (SELECT COALESCE(SUM(x.qty),0) FROM sale_items x WHERE x.sale_id=s.id AND x.bill_later=1) bill_later_qty,
                     (SELECT cd.id FROM commercial_deliveries cd WHERE cd.source_sale_id=s.id AND cd.status!='CANCELLED'
                       ORDER BY cd.id DESC LIMIT 1) billing_id,
                     (SELECT cd.delivery_no || '|' || COALESCE(cd.billed_amount,0) FROM commercial_deliveries cd
                       WHERE cd.source_sale_id=s.id AND cd.status!='CANCELLED' ORDER BY cd.id DESC LIMIT 1) billing_ref,
                     eu.name edited_by_name
              {joins} {cond}
              ORDER BY s.ts {order}, s.id {order} LIMIT ? OFFSET ?"""
    rows = conn.execute(sql, params + [page_size, (page - 1) * page_size]).fetchall()
    conn.close()
    return jsonify(rows=[dict(r) for r in rows], total=total["n"], total_amount=round(total["amt"], 2),
                   page=page, page_size=page_size, pages=max(1, -(-total["n"] // page_size)))


@app.get("/api/sales/<int:sale_id>")
@login_required
def get_sale(sale_id):
    return jsonify(get_sale_detail(sale_id))


@app.post("/api/sales/<int:sale_id>/void")
@roles_required("ADMIN", "SUPERVISOR")
def void_sale(sale_id):
    conn = get_db()
    sale = conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    if not sale:
        conn.close()
        return jsonify(error="Not found"), 404
    if sale["status"] == "VOIDED":
        conn.close()
        return jsonify(error="Already voided"), 400
    if sale["is_billing"]:
        # A billing's line is in kg, not cylinders: reversing it as stock
        # would put fractional counts (e.g. 44.9) into inventory and empties.
        conn.close()
        return jsonify(error="This is a billing, not a sale — void it from "
                             "50kg Commercial Billing > Void Billing"), 400
    billed = conn.execute("SELECT delivery_no FROM commercial_deliveries WHERE source_sale_id=? AND status!='CANCELLED'",
                          (sale_id,)).fetchone()
    if billed:
        conn.close()
        return jsonify(error=f"This delivery is billed in {billed['delivery_no']}. Void that billing first "
                             "(50kg Commercial Billing > Void Billing)."), 400
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
        elif it["sale_mode"] == "SWAP":
            swap_empty_reverse(conn, sale["location_id"], it, "VOID REVERSAL", void_ref,
                               "Swapped empty reversed on voided sale")
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


# ---------------------------------------------------------------- adjust a completed sale
# The Operation Head (and Admin) can correct a completed sale: change items,
# quantities, unit prices, discounts, the customer (e.g. a sale rung up as
# walk-in by mistake) and notes. Stock, empties, swapped empties and cylinder
# deposits are reversed for the old lines and applied for the new ones; the
# sale totals and its receivable follow. Payments are not touched, so a
# change can't leave the customer having paid more than the new amount due.

SALE_EDIT_ROLES = ("ADMIN", "OPERATION_HEAD")


def _reverse_sale_line_stock(conn, sale, it, ref, customer_id):
    adjust_inventory(conn, sale["location_id"], it["product_id"], it["qty"], "ADJUSTMENT", ref,
                     notes=f"Sale edit: old line reversed ({sale['sale_no']})", allow_negative=True)
    if it["sale_mode"] == "REFILL":
        remove_empty_cylinders(conn, sale["location_id"], it["product_id"], it["qty"], "SALE EDIT", ref,
                               notes=f"Sale edit {sale['sale_no']}: old refill line reversed", allow_negative=True)
    elif it["sale_mode"] == "SWAP":
        swap_empty_reverse(conn, sale["location_id"], it, "SALE EDIT", ref,
                           f"Sale edit {sale['sale_no']}: old swap line reversed")
    elif it["sale_mode"] == "DEPOSIT" and customer_id:
        conn.execute("UPDATE cylinder_deposits SET qty_on_loan = MAX(qty_on_loan - ?, 0) WHERE customer_id=? AND product_id=?",
                     (it["qty"], customer_id, it["product_id"]))


def _apply_sale_line_stock(conn, sale, prod, qty, sale_mode, swap_brand, ref, customer_id,
                           swap_type=None, empty_pid=None):
    have = conn.execute("SELECT quantity FROM inventory WHERE location_id=? AND product_id=?",
                        (sale["location_id"], prod["id"])).fetchone()
    if (have["quantity"] if have else 0) < qty:
        raise ValueError(f"Not enough {prod['name']} in stock at this location for {qty} "
                         f"(on hand {have['quantity'] if have else 0})")
    adjust_inventory(conn, sale["location_id"], prod["id"], -qty, "SALE", ref,
                     notes=f"Sale edit {sale['sale_no']}: new line ({sale_mode})")
    if sale_mode == "REFILL":
        add_empty_cylinders(conn, sale["location_id"], prod["id"], qty, "SALE EDIT", reference=ref,
                            notes=f"Empty returned on edited sale {sale['sale_no']}")
    elif sale_mode == "SWAP":
        swap_empty_in(conn, sale["location_id"], prod["id"], swap_brand, swap_type, empty_pid, qty, "SALE EDIT", ref,
                      f"{swap_brand} empty on edited sale {sale['sale_no']}")
    elif customer_id:
        record_cylinder_deposit(conn, customer_id, prod["id"], qty)


@app.put("/api/sales/<int:sale_id>")
@roles_required(*SALE_EDIT_ROLES)
def edit_sale(sale_id):
    d = request.get_json(force=True) or {}
    u = session["user"]
    conn = get_db()
    try:
        sale = conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        if not sale:
            conn.close(); return jsonify(error="Not found"), 404
        if sale["status"] != "COMPLETED":
            raise ValueError(f"This sale is {sale['status']} — only completed sales can be adjusted")
        if sale["is_billing"] or sale["is_opening_balance"]:
            raise ValueError("Billings and previous arrears are not sales — adjust them in their own screens")
        billed = conn.execute("SELECT delivery_no FROM commercial_deliveries WHERE source_sale_id=? AND status!='CANCELLED'",
                              (sale_id,)).fetchone()
        if billed:
            raise ValueError(f"This delivery is billed in {billed['delivery_no']}. Void that billing first "
                             "(50kg Commercial Billing > Void Billing), then adjust the sale.")
        reason = (d.get("reason") or "").strip()
        if not reason:
            raise ValueError("Type the reason for the adjustment")

        # customer: a walk-in sale can be linked to its account, or vice versa (cash only)
        customer_id = d["customer_id"] if "customer_id" in d else sale["customer_id"]
        customer_id = int(customer_id) if customer_id not in (None, "", 0, "0") else None
        customer = None
        if customer_id:
            customer = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
            if not customer:
                raise ValueError("Customer not found")
        if sale["sale_type"] == "CREDIT" and not customer:
            raise ValueError("A credit / terms sale must stay on a customer account")
        walkin_name = None if customer_id else (
            ((d.get("walkin_name") if "walkin_name" in d else sale["walkin_name"]) or "").strip()[:120] or None)

        items = d.get("items") or []
        if not items:
            raise ValueError("A sale needs at least one item (void it instead)")
        commercial_delivery = bool(sale["sale_type"] == "CREDIT" and customer and is_commercial_account(customer))
        resolved = []
        for n, it in enumerate(items, start=1):
            prod = conn.execute("SELECT * FROM products WHERE id=?", (it.get("product_id"),)).fetchone()
            if not prod:
                raise ValueError(f"Line {n}: choose a product")
            try:
                qty = int(it.get("qty"))
            except (TypeError, ValueError):
                raise ValueError(f"Line {n}: quantity must be a whole number")
            if qty <= 0:
                raise ValueError(f"Line {n}: quantity must be at least 1")
            sale_mode = it.get("sale_mode") or "REFILL"
            if sale_mode not in ("REFILL", "DEPOSIT", "SWAP"):
                raise ValueError(f"Line {n}: mode must be REFILL, DEPOSIT or SWAP")
            swap_brand, swap_fee, swap_type, empty_pid, swap_valve = None, 0.0, None, None, None
            if sale_mode == "SWAP":
                swap_brand, swap_fee, swap_type, empty_pid, swap_valve = resolve_swap(conn, prod, it, f"Line {n}: ")
            bill_later = commercial_delivery and is_commercial_cylinder(prod)
            price = _num(it.get("price"), f"Line {n} unit price", allow_blank=True)
            if price is None:  # default the way the POS prices it
                price = 0.0 if bill_later else float(prod["price"])
                price += float(prod["deposit_price"] or 0) if sale_mode == "DEPOSIT" else swap_fee
            price = round(price, 2)
            if price < 0:
                raise ValueError(f"Line {n}: unit price cannot be negative")
            line_total = round(price * qty, 2)
            disc = round(_num(it.get("discount_amount"), f"Line {n} discount", allow_blank=True) or 0, 2)
            if disc < 0 or disc > line_total + 0.009:
                raise ValueError(f"Line {n}: discount must be between 0 and the line total ({line_total:.2f})")
            resolved.append({"prod": prod, "qty": qty, "price": price, "line_total": line_total, "sale_mode": sale_mode,
                             "swap_brand": swap_brand, "swap_fee": swap_fee, "bill_later": bill_later,
                             "swap_type": swap_type, "swap_empty_product_id": empty_pid, "swap_valve": swap_valve,
                             "package_id": it.get("package_id") or None,
                             "package_name": (it.get("package_name") or None) if it.get("package_id") else None,
                             "srp_price": float(prod["price"]) if bill_later else None, "discount_amount": disc,
                             "discount_id": it.get("discount_id") if disc > 0 else None,
                             "discount_name": (it.get("discount_name") or ("Adjustment" if disc > 0 else None)) if disc > 0 else None})

        subtotal = round(sum(r["line_total"] for r in resolved), 2)
        discount = round(sum(r["discount_amount"] for r in resolved), 2)
        total = round(subtotal - discount, 2)
        apply_wht = bool(d["withholding_tax"]) if "withholding_tax" in d else (sale["withholding_tax"] or 0) > 0
        wht = compute_withholding_tax(total) if apply_wht else 0.0
        amount_due = round(total - wht, 2)
        # Payments taken at the POS (not later AR collections) can be corrected
        # too: method, amount and reference. AR collections stay as they are.
        old_pays = conn.execute("SELECT * FROM payments WHERE sale_id=? AND payment_source='SALE' ORDER BY id",
                                (sale_id,)).fetchall()
        pay_changes = None
        if "payments" in d:
            old_by_id = {p["id"]: p for p in old_pays}
            pay_changes = []
            for n, p in enumerate(d.get("payments") or [], start=1):
                method = (p.get("method") or "").strip()[:40]
                if not method:
                    raise ValueError(f"Payment {n}: choose the payment method")
                amt = round(_num(p.get("amount"), f"Payment {n} amount", allow_blank=True) or 0, 2)
                if amt < 0:
                    raise ValueError(f"Payment {n}: amount cannot be negative")
                pid = p.get("id")
                pid = int(pid) if pid not in (None, "", 0, "0") else None
                if pid is not None and pid not in old_by_id:
                    raise ValueError(f"Payment {n} is not a POS payment of this sale")
                if amt <= 0:
                    continue  # a zeroed row is dropped
                pay_changes.append({"id": pid, "method": method, "amount": amt,
                                    "reference": (p.get("reference") or "").strip()[:120]})
        old_pay_sum = round(sum(p["amount"] for p in old_pays), 2)
        new_pay_sum = round(sum(p["amount"] for p in pay_changes), 2) if pay_changes is not None else old_pay_sum
        pay_delta = round(new_pay_sum - old_pay_sum, 2)
        paid = round((sale["paid"] or 0) + pay_delta, 2)
        balance = round(amount_due - paid, 2)
        recv = conn.execute("SELECT * FROM receivables WHERE sale_id=?", (sale_id,)).fetchone()
        if sale["sale_type"] == "CASH" and balance > 0.009:
            raise ValueError(f"The new amount due ({amount_due:,.2f}) is more than what was paid ({paid:,.2f}) on this "
                             "cash sale. Raise the payment, lower the amount, or ring up the difference as a new sale.")
        if sale["sale_type"] == "CREDIT" and balance < -0.009:
            raise ValueError(f"The customer paid {paid:,.2f}, more than the new amount due {amount_due:,.2f}. "
                             "Lower the payment or the amount paid.")

        old_items = conn.execute("SELECT * FROM sale_items WHERE sale_id=? ORDER BY id", (sale_id,)).fetchall()
        sig = lambda rows: sorted((r[0], r[1], r[2], r[3] or "", r[4] or "", r[5] or 0) for r in rows)
        old_sig = sig([(i["product_id"], i["qty"], i["sale_mode"], i["swap_brand"], i["swap_type"],
                        i["swap_empty_product_id"]) for i in old_items])
        new_sig = sig([(r["prod"]["id"], r["qty"], r["sale_mode"], r["swap_brand"], r["swap_type"],
                        r["swap_empty_product_id"]) for r in resolved])
        has_deposit = any(i["sale_mode"] == "DEPOSIT" for i in old_items) or any(r["sale_mode"] == "DEPOSIT" for r in resolved)
        stock_changed = old_sig != new_sig or (has_deposit and customer_id != sale["customer_id"])
        ref = f"EDIT-{sale['sale_no']}"
        if stock_changed:
            for it in old_items:
                _reverse_sale_line_stock(conn, sale, it, ref, sale["customer_id"])
            for r in resolved:
                _apply_sale_line_stock(conn, sale, r["prod"], r["qty"], r["sale_mode"], r["swap_brand"], ref, customer_id,
                                       r["swap_type"], r["swap_empty_product_id"])

        conn.execute("DELETE FROM sale_items WHERE sale_id=?", (sale_id,))
        for r in resolved:
            conn.execute(
                """INSERT INTO sale_items (sale_id, product_id, product_name, qty, price, subtotal, sale_mode,
                   discount_id, discount_name, discount_amount, swap_brand, swap_fee, bill_later, srp_price,
                   swap_type, swap_empty_product_id, swap_valve, package_id, package_name)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sale_id, r["prod"]["id"], r["prod"]["name"], r["qty"], r["price"], r["line_total"], r["sale_mode"],
                 r["discount_id"], r["discount_name"], r["discount_amount"], r["swap_brand"], r["swap_fee"],
                 1 if r["bill_later"] else 0, r["srp_price"], r["swap_type"], r["swap_empty_product_id"],
                 r["swap_valve"], r["package_id"], r["package_name"]))
        pay_note = ""
        if pay_changes is not None:
            fmt_pays = lambda rows: ", ".join(f"{p['method']} {p['amount']:.2f}" for p in rows) or "none"
            before_p, after_p = fmt_pays(old_pays), fmt_pays(pay_changes)
            keep = {p["id"] for p in pay_changes if p["id"]}
            for p in old_pays:
                if p["id"] not in keep:
                    conn.execute("DELETE FROM payments WHERE id=?", (p["id"],))
            for p in pay_changes:
                if p["id"]:
                    conn.execute("UPDATE payments SET method=?, amount=?, reference=? WHERE id=?",
                                 (p["method"], p["amount"], p["reference"], p["id"]))
                else:  # dated with the sale, so it stays in that day's collections
                    conn.execute("INSERT INTO payments (sale_id, method, amount, reference, ts, user_id) "
                                 "VALUES (?,?,?,?,?,?)",
                                 (sale_id, p["method"], p["amount"], p["reference"], sale["ts"], u["id"]))
            if before_p != after_p or any(
                    p["id"] and (old_by_id[p["id"]]["reference"] or "") != p["reference"] for p in pay_changes):
                pay_note = f" | payments {before_p} -> {after_p}"

        notes = d["notes"] if "notes" in d else sale["notes"]
        conn.execute(
            """UPDATE sales SET customer_id=?, walkin_name=?, subtotal=?, discount=?, total=?, withholding_tax=?,
                   paid=?, balance=?, notes=?, edited_at=datetime('now'), edited_by=? WHERE id=?""",
            (customer_id, walkin_name, subtotal, discount, total, wht, paid, balance, notes, u["id"], sale_id))

        if recv:
            recv_paid = round((recv["paid"] or 0) + pay_delta, 2)
            conn.execute("UPDATE receivables SET customer_id=?, amount=?, paid=?, balance=? WHERE id=?",
                         (customer_id or recv["customer_id"], amount_due, recv_paid, round(amount_due - recv_paid, 2),
                          recv["id"]))
            recompute_receivable_status(conn, recv["id"])
        elif sale["sale_type"] == "CREDIT" and balance > 0.009:
            invoice_date = datetime.now().date()
            conn.execute(
                """INSERT INTO receivables (sale_id, customer_id, invoice_no, invoice_date, due_date, amount, paid, balance, status)
                   VALUES (?,?,?,?,?,?,?,?,'CURRENT')""",
                (sale_id, customer_id, next_number(conn, "INV", "receivables", "invoice_no"), str(invoice_date),
                 str(invoice_date + timedelta(days=customer["terms_days"] or 0)), amount_due, paid, balance))

        before = "; ".join(f"{i['qty']}x {i['product_name']} @{i['price']:.2f}" for i in old_items)
        after = "; ".join(f"{r['qty']}x {r['prod']['name']} @{r['price']:.2f}" for r in resolved)
        cust_note = ""
        if customer_id != sale["customer_id"]:
            old_c = conn.execute("SELECT name FROM customers WHERE id=?", (sale["customer_id"],)).fetchone() \
                if sale["customer_id"] else None
            cust_note = (f" | customer {old_c['name'] if old_c else (sale['walkin_name'] or 'Walk-in')} -> "
                         f"{customer['name'] if customer else (walkin_name or 'Walk-in')}")
        audit(conn, "ADJUST", "SALES", sale_id,
              f"{sale['sale_no']} adjusted: total {sale['total']:.2f} -> {total:.2f}, balance {sale['balance']:.2f} -> "
              f"{balance:.2f}{cust_note}{pay_note} | before: {before} | after: {after} | reason: {reason}")
        conn.commit()
    except ValueError as e:
        conn.rollback(); conn.close()
        return jsonify(error=str(e)), 400
    conn.close()
    return jsonify(get_sale_detail(sale_id))


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

ARREARS_ROLES = ("ADMIN", "OPERATION_HEAD", "ACCOUNTING")


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
        f"SELECT COALESCE(SUM(total),0) t, COUNT(*) c FROM sales WHERE status='COMPLETED' AND is_opening_balance=0 AND is_billing=0 "
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
    if u["role"] not in ("ADMIN", "OPERATION_HEAD", "MANAGER"):
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
    sql = """SELECT s.sale_no, s.ts, l.name location_name, u.name cashier, COALESCE(c.name, s.walkin_name) customer,
                    CASE WHEN c.id IS NULL THEN 'Walk-in' ELSE COALESCE(NULLIF(c.category,''), 'Uncategorized') END customer_category,
                    s.sale_type, s.status, s.total sale_total, s.discount sale_discount,
                    si.product_name || CASE WHEN si.sale_mode='DEPOSIT' THEN ' (deposit)'
                        WHEN si.sale_mode='SWAP' THEN ' (swap: ' || COALESCE(si.swap_brand,'')
                             || CASE WHEN si.swap_valve IS NOT NULL THEN ' ' || si.swap_valve ELSE '' END || ')'
                        ELSE '' END item,
                    si.qty, si.price,
                    COALESCE(si.subtotal, s.subtotal) amount,
                    COALESCE(si.discount_amount, CASE WHEN si.id IS NULL THEN s.discount ELSE 0 END) discount,
                    si.discount_name
             FROM sales s JOIN locations l ON l.id=s.location_id JOIN users u ON u.id=s.user_id
             LEFT JOIN customers c ON c.id=s.customer_id
             LEFT JOIN sale_items si ON si.sale_id = s.id
             WHERE s.is_opening_balance=0 AND s.is_billing=0"""
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
        f"""SELECT s.ts, s.sale_no, l.name location_name, u.name cashier, COALESCE(c.name, s.walkin_name) customer_name,
                   COALESCE(NULLIF(si.swap_brand,''), 'Unspecified') swap_brand, si.product_name,
                   si.qty, COALESCE(si.swap_fee,0) swap_fee, ROUND(COALESCE(si.swap_fee,0) * si.qty, 2) swap_fee_total,
                   si.subtotal amount, si.swap_type, si.swap_valve
            FROM sale_items si JOIN sales s ON s.id = si.sale_id
            JOIN locations l ON l.id = s.location_id JOIN users u ON u.id = s.user_id
            LEFT JOIN customers c ON c.id = s.customer_id
            WHERE {' AND '.join(where)} ORDER BY s.ts DESC, si.id""",
        params,
    ).fetchall()
    rows = [dict(r) for r in rows]
    for r in rows:  # A/S-POL swaps show which way the valve changed
        r["swap_kind"] = ("A/S-POL (Solane)" if r["swap_type"] == "VALVE_SOLANE"
                          else "A/S-POL (other brand)" if r["swap_type"] == "VALVE_OTHER" else "Other brand")
        if r["swap_valve"]:
            r["product_name"] = f"{r['product_name']} ({r['swap_valve']})"

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
# match so LPG accessories (regulators, hoses) are not counted. A product
# whose name or SKU carries a cylinder size ("11kg", "50 kg", "1.4KG") also
# counts, so cylinders filed under another category (e.g. "LPG", "Refill")
# with no deposit fee are not silently left out of the customer reports.
LPG_CYLINDER_SQL = ("(LOWER(COALESCE(p.category,'')) LIKE '%cylinder%' "
                    "OR COALESCE(p.deposit_price,0) > 0 "
                    "OR LOWER(COALESCE(p.name,'') || ' ' || COALESCE(p.sku,'')) GLOB '*[0-9]kg*' "
                    "OR LOWER(COALESCE(p.name,'') || ' ' || COALESCE(p.sku,'')) GLOB '*[0-9] kg*' "
                    "OR LOWER(COALESCE(p.name,'') || ' ' || COALESCE(p.sku,'')) GLOB '*[0-9]-kg*')")
GROWTH_MAX_PERIODS = 60
# A POS line counts unless it is a Commercial 50kg delivery (bill_later)
# that already has its 50kg Commercial Billing — then the billing counts.
UNBILLED_OR_CHARGED_SQL = ("(COALESCE(si.bill_later,0)=0 OR NOT EXISTS (SELECT 1 FROM commercial_deliveries bx "
                           "WHERE bx.source_sale_id=s.id AND bx.status!='CANCELLED'))")


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


def _growth_billing_lines(conn, range_end, location_id=None, category=None):
    """50kg Commercial Billing records, as customer purchase lines. Since the
    billing is no longer a sale, without these a Commercial account billed
    only through 50kg Commercial Billing (DR / WIWO / Reading, no POS sale)
    never showed as a customer at all. A billing generated from a POS
    delivery adds only its amount, on the delivery's date; the cylinders are
    already counted on that POS sale."""
    where = ["cd.status='BILLED'", "s.status='COMPLETED'", "s.customer_id IS NOT NULL",
             "date(COALESCE(src.ts, s.ts)) <= date(?)"]
    params = [range_end.isoformat()]
    if location_id:
        where.append("s.location_id=?"); params.append(location_id)
    if category:
        where.append("c.category=?"); params.append(category)
    out = []
    for r in conn.execute(
        f"""SELECT s.customer_id, date(COALESCE(src.ts, s.ts)) d, cd.billing_type, cd.delivered_qty,
                   cd.source_sale_id, s.total amount,
                   (SELECT COUNT(*) FROM commercial_delivery_cylinders x WHERE x.delivery_id=cd.id) wiwo_cyls
            FROM commercial_deliveries cd JOIN sales s ON s.id=cd.sale_id
            JOIN customers c ON c.id=s.customer_id
            LEFT JOIN sales src ON src.id=cd.source_sale_id AND src.status='COMPLETED'
            WHERE {' AND '.join(where)}""", params).fetchall():
        if r["source_sale_id"]:
            qty = 0
        elif r["billing_type"] == "DR":
            qty = r["delivered_qty"] or 0
        elif r["billing_type"] == "WIWO":
            qty = r["wiwo_cyls"] or 0
        else:
            qty = 0  # meter / tank reading: no cylinders
        out.append({"customer_id": r["customer_id"], "d": r["d"], "qty": qty, "amount": r["amount"] or 0})
    return out


def _build_customer_growth(conn, period="month", count=12, end_date=None, location_id=None, category=None):
    period = "week" if period == "week" else "month"
    count = max(2, min(int(count or 12), GROWTH_MAX_PERIODS))
    end_date = end_date or datetime.now().date()
    # One extra (hidden) period up front so the first shown period has a
    # "previous" to compare against.
    periods = _growth_periods(period, count + 1, end_date)
    range_start, range_end = periods[0][0], periods[-1][1]

    where = ["s.status='COMPLETED'", "s.is_opening_balance=0", "s.is_billing=0", "si.qty > 0", LPG_CYLINDER_SQL]
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
    lines = [dict(r) for r in lines] + _growth_billing_lines(conn, range_end, location_id, category)

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

    # bill_later lines (Commercial 50kg deliveries) are counted through
    # their 50kg Commercial Billing instead, so they aren't counted twice.
    # Until that delivery is billed, the delivered cylinders count, so the
    # customer's kg isn't missing in the meantime.
    where = ["s.status='COMPLETED'", "s.is_opening_balance=0", "si.qty > 0", UNBILLED_OR_CHARGED_SQL,
             LPG_CYLINDER_SQL, "date(s.ts) BETWEEN date(?) AND date(?)"]
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

    # Every account is listed, not only those that bought in the period, so
    # the report covers all customers (zero kg shows as "-"). Inactive
    # accounts are listed only if they bought something in the period.
    cust_sql = "SELECT id, account_no, name, business_name, category, status FROM customers"
    cust_params = []
    if category:
        cust_sql += " WHERE category=?"; cust_params.append(category)
    for c in conn.execute(cust_sql, cust_params).fetchall():
        if c["id"] in customers or (c["status"] or "ACTIVE") != "ACTIVE":
            continue
        customers[c["id"]] = blank(customer_id=c["id"], account_no=c["account_no"], name=c["name"],
                                   business_name=c["business_name"] or "", category=c["category"] or "")

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


# ---------------------------------------------------------------- sales department: targets
# Monthly cylinder targets in 4 tiers, set separately for Domestic (11kg and
# 1.4kg) and Commercial (50kg), for the whole business (OVERALL), per
# location, or per customer (normally MRTO / ERTO accounts). Actuals are
# cylinders on completed, non-voided sales in that month:
#   Domestic   = 11kg and 1.4kg cylinder lines (qty)
#   Commercial = 50kg cylinder lines, plus 50kg commercial billings, counted
#                as cylinders delivered (DR), cylinders weighed (WIWO) or
#                kg / 50 (meter reading)

TARGET_SCOPES = ("OVERALL", "LOCATION", "CUSTOMER")
TARGET_SEGMENTS = ("DOMESTIC", "COMMERCIAL")
TARGET_TIERS = (1, 2, 3, 4)
DOMESTIC_SIZES = (11.0, 1.4)
COMMERCIAL_SIZES = (50.0,)


def _target_segment(size):
    if size is None:
        return None
    if any(abs(size - s) < 0.01 for s in DOMESTIC_SIZES):
        return "DOMESTIC"
    if any(abs(size - s) < 0.01 for s in COMMERCIAL_SIZES):
        return "COMMERCIAL"
    return None


def _target_actuals(conn, month_from, month_to):
    """{(scope, scope_id, 'YYYY-MM', segment): cylinders} for every scope."""
    first_day = month_from + "-01"
    ly, lm = map(int, month_to.split("-"))
    last_day = (datetime(ly + (lm == 12), 1 if lm == 12 else lm + 1, 1).date() - timedelta(days=1)).isoformat()
    lines = conn.execute(
        f"""SELECT s.customer_id, s.location_id, strftime('%Y-%m', s.ts) ym, si.qty, p.name product_name, p.sku,
                   cd.billing_type cd_type, cd.delivered_qty,
                   (SELECT COUNT(*) FROM commercial_delivery_cylinders x WHERE x.delivery_id=cd.id) wiwo_cyls
            FROM sale_items si JOIN sales s ON s.id=si.sale_id JOIN products p ON p.id=si.product_id
            LEFT JOIN commercial_deliveries cd ON cd.sale_id=s.id
            WHERE s.status='COMPLETED' AND s.is_opening_balance=0 AND si.qty > 0 AND {UNBILLED_OR_CHARGED_SQL}
                  AND {LPG_CYLINDER_SQL} AND date(s.ts) BETWEEN date(?) AND date(?)""",
        (first_day, last_day),
    ).fetchall()
    out = {}
    for r in lines:
        size = product_kg_size(r["product_name"], r["sku"])
        if r["cd_type"]:
            seg = "COMMERCIAL"
            if r["cd_type"] == "DR":
                cyl = r["delivered_qty"] or 0
            elif r["cd_type"] == "WIWO":
                cyl = r["wiwo_cyls"] or 0
            else:
                cyl = float(r["qty"]) / (size or 50.0)
        else:
            seg = _target_segment(size)
            cyl = r["qty"]
        if not seg:
            continue
        keys = [("OVERALL", 0), ("LOCATION", r["location_id"])]
        if r["customer_id"]:
            keys.append(("CUSTOMER", r["customer_id"]))
        for scope, sid in keys:
            k = (scope, sid, r["ym"], seg)
            out[k] = out.get(k, 0) + cyl
    return {k: round(v, 2) for k, v in out.items()}


def _tier_result(tiers, actual):
    """tiers: {1: qty, ...}. Highest tier reached, and the next one to aim for."""
    set_tiers = sorted((t, q) for t, q in tiers.items() if q)
    reached = None
    for t, q in set_tiers:
        if actual >= q:
            reached = t
    nxt = next(((t, q) for t, q in set_tiers if actual < q), None)
    return {
        "tiers": {str(t): tiers.get(t) for t in TARGET_TIERS},
        "actual": actual,
        "reached_tier": reached,
        "next_tier": nxt[0] if nxt else None,
        "to_next_tier": round(nxt[1] - actual, 2) if nxt else None,
        "pct": {str(t): (round(actual * 100.0 / q, 1) if q else None) for t, q in tiers.items()},
    }


def _target_scope_args(d):
    scope = (d.get("scope") or "OVERALL").upper()
    if scope not in TARGET_SCOPES:
        raise ValueError("scope must be OVERALL, LOCATION or CUSTOMER")
    try:
        scope_id = 0 if scope == "OVERALL" else int(d.get("scope_id"))
    except (TypeError, ValueError):
        raise ValueError("Pick a " + scope.lower())
    return scope, scope_id


def _scope_name(conn, scope, scope_id):
    if scope == "OVERALL":
        return "Overall business"
    if scope == "LOCATION":
        r = conn.execute("SELECT name FROM locations WHERE id=?", (scope_id,)).fetchone()
        return r["name"] if r else None
    r = conn.execute("SELECT name, account_no, category FROM customers WHERE id=?", (scope_id,)).fetchone()
    return f"{r['name']} ({r['account_no']}, {r['category']})" if r else None


@app.get("/api/sales-targets")
@roles_or_module_required("ADMIN", module="sales_targets")
def get_sales_targets():
    """Targets and actuals for one scope (overall / a location / a customer)
    for one year: 12 months x 2 segments x 4 tiers."""
    try:
        scope, scope_id = _target_scope_args(request.args)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    year = request.args.get("year", type=int) or datetime.now().year
    months = [f"{year:04d}-{m:02d}" for m in range(1, 13)]
    conn = get_db()
    name = _scope_name(conn, scope, scope_id)
    if name is None:
        conn.close()
        return jsonify(error="Not found"), 404
    rows = conn.execute(
        "SELECT month, segment, tier, qty FROM sales_targets WHERE scope=? AND scope_id=? AND month BETWEEN ? AND ?",
        (scope, scope_id, months[0], months[-1])).fetchall()
    actuals = _target_actuals(conn, months[0], months[-1])
    conn.close()
    grid = {seg: {ym: {t: None for t in TARGET_TIERS} for ym in months} for seg in TARGET_SEGMENTS}
    for r in rows:
        grid[r["segment"]][r["month"]][r["tier"]] = r["qty"]
    result = {seg: {ym: _tier_result(grid[seg][ym], actuals.get((scope, scope_id, ym, seg), 0)) for ym in months}
              for seg in TARGET_SEGMENTS}
    return jsonify(scope=scope, scope_id=scope_id, scope_name=name, year=year, months=months,
                   month_labels=[datetime.strptime(ym, "%Y-%m").strftime("%b %Y") for ym in months],
                   segments=result)


@app.put("/api/sales-targets")
@roles_or_module_required("ADMIN", module="sales_targets")
def save_sales_targets():
    """Save the year's target grid for one scope. A blank / 0 qty clears it."""
    d = request.get_json(force=True) or {}
    try:
        scope, scope_id = _target_scope_args(d)
        year = int(d.get("year"))
        if not 2000 <= year <= 2100:
            raise ValueError
    except ValueError as e:
        return jsonify(error=str(e) or "Invalid year"), 400
    except TypeError:
        return jsonify(error="Invalid year"), 400
    cells = d.get("targets") or []
    conn = get_db()
    if _scope_name(conn, scope, scope_id) is None:
        conn.close()
        return jsonify(error="Not found"), 404
    try:
        clean = []
        for c in cells:
            ym, seg = str(c.get("month") or ""), (c.get("segment") or "").upper()
            try:
                tier = int(c.get("tier"))
            except (TypeError, ValueError):
                raise ValueError("tier must be 1 to 4")
            if not ym.startswith(f"{year:04d}-") or not _parse_ym(ym):
                raise ValueError(f"Month {ym} is not in {year}")
            if seg not in TARGET_SEGMENTS or tier not in TARGET_TIERS:
                raise ValueError("segment must be DOMESTIC or COMMERCIAL and tier 1 to 4")
            raw = c.get("qty")
            if raw in (None, ""):
                qty = 0
            else:
                try:
                    qty = int(float(raw))
                except (TypeError, ValueError):
                    raise ValueError("Target quantities must be whole numbers")
            if qty < 0:
                raise ValueError("Target quantities can't be negative")
            clean.append((ym, seg, tier, qty))
    except ValueError as e:
        conn.close()
        return jsonify(error=str(e)), 400
    u = session["user"]
    for ym, seg, tier, qty in clean:
        if qty:
            conn.execute(
                """INSERT INTO sales_targets (scope, scope_id, month, segment, tier, qty, updated_by, updated_at)
                   VALUES (?,?,?,?,?,?,?,datetime('now'))
                   ON CONFLICT(scope, scope_id, month, segment, tier)
                   DO UPDATE SET qty=excluded.qty, updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
                (scope, scope_id, ym, seg, tier, qty, u["id"]))
        else:
            conn.execute("DELETE FROM sales_targets WHERE scope=? AND scope_id=? AND month=? AND segment=? AND tier=?",
                         (scope, scope_id, ym, seg, tier))
    audit(conn, "UPDATE", "SALES_TARGETS", scope_id, f"{scope} {year}: {sum(1 for c in clean if c[3])} target(s) set")
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.get("/api/sales-targets/achievement")
@roles_or_module_required("ADMIN", module="sales_targets")
def sales_target_achievement():
    """One month: every scope that has a target (overall always shown), with
    actual cylinders vs each tier, for Domestic and Commercial."""
    month = request.args.get("month") or datetime.now().strftime("%Y-%m")
    if not _parse_ym(month):
        return jsonify(error="month must be YYYY-MM"), 400
    conn = get_db()
    rows = conn.execute("SELECT scope, scope_id, segment, tier, qty FROM sales_targets WHERE month=?", (month,)).fetchall()
    actuals = _target_actuals(conn, month, month)
    scopes = {("OVERALL", 0): {}}
    for r in rows:
        scopes.setdefault((r["scope"], r["scope_id"]), {}).setdefault(r["segment"], {})[r["tier"]] = r["qty"]
    out = []
    for (scope, sid), segs in scopes.items():
        name = _scope_name(conn, scope, sid)
        if name is None:
            continue
        out.append({
            "scope": scope, "scope_id": sid, "name": name,
            "segments": {seg: _tier_result({t: segs.get(seg, {}).get(t) for t in TARGET_TIERS},
                                           actuals.get((scope, sid, month, seg), 0)) for seg in TARGET_SEGMENTS},
        })
    conn.close()
    order = {s: i for i, s in enumerate(TARGET_SCOPES)}
    out.sort(key=lambda x: (order[x["scope"]], x["name"].lower()))
    return jsonify(month=month, month_label=datetime.strptime(month, "%Y-%m").strftime("%B %Y"), rows=out)


# ---------------------------------------------------------------- one-time fix: old Commercial SOA
# Before the "bill 50kg only through Commercial Billing" change, POS credit
# sales to Commercial-category customers charged the 50kg SRP, so it sits
# on their receivables / SOA. This turns those 50kg lines into delivery-only
# lines (gas ₱0, SRP kept for reference), the same as new sales, and lowers
# the sale and its receivable. Stock is not touched. Sales that came from
# 50kg Commercial Billing itself are never changed. A sale where the
# customer already paid more than the new amount due is skipped and listed.

def _commercial_soa_fix_plan(conn):
    rows = conn.execute(
        """SELECT s.*, c.name customer_name, c.account_no, c.category
           FROM sales s JOIN customers c ON c.id = s.customer_id
           WHERE s.sale_type='CREDIT' AND s.status='COMPLETED' AND s.is_opening_balance=0
             AND LOWER(TRIM(COALESCE(c.category,''))) = ?
             AND NOT EXISTS (SELECT 1 FROM commercial_deliveries cd WHERE cd.sale_id = s.id)
           ORDER BY s.ts, s.id""", (COMMERCIAL_CATEGORY,)).fetchall()
    plan, skipped = [], []
    for s in rows:
        items = conn.execute(
            """SELECT si.*, p.sku, p.deposit_price cur_deposit_price FROM sale_items si
               JOIN products p ON p.id = si.product_id
               WHERE si.sale_id=? AND COALESCE(si.bill_later,0)=0""", (s["id"],)).fetchall()
        changes = []
        for it in items:
            if not is_commercial_cylinder({"name": it["product_name"], "sku": it["sku"]}):
                continue
            extra = (it["swap_fee"] or 0) if it["sale_mode"] == "SWAP" else \
                (it["cur_deposit_price"] or 0) if it["sale_mode"] == "DEPOSIT" else 0.0
            extra = min(max(float(extra), 0.0), float(it["price"]))
            gas = round(float(it["price"]) - extra, 2)
            if gas <= 0:
                continue
            new_sub = round(extra * it["qty"], 2)
            changes.append({"item_id": it["id"], "product_name": it["product_name"], "qty": it["qty"],
                            "srp_price": gas, "new_price": round(extra, 2), "new_subtotal": new_sub,
                            "removed": round(it["subtotal"] - new_sub, 2),
                            "old_discount": it["discount_amount"] or 0})
        if not changes:
            continue
        removed_sub = round(sum(c["removed"] for c in changes), 2)
        removed_disc = round(sum(c["old_discount"] for c in changes), 2)
        new_subtotal = round(s["subtotal"] - removed_sub, 2)
        new_discount = round(max((s["discount"] or 0) - removed_disc, 0), 2)
        new_total = round(new_subtotal - new_discount, 2)
        new_wht = compute_withholding_tax(new_total) if (s["withholding_tax"] or 0) > 0 else 0.0
        new_due = round(new_total - new_wht, 2)
        paid = round(s["paid"] or 0, 2)
        recv = conn.execute("SELECT * FROM receivables WHERE sale_id=?", (s["id"],)).fetchone()
        entry = {
            "sale_id": s["id"], "sale_no": s["sale_no"], "dr_no": s["dr_no"], "date": s["ts"],
            "customer_id": s["customer_id"], "customer_name": s["customer_name"], "account_no": s["account_no"],
            "invoice_no": recv["invoice_no"] if recv else None,
            "old_total": s["total"], "new_total": new_total, "old_due": round(s["total"] - (s["withholding_tax"] or 0), 2),
            "new_due": new_due, "paid": paid, "old_balance": s["balance"], "new_balance": round(new_due - paid, 2),
            "lines": [{"product_name": c["product_name"], "qty": c["qty"], "srp_price": c["srp_price"]} for c in changes],
            "_changes": changes, "_new": (new_subtotal, new_discount, new_total, new_wht), "_recv": recv,
        }
        if paid > new_due + 0.009:
            entry["reason"] = (f"Already paid {paid:,.2f}, more than the new amount due {new_due:,.2f} — "
                               "fix by hand (e.g. refund or apply to a billing)")
            skipped.append(entry)
        else:
            plan.append(entry)
    return plan, skipped


def _public_fix_entry(e):
    return {k: v for k, v in e.items() if not k.startswith("_")}


@app.get("/api/admin/commercial-soa-fix")
@roles_required("ADMIN")
def commercial_soa_fix_preview():
    conn = get_db()
    plan, skipped = _commercial_soa_fix_plan(conn)
    conn.close()
    return jsonify(sales=[_public_fix_entry(e) for e in plan], skipped=[_public_fix_entry(e) for e in skipped],
                   total_removed=round(sum(e["old_balance"] - e["new_balance"] for e in plan), 2))


@app.post("/api/admin/commercial-soa-fix")
@roles_required("ADMIN")
def commercial_soa_fix_apply():
    from db import DB_PATH
    d = request.get_json(force=True) or {}
    wanted = {int(x) for x in (d.get("sale_ids") or [])}
    if not wanted:
        return jsonify(error="No sales selected"), 400
    backup_path = f"{DB_PATH}.before-commercial-soa-fix-{datetime.now().strftime('%Y%m%d-%H%M%S')}.bak"
    try:
        _backup_database(backup_path)
    except Exception as e:
        return jsonify(error=f"Could not save a backup first, nothing was changed: {e}"), 500
    conn = get_db()
    plan, _ = _commercial_soa_fix_plan(conn)
    done = []
    try:
        for e in plan:
            if e["sale_id"] not in wanted:
                continue
            for c in e["_changes"]:
                conn.execute(
                    """UPDATE sale_items SET bill_later=1, srp_price=?, price=?, subtotal=?, discount_id=NULL,
                           discount_name=NULL, discount_amount=0 WHERE id=?""",
                    (c["srp_price"], c["new_price"], c["new_subtotal"], c["item_id"]))
            new_subtotal, new_discount, new_total, new_wht = e["_new"]
            conn.execute("UPDATE sales SET subtotal=?, discount=?, total=?, withholding_tax=?, balance=? WHERE id=?",
                         (new_subtotal, new_discount, new_total, new_wht, e["new_balance"], e["sale_id"]))
            r = e["_recv"]
            if r:
                if e["new_due"] <= 0.009 and e["paid"] <= 0.009:
                    conn.execute("DELETE FROM receivables WHERE id=?", (r["id"],))
                else:
                    conn.execute("UPDATE receivables SET amount=?, paid=?, balance=? WHERE id=?",
                                 (e["new_due"], e["paid"], e["new_balance"], r["id"]))
                    recompute_receivable_status(conn, r["id"])
            audit(conn, "FIX", "SALES", e["sale_id"],
                  f"Commercial SOA fix {e['sale_no']}: 50kg made delivery-only, total {e['old_total']} -> {e['new_total']}, "
                  f"balance {e['old_balance']} -> {e['new_balance']}")
            done.append(e["sale_no"])
        conn.commit()
    except Exception as ex:
        conn.rollback(); conn.close()
        return jsonify(error=f"Fix failed, nothing was changed: {ex}"), 500
    conn.close()
    return jsonify(ok=True, fixed=done, backup=os.path.basename(backup_path))


# ---------------------------------------------------------------- end of day report
# Per-attendant cash-up: everything they collected on a given day, totalled
# by payment method (Cash, GCash, Bank Transfer, ...), split between POS
# sales and collections on receivables. Attendants only ever see their own;
# Admin/Manager/Accounting can pick any staff member (or everyone).

EOD_SUPERVISOR_ROLES = ("ADMIN", "OPERATION_HEAD", "MANAGER", "ACCOUNTING")


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
    s_where = ["date(s.ts) = date(?)", "s.is_opening_balance = 0", "s.is_billing = 0"]
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
    paid = conn.execute(
        f"""SELECT COALESCE(SUM(p.amount),0) a,
                   COALESCE(SUM(CASE WHEN s.sale_type='CASH' THEN p.amount END),0) cash_paid,
                   COALESCE(SUM(CASE WHEN s.sale_type='CREDIT' THEN p.amount END),0) credit_paid
            FROM payments p JOIN sales s ON s.id=p.sale_id
            WHERE {sw} AND s.status='COMPLETED' AND p.payment_source='SALE'""",
        s_params,
    ).fetchone()
    paid_at_sale = paid["a"]
    cash_wht = conn.execute(
        f"""SELECT COALESCE(SUM(withholding_tax),0) w FROM sales s
            WHERE {sw} AND status='COMPLETED' AND sale_type='CASH'""", s_params).fetchone()["w"]
    sales_summary = {k: (round(sales[k], 2) if isinstance(sales[k], float) else sales[k]) for k in sales.keys()}
    sales_summary["charged_to_accounts"] = round(sales["total"] - paid_at_sale, 2)
    # How "Cash sales" (sale totals) ties to what was received at the POS
    # (Collections by Payment Method): down payments on credit / terms sales
    # are received too, 1% WHT on cash sales is not, and a cash sale can be
    # paid above its amount due (change typed in as payment, or the sale
    # lowered afterwards).
    sales_summary["paid_at_sale"] = round(paid_at_sale, 2)
    sales_summary["credit_paid_at_sale"] = round(paid["credit_paid"], 2)
    sales_summary["cash_wht"] = round(cash_wht, 2)
    sales_summary["cash_overpaid"] = round(paid["cash_paid"] - (sales["cash_sales"] - cash_wht), 2)
    sales_summary["overpaid_sales"] = [dict(r) for r in conn.execute(
        f"""SELECT s.id, s.sale_no, s.ts, s.total, s.withholding_tax, s.paid,
                   ROUND(s.paid - (s.total - s.withholding_tax), 2) over, s.edited_at
            FROM sales s WHERE {sw} AND s.status='COMPLETED' AND s.sale_type='CASH'
              AND s.paid - (s.total - s.withholding_tax) > 0.009 ORDER BY s.ts""", s_params).fetchall()]

    items = conn.execute(
        f"""SELECT si.product_name, si.sale_mode, SUM(si.qty) qty, SUM(si.subtotal) amount
            FROM sale_items si JOIN sales s ON s.id = si.sale_id
            WHERE {sw} AND s.status='COMPLETED'
            GROUP BY si.product_name, si.sale_mode ORDER BY si.product_name""",
        s_params,
    ).fetchall()

    # For Delivery orders: not in the sales above until they're delivered.
    # Shown so the day's orders reconcile with what actually went out.
    def _fd(date_col, user_col, extra=""):
        w, prm = [f"date(ps.{date_col}) = date(?)"], [report_date]
        if user_id:
            w.append(f"ps.{user_col} = ?"); prm.append(user_id)
        if location_id:
            w.append("ps.location_id = ?"); prm.append(location_id)
        r = conn.execute(f"SELECT COUNT(*) n, COALESCE(SUM(ps.total),0) t FROM pending_sales ps "
                         f"WHERE {' AND '.join(w)}{extra}", prm).fetchone()
        return {"count": r["n"], "total": round(r["t"], 2)}
    o_w, o_p = ["ps.status='PENDING'", "date(ps.ts) <= date(?)"], [report_date]
    if user_id:
        o_w.append("ps.created_by = ?"); o_p.append(user_id)
    if location_id:
        o_w.append("ps.location_id = ?"); o_p.append(location_id)
    open_rows = conn.execute(
        f"""SELECT ps.pending_no, ps.ts, ps.total, ps.sale_type, COALESCE(c.name, ps.walkin_name) customer_name
            FROM pending_sales ps LEFT JOIN customers c ON c.id=ps.customer_id
            WHERE {' AND '.join(o_w)} ORDER BY ps.ts""", o_p).fetchall()
    for_delivery = {
        "taken": _fd("ts", "created_by"),
        "delivered": _fd("delivered_at", "delivered_by", " AND ps.status='DELIVERED'"),
        "open": {"count": len(open_rows), "total": round(sum(r["total"] for r in open_rows), 2)},
        "open_list": [{**dict(r), "ts": _local_ts(r["ts"])} for r in open_rows],
    }

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
        "for_delivery": for_delivery,
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

def _role_assign_problem(actor_role, target_role):
    """Only Admin can give out the ADMIN role, and only Admin / Operation Head
    can give out OPERATION_HEAD — otherwise a Manager (or an Operation Head)
    could hand themselves more access, e.g. the Admin-only System Reset."""
    if target_role == "ADMIN" and actor_role != "ADMIN":
        return "Only the Admin can assign the ADMIN role"
    if target_role == "OPERATION_HEAD" and not is_admin_like(actor_role):
        return "Only the Admin or an Operation Head can assign the OPERATION_HEAD role"
    return None


def _user_manage_problem(conn, uid):
    """Non-Admins can't change, disable or delete an Admin account (which
    would let them log in as Admin); Managers can't touch Operation Heads."""
    actor = session["user"]["role"]
    row = conn.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        return None
    if row["role"] == "ADMIN" and actor != "ADMIN":
        return "Only the Admin can change an Admin account"
    if row["role"] == "OPERATION_HEAD" and not is_admin_like(actor):
        return "Only the Admin or an Operation Head can change an Operation Head account"
    return None


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
    problem = _role_assign_problem(u["role"], role)
    if problem:
        return jsonify(error=problem), 403
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
    problem = _user_manage_problem(conn, existing["id"]) or _role_assign_problem(u["role"], new_role)
    if problem:
        conn.close()
        return jsonify(error=problem), 403
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
    problem = _user_manage_problem(conn, uid)
    if problem:
        conn.close()
        return jsonify(error=problem), 403
    conn.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
    audit(conn, "DISABLE", "USERS", uid)
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.post("/api/users/<int:uid>/enable")
@roles_required("ADMIN", "MANAGER")
def enable_user(uid):
    conn = get_db()
    problem = _user_manage_problem(conn, uid)
    if problem:
        conn.close()
        return jsonify(error=problem), 403
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
    problem = _user_manage_problem(conn, uid)
    if problem:
        conn.close()
        return jsonify(error=problem), 403
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
@roles_or_module_required("ADMIN", "MANAGER", "DISPATCHER", "ACCOUNTING", module="commercial_deliveries")
def list_commercial_deliveries():
    u = session["user"]
    conn = get_db()
    sql = """SELECT cd.*, c.name customer_name, c.account_no, l.name location_name, p.name product_name,
                     creator.name created_by_name, src.sale_no source_sale_no, src.dr_no source_dr_no
              FROM commercial_deliveries cd LEFT JOIN sales src ON src.id=cd.source_sale_id JOIN customers c ON c.id=cd.customer_id
              JOIN locations l ON l.id=cd.location_id JOIN products p ON p.id=cd.product_id
              LEFT JOIN users creator ON creator.id=cd.created_by WHERE 1=1"""
    params = []
    if u["role"] not in ALL_LOCATION_FINANCE_ROLES and u.get("location_id"):
        sql += " AND cd.location_id=?"
        params.append(u["location_id"])
    sql += " ORDER BY cd.delivery_date DESC"
    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        if r["billing_type"] == "DR":
            total_net_in = round((r["delivered_qty"] or 0) * (r["kg_per_cylinder"] or 0), 2)
            total_consumption = round(total_net_in - (r["total_residual_kg"] or 0), 2)
            cyls = [dict(c) for c in conn.execute(
                "SELECT * FROM commercial_pullouts WHERE delivery_id=? ORDER BY id", (r["id"],)).fetchall()]
        elif r["billing_type"] == "READING":
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


def _meter_previous_reading(conn, customer_id, meter_label):
    """(previous_reading, source) for the next READING bill of a meter:
    the current reading of its last bill, else the beginning reading entered
    for it. (None, None) means the beginning reading must be entered first."""
    meter_label = (meter_label or "").strip()
    row = conn.execute(
        """SELECT current_reading FROM commercial_deliveries
           WHERE customer_id=? AND billing_type='READING' AND current_reading IS NOT NULL
                 AND status != 'CANCELLED' AND TRIM(COALESCE(meter_label,''))=?
           ORDER BY id DESC LIMIT 1""",
        (customer_id, meter_label),
    ).fetchone()
    if row:
        return row["current_reading"], "LAST_BILL"
    m = conn.execute("SELECT beginning_reading FROM commercial_meters WHERE customer_id=? AND meter_label=?",
                     (customer_id, meter_label)).fetchone()
    if m:
        return m["beginning_reading"], "BEGINNING"
    return None, None


@app.get("/api/commercial-deliveries/last-reading")
@roles_or_module_required("ADMIN", "MANAGER", "DISPATCHER", "ACCOUNTING", module="commercial_deliveries")
def last_meter_reading():
    """The previous reading for a new READING-type bill, like a utility
    company: the ending reading of that meter's last bill, or its beginning
    reading for the first bill. Also lists the customer's known meters."""
    customer_id = request.args.get("customer_id", type=int)
    meter_label = (request.args.get("meter_label") or "").strip()
    if not customer_id:
        return jsonify(error="customer_id is required"), 400
    conn = get_db()
    prev, source = _meter_previous_reading(conn, customer_id, meter_label)
    beginning = conn.execute("SELECT beginning_reading, set_at FROM commercial_meters WHERE customer_id=? AND meter_label=?",
                             (customer_id, meter_label)).fetchone()
    meters = [r["label"] for r in conn.execute(
        """SELECT meter_label label FROM commercial_meters WHERE customer_id=?
           UNION SELECT TRIM(COALESCE(meter_label,'')) FROM commercial_deliveries
                 WHERE customer_id=? AND billing_type='READING' AND status != 'CANCELLED'
           ORDER BY 1""", (customer_id, customer_id)).fetchall()]
    conn.close()
    return jsonify(previous_reading=prev, source=source, meters=meters,
                   beginning_reading=beginning["beginning_reading"] if beginning else None,
                   beginning_set_at=beginning["set_at"] if beginning else None)


@app.post("/api/commercial-meters")
@roles_or_module_required("ADMIN", "MANAGER", "DISPATCHER", "ACCOUNTING", module="commercial_deliveries")
def set_meter_beginning_reading():
    """Enter the beginning reading of a customer's meter / tank. Needed once,
    before its first READING bill. Changing it afterwards is for
    Admin / Manager / Accounting only."""
    d = request.get_json(force=True) or {}
    u = session["user"]
    meter_label = (d.get("meter_label") or "").strip()
    try:
        customer_id = int(d.get("customer_id"))
    except (TypeError, ValueError):
        return jsonify(error="customer_id is required"), 400
    try:
        reading = _num(d.get("beginning_reading"), "Beginning reading")
    except ValueError as e:
        return jsonify(error=str(e)), 400
    if reading < 0:
        return jsonify(error="The beginning reading can't be negative"), 400
    conn = get_db()
    if not conn.execute("SELECT 1 FROM customers WHERE id=?", (customer_id,)).fetchone():
        conn.close()
        return jsonify(error="Customer not found"), 404
    existing = conn.execute("SELECT * FROM commercial_meters WHERE customer_id=? AND meter_label=?",
                            (customer_id, meter_label)).fetchone()
    if existing:
        if u["role"] not in COMMERCIAL_RATE_ROLES:
            conn.close()
            return jsonify(error="This meter already has a beginning reading. Ask Admin / Accounting to change it."), 403
        conn.execute("UPDATE commercial_meters SET beginning_reading=?, set_by=?, set_at=datetime('now') WHERE id=?",
                     (reading, u["id"], existing["id"]))
        audit(conn, "UPDATE", "COMMERCIAL_METERS", existing["id"],
              f"customer {customer_id} meter '{meter_label or 'default'}': beginning {existing['beginning_reading']} -> {reading}")
    else:
        cur = conn.execute("INSERT INTO commercial_meters (customer_id, meter_label, beginning_reading, set_by) VALUES (?,?,?,?)",
                           (customer_id, meter_label, reading, u["id"]))
        audit(conn, "CREATE", "COMMERCIAL_METERS", cur.lastrowid,
              f"customer {customer_id} meter '{meter_label or 'default'}': beginning reading {reading}")
    conn.commit()
    prev, source = _meter_previous_reading(conn, customer_id, meter_label)
    conn.close()
    return jsonify(ok=True, previous_reading=prev, source=source)


@app.post("/api/commercial-deliveries")
@roles_or_module_required("ADMIN", "MANAGER", "DISPATCHER", "ACCOUNTING", module="commercial_deliveries")
def create_commercial_delivery():
    d = request.get_json(force=True)
    u = session["user"]
    if u["role"] in ("ADMIN", "OPERATION_HEAD", "MANAGER") or not u.get("location_id"):
        # Users without an assigned branch (e.g. Accounting) choose one in the form.
        location_id = d.get("location_id")
    else:
        location_id = u["location_id"]
    billing_type = d.get("billing_type", "WIWO")
    if billing_type not in ("WIWO", "READING", "DR"):
        return jsonify(error="billing_type must be WIWO, READING or DR"), 400
    if not d.get("customer_id") or not d.get("product_id") or not location_id:
        return jsonify(error="customer_id, product_id and location_id are required"), 400

    if billing_type == "DR":
        return _create_dr_billing(d, location_id)

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
        # The previous reading is never typed: it is the last bill's current
        # reading, or the meter's beginning reading for its first bill.
        meter_label = (d.get("meter_label") or "").strip()
        previous_reading, _src = _meter_previous_reading(conn, d["customer_id"], meter_label)
        if previous_reading is None:
            conn.close()
            return jsonify(error=f"Enter the beginning reading of meter '{meter_label or 'default'}' first — "
                                 "it becomes the previous reading of the first bill"), 400
        try:
            current_reading = float(d.get("current_reading"))
        except (TypeError, ValueError):
            conn.close()
            return jsonify(error="The current reading must be a number"), 400
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
             meter_label, previous_reading, current_reading, "PICKED_UP",
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
@roles_or_module_required("ADMIN", "MANAGER", "DISPATCHER", "ACCOUNTING", module="commercial_deliveries")
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
    if row["billing_type"] == "DR":
        pullouts = conn.execute("SELECT * FROM commercial_pullouts WHERE delivery_id=? ORDER BY id", (did,)).fetchall()
        sale = conn.execute("SELECT sale_no, dr_no FROM sales WHERE id=?", (row["sale_id"],)).fetchone() if row["sale_id"] else None
        conn.close()
        delivered_kg = round((row["delivered_qty"] or 0) * (row["kg_per_cylinder"] or 0), 2)
        return jsonify({**dict(row), "cylinders": [dict(c) for c in pullouts], "delivered_kg": delivered_kg,
                        "total_consumption_weight": round(delivered_kg - (row["total_residual_kg"] or 0), 2),
                        "sale_no": sale["sale_no"] if sale else None, "sale_dr_no": sale["dr_no"] if sale else None})
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
@roles_or_module_required("ADMIN", "MANAGER", "DISPATCHER", "ACCOUNTING", module="commercial_deliveries")
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
    data = dict(row)
    if row["billing_type"] == "DR":
        cyls = conn.execute("SELECT * FROM commercial_pullouts WHERE delivery_id=? ORDER BY id", (did,)).fetchall()
        sale = conn.execute("SELECT dr_no FROM sales WHERE id=?", (row["sale_id"],)).fetchone() if row["sale_id"] else None
        data["sale_dr_no"] = sale["dr_no"] if sale else None
    else:
        cyls = conn.execute("SELECT * FROM commercial_delivery_cylinders WHERE delivery_id=?", (did,)).fetchall()
    conn.close()
    fname = f"{row['delivery_no']}.pdf"
    return commercial_billing_pdf(data, [dict(c) for c in cyls], fname)


@app.put("/api/commercial-deliveries/<int:did>/pickup")
@roles_or_module_required("ADMIN", "MANAGER", "DISPATCHER", "ACCOUNTING", module="commercial_deliveries")
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


# Roles that may type their own price per kg on a DR billing. Everyone else
# with the 50kg Commercial Billing module bills at the product's default.
COMMERCIAL_RATE_ROLES = ("ADMIN", "OPERATION_HEAD", "MANAGER", "ACCOUNTING")


def _num(v, label, allow_blank=False):
    if v is None or (isinstance(v, str) and not v.strip()):
        if allow_blank:
            return None
        raise ValueError(f"{label} is required")
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number")


def _commercial_rate(customer, product, typed=None):
    """(price per kg, source) for a commercial billing. The rate Accounting
    set on the Commercial account always wins; without one, a rate typed by
    a rate role, else the product's default price per kg."""
    rate = customer["commercial_rate"] if "commercial_rate" in customer.keys() else None
    if rate and rate > 0:
        return float(rate), "CUSTOMER"
    if typed not in (None, "") and session["user"]["role"] in COMMERCIAL_RATE_ROLES:
        return _num(typed, "Price per kg"), "TYPED"
    return float(product["price_per_kg"] or 0), "PRODUCT"


def _create_dr_billing(d, location_id, source_sale=None):
    """DR (manual) 50kg billing, the way the business actually does it: bill
    right at delivery, even when the serial numbers of the 50kg cylinders
    pulled out don't match the ones delivered.

        billable kg = delivered cylinders x kg per cylinder
                      - residual kg left in the cylinders pulled out

    Creates the receivable at once (carried by a hidden is_billing credit
    "sale", so it is not counted as a sale), so the record is BILLED
    immediately. With source_sale (Generate Billing on a POS delivery sale)
    the delivery's own DR is used instead of issuing a new one."""
    u = session["user"]
    conn = get_db()
    try:
        product = conn.execute("SELECT * FROM products WHERE id=? AND active=1", (d["product_id"],)).fetchone()
        customer = conn.execute("SELECT * FROM customers WHERE id=?", (d["customer_id"],)).fetchone()
        if not product:
            raise ValueError("That product no longer exists. Refresh the page and pick it again.")
        if not customer:
            raise ValueError("Customer not found")
        if not conn.execute("SELECT 1 FROM locations WHERE id=?", (location_id,)).fetchone():
            raise ValueError("Location not found")
        try:
            delivered_qty = int(d.get("delivered_qty"))
        except (TypeError, ValueError):
            raise ValueError("Number of cylinders delivered must be a whole number")
        if delivered_qty <= 0:
            raise ValueError("Number of cylinders delivered must be at least 1")
        kg_per_cyl = _num(d.get("kg_per_cylinder"), "kg per cylinder", allow_blank=True)
        if kg_per_cyl is None:
            kg_per_cyl = product_kg_size(product["name"], product["sku"]) or 50.0
        if kg_per_cyl <= 0:
            raise ValueError("kg per cylinder must be positive")

        pullouts = []
        for i, po in enumerate(d.get("pullouts") or [], start=1):
            serial = (po.get("serial_no") or "").strip()
            gw = _num(po.get("gross_weight"), f"Pull-out #{i} gross weight", allow_blank=True)
            tw = _num(po.get("tare_weight"), f"Pull-out #{i} tare weight", allow_blank=True)
            if gw is not None and tw is not None:
                residual = round(gw - tw, 2)
            else:
                residual = _num(po.get("residual_kg"), f"Pull-out #{i} residual kg", allow_blank=True) or 0.0
            if residual < 0:
                raise ValueError(f"Pull-out #{i}{' (' + serial + ')' if serial else ''}: residual can't be negative — "
                                 "check the gross and tare weights")
            if residual > kg_per_cyl:
                raise ValueError(f"Pull-out #{i}{' (' + serial + ')' if serial else ''}: residual {residual} kg is more "
                                 f"than a full {kg_per_cyl:g} kg cylinder — check the reading")
            pullouts.append((serial, gw, tw, round(residual, 2)))

        delivered_kg = round(delivered_qty * kg_per_cyl, 2)
        total_residual = round(sum(p[3] for p in pullouts), 2)
        # Billing from a POS sale may give just the overall residual (no per-cylinder rows).
        if not pullouts:
            overall = _num(d.get("total_residual_kg"), "Total residual kg", allow_blank=True)
            if overall is not None:
                if overall < 0:
                    raise ValueError("Total residual can't be negative")
                total_residual = round(overall, 2)
        billable_kg = round(delivered_kg - total_residual, 2)
        if billable_kg <= 0:
            raise ValueError("Residual is equal to or more than what was delivered — nothing to bill")

        price_per_kg, _src = _commercial_rate(customer, product, d.get("price_per_kg"))
        if price_per_kg <= 0:
            raise ValueError("No rate per kg — Accounting can set this Commercial account's rate (Customers > Rate)"
                             + (", or enter one now" if u["role"] in COMMERCIAL_RATE_ROLES else ""))
        billed_amount = round(billable_kg * price_per_kg, 2)
        dr_ref = (d.get("dr_ref") or "").strip()
        if source_sale is not None and not dr_ref:
            dr_ref = source_sale["dr_no"] or source_sale["sale_no"]

        delivery_no = next_number(conn, "CD", "commercial_deliveries", "delivery_no")
        cur = conn.execute(
            """INSERT INTO commercial_deliveries (delivery_no, customer_id, location_id, product_id, billing_type, status,
                   pickup_date, notes, created_by, collected_by, dr_ref, delivered_qty, kg_per_cylinder, total_residual_kg,
                   source_sale_id)
               VALUES (?,?,?,?,'DR','DELIVERED',datetime('now'),?,?,?,?,?,?,?,?)""",
            (delivery_no, customer["id"], location_id, product["id"], d.get("notes", ""), u["id"], u["id"],
             dr_ref, delivered_qty, kg_per_cyl, total_residual, source_sale["id"] if source_sale is not None else None),
        )
        did = cur.lastrowid
        for serial, gw, tw, residual in pullouts:
            conn.execute("INSERT INTO commercial_pullouts (delivery_id, serial_no, gross_weight, tare_weight, residual_kg) "
                         "VALUES (?,?,?,?,?)", (did, serial, gw, tw, residual))
        desc = (f"Commercial DR billing {delivery_no}" + (f" (DR {dr_ref})" if dr_ref else "") +
                (f" for {source_sale['sale_no']}" if source_sale is not None else "") +
                f": {delivered_qty} x {kg_per_cyl:g}kg = {delivered_kg}kg delivered - {total_residual}kg residual "
                f"{f'({len(pullouts)} pulled out) ' if pullouts else ''}= {billable_kg}kg @ {money_fmt(price_per_kg)}/kg")
        sale_id, sale_no, dr_no = _bill_delivery_as_credit_sale(
            conn, did, customer, product, location_id, billable_kg, price_per_kg, desc,
            f"{product['name']} (DR billing)", with_dr_no=source_sale is None)
        audit(conn, "CREATE", "COMMERCIAL_DELIVERIES", did,
              f"{delivery_no} (DR{' ' + dr_ref if dr_ref else ''}) -> {sale_no}: {delivered_kg}kg - {total_residual}kg residual "
              f"= {billable_kg}kg @ {price_per_kg}/kg = {billed_amount}")
        conn.commit()
    except ValueError as e:
        conn.rollback(); conn.close()
        return jsonify(error=str(e)), 400
    conn.close()
    return jsonify(id=did, delivery_no=delivery_no, sale_id=sale_id, sale_no=sale_no, dr_no=dr_no,
                   delivered_kg=delivered_kg, total_residual_kg=total_residual, billable_kg=billable_kg,
                   price_per_kg=price_per_kg, billed_amount=billed_amount), 201


def _bill_delivery_as_credit_sale(conn, did, customer, product, location_id, qty_kg, price_per_kg, desc, item_label,
                                  with_dr_no=False):
    """Turn a commercial delivery into a receivable and mark it BILLED. The
    receivable needs a sale row to hang on (payments, SOA, aging all key on
    it), so one is made with is_billing=1, which keeps it out of the Sales
    list and every sales report. Caller commits."""
    u = session["user"]
    billed_amount = round(qty_kg * price_per_kg, 2)
    sale_no = next_number(conn, "SALE", "sales", "sale_no")
    dr_no = next_number(conn, "DR", "sales", "dr_no") if with_dr_no else None
    cur = conn.execute(
        """INSERT INTO sales (sale_no, location_id, user_id, customer_id, sale_type, subtotal, discount, total, paid, balance, notes, dr_no,
                              is_billing)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)""",
        (sale_no, location_id, u["id"], customer["id"], "CREDIT",
         billed_amount, 0, billed_amount, 0, billed_amount, desc, dr_no),
    )
    sale_id = cur.lastrowid
    conn.execute(
        "INSERT INTO sale_items (sale_id, product_id, product_name, qty, price, subtotal, sale_mode) VALUES (?,?,?,?,?,?,?)",
        (sale_id, product["id"], item_label, qty_kg, price_per_kg, billed_amount, "REFILL"),
    )
    invoice_date = datetime.now().date()
    due_date = invoice_date + timedelta(days=customer["terms_days"] or 0)
    invoice_no = next_number(conn, "INV", "receivables", "invoice_no")
    conn.execute(
        """INSERT INTO receivables (sale_id, customer_id, invoice_no, invoice_date, due_date, amount, paid, balance, status)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (sale_id, customer["id"], invoice_no, str(invoice_date), str(due_date), billed_amount, 0, billed_amount, "CURRENT"),
    )
    conn.execute(
        "UPDATE commercial_deliveries SET status='BILLED', price_per_kg=?, billed_amount=?, sale_id=?, billed_by=? WHERE id=?",
        (price_per_kg, billed_amount, sale_id, u["id"], did),
    )
    return sale_id, sale_no, dr_no


@app.post("/api/commercial-deliveries/<int:did>/bill")
@roles_required("ADMIN", "MANAGER", "ACCOUNTING")
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
    customer = conn.execute("SELECT * FROM customers WHERE id=?", (delivery["customer_id"],)).fetchone()
    try:
        price_per_kg, _src = _commercial_rate(customer, product, d.get("price_per_kg"))
    except ValueError as e:
        conn.close()
        return jsonify(error=str(e)), 400
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
            """INSERT INTO sales (sale_no, location_id, user_id, customer_id, sale_type, subtotal, discount, total, paid, balance, notes,
                                  is_billing)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
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
@roles_or_module_required("ADMIN", "MANAGER", "DISPATCHER", "ACCOUNTING", module="commercial_deliveries")
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


# ---------------------------------------------------------------- Generate Billing from a POS delivery sale
# A Commercial account's 50kg cylinders on a POS credit sale are delivered
# only (bill_later lines, no gas charge). Accounting then clicks "Generate
# Billing" beside that sale, types the residual of the cylinders pulled out,
# and the customer's rate is applied automatically. The billing is a
# receivable only — it is NOT a new sale (see _bill_delivery_as_credit_sale).

COMMERCIAL_BILLING_ROLES = ("ADMIN", "ACCOUNTING")


def _sale_billing_context(conn, sale_id):
    sale = conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    if not sale:
        raise LookupError("Sale not found")
    if sale["is_billing"]:
        raise ValueError("This is already a billing, not a delivery")
    lines = conn.execute(
        """SELECT si.*, p.name p_name, p.sku FROM sale_items si JOIN products p ON p.id=si.product_id
           WHERE si.sale_id=? AND COALESCE(si.bill_later,0)=1 AND si.qty > 0 ORDER BY si.id""", (sale_id,)).fetchall()
    customer = conn.execute("SELECT * FROM customers WHERE id=?", (sale["customer_id"],)).fetchone() \
        if sale["customer_id"] else None
    billing = conn.execute(
        """SELECT cd.id, cd.delivery_no, cd.status, cd.billed_amount, cd.total_residual_kg, cd.price_per_kg, cd.sale_id,
                  r.invoice_no, r.paid invoice_paid
           FROM commercial_deliveries cd LEFT JOIN receivables r ON r.sale_id=cd.sale_id
           WHERE cd.source_sale_id=? AND cd.status!='CANCELLED' ORDER BY cd.id DESC LIMIT 1""", (sale_id,)).fetchone()
    return sale, lines, customer, billing


@app.get("/api/sales/<int:sale_id>/commercial-billing")
@roles_required(*COMMERCIAL_BILLING_ROLES)
def sale_commercial_billing_preview(sale_id):
    conn = get_db()
    try:
        sale, lines, customer, billing = _sale_billing_context(conn, sale_id)
    except LookupError as e:
        conn.close(); return jsonify(error=str(e)), 404
    except ValueError as e:
        conn.close(); return jsonify(error=str(e)), 400
    out_lines, delivered_qty, delivered_kg = [], 0, 0.0
    for ln in lines:
        size = product_kg_size(ln["p_name"], ln["sku"]) or 50.0
        out_lines.append({"product_id": ln["product_id"], "product_name": ln["product_name"], "qty": ln["qty"],
                          "kg_per_cylinder": size, "kg": round(ln["qty"] * size, 2)})
        delivered_qty += ln["qty"]; delivered_kg += ln["qty"] * size
    rate, rate_source = (None, None)
    if customer and lines:
        product = conn.execute("SELECT * FROM products WHERE id=?", (lines[0]["product_id"],)).fetchone()
        rate, rate_source = _commercial_rate(customer, product)
    # Billings made in 50kg Commercial Billing before this button existed are
    # not linked to their sale — show them so nothing is billed twice.
    possible_dupes = []
    if customer and not billing:
        possible_dupes = [dict(r) for r in conn.execute(
            """SELECT id, delivery_no, delivery_date, dr_ref, delivered_qty, billed_amount FROM commercial_deliveries
               WHERE customer_id=? AND source_sale_id IS NULL AND status='BILLED' AND billing_type='DR'
                     AND date(delivery_date) >= date(?) ORDER BY id LIMIT 10""", (customer["id"], sale["ts"])).fetchall()]
    conn.close()
    return jsonify(
        sale={"id": sale["id"], "sale_no": sale["sale_no"], "dr_no": sale["dr_no"], "ts": sale["ts"],
              "status": sale["status"], "sale_type": sale["sale_type"]},
        customer={"id": customer["id"], "name": customer["name"], "account_no": customer["account_no"],
                  "category": customer["category"], "commercial_rate": customer["commercial_rate"]} if customer else None,
        lines=out_lines, delivered_qty=delivered_qty, delivered_kg=round(delivered_kg, 2),
        rate=rate, rate_source=rate_source, billing=dict(billing) if billing else None,
        possible_duplicates=possible_dupes)


@app.post("/api/sales/<int:sale_id>/commercial-billing")
@roles_required(*COMMERCIAL_BILLING_ROLES)
def sale_generate_commercial_billing(sale_id):
    """Accounting's Generate Billing: delivered kg (from the sale's 50kg
    lines) less the residual typed in, at the customer's rate."""
    d = request.get_json(force=True) or {}
    conn = get_db()
    try:
        sale, lines, customer, billing = _sale_billing_context(conn, sale_id)
        if sale["status"] != "COMPLETED":
            raise ValueError(f"This sale is {sale['status']} — it can't be billed")
        if not customer:
            raise ValueError("This sale has no customer account")
        if not lines:
            raise ValueError("This sale has no 50kg cylinders delivered for billing")
        if billing:
            raise ValueError(f"Already billed in {billing['delivery_no']}")
    except LookupError as e:
        conn.close(); return jsonify(error=str(e)), 404
    except ValueError as e:
        conn.close(); return jsonify(error=str(e)), 400
    delivered_qty = sum(ln["qty"] for ln in lines)
    delivered_kg = sum(ln["qty"] * (product_kg_size(ln["p_name"], ln["sku"]) or 50.0) for ln in lines)
    location_id = sale["location_id"]
    conn.close()
    body = {
        "customer_id": customer["id"], "product_id": lines[0]["product_id"],
        "delivered_qty": delivered_qty,
        # one size per billing record (mixed sizes are averaged so the kg stays exact)
        "kg_per_cylinder": delivered_kg / delivered_qty,
        "pullouts": d.get("pullouts") or [], "notes": d.get("notes", ""), "dr_ref": d.get("dr_ref") or "",
        "total_residual_kg": d.get("total_residual_kg"),
    }
    return _create_dr_billing(body, location_id, source_sale=sale)


@app.post("/api/commercial-deliveries/<int:did>/void-billing")
@roles_required(*COMMERCIAL_BILLING_ROLES)
def void_commercial_billing(did):
    """Undo a billing made by mistake (wrong residual or rate) while nothing
    has been paid on it: the invoice is voided and, for one generated from a
    POS sale, Generate Billing can be used again."""
    conn = get_db()
    cd = conn.execute("SELECT * FROM commercial_deliveries WHERE id=?", (did,)).fetchone()
    if not cd:
        conn.close(); return jsonify(error="Not found"), 404
    if cd["status"] != "BILLED" or not cd["sale_id"]:
        conn.close(); return jsonify(error="Only a billed record can be voided"), 400
    r = conn.execute("SELECT * FROM receivables WHERE sale_id=?", (cd["sale_id"],)).fetchone()
    if r and (r["paid"] or 0) > 0.009:
        conn.close()
        return jsonify(error=f"Invoice {r['invoice_no']} already has {money_fmt(r['paid'])} paid — it can't be voided"), 400
    conn.execute("UPDATE sales SET status='VOIDED' WHERE id=?", (cd["sale_id"],))
    conn.execute("UPDATE receivables SET status='PAID', balance=0 WHERE sale_id=?", (cd["sale_id"],))
    conn.execute("UPDATE commercial_deliveries SET status='CANCELLED' WHERE id=?", (did,))
    audit(conn, "VOID", "COMMERCIAL_DELIVERIES", did,
          f"{cd['delivery_no']} billing voided ({money_fmt(cd['billed_amount'] or 0)})"
          + (f", invoice {r['invoice_no']}" if r else ""))
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.put("/api/customers/<int:cid>/commercial-rate")
@roles_required(*COMMERCIAL_BILLING_ROLES)
def set_customer_commercial_rate(cid):
    """Accounting sets a Commercial account's rate per kg; every billing for
    that account then uses it automatically. Blank clears it."""
    d = request.get_json(force=True) or {}
    conn = get_db()
    cust = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    if not cust:
        conn.close(); return jsonify(error="Not found"), 404
    if not is_commercial_account(cust):
        conn.close(); return jsonify(error="A rate per kg is only for Commercial accounts"), 400
    try:
        rate = _num(d.get("rate"), "Rate per kg", allow_blank=True)
    except ValueError as e:
        conn.close(); return jsonify(error=str(e)), 400
    if rate is not None and rate <= 0:
        conn.close(); return jsonify(error="The rate per kg must be more than zero"), 400
    conn.execute("UPDATE customers SET commercial_rate=? WHERE id=?", (rate, cid))
    record_rate_change(conn, cust["commercial_rate"], rate, customer_id=cid)
    audit(conn, "UPDATE", "CUSTOMERS", cid,
          f"{cust['name']}: commercial rate {cust['commercial_rate'] or '-'} -> {rate if rate is not None else '-'} per kg")
    conn.commit()
    conn.close()
    return jsonify(ok=True, commercial_rate=rate)


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
              LEFT JOIN sales s ON s.user_id=u.id AND s.status='COMPLETED' AND s.is_opening_balance=0 AND s.is_billing=0"""
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


@app.get("/portal")
def customer_portal_page():
    return send_from_directory("static", "portal.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
