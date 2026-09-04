import os
import functools
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

from db import get_db, init_db

app = Flask(__name__, static_folder="static", static_url_path="")
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-change-me")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

init_db()

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
            "INSERT INTO products (sku, name, category, unit, price, cost, min_stock) VALUES (?,?,?,?,?,?,?)",
            (d["sku"], d["name"], d.get("category", ""), d.get("unit", "cylinder"),
             float(d["price"]), float(d.get("cost", 0)), int(d.get("min_stock", 0))),
        )
    except Exception as e:
        conn.close()
        return jsonify(error=f"Could not create product: {e}"), 400
    audit(conn, "CREATE", "PRODUCTS", cur.lastrowid, d["name"])
    conn.commit()
    conn.close()
    return jsonify(id=cur.lastrowid), 201


@app.put("/api/products/<int:pid>")
@roles_required("ADMIN")
def update_product(pid):
    d = request.get_json(force=True)
    conn = get_db()
    conn.execute(
        "UPDATE products SET name=?, category=?, unit=?, price=?, cost=?, min_stock=? WHERE id=?",
        (d["name"], d.get("category", ""), d.get("unit", "cylinder"), float(d["price"]),
         float(d.get("cost", 0)), int(d.get("min_stock", 0)), pid),
    )
    audit(conn, "UPDATE", "PRODUCTS", pid, f"price={d['price']}")
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


@app.post("/api/customers")
@login_required
def create_customer():
    d = request.get_json(force=True)
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) c FROM customers").fetchone()["c"] + 1
    account_no = f"ACC-{n:05d}"
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
    conn = get_db()
    sql = "SELECT m.*, p.name product_name, l.name location_name FROM inventory_movements m " \
          "JOIN products p ON p.id=m.product_id JOIN locations l ON l.id=m.location_id WHERE 1=1"
    params = []
    if product_id:
        sql += " AND m.product_id=?"; params.append(product_id)
    if location_id:
        sql += " AND m.location_id=?"; params.append(location_id)
    sql += " ORDER BY m.ts DESC LIMIT 200"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


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
            price = float(prod["price"])  # server-side price, never trust frontend
            line_total = round(price * qty, 2)
            subtotal += line_total
            resolved_items.append((prod, qty, price, line_total))

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

        for prod, qty, price, line_total in resolved_items:
            conn.execute(
                "INSERT INTO sale_items (sale_id, product_id, product_name, qty, price, subtotal) VALUES (?,?,?,?,?,?)",
                (sale_id, prod["id"], prod["name"], qty, price, line_total),
            )
            adjust_inventory(conn, location_id, prod["id"], -qty, "SALE", sale_no,
                              notes=f"Sold via {sale_no}")

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
    conn = get_db()
    sql = """SELECT s.sale_no, s.ts, l.name location_name, u.name cashier, c.name customer,
                    s.sale_type, s.subtotal, s.discount, s.total, s.paid, s.balance, s.status
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
    return jsonify([dict(r) for r in rows])


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


# ---------------------------------------------------------------- users (admin)

@app.get("/api/users")
@roles_required("ADMIN")
def list_users():
    conn = get_db()
    rows = conn.execute(
        "SELECT u.id, u.username, u.name, u.role, u.active, l.name location_name FROM users u "
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


@app.post("/api/users/<int:uid>/disable")
@roles_required("ADMIN")
def disable_user(uid):
    conn = get_db()
    conn.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
    audit(conn, "DISABLE", "USERS", uid)
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------- audit log

@app.get("/api/audit-logs")
@roles_required("ADMIN")
def get_audit_logs():
    conn = get_db()
    rows = conn.execute("SELECT * FROM audit_logs ORDER BY ts DESC LIMIT 300").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------- static frontend

@app.get("/")
def index():
    return send_from_directory("static", "index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
