// ---------------------------------------------------------------- core
const state = { user: null, locations: [], products: [], view: "dashboard", cart: [] };

function toast(msg, type = "") {
  const el = document.createElement("div");
  el.className = "toast " + type;
  el.textContent = msg;
  document.getElementById("toastWrap").appendChild(el);
  setTimeout(() => el.remove(), 3800);
}

async function api(path, opts = {}) {
  const res = await fetch("/api" + path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) {
    const msg = data.error || "Request failed";
    toast(msg, "error");
    throw new Error(msg);
  }
  return data;
}

function money(n) {
  n = Number(n || 0);
  return "\u20b1" + n.toLocaleString("en-PH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function esc(s) { return (s ?? "").toString().replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

// ---------------------------------------------------------------- login
document.getElementById("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = document.getElementById("loginUsername").value.trim();
  const password = document.getElementById("loginPassword").value;
  const errEl = document.getElementById("loginError");
  errEl.classList.add("hidden");
  try {
    const data = await api("/login", { method: "POST", body: { username, password } });
    state.user = data.user;
    await boot();
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  }
});

document.getElementById("logoutBtn").addEventListener("click", async () => {
  await api("/logout", { method: "POST" });
  location.reload();
});

// ---------------------------------------------------------------- nav config
const NAV = [
  { id: "dashboard", label: "Dashboard", icon: "\u{1F3E0}", roles: ["ADMIN", "ATTENDANT", "DISPATCHER"] },
  { id: "pos", label: "POS", icon: "\u{1F6D2}", roles: ["ADMIN", "ATTENDANT"] },
  { id: "sales", label: "Sales", icon: "\u{1F9FE}", roles: ["ADMIN", "ATTENDANT"] },
  { id: "customers", label: "Customers", icon: "\u{1F465}", roles: ["ADMIN", "ATTENDANT"] },
  { id: "receivables", label: "Receivables", icon: "\u{1F4B0}", roles: ["ADMIN", "ATTENDANT"] },
  { id: "inventory", label: "Inventory", icon: "\u{1F4E6}", roles: ["ADMIN", "ATTENDANT", "DISPATCHER"] },
  { id: "inventory_ledger", label: "Inv. Ledger", icon: "\u{1F4D2}", roles: ["ADMIN"] },
  { id: "transfers", label: "Dispatch", icon: "\u{1F69A}", roles: ["ADMIN", "DISPATCHER"] },
  { id: "reports", label: "Reports", icon: "\u{1F4CA}", roles: ["ADMIN"] },
  { id: "users", label: "Users", icon: "\u{1F464}", roles: ["ADMIN"] },
  { id: "products", label: "Products", icon: "\u{1F3F7}", roles: ["ADMIN"] },
  { id: "locations", label: "Locations", icon: "\u{1F3E2}", roles: ["ADMIN"] },
  { id: "audit", label: "Audit Log", icon: "\u{1F4DC}", roles: ["ADMIN"] },
];

function buildNav() {
  const items = NAV.filter(n => n.roles.includes(state.user.role));
  document.getElementById("sideNav").innerHTML = items.map(n =>
    `<button class="nav-item" data-view="${n.id}">${n.icon}&nbsp;&nbsp;${n.label}</button>`).join("");
  document.getElementById("bottomNav").innerHTML = items.map(n =>
    `<button data-view="${n.id}"><span>${n.icon}</span><span>${n.label}</span></button>`).join("");
  document.querySelectorAll("[data-view]").forEach(btn =>
    btn.addEventListener("click", () => switchView(btn.dataset.view)));
}

function switchView(view) {
  state.view = view;
  document.querySelectorAll("[data-view]").forEach(b =>
    b.classList.toggle("active", b.dataset.view === view));
  const navItem = NAV.find(n => n.id === view);
  document.getElementById("viewTitle").textContent = navItem ? navItem.label : view;
  render();
}

// ---------------------------------------------------------------- boot
async function boot() {
  document.getElementById("loginScreen").classList.add("hidden");
  document.getElementById("appShell").classList.remove("hidden");
  document.getElementById("sideUserName").textContent = state.user.name;
  document.getElementById("sideUserRole").textContent = state.user.role;
  const locs = await api("/locations");
  state.locations = locs;
  const myLoc = locs.find(l => l.id === state.user.location_id);
  document.getElementById("topbarLoc").textContent = state.user.role === "ADMIN"
    ? "All Locations" : (myLoc ? myLoc.name : "");
  state.products = await api("/products");
  buildNav();
  switchView("dashboard");
}

(async function initSession() {
  const data = await api("/me");
  if (data.user) { state.user = data.user; boot(); }
})();

// ---------------------------------------------------------------- render router
function render() {
  const c = document.getElementById("content");
  c.innerHTML = "";
  const views = {
    dashboard: renderDashboard, pos: renderPOS, sales: renderSales,
    customers: renderCustomers, receivables: renderReceivables,
    inventory: renderInventory, inventory_ledger: renderInventoryLedger, transfers: renderTransfers, reports: renderReports,
    users: renderUsers, products: renderProducts, locations: renderLocations,
    audit: renderAudit,
  };
  (views[state.view] || renderDashboard)(c);
}

// ---------------------------------------------------------------- modal helper
function openModal(title, bodyHtml, onMount) {
  const root = document.getElementById("modalRoot");
  root.innerHTML = `<div class="modal-backdrop"><div class="modal">
    <h3>${title}</h3>
    <div id="modalBody">${bodyHtml}</div>
  </div></div>`;
  root.querySelector(".modal-backdrop").addEventListener("click", (e) => {
    if (e.target.classList.contains("modal-backdrop")) closeModal();
  });
  if (onMount) onMount(root.querySelector("#modalBody"));
}
function closeModal() { document.getElementById("modalRoot").innerHTML = ""; }

// ================================================================== DASHBOARD
async function renderDashboard(c) {
  c.innerHTML = "Loading...";
  const d = await api("/dashboard");
  c.innerHTML = `
    <div class="grid grid-4">
      <div class="card stat-card green"><div class="label">Today's Sales</div><div class="value">${money(d.today_sales_total)}</div></div>
      <div class="card stat-card"><div class="label">Today's Transactions</div><div class="value">${d.today_sales_count}</div></div>
      <div class="card stat-card"><div class="label">Outstanding Receivables</div><div class="value">${money(d.outstanding_receivables)}</div></div>
      <div class="card stat-card danger"><div class="label">Overdue Receivables</div><div class="value">${money(d.overdue_receivables)}</div></div>
    </div>
    <div class="grid grid-2" style="margin-top:16px">
      <div class="card">
        <h3 style="margin-top:0">Low Stock Alerts</h3>
        ${d.low_stock.length ? `<div class="table-wrap"><table><thead><tr><th>Product</th><th>Location</th><th>Qty</th><th>Min</th></tr></thead><tbody>
          ${d.low_stock.map(r => `<tr><td>${esc(r.name)}</td><td>${esc(r.location_name)}</td><td>${r.quantity}</td><td>${r.min_stock}</td></tr>`).join("")}
        </tbody></table></div>` : `<p style="color:var(--muted)">No low-stock items.</p>`}
      </div>
      <div class="card">
        <h3 style="margin-top:0">Payments by Method (all time)</h3>
        ${d.payments_by_method.length ? `<div class="table-wrap"><table><thead><tr><th>Method</th><th>Total</th></tr></thead><tbody>
          ${d.payments_by_method.map(r => `<tr><td>${esc(r.method)}</td><td>${money(r.total)}</td></tr>`).join("")}
        </tbody></table></div>` : `<p style="color:var(--muted)">No payments yet.</p>`}
      </div>
    </div>`;
}

// ================================================================== POS
async function renderPOS(c) {
  state.cart = [];
  state.products = await api("/products");
  const locationId = state.user.role === "ADMIN" ? (state.locations[0] && state.locations[0].id) : state.user.location_id;
  const inv = await api(`/inventory?location_id=${locationId}`);
  const stockMap = {};
  inv.forEach(r => stockMap[r.product_id] = r.quantity);

  c.innerHTML = `
    <div class="pos-grid">
      <div>
        <input type="text" id="posSearch" placeholder="Search product name or SKU..." style="width:100%;margin-bottom:12px">
        <div class="product-grid" id="productGrid"></div>
      </div>
      <div class="card" id="cartPanel"></div>
    </div>`;

  function drawProducts(filter = "") {
    const grid = document.getElementById("productGrid");
    const f = filter.toLowerCase();
    const items = state.products.filter(p => p.name.toLowerCase().includes(f) || p.sku.toLowerCase().includes(f));
    grid.innerHTML = items.map(p => {
      const stock = stockMap[p.id] ?? 0;
      const low = stock <= 0;
      return `<div class="product-tile" data-pid="${p.id}">
        <div class="pname">${esc(p.name)}</div>
        <div class="pprice">${money(p.price)}</div>
        <div class="pstock ${low ? 'low' : ''}">${stock} in stock</div>
      </div>`;
    }).join("") || `<p style="color:var(--muted)">No products found.</p>`;
    grid.querySelectorAll(".product-tile").forEach(t => t.addEventListener("click", () => {
      addToCart(parseInt(t.dataset.pid), stockMap);
    }));
  }
  document.getElementById("posSearch").addEventListener("input", e => drawProducts(e.target.value));
  drawProducts();
  drawCart(locationId);

  function addToCart(pid, stockMap) {
    const prod = state.products.find(p => p.id === pid);
    const stock = stockMap[pid] ?? 0;
    const existing = state.cart.find(i => i.product_id === pid);
    const currentQty = existing ? existing.qty : 0;
    if (currentQty + 1 > stock) { toast(`Only ${stock} in stock for ${prod.name}`, "error"); return; }
    if (existing) existing.qty += 1;
    else state.cart.push({ product_id: pid, name: prod.name, price: prod.price, qty: 1 });
    drawCart(locationId);
  }

  window.__posRemove = (pid) => { state.cart = state.cart.filter(i => i.product_id !== pid); drawCart(locationId); };
  window.__posQty = (pid, val) => {
    const item = state.cart.find(i => i.product_id === pid);
    const stock = stockMap[pid] ?? 0;
    let q = parseInt(val) || 0;
    if (q > stock) { toast(`Only ${stock} in stock`, "error"); q = stock; }
    if (q <= 0) { state.cart = state.cart.filter(i => i.product_id !== pid); }
    else item.qty = q;
    drawCart(locationId);
  };
}

function cartTotals() {
  const subtotal = state.cart.reduce((s, i) => s + i.price * i.qty, 0);
  return { subtotal };
}

function drawCart(locationId) {
  const panel = document.getElementById("cartPanel");
  const { subtotal } = cartTotals();
  panel.innerHTML = `
    <h3 style="margin-top:0">Cart</h3>
    <div id="cartLines">${state.cart.length ? state.cart.map(i => `
      <div class="cart-line">
        <div class="cname">${esc(i.name)}<br><span style="color:var(--muted);font-size:12px">${money(i.price)} each</span></div>
        <input type="number" min="0" value="${i.qty}" onchange="__posQty(${i.product_id}, this.value)">
        <div style="width:78px;text-align:right;font-weight:600">${money(i.price * i.qty)}</div>
        <button class="btn small danger" onclick="__posRemove(${i.product_id})">&times;</button>
      </div>`).join("") : `<p style="color:var(--muted)">Cart is empty. Tap a product to add it.</p>`}
    </div>
    <div style="margin-top:10px">
      <label style="font-size:12px;color:var(--muted)">Discount (₱)</label>
      <input type="number" id="discountInput" value="0" min="0" style="width:100%">
    </div>
    <div style="margin-top:10px">
      <div class="cart-total-row"><span>Subtotal</span><span>${money(subtotal)}</span></div>
      <div class="cart-total-row grand"><span>Total</span><span id="grandTotal">${money(subtotal)}</span></div>
    </div>
    <button class="btn green" style="width:100%;margin-top:14px" id="checkoutBtn" ${state.cart.length ? "" : "disabled"}>Checkout</button>
  `;
  const discountInput = document.getElementById("discountInput");
  discountInput.addEventListener("input", () => {
    const disc = parseFloat(discountInput.value) || 0;
    document.getElementById("grandTotal").textContent = money(Math.max(0, subtotal - disc));
  });
  document.getElementById("checkoutBtn").addEventListener("click", () => openCheckout(locationId, subtotal));
}

async function openCheckout(locationId, subtotal) {
  const discount = parseFloat(document.getElementById("discountInput").value) || 0;
  const total = Math.max(0, subtotal - discount);
  const customers = await api("/customers");
  const custOptions = `<option value="">-- Walk-in / no account --</option>` +
    customers.map(c => `<option value="${c.id}">${esc(c.name)} (${c.account_no}) - ${esc(c.category)}</option>`).join("");

  openModal("Checkout", `
    <div class="field"><label>Customer</label><select id="custSelect">${custOptions}</select></div>
    <div class="field"><label>Sale Type</label>
      <select id="saleType"><option value="CASH">Cash Sale</option><option value="CREDIT">Credit / Terms Sale</option></select>
    </div>
    <div class="field"><label>Total Due</label><div style="font-size:20px;font-weight:800">${money(total)}</div></div>
    <div class="field">
      <label>Payments (split payments supported)</label>
      <div id="payRows"></div>
      <button class="btn small" id="addPayRow" type="button">+ Add Payment</button>
    </div>
    <div class="field"><b>Paid so far: </b><span id="paidSoFar">${money(0)}</span> &nbsp; <b>Balance: </b><span id="balSoFar">${money(total)}</span></div>
    <div class="modal-actions">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn green" id="submitSaleBtn">Complete Sale</button>
    </div>
  `, () => {
    const payRows = document.getElementById("payRows");
    function addRow(amount = "") {
      const row = document.createElement("div");
      row.className = "pay-row";
      row.innerHTML = `
        <select class="payMethod">
          <option>Cash</option><option>GCash</option><option>Bank Transfer</option><option>Maya</option><option>Cheque</option><option>Other</option>
        </select>
        <input type="number" class="payAmount" placeholder="Amount" value="${amount}" min="0">
        <input type="text" class="payRef" placeholder="Ref #" style="width:90px">
        <button class="btn small danger" type="button">&times;</button>`;
      row.querySelector("button").addEventListener("click", () => { row.remove(); recalc(); });
      row.querySelector(".payAmount").addEventListener("input", recalc);
      payRows.appendChild(row);
      recalc();
    }
    function recalc() {
      let paid = 0;
      payRows.querySelectorAll(".payAmount").forEach(i => paid += parseFloat(i.value) || 0);
      document.getElementById("paidSoFar").textContent = money(paid);
      document.getElementById("balSoFar").textContent = money(Math.max(0, total - paid));
    }
    document.getElementById("addPayRow").addEventListener("click", () => addRow());
    document.getElementById("saleType").addEventListener("change", (e) => {
      if (e.target.value === "CASH" && payRows.children.length === 0) addRow(total);
    });
    addRow(total); // default: single cash payment for full total

    document.getElementById("submitSaleBtn").addEventListener("click", async () => {
      const payments = [...payRows.querySelectorAll(".pay-row")].map(r => ({
        method: r.querySelector(".payMethod").value,
        amount: parseFloat(r.querySelector(".payAmount").value) || 0,
        reference: r.querySelector(".payRef").value,
      })).filter(p => p.amount > 0);

      const body = {
        location_id: locationId,
        customer_id: document.getElementById("custSelect").value || null,
        sale_type: document.getElementById("saleType").value,
        discount,
        items: state.cart.map(i => ({ product_id: i.product_id, qty: i.qty })),
        payments,
      };
      try {
        const result = await api("/sales", { method: "POST", body });
        closeModal();
        toast(`Sale ${result.sale.sale_no} completed`, "success");
        state.cart = [];
        printReceipt(result);
        switchView("pos");
      } catch (e) { /* toast already shown */ }
    });
  });
}

function printReceipt(result) {
  const { sale, items, payments } = result;
  const html = `
    <div style="max-width:340px;font-family:monospace;font-size:12.5px">
      <div style="text-align:center">
        <img src="/images/logo.png" alt="GUFC" style="width:56px;height:56px;object-fit:contain;margin-bottom:4px">
        <br><strong style="font-size:15px">GREAT UNLI FOOD CORP.</strong><br>
        Solane LPG Distributor<br>${esc(sale.location_name)}<br>
        --------------------------------
      </div>
      <div>Txn: ${sale.sale_no}<br>Date: ${sale.ts}<br>Cashier: ${esc(sale.cashier_name)}<br>
      Customer: ${esc(sale.customer_name || "Walk-in")} ${sale.account_no ? "(" + sale.account_no + ")" : ""}</div>
      <div>--------------------------------</div>
      ${items.map(i => `${esc(i.product_name)}<br>  ${i.qty} x ${money(i.price)} = ${money(i.subtotal)}`).join("<br>")}
      <div>--------------------------------</div>
      <div>Subtotal: ${money(sale.subtotal)}<br>Discount: ${money(sale.discount)}<br><b>Total: ${money(sale.total)}</b></div>
      <div>--------------------------------</div>
      ${payments.map(p => `${esc(p.method)}: ${money(p.amount)}`).join("<br>")}
      <div>Paid: ${money(sale.paid)}<br>Balance: ${money(sale.balance)}</div>
      <div style="text-align:center;margin-top:8px">Thank you for your purchase!</div>
    </div>`;
  document.getElementById("receiptArea").innerHTML = html;
  setTimeout(() => window.print(), 200);
}

// ================================================================== SALES
async function renderSales(c) {
  c.innerHTML = "Loading...";
  const sales = await api("/sales");
  c.innerHTML = `<div class="table-wrap"><table><thead><tr>
    <th>Sale #</th><th>Date</th><th>Location</th><th>Cashier</th><th>Customer</th><th>Type</th><th>Total</th><th>Balance</th><th>Status</th><th></th>
  </tr></thead><tbody>
    ${sales.map(s => `<tr>
      <td>${s.sale_no}</td><td>${s.ts}</td><td>${esc(s.location_name)}</td><td>${esc(s.cashier_name)}</td>
      <td>${esc(s.customer_name || "Walk-in")}</td>
      <td><span class="badge ${s.sale_type === 'CASH' ? 'green' : 'warn'}">${s.sale_type}</span></td>
      <td>${money(s.total)}</td><td>${money(s.balance)}</td>
      <td><span class="badge ${s.status === 'VOIDED' ? 'danger' : 'blue'}">${s.status}</span></td>
      <td style="display:flex;gap:6px">
        <button class="btn small" onclick="viewSale(${s.id})">View</button>
        ${state.user.role === 'ADMIN' && s.status !== 'VOIDED' ? `<button class="btn small danger" onclick="voidSale(${s.id})">Void</button>` : ""}
      </td>
    </tr>`).join("")}
  </tbody></table></div>`;
}
window.voidSale = async (id) => {
  if (!confirm("Void this sale? This will reverse the inventory and cannot be undone.")) return;
  try { await api(`/sales/${id}/void`, { method: "POST" }); toast("Sale voided", "success"); renderSales(document.getElementById("content")); }
  catch (e) {}
};
window.viewSale = async (id) => {
  const result = await api(`/sales/${id}`);
  const { sale, items, payments } = result;
  openModal(`Sale ${sale.sale_no}`, `
    <div class="field"><b>Date:</b> ${sale.ts} &nbsp; <b>Location:</b> ${esc(sale.location_name)}</div>
    <div class="field"><b>Cashier:</b> ${esc(sale.cashier_name)} &nbsp; <b>Customer:</b> ${esc(sale.customer_name || "Walk-in")}</div>
    <div class="field"><b>Status:</b> <span class="badge ${sale.status === 'VOIDED' ? 'danger' : 'blue'}">${sale.status}</span></div>
    <div class="table-wrap"><table><thead><tr><th>Item</th><th>Qty</th><th>Price</th><th>Subtotal</th></tr></thead><tbody>
      ${items.map(i => `<tr><td>${esc(i.product_name)}</td><td>${i.qty}</td><td>${money(i.price)}</td><td>${money(i.subtotal)}</td></tr>`).join("")}
    </tbody></table></div>
    <div style="margin-top:10px">
      <div class="cart-total-row"><span>Subtotal</span><span>${money(sale.subtotal)}</span></div>
      <div class="cart-total-row"><span>Discount</span><span>${money(sale.discount)}</span></div>
      <div class="cart-total-row grand"><span>Total</span><span>${money(sale.total)}</span></div>
    </div>
    <div class="field" style="margin-top:10px"><b>Payments:</b><br>
      ${payments.length ? payments.map(p => `${esc(p.method)}: ${money(p.amount)} ${p.reference ? '(' + esc(p.reference) + ')' : ''}`).join("<br>") : "None recorded"}
    </div>
    <div class="field"><b>Paid:</b> ${money(sale.paid)} &nbsp; <b>Balance:</b> ${money(sale.balance)}</div>
    <div class="modal-actions">
      <button class="btn" onclick="closeModal()">Close</button>
      <button class="btn green" id="reprintBtn">Reprint Receipt</button>
    </div>
  `, () => {
    document.getElementById("reprintBtn").addEventListener("click", () => printReceipt(result));
  });
};

// ================================================================== CUSTOMERS
async function renderCustomers(c) {
  c.innerHTML = `<div style="margin-bottom:12px;display:flex;gap:8px">
    <input type="text" id="custSearch" placeholder="Search customers..." style="flex:1">
    <button class="btn green" id="newCustBtn">+ New Customer</button>
  </div><div id="custTable"></div>`;
  async function load(q = "") {
    const list = await api("/customers?q=" + encodeURIComponent(q));
    document.getElementById("custTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Account #</th><th>Name</th><th>Category</th><th>Phone</th><th>Terms</th><th>Credit Limit</th><th></th>
    </tr></thead><tbody>
      ${list.map(cu => `<tr><td>${cu.account_no}</td><td>${esc(cu.name)}</td>
        <td><span class="badge blue">${esc(cu.category)}</span></td><td>${esc(cu.phone || "")}</td>
        <td>${cu.terms_days} days</td><td>${money(cu.credit_limit)}</td>
        <td><button class="btn small" onclick="openCustomerLedger(${cu.id})">Ledger / SOA</button></td></tr>`).join("")}
    </tbody></table></div>`;
  }
  document.getElementById("custSearch").addEventListener("input", e => load(e.target.value));
  document.getElementById("newCustBtn").addEventListener("click", () => {
    openModal("New Customer", `
      <div class="field"><label>Name</label><input id="cName"></div>
      <div class="field"><label>Business Name</label><input id="cBiz"></div>
      <div class="field"><label>Phone</label><input id="cPhone"></div>
      <div class="field"><label>Category</label>
        <select id="cCat"><option>Solus</option><option>ERTO</option><option>MRTO</option><option>Commercial</option><option>DOMCOM</option><option>ETC</option></select></div>
      <div class="field"><label>Payment Terms (days, 0 = cash only)</label><input type="number" id="cTerms" value="0"></div>
      <div class="field"><label>Credit Limit (₱)</label><input type="number" id="cLimit" value="0"></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="saveCustBtn">Save</button></div>
    `, () => {
      document.getElementById("saveCustBtn").addEventListener("click", async () => {
        const name = document.getElementById("cName").value.trim();
        if (!name) { toast("Name is required", "error"); return; }
        try {
          await api("/customers", { method: "POST", body: {
            name, business_name: document.getElementById("cBiz").value,
            phone: document.getElementById("cPhone").value,
            category: document.getElementById("cCat").value,
            terms_days: document.getElementById("cTerms").value,
            credit_limit: document.getElementById("cLimit").value,
          }});
          closeModal(); toast("Customer created", "success"); load();
        } catch (e) {}
      });
    });
  });
  load();
}

window.openCustomerLedger = async (cid) => {
  async function loadLedger(from, to) {
    const qs = new URLSearchParams();
    if (from) qs.set("from", from);
    if (to) qs.set("to", to);
    const data = await api(`/customers/${cid}/ledger?` + qs.toString());
    const body = document.getElementById("modalBody");
    if (!body) return;
    body.querySelector("#ledgerContent").innerHTML = `
      <div class="cart-total-row"><span>Beginning Balance</span><span>${money(data.beginning_balance)}</span></div>
      <div class="table-wrap" style="margin:8px 0"><table><thead><tr><th>Date</th><th>Type</th><th>Reference</th><th>Charges</th><th>Payments</th><th>Balance</th></tr></thead><tbody>
        ${data.entries.length ? data.entries.map(e => `<tr><td>${e.date}</td>
          <td><span class="badge ${e.type === 'SALE' ? 'warn' : 'green'}">${e.type}</span></td><td>${esc(e.ref)}</td>
          <td>${e.debit ? money(e.debit) : ""}</td><td>${e.credit ? money(e.credit) : ""}</td><td>${money(e.balance)}</td></tr>`).join("")
          : `<tr><td colspan="6" style="color:var(--muted)">No activity in this range.</td></tr>`}
      </tbody></table></div>
      <div class="cart-total-row grand"><span>Ending Balance</span><span>${money(data.ending_balance)}</span></div>
      <div class="modal-actions">
        <a class="btn green" href="/api/customers/${cid}/statement/export${qs.toString() ? '?' + qs.toString() : ''}">Download SOA (PDF)</a>
      </div>`;
  }
  const cust = (await api("/customers?q=")).find(c => c.id === cid) || {};
  openModal(`Account Ledger — ${cust.name || ""} (${cust.account_no || ""})`, `
    <div style="display:flex;gap:8px;margin-bottom:10px;align-items:end">
      <div class="field" style="margin:0"><label>From</label><input type="date" id="ledgerFrom"></div>
      <div class="field" style="margin:0"><label>To</label><input type="date" id="ledgerTo"></div>
      <button class="btn" id="ledgerApply">Apply</button>
    </div>
    <div id="ledgerContent">Loading...</div>
  `, () => {
    document.getElementById("ledgerApply").addEventListener("click", () =>
      loadLedger(document.getElementById("ledgerFrom").value, document.getElementById("ledgerTo").value));
    loadLedger();
  });
};

// ================================================================== RECEIVABLES
async function renderReceivables(c) {
  c.innerHTML = "Loading...";
  const rows = await api("/receivables");
  const statusColor = s => ({ PAID: "green", CURRENT: "blue", "DUE SOON": "warn", OVERDUE: "danger", "PARTIALLY PAID": "warn" }[s] || "gray");
  c.innerHTML = `<div class="table-wrap"><table><thead><tr>
    <th>Invoice #</th><th>Customer</th><th>Sale #</th><th>Invoice Date</th><th>Due Date</th><th>Amount</th><th>Paid</th><th>Balance</th><th>Status</th><th></th>
  </tr></thead><tbody>
    ${rows.map(r => `<tr>
      <td>${r.invoice_no}</td><td>${esc(r.customer_name)} (${r.account_no})</td><td>${r.sale_no}</td>
      <td>${r.invoice_date}</td><td>${r.due_date}</td><td>${money(r.amount)}</td><td>${money(r.paid)}</td><td>${money(r.balance)}</td>
      <td><span class="badge ${statusColor(r.status)}">${r.status}</span></td>
      <td>${r.balance > 0.009 ? `<button class="btn small green" onclick="payReceivable(${r.id}, ${r.balance})">Record Payment</button>` : ""}</td>
    </tr>`).join("")}
  </tbody></table></div>`;
}
window.payReceivable = (id, balance) => {
  openModal("Record Payment", `
    <div class="field"><label>Outstanding Balance</label><div style="font-weight:700">${money(balance)}</div></div>
    <div class="field"><label>Payment Method</label>
      <select id="rMethod"><option>Cash</option><option>GCash</option><option>Bank Transfer</option><option>Maya</option><option>Cheque</option><option>Other</option></select></div>
    <div class="field"><label>Amount</label><input type="number" id="rAmount" value="${balance}" max="${balance}" min="0"></div>
    <div class="field"><label>Reference #</label><input id="rRef"></div>
    <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn green" id="rSaveBtn">Save Payment</button></div>
  `, () => {
    document.getElementById("rSaveBtn").addEventListener("click", async () => {
      try {
        await api(`/receivables/${id}/pay`, { method: "POST", body: {
          amount: parseFloat(document.getElementById("rAmount").value),
          method: document.getElementById("rMethod").value,
          reference: document.getElementById("rRef").value,
        }});
        closeModal(); toast("Payment recorded", "success"); renderReceivables(document.getElementById("content"));
      } catch (e) {}
    });
  });
};

// ================================================================== INVENTORY
async function renderInventory(c) {
  c.innerHTML = "Loading...";
  const isAdmin = state.user.role === "ADMIN";
  async function load() {
    const rows = await api("/inventory");
    c.innerHTML = `
      ${isAdmin ? `<div style="margin-bottom:12px;display:flex;gap:8px">
        <button class="btn green" id="adjustStockBtn">+ Adjust Stock</button>
        <a class="btn" href="/api/reports/inventory/export?format=xlsx">Export Excel</a>
        <a class="btn" href="/api/reports/inventory/export?format=pdf">Export PDF</a>
      </div>` : ""}
      <div class="table-wrap"><table><thead><tr>
        <th>Location</th><th>SKU</th><th>Product</th><th>Qty on Hand</th><th>Min Stock</th><th>Status</th>
      </tr></thead><tbody>
        ${rows.map(r => `<tr><td>${esc(r.location_name)}</td><td>${esc(r.sku)}</td><td>${esc(r.name)}</td>
          <td>${r.quantity}</td><td>${r.min_stock}</td>
          <td>${r.quantity <= r.min_stock ? '<span class="badge danger">LOW STOCK</span>' : '<span class="badge green">OK</span>'}</td></tr>`).join("")}
      </tbody></table></div>`;
    if (isAdmin) document.getElementById("adjustStockBtn").addEventListener("click", openAdjustStockModal);
  }
  function openAdjustStockModal() {
    const locOpts = state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
    const prodOpts = state.products.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
    openModal("Adjust Stock", `
      <div class="field"><label>Location</label><select id="aLoc">${locOpts}</select></div>
      <div class="field"><label>Product</label><select id="aProd">${prodOpts}</select></div>
      <div class="field"><label>Quantity Change</label><input type="number" id="aDelta" placeholder="e.g. 10 or -5" value="0">
        <div style="font-size:11.5px;color:var(--muted);margin-top:3px">Positive to add stock, negative to remove.</div></div>
      <div class="field"><label>Reason (required)</label><input type="text" id="aReason" placeholder="e.g. Physical count correction"></div>
      <div class="field"><label style="display:flex;align-items:center;gap:6px;font-weight:400">
        <input type="checkbox" id="aAllowNeg" style="width:auto"> Allow result to go below zero</label></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="aSaveBtn">Save Adjustment</button></div>
    `, () => {
      document.getElementById("aSaveBtn").addEventListener("click", async () => {
        const delta = parseInt(document.getElementById("aDelta").value, 10);
        const reason = document.getElementById("aReason").value.trim();
        if (!delta) { toast("Enter a non-zero quantity change", "error"); return; }
        if (!reason) { toast("A reason is required", "error"); return; }
        try {
          await api("/inventory/adjust", { method: "POST", body: {
            location_id: document.getElementById("aLoc").value,
            product_id: document.getElementById("aProd").value,
            delta, reason, allow_negative: document.getElementById("aAllowNeg").checked,
          }});
          closeModal(); toast("Inventory adjusted", "success"); load();
        } catch (e) {}
      });
    });
  }
  load();
}

// ================================================================== INVENTORY LEDGER (admin)
async function renderInventoryLedger(c) {
  c.innerHTML = `
    <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
      <select id="ledgerLoc"><option value="">All Locations</option>${state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("")}</select>
      <select id="ledgerProd"><option value="">All Products</option>${state.products.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select>
      <button class="btn" id="ledgerFilterBtn">Filter</button>
    </div>
    <div id="ledgerTable">Loading...</div>`;
  async function load() {
    const loc = document.getElementById("ledgerLoc").value;
    const prod = document.getElementById("ledgerProd").value;
    const qs = new URLSearchParams();
    if (loc) qs.set("location_id", loc);
    if (prod) qs.set("product_id", prod);
    const rows = await api("/inventory/movements?" + qs.toString());
    document.getElementById("ledgerTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Date</th><th>Location</th><th>Product</th><th>Change</th><th>Type</th><th>Reference</th><th>Notes</th>
    </tr></thead><tbody>
      ${rows.length ? rows.map(r => `<tr><td>${r.ts}</td><td>${esc(r.location_name)}</td><td>${esc(r.product_name)}</td>
        <td style="color:${r.qty_change >= 0 ? 'var(--green-dark)' : 'var(--danger)'};font-weight:700">${r.qty_change >= 0 ? '+' : ''}${r.qty_change}</td>
        <td><span class="badge blue">${esc(r.type)}</span></td><td>${esc(r.reference || "")}</td><td>${esc(r.notes || "")}</td></tr>`).join("")
        : `<tr><td colspan="7" style="color:var(--muted)">No movements found.</td></tr>`}
    </tbody></table></div>`;
  }
  document.getElementById("ledgerFilterBtn").addEventListener("click", load);
  load();
}

// ================================================================== TRANSFERS / DISPATCH
async function renderTransfers(c) {
  c.innerHTML = `<div style="margin-bottom:12px"><button class="btn green" id="newXferBtn">+ New Transfer / Dispatch</button></div><div id="xferTable">Loading...</div>`;
  async function load() {
    const rows = await api("/transfers");
    document.getElementById("xferTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Dispatch #</th><th>Date</th><th>From</th><th>To</th><th>Items</th><th>Status</th><th></th>
    </tr></thead><tbody>
      ${rows.map(t => `<tr><td>${t.transfer_no}</td><td>${t.ts}</td><td>${esc(t.from_name)}</td><td>${esc(t.to_name)}</td>
        <td>${t.items.map(i => `${esc(i.product_name)} x${i.qty}`).join(", ")}</td>
        <td><span class="badge ${t.status === 'RECEIVED' ? 'green' : t.status === 'CANCELLED' ? 'danger' : 'warn'}">${t.status}</span></td>
        <td>${t.status === 'RELEASED' ? `<button class="btn small green" onclick="receiveXfer(${t.id})">Mark Received</button>` : ""}</td>
      </tr>`).join("")}
    </tbody></table></div>`;
  }
  document.getElementById("newXferBtn").addEventListener("click", () => {
    const locOpts = state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
    const prodOpts = state.products.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
    openModal("New Transfer / Dispatch", `
      <div class="field"><label>From</label><select id="xFrom">${locOpts}</select></div>
      <div class="field"><label>To</label><select id="xTo">${locOpts}</select></div>
      <div class="field"><label>Product</label><select id="xProd">${prodOpts}</select></div>
      <div class="field"><label>Quantity</label><input type="number" id="xQty" value="1" min="1"></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="xSaveBtn">Create Transfer</button></div>
    `, () => {
      document.getElementById("xSaveBtn").addEventListener("click", async () => {
        const from = document.getElementById("xFrom").value, to = document.getElementById("xTo").value;
        if (from === to) { toast("Origin and destination must differ", "error"); return; }
        try {
          await api("/transfers", { method: "POST", body: {
            from_location: from, to_location: to,
            items: [{ product_id: document.getElementById("xProd").value, qty: document.getElementById("xQty").value }],
          }});
          closeModal(); toast("Transfer dispatched", "success"); load();
        } catch (e) {}
      });
    });
  });
  load();
  window.receiveXfer = async (id) => {
    try { await api(`/transfers/${id}/receive`, { method: "POST" }); toast("Transfer received; inventory updated", "success"); load(); }
    catch (e) {}
  };
}

// ================================================================== REPORTS
async function renderReports(c) {
  c.innerHTML = `
    <div class="grid grid-2" style="margin-bottom:16px">
      <div class="card">
        <h3 style="margin-top:0">Sales Report</h3>
        <div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap">
          <input type="date" id="repFrom"><input type="date" id="repTo">
          <button class="btn" id="runSalesRep">Run</button>
          <a class="btn small" id="salesXlsx" href="#">Export Excel</a>
          <a class="btn small" id="salesPdf" href="#">Export PDF</a>
        </div>
        <div id="salesRepOut" class="table-wrap"></div>
      </div>
      <div class="card">
        <h3 style="margin-top:0">Inventory Report</h3>
        <div style="margin-bottom:10px">
          <a class="btn small" href="/api/reports/inventory/export?format=xlsx">Export Excel</a>
          <a class="btn small" href="/api/reports/inventory/export?format=pdf">Export PDF</a>
        </div>
        <div id="invRepOut" class="table-wrap"></div>
      </div>
    </div>
    <div class="card">
      <h3 style="margin-top:0">Accounts Receivable Report</h3>
      <div style="margin-bottom:10px">
        <a class="btn small" href="/api/reports/receivables/export?format=xlsx">Export Excel</a>
        <a class="btn small" href="/api/reports/receivables/export?format=pdf">Export PDF</a>
      </div>
      <p style="color:var(--muted);font-size:13px;margin:0">Full detail available on the Receivables tab; use these buttons for a downloadable copy.</p>
    </div>`;

  function updateExportLinks() {
    const from = document.getElementById("repFrom").value, to = document.getElementById("repTo").value;
    const qs = new URLSearchParams(); if (from) qs.set("from", from); if (to) qs.set("to", to);
    document.getElementById("salesXlsx").href = "/api/reports/sales/export?format=xlsx&" + qs.toString();
    document.getElementById("salesPdf").href = "/api/reports/sales/export?format=pdf&" + qs.toString();
  }
  document.getElementById("repFrom").addEventListener("change", updateExportLinks);
  document.getElementById("repTo").addEventListener("change", updateExportLinks);
  updateExportLinks();

  document.getElementById("runSalesRep").addEventListener("click", async () => {
    const from = document.getElementById("repFrom").value, to = document.getElementById("repTo").value;
    const rows = await api(`/reports/sales?from=${from}&to=${to}`);
    document.getElementById("salesRepOut").innerHTML = `<table><thead><tr><th>Sale#</th><th>Date</th><th>Location</th><th>Customer</th><th>Total</th></tr></thead><tbody>
      ${rows.map(r => `<tr><td>${r.sale_no}</td><td>${r.ts}</td><td>${esc(r.location_name)}</td><td>${esc(r.customer || "Walk-in")}</td><td>${money(r.total)}</td></tr>`).join("")}
    </tbody></table>`;
  });
  const inv = await api("/reports/inventory");
  document.getElementById("invRepOut").innerHTML = `<table><thead><tr><th>Location</th><th>Product</th><th>Qty</th></tr></thead><tbody>
    ${inv.map(r => `<tr><td>${esc(r.location_name)}</td><td>${esc(r.product_name)}</td><td>${r.quantity}</td></tr>`).join("")}
  </tbody></table>`;
}

// ================================================================== ADMIN: USERS
async function renderUsers(c) {
  c.innerHTML = `<div style="margin-bottom:12px"><button class="btn green" id="newUserBtn">+ New User</button></div><div id="usersTable">Loading...</div>`;
  async function load() {
    const rows = await api("/users");
    document.getElementById("usersTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Username</th><th>Name</th><th>Role</th><th>Location</th><th>Status</th><th></th>
    </tr></thead><tbody>
      ${rows.map(u => `<tr><td>${u.username}</td><td>${esc(u.name)}</td><td><span class="badge blue">${u.role}</span></td>
        <td>${esc(u.location_name || "—")}</td><td>${u.active ? '<span class="badge green">Active</span>' : '<span class="badge danger">Disabled</span>'}</td>
        <td>${u.active ? `<button class="btn small danger" onclick="disableUser(${u.id})">Disable</button>` : ""}</td></tr>`).join("")}
    </tbody></table></div>`;
  }
  document.getElementById("newUserBtn").addEventListener("click", () => {
    const locOpts = state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
    openModal("New User", `
      <div class="field"><label>Username</label><input id="uUsername"></div>
      <div class="field"><label>Full Name</label><input id="uName"></div>
      <div class="field"><label>Temporary Password</label><input id="uPass" type="text" value="Welcome123!"></div>
      <div class="field"><label>Role</label><select id="uRole"><option value="ADMIN">Admin</option><option value="ATTENDANT">Attendant</option><option value="DISPATCHER">Dispatcher</option></select></div>
      <div class="field"><label>Assigned Location</label><select id="uLoc"><option value="">— None (Admin only) —</option>${locOpts}</select></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="uSaveBtn">Create User</button></div>
    `, () => {
      document.getElementById("uSaveBtn").addEventListener("click", async () => {
        try {
          await api("/users", { method: "POST", body: {
            username: document.getElementById("uUsername").value,
            name: document.getElementById("uName").value,
            password: document.getElementById("uPass").value,
            role: document.getElementById("uRole").value,
            location_id: document.getElementById("uLoc").value || null,
          }});
          closeModal(); toast("User created", "success"); load();
        } catch (e) {}
      });
    });
  });
  load();
  window.disableUser = async (id) => {
    if (!confirm("Disable this user?")) return;
    await api(`/users/${id}/disable`, { method: "POST" }); toast("User disabled", "success"); load();
  };
}

// ================================================================== ADMIN: PRODUCTS
async function renderProducts(c) {
  c.innerHTML = `<div style="margin-bottom:12px"><button class="btn green" id="newProdBtn">+ New Product</button></div><div id="prodTable">Loading...</div>`;
  async function load() {
    state.products = await api("/products");
    document.getElementById("prodTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>SKU</th><th>Name</th><th>Category</th><th>Price</th><th>Cost</th><th>Min Stock</th>
    </tr></thead><tbody>
      ${state.products.map(p => `<tr><td>${esc(p.sku)}</td><td>${esc(p.name)}</td><td>${esc(p.category)}</td>
        <td>${money(p.price)}</td><td>${money(p.cost)}</td><td>${p.min_stock}</td></tr>`).join("")}
    </tbody></table></div>`;
  }
  document.getElementById("newProdBtn").addEventListener("click", () => {
    openModal("New Product", `
      <div class="field"><label>SKU</label><input id="pSku"></div>
      <div class="field"><label>Name</label><input id="pName"></div>
      <div class="field"><label>Category</label><input id="pCat" value="LPG Cylinder"></div>
      <div class="field"><label>Selling Price</label><input type="number" id="pPrice"></div>
      <div class="field"><label>Cost Price</label><input type="number" id="pCost" value="0"></div>
      <div class="field"><label>Minimum Stock Level</label><input type="number" id="pMin" value="10"></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="pSaveBtn">Save</button></div>
    `, () => {
      document.getElementById("pSaveBtn").addEventListener("click", async () => {
        try {
          await api("/products", { method: "POST", body: {
            sku: document.getElementById("pSku").value, name: document.getElementById("pName").value,
            category: document.getElementById("pCat").value, price: document.getElementById("pPrice").value,
            cost: document.getElementById("pCost").value, min_stock: document.getElementById("pMin").value,
          }});
          closeModal(); toast("Product created", "success"); load();
        } catch (e) {}
      });
    });
  });
  load();
}

// ================================================================== ADMIN: LOCATIONS
async function renderLocations(c) {
  c.innerHTML = `<div style="margin-bottom:12px"><button class="btn green" id="newLocBtn">+ New Location</button></div><div id="locTable">Loading...</div>`;
  async function load() {
    state.locations = await api("/locations");
    document.getElementById("locTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Name</th><th>Type</th><th>Address</th><th>Status</th></tr></thead><tbody>
      ${state.locations.map(l => `<tr><td>${esc(l.name)}</td><td><span class="badge blue">${l.type}</span></td><td>${esc(l.address || "")}</td>
        <td><span class="badge green">${l.status}</span></td></tr>`).join("")}
    </tbody></table></div>`;
  }
  document.getElementById("newLocBtn").addEventListener("click", () => {
    openModal("New Location", `
      <div class="field"><label>Name</label><input id="lName"></div>
      <div class="field"><label>Type</label><select id="lType"><option value="WAREHOUSE">Warehouse</option><option value="SHOWROOM">Showroom</option></select></div>
      <div class="field"><label>Address</label><input id="lAddr"></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="lSaveBtn">Save</button></div>
    `, () => {
      document.getElementById("lSaveBtn").addEventListener("click", async () => {
        try {
          await api("/locations", { method: "POST", body: {
            name: document.getElementById("lName").value, type: document.getElementById("lType").value,
            address: document.getElementById("lAddr").value,
          }});
          closeModal(); toast("Location created", "success"); load();
        } catch (e) {}
      });
    });
  });
  load();
}

// ================================================================== ADMIN: AUDIT LOG
async function renderAudit(c) {
  c.innerHTML = `
    <div style="margin-bottom:12px;display:flex;gap:8px">
      <select id="auditModuleFilter">
        <option value="">All Activity</option>
        <option value="SALES">Sales Only</option>
        <option value="INVENTORY">Inventory Only</option>
      </select>
    </div>
    <div id="auditTable">Loading...</div>`;
  async function load() {
    const module = document.getElementById("auditModuleFilter").value;
    const rows = await api("/audit-logs" + (module ? "?module=" + module : ""));
    document.getElementById("auditTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Time</th><th>User</th><th>Action</th><th>Module</th><th>Record</th><th>Details</th>
    </tr></thead><tbody>
      ${rows.length ? rows.map(r => `<tr><td>${r.ts}</td><td>${esc(r.username)}</td><td><span class="badge blue">${r.action}</span></td>
        <td>${r.module}</td><td>${r.record_id || ""}</td><td>${esc(r.details || "")}</td></tr>`).join("")
        : `<tr><td colspan="6" style="color:var(--muted)">No matching activity.</td></tr>`}
    </tbody></table></div>`;
  }
  document.getElementById("auditModuleFilter").addEventListener("change", load);
  load();
}
