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
    state.permittedModules = data.permitted_modules || [];
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
  { section: "Operations" },
  { id: "dashboard", label: "Dashboard", icon: "\u{1F3E0}", module: "dashboard" },
  { id: "pos", label: "POS", icon: "\u{1F6D2}", module: "pos" },
  { id: "sales", label: "Sales", icon: "\u{1F9FE}", module: "sales" },
  { id: "booked_orders", label: "Booked Orders", icon: "\u{1F4CB}", module: "booked_orders" },

  { section: "Customers & Money" },
  { id: "customers", label: "Customers", icon: "\u{1F465}", module: "customers" },
  { id: "receivables", label: "Receivables", icon: "\u{1F4B0}", module: "receivables" },
  { id: "cylinder_deposits", label: "Cylinder Deposits", icon: "\u{1F6E2}\uFE0F", module: "cylinder_deposits" },

  { section: "Inventory & Dispatch" },
  { id: "inventory", label: "Inventory", icon: "\u{1F4E6}", module: "inventory" },
  { id: "inventory_ledger", label: "Inv. Ledger", icon: "\u{1F4D2}", module: "inventory_ledger" },
  { id: "transfers", label: "Dispatch", icon: "\u{1F69A}", module: "transfers" },
  { id: "hauling", label: "Hauling Schedule", icon: "\u{1F69B}", module: "hauling" },
  { id: "commercial_deliveries", label: "50kg Commercial Billing", icon: "\u2696\uFE0F", module: "commercial_deliveries" },

  { section: "Fleet & Staff" },
  { id: "vehicles", label: "Vehicles", icon: "\u{1F697}", module: "vehicles" },
  { id: "employees", label: "Employees", icon: "\u{1F477}", module: "employees" },
  { id: "staff_schedule", label: "Staff Schedule", icon: "\u{1F4C5}", module: "staff_schedule" },
  { id: "kpi", label: "Employee KPI", icon: "\u{1F4C8}", module: "kpi" },

  { section: "Reports & Admin" },
  { id: "reports", label: "Reports", icon: "\u{1F4CA}", module: "reports" },
  { id: "audit", label: "Audit Log", icon: "\u{1F4DC}", module: "audit" },
  { id: "incident_reports", label: "Incident Reports", icon: "\u{26A0}\uFE0F", module: "incident_reports" },
  { id: "users", label: "Users", icon: "\u{1F464}", roles: ["ADMIN", "MANAGER"] },
  { id: "roles", label: "Manage Roles", icon: "\u{1F511}", roles: ["ADMIN", "MANAGER"] },
  { id: "products", label: "Products", icon: "\u{1F3F7}", roles: ["ADMIN"] },
  { id: "locations", label: "Locations", icon: "\u{1F3E2}", roles: ["ADMIN"] },
];

function navVisible(n) {
  // Users/Products/Locations/Roles are deliberately NOT part of the custom
  // module-permission system — they stay hardcoded to fixed roles as a
  // safety rail (see role_permissions design note).
  if (n.roles) return n.roles.includes(state.user.role);
  if (n.module) return state.user.role === "ADMIN" || state.permittedModules.includes(n.module);
  return false;
}

function buildNav() {
  let sideHtml = "", bottomHtml = "", currentSectionHasItems = false, pendingSection = null;
  for (const n of NAV) {
    if (n.section) { pendingSection = n.section; continue; }
    if (!navVisible(n)) continue;
    if (pendingSection) {
      sideHtml += `<div class="nav-section">${esc(pendingSection)}</div>`;
      pendingSection = null;
    }
    sideHtml += `<button class="nav-item" data-view="${n.id}">${n.icon}&nbsp;&nbsp;${n.label}</button>`;
    bottomHtml += `<button data-view="${n.id}"><span>${n.icon}</span><span>${n.label}</span></button>`;
  }
  document.getElementById("sideNav").innerHTML = sideHtml;
  document.getElementById("bottomNav").innerHTML = bottomHtml;
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
  const firstVisible = NAV.find(n => !n.section && navVisible(n));
  switchView(firstVisible ? firstVisible.id : "dashboard");
}

(async function initSession() {
  const data = await api("/me");
  if (data.user) { state.user = data.user; state.permittedModules = data.permitted_modules || []; boot(); }
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
    audit: renderAudit, cylinder_deposits: renderCylinderDeposits,
    hauling: renderHauling, vehicles: renderVehicles, employees: renderEmployees,
    staff_schedule: renderStaffSchedule, kpi: renderKpi,
    booked_orders: renderBookedOrders, incident_reports: renderIncidentReports, roles: renderRoles,
    commercial_deliveries: renderCommercialDeliveries,
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
    const existing = state.cart.find(i => i.product_id === pid && i.sale_mode === "REFILL");
    const currentQty = existing ? existing.qty : 0;
    if (currentQty + 1 > stock) { toast(`Only ${stock} in stock for ${prod.name}`, "error"); return; }
    if (existing) existing.qty += 1;
    else state.cart.push({
      product_id: pid, name: prod.name, base_price: prod.price, deposit_price: prod.deposit_price || 0,
      sale_mode: "REFILL", price: prod.price, qty: 1,
    });
    drawCart(locationId);
  }

  window.__posRemove = (pid, mode) => { state.cart = state.cart.filter(i => !(i.product_id === pid && i.sale_mode === mode)); drawCart(locationId); };
  window.__posQty = (pid, mode, val) => {
    const item = state.cart.find(i => i.product_id === pid && i.sale_mode === mode);
    const stock = stockMap[pid] ?? 0;
    let q = parseInt(val) || 0;
    if (q > stock) { toast(`Only ${stock} in stock`, "error"); q = stock; }
    if (q <= 0) { state.cart = state.cart.filter(i => !(i.product_id === pid && i.sale_mode === mode)); }
    else item.qty = q;
    drawCart(locationId);
  };
  window.__posMode = (pid, oldMode, newMode) => {
    const item = state.cart.find(i => i.product_id === pid && i.sale_mode === oldMode);
    if (!item) return;
    // merge into an existing line with the same new mode, if one exists
    const target = state.cart.find(i => i.product_id === pid && i.sale_mode === newMode);
    if (target) {
      target.qty += item.qty;
      state.cart = state.cart.filter(i => i !== item);
    } else {
      item.sale_mode = newMode;
      item.price = item.base_price + (newMode === "DEPOSIT" ? item.deposit_price : 0);
    }
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
        <div class="cname">${esc(i.name)} ${i.sale_mode === 'DEPOSIT' ? '<span class="badge warn">DEPOSIT</span>' : ''}<br>
          <span style="color:var(--muted);font-size:12px">${money(i.price)} each</span>
          ${i.deposit_price > 0 ? `<select style="margin-left:6px;font-size:11px;padding:2px 4px" onchange="__posMode(${i.product_id}, '${i.sale_mode}', this.value)">
            <option value="REFILL" ${i.sale_mode === 'REFILL' ? 'selected' : ''}>Refill (exchange empty)</option>
            <option value="DEPOSIT" ${i.sale_mode === 'DEPOSIT' ? 'selected' : ''}>New/Deposit (+${money(i.deposit_price)})</option>
          </select>` : ""}
        </div>
        <input type="number" min="0" value="${i.qty}" onchange="__posQty(${i.product_id}, '${i.sale_mode}', this.value)">
        <div style="width:78px;text-align:right;font-weight:600">${money(i.price * i.qty)}</div>
        <button class="btn small danger" onclick="__posRemove(${i.product_id}, '${i.sale_mode}')">&times;</button>
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
        items: state.cart.map(i => ({ product_id: i.product_id, qty: i.qty, sale_mode: i.sale_mode })),
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
      ${items.map(i => `${esc(i.product_name)}${i.sale_mode === 'DEPOSIT' ? ' (DEPOSIT)' : ''}<br>  ${i.qty} x ${money(i.price)} = ${money(i.subtotal)}`).join("<br>")}
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
      ${items.map(i => `<tr><td>${esc(i.product_name)}${i.sale_mode === 'DEPOSIT' ? ' <span class="badge warn">DEPOSIT</span>' : ''}</td><td>${i.qty}</td><td>${money(i.price)}</td><td>${money(i.subtotal)}</td></tr>`).join("")}
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
  const isAdmin = state.user.role === "ADMIN";
  const categories = await api("/customer-categories");
  c.innerHTML = `<div style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap">
    <input type="text" id="custSearch" placeholder="Search customers..." style="flex:1;min-width:180px">
    ${isAdmin ? `
      <button class="btn green" id="newCustBtn">+ New Customer</button>
      <button class="btn" id="manageCatBtn">Manage Categories</button>
      <a class="btn" href="/api/customers/template">Download CSV Template</a>
      <button class="btn" id="importCustBtn">Import CSV</button>
    ` : ""}
  </div><div id="custTable"></div>`;

  function categoryOptions(selected) {
    return categories.map(cat => `<option value="${esc(cat.name)}" ${cat.name === selected ? "selected" : ""}>${esc(cat.name)}</option>`).join("");
  }

  async function load(q = "") {
    const list = await api("/customers?q=" + encodeURIComponent(q));
    document.getElementById("custTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Account #</th><th>Name</th><th>Category</th><th>Phone</th><th>Terms</th><th>Credit Limit</th><th>Status</th><th></th>
    </tr></thead><tbody>
      ${list.map(cu => `<tr><td>${cu.account_no}</td><td>${esc(cu.name)}</td>
        <td><span class="badge blue">${esc(cu.category)}</span></td><td>${esc(cu.phone || "")}</td>
        <td>${cu.terms_days} days</td><td>${money(cu.credit_limit)}</td>
        <td><span class="badge ${cu.status === 'ACTIVE' ? 'green' : 'gray'}">${cu.status}</span></td>
        <td style="display:flex;gap:6px">
          <button class="btn small" onclick="openCustomerLedger(${cu.id})">Ledger / SOA</button>
          ${isAdmin ? `<button class="btn small" onclick="editCustomer(${cu.id})">Edit</button>
            <button class="btn small danger" onclick="deleteCustomer(${cu.id})">Delete</button>` : ""}
        </td></tr>`).join("")}
    </tbody></table></div>`;
  }

  function customerFormFields(cu = {}) {
    return `
      <div class="field"><label>Name</label><input id="cName" value="${esc(cu.name || "")}"></div>
      <div class="field"><label>Business Name</label><input id="cBiz" value="${esc(cu.business_name || "")}"></div>
      <div class="field"><label>Phone</label><input id="cPhone" value="${esc(cu.phone || "")}"></div>
      <div class="field"><label>Address</label><input id="cAddress" value="${esc(cu.address || "")}"></div>
      <div class="field"><label>Category</label><select id="cCat">${categoryOptions(cu.category)}</select></div>
      <div class="field"><label>Payment Terms (days, 0 = cash only)</label><input type="number" id="cTerms" value="${cu.terms_days ?? 0}"></div>
      <div class="field"><label>Credit Limit (₱)</label><input type="number" id="cLimit" value="${cu.credit_limit ?? 0}"></div>
      ${cu.id ? `<div class="field"><label>Status</label><select id="cStatus">
        <option value="ACTIVE" ${cu.status === 'ACTIVE' ? 'selected' : ''}>Active</option>
        <option value="INACTIVE" ${cu.status === 'INACTIVE' ? 'selected' : ''}>Inactive</option></select></div>` : ""}
    `;
  }
  function readCustomerForm() {
    return {
      name: document.getElementById("cName").value.trim(),
      business_name: document.getElementById("cBiz").value,
      phone: document.getElementById("cPhone").value,
      address: document.getElementById("cAddress").value,
      category: document.getElementById("cCat").value,
      terms_days: document.getElementById("cTerms").value,
      credit_limit: document.getElementById("cLimit").value,
      status: document.getElementById("cStatus") ? document.getElementById("cStatus").value : "ACTIVE",
    };
  }

  document.getElementById("custSearch").addEventListener("input", e => load(e.target.value));

  if (isAdmin) {
    document.getElementById("newCustBtn").addEventListener("click", () => {
      openModal("New Customer", customerFormFields() + `
        <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
          <button class="btn green" id="saveCustBtn">Save</button></div>`, () => {
        document.getElementById("saveCustBtn").addEventListener("click", async () => {
          const body = readCustomerForm();
          if (!body.name) { toast("Name is required", "error"); return; }
          try { await api("/customers", { method: "POST", body }); closeModal(); toast("Customer created", "success"); load(); }
          catch (e) {}
        });
      });
    });

    document.getElementById("manageCatBtn").addEventListener("click", openCategoryManager);

    document.getElementById("importCustBtn").addEventListener("click", () => {
      openModal("Import Customers from CSV", `
        <p style="font-size:13px;color:var(--muted)">Download the template above, fill it in, then upload it here.</p>
        <div class="field"><input type="file" id="custCsvFile" accept=".csv"></div>
        <div id="importResult"></div>
        <div class="modal-actions"><button class="btn" onclick="closeModal()">Close</button>
          <button class="btn green" id="doImportBtn">Upload</button></div>
      `, () => {
        document.getElementById("doImportBtn").addEventListener("click", async () => {
          const file = document.getElementById("custCsvFile").files[0];
          if (!file) { toast("Choose a CSV file first", "error"); return; }
          const fd = new FormData(); fd.append("file", file);
          try {
            const res = await fetch("/api/customers/import", { method: "POST", body: fd, credentials: "include" });
            const data = await res.json();
            if (!res.ok) { toast(data.error || "Import failed", "error"); return; }
            document.getElementById("importResult").innerHTML = `
              <p style="color:var(--green-dark);font-weight:600">${data.created} customer(s) imported.</p>
              ${data.errors.length ? `<p style="color:var(--danger);font-size:12.5px">${data.errors.map(esc).join("<br>")}</p>` : ""}
            `;
            toast(`${data.created} customers imported`, "success");
            load();
          } catch (e) { toast("Import failed", "error"); }
        });
      });
    });
  }

  window.editCustomer = async (cid) => {
    const { customer } = await api(`/customers/${cid}`);
    openModal("Edit Customer", customerFormFields(customer) + `
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="saveCustBtn">Save Changes</button></div>`, () => {
      document.getElementById("saveCustBtn").addEventListener("click", async () => {
        const body = readCustomerForm();
        if (!body.name) { toast("Name is required", "error"); return; }
        try { await api(`/customers/${cid}`, { method: "PUT", body }); closeModal(); toast("Customer updated", "success"); load(); }
        catch (e) {}
      });
    });
  };

  window.deleteCustomer = async (cid) => {
    if (!confirm("Delete this customer? This only works if they have no sales history.")) return;
    try { await api(`/customers/${cid}`, { method: "DELETE" }); toast("Customer deleted", "success"); load(); }
    catch (e) {}
  };

  function openCategoryManager() {
    openModal("Manage Customer Categories", `
      <div id="catList"></div>
      <div style="display:flex;gap:8px;margin-top:10px">
        <input type="text" id="newCatName" placeholder="New category name" style="flex:1">
        <button class="btn green" id="addCatBtn">Add</button>
      </div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Close</button></div>
    `, () => {
      async function loadCats() {
        const cats = await api("/customer-categories");
        document.getElementById("catList").innerHTML = cats.map(cat => `
          <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--border)">
            <span>${esc(cat.name)}</span>
            <button class="btn small danger" onclick="deleteCategory(${cat.id})">Remove</button>
          </div>`).join("");
      }
      document.getElementById("addCatBtn").addEventListener("click", async () => {
        const name = document.getElementById("newCatName").value.trim();
        if (!name) return;
        try { await api("/customer-categories", { method: "POST", body: { name } }); document.getElementById("newCatName").value = ""; loadCats(); toast("Category added", "success"); }
        catch (e) {}
      });
      window.deleteCategory = async (id) => {
        try { await api(`/customer-categories/${id}`, { method: "DELETE" }); loadCats(); toast("Category removed", "success"); }
        catch (e) {}
      };
      loadCats();
    });
  }

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
  const canExport = state.user.role === "ADMIN" || state.user.role === "MANAGER";
  const statusColor = s => ({ PAID: "green", CURRENT: "blue", "DUE SOON": "warn", OVERDUE: "danger", "PARTIALLY PAID": "warn" }[s] || "gray");
  c.innerHTML = `<div style="margin-bottom:12px"><button class="btn" id="agingBtn">Aging Report</button></div>
  <div class="table-wrap"><table><thead><tr>
    <th>Invoice #</th><th>Customer</th><th>Sale #</th><th>Invoice Date</th><th>Due Date</th><th>Amount</th><th>Paid</th><th>Balance</th><th>Status</th><th></th>
  </tr></thead><tbody>
    ${rows.map(r => `<tr>
      <td>${r.invoice_no}</td><td>${esc(r.customer_name)} (${r.account_no})</td><td>${r.sale_no}</td>
      <td>${r.invoice_date}</td><td>${r.due_date}</td><td>${money(r.amount)}</td><td>${money(r.paid)}</td><td>${money(r.balance)}</td>
      <td><span class="badge ${statusColor(r.status)}">${r.status}</span></td>
      <td>${r.balance > 0.009 ? `<button class="btn small green" onclick="payReceivable(${r.id}, ${r.balance})">Record Payment</button>` : ""}</td>
    </tr>`).join("")}
  </tbody></table></div>`;
  document.getElementById("agingBtn").addEventListener("click", () => openAgingReport(canExport));
}

async function openAgingReport(canExport) {
  const data = await api("/reports/receivables-aging");
  const bucketLabels = [
    ["current", "Current"], ["days_1_30", "1-30 Days"], ["days_31_60", "31-60 Days"],
    ["days_61_90", "61-90 Days"], ["days_91_120", "91-120 Days"], ["days_120_plus", "120+ Days"],
  ];
  openModal("Accounts Receivable Aging", `
    ${canExport ? `<div style="margin-bottom:10px">
      <a class="btn small" href="/api/reports/receivables-aging/export?format=xlsx">Export Excel</a>
      <a class="btn small" href="/api/reports/receivables-aging/export?format=pdf">Export PDF</a>
    </div>` : ""}
    <div class="table-wrap"><table><thead><tr>
      <th>Customer</th>${bucketLabels.map(([, l]) => `<th>${l}</th>`).join("")}<th>Total</th>
    </tr></thead><tbody>
      ${data.rows.length ? data.rows.map(r => `<tr>
        <td>${esc(r.customer_name)} (${r.account_no})</td>
        ${bucketLabels.map(([k]) => `<td>${r[k] ? money(r[k]) : ""}</td>`).join("")}
        <td style="font-weight:700">${money(r.total)}</td>
      </tr>`).join("") : `<tr><td colspan="8" style="color:var(--muted)">No outstanding receivables.</td></tr>`}
    </tbody></table>
    <table style="margin-top:2px"><tbody>
      <tr style="font-weight:700;background:var(--bg)">
        <td style="width:220px">TOTAL</td>
        ${bucketLabels.map(([k]) => `<td>${data.totals[k] ? money(data.totals[k]) : ""}</td>`).join("")}
        <td>${money(data.grand_total)}</td>
      </tr>
    </tbody></table>
    </div>
    <div class="modal-actions"><button class="btn" onclick="closeModal()">Close</button></div>
  `);
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
    const empties = await api("/inventory/empties");
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
      </tbody></table></div>
      <h3 style="margin:20px 0 8px">Empty Cylinders on Hand</h3>
      <p style="color:var(--muted);font-size:13px;margin-top:0">Returned via refill sales — ready to haul back to the plant for refilling.</p>
      <div class="table-wrap"><table><thead><tr><th>Location</th><th>Product</th><th>Empty Qty</th></tr></thead><tbody>
        ${empties.filter(e => e.quantity > 0).length ? empties.filter(e => e.quantity > 0).map(e => `<tr>
          <td>${esc(e.location_name)}</td><td>${esc(e.product_name)}</td><td><b>${e.quantity}</b></td></tr>`).join("")
          : `<tr><td colspan="3" style="color:var(--muted)">No empty cylinders on hand.</td></tr>`}
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
  c.innerHTML = `<div style="margin-bottom:12px;display:flex;gap:8px">
    <button class="btn green" id="newXferBtn">+ New Transfer / Dispatch</button>
    <button class="btn" id="importStockBtn">+ Import Stock from Supplier</button>
  </div><div id="xferTable">Loading...</div>`;
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
  document.getElementById("importStockBtn").addEventListener("click", () => {
    const locOpts = state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
    const prodOpts = state.products.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
    openModal("Import Stock from Supplier", `
      <div class="field"><label>Receiving Location</label><select id="ibLoc">${locOpts}</select></div>
      <div class="field"><label>Product</label><select id="ibProd">${prodOpts}</select></div>
      <div class="field"><label>Quantity Received</label><input type="number" id="ibQty" value="1" min="1"></div>
      <div class="field"><label>Supplier</label><input id="ibSupplier" placeholder="e.g. Solane Plant"></div>
      <div class="field"><label>Reference # (delivery receipt, etc.)</label><input id="ibRef"></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="ibSaveBtn">Record Received Stock</button></div>
    `, () => {
      document.getElementById("ibSaveBtn").addEventListener("click", async () => {
        const supplier = document.getElementById("ibSupplier").value.trim();
        if (!supplier) { toast("Supplier name is required", "error"); return; }
        try {
          await api("/inventory/inbound", { method: "POST", body: {
            location_id: document.getElementById("ibLoc").value, product_id: document.getElementById("ibProd").value,
            qty: document.getElementById("ibQty").value, supplier, reference: document.getElementById("ibRef").value,
          }});
          closeModal(); toast("Stock received and recorded", "success"); load();
        } catch (e) {}
      });
    });
  });
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
  const canManage = state.user.role === "ADMIN" || state.user.role === "MANAGER";
  c.innerHTML = `<div style="margin-bottom:12px">${canManage ? `<button class="btn green" id="newUserBtn">+ New User</button>` : ""}</div><div id="usersTable">Loading...</div>`;
  let allRoles = [];
  function roleOptions(selected) {
    return allRoles
      .map(r => `<option value="${esc(r.name)}" ${r.name === selected ? "selected" : ""}>${esc(r.name)}</option>`).join("");
  }
  async function load() {
    allRoles = await api("/roles");
    const rows = await api("/users");
    document.getElementById("usersTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Username</th><th>Name</th><th>Role</th><th>Location</th><th>Status</th>${canManage ? "<th></th>" : ""}
    </tr></thead><tbody>
      ${rows.map(u => `<tr><td>${u.username}</td><td>${esc(u.name)}</td><td><span class="badge blue">${esc(u.role)}</span></td>
        <td>${esc(u.location_name || "—")}</td><td>${u.active ? '<span class="badge green">Active</span>' : '<span class="badge danger">Disabled</span>'}</td>
        ${canManage ? `<td style="display:flex;gap:6px">
          <button class="btn small" onclick="editUser(${u.id})">Edit</button>
          ${u.active ? `<button class="btn small danger" onclick="disableUser(${u.id})">Disable</button>`
                     : `<button class="btn small" onclick="enableUser(${u.id})">Enable</button>`}
          <button class="btn small danger" onclick="deleteUserRow(${u.id})">Delete</button>
        </td>` : ""}</tr>`).join("")}
    </tbody></table></div>`;
  }
  if (canManage) {
    document.getElementById("newUserBtn").addEventListener("click", () => {
      const locOpts = state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
      openModal("New User", `
        <div class="field"><label>Username</label><input id="uUsername"></div>
        <div class="field"><label>Full Name</label><input id="uName"></div>
        <div class="field"><label>Temporary Password</label><input id="uPass" type="text" value="Welcome123!"></div>
        <div class="field"><label>Role</label><select id="uRole">${roleOptions()}</select></div>
        <div class="field"><label>Assigned Location</label><select id="uLoc"><option value="">— None (Admin/Manager) —</option>${locOpts}</select></div>
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
    window.editUser = async (id) => {
      const u = (await api("/users")).find(x => x.id === id);
      const locOpts = state.locations.map(l => `<option value="${l.id}" ${u.location_id === l.id ? "selected" : ""}>${esc(l.name)}</option>`).join("");
      openModal("Edit User", `
        <div class="field"><label>Username</label><input value="${esc(u.username)}" disabled></div>
        <div class="field"><label>Full Name</label><input id="uName" value="${esc(u.name)}"></div>
        <div class="field"><label>Role</label><select id="uRole">${roleOptions(u.role)}</select></div>
        <div class="field"><label>Assigned Location</label><select id="uLoc"><option value="">— None —</option>${locOpts}</select></div>
        <div class="field"><label>Reset Password (leave blank to keep current)</label><input id="uPass" type="text" placeholder="New password"></div>
        <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
          <button class="btn green" id="uSaveBtn">Save Changes</button></div>
      `, () => {
        document.getElementById("uSaveBtn").addEventListener("click", async () => {
          const body = {
            name: document.getElementById("uName").value, role: document.getElementById("uRole").value,
            location_id: document.getElementById("uLoc").value || null,
          };
          const pw = document.getElementById("uPass").value;
          if (pw) body.password = pw;
          try { await api(`/users/${id}`, { method: "PUT", body }); closeModal(); toast("User updated", "success"); load(); }
          catch (e) {}
        });
      });
    };
    window.disableUser = async (id) => {
      if (!confirm("Disable this user?")) return;
      try { await api(`/users/${id}/disable`, { method: "POST" }); toast("User disabled", "success"); load(); }
      catch (e) {}
    };
    window.enableUser = async (id) => {
      try { await api(`/users/${id}/enable`, { method: "POST" }); toast("User re-enabled", "success"); load(); }
      catch (e) {}
    };
    window.deleteUserRow = async (id) => {
      if (!confirm("Permanently delete this user? This only works if they have no activity on record.")) return;
      try { await api(`/users/${id}`, { method: "DELETE" }); toast("User deleted", "success"); load(); }
      catch (e) {}
    };
  }
  load();
}

// ================================================================== ADMIN: PRODUCTS
async function renderProducts(c) {
  c.innerHTML = `<div style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap">
    <button class="btn green" id="newProdBtn">+ New Product</button>
    <a class="btn" href="/api/products/template">Download CSV Template</a>
    <button class="btn" id="importProdBtn">Import CSV</button>
  </div><div id="prodTable">Loading...</div>`;
  async function load() {
    state.products = await api("/products");
    document.getElementById("prodTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>SKU</th><th>Name</th><th>Category</th><th>Price</th><th>Cost</th><th>Deposit Price</th><th>Min Stock</th><th></th>
    </tr></thead><tbody>
      ${state.products.map(p => `<tr><td>${esc(p.sku)}</td><td>${esc(p.name)}</td><td>${esc(p.category)}</td>
        <td>${money(p.price)}</td><td>${money(p.cost)}</td><td>${money(p.deposit_price)}</td><td>${p.min_stock}</td>
        <td><button class="btn small" onclick="editProduct(${p.id})">Edit</button></td></tr>`).join("")}
    </tbody></table></div>`;
  }
  function formFields(p = {}) {
    return `
      <div class="field"><label>SKU</label><input id="pSku" value="${esc(p.sku || "")}" ${p.id ? "disabled" : ""}></div>
      <div class="field"><label>Name</label><input id="pName" value="${esc(p.name || "")}"></div>
      <div class="field"><label>Category</label><input id="pCat" value="${esc(p.category || "LPG Cylinder")}"></div>
      <div class="field"><label>Selling Price (Refill)</label><input type="number" id="pPrice" value="${p.price ?? ""}"></div>
      <div class="field"><label>Cost Price</label><input type="number" id="pCost" value="${p.cost ?? 0}"></div>
      <div class="field"><label>Cylinder Deposit Fee (added on top for New/Deposit sales)</label><input type="number" id="pDeposit" value="${p.deposit_price ?? 0}"></div>
      <div class="field"><label>Price per KG (for 50kg commercial weight-billing)</label><input type="number" step="0.01" id="pPricePerKg" value="${p.price_per_kg ?? 0}"></div>
      <div class="field"><label>Minimum Stock Level</label><input type="number" id="pMin" value="${p.min_stock ?? 10}"></div>
    `;
  }
  function readForm() {
    return {
      sku: document.getElementById("pSku").value, name: document.getElementById("pName").value,
      category: document.getElementById("pCat").value, price: document.getElementById("pPrice").value,
      cost: document.getElementById("pCost").value, deposit_price: document.getElementById("pDeposit").value,
      price_per_kg: document.getElementById("pPricePerKg").value,
      min_stock: document.getElementById("pMin").value,
    };
  }
  document.getElementById("newProdBtn").addEventListener("click", () => {
    openModal("New Product", formFields() + `<div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn green" id="pSaveBtn">Save</button></div>`, () => {
      document.getElementById("pSaveBtn").addEventListener("click", async () => {
        try { await api("/products", { method: "POST", body: readForm() }); closeModal(); toast("Product created", "success"); load(); }
        catch (e) {}
      });
    });
  });
  window.editProduct = (id) => {
    const p = state.products.find(x => x.id === id);
    openModal("Edit Product", formFields(p) + `<div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn green" id="pSaveBtn">Save Changes</button></div>`, () => {
      document.getElementById("pSaveBtn").addEventListener("click", async () => {
        try { await api(`/products/${id}`, { method: "PUT", body: readForm() }); closeModal(); toast("Product updated", "success"); load(); }
        catch (e) {}
      });
    });
  };
  document.getElementById("importProdBtn").addEventListener("click", () => {
    openModal("Import Products from CSV", `
      <p style="font-size:13px;color:var(--muted)">Download the template above, fill it in, then upload it here.</p>
      <div class="field"><input type="file" id="prodCsvFile" accept=".csv"></div>
      <div id="importResult"></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Close</button>
        <button class="btn green" id="doImportBtn">Upload</button></div>
    `, () => {
      document.getElementById("doImportBtn").addEventListener("click", async () => {
        const file = document.getElementById("prodCsvFile").files[0];
        if (!file) { toast("Choose a CSV file first", "error"); return; }
        const fd = new FormData(); fd.append("file", file);
        try {
          const res = await fetch("/api/products/import", { method: "POST", body: fd, credentials: "include" });
          const data = await res.json();
          if (!res.ok) { toast(data.error || "Import failed", "error"); return; }
          document.getElementById("importResult").innerHTML = `
            <p style="color:var(--green-dark);font-weight:600">${data.created} product(s) imported.</p>
            ${data.errors.length ? `<p style="color:var(--danger);font-size:12.5px">${data.errors.map(esc).join("<br>")}</p>` : ""}
          `;
          toast(`${data.created} products imported`, "success");
          load();
        } catch (e) { toast("Import failed", "error"); }
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
      <th>Name</th><th>Type</th><th>Address</th><th>Status</th><th></th></tr></thead><tbody>
      ${state.locations.map(l => `<tr><td>${esc(l.name)}</td><td><span class="badge blue">${l.type}</span></td><td>${esc(l.address || "")}</td>
        <td><span class="badge ${l.status === 'ACTIVE' ? 'green' : 'gray'}">${l.status}</span></td>
        <td style="display:flex;gap:6px"><button class="btn small" onclick="editLocation(${l.id})">Edit</button>
          <button class="btn small danger" onclick="deleteLocation(${l.id})">Delete</button></td></tr>`).join("")}
    </tbody></table></div>`;
  }
  function formFields(l = {}) {
    return `
      <div class="field"><label>Name</label><input id="lName" value="${esc(l.name || "")}"></div>
      <div class="field"><label>Type</label><select id="lType">
        <option value="WAREHOUSE" ${l.type === 'WAREHOUSE' ? 'selected' : ''}>Warehouse</option>
        <option value="SHOWROOM" ${l.type === 'SHOWROOM' ? 'selected' : ''}>Showroom</option></select></div>
      <div class="field"><label>Address</label><input id="lAddr" value="${esc(l.address || "")}"></div>
      ${l.id ? `<div class="field"><label>Status</label><select id="lStatus">
        <option value="ACTIVE" ${l.status === 'ACTIVE' ? 'selected' : ''}>Active</option>
        <option value="INACTIVE" ${l.status === 'INACTIVE' ? 'selected' : ''}>Inactive</option></select></div>` : ""}
    `;
  }
  function readForm() {
    return {
      name: document.getElementById("lName").value, type: document.getElementById("lType").value,
      address: document.getElementById("lAddr").value,
      status: document.getElementById("lStatus") ? document.getElementById("lStatus").value : "ACTIVE",
    };
  }
  document.getElementById("newLocBtn").addEventListener("click", () => {
    openModal("New Location", formFields() + `<div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn green" id="lSaveBtn">Save</button></div>`, () => {
      document.getElementById("lSaveBtn").addEventListener("click", async () => {
        try { await api("/locations", { method: "POST", body: readForm() }); closeModal(); toast("Location created", "success"); load(); }
        catch (e) {}
      });
    });
  });
  window.editLocation = (id) => {
    const l = state.locations.find(x => x.id === id);
    openModal("Edit Location", formFields(l) + `<div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn green" id="lSaveBtn">Save Changes</button></div>`, () => {
      document.getElementById("lSaveBtn").addEventListener("click", async () => {
        try { await api(`/locations/${id}`, { method: "PUT", body: readForm() }); closeModal(); toast("Location updated", "success"); load(); }
        catch (e) {}
      });
    });
  };
  window.deleteLocation = async (id) => {
    if (!confirm("Delete this location? This only works if it has no inventory, users, or sales.")) return;
    try { await api(`/locations/${id}`, { method: "DELETE" }); toast("Location deleted", "success"); load(); }
    catch (e) {}
  };
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

// ================================================================== CYLINDER DEPOSITS
async function renderCylinderDeposits(c) {
  c.innerHTML = `
    <p style="color:var(--muted);font-size:13px">Cylinders currently on loan to customers from DEPOSIT sales — not yet returned.</p>
    <div id="depTable">Loading...</div>`;
  async function load() {
    const rows = await api("/cylinder-deposits");
    document.getElementById("depTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Customer</th><th>Account #</th><th>Product</th><th>Qty on Loan</th><th></th>
    </tr></thead><tbody>
      ${rows.length ? rows.map(r => `<tr><td>${esc(r.customer_name)}</td><td>${r.account_no}</td><td>${esc(r.product_name)}</td>
        <td><b>${r.qty_on_loan}</b></td>
        <td><button class="btn small green" onclick="openReturnCylinder(${r.customer_id}, ${r.product_id}, ${r.qty_on_loan}, '${esc(r.customer_name)}', '${esc(r.product_name)}')">Record Return</button></td></tr>`).join("")
        : `<tr><td colspan="5" style="color:var(--muted)">No cylinders currently on deposit.</td></tr>`}
    </tbody></table></div>`;
  }
  window.openReturnCylinder = (customerId, productId, maxQty, custName, prodName) => {
    const locOpts = state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
    openModal(`Return Cylinder — ${custName}`, `
      <p>${esc(prodName)} — currently ${maxQty} on loan</p>
      ${state.user.role === "ADMIN" ? `<div class="field"><label>Receiving Location</label><select id="retLoc">${locOpts}</select></div>` : ""}
      <div class="field"><label>Quantity Returned</label><input type="number" id="retQty" value="1" min="1" max="${maxQty}"></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="retSaveBtn">Confirm Return</button></div>
    `, () => {
      document.getElementById("retSaveBtn").addEventListener("click", async () => {
        const body = { customer_id: customerId, product_id: productId, qty: document.getElementById("retQty").value };
        if (state.user.role === "ADMIN") body.location_id = document.getElementById("retLoc").value;
        try {
          await api("/cylinder-deposits/return", { method: "POST", body });
          closeModal(); toast("Cylinder return recorded", "success"); load();
        } catch (e) {}
      });
    });
  };
  load();
}

// ================================================================== VEHICLES
async function renderVehicles(c) {
  c.innerHTML = `<div style="margin-bottom:12px"><button class="btn green" id="newVehBtn">+ New Vehicle</button></div><div id="vehTable">Loading...</div>`;
  const statusColor = s => ({ AVAILABLE: "green", DISPATCHED: "warn", "IN TRANSIT": "warn", MAINTENANCE: "danger", INACTIVE: "gray" }[s] || "gray");
  async function load() {
    const rows = await api("/vehicles");
    document.getElementById("vehTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Plate #</th><th>Type</th><th>Driver</th><th>Status</th><th>Odometer</th><th>Next Maintenance</th><th>Reg. Expiry</th><th></th>
    </tr></thead><tbody>
      ${rows.length ? rows.map(v => `<tr><td>${esc(v.plate_number)}</td><td>${esc(v.vehicle_type || "")}</td><td>${esc(v.driver || "")}</td>
        <td><span class="badge ${statusColor(v.status)}">${v.status}</span></td><td>${v.odometer || 0}</td>
        <td>${v.next_maintenance_date || "—"}</td><td>${v.registration_expiry || "—"}</td>
        <td style="display:flex;gap:6px"><button class="btn small" onclick="editVehicle(${v.id})">Edit</button>
          <button class="btn small danger" onclick="deleteVehicle(${v.id})">Delete</button></td></tr>`).join("")
        : `<tr><td colspan="8" style="color:var(--muted)">No vehicles yet.</td></tr>`}
    </tbody></table></div>`;
  }
  function formFields(v = {}) {
    return `
      <div class="field"><label>Plate Number</label><input id="vPlate" value="${esc(v.plate_number || "")}" ${v.id ? "disabled" : ""}></div>
      <div class="field"><label>Vehicle Type</label><input id="vType" value="${esc(v.vehicle_type || "")}" placeholder="e.g. 6-Wheeler Truck"></div>
      <div class="field"><label>Driver</label><input id="vDriver" value="${esc(v.driver || "")}"></div>
      <div class="field"><label>Status</label><select id="vStatus">
        ${["AVAILABLE", "DISPATCHED", "IN TRANSIT", "MAINTENANCE", "INACTIVE"].map(s => `<option value="${s}" ${v.status === s ? "selected" : ""}>${s}</option>`).join("")}
      </select></div>
      <div class="field"><label>Odometer (km)</label><input type="number" id="vOdo" value="${v.odometer || 0}"></div>
      <div class="field"><label>Last Maintenance Date</label><input type="date" id="vLastMaint" value="${v.last_maintenance_date || ""}"></div>
      <div class="field"><label>Next Maintenance Date</label><input type="date" id="vNextMaint" value="${v.next_maintenance_date || ""}"></div>
      <div class="field"><label>Registration Expiry</label><input type="date" id="vReg" value="${v.registration_expiry || ""}"></div>
      <div class="field"><label>Insurance Expiry</label><input type="date" id="vIns" value="${v.insurance_expiry || ""}"></div>
      <div class="field"><label>Notes</label><input id="vNotes" value="${esc(v.notes || "")}"></div>
    `;
  }
  function readForm() {
    return {
      plate_number: document.getElementById("vPlate").value.trim(),
      vehicle_type: document.getElementById("vType").value,
      driver: document.getElementById("vDriver").value,
      status: document.getElementById("vStatus").value,
      odometer: document.getElementById("vOdo").value,
      last_maintenance_date: document.getElementById("vLastMaint").value,
      next_maintenance_date: document.getElementById("vNextMaint").value,
      registration_expiry: document.getElementById("vReg").value,
      insurance_expiry: document.getElementById("vIns").value,
      notes: document.getElementById("vNotes").value,
    };
  }
  document.getElementById("newVehBtn").addEventListener("click", () => {
    openModal("New Vehicle", formFields() + `<div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn green" id="vSaveBtn">Save</button></div>`, () => {
      document.getElementById("vSaveBtn").addEventListener("click", async () => {
        const body = readForm();
        if (!body.plate_number) { toast("Plate number is required", "error"); return; }
        try { await api("/vehicles", { method: "POST", body }); closeModal(); toast("Vehicle added", "success"); load(); }
        catch (e) {}
      });
    });
  });
  window.editVehicle = async (id) => {
    const v = (await api("/vehicles")).find(x => x.id === id);
    openModal("Edit Vehicle", formFields(v) + `<div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn green" id="vSaveBtn">Save Changes</button></div>`, () => {
      document.getElementById("vSaveBtn").addEventListener("click", async () => {
        try { await api(`/vehicles/${id}`, { method: "PUT", body: readForm() }); closeModal(); toast("Vehicle updated", "success"); load(); }
        catch (e) {}
      });
    });
  };
  window.deleteVehicle = async (id) => {
    if (!confirm("Delete this vehicle?")) return;
    try { await api(`/vehicles/${id}`, { method: "DELETE" }); toast("Vehicle deleted", "success"); load(); }
    catch (e) {}
  };
  load();
}

// ================================================================== HAULING SCHEDULE
async function renderHauling(c) {
  c.innerHTML = `<div style="margin-bottom:12px"><button class="btn green" id="newHaulBtn">+ Schedule Hauling</button></div><div id="haulTable">Loading...</div>`;
  const statusColor = s => ({ SCHEDULED: "blue", LOADING: "warn", "IN TRANSIT": "warn", COMPLETED: "green", CANCELLED: "danger" }[s] || "gray");
  async function load() {
    const rows = await api("/hauling");
    document.getElementById("haulTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Hauling #</th><th>Date</th><th>Plant</th><th>Destination</th><th>Vehicle</th><th>Products</th><th>Status</th><th></th>
    </tr></thead><tbody>
      ${rows.length ? rows.map(h => `<tr><td>${h.hauling_no}</td><td>${h.scheduled_date}</td><td>${esc(h.plant_name)}</td>
        <td>${esc(h.destination_name || "")}</td><td>${esc(h.plate_number || "")}</td>
        <td>${h.items.map(i => `${esc(i.product_name)} x${i.qty}`).join(", ")}</td>
        <td><span class="badge ${statusColor(h.status)}">${h.status}</span></td>
        <td style="display:flex;gap:6px">
          <select onchange="updateHaulingStatus(${h.id}, this.value)" style="font-size:12px;padding:4px">
            ${["SCHEDULED", "LOADING", "IN TRANSIT", "COMPLETED", "CANCELLED"].map(s => `<option value="${s}" ${h.status === s ? "selected" : ""}>${s}</option>`).join("")}
          </select>
          <button class="btn small danger" onclick="deleteHauling(${h.id})">Delete</button>
        </td></tr>`).join("")
        : `<tr><td colspan="8" style="color:var(--muted)">No hauling trips scheduled.</td></tr>`}
    </tbody></table></div>`;
  }
  document.getElementById("newHaulBtn").addEventListener("click", async () => {
    const vehicles = await api("/vehicles");
    const vehOpts = `<option value="">— Unassigned —</option>` + vehicles.map(v => `<option value="${v.id}">${esc(v.plate_number)}</option>`).join("");
    const locOpts = state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
    openModal("Schedule Hauling", `
      <div class="field"><label>Scheduled Date</label><input type="date" id="hDate"></div>
      <div class="field"><label>Plant Name</label><input id="hPlant" placeholder="e.g. Solane Plant"></div>
      <div class="field"><label>Vehicle</label><select id="hVeh">${vehOpts}</select></div>
      <div class="field"><label>Driver</label><input id="hDriver"></div>
      <div class="field"><label>Destination Location</label><select id="hDest">${locOpts}</select></div>
      <div class="field"><label>Products</label>
        <div id="hItemRows"></div>
        <button class="btn small" id="hAddItemBtn" type="button">+ Add Product</button>
      </div>
      <div class="field"><label>Notes</label><input id="hNotes"></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="hSaveBtn">Save</button></div>
    `, () => {
      const itemRows = document.getElementById("hItemRows");
      const prodOpts = state.products.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
      function addItemRow() {
        const row = document.createElement("div");
        row.className = "pay-row";
        row.innerHTML = `
          <select class="hItemProd" style="flex:1">${prodOpts}</select>
          <input type="number" class="hItemQty" placeholder="Qty" value="1" min="1" style="width:80px">
          <button class="btn small danger" type="button">&times;</button>`;
        row.querySelector("button").addEventListener("click", () => row.remove());
        itemRows.appendChild(row);
      }
      document.getElementById("hAddItemBtn").addEventListener("click", addItemRow);
      addItemRow();

      document.getElementById("hSaveBtn").addEventListener("click", async () => {
        const scheduled_date = document.getElementById("hDate").value;
        const plant_name = document.getElementById("hPlant").value.trim();
        const items = [...itemRows.querySelectorAll(".pay-row")].map(r => ({
          product_id: r.querySelector(".hItemProd").value,
          qty: r.querySelector(".hItemQty").value,
        })).filter(i => parseInt(i.qty) > 0);
        if (!scheduled_date || !plant_name) { toast("Date and plant name are required", "error"); return; }
        if (!items.length) { toast("Add at least one product", "error"); return; }
        try {
          await api("/hauling", { method: "POST", body: {
            scheduled_date, plant_name, vehicle_id: document.getElementById("hVeh").value || null,
            driver: document.getElementById("hDriver").value, destination_location_id: document.getElementById("hDest").value,
            items, notes: document.getElementById("hNotes").value,
          }});
          closeModal(); toast("Hauling trip scheduled", "success"); load();
        } catch (e) {}
      });
    });
  });
  window.updateHaulingStatus = async (id, status) => {
    try {
      await api(`/hauling/${id}`, { method: "PUT", body: { status } });
      toast("Status updated", "success"); load();
    } catch (e) {}
  };
  window.deleteHauling = async (id) => {
    if (!confirm("Delete this hauling schedule entry?")) return;
    try { await api(`/hauling/${id}`, { method: "DELETE" }); toast("Deleted", "success"); load(); }
    catch (e) {}
  };
  load();
}

// ================================================================== EMPLOYEES
async function renderEmployees(c) {
  c.innerHTML = `<div style="margin-bottom:12px"><button class="btn green" id="newEmpBtn">+ New Employee</button></div><div id="empTable">Loading...</div>`;
  async function load() {
    const rows = await api("/employees");
    document.getElementById("empTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Name</th><th>Position</th><th>Location</th><th>Contact</th><th>Hire Date</th><th>Status</th><th></th>
    </tr></thead><tbody>
      ${rows.length ? rows.map(e => `<tr><td>${esc(e.name)}</td><td>${esc(e.position || "")}</td><td>${esc(e.location_name || "")}</td>
        <td>${esc(e.contact_number || "")}</td><td>${e.hire_date || ""}</td>
        <td><span class="badge ${e.status === 'ACTIVE' ? 'green' : e.status === 'ON LEAVE' ? 'warn' : 'gray'}">${e.status}</span></td>
        <td style="display:flex;gap:6px"><button class="btn small" onclick="editEmployee(${e.id})">Edit</button>
          <button class="btn small danger" onclick="deleteEmployee(${e.id})">Delete</button></td></tr>`).join("")
        : `<tr><td colspan="7" style="color:var(--muted)">No employees on file yet.</td></tr>`}
    </tbody></table></div>`;
  }
  function formFields(e = {}) {
    const locOpts = state.locations.map(l => `<option value="${l.id}" ${e.location_id === l.id ? "selected" : ""}>${esc(l.name)}</option>`).join("");
    return `
      <div class="field"><label>Name</label><input id="eName" value="${esc(e.name || "")}"></div>
      <div class="field"><label>Position</label><input id="ePosition" value="${esc(e.position || "")}"></div>
      <div class="field"><label>Location</label><select id="eLoc"><option value="">—</option>${locOpts}</select></div>
      <div class="field"><label>Contact Number</label><input id="eContact" value="${esc(e.contact_number || "")}"></div>
      <div class="field"><label>Hire Date</label><input type="date" id="eHire" value="${e.hire_date || ""}"></div>
      <div class="field"><label>Status</label><select id="eStatus">
        ${["ACTIVE", "ON LEAVE", "INACTIVE"].map(s => `<option value="${s}" ${e.status === s ? "selected" : ""}>${s}</option>`).join("")}
      </select></div>
      <div class="field"><label>Notes</label><input id="eNotes" value="${esc(e.notes || "")}"></div>
    `;
  }
  function readForm() {
    return {
      name: document.getElementById("eName").value.trim(), position: document.getElementById("ePosition").value,
      location_id: document.getElementById("eLoc").value || null, contact_number: document.getElementById("eContact").value,
      hire_date: document.getElementById("eHire").value, status: document.getElementById("eStatus").value,
      notes: document.getElementById("eNotes").value,
    };
  }
  document.getElementById("newEmpBtn").addEventListener("click", () => {
    openModal("New Employee", formFields() + `<div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn green" id="eSaveBtn">Save</button></div>`, () => {
      document.getElementById("eSaveBtn").addEventListener("click", async () => {
        const body = readForm();
        if (!body.name) { toast("Name is required", "error"); return; }
        try { await api("/employees", { method: "POST", body }); closeModal(); toast("Employee added", "success"); load(); }
        catch (e) {}
      });
    });
  });
  window.editEmployee = async (id) => {
    const e = (await api("/employees")).find(x => x.id === id);
    openModal("Edit Employee", formFields(e) + `<div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn green" id="eSaveBtn">Save Changes</button></div>`, () => {
      document.getElementById("eSaveBtn").addEventListener("click", async () => {
        try { await api(`/employees/${id}`, { method: "PUT", body: readForm() }); closeModal(); toast("Employee updated", "success"); load(); }
        catch (e) {}
      });
    });
  };
  window.deleteEmployee = async (id) => {
    if (!confirm("Delete this employee?")) return;
    try { await api(`/employees/${id}`, { method: "DELETE" }); toast("Employee deleted", "success"); load(); }
    catch (e) {}
  };
  load();
}

// ================================================================== STAFF SCHEDULE
async function renderStaffSchedule(c) {
  const canEdit = state.user.role === "ADMIN" || state.user.role === "MANAGER";
  c.innerHTML = `
    <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
      <input type="date" id="schedFrom"><input type="date" id="schedTo">
      <button class="btn" id="schedFilterBtn">Filter</button>
      ${canEdit ? `<button class="btn green" id="newSchedBtn">+ Add Shift</button>` : ""}
    </div>
    <div id="schedTable">Loading...</div>`;
  async function load() {
    const from = document.getElementById("schedFrom").value, to = document.getElementById("schedTo").value;
    const qs = new URLSearchParams(); if (from) qs.set("from", from); if (to) qs.set("to", to);
    const rows = await api("/staff-schedule?" + qs.toString());
    document.getElementById("schedTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Date</th><th>Employee</th><th>Shift</th><th>Location</th><th>Notes</th>${canEdit ? "<th></th>" : ""}
    </tr></thead><tbody>
      ${rows.length ? rows.map(s => `<tr><td>${s.shift_date}</td><td>${esc(s.employee_name)}</td>
        <td>${esc(s.shift_start || "")} – ${esc(s.shift_end || "")}</td><td>${esc(s.location_name || "")}</td><td>${esc(s.notes || "")}</td>
        ${canEdit ? `<td><button class="btn small danger" onclick="deleteSchedule(${s.id})">Remove</button></td>` : ""}</tr>`).join("")
        : `<tr><td colspan="${canEdit ? 6 : 5}" style="color:var(--muted)">No shifts scheduled in this range.</td></tr>`}
    </tbody></table></div>`;
  }
  document.getElementById("schedFilterBtn").addEventListener("click", load);
  if (canEdit) {
    document.getElementById("newSchedBtn").addEventListener("click", async () => {
      const employees = await api("/employees");
      const empOpts = employees.map(e => `<option value="${e.id}">${esc(e.name)}</option>`).join("");
      const locOpts = state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
      openModal("Add Shift", `
        <div class="field"><label>Employee</label><select id="sEmp">${empOpts}</select></div>
        <div class="field"><label>Date</label><input type="date" id="sDate"></div>
        <div class="field"><label>Shift Start</label><input type="time" id="sStart"></div>
        <div class="field"><label>Shift End</label><input type="time" id="sEnd"></div>
        <div class="field"><label>Location</label><select id="sLoc">${locOpts}</select></div>
        <div class="field"><label>Notes</label><input id="sNotes"></div>
        <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
          <button class="btn green" id="sSaveBtn">Save</button></div>
      `, () => {
        document.getElementById("sSaveBtn").addEventListener("click", async () => {
          const shift_date = document.getElementById("sDate").value;
          if (!shift_date) { toast("Date is required", "error"); return; }
          try {
            await api("/staff-schedule", { method: "POST", body: {
              employee_id: document.getElementById("sEmp").value, shift_date,
              shift_start: document.getElementById("sStart").value, shift_end: document.getElementById("sEnd").value,
              location_id: document.getElementById("sLoc").value, notes: document.getElementById("sNotes").value,
            }});
            closeModal(); toast("Shift added", "success"); load();
          } catch (e) {}
        });
      });
    });
    window.deleteSchedule = async (id) => {
      try { await api(`/staff-schedule/${id}`, { method: "DELETE" }); toast("Shift removed", "success"); load(); }
      catch (e) {}
    };
  }
  load();
}

// ================================================================== EMPLOYEE KPI
async function renderKpi(c) {
  c.innerHTML = `
    <div style="display:flex;gap:8px;margin-bottom:12px">
      <input type="date" id="kpiFrom"><input type="date" id="kpiTo">
      <button class="btn" id="kpiFilterBtn">Filter</button>
    </div>
    <p style="color:var(--muted);font-size:13px">Built from actual recorded sales per cashier — no separate data entry needed.</p>
    <div id="kpiTable">Loading...</div>`;
  async function load() {
    const from = document.getElementById("kpiFrom").value, to = document.getElementById("kpiTo").value;
    const qs = new URLSearchParams(); if (from) qs.set("from", from); if (to) qs.set("to", to);
    const rows = await api("/reports/employee-kpi?" + qs.toString());
    document.getElementById("kpiTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Employee</th><th>Role</th><th>Location</th><th>Transactions</th><th>Total Sales</th><th>Avg Sale Value</th><th>Items Sold</th>
    </tr></thead><tbody>
      ${rows.map(r => `<tr><td>${esc(r.name)}</td><td>${r.role}</td><td>${esc(r.location_name || "")}</td>
        <td>${r.transaction_count}</td><td>${money(r.total_sales)}</td><td>${money(r.avg_sale_value)}</td><td>${r.total_items_sold}</td></tr>`).join("")}
    </tbody></table></div>`;
  }
  document.getElementById("kpiFilterBtn").addEventListener("click", load);
  load();
}

// ================================================================== BOOKED ORDERS
async function renderBookedOrders(c) {
  const role = state.user.role;
  const canCreate = ["ADMIN", "MANAGER", "ORDER_TAKER"].includes(role);
  const canAssign = canCreate;
  const isAttendant = !["ADMIN", "MANAGER", "ORDER_TAKER"].includes(role);
  const publicUrl = `${location.origin}/order`;
  c.innerHTML = `
    ${canCreate ? `<div class="card" style="margin-bottom:14px;padding:12px 16px">
      <div style="font-size:12.5px;color:var(--muted);margin-bottom:4px">Share this link so customers can place orders themselves — they'll land here as unassigned for you to route:</div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <code style="background:var(--bg);padding:6px 10px;border-radius:6px;font-size:13px">${esc(publicUrl)}</code>
        <button class="btn small" id="copyLinkBtn">Copy Link</button>
      </div>
    </div>` : ""}
    <div style="margin-bottom:12px">${canCreate ? `<button class="btn green" id="newOrderBtn">+ Book Order</button>` : ""}</div>
    <div id="ordTable">Loading...</div>`;
  if (canCreate) {
    document.getElementById("copyLinkBtn").addEventListener("click", () => {
      navigator.clipboard.writeText(publicUrl).then(() => toast("Link copied", "success"));
    });
  }
  const statusColor = s => ({ PENDING: "warn", FULFILLED: "green", CANCELLED: "danger" }[s] || "gray");
  async function load() {
    const rows = await api("/booked-orders");
    document.getElementById("ordTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Order #</th><th>Date</th><th>Customer</th><th>Source</th><th>Location</th><th>Items</th><th>Booked By</th><th>Status</th><th></th>
    </tr></thead><tbody>
      ${rows.length ? rows.map(o => `<tr>
        <td>${o.order_no}</td><td>${o.order_date}</td>
        <td>${esc(o.customer_name || o.customer_name_freeform || "—")}${o.customer_phone ? " (" + esc(o.customer_phone) + ")" : ""}
          ${o.delivery_address ? `<br><span style="color:var(--muted);font-size:11.5px">${esc(o.delivery_address)}</span>` : ""}</td>
        <td><span class="badge ${o.source === 'CUSTOMER' ? 'warn' : 'blue'}">${o.source === 'CUSTOMER' ? 'Online' : 'Staff'}</span></td>
        <td>${o.showroom_name ? esc(o.showroom_name) : '<span class="badge danger">Unassigned</span>'}</td>
        <td>${o.items.map(i => `${esc(i.product_name)} x${i.qty}`).join(", ")}</td>
        <td>${esc(o.created_by_name || "—")}</td>
        <td><span class="badge ${statusColor(o.status)}">${o.status}</span></td>
        <td style="display:flex;gap:6px;flex-wrap:wrap">
          ${canAssign && o.status === "PENDING" ? `<button class="btn small" onclick="assignOrderLocation(${o.id}, ${o.showroom_location_id || 'null'})">${o.showroom_location_id ? 'Reassign' : 'Assign'}</button>` : ""}
          ${o.status === "PENDING" ? (isAttendant
              ? `<button class="btn small green" onclick="updateOrderStatus(${o.id}, 'FULFILLED')">Mark Fulfilled</button>`
              : `<button class="btn small green" onclick="updateOrderStatus(${o.id}, 'FULFILLED')">Fulfill</button>
                 <button class="btn small danger" onclick="updateOrderStatus(${o.id}, 'CANCELLED')">Cancel</button>`) : ""}
        </td>
      </tr>`).join("") : `<tr><td colspan="9" style="color:var(--muted)">No booked orders${isAttendant ? " for your location" : ""} yet.</td></tr>`}
    </tbody></table></div>`;
  }
  window.updateOrderStatus = async (id, status) => {
    try { await api(`/booked-orders/${id}`, { method: "PUT", body: { status } }); toast(`Order ${status.toLowerCase()}`, "success"); load(); }
    catch (e) {}
  };
  window.assignOrderLocation = (id, currentLocationId) => {
    const locOpts = state.locations.map(l => `<option value="${l.id}" ${l.id === currentLocationId ? "selected" : ""}>${esc(l.name)} (${l.type === "WAREHOUSE" ? "Warehouse" : "Showroom"})</option>`).join("");
    openModal("Assign Order to Location", `
      <div class="field"><label>Warehouse / Showroom</label><select id="assignLoc">${locOpts}</select></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="assignSaveBtn">Assign</button></div>
    `, () => {
      document.getElementById("assignSaveBtn").addEventListener("click", async () => {
        try {
          await api(`/booked-orders/${id}`, { method: "PUT", body: { showroom_location_id: document.getElementById("assignLoc").value } });
          closeModal(); toast("Order assigned", "success"); load();
        } catch (e) {}
      });
    });
  };
  if (canCreate) {
    document.getElementById("newOrderBtn").addEventListener("click", async () => {
      const customers = await api("/customers?q=");
      const custOptions = `<option value="">-- No account / phone-in order --</option>` +
        customers.map(cu => `<option value="${cu.id}">${esc(cu.name)} (${cu.account_no})</option>`).join("");
      const locationOptions = `<option value="">-- Leave unassigned for now --</option>` + state.locations
        .map(l => `<option value="${l.id}">${esc(l.name)} (${l.type === "WAREHOUSE" ? "Warehouse" : "Showroom"})</option>`).join("");
      const prodOptions = state.products.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
      openModal("Book Order", `
        <div class="field"><label>Existing Customer (optional)</label><select id="oCust">${custOptions}</select></div>
        <div class="field"><label>Or Caller Name</label><input id="oCallerName" placeholder="If no account on file"></div>
        <div class="field"><label>Phone Number</label><input id="oPhone"></div>
        <div class="field"><label>Delivery Address</label><input id="oAddress"></div>
        <div class="field"><label>Assign to Location</label><select id="oShowroom">${locationOptions}</select></div>
        <div class="field"><label>Products</label>
          <div id="oItemRows"></div>
          <button class="btn small" id="oAddItemBtn" type="button">+ Add Product</button>
        </div>
        <div class="field"><label>Notes</label><input id="oNotes"></div>
        <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
          <button class="btn green" id="oSaveBtn">Book Order</button></div>
      `, () => {
        const itemRows = document.getElementById("oItemRows");
        function addItemRow() {
          const row = document.createElement("div");
          row.className = "pay-row";
          row.innerHTML = `
            <select class="oItemProd" style="flex:1">${prodOptions}</select>
            <input type="number" class="oItemQty" placeholder="Qty" value="1" min="1" style="width:80px">
            <button class="btn small danger" type="button">&times;</button>`;
          row.querySelector("button").addEventListener("click", () => row.remove());
          itemRows.appendChild(row);
        }
        document.getElementById("oAddItemBtn").addEventListener("click", addItemRow);
        addItemRow();

        document.getElementById("oSaveBtn").addEventListener("click", async () => {
          const items = [...itemRows.querySelectorAll(".pay-row")].map(r => ({
            product_id: r.querySelector(".oItemProd").value,
            qty: r.querySelector(".oItemQty").value,
          })).filter(i => parseInt(i.qty) > 0);
          if (!items.length) { toast("Add at least one product", "error"); return; }
          try {
            await api("/booked-orders", { method: "POST", body: {
              customer_id: document.getElementById("oCust").value || null,
              customer_name_freeform: document.getElementById("oCallerName").value,
              customer_phone: document.getElementById("oPhone").value,
              delivery_address: document.getElementById("oAddress").value,
              showroom_location_id: document.getElementById("oShowroom").value || null,
              items, notes: document.getElementById("oNotes").value,
            }});
            closeModal(); toast("Order booked", "success"); load();
          } catch (e) {}
        });
      });
    });
  }
  load();
}

// ================================================================== INCIDENT REPORTS
async function renderIncidentReports(c) {
  const canResolve = state.user.role === "ADMIN" || state.user.role === "MANAGER";
  c.innerHTML = `<div style="margin-bottom:12px"><button class="btn green" id="newIncBtn">+ Report Incident</button></div>
    <div id="incTable">Loading...</div>`;
  const sevColor = s => ({ LOW: "blue", MEDIUM: "warn", HIGH: "danger", CRITICAL: "danger" }[s] || "gray");
  async function load() {
    const rows = await api("/incident-reports");
    document.getElementById("incTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Report #</th><th>Date</th><th>Location</th><th>Reported By</th><th>Category</th><th>Severity</th><th>Description</th><th>Files</th><th>Status</th><th></th>
    </tr></thead><tbody>
      ${rows.length ? rows.map(r => `<tr><td>${r.report_no}</td><td>${r.ts}</td><td>${esc(r.location_name || "")}</td>
        <td>${esc(r.reporter_name || "")}</td><td>${esc(r.category || "")}</td>
        <td><span class="badge ${sevColor(r.severity)}">${r.severity}</span></td><td>${esc(r.description)}</td>
        <td>${r.attachment_count > 0 ? `<span class="badge blue">${r.attachment_count} \u{1F4CE}</span>` : "—"}</td>
        <td><span class="badge ${r.status === 'OPEN' ? 'warn' : 'green'}">${r.status}</span></td>
        <td style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn small" onclick="openIncidentDetail(${r.id})">View</button>
          <button class="btn small" onclick="printIncidentReport(${r.id})">Print</button>
          ${canResolve && r.status === 'OPEN' ? `<button class="btn small green" onclick="resolveIncident(${r.id})">Resolve</button>` : ""}
        </td>
      </tr>`).join("") : `<tr><td colspan="10" style="color:var(--muted)">No incidents reported.</td></tr>`}
    </tbody></table></div>`;
  }
  document.getElementById("newIncBtn").addEventListener("click", () => {
    const locOpts = state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
    const isAdminOrMgr = state.user.role === "ADMIN" || state.user.role === "MANAGER";
    openModal("Report Incident", `
      ${isAdminOrMgr ? `<div class="field"><label>Location</label><select id="iLoc">${locOpts}</select></div>` : ""}
      <div class="field"><label>Category</label><select id="iCat">
        <option>Safety</option><option>Equipment</option><option>Customer Complaint</option><option>Inventory Discrepancy</option><option>Other</option>
      </select></div>
      <div class="field"><label>Severity</label><select id="iSev">
        <option value="LOW">Low</option><option value="MEDIUM">Medium</option><option value="HIGH">High</option><option value="CRITICAL">Critical</option>
      </select></div>
      <div class="field"><label>Description</label><textarea id="iDesc" rows="4" style="width:100%"></textarea></div>
      <div class="field"><label>Attach Files (optional — photos, PDF, Excel, etc.)</label><input type="file" id="iFiles" multiple accept=".png,.jpg,.jpeg,.gif,.pdf,.xls,.xlsx,.csv,.doc,.docx"></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="iSaveBtn">Submit Report</button></div>
    `, () => {
      document.getElementById("iSaveBtn").addEventListener("click", async () => {
        const description = document.getElementById("iDesc").value.trim();
        if (!description) { toast("Description is required", "error"); return; }
        try {
          const body = { category: document.getElementById("iCat").value, severity: document.getElementById("iSev").value, description };
          if (isAdminOrMgr) body.location_id = document.getElementById("iLoc").value;
          const result = await api("/incident-reports", { method: "POST", body });
          const files = document.getElementById("iFiles").files;
          if (files.length) {
            const fd = new FormData();
            for (const f of files) fd.append("files", f);
            const res = await fetch(`/api/incident-reports/${result.id}/attachments`, { method: "POST", body: fd, credentials: "include" });
            const uploadResult = await res.json();
            if (uploadResult.errors && uploadResult.errors.length) toast(uploadResult.errors.join("; "), "error");
          }
          closeModal(); toast("Incident reported", "success"); load();
        } catch (e) {}
      });
    });
  });
  window.resolveIncident = (id) => {
    openModal("Resolve Incident", `
      <div class="field"><label>Resolution Notes</label><textarea id="rNotes" rows="4" style="width:100%"></textarea></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="rResolveBtn">Mark Resolved</button></div>
    `, () => {
      document.getElementById("rResolveBtn").addEventListener("click", async () => {
        try {
          await api(`/incident-reports/${id}/resolve`, { method: "PUT", body: { resolution_notes: document.getElementById("rNotes").value } });
          closeModal(); toast("Incident resolved", "success"); load();
        } catch (e) {}
      });
    });
  };

  function fileSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }
  const PREVIEWABLE_EXT = ["png", "jpg", "jpeg", "gif", "pdf"];

  window.openIncidentDetail = async (id) => {
    const report = await api(`/incident-reports/${id}`);
    const canManage = report.reported_by === state.user.id || canResolve;
    async function renderAttachments() {
      const attachments = await api(`/incident-reports/${id}/attachments`);
      const box = document.getElementById("attachList");
      if (!box) return;
      box.innerHTML = attachments.length ? attachments.map(a => {
        const ext = a.filename.split(".").pop().toLowerCase();
        const canPreview = PREVIEWABLE_EXT.includes(ext);
        return `<div style="display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid var(--border)">
          <div style="font-size:13px">
            ${esc(a.filename)} <span style="color:var(--muted);font-size:11.5px">(${fileSize(a.size_bytes)}, by ${esc(a.uploaded_by_name || "?")})</span>
          </div>
          <div style="display:flex;gap:6px">
            <a class="btn small" href="/api/incident-attachments/${a.id}" target="_blank">${canPreview ? "Preview" : "Download"}</a>
            ${canResolve ? `<button class="btn small danger" onclick="deleteIncidentAttachment(${id}, ${a.id})">Delete</button>` : ""}
          </div>
        </div>`;
      }).join("") : `<p style="color:var(--muted);font-size:13px">No attachments yet.</p>`;
    }
    openModal(`${report.report_no}`, `
      <div class="field"><b>Location:</b> ${esc(report.location_name || "")} &nbsp; <b>Reported by:</b> ${esc(report.reporter_name || "")}</div>
      <div class="field"><b>Date:</b> ${report.ts} &nbsp; <b>Category:</b> ${esc(report.category || "")} &nbsp;
        <span class="badge ${sevColor(report.severity)}">${report.severity}</span></div>
      <div class="field"><b>Description:</b><br>${esc(report.description)}</div>
      <div class="field"><span class="badge ${report.status === 'OPEN' ? 'warn' : 'green'}">${report.status}</span></div>
      ${report.status === 'RESOLVED' ? `<div class="field"><b>Resolution:</b><br>${esc(report.resolution_notes || "")}
        <br><span style="color:var(--muted);font-size:12px">By ${esc(report.resolver_name || "")} on ${report.resolved_at}</span></div>` : ""}
      <h3 style="margin:16px 0 8px;font-size:14px">Attachments</h3>
      <div id="attachList">Loading...</div>
      ${canManage ? `<div class="field" style="margin-top:10px">
        <input type="file" id="addFiles" multiple accept=".png,.jpg,.jpeg,.gif,.pdf,.xls,.xlsx,.csv,.doc,.docx">
        <button class="btn small" id="addFilesBtn" style="margin-top:6px">Upload</button>
      </div>` : ""}
      <div class="modal-actions">
        <button class="btn" onclick="closeModal()">Close</button>
        <button class="btn" onclick="printIncidentReport(${id})">Print</button>
      </div>
    `, () => {
      renderAttachments();
      if (canManage) {
        document.getElementById("addFilesBtn").addEventListener("click", async () => {
          const files = document.getElementById("addFiles").files;
          if (!files.length) { toast("Choose file(s) first", "error"); return; }
          const fd = new FormData();
          for (const f of files) fd.append("files", f);
          const res = await fetch(`/api/incident-reports/${id}/attachments`, { method: "POST", body: fd, credentials: "include" });
          const result = await res.json();
          if (result.errors && result.errors.length) toast(result.errors.join("; "), "error");
          if (result.saved && result.saved.length) toast(`${result.saved.length} file(s) uploaded`, "success");
          renderAttachments();
        });
      }
    });
  };

  window.deleteIncidentAttachment = async (incidentId, attachmentId) => {
    if (!confirm("Delete this attachment?")) return;
    try {
      await api(`/incident-attachments/${attachmentId}`, { method: "DELETE" });
      toast("Attachment deleted", "success");
      openIncidentDetail(incidentId);
    } catch (e) {}
  };

  window.printIncidentReport = async (id) => {
    const report = await api(`/incident-reports/${id}`);
    const html = `
      <div style="max-width:640px;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:13px">
        <div style="text-align:center;margin-bottom:14px">
          <img src="/images/logo.png" alt="GUFC" style="width:56px;height:56px;object-fit:contain">
          <div style="font-weight:800;font-size:16px;margin-top:4px">GREAT UNLI FOOD CORP.</div>
          <div style="color:#6b7785">Incident Report</div>
        </div>
        <table style="width:100%;border-collapse:collapse;margin-bottom:14px">
          <tr><td style="padding:4px 0;width:140px"><b>Report #</b></td><td>${report.report_no}</td></tr>
          <tr><td style="padding:4px 0"><b>Date</b></td><td>${report.ts}</td></tr>
          <tr><td style="padding:4px 0"><b>Location</b></td><td>${esc(report.location_name || "")}</td></tr>
          <tr><td style="padding:4px 0"><b>Reported By</b></td><td>${esc(report.reporter_name || "")}</td></tr>
          <tr><td style="padding:4px 0"><b>Category</b></td><td>${esc(report.category || "")}</td></tr>
          <tr><td style="padding:4px 0"><b>Severity</b></td><td>${report.severity}</td></tr>
          <tr><td style="padding:4px 0"><b>Status</b></td><td>${report.status}</td></tr>
        </table>
        <div style="margin-bottom:14px"><b>Description</b><br>${esc(report.description).replace(/\n/g, "<br>")}</div>
        ${report.status === 'RESOLVED' ? `<div style="margin-bottom:14px"><b>Resolution</b><br>${esc(report.resolution_notes || "").replace(/\n/g, "<br>")}
          <br><span style="color:#6b7785;font-size:12px">Resolved by ${esc(report.resolver_name || "")} on ${report.resolved_at}</span></div>` : ""}
        <div style="margin-top:24px;color:#6b7785;font-size:11px;text-align:center">Printed ${new Date().toLocaleString()}</div>
      </div>`;
    document.getElementById("receiptArea").innerHTML = html;
    setTimeout(() => window.print(), 200);
  };

  load();
}

// ================================================================== MANAGE ROLES
async function renderRoles(c) {
  c.innerHTML = `<div style="margin-bottom:12px;display:flex;gap:8px">
    <input type="text" id="newRoleName" placeholder="New role name (e.g. Senior Cashier)" style="flex:1">
    <button class="btn green" id="addRoleBtn">+ Create Role</button>
  </div><div id="rolesTable">Loading...</div>
  <p style="color:var(--muted);font-size:12.5px;margin-top:14px">
    Note: Users, Products, and Locations management always stay restricted to Admin
    (and Manager, for Users) regardless of these settings — custom roles can only be
    granted access to the operational and reporting modules listed below.
  </p>`;
  async function load() {
    const roles = await api("/roles");
    document.getElementById("rolesTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Role</th><th>Type</th><th></th></tr></thead><tbody>
      ${roles.map(r => `<tr><td>${esc(r.name)}</td><td><span class="badge ${r.is_builtin ? 'blue' : 'gray'}">${r.is_builtin ? "Built-in" : "Custom"}</span></td>
        <td style="display:flex;gap:6px">
          <button class="btn small" onclick="editRolePermissions('${esc(r.name).replace(/'/g, "\\'")}', ${r.name === 'ADMIN'})">${r.name === 'ADMIN' ? 'View Access' : 'Edit Modules'}</button>
          ${!r.is_builtin ? `<button class="btn small danger" onclick="deleteRole('${esc(r.name).replace(/'/g, "\\'")}')">Delete</button>` : ""}
        </td></tr>`).join("")}
    </tbody></table></div>`;
  }
  document.getElementById("addRoleBtn").addEventListener("click", async () => {
    const name = document.getElementById("newRoleName").value.trim();
    if (!name) { toast("Enter a role name", "error"); return; }
    try {
      await api("/roles", { method: "POST", body: { name } });
      document.getElementById("newRoleName").value = ""; toast("Role created", "success"); load();
    } catch (e) {}
  });
  window.deleteRole = async (name) => {
    if (!confirm(`Delete the role "${name}"? This only works if no users currently have it.`)) return;
    try { await api(`/roles/${encodeURIComponent(name)}`, { method: "DELETE" }); toast("Role deleted", "success"); load(); }
    catch (e) {}
  };
  window.editRolePermissions = async (name, locked) => {
    const data = await api(`/roles/${encodeURIComponent(name)}/permissions`);
    const labels = {
      dashboard: "Dashboard", pos: "POS", sales: "Sales", customers: "Customers (view)",
      receivables: "Receivables", inventory: "Inventory", inventory_ledger: "Inventory Ledger",
      cylinder_deposits: "Cylinder Deposits", transfers: "Dispatch / Transfers", hauling: "Hauling Schedule",
      vehicles: "Vehicles", employees: "Employees", staff_schedule: "Staff Schedule", kpi: "Employee KPI",
      reports: "Reports", audit: "Audit Log", booked_orders: "Booked Orders", incident_reports: "Incident Reports",
    };
    openModal(`${locked ? "View" : "Edit"} Modules — ${name}`, `
      ${locked ? `<p style="color:var(--muted);font-size:13px">Admin always has full access to every module — this can't be changed.</p>` : ""}
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px 12px">
        ${data.all_modules.map(m => `<label style="display:flex;align-items:center;gap:6px;font-size:13.5px;font-weight:400">
          <input type="checkbox" class="modChk" value="${m}" ${data.module_keys.includes(m) ? "checked" : ""} ${locked ? "disabled" : ""} style="width:auto">
          ${esc(labels[m] || m)}</label>`).join("")}
      </div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">${locked ? "Close" : "Cancel"}</button>
        ${locked ? "" : `<button class="btn green" id="permSaveBtn">Save</button>`}</div>
    `, () => {
      if (locked) return;
      document.getElementById("permSaveBtn").addEventListener("click", async () => {
        const checked = [...document.querySelectorAll(".modChk:checked")].map(cb => cb.value);
        try {
          await api(`/roles/${encodeURIComponent(name)}/permissions`, { method: "PUT", body: { module_keys: checked } });
          closeModal(); toast("Permissions updated", "success"); load();
        } catch (e) {}
      });
    });
  };
  load();
}

// ================================================================== COMMERCIAL 50kg BILLING
async function renderCommercialDeliveries(c) {
  const role = state.user.role;
  const canCreate = ["ADMIN", "MANAGER", "DISPATCHER"].includes(role);
  const canBill = role === "ADMIN" || role === "MANAGER";
  c.innerHTML = `<div style="margin-bottom:12px">${canCreate ? `<button class="btn green" id="newDeliveryBtn">+ New Commercial Billing</button>` : ""}</div>
    <div id="cdTable">Loading...</div>`;
  const statusColor = s => ({ DELIVERED: "warn", PICKED_UP: "blue", BILLED: "green", CANCELLED: "danger" }[s] || "gray");
  async function load() {
    const rows = await api("/commercial-deliveries");
    document.getElementById("cdTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Delivery #</th><th>Date</th><th>Customer</th><th>Location</th><th>Type</th><th>Product</th>
      <th>Detail</th><th>Consumed</th><th>Billed</th><th>Status</th><th></th>
    </tr></thead><tbody>
      ${rows.length ? rows.map(d => `<tr>
        <td>${d.delivery_no}</td><td>${d.delivery_date}</td>
        <td>${esc(d.customer_name)} (${d.account_no})</td><td>${esc(d.location_name)}</td>
        <td><span class="badge ${d.billing_type === 'WIWO' ? 'blue' : 'warn'}">${d.billing_type}</span></td>
        <td>${esc(d.product_name)}</td>
        <td>${d.billing_type === 'WIWO' ? `${d.cylinders.length} cylinder(s), ${d.total_net_weight_in}kg in`
              : `${esc(d.meter_label || 'meter')}: ${d.previous_reading} \u2192 ${d.current_reading}`}</td>
        <td>${d.total_consumption_weight !== null ? d.total_consumption_weight + (d.billing_type === 'WIWO' ? ' kg' : '') : '—'}</td>
        <td>${d.billed_amount ? money(d.billed_amount) : '—'}</td>
        <td><span class="badge ${statusColor(d.status)}">${d.status}</span></td>
        <td style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn small" onclick="viewCommercialDelivery(${d.id})">View</button>
          <button class="btn small" onclick="printCommercialDelivery(${d.id})">Print</button>
          <a class="btn small" href="/api/commercial-deliveries/${d.id}/export/pdf">PDF</a>
          ${d.status === 'DELIVERED' && d.billing_type === 'WIWO' && canCreate ? `<button class="btn small" onclick="openRecordPickup(${d.id})">Record Pickup</button>` : ""}
          ${d.status === 'PICKED_UP' && canBill ? `<button class="btn small green" onclick="openBillDelivery(${d.id}, ${d.total_consumption_weight})">Generate Invoice</button>` : ""}
          ${d.status !== 'BILLED' && d.status !== 'CANCELLED' && canCreate ? `<button class="btn small danger" onclick="cancelCommercialDelivery(${d.id})">Cancel</button>` : ""}
        </td>
      </tr>`).join("") : `<tr><td colspan="11" style="color:var(--muted)">No commercial billing records yet.</td></tr>`}
    </tbody></table></div>`;
  }

  if (canCreate) {
    document.getElementById("newDeliveryBtn").addEventListener("click", async () => {
      const customers = await api("/customers?q=");
      const custOpts = customers.map(cu => `<option value="${cu.id}">${esc(cu.name)} (${cu.account_no})</option>`).join("");
      const locOpts = state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
      const prodOpts = state.products.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
      const isAdminOrMgr = role === "ADMIN" || role === "MANAGER";
      openModal("New Commercial Billing", `
        <div class="field"><label>Billing Type</label><select id="cdType">
          <option value="WIWO">WIWO — Weight In / Weight Out (cylinders)</option>
          <option value="READING">READING — Meter Reading</option>
        </select></div>
        <div class="field"><label>Customer</label><select id="cdCust">${custOpts}</select></div>
        ${isAdminOrMgr ? `<div class="field"><label>Location</label><select id="cdLoc">${locOpts}</select></div>` : ""}
        <div class="field"><label>Product</label><select id="cdProd">${prodOpts}</select></div>

        <div id="wiwoFields">
          <div class="field"><label>Cylinders</label>
            <div id="cdCylRows"></div>
            <button class="btn small" id="cdAddCylBtn" type="button">+ Add Cylinder</button>
          </div>
        </div>
        <div id="readingFields" class="hidden">
          <div class="field"><label>Meter / Tank Label</label><input id="cdMeterLabel" placeholder="e.g. Bulk Tank A"></div>
          <div class="field"><label>Previous Reading</label><input type="number" step="0.01" id="cdPrevReading" placeholder="Auto-filled from last bill if available"></div>
          <div class="field"><label>Current Reading</label><input type="number" step="0.01" id="cdCurrReading"></div>
          <div class="field"><b>Consumption:</b> <span id="cdConsumptionPreview">—</span></div>
        </div>

        <div class="field"><label>Notes</label><input id="cdNotes"></div>
        <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
          <button class="btn green" id="cdSaveBtn">Save</button></div>
      `, () => {
        const cylRows = document.getElementById("cdCylRows");
        function addCylRow() {
          const row = document.createElement("div");
          row.className = "pay-row";
          row.innerHTML = `
            <input type="text" class="cdLabel" placeholder="Cylinder ID (optional)" style="width:110px">
            <input type="number" step="0.01" class="cdGW" placeholder="Gross Wt (kg)" style="width:100px">
            <input type="number" step="0.01" class="cdTW" placeholder="Tare Wt (kg)" style="width:100px">
            <span class="cdNW" style="width:75px;text-align:right;font-weight:600;color:var(--green-dark)">— kg</span>
            <button class="btn small danger" type="button">&times;</button>`;
          const recalc = () => {
            const gw = parseFloat(row.querySelector(".cdGW").value) || 0;
            const tw = parseFloat(row.querySelector(".cdTW").value) || 0;
            const nw = gw - tw;
            row.querySelector(".cdNW").textContent = (gw && tw) ? `${nw.toFixed(2)} kg` : "— kg";
          };
          row.querySelector(".cdGW").addEventListener("input", recalc);
          row.querySelector(".cdTW").addEventListener("input", recalc);
          row.querySelector("button").addEventListener("click", () => row.remove());
          cylRows.appendChild(row);
        }
        document.getElementById("cdAddCylBtn").addEventListener("click", addCylRow);
        addCylRow();

        function updateConsumptionPreview() {
          const prev = parseFloat(document.getElementById("cdPrevReading").value);
          const curr = parseFloat(document.getElementById("cdCurrReading").value);
          document.getElementById("cdConsumptionPreview").textContent =
            (!isNaN(prev) && !isNaN(curr)) ? (curr - prev).toFixed(2) : "—";
        }
        document.getElementById("cdPrevReading").addEventListener("input", updateConsumptionPreview);
        document.getElementById("cdCurrReading").addEventListener("input", updateConsumptionPreview);

        async function suggestPreviousReading() {
          const custId = document.getElementById("cdCust").value;
          if (!custId) return;
          try {
            const r = await api(`/commercial-deliveries/last-reading?customer_id=${custId}`);
            if (r.previous_reading !== null) {
              document.getElementById("cdPrevReading").value = r.previous_reading;
              updateConsumptionPreview();
            }
          } catch (e) {}
        }

        document.getElementById("cdType").addEventListener("change", (e) => {
          const isReading = e.target.value === "READING";
          document.getElementById("wiwoFields").classList.toggle("hidden", isReading);
          document.getElementById("readingFields").classList.toggle("hidden", !isReading);
          if (isReading) suggestPreviousReading();
        });
        document.getElementById("cdCust").addEventListener("change", () => {
          if (document.getElementById("cdType").value === "READING") suggestPreviousReading();
        });

        document.getElementById("cdSaveBtn").addEventListener("click", async () => {
          const billingType = document.getElementById("cdType").value;
          const base = {
            customer_id: document.getElementById("cdCust").value,
            location_id: isAdminOrMgr ? document.getElementById("cdLoc").value : undefined,
            product_id: document.getElementById("cdProd").value,
            billing_type: billingType,
            notes: document.getElementById("cdNotes").value,
          };
          if (billingType === "WIWO") {
            const cylinders = [...cylRows.querySelectorAll(".pay-row")].map(r => ({
              cylinder_label: r.querySelector(".cdLabel").value,
              gross_weight_in: r.querySelector(".cdGW").value,
              tare_weight: r.querySelector(".cdTW").value,
            })).filter(cy => cy.gross_weight_in && cy.tare_weight);
            if (!cylinders.length) { toast("Add at least one cylinder with weights", "error"); return; }
            base.cylinders = cylinders;
          } else {
            const prev = document.getElementById("cdPrevReading").value;
            const curr = document.getElementById("cdCurrReading").value;
            if (!prev || !curr) { toast("Enter both previous and current readings", "error"); return; }
            base.meter_label = document.getElementById("cdMeterLabel").value;
            base.previous_reading = prev;
            base.current_reading = curr;
          }
          try {
            await api("/commercial-deliveries", { method: "POST", body: base });
            closeModal(); toast("Billing record saved", "success"); load();
          } catch (e) {}
        });
      });
    });
  }

  window.viewCommercialDelivery = async (id) => {
    const d = await api(`/commercial-deliveries/${id}`);
    const detailHtml = d.billing_type === "WIWO" ? `
      <div class="table-wrap"><table><thead><tr>
        <th>Cylinder</th><th>GW In</th><th>Tare</th><th>NW In</th><th>GW Out</th><th>NW Out</th><th>Consumed</th>
      </tr></thead><tbody>
        ${d.cylinders.map(cy => `<tr><td>${esc(cy.cylinder_label || '—')}</td><td>${cy.gross_weight_in}</td>
          <td>${cy.tare_weight}</td><td>${cy.net_weight_in}</td><td>${cy.gross_weight_out ?? '—'}</td>
          <td>${cy.net_weight_out ?? '—'}</td><td>${cy.consumption_weight ?? '—'}</td></tr>`).join("")}
      </tbody></table></div>
    ` : `
      <div class="field"><b>Meter/Tank:</b> ${esc(d.meter_label || '—')}</div>
      <div class="field"><b>Previous Reading:</b> ${d.previous_reading} &nbsp; <b>Current Reading:</b> ${d.current_reading}</div>
      <div class="field"><b>Consumption:</b> ${d.total_consumption_weight}</div>
    `;
    openModal(`${d.delivery_no}`, `
      <div class="field"><b>Type:</b> <span class="badge ${d.billing_type === 'WIWO' ? 'blue' : 'warn'}">${d.billing_type}</span></div>
      <div class="field"><b>Customer:</b> ${esc(d.customer_name)} (${d.account_no})</div>
      <div class="field"><b>Location:</b> ${esc(d.location_name)} &nbsp; <b>Product:</b> ${esc(d.product_name)}</div>
      <div class="field"><b>Status:</b> <span class="badge ${statusColor(d.status)}">${d.status}</span></div>
      ${detailHtml}
      ${d.billed_amount ? `<div class="field" style="margin-top:10px"><b>Billed:</b> ${money(d.price_per_kg)}/unit x ${d.total_consumption_weight} = ${money(d.billed_amount)}</div>` : ""}
      <div class="modal-actions">
        <button class="btn" onclick="closeModal()">Close</button>
        <button class="btn" onclick="printCommercialDelivery(${id})">Print</button>
        <a class="btn green" href="/api/commercial-deliveries/${id}/export/pdf">Export PDF</a>
      </div>
    `);
  };

  window.openRecordPickup = async (id) => {
    const d = await api(`/commercial-deliveries/${id}`);
    openModal(`Record Pickup — ${d.delivery_no}`, `
      <p style="font-size:13px;color:var(--muted)">Enter the gross weight-out reading for each cylinder. Net residual and consumption are calculated automatically.</p>
      <div id="pickupRows">
        ${d.cylinders.map(cy => `<div class="pay-row" data-cyl-id="${cy.id}">
          <span style="width:90px">${esc(cy.cylinder_label || 'Cylinder ' + cy.id)}</span>
          <span style="width:70px;font-size:12px;color:var(--muted)">NW in: ${cy.net_weight_in}kg</span>
          <input type="number" step="0.01" class="pickupGW" placeholder="Gross Wt Out" style="width:110px">
          <span class="pickupCalc" style="width:110px;text-align:right;font-size:12.5px;color:var(--green-dark)">—</span>
        </div>`).join("")}
      </div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="pickupSaveBtn">Save Pickup</button></div>
    `, () => {
      document.querySelectorAll("#pickupRows .pay-row").forEach(row => {
        const cyl = d.cylinders.find(c => c.id === parseInt(row.dataset.cylId));
        row.querySelector(".pickupGW").addEventListener("input", (e) => {
          const gwOut = parseFloat(e.target.value) || 0;
          const nwOut = gwOut - cyl.tare_weight;
          const consumption = cyl.net_weight_in - nwOut;
          row.querySelector(".pickupCalc").textContent = gwOut ? `NW ${nwOut.toFixed(2)} | used ${consumption.toFixed(2)}kg` : "—";
        });
      });
      document.getElementById("pickupSaveBtn").addEventListener("click", async () => {
        const cylinders = [...document.querySelectorAll("#pickupRows .pay-row")].map(row => ({
          id: parseInt(row.dataset.cylId),
          gross_weight_out: row.querySelector(".pickupGW").value,
        })).filter(cy => cy.gross_weight_out);
        if (cylinders.length !== d.cylinders.length) { toast("Enter a weight-out reading for every cylinder", "error"); return; }
        try {
          await api(`/commercial-deliveries/${id}/pickup`, { method: "PUT", body: { cylinders } });
          closeModal(); toast("Pickup recorded", "success"); load();
        } catch (e) {}
      });
    });
  };

  window.openBillDelivery = async (id, totalConsumption) => {
    const products = state.products;
    const d = await api(`/commercial-deliveries/${id}`);
    const prod = products.find(p => p.id === d.product_id);
    const defaultRate = prod ? prod.price_per_kg : 0;
    const unitLabel = d.billing_type === "WIWO" ? "kg" : "unit(s)";
    openModal("Generate Invoice", `
      <div class="field"><b>Total Consumption:</b> ${totalConsumption} ${unitLabel}</div>
      <div class="field"><label>Price per ${d.billing_type === 'WIWO' ? 'KG' : 'Unit'}</label><input type="number" step="0.01" id="billRate" value="${defaultRate}"></div>
      <div class="field"><b>Estimated Total:</b> <span id="billTotal">${money(totalConsumption * defaultRate)}</span></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="billSaveBtn">Generate Invoice</button></div>
    `, () => {
      document.getElementById("billRate").addEventListener("input", (e) => {
        document.getElementById("billTotal").textContent = money(totalConsumption * (parseFloat(e.target.value) || 0));
      });
      document.getElementById("billSaveBtn").addEventListener("click", async () => {
        const rate = parseFloat(document.getElementById("billRate").value);
        if (!rate || rate <= 0) { toast(`Enter a valid price per ${unitLabel}`, "error"); return; }
        try {
          const result = await api(`/commercial-deliveries/${id}/bill`, { method: "POST", body: { price_per_kg: rate } });
          closeModal();
          toast(`Invoice ${result.sale_no} created for ${money(result.billed_amount)}`, "success");
          load();
        } catch (e) {}
      });
    });
  };

  window.cancelCommercialDelivery = async (id) => {
    if (!confirm("Cancel this commercial delivery?")) return;
    try { await api(`/commercial-deliveries/${id}/cancel`, { method: "POST" }); toast("Delivery cancelled", "success"); load(); }
    catch (e) {}
  };

  window.printCommercialDelivery = async (id) => {
    const d = await api(`/commercial-deliveries/${id}`);
    const detailRows = d.billing_type === "WIWO"
      ? `<table style="width:100%;border-collapse:collapse;font-size:12px;margin:10px 0">
          <tr style="border-bottom:1px solid #ccc"><th style="text-align:left">Cylinder</th><th>GW In</th><th>Tare</th><th>NW In</th><th>GW Out</th><th>NW Out</th><th>Consumed</th></tr>
          ${d.cylinders.map(cy => `<tr><td>${esc(cy.cylinder_label || '—')}</td><td>${cy.gross_weight_in}</td>
            <td>${cy.tare_weight}</td><td>${cy.net_weight_in}</td><td>${cy.gross_weight_out ?? '—'}</td>
            <td>${cy.net_weight_out ?? '—'}</td><td>${cy.consumption_weight ?? '—'}</td></tr>`).join("")}
        </table>`
      : `<div style="margin:10px 0">Meter/Tank: ${esc(d.meter_label || '—')}<br>
          Previous Reading: ${d.previous_reading} &nbsp; Current Reading: ${d.current_reading}</div>`;
    const html = `
      <div style="max-width:640px;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:13px">
        <div style="text-align:center;margin-bottom:14px">
          <img src="/images/logo.png" alt="GUFC" style="width:56px;height:56px;object-fit:contain">
          <div style="font-weight:800;font-size:16px;margin-top:4px">GREAT UNLI FOOD CORP.</div>
          <div style="color:#6b7785">Commercial LPG Billing — ${d.billing_type === 'WIWO' ? 'Weight In / Weight Out' : 'Meter Reading'}</div>
        </div>
        <table style="width:100%;border-collapse:collapse;margin-bottom:10px">
          <tr><td style="padding:4px 0;width:140px"><b>Delivery #</b></td><td>${d.delivery_no}</td></tr>
          <tr><td style="padding:4px 0"><b>Date</b></td><td>${d.delivery_date}</td></tr>
          <tr><td style="padding:4px 0"><b>Customer</b></td><td>${esc(d.customer_name)} (${d.account_no})</td></tr>
          <tr><td style="padding:4px 0"><b>Location</b></td><td>${esc(d.location_name)}</td></tr>
          <tr><td style="padding:4px 0"><b>Product</b></td><td>${esc(d.product_name)}</td></tr>
          <tr><td style="padding:4px 0"><b>Status</b></td><td>${d.status}</td></tr>
        </table>
        ${detailRows}
        <div style="margin-top:14px;font-size:14px">
          <b>Total Consumption:</b> ${d.total_consumption_weight ?? '—'} ${d.billing_type === 'WIWO' ? 'kg' : ''}<br>
          ${d.price_per_kg ? `<b>Rate:</b> ${money(d.price_per_kg)}<br>` : ""}
          ${d.billed_amount ? `<b style="color:#1e9e5a">Billed Amount: ${money(d.billed_amount)}</b>` : ""}
        </div>
        <div style="margin-top:24px;color:#6b7785;font-size:11px;text-align:center">Printed ${new Date().toLocaleString()}</div>
      </div>`;
    document.getElementById("receiptArea").innerHTML = html;
    setTimeout(() => window.print(), 200);
  };

  load();
}
