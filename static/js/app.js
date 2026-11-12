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
  { id: "eod_report", label: "End of Day Report", icon: "\u{1F4B5}", module: "eod_report" },
  { id: "booked_orders", label: "Booked Orders", icon: "\u{1F4CB}", module: "booked_orders" },

  { section: "Customers & Money" },
  { id: "customers", label: "Customers", icon: "\u{1F465}", module: "customers" },
  { id: "receivables", label: "Receivables", icon: "\u{1F4B0}", module: "receivables" },
  { id: "cylinder_deposits", label: "Cylinder Deposits", icon: "\u{1F6E2}\uFE0F", module: "cylinder_deposits" },

  { section: "Sales Department" },
  { id: "customer_growth", label: "Customer Growth", icon: "\u{1F331}", module: "customer_growth" },
  { id: "sales_targets", label: "Sales Targets", icon: "\u{1F3AF}", module: "sales_targets" },

  { section: "Inventory & Dispatch" },
  { id: "inventory", label: "Inventory", icon: "\u{1F4E6}", module: "inventory" },
  { id: "inventory_ledger", label: "Inv. Ledger", icon: "\u{1F4D2}", module: "inventory_ledger" },
  { id: "stock_import", label: "Import Stock", icon: "\u{1F4E5}", module: "stock_import" },
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
  { id: "discounts", label: "Discounts & Promos", icon: "\u{1F3AB}", roles: ["ADMIN", "MANAGER"] },
  { id: "locations", label: "Locations", icon: "\u{1F3E2}", roles: ["ADMIN"] },
  // strict: Admin only — Operation Head has every other Admin screen but not this one.
  { id: "system_reset", label: "System Reset (Go Live)", icon: "\u{1F9F9}", roles: ["ADMIN"], strict: true },
];

// ADMIN and OPERATION_HEAD both have Admin-level access; the only thing
// Operation Head can't do is the System Reset (Go Live).
const ADMIN_LIKE_ROLES = ["ADMIN", "OPERATION_HEAD"];
function isAdminLike(role) { return ADMIN_LIKE_ROLES.includes(role === undefined ? state.user.role : role); }
function hasModule(key) { return isAdminLike() || (state.permittedModules || []).includes(key); }

function navVisible(n) {
  // Users/Products/Locations/Roles are deliberately NOT part of the custom
  // module-permission system — they stay hardcoded to fixed roles as a
  // safety rail (see role_permissions design note).
  if (n.roles) {
    if (n.strict) return n.roles.includes(state.user.role);
    return n.roles.includes(state.user.role) || (n.roles.includes("ADMIN") && isAdminLike());
  }
  if (n.module) return hasModule(n.module);
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
  document.getElementById("topbarLoc").textContent = isAdminLike()
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
    users: renderUsers, products: renderProducts, locations: renderLocations, discounts: renderDiscounts, system_reset: renderSystemReset,
    audit: renderAudit, cylinder_deposits: renderCylinderDeposits,
    hauling: renderHauling, vehicles: renderVehicles, employees: renderEmployees,
    staff_schedule: renderStaffSchedule, kpi: renderKpi,
    booked_orders: renderBookedOrders, incident_reports: renderIncidentReports, roles: renderRoles,
    commercial_deliveries: renderCommercialDeliveries, eod_report: renderEndOfDay,
    customer_growth: renderCustomerGrowth, sales_targets: renderSalesTargets, stock_import: renderStockImport,
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

// Shared print helper. Loads the given HTML into the hidden #receiptArea and
// prints it. Waits for any images (the logo) to finish loading first —
// otherwise the browser can open the print dialog before the logo has
// rendered. Also closes any open modal so it can't overlay the printout.
function printDocument(html) {
  closeModal();
  const area = document.getElementById("receiptArea");
  area.innerHTML = html;

  const images = [...area.querySelectorAll("img")];
  const pending = images.filter(img => !img.complete);
  if (!pending.length) {
    setTimeout(() => window.print(), 100);
    return;
  }
  let remaining = pending.length;
  let done = false;
  const go = () => {
    if (done) return;
    done = true;
    setTimeout(() => window.print(), 100);
  };
  pending.forEach(img => {
    const onSettled = () => { remaining -= 1; if (remaining <= 0) go(); };
    img.addEventListener("load", onSettled, { once: true });
    img.addEventListener("error", onSettled, { once: true });
  });
  // Safety net: never block printing on a stuck image request.
  setTimeout(go, 2000);
}

// ================================================================== DASHBOARD
async function renderDashboard(c) {
  c.innerHTML = "Loading...";

  function todayStr() { return new Date().toISOString().slice(0, 10); }
  function daysAgoStr(n) {
    const dt = new Date();
    dt.setDate(dt.getDate() - n);
    return dt.toISOString().slice(0, 10);
  }
  function monthStartStr() {
    const dt = new Date();
    return new Date(dt.getFullYear(), dt.getMonth(), 1).toISOString().slice(0, 10);
  }

  let payFrom = todayStr();
  let payTo = todayStr();

  async function load() {
    const qs = new URLSearchParams({ payments_from: payFrom, payments_to: payTo });
    const d = await api("/dashboard?" + qs.toString());
    c.innerHTML = `
      <div class="grid ${d.show_receivables ? "grid-4" : "grid-2"}">
        <div class="card stat-card green"><div class="label">Today's Sales</div><div class="value">${money(d.today_sales_total)}</div></div>
        <div class="card stat-card"><div class="label">Today's Transactions</div><div class="value">${d.today_sales_count}</div></div>
        ${d.show_receivables ? `
        <div class="card stat-card"><div class="label">Outstanding Receivables</div><div class="value">${money(d.outstanding_receivables)}</div></div>
        <div class="card stat-card danger"><div class="label">Overdue Receivables</div><div class="value">${money(d.overdue_receivables)}</div></div>` : ""}
      </div>
      <div class="grid grid-2" style="margin-top:16px">
        <div class="card">
          <h3 style="margin-top:0">Low Stock Alerts</h3>
          ${d.low_stock.length ? `<div class="table-wrap"><table><thead><tr><th>Product</th><th>Location</th><th>Qty</th><th>Min</th></tr></thead><tbody>
            ${d.low_stock.map(r => `<tr><td>${esc(r.name)}</td><td>${esc(r.location_name)}</td><td>${r.quantity}</td><td>${r.min_stock}</td></tr>`).join("")}
          </tbody></table></div>` : `<p style="color:var(--muted)">No low-stock items.</p>`}
        </div>
        <div class="card">
          <h3 style="margin-top:0">Payments by Method</h3>
          <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
            <button class="btn small" data-preset="today">Today</button>
            <button class="btn small" data-preset="7">Last 7 Days</button>
            <button class="btn small" data-preset="30">Last 30 Days</button>
            <button class="btn small" data-preset="month">This Month</button>
          </div>
          <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
            <input type="date" id="payFrom" value="${payFrom}" style="font-size:13px">
            <span style="color:var(--muted);font-size:13px">to</span>
            <input type="date" id="payTo" value="${payTo}" style="font-size:13px">
            <button class="btn small" id="payApply">Apply</button>
          </div>
          ${d.payments_by_method.length ? `<div class="table-wrap"><table><thead><tr><th>Method</th><th>Total</th></tr></thead><tbody>
            ${d.payments_by_method.map(r => `<tr><td>${esc(r.method)}</td><td>${money(r.total)}</td></tr>`).join("")}
            <tr style="font-weight:700;background:var(--bg)"><td>TOTAL</td><td>${money(d.payments_total)}</td></tr>
          </tbody></table></div>` : `<p style="color:var(--muted)">No payments in this date range.</p>`}
        </div>
      </div>`;

    c.querySelectorAll("[data-preset]").forEach(btn => {
      btn.addEventListener("click", () => {
        const p = btn.dataset.preset;
        if (p === "today") { payFrom = todayStr(); payTo = todayStr(); }
        else if (p === "month") { payFrom = monthStartStr(); payTo = todayStr(); }
        else { payFrom = daysAgoStr(parseInt(p, 10) - 1); payTo = todayStr(); }
        load();
      });
    });
    document.getElementById("payApply").addEventListener("click", () => {
      const f = document.getElementById("payFrom").value;
      const t = document.getElementById("payTo").value;
      if (!f || !t) { toast("Choose both dates", "error"); return; }
      if (f > t) { toast("'From' date can't be after 'To' date", "error"); return; }
      payFrom = f; payTo = t;
      load();
    });
  }

  load();
}

// ================================================================== POS
async function renderPOS(c) {
  state.cart = [];
  state.products = await api("/products");
  try { state.discounts = await api("/discounts?live=1"); } catch (e) { state.discounts = []; }
  const locationId = isAdminLike() ? (state.locations[0] && state.locations[0].id) : state.user.location_id;
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
      is_cylinder: (prod.deposit_price || 0) > 0 || /cylinder|lpg/i.test(prod.category || ""),
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
  window.__posDiscount = (pid, mode, val) => {
    const item = state.cart.find(i => i.product_id === pid && i.sale_mode === mode);
    if (!item) return;
    item.discount_id = val ? parseInt(val) : null;
    drawCart(locationId);
  };
  window.__posMode = (pid, oldMode, newMode) => {
    const item = state.cart.find(i => i.product_id === pid && i.sale_mode === oldMode);
    if (!item) return;
    if (newMode === "SWAP") {
      drawCart(locationId); // keep the dropdown on the old mode until confirmed
      // ask which brand is being swapped in and the swap fee first; the line
      // only switches to SWAP once the cashier confirms
      openSwapDialog(item, (brand, fee) => {
        const target = state.cart.find(i => i !== item && i.product_id === pid && i.sale_mode === "SWAP");
        if (target) {
          target.qty += item.qty;
          target.swap_brand = brand; target.swap_fee = fee;
          target.price = target.base_price + fee;
          state.cart = state.cart.filter(i => i !== item);
        } else {
          item.sale_mode = "SWAP"; item.swap_brand = brand; item.swap_fee = fee;
          item.price = item.base_price + fee;
        }
        drawCart(locationId);
      });
      return;
    }
    // merge into an existing line with the same new mode, if one exists
    const target = state.cart.find(i => i.product_id === pid && i.sale_mode === newMode);
    if (target) {
      target.qty += item.qty;
      state.cart = state.cart.filter(i => i !== item);
    } else {
      item.sale_mode = newMode;
      item.price = item.base_price + (newMode === "DEPOSIT" ? item.deposit_price : 0);
      delete item.swap_brand; delete item.swap_fee;
    }
    drawCart(locationId);
  };
  window.__posEditSwap = (pid) => {
    const item = state.cart.find(i => i.product_id === pid && i.sale_mode === "SWAP");
    if (!item) return;
    openSwapDialog(item, (brand, fee) => {
      item.swap_brand = brand; item.swap_fee = fee;
      item.price = item.base_price + fee;
      drawCart(locationId);
    });
  };
}

// Common LPG brands a customer may bring in to swap for ours.
const SWAP_BRANDS = ["Petron Gasul", "Shellane", "Prycegas", "Fiesta Gas", "Regasco", "Phoenix LPG", "Island Gas", "Total Gas"];

// Modal for a brand swap: which brand the customer's empty is, and the swap
// fee per cylinder (added on top of the gas price).
function openSwapDialog(item, onOk, onCancel) {
  const cur = item.swap_brand || "";
  const isPreset = SWAP_BRANDS.includes(cur);
  const fee = item.swap_fee ? item.swap_fee : "";
  openModal(`Swap &mdash; ${esc(item.name)}`, `
    <p style="color:var(--muted);font-size:13px;margin-top:0">Customer hands over an empty cylinder of another brand in exchange for a full ${esc(item.name)}.</p>
    <div class="field"><label>Brand of LPG being swapped</label>
      <select id="swapBrand">
        <option value="">-- Select brand --</option>
        ${SWAP_BRANDS.map(b => `<option ${b === cur ? "selected" : ""}>${esc(b)}</option>`).join("")}
        <option value="__other" ${cur && !isPreset ? "selected" : ""}>Other (type below)</option>
      </select>
    </div>
    <div class="field" id="swapOtherWrap" style="${cur && !isPreset ? "" : "display:none"}">
      <label>Other brand</label><input type="text" id="swapOther" value="${cur && !isPreset ? esc(cur) : ""}" placeholder="Brand name">
    </div>
    <div class="field"><label>Swapping fee per cylinder <span style="font-weight:400;color:var(--muted)">(optional)</span></label>
      <input type="number" id="swapFee" min="0" step="0.01" value="${fee}" placeholder="Leave blank if no fee">
    </div>
    <div class="field" style="font-size:13px">Price per cylinder: <b id="swapPreview"></b>
      <span style="color:var(--muted)">(${money(item.base_price)} gas + fee)</span></div>
    <div class="modal-actions">
      <button class="btn" id="swapCancel" type="button">Cancel</button>
      <button class="btn green" id="swapOk" type="button">Apply Swap</button>
    </div>
  `, () => {
    const sel = document.getElementById("swapBrand");
    const feeIn = document.getElementById("swapFee");
    const preview = () => { document.getElementById("swapPreview").textContent = money(item.base_price + (parseFloat(feeIn.value) || 0)); };
    sel.addEventListener("change", () => {
      document.getElementById("swapOtherWrap").style.display = sel.value === "__other" ? "" : "none";
    });
    feeIn.addEventListener("input", preview);
    preview();
    document.getElementById("swapCancel").addEventListener("click", () => { closeModal(); if (onCancel) onCancel(); });
    document.getElementById("swapOk").addEventListener("click", () => {
      const brand = sel.value === "__other" ? document.getElementById("swapOther").value.trim() : sel.value;
      const raw = feeIn.value.trim();
      const f = raw === "" ? 0 : parseFloat(raw);
      if (!brand) { toast("Select the LPG brand being swapped", "error"); return; }
      if (isNaN(f) || f < 0) { toast("Swapping fee must be a number (0 or more), or leave it blank", "error"); return; }
      const rounded = Math.round(f * 100) / 100;
      closeModal();
      onOk(brand, rounded);
    });
  });
}

// Discounts usable on a cart line (all-items promos + ones for this product).
function discountsFor(item) {
  return (state.discounts || []).filter(d => !d.product_id || d.product_id === item.product_id);
}

// Mirrors compute_item_discount() on the server: % off the gas price, or a
// fixed peso amount per unit. Never touches the cylinder deposit fee.
function lineDiscount(item) {
  if (!item.discount_id) return 0;
  const d = (state.discounts || []).find(x => x.id === item.discount_id);
  if (!d || (d.product_id && d.product_id !== item.product_id) || item.qty < (d.min_qty || 1)) return 0;
  const gas = item.base_price * item.qty;
  const amt = d.type === "PERCENT" ? gas * d.value / 100 : d.value * item.qty;
  return Math.round(Math.min(Math.max(amt, 0), gas) * 100) / 100;
}

function discountLabel(d) {
  const v = d.type === "PERCENT" ? `${d.value}% off` : `${money(d.value)} off/unit`;
  return `${d.name} (${v}${d.min_qty > 1 ? `, min ${d.min_qty}` : ""})`;
}

function cartTotals() {
  const subtotal = state.cart.reduce((s, i) => s + i.price * i.qty, 0);
  const discount = state.cart.reduce((s, i) => s + lineDiscount(i), 0);
  return { subtotal, discount, total: Math.max(0, subtotal - discount) };
}

function drawCart(locationId) {
  const panel = document.getElementById("cartPanel");
  const { subtotal, discount, total } = cartTotals();
  panel.innerHTML = `
    <h3 style="margin-top:0">Cart</h3>
    <div id="cartLines">${state.cart.length ? state.cart.map(i => `
      <div class="cart-line">
        <div class="cname">${esc(i.name)} ${i.sale_mode === 'DEPOSIT' ? '<span class="badge warn">DEPOSIT</span>' : ''}${i.sale_mode === 'SWAP' ? '<span class="badge blue">SWAP</span>' : ''}<br>
          <span style="color:var(--muted);font-size:12px">${money(i.price)} each</span>
          ${i.is_cylinder ? `<select style="margin-left:6px;font-size:11px;padding:2px 4px" onchange="__posMode(${i.product_id}, '${i.sale_mode}', this.value)">
            <option value="REFILL" ${i.sale_mode === 'REFILL' ? 'selected' : ''}>Refill (exchange empty)</option>
            ${i.deposit_price > 0 ? `<option value="DEPOSIT" ${i.sale_mode === 'DEPOSIT' ? 'selected' : ''}>New/Deposit (+${money(i.deposit_price)})</option>` : ""}
            <option value="SWAP" ${i.sale_mode === 'SWAP' ? 'selected' : ''}>Swap (other brand)</option>
          </select>` : ""}
          ${i.sale_mode === 'SWAP' ? `<br><span style="font-size:12px">Swap from <b>${esc(i.swap_brand)}</b>${i.swap_fee > 0 ? ` &middot; fee ${money(i.swap_fee)}/cyl` : " &middot; no fee"}</span>
            <button class="btn small" type="button" style="font-size:11px;padding:1px 6px" onclick="__posEditSwap(${i.product_id})">Edit</button>` : ""}
          ${(() => {
            const opts = discountsFor(i);
            if (!opts.length) return "";
            const ld = lineDiscount(i);
            const blocked = i.discount_id && !ld;
            return `<br><select style="margin-top:4px;font-size:11px;padding:2px 4px;max-width:100%" onchange="__posDiscount(${i.product_id}, '${i.sale_mode}', this.value)">
              <option value="">No discount</option>
              ${opts.map(d => `<option value="${d.id}" ${i.discount_id === d.id ? "selected" : ""}>${esc(discountLabel(d))}</option>`).join("")}
            </select>
            ${ld ? `<span style="color:var(--green-dark);font-size:12px;font-weight:600">&minus;${money(ld)}</span>` : ""}
            ${blocked ? `<span style="color:var(--danger);font-size:11px">qty below promo minimum</span>` : ""}`;
          })()}
        </div>
        <input type="number" min="0" value="${i.qty}" onchange="__posQty(${i.product_id}, '${i.sale_mode}', this.value)">
        <div style="width:78px;text-align:right;font-weight:600">${money(i.price * i.qty)}</div>
        <button class="btn small danger" onclick="__posRemove(${i.product_id}, '${i.sale_mode}')">&times;</button>
      </div>`).join("") : `<p style="color:var(--muted)">Cart is empty. Tap a product to add it.</p>`}
    </div>
    <div style="margin-top:10px">
      <div class="cart-total-row"><span>Subtotal</span><span>${money(subtotal)}</span></div>
      ${discount > 0 ? `<div class="cart-total-row"><span>Item discounts</span><span>&minus;${money(discount)}</span></div>` : ""}
      <div class="cart-total-row grand"><span>Total</span><span id="grandTotal">${money(total)}</span></div>
    </div>
    <button class="btn green" style="width:100%;margin-top:14px" id="checkoutBtn" ${state.cart.length ? "" : "disabled"}>Checkout</button>
  `;
  document.getElementById("checkoutBtn").addEventListener("click", () => openCheckout(locationId));
}

// 1% creditable withholding tax on a VAT-inclusive sale. Mirrors
// compute_withholding_tax() on the server: (gross / 1.12) x 1%.
function withholdingTax(gross) { return Math.round(gross / 1.12 * 0.01 * 100) / 100; }

// Commercial accounts: 50kg cylinders on a CREDIT sale are delivered only —
// the gas is charged later in 50kg Commercial Billing (mirrors the server).
function is50kgName(name) { const m = /(\d+(?:\.\d+)?)\s*-?\s*kg\b/i.exec(name || ""); return !!m && Math.abs(parseFloat(m[1]) - 50) < 0.01; }
function cartTotalsFor(commercialCredit) {
  if (!commercialCredit) return { ...cartTotals(), deferred: 0 };
  let subtotal = 0, discount = 0, deferred = 0;
  state.cart.forEach(i => {
    const prod = state.products.find(p => p.id === i.product_id) || {};
    if (is50kgName(i.name) || is50kgName(prod.sku)) {
      subtotal += (i.price - i.base_price) * i.qty;  // deposit / swap fee only
      deferred += i.base_price * i.qty;
    } else { subtotal += i.price * i.qty; discount += lineDiscount(i); }
  });
  return { subtotal, discount, total: Math.max(0, subtotal - discount), deferred };
}

async function openCheckout(locationId) {
  let gross = cartTotals().total;
  let total = gross;
  const customers = await api("/customers");
  const custOptions = `<option value="">-- Walk-in / no account --</option>` +
    customers.map(c => `<option value="${c.id}">${esc(c.name)} (${c.account_no}) - ${esc(c.category)}</option>`).join("");

  openModal("Checkout", `
    <div class="field"><label>Customer</label><select id="custSelect">${custOptions}</select></div>
    <div class="field" id="walkinNameField"><label>Walk-in customer name (optional)</label>
      <input type="text" id="walkinName" maxlength="120" placeholder="Type the customer's name"></div>
    <div class="field"><label>Sale Type</label>
      <select id="saleType"><option value="CASH">Cash Sale</option><option value="CREDIT">Credit / Terms Sale</option></select>
    </div>
    <div class="field"><label style="display:flex;align-items:center;gap:8px;cursor:pointer">
      <input type="checkbox" id="whtCheck" style="width:auto;margin:0"> Withholding tax (1%)</label>
      <div id="whtInfo" style="display:none;font-size:12.5px;color:var(--muted);margin-top:4px">
        Gross ${money(gross)} &divide; 1.12 = ${money(gross / 1.12)} &times; 1% = <b>&minus;${money(withholdingTax(gross))}</b></div></div>
    <div class="field"><label>Total Due</label><div style="font-size:20px;font-weight:800" id="totalDue">${money(total)}</div>
      <div id="commercialNote" style="display:none;font-size:12.5px;color:var(--muted);margin-top:4px"></div></div>
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
    const custSelect = document.getElementById("custSelect");
    custSelect.addEventListener("change", () => {
      document.getElementById("walkinNameField").style.display = custSelect.value ? "none" : "";
    });
    function refreshTotal() {
      const prev = total;
      const cust = customers.find(cu => String(cu.id) === custSelect.value);
      const commercialCredit = document.getElementById("saleType").value === "CREDIT" && cust
        && (cust.category || "").trim().toLowerCase() === "commercial";
      const t = cartTotalsFor(commercialCredit);
      gross = t.total;
      const wht = document.getElementById("whtCheck").checked;
      total = wht ? Math.round((gross - withholdingTax(gross)) * 100) / 100 : gross;
      document.getElementById("whtInfo").innerHTML =
        `Gross ${money(gross)} &divide; 1.12 = ${money(gross / 1.12)} &times; 1% = <b>&minus;${money(withholdingTax(gross))}</b>`;
      document.getElementById("whtInfo").style.display = wht ? "" : "none";
      document.getElementById("totalDue").textContent = money(total);
      const note = document.getElementById("commercialNote");
      note.style.display = t.deferred > 0 ? "" : "none";
      note.innerHTML = t.deferred > 0 ? `Commercial account: the 50kg cylinders (SRP ${money(t.deferred)}) are <b>delivered only</b> — not charged here and not on the SOA. Bill them in 50kg Commercial Billing.` : "";
      // keep a single full-payment row in step with the new amount due
      const amts = payRows.querySelectorAll(".payAmount");
      if (amts.length === 1 && Math.abs((parseFloat(amts[0].value) || 0) - prev) < 0.005) amts[0].value = total;
      recalc();
    }
    document.getElementById("whtCheck").addEventListener("change", refreshTotal);
    custSelect.addEventListener("change", refreshTotal);
    document.getElementById("saleType").addEventListener("change", (e) => {
      if (e.target.value === "CASH" && payRows.children.length === 0) addRow(total);
      refreshTotal();
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
        walkin_name: document.getElementById("custSelect").value ? "" : document.getElementById("walkinName").value.trim(),
        withholding_tax: document.getElementById("whtCheck").checked,
        sale_type: document.getElementById("saleType").value,
        items: state.cart.map(i => ({
          product_id: i.product_id, qty: i.qty, sale_mode: i.sale_mode,
          discount_id: lineDiscount(i) > 0 ? i.discount_id : null,
          ...(i.sale_mode === "SWAP" ? { swap_brand: i.swap_brand, swap_fee: i.swap_fee } : {}),
        })),
        payments,
      };
      try {
        const result = await api("/sales", { method: "POST", body });
        closeModal();
        toast(`Sale ${result.sale.sale_no} completed`, "success");
        state.cart = [];
        if (result.sale.sale_type === "CREDIT") printDeliveryReceipt(result);
        else printReceipt(result);
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
      ${items.map(i => `${esc(i.product_name)}${i.sale_mode === 'DEPOSIT' ? ' (DEPOSIT)' : ''}${i.sale_mode === 'SWAP' ? `<br>  SWAP from ${esc(i.swap_brand)}${i.swap_fee > 0 ? ` (fee ${money(i.swap_fee)}/cyl)` : ""}` : ''}<br>  ${i.qty} x ${money(i.price)} = ${money(i.subtotal)}${i.discount_amount > 0 ? `<br>  Less: ${esc(i.discount_name)} -${money(i.discount_amount)}` : ""}`).join("<br>")}
      <div>--------------------------------</div>
      <div>Subtotal: ${money(sale.subtotal)}<br>Discount: ${money(sale.discount)}<br><b>Total: ${money(sale.total)}</b>${sale.withholding_tax > 0 ? `<br>Less 1% withholding tax: -${money(sale.withholding_tax)}<br><b>Amount Due: ${money(sale.total - sale.withholding_tax)}</b>` : ""}</div>
      <div>--------------------------------</div>
      ${payments.map(p => `${esc(p.method)}: ${money(p.amount)}`).join("<br>")}
      <div>Paid: ${money(sale.paid)}<br>Balance: ${money(sale.balance)}</div>
      <div style="text-align:center;margin-top:8px">Thank you for your purchase!</div>
    </div>`;
  printDocument(html);
}

// Delivery Receipt for credit / terms sales: letter-size page with two
// copies (Customer / Office) the customer signs on receipt of the cylinders.
function printDeliveryReceipt(result) {
  const { sale, items } = result;
  const fmtDate = v => v ? new Date(v.replace(" ", "T")).toLocaleDateString("en-PH", { year: "numeric", month: "long", day: "numeric" }) : "";
  const terms = sale.customer_terms_days ? `${sale.customer_terms_days} days` : "Credit";
  const minRows = 6;
  // Commercial accounts: the DR shows quantities only, no prices or amounts —
  // they're charged through 50kg Commercial Billing.
  const noAmounts = (sale.customer_category || "").trim().toLowerCase() === "commercial";
  const lineRows = items.map(i => {
    const notes = [];
    if (i.sale_mode === "REFILL") notes.push("Refill — empty returned");
    if (i.sale_mode === "DEPOSIT") notes.push("With cylinder deposit");
    if (i.sale_mode === "SWAP") notes.push(`Swap from ${esc(i.swap_brand || "")}${i.swap_fee > 0 && !noAmounts ? `, fee ${money(i.swap_fee)}/cyl` : ""}`);
    if (i.discount_amount > 0 && !noAmounts) notes.push(`Less ${esc(i.discount_name || "discount")}: −${money(i.discount_amount)}`);
    if (i.bill_later && !noAmounts) notes.push("Gas billed separately (commercial billing)");
    const priceCell = i.bill_later && !i.price ? "For billing" : money(i.price);
    const amtCell = i.bill_later && !i.subtotal ? "—" : money(i.subtotal - (i.discount_amount || 0));
    return `<tr><td class="c">${i.qty}</td><td class="c">cyl</td>
      <td>${esc(i.product_name)}${notes.length ? `<div class="sub">${notes.join(" · ")}</div>` : ""}</td>
      ${noAmounts ? "" : `<td class="r">${priceCell}</td><td class="r">${amtCell}</td>`}</tr>`;
  });
  while (lineRows.length < minRows) lineRows.push(`<tr class="blank"><td></td><td></td><td></td>${noAmounts ? "" : "<td></td><td></td>"}</tr>`);
  const totalQty = items.reduce((a, i) => a + i.qty, 0);
  const voided = sale.status === "VOIDED";

  const copy = label => `
  <div class="dr">
    ${voided ? `<div class="void">VOID</div>` : ""}
    <div class="hdr">
      <img src="/images/logo.png" alt="">
      <div class="co">
        <div class="name">GREAT UNLI FOOD CORP.</div>
        <div>Solane LPG Distributor · ${esc(sale.location_name)}</div>
        ${sale.location_address ? `<div>${esc(sale.location_address)}</div>` : ""}
      </div>
      <div class="title">
        <div class="t">DELIVERY RECEIPT</div>
        <div class="no">No. <b>${esc(sale.dr_no || sale.sale_no)}</b></div>
        <div class="copy">${label}</div>
      </div>
    </div>
    <table class="info">
      <tr><td class="k">Delivered to</td><td class="v"><b>${esc(sale.customer_name || "")}</b>${sale.account_no ? ` (${esc(sale.account_no)})` : ""}</td>
          <td class="k">Date</td><td class="v">${fmtDate(sale.ts)}</td></tr>
      <tr><td class="k">Business name</td><td class="v">${esc(sale.customer_business_name || "")}</td>
          <td class="k">Terms</td><td class="v">${terms}</td></tr>
      <tr><td class="k">Address</td><td class="v">${esc(sale.customer_address || "")}</td>
          <td class="k">Due date</td><td class="v">${sale.due_date ? fmtDate(sale.due_date) : "—"}</td></tr>
      <tr><td class="k">Contact no.</td><td class="v">${esc(sale.customer_phone || "")}</td>
          <td class="k">Ref.</td><td class="v">${esc(sale.sale_no)}${sale.invoice_no ? ` / ${esc(sale.invoice_no)}` : ""}</td></tr>
    </table>
    <table class="items">
      <thead><tr><th style="width:44px">Qty</th><th style="width:40px">Unit</th><th>Description</th>${noAmounts ? "" : `<th style="width:84px">Unit Price</th><th style="width:94px">Amount</th>`}</tr></thead>
      <tbody>${lineRows.join("")}</tbody>
      ${noAmounts ? `<tfoot><tr><td class="c"><b>${totalQty}</b></td><td colspan="2">Total quantity</td></tr></tfoot>` : `<tfoot>
        <tr><td class="c"><b>${totalQty}</b></td><td colspan="3" class="r">Total amount</td><td class="r"><b>${money(sale.total)}</b></td></tr>
        ${sale.withholding_tax > 0 ? `<tr><td colspan="4" class="r">Less: 1% withholding tax</td><td class="r">${money(sale.withholding_tax)}</td></tr>` : ""}
        <tr><td colspan="4" class="r">Less: amount paid</td><td class="r">${money(sale.paid)}</td></tr>
        <tr class="due"><td colspan="4" class="r">Balance charged to account</td><td class="r">${money(sale.balance)}</td></tr>
      </tfoot>`}
    </table>
    <div class="note">Received the above items in good order and condition.${noAmounts ? "" : " Payment is due on or before the due date stated above."}</div>
    <table class="sig"><tr>
      <td><div class="line">${esc(sale.cashier_name || "")}</div>Prepared by</td>
      <td><div class="line">&nbsp;</div>Delivered by (driver / plate no.)</td>
      <td><div class="line">&nbsp;</div>Received by: signature over printed name / date</td>
    </tr></table>
  </div>`;

  const twoPerPage = items.length <= 6;
  printDocument(`<style>
    @page{size:letter portrait;margin:8mm}
    .drs, .drs *{-webkit-print-color-adjust:exact;print-color-adjust:exact;box-sizing:border-box}
    .drs{font-family:Arial,Helvetica,sans-serif;font-size:10.5px;color:#111}
    .drs table{font-size:10px}
    .drs th, .drs td{white-space:normal;text-transform:none;letter-spacing:0;font-size:10px;padding:2px 4px}
    .dr{position:relative;border:1.5px solid #0b3d66;padding:10px 12px;page-break-inside:avoid;break-inside:avoid}
    .dr .hdr{display:flex;align-items:center;gap:10px;border-bottom:2px solid #0b3d66;padding-bottom:6px;margin-bottom:6px}
    .dr .hdr img{height:46px;width:46px;object-fit:contain}
    .dr .co{flex:1;color:#333;line-height:1.35}
    .dr .co .name{font-weight:700;font-size:14px;color:#0b3d66}
    .dr .title{text-align:right}
    .dr .title .t{font-weight:800;font-size:16px;color:#0b3d66;letter-spacing:.5px}
    .dr .title .no{font-size:12px;margin-top:2px}
    .dr .title .no b{color:#c0392b;font-size:13px}
    .dr .copy{display:inline-block;margin-top:3px;padding:1px 6px;background:#1e9e5a;color:#fff;font-size:9px;font-weight:700;border-radius:3px}
    .dr table{width:100%;border-collapse:collapse}
    .dr .info td{padding:2px 4px;vertical-align:top;border:none}
    .dr .info .k{width:78px;color:#555;font-size:9px}
    .dr .info .v{border-bottom:1px solid #bbb !important}
    .dr .items{margin-top:6px}
    .dr .items th{background:#0b3d66;color:#fff;font-size:9px;padding:3px 4px;text-align:left;border:1px solid #0b3d66}
    .dr .items td{border:1px solid #999;padding:3px 4px;vertical-align:top}
    .dr .items tr.blank td{height:16px}
    .dr .items tfoot td{border:1px solid #999}
    .dr .items tr.due td{background:#e8f5ee;font-weight:700;font-size:11px}
    .dr .c{text-align:center}.dr .r{text-align:right}
    .dr .sub{font-size:8.5px;color:#555;margin-top:1px}
    .dr .note{margin:6px 0 4px;font-size:9px;font-style:italic;color:#444}
    .dr .sig td{width:33.3%;text-align:center;font-size:8.5px;color:#444;padding:22px 8px 0;border:none}
    .dr .sig .line{border-top:1px solid #111;padding-top:2px;color:#111;font-size:10px;min-height:14px}
    .dr .void{position:absolute;top:40%;left:0;right:0;text-align:center;font-size:90px;font-weight:800;color:rgba(192,57,43,.18);transform:rotate(-18deg);pointer-events:none}
    .cut{border-top:1px dashed #888;margin:8px 0 10px;text-align:center;font-size:8px;color:#888;line-height:0}
    .cut span{background:#fff;padding:0 6px}
  </style>
  <div class="drs" style="margin:-20px -12px">
    ${copy("CUSTOMER'S COPY")}
    ${twoPerPage ? `<div class="cut"><span>✂ cut here</span></div>` : `<div style="page-break-after:always"></div>`}
    ${copy("OFFICE COPY")}
  </div>`);
}

// End of Day: how "Cash sales" ties to the POS Sales received in
// Collections by Payment Method, so a difference is explained line by line.
function eodReconcileHtml(s, print = false) {
  if (s.paid_at_sale === undefined) return "";
  const diff = Math.round((s.paid_at_sale - s.cash_sales) * 100) / 100;
  const line = (label, v, strong) => `<div class="cart-total-row" style="${strong ? "font-weight:700;" : ""}${print ? "display:flex;justify-content:space-between;font-size:11px" : ""}"><span>${label}</span><span>${v}</span></div>`;
  const parts = [line("Cash sales", money(s.cash_sales))];
  if (s.credit_paid_at_sale) parts.push(line("&nbsp;&nbsp;+ Paid at the counter on credit / terms sales", money(s.credit_paid_at_sale)));
  if (s.cash_wht) parts.push(line("&nbsp;&nbsp;&minus; 1% withholding tax on cash sales (Form 2307)", money(s.cash_wht)));
  if (Math.abs(s.cash_overpaid) > 0.009) parts.push(line("&nbsp;&nbsp;+ Paid above the amount due on cash sales", money(s.cash_overpaid)));
  parts.push(line("= POS Sales received (Collections table)", money(s.paid_at_sale), true));
  const over = (s.overpaid_sales || []).map(x => `${esc(x.sale_no)} paid ${money(x.paid)} for ${money(x.total - x.withholding_tax)} (+${money(x.over)})${x.edited_at ? " — adjusted after the sale" : ""}`);
  return `<div style="margin-top:10px;padding-top:8px;border-top:1px dashed var(--border, #ccc)">
    <div style="font-size:12px;color:var(--muted, #666);margin-bottom:4px"><b>Why POS Sales received ${Math.abs(diff) > 0.009 ? `differs from Cash sales by ${money(diff)}` : "matches Cash sales"}</b></div>
    ${parts.join("")}
    ${over.length ? `<div style="font-size:11.5px;color:var(--muted, #666);margin-top:4px">Overpaid cash sales (change typed in as payment, or price lowered later): ${over.join("; ")}</div>` : ""}
  </div>`;
}

// ================================================================== SALES
// Generate Billing (Commercial 50kg deliveries) is for Accounting only (+ Admin).
const canCommercialBilling = () => isAdminLike() || state.user.role === "ACCOUNTING";
// Operation Head (and Admin) can adjust a completed sale.
const canEditSale = () => isAdminLike();
const salesView = { q: "", from: "", to: "", status: "", order: "newest", page: 1, pageSize: 100 };
async function renderSales(c) {
  const v = salesView;
  c.innerHTML = `<div class="card" style="margin-bottom:12px">
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <input type="search" id="salesQ" placeholder="Search sale #, DR # or customer name..." value="${esc(v.q)}" style="flex:1;min-width:220px">
      <label style="font-size:13px;color:var(--muted)">From</label><input type="date" id="salesFrom" value="${esc(v.from)}">
      <label style="font-size:13px;color:var(--muted)">to</label><input type="date" id="salesTo" value="${esc(v.to)}">
      <select id="salesStatus"><option value="">All statuses</option><option value="COMPLETED">Completed</option><option value="VOIDED">Voided</option></select>
      <select id="salesOrder"><option value="newest">Newest first</option><option value="oldest">Oldest first (from the first sale)</option></select>
      <select id="salesPageSize"><option>50</option><option>100</option><option>250</option><option>500</option></select>
      <button class="btn small" id="salesClear" type="button">Clear</button>
    </div>
  </div>
  <div id="salesSummary" style="font-size:13px;color:var(--muted);margin-bottom:8px"></div>
  <div id="salesTable">Loading...</div>
  <div id="salesPager" style="display:flex;gap:8px;align-items:center;justify-content:center;margin-top:10px;flex-wrap:wrap"></div>`;
  document.getElementById("salesStatus").value = v.status;
  document.getElementById("salesOrder").value = v.order;
  document.getElementById("salesPageSize").value = String(v.pageSize);
  const reload = (resetPage = true) => { if (resetPage) v.page = 1; loadSalesPage(); };
  let t;
  document.getElementById("salesQ").addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => { v.q = e.target.value.trim(); reload(); }, 300); });
  [["salesFrom", "from"], ["salesTo", "to"], ["salesStatus", "status"], ["salesOrder", "order"]].forEach(([id, k]) =>
    document.getElementById(id).addEventListener("change", (e) => { v[k] = e.target.value; reload(); }));
  document.getElementById("salesPageSize").addEventListener("change", (e) => { v.pageSize = parseInt(e.target.value) || 100; reload(); });
  document.getElementById("salesClear").addEventListener("click", () => {
    Object.assign(v, { q: "", from: "", to: "", status: "", order: "newest", page: 1 });
    renderSales(c);
  });
  await loadSalesPage();
}
async function loadSalesPage() {
  const v = salesView;
  const qs = new URLSearchParams({ page: v.page, page_size: v.pageSize, order: v.order });
  if (v.q) qs.set("q", v.q);
  if (v.from) qs.set("from", v.from);
  if (v.to) qs.set("to", v.to);
  if (v.status) qs.set("status", v.status);
  let data;
  try { data = await api("/sales?" + qs.toString()); } catch (e) { return; }
  const tableEl = document.getElementById("salesTable");
  if (!tableEl) return;
  const sales = data.rows;
  const canGenBill = canCommercialBilling();
  const first = data.total ? (data.page - 1) * data.page_size + 1 : 0;
  document.getElementById("salesSummary").innerHTML = data.total
    ? `Showing <b>${first}–${first + sales.length - 1}</b> of <b>${data.total}</b> sale(s)${v.q ? ` matching “${esc(v.q)}”` : ""} &middot; completed total ${money(data.total_amount)}`
    : "";
  tableEl.innerHTML = `<div class="table-wrap"><table><thead><tr>
    <th>Sale #</th><th>Date</th><th>Location</th><th>Cashier</th><th>Customer</th><th>Type</th><th>Total</th><th>Balance</th><th>Status</th><th></th>
  </tr></thead><tbody>
    ${sales.length ? sales.map(s => `<tr>
      <td>${esc(s.sale_no)}${s.dr_no ? `<br><span style="font-size:11.5px;color:var(--muted)">${esc(s.dr_no)}</span>` : ""}</td><td>${s.ts}</td><td>${esc(s.location_name)}</td><td>${esc(s.cashier_name)}</td>
      <td>${s.customer_id ? esc(s.customer_name) : `${esc(s.customer_name || "Walk-in")} <span class="badge gray">walk-in</span>`}</td>
      <td><span class="badge ${s.sale_type === 'CASH' ? 'green' : 'warn'}">${s.sale_type}</span></td>
      <td>${money(s.total)}</td><td>${money(s.balance)}</td>
      <td><span class="badge ${s.status === 'VOIDED' ? 'danger' : 'blue'}">${s.status}</span>
        ${s.edited_at ? `<br><span class="badge gray" title="Adjusted ${esc(s.edited_at)}${s.edited_by_name ? " by " + esc(s.edited_by_name) : ""}">ADJUSTED</span>` : ""}</td>
      <td style="display:flex;gap:6px">
        <button class="btn small" onclick="viewSale(${s.id})">View</button>
        ${s.sale_type === 'CREDIT' ? `<button class="btn small green" onclick="printSaleDR(${s.id})" title="Print Delivery Receipt">DR</button>` : ""}
        ${s.billing_id ? `<span class="badge green" title="Billed ${money(Number((s.billing_ref || "|0").split("|")[1]))}">Billed ${esc((s.billing_ref || "").split("|")[0])}</span>`
          : canGenBill && s.bill_later_qty > 0 && s.status === 'COMPLETED' ? `<button class="btn small blue" onclick="openSaleBilling(${s.id})" title="50kg commercial delivery — bill the gas">Generate Billing</button>` : ""}
        ${canEditSale() && s.status === 'COMPLETED' && !s.billing_id ? `<button class="btn small" onclick="openEditSale(${s.id})" title="Adjust items, prices or customer">Edit</button>` : ""}
        ${(isAdminLike() || state.user.role === 'SUPERVISOR') && s.status !== 'VOIDED' ? `<button class="btn small danger" onclick="voidSale(${s.id})">Void</button>` : ""}
      </td>
    </tr>`).join("") : `<tr><td colspan="10" style="color:var(--muted)">No sales found.</td></tr>`}
  </tbody></table></div>`;
  const pager = document.getElementById("salesPager");
  pager.innerHTML = data.pages > 1 ? `
    <button class="btn small" id="spFirst" ${data.page <= 1 ? "disabled" : ""}>&laquo; First</button>
    <button class="btn small" id="spPrev" ${data.page <= 1 ? "disabled" : ""}>&lsaquo; Prev</button>
    <span style="font-size:13px">Page <input type="number" id="spPage" min="1" max="${data.pages}" value="${data.page}" style="width:70px"> of ${data.pages}</span>
    <button class="btn small" id="spNext" ${data.page >= data.pages ? "disabled" : ""}>Next &rsaquo;</button>
    <button class="btn small" id="spLast" ${data.page >= data.pages ? "disabled" : ""}>Last &raquo;</button>` : "";
  const go = (p) => { v.page = Math.min(Math.max(1, p), data.pages); loadSalesPage(); };
  if (data.pages > 1) {
    document.getElementById("spFirst").onclick = () => go(1);
    document.getElementById("spPrev").onclick = () => go(data.page - 1);
    document.getElementById("spNext").onclick = () => go(data.page + 1);
    document.getElementById("spLast").onclick = () => go(data.pages);
    document.getElementById("spPage").onchange = (e) => go(parseInt(e.target.value) || 1);
  }
}
// Operation Head / Admin: adjust a completed sale — items, qty, unit price,
// discount, customer and notes. Stock and the receivable follow the change.
window.openEditSale = async (saleId) => {
  let detail, customers;
  try { [detail, customers] = await Promise.all([api(`/sales/${saleId}`), api("/customers")]); } catch (e) { return; }
  const { sale, items } = detail;
  const isCredit = sale.sale_type === "CREDIT";
  const prodOpts = (sel, selName) => {
    const list = state.products.filter(p => p.active !== 0 || p.id === sel);
    const missing = sel && !list.some(p => p.id === sel) ? `<option value="${sel}" selected>${esc(selName || "Product #" + sel)}</option>` : "";
    return missing + list.map(p => `<option value="${p.id}" ${p.id === sel ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  };
  // Commercial account on credit: 50kg gas is billed later, so its line price is only the deposit / swap fee.
  const is50 = p => /(^|[^0-9.])50\s*-?\s*kg/i.test(`${p.name || ""} ${p.sku || ""}`);
  const billLater = (p) => {
    const cu = customers.find(x => x.id === parseInt(document.getElementById("esCust").value));
    return isCredit && cu && (cu.category || "").trim().toLowerCase() === "commercial" && is50(p);
  };
  const custOpts = `${isCredit ? "" : `<option value="">Walk-in (no account)</option>`}` + customers
    .filter(cu => cu.status === "ACTIVE" || cu.id === sale.customer_id)
    .map(cu => `<option value="${cu.id}" ${cu.id === sale.customer_id ? "selected" : ""}>${esc(cu.name)} (${esc(cu.account_no)})</option>`).join("");
  openModal(`Edit Sale ${esc(sale.sale_no)}`, `
    <p style="font-size:12.5px;color:var(--muted);margin-top:0">${esc(sale.ts)} &middot; ${esc(sale.location_name)} &middot; ${sale.sale_type}
      &middot; paid so far <b>${money(sale.paid)}</b>. Changing items or quantities moves stock (and empties) for you; payments are not changed.</p>
    <div class="field"><label>Customer</label><select id="esCust">${custOpts}</select></div>
    <div class="field" id="esWalkinWrap"><label>Walk-in name (optional)</label><input id="esWalkin" value="${esc(sale.walkin_name || "")}"></div>
    <div class="field"><label>Items</label>
      <div style="display:flex;gap:6px;font-size:11.5px;color:var(--muted);font-weight:600">
        <span style="flex:2.2">Product</span><span style="flex:1.1">Mode</span><span style="width:64px">Qty</span>
        <span style="flex:1">Unit price</span><span style="flex:1">Discount</span><span style="flex:1.2">Swap brand</span><span style="width:30px"></span></div>
      <div id="esRows"></div>
      <button class="btn small" id="esAdd" type="button">+ Add Item</button>
    </div>
    <label style="font-size:13px;display:flex;gap:6px;align-items:center;margin-bottom:10px"><input type="checkbox" id="esWht" ${sale.withholding_tax > 0 ? "checked" : ""}> Less 1% withholding tax</label>
    <div class="field"><label>Notes</label><input id="esNotes" value="${esc(sale.notes || "")}"></div>
    <div class="field"><label>Reason for the adjustment (required, kept in the Audit Log)</label><input id="esReason" placeholder="e.g. wrong price entered, wrong customer"></div>
    <div class="field" id="esSummary" style="background:var(--bg);padding:10px;border-radius:8px;font-size:13.5px"></div>
    <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn green" id="esSave">Save Adjustment</button></div>
  `, () => {
    document.querySelector("#modalRoot .modal").style.width = "900px";
    const rowsEl = document.getElementById("esRows");
    const custSel = document.getElementById("esCust");
    const syncWalkin = () => { document.getElementById("esWalkinWrap").style.display = custSel.value ? "none" : ""; };
    custSel.addEventListener("change", syncWalkin); syncWalkin();
    const recalc = () => {
      let sub = 0, disc = 0;
      rowsEl.querySelectorAll(".pay-row").forEach(r => {
        const q = parseInt(r.querySelector(".esQty").value) || 0, pr = parseFloat(r.querySelector(".esPrice").value) || 0;
        sub += q * pr; disc += parseFloat(r.querySelector(".esDisc").value) || 0;
      });
      const total = sub - disc;
      const wht = document.getElementById("esWht").checked ? Math.round(total / 1.12 * 0.01 * 100) / 100 : 0;
      const due = total - wht, bal = due - sale.paid;
      document.getElementById("esSummary").innerHTML = `
        Total: <b>${money(total)}</b> <span style="color:var(--muted)">(was ${money(sale.total)})</span>${wht ? ` &middot; less WHT ${money(wht)} = due <b>${money(due)}</b>` : ""}<br>
        Paid: ${money(sale.paid)} &middot; New balance: <b style="color:${bal > 0.009 && !isCredit ? "var(--danger)" : "inherit"}">${money(bal)}</b>
        ${!isCredit && bal > 0.009 ? `<br><span style="color:var(--danger)">A cash sale can't be left unpaid — lower the amount or ring up the difference as a new sale.</span>` : ""}
        ${!isCredit && bal < -0.009 ? `<br><span style="color:var(--muted)">Refund / change due to the customer: ${money(-bal)}</span>` : ""}`;
    };
    const addRow = (it = {}) => {
      const row = document.createElement("div");
      row.className = "pay-row";
      row.dataset.discountId = it.discount_id || "";
      row.dataset.discountName = it.discount_name || "";
      row.innerHTML = `<select class="esProd" style="flex:2.2;min-width:0;width:auto">${prodOpts(it.product_id, it.product_name)}</select>
        <select class="esMode" style="flex:1.1;min-width:0;width:auto">
          ${["REFILL", "DEPOSIT", "SWAP"].map(m => `<option ${m === (it.sale_mode || "REFILL") ? "selected" : ""}>${m}</option>`).join("")}</select>
        <input type="number" class="esQty" min="1" step="1" value="${it.qty || 1}" style="width:64px;flex:none">
        <input type="number" class="esPrice" min="0" step="0.01" value="${it.price ?? ""}" style="flex:1;min-width:0">
        <input type="number" class="esDisc" min="0" step="0.01" value="${it.discount_amount || 0}" style="flex:1;min-width:0">
        <input type="text" class="esBrand" placeholder="brand" value="${esc(it.swap_brand || "")}" style="flex:1.2;min-width:0">
        <button class="btn small danger" type="button">&times;</button>`;
      const prodSel = row.querySelector(".esProd"), modeSel = row.querySelector(".esMode"), priceIn = row.querySelector(".esPrice");
      const defaultPrice = () => {
        const p = state.products.find(x => x.id === parseInt(prodSel.value)) || {};
        priceIn.value = ((billLater(p) ? 0 : Number(p.price || 0)) + (modeSel.value === "DEPOSIT" ? Number(p.deposit_price || 0) : 0)).toFixed(2);
      };
      if (it.price === undefined) defaultPrice();
      prodSel.addEventListener("change", () => { defaultPrice(); recalc(); });
      modeSel.addEventListener("change", () => { defaultPrice(); recalc(); });
      row.querySelectorAll("input").forEach(i => i.addEventListener("input", recalc));
      row.querySelector("button").addEventListener("click", () => { row.remove(); recalc(); });
      rowsEl.appendChild(row);
    };
    items.forEach(addRow);
    document.getElementById("esAdd").addEventListener("click", () => { addRow(); recalc(); });
    document.getElementById("esWht").addEventListener("change", recalc);
    recalc();
    document.getElementById("esSave").addEventListener("click", async (ev) => {
      const body = {
        customer_id: custSel.value ? parseInt(custSel.value) : null,
        walkin_name: document.getElementById("esWalkin").value,
        notes: document.getElementById("esNotes").value,
        reason: document.getElementById("esReason").value.trim(),
        withholding_tax: document.getElementById("esWht").checked,
        items: [...rowsEl.querySelectorAll(".pay-row")].map(r => ({
          product_id: parseInt(r.querySelector(".esProd").value), sale_mode: r.querySelector(".esMode").value,
          qty: r.querySelector(".esQty").value, price: r.querySelector(".esPrice").value,
          discount_amount: r.querySelector(".esDisc").value, swap_brand: r.querySelector(".esBrand").value,
          discount_id: r.dataset.discountId ? parseInt(r.dataset.discountId) : null, discount_name: r.dataset.discountName || null,
        })),
      };
      if (!body.reason) { toast("Type the reason for the adjustment", "error"); return; }
      if (!confirm(`Save the adjustment to ${sale.sale_no}?`)) return;
      ev.target.disabled = true;
      try {
        await api(`/sales/${saleId}`, { method: "PUT", body });
        closeModal(); toast(`${sale.sale_no} adjusted`, "success");
        loadSalesPage();
      } catch (e) { ev.target.disabled = false; }
    });
  });
};
// Accounting: bill a Commercial account's 50kg delivery from its POS sale.
// Delivered kg (from the sale) less the residual of the cylinders pulled
// out, at the account's rate. This is a receivable, not a new sale.
window.openSaleBilling = async (saleId) => {
  let d;
  try { d = await api(`/sales/${saleId}/commercial-billing`); } catch (e) { return; }
  if (d.billing) { toast(`Already billed in ${d.billing.delivery_no}`, "error"); return; }
  const rate = d.rate || 0;
  const rateNote = d.rate_source === "CUSTOMER" ? "account rate"
    : rate ? "product default — no rate set on this account yet" : "";
  openModal(`Generate Billing — ${esc(d.sale.sale_no)}`, `
    <div class="field" style="font-size:13.5px;line-height:1.7">
      <b>Customer:</b> ${esc(d.customer ? d.customer.name + " (" + d.customer.account_no + ")" : "—")}<br>
      <b>Sale:</b> ${esc(d.sale.sale_no)}${d.sale.dr_no ? ` &nbsp; <b>DR:</b> ${esc(d.sale.dr_no)}` : ""} &nbsp; <b>Date:</b> ${esc(d.sale.ts)}<br>
      <b>Delivered:</b> ${d.lines.map(l => `${l.qty} x ${esc(l.product_name)} (${l.kg_per_cylinder} kg)`).join(", ")} = <b>${d.delivered_kg} kg</b><br>
      <b>Rate:</b> ${rate ? `${money(rate)}/kg <span style="color:var(--muted)">(${rateNote})</span>` : `<span style="color:var(--danger)">No rate — set this account's rate in Customers first.</span>`}
    </div>
    ${d.possible_duplicates.length ? `<div class="field" style="background:#fff4e5;padding:8px 10px;border-radius:8px;font-size:12.5px">
      ⚠ This customer already has 50kg Commercial Billing record(s) since this sale that aren't linked to a sale:
      ${d.possible_duplicates.map(x => `${esc(x.delivery_no)}${x.dr_ref ? " (DR " + esc(x.dr_ref) + ")" : ""} ${money(x.billed_amount)}`).join(", ")}.
      Make sure this delivery wasn't billed there already.</div>` : ""}
    <div class="field"><label>Residual of the cylinders pulled out (kg)</label>
      <p style="font-size:12px;color:var(--muted);margin:0 0 6px">Type the residual kg of each pulled-out cylinder (serial optional). It is deducted from the delivered kg.</p>
      <div style="display:flex;gap:8px;font-size:11.5px;color:var(--muted);font-weight:600">
        <span style="flex:1">Serial no.</span><span style="flex:1">Residual (kg)</span><span style="width:30px"></span></div>
      <div id="sbRows"></div>
      <button class="btn small" id="sbAdd" type="button">+ Add Pull-out</button>
    </div>
    <div class="field"><label>Notes</label><input id="sbNotes"></div>
    <div class="field" id="sbSummary" style="background:var(--bg);padding:10px;border-radius:8px;font-size:13.5px"></div>
    <p style="font-size:12px;color:var(--muted);margin:0">This creates the customer's invoice (Receivables / SOA). It is not recorded as a new sale.</p>
    <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn green" id="sbSave" ${rate ? "" : "disabled"}>Generate Billing</button></div>
  `, () => {
    const rowsEl = document.getElementById("sbRows");
    const recalc = () => {
      const rows = [...rowsEl.querySelectorAll(".pay-row")];
      const residual = rows.reduce((t, r) => t + (parseFloat(r.querySelector(".sbRes").value) || 0), 0);
      const billable = d.delivered_kg - residual;
      document.getElementById("sbSummary").innerHTML = `
        Delivered: <b>${d.delivered_kg.toFixed(2)} kg</b><br>
        Less residual (${rows.length} pulled out): <b>${residual.toFixed(2)} kg</b><br>
        Billable: <b>${billable.toFixed(2)} kg</b> @ ${money(rate)}/kg = <b style="color:var(--green-dark)">${money(billable * rate)}</b>`;
    };
    const addRow = () => {
      const row = document.createElement("div");
      row.className = "pay-row";
      row.innerHTML = `<input type="text" class="sbSerial" placeholder="Serial no." style="flex:1;min-width:0">
        <input type="number" step="0.01" min="0" class="sbRes" placeholder="Residual kg" style="flex:1;min-width:0">
        <button class="btn small danger" type="button">&times;</button>`;
      row.querySelector(".sbRes").addEventListener("input", recalc);
      row.querySelector("button").addEventListener("click", () => { row.remove(); recalc(); });
      rowsEl.appendChild(row);
    };
    for (let i = 0; i < d.delivered_qty; i++) addRow();
    document.getElementById("sbAdd").addEventListener("click", () => { addRow(); recalc(); });
    recalc();
    document.getElementById("sbSave").addEventListener("click", async (ev) => {
      const pullouts = [...rowsEl.querySelectorAll(".pay-row")].map(r => ({
        serial_no: r.querySelector(".sbSerial").value, residual_kg: r.querySelector(".sbRes").value,
      })).filter(p => p.serial_no || p.residual_kg);
      ev.target.disabled = true;
      try {
        const r = await api(`/sales/${saleId}/commercial-billing`, { method: "POST",
          body: { pullouts, notes: document.getElementById("sbNotes").value } });
        closeModal();
        toast(`Billed ${r.billable_kg} kg = ${money(r.billed_amount)} (${r.delivery_no})`, "success");
        loadSalesPage();
      } catch (e) { ev.target.disabled = false; }
    });
  });
};
window.printSaleDR = async (id) => {
  try { printDeliveryReceipt(await api(`/sales/${id}`)); } catch (e) {}
};
window.voidSale = async (id) => {
  if (!confirm("Void this sale? This will reverse the inventory and cannot be undone.")) return;
  try { await api(`/sales/${id}/void`, { method: "POST" }); toast("Sale voided", "success"); loadSalesPage(); }
  catch (e) {}
};
window.viewSale = async (id) => {
  const result = await api(`/sales/${id}`);
  const { sale, items, payments } = result;
  openModal(`Sale ${sale.sale_no}`, `
    <div class="field"><b>Date:</b> ${sale.ts} &nbsp; <b>Location:</b> ${esc(sale.location_name)}</div>
    <div class="field"><b>Cashier:</b> ${esc(sale.cashier_name)} &nbsp; <b>Customer:</b> ${esc(sale.customer_name || "Walk-in")}</div>
    <div class="field"><b>Status:</b> <span class="badge ${sale.status === 'VOIDED' ? 'danger' : 'blue'}">${sale.status}</span>
      ${sale.dr_no ? ` &nbsp; <b>Delivery Receipt:</b> ${esc(sale.dr_no)}` : ""}
      ${sale.edited_at ? `<br><span style="font-size:12.5px;color:var(--muted)">Adjusted ${esc(sale.edited_at)}${sale.edited_by_name ? " by " + esc(sale.edited_by_name) : ""} (see Audit Log for the details)</span>` : ""}</div>
    <div class="table-wrap"><table><thead><tr><th>Item</th><th>Qty</th><th>Price</th><th>Subtotal</th></tr></thead><tbody>
      ${items.map(i => `<tr><td>${esc(i.product_name)}${i.sale_mode === 'DEPOSIT' ? ' <span class="badge warn">DEPOSIT</span>' : ''}${i.sale_mode === 'SWAP' ? ` <span class="badge blue">SWAP</span><br><span style="font-size:12px">from ${esc(i.swap_brand)}${i.swap_fee > 0 ? ` &middot; fee ${money(i.swap_fee)}/cyl` : ""}</span>` : ''}${i.discount_amount > 0 ? `<br><span style="font-size:12px;color:var(--green-dark)">${esc(i.discount_name)} &minus;${money(i.discount_amount)}</span>` : ""}</td><td>${i.qty}</td><td>${money(i.price)}</td><td>${money(i.subtotal)}</td></tr>`).join("")}
    </tbody></table></div>
    <div style="margin-top:10px">
      <div class="cart-total-row"><span>Subtotal</span><span>${money(sale.subtotal)}</span></div>
      <div class="cart-total-row"><span>Discount</span><span>${money(sale.discount)}</span></div>
      <div class="cart-total-row grand"><span>Total</span><span>${money(sale.total)}</span></div>
      ${sale.withholding_tax > 0 ? `<div class="cart-total-row"><span>Less 1% withholding tax</span><span>&minus;${money(sale.withholding_tax)}</span></div>
      <div class="cart-total-row grand"><span>Amount Due</span><span>${money(sale.total - sale.withholding_tax)}</span></div>` : ""}
    </div>
    <div class="field" style="margin-top:10px"><b>Payments:</b><br>
      ${payments.length ? payments.map(p => `${esc(p.method)}: ${money(p.amount)} ${p.reference ? '(' + esc(p.reference) + ')' : ''}`).join("<br>") : "None recorded"}
    </div>
    <div class="field"><b>Paid:</b> ${money(sale.paid)} &nbsp; <b>Balance:</b> ${money(sale.balance)}</div>
    <div class="modal-actions">
      <button class="btn" onclick="closeModal()">Close</button>
      ${sale.sale_type === "CREDIT" ? `<button class="btn green" id="drBtn">Print Delivery Receipt</button>` : ""}
      <button class="btn green" id="reprintBtn">Reprint Receipt</button>
    </div>
  `, () => {
    document.getElementById("reprintBtn").addEventListener("click", () => printReceipt(result));
    const drBtn = document.getElementById("drBtn");
    if (drBtn) drBtn.addEventListener("click", () => printDeliveryReceipt(result));
  });
};

// One-time fix: old POS credit sales to Commercial accounts that charged the
// 50kg SRP. Preview first, then apply to the ticked sales (Admin).
async function openCommercialSoaFix() {
  let data;
  try { data = await api("/admin/commercial-soa-fix"); } catch (e) { return; }
  const row = (e, tick) => `<tr>
    <td>${tick ? `<input type="checkbox" class="cfxChk" value="${e.sale_id}" checked style="width:auto">` : ""}</td>
    <td>${esc(e.date.slice(0, 10))}</td><td>${esc(e.sale_no)}${e.dr_no ? `<div style="font-size:11px;color:var(--muted)">${esc(e.dr_no)}</div>` : ""}</td>
    <td>${esc(e.customer_name)} (${esc(e.account_no)})</td>
    <td>${e.lines.map(l => `${l.qty} x ${esc(l.product_name)} @ ${money(l.srp_price)}`).join("<br>")}</td>
    <td>${money(e.old_total)} &rarr; <b>${money(e.new_total)}</b></td>
    <td>${money(e.old_balance)} &rarr; <b>${money(e.new_balance)}</b></td>
    ${tick ? "" : `<td style="color:var(--danger);font-size:12px">${esc(e.reason)}</td>`}</tr>`;
  openModal("Fix old Commercial SOA", `
    <p style="font-size:13px;margin-top:0">Old <b>credit</b> sales to <b>Commercial</b> customers that charged the 50kg SRP. Applying turns those 50kg lines into
      delivery-only lines (the DR stays, stock is not touched) and lowers the sale and its receivable, so the SOA shows only what was billed
      in 50kg Commercial Billing. Other items on the sale stay charged. A backup of the database is saved first.</p>
    ${data.sales.length ? `<div class="table-wrap" style="max-height:340px;overflow:auto"><table><thead><tr><th></th><th>Date</th><th>Sale / DR</th><th>Customer</th><th>50kg lines (SRP)</th><th>Total</th><th>Balance</th></tr></thead>
      <tbody>${data.sales.map(e => row(e, true)).join("")}</tbody></table></div>
      <p style="font-size:13px"><b>${data.sales.length}</b> sale(s), balance lowered by <b>${money(data.total_removed)}</b> in total.</p>`
      : `<p style="color:var(--muted)">Nothing to fix — no old Commercial credit sales with a 50kg SRP charge.</p>`}
    ${data.skipped.length ? `<h4 style="margin-bottom:4px">Skipped (fix by hand)</h4><div class="table-wrap"><table><thead><tr><th></th><th>Date</th><th>Sale / DR</th><th>Customer</th><th>50kg lines</th><th>Total</th><th>Balance</th><th>Why</th></tr></thead>
      <tbody>${data.skipped.map(e => row(e, false)).join("")}</tbody></table></div>` : ""}
    <div class="modal-actions"><button class="btn" onclick="closeModal()">Close</button>
      ${data.sales.length ? `<button class="btn green" id="cfxApply">Apply fix to ticked sales</button>` : ""}</div>
  `, () => {
    const btn = document.getElementById("cfxApply");
    if (!btn) return;
    btn.addEventListener("click", async () => {
      const ids = [...document.querySelectorAll(".cfxChk:checked")].map(x => parseInt(x.value));
      if (!ids.length) { toast("Tick at least one sale", "error"); return; }
      if (!confirm(`Remove the 50kg SRP charge from ${ids.length} sale(s)? A database backup is saved first.`)) return;
      try {
        const r = await api("/admin/commercial-soa-fix", { method: "POST", body: { sale_ids: ids } });
        closeModal(); toast(`Fixed ${r.fixed.length} sale(s). Backup: ${r.backup}`, "success");
      } catch (e) {}
    });
  });
}

// ================================================================== CUSTOMERS
async function renderCustomers(c) {
  const isAdmin = isAdminLike();
  const canPortal = ["ADMIN", "OPERATION_HEAD", "MANAGER", "ACCOUNTING"].includes(state.user.role);
  const canRate = canCommercialBilling();
  const isCommercialCat = cat => (cat || "").trim().toLowerCase() === "commercial";
  const categories = await api("/customer-categories");
  c.innerHTML = `<div style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap">
    <input type="text" id="custSearch" placeholder="Search customers..." style="flex:1;min-width:180px">
    ${isAdmin ? `
      <button class="btn green" id="newCustBtn">+ New Customer</button>
      <button class="btn" id="manageCatBtn">Manage Categories</button>
      <a class="btn" href="/api/customers/template">Download CSV Template</a>
      <button class="btn" id="importCustBtn">Import CSV</button>
      <button class="btn" id="commercialFixBtn" title="Remove the 50kg SRP from old Commercial credit sales">Fix old Commercial SOA</button>
    ` : ""}
  </div><div id="custTable"></div>`;

  function categoryOptions(selected) {
    return categories.map(cat => `<option value="${esc(cat.name)}" ${cat.name === selected ? "selected" : ""}>${esc(cat.name)}</option>`).join("");
  }

  let custList = [];
  async function load(q = "") {
    const list = custList = await api("/customers?q=" + encodeURIComponent(q));
    document.getElementById("custTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Account #</th><th>Name</th><th>Category</th><th>Phone</th><th>Terms</th><th>Credit Limit</th><th>Status</th><th></th>
    </tr></thead><tbody>
      ${list.map(cu => `<tr><td>${cu.account_no}</td><td>${esc(cu.name)}</td>
        <td><span class="badge blue">${esc(cu.category)}</span>
          ${isCommercialCat(cu.category) ? `<br><span style="font-size:12px;color:${cu.commercial_rate ? "var(--muted)" : "var(--danger)"}">Rate: ${cu.commercial_rate ? money(cu.commercial_rate) + "/kg" : "not set"}</span>` : ""}</td>
        <td>${esc(cu.phone || "")}</td>
        <td>${cu.terms_days} days</td><td>${money(cu.credit_limit)}</td>
        <td><span class="badge ${cu.status === 'ACTIVE' ? 'green' : 'gray'}">${cu.status}</span>
          ${cu.portal_username ? `<br><span class="badge ${cu.portal_active ? 'blue' : 'gray'}" title="Portal username: ${esc(cu.portal_username)}">Portal${cu.portal_active ? "" : " (off)"}</span>` : ""}</td>
        <td style="display:flex;gap:6px">
          <button class="btn small" onclick="openCustomerLedger(${cu.id})">Ledger / SOA</button>
          ${canPortal ? `<button class="btn small" onclick="openPortalLogin(${cu.id})">Portal Login</button>` : ""}
          ${canRate && isCommercialCat(cu.category) ? `<button class="btn small" onclick="openCommercialRate(${cu.id})">Rate</button>` : ""}
          ${isAdmin ? `<button class="btn small" onclick="editCustomer(${cu.id})">Edit</button>
            <button class="btn small danger" onclick="deleteCustomer(${cu.id})">Delete</button>` : ""}
        </td></tr>`).join("")}
    </tbody></table></div>`;
  }

  // Accounting: the Commercial account's rate per kg, used automatically on every billing.
  window.openCommercialRate = (cid) => {
    const cu = custList.find(x => x.id === cid) || {};
    const rate = cu.commercial_rate;
    openModal(`Commercial Rate — ${esc(cu.name || "")}`, `
      <p style="font-size:13px;color:var(--muted);margin-top:0">Price per kg used automatically when this account is billed
        (Generate Billing, DR / WIWO / Reading billing). Leave blank to use the product's default price per kg.</p>
      <div class="field"><label>Rate per kg</label><input type="number" step="0.01" min="0" id="crRate" value="${rate || ""}"></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="crSave">Save</button></div>
    `, () => {
      document.getElementById("crSave").addEventListener("click", async () => {
        try { await api(`/customers/${cid}/commercial-rate`, { method: "PUT", body: { rate: document.getElementById("crRate").value } }); }
        catch (e) { return; }
        closeModal(); toast("Rate saved", "success"); load(document.getElementById("custSearch").value);
      });
    });
  };

  window.openPortalLogin = async (cid) => {
    let d;
    try { d = await api(`/customers/${cid}/portal-account`); } catch (e) { return; }
    const a = d.account, portalUrl = `${location.origin}/portal`;
    const genPw = () => {
      const chars = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789";
      const buf = new Uint32Array(10); crypto.getRandomValues(buf);
      return [...buf].map(n => chars[n % chars.length]).join("");
    };
    openModal(`Customer Portal Login — ${esc(d.customer.name)}`, `
      <p style="font-size:13px;color:var(--muted);margin-top:0">The customer logs in at
        <code>${esc(portalUrl)}</code> to place orders, follow their order status, see purchases and, for credit / terms accounts, their billing and SOA.</p>
      ${a ? `<p style="font-size:13px">Status: <span class="badge ${a.active ? "green" : "gray"}">${a.active ? "Active" : "Disabled"}</span>
        ${d.locked ? ` <span class="badge danger">Locked (too many wrong passwords) — saving unlocks it</span>` : ""}
        · Last login: ${esc(a.last_login || "never")}</p>` : `<p style="font-size:13px">No portal login yet.</p>`}
      <div class="field"><label>Username</label><input id="plUser" value="${esc(a ? a.username : d.suggested_username)}" autocapitalize="none"></div>
      <div class="field"><label>${a ? "New password (leave blank to keep the current one)" : "Password"}</label>
        <div style="display:flex;gap:6px"><input id="plPass" type="text" autocomplete="off" placeholder="at least 8 characters">
        <button class="btn small" id="plGen" type="button">Generate</button></div></div>
      <div class="field"><label style="display:flex;align-items:center;gap:6px;font-weight:400">
        <input type="checkbox" id="plActive" style="width:auto" ${!a || a.active ? "checked" : ""}> Login enabled</label></div>
      <div class="modal-actions">
        ${a ? `<button class="btn danger" id="plDelete">Remove Login</button>` : ""}
        <button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="plSave">${a ? "Save" : "Create Login"}</button></div>
    `, () => {
      document.getElementById("plGen").addEventListener("click", () => { document.getElementById("plPass").value = genPw(); });
      document.getElementById("plSave").addEventListener("click", async () => {
        const username = document.getElementById("plUser").value.trim(), password = document.getElementById("plPass").value;
        try {
          await api(`/customers/${cid}/portal-account`, { method: "POST", body: {
            username, password, active: document.getElementById("plActive").checked } });
        } catch (e) { return; }
        closeModal(); load(document.getElementById("custSearch").value);
        if (password) {
          openModal("Portal Login Saved", `
            <p>Give these to the customer (the password is not shown again):</p>
            <div class="card" style="font-size:14px;line-height:1.8">Link: <b>${esc(portalUrl)}</b><br>Username: <b>${esc(username.toLowerCase())}</b><br>Password: <b>${esc(password)}</b></div>
            <div class="modal-actions"><button class="btn" id="plCopy">Copy</button><button class="btn green" onclick="closeModal()">Done</button></div>`, () => {
            document.getElementById("plCopy").addEventListener("click", () =>
              navigator.clipboard.writeText(`GUFC Customer Portal\n${portalUrl}\nUsername: ${username.toLowerCase()}\nPassword: ${password}`)
                .then(() => toast("Copied", "success")));
          });
        } else toast("Portal login saved", "success");
      });
      const del = document.getElementById("plDelete");
      if (del) del.addEventListener("click", async () => {
        if (!confirm("Remove this customer's portal login? They will no longer be able to log in.")) return;
        try { await api(`/customers/${cid}/portal-account`, { method: "DELETE" }); } catch (e) { return; }
        closeModal(); toast("Portal login removed", "success"); load(document.getElementById("custSearch").value);
      });
    });
  };

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
    document.getElementById("commercialFixBtn").addEventListener("click", openCommercialSoaFix);

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
      <div class="cart-total-row"><span><b>Beginning Balance</b></span><span><b>${money(data.beginning_balance)}</b></span></div>
      ${data.previous_arrears || data.balance_forward ? `<div style="font-size:12.5px;color:var(--muted);padding:0 0 4px 12px">
        Previous arrears ${money(data.previous_arrears)}${data.balance_forward ? ` &middot; balance forward (before From) ${money(data.balance_forward)}` : ""}</div>` : ""}
      <div class="table-wrap" style="margin:8px 0"><table><thead><tr><th>Date</th><th>Type</th><th>Reference</th><th>Charges</th><th>Payments</th><th>Balance</th></tr></thead><tbody>
        ${data.entries.length ? data.entries.map(e => `<tr><td>${e.date}</td>
          <td><span class="badge ${e.type === 'PAYMENT' ? 'green' : 'warn'}">${e.type}</span></td><td>${esc(e.ref)}</td>
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
  const role = state.user.role;
  const canExport = ["ADMIN", "OPERATION_HEAD", "MANAGER", "ACCOUNTING"].includes(role);
  const canArrears = ["ADMIN", "OPERATION_HEAD", "ACCOUNTING"].includes(role);
  const statusColor = s => ({ PAID: "green", CURRENT: "blue", "DUE SOON": "warn", OVERDUE: "danger", "PARTIALLY PAID": "warn" }[s] || "gray");
  c.innerHTML = `<div style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap">
    <button class="btn" id="agingBtn">Aging Report</button>
    ${canArrears ? `<button class="btn green" id="addArrearsBtn">+ Add Previous Arrears</button>
      <button class="btn" id="uploadArrearsBtn">Upload Previous Arrears (CSV)</button>` : ""}
  </div>
  <div class="table-wrap"><table><thead><tr>
    <th>Invoice #</th><th>Customer</th><th>Sale #</th><th>Invoice Date</th><th>Due Date</th><th>Amount</th><th>Paid</th><th>Balance</th><th>Status</th><th></th>
  </tr></thead><tbody>
    ${rows.map(r => `<tr>
      <td>${esc(r.invoice_no)}</td><td>${esc(r.customer_name)} (${r.account_no})</td>
      <td>${r.is_opening_balance ? '<span class="badge gray">PREV. ARREARS</span>' : esc(r.sale_no)}</td>
      <td>${r.invoice_date}</td><td>${r.due_date}</td><td>${money(r.amount)}</td><td>${money(r.paid)}</td><td>${money(r.balance)}</td>
      <td><span class="badge ${statusColor(r.status)}">${r.status}</span></td>
      <td style="display:flex;gap:6px">${r.balance > 0.009 ? `<button class="btn small green" onclick="payReceivable(${r.id}, ${r.balance})">Record Payment</button>` : ""}
        ${canArrears && r.is_opening_balance && r.paid <= 0 ? `<button class="btn small danger" onclick="deleteArrears(${r.id})">Remove</button>` : ""}</td>
    </tr>`).join("")}
  </tbody></table></div>`;
  document.getElementById("agingBtn").addEventListener("click", () => openAgingReport(canExport));
  if (!canArrears) return;

  const reload = () => renderReceivables(document.getElementById("content"));
  const locOpts = state.locations.map(l => `<option value="${l.id}" ${l.id === state.user.location_id ? "selected" : ""}>${esc(l.name)}</option>`).join("");
  const arrearsHelp = `<p style="font-size:12.5px;color:var(--muted);margin-top:0">
    Unpaid balances from before this system. They show up in Receivables and the Aging Report, can be paid like any invoice,
    and roll into the <b>Beginning Balance</b> of the customer's Statement of Account. They are not counted as sales.</p>`;

  document.getElementById("addArrearsBtn").addEventListener("click", async () => {
    const customers = await api("/customers");
    const today = new Date().toISOString().slice(0, 10);
    openModal("Add Previous Arrears", `${arrearsHelp}
      <div class="field"><label>Customer</label><select id="obCust"><option value="">-- choose --</option>
        ${customers.map(cu => `<option value="${cu.id}">${esc(cu.name)} (${cu.account_no})</option>`).join("")}</select></div>
      <div class="field"><label>Location (branch the account belongs to)</label><select id="obLoc">${locOpts}</select></div>
      <div class="field"><label>Amount Owed</label><input type="number" id="obAmount" min="0" step="0.01"></div>
      <div class="field"><label>Balance As Of</label><input type="date" id="obAsOf" value="${today}" max="${today}"></div>
      <div class="field"><label>Due Date (optional — defaults to as-of date + customer terms)</label><input type="date" id="obDue"></div>
      <div class="field"><label>Old Invoice / Reference # (optional)</label><input id="obRef"></div>
      <div class="field"><label>Notes</label><input id="obNotes"></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="obSaveBtn">Save</button></div>
    `, () => {
      document.getElementById("obSaveBtn").addEventListener("click", async () => {
        const body = {
          customer_id: document.getElementById("obCust").value,
          location_id: document.getElementById("obLoc").value,
          amount: document.getElementById("obAmount").value,
          as_of_date: document.getElementById("obAsOf").value,
          due_date: document.getElementById("obDue").value || null,
          reference: document.getElementById("obRef").value,
          notes: document.getElementById("obNotes").value,
        };
        if (!body.customer_id) { toast("Choose a customer", "error"); return; }
        if (!(parseFloat(body.amount) > 0)) { toast("Enter the amount owed", "error"); return; }
        try { await api("/receivables/opening-balance", { method: "POST", body }); closeModal(); toast("Previous arrears added", "success"); reload(); }
        catch (e) {}
      });
    });
  });

  document.getElementById("uploadArrearsBtn").addEventListener("click", () => {
    openModal("Upload Previous Arrears (CSV)", `${arrearsHelp}
      <p style="font-size:13px"><a href="/api/receivables/opening-balance/template">Download the CSV template</a>.
        Columns: <code>account_no</code>, <code>amount</code>, <code>as_of_date</code> (YYYY-MM-DD), and optional
        <code>due_date</code>, <code>reference</code>, <code>notes</code>. One row per unpaid invoice (or one row per customer for a lump sum).</p>
      <p style="font-size:12.5px;color:var(--muted)">If any row has an error, nothing is saved — fix the file and upload again.</p>
      <div class="field"><label>Location (branch these accounts belong to)</label><select id="obCsvLoc">${locOpts}</select></div>
      <div class="field"><input type="file" id="obCsvFile" accept=".csv"></div>
      <div id="obImportResult"></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Close</button>
        <button class="btn green" id="obUploadBtn">Upload</button></div>
    `, () => {
      document.getElementById("obUploadBtn").addEventListener("click", async () => {
        const file = document.getElementById("obCsvFile").files[0];
        if (!file) { toast("Choose a CSV file first", "error"); return; }
        const fd = new FormData();
        fd.append("file", file);
        fd.append("location_id", document.getElementById("obCsvLoc").value);
        const out = document.getElementById("obImportResult");
        try {
          const res = await fetch("/api/receivables/opening-balance/import", { method: "POST", body: fd, credentials: "include" });
          const data = await res.json();
          if (!res.ok) {
            out.innerHTML = data.errors && data.errors.length
              ? `<p style="color:var(--danger);font-size:12.5px"><b>Nothing was saved.</b><br>${data.errors.map(esc).join("<br>")}</p>` : "";
            toast(data.error || "Upload failed — see errors", "error");
            return;
          }
          out.innerHTML = `<p style="color:var(--green-dark);font-weight:600">${data.created} row(s) imported — total ${money(data.total)}.</p>`;
          toast(`${data.created} arrears row(s) imported`, "success");
          reload();
        } catch (e) { toast("Upload failed", "error"); }
      });
    });
  });

  window.deleteArrears = async (id) => {
    if (!confirm("Remove this previous-arrears entry? Use this only to correct an upload mistake.")) return;
    try { await api(`/receivables/${id}/opening-balance`, { method: "DELETE" }); toast("Entry removed", "success"); reload(); }
    catch (e) {}
  };
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
      <th>Customer</th><th>Category</th>${bucketLabels.map(([, l]) => `<th>${l}</th>`).join("")}<th>Total</th>
    </tr></thead><tbody>
      ${data.rows.length ? data.rows.map(r => `<tr>
        <td>${esc(r.customer_name)} (${r.account_no})</td><td>${esc(r.customer_category)}</td>
        ${bucketLabels.map(([k]) => `<td>${r[k] ? money(r[k]) : ""}</td>`).join("")}
        <td style="font-weight:700">${money(r.total)}</td>
      </tr>`).join("") : `<tr><td colspan="9" style="color:var(--muted)">No outstanding receivables.</td></tr>`}
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

// Reload products and locations so pick-lists never offer something that was
// deleted or re-encoded (e.g. after a System Reset) since the page was opened.
async function refreshLookups() {
  try {
    const [products, locations] = await Promise.all([api("/products"), api("/locations")]);
    state.products = products; state.locations = locations;
  } catch (e) { /* keep the lists we already have */ }
}

// ================================================================== INVENTORY
async function renderInventory(c) {
  c.innerHTML = "Loading...";
  const isAdmin = isAdminLike();
  async function load() {
    const rows = await api("/inventory");
    const empties = await api("/inventory/empties");
    const swapped = await api("/inventory/swapped-empties");
    const brandTotals = {};
    swapped.forEach(r => brandTotals[r.brand] = (brandTotals[r.brand] || 0) + r.quantity);
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
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin:20px 0 8px">
        <h3 style="margin:0">Empty Cylinders on Hand</h3>
        ${isAdmin ? `<button class="btn green" id="adjustEmptiesBtn">+ Adjust Empties</button>` : ""}
      </div>
      <p style="color:var(--muted);font-size:13px;margin-top:0">Returned via refill sales and deposit returns — dispatch them to the warehouse or plant from the Dispatch page.</p>
      <div class="table-wrap"><table><thead><tr><th>Location</th><th>Product</th><th>Empty Qty</th></tr></thead><tbody>
        ${empties.filter(e => e.quantity !== 0).length ? empties.filter(e => e.quantity !== 0).map(e => `<tr>
          <td>${esc(e.location_name)}</td><td>${esc(e.product_name)}</td>
          <td><b${e.quantity < 0 ? ' style="color:var(--danger)" title="Negative — a voided refill sale reversed an empty that was already dispatched. Recount and adjust."' : ""}>${e.quantity}</b></td></tr>`).join("")
          : `<tr><td colspan="3" style="color:var(--muted)">No empty cylinders on hand.</td></tr>`}
      </tbody></table></div>
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin:20px 0 8px">
        <h3 style="margin:0">Swapped Empties (Other Brands)</h3>
        ${isAdmin ? `<button class="btn green" id="adjustSwappedBtn">+ Adjust Swapped Empties</button>` : ""}
      </div>
      <p style="color:var(--muted);font-size:13px;margin-top:0">Empties of other LPG brands taken in on swap sales — dispatch them to the plant or another location from the Dispatch page.</p>
      ${Object.keys(brandTotals).length ? `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
        ${Object.entries(brandTotals).sort((a, b) => a[0].localeCompare(b[0])).map(([b, q]) =>
          `<div class="card" style="padding:8px 12px;min-width:120px"><div style="font-size:12px;color:var(--muted)">${esc(b)}</div><div style="font-size:20px;font-weight:800">${q}</div></div>`).join("")}
      </div>` : ""}
      <div class="table-wrap"><table><thead><tr><th>Location</th><th>Brand</th><th>Size (swapped for)</th><th>Empty Qty</th></tr></thead><tbody>
        ${swapped.length ? swapped.map(e => `<tr>
          <td>${esc(e.location_name)}</td><td><b>${esc(e.brand)}</b></td><td>${esc(e.product_name)}</td>
          <td><b${e.quantity < 0 ? ' style="color:var(--danger)" title="Negative — a voided swap sale reversed an empty that was already dispatched. Recount and adjust."' : ""}>${e.quantity}</b></td></tr>`).join("")
          : `<tr><td colspan="4" style="color:var(--muted)">No swapped empties on hand.</td></tr>`}
      </tbody></table></div>`;
    if (isAdmin) {
      document.getElementById("adjustStockBtn").addEventListener("click", openAdjustStockModal);
      document.getElementById("adjustEmptiesBtn").addEventListener("click", openAdjustEmptiesModal);
      document.getElementById("adjustSwappedBtn").addEventListener("click", openAdjustSwappedModal);
    }
  }
  async function openAdjustSwappedModal() {
    await refreshLookups();
    const locOpts = state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
    const prodOpts = state.products.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
    openModal("Adjust Swapped Empties", `
      <div class="field"><label>Location</label><select id="sLoc">${locOpts}</select></div>
      <div class="field"><label>Brand</label><select id="sBrand">
        ${SWAP_BRANDS.map(b => `<option>${esc(b)}</option>`).join("")}<option value="__other">Other (type below)</option></select></div>
      <div class="field hidden" id="sOtherWrap"><label>Other brand</label><input type="text" id="sOther" placeholder="Brand name"></div>
      <div class="field"><label>Size (swapped for)</label><select id="sProd">${prodOpts}</select></div>
      <div class="field"><label>Quantity Change</label><input type="number" id="sDelta" placeholder="e.g. 10 or -5" value="0">
        <div style="font-size:11.5px;color:var(--muted);margin-top:3px">Positive to add, negative to remove (e.g. damaged / written off).</div></div>
      <div class="field"><label>Reason (required)</label><input type="text" id="sReason" placeholder="e.g. Physical count correction"></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="sSaveBtn">Save Adjustment</button></div>
    `, () => {
      const sel = document.getElementById("sBrand");
      sel.addEventListener("change", () => document.getElementById("sOtherWrap").classList.toggle("hidden", sel.value !== "__other"));
      document.getElementById("sSaveBtn").addEventListener("click", async () => {
        const delta = parseInt(document.getElementById("sDelta").value, 10);
        const reason = document.getElementById("sReason").value.trim();
        const brand = sel.value === "__other" ? document.getElementById("sOther").value.trim() : sel.value;
        if (!brand) { toast("Enter the brand", "error"); return; }
        if (!delta) { toast("Enter a non-zero quantity change", "error"); return; }
        if (!reason) { toast("A reason is required", "error"); return; }
        try {
          await api("/inventory/swapped-empties/adjust", { method: "POST", body: {
            location_id: document.getElementById("sLoc").value,
            product_id: document.getElementById("sProd").value, brand, delta, reason,
          }});
          closeModal(); toast("Swapped empties adjusted", "success"); load();
        } catch (e) {}
      });
    });
  }
  async function openAdjustEmptiesModal() {
    await refreshLookups();
    const locOpts = state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
    const prodOpts = state.products.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
    openModal("Adjust Empty Cylinders", `
      <div class="field"><label>Location</label><select id="eLoc">${locOpts}</select></div>
      <div class="field"><label>Product</label><select id="eProd">${prodOpts}</select></div>
      <div class="field"><label>Quantity Change</label><input type="number" id="eDelta" placeholder="e.g. 10 or -5" value="0">
        <div style="font-size:11.5px;color:var(--muted);margin-top:3px">Positive to add empties, negative to remove (e.g. damaged / written off).</div></div>
      <div class="field"><label>Reason (required)</label><input type="text" id="eReason" placeholder="e.g. Physical count correction"></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="eSaveBtn">Save Adjustment</button></div>
    `, () => {
      document.getElementById("eSaveBtn").addEventListener("click", async () => {
        const delta = parseInt(document.getElementById("eDelta").value, 10);
        const reason = document.getElementById("eReason").value.trim();
        if (!delta) { toast("Enter a non-zero quantity change", "error"); return; }
        if (!reason) { toast("A reason is required", "error"); return; }
        try {
          await api("/inventory/empties/adjust", { method: "POST", body: {
            location_id: document.getElementById("eLoc").value,
            product_id: document.getElementById("eProd").value, delta, reason,
          }});
          closeModal(); toast("Empty cylinders adjusted", "success"); load();
        } catch (e) {}
      });
    });
  }
  async function openAdjustStockModal() {
    await refreshLookups();
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
      <select id="ledgerStock"><option value="FULL">Full Cylinders</option><option value="EMPTY">Empty Cylinders</option><option value="SWAPPED">Swapped Empties (other brands)</option></select>
      <select id="ledgerLoc"><option value="">All Locations</option>${state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("")}</select>
      <select id="ledgerProd"><option value="">All Products</option>${state.products.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select>
      <button class="btn" id="ledgerFilterBtn">Filter</button>
    </div>
    <div id="ledgerTable">Loading...</div>`;
  async function load() {
    const loc = document.getElementById("ledgerLoc").value;
    const prod = document.getElementById("ledgerProd").value;
    const qs = new URLSearchParams();
    qs.set("stock", document.getElementById("ledgerStock").value);
    if (loc) qs.set("location_id", loc);
    if (prod) qs.set("product_id", prod);
    const rows = await api("/inventory/movements?" + qs.toString());
    document.getElementById("ledgerTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Date</th><th>Location</th><th>Product</th><th>Change</th><th>Type</th><th>Reference</th><th>Notes</th>
    </tr></thead><tbody>
      ${rows.length ? rows.map(r => `<tr><td>${r.ts}</td><td>${esc(r.location_name)}</td><td>${r.brand ? `<b>${esc(r.brand)}</b> empty &middot; ` : ""}${esc(r.product_name)}</td>
        <td style="color:${r.qty_change >= 0 ? 'var(--green-dark)' : 'var(--danger)'};font-weight:700">${r.qty_change >= 0 ? '+' : ''}${r.qty_change}</td>
        <td><span class="badge blue">${esc(r.type)}</span></td><td>${esc(r.reference || "")}</td><td>${esc(r.notes || "")}</td></tr>`).join("")
        : `<tr><td colspan="7" style="color:var(--muted)">No movements found.</td></tr>`}
    </tbody></table></div>`;
  }
  document.getElementById("ledgerFilterBtn").addEventListener("click", load);
  load();
}

// ================================================================== TRANSFERS / DISPATCH
// Import stock received from a supplier into ANY location (Admin, Operation
// Head, Dispatcher, Supervisor, Order Taker — the "Import Stock" module).
function openImportStockModal(onDone) {
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
    if (state.user.location_id) document.getElementById("ibLoc").value = state.user.location_id;
    document.getElementById("ibSaveBtn").addEventListener("click", async () => {
      const supplier = document.getElementById("ibSupplier").value.trim();
      if (!supplier) { toast("Supplier name is required", "error"); return; }
      try {
        await api("/inventory/inbound", { method: "POST", body: {
          location_id: document.getElementById("ibLoc").value, product_id: document.getElementById("ibProd").value,
          qty: document.getElementById("ibQty").value, supplier, reference: document.getElementById("ibRef").value,
        }});
        closeModal(); toast("Stock received and recorded", "success"); if (onDone) onDone();
      } catch (e) {}
    });
  });
}

async function renderStockImport(c) {
  const locOpts = state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
  c.innerHTML = `<div style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <button class="btn green" id="importStockBtn">+ Import Stock from Supplier</button>
      <select id="ibFilterLoc" style="width:auto"><option value="">All locations</option>${locOpts}</select>
    </div>
    <p style="color:var(--muted);font-size:13px;margin-top:0">Full cylinders received from a supplier / plant into any location. Each import adds to that location's stock and appears in the Inventory Ledger as INBOUND.</p>
    <div id="ibTable">Loading...</div>`;
  async function load() {
    const loc = document.getElementById("ibFilterLoc").value;
    const rows = await api("/inventory/inbound" + (loc ? `?location_id=${loc}` : ""));
    document.getElementById("ibTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Date</th><th>Location</th><th>Product</th><th>Qty</th><th>Supplier / Notes</th><th>Reference</th><th>Recorded by</th>
    </tr></thead><tbody>
      ${rows.length ? rows.map(r => `<tr><td>${esc(r.ts)}</td><td>${esc(r.location_name)}</td><td>${esc(r.product_name)}</td>
        <td>${r.qty}</td><td>${esc(r.notes || "")}</td><td>${esc(r.reference || "")}</td><td>${esc(r.user_name || "")}</td></tr>`).join("")
        : `<tr><td colspan="7" style="color:var(--muted)">No stock imports yet.</td></tr>`}
    </tbody></table></div>`;
  }
  document.getElementById("importStockBtn").addEventListener("click", () => openImportStockModal(load));
  document.getElementById("ibFilterLoc").addEventListener("change", load);
  load();
}

async function renderTransfers(c) {
  c.innerHTML = `<div style="margin-bottom:12px;display:flex;gap:8px">
    <button class="btn green" id="newXferBtn">+ New Transfer / Dispatch</button>
    ${hasModule("stock_import") ? `<button class="btn" id="importStockBtn">+ Import Stock from Supplier</button>` : ""}
  </div><div id="xferTable">Loading...</div>`;
  async function load() {
    const rows = await api("/transfers");
    document.getElementById("xferTable").innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Dispatch #</th><th>Date</th><th>From</th><th>To</th><th>Items</th><th>Status</th><th></th>
    </tr></thead><tbody>
      ${rows.map(t => `<tr><td>${t.transfer_no}</td><td>${t.ts}</td><td>${esc(t.from_name)}</td><td>${esc(t.to_name)}</td>
        <td>${t.items.map(i => `${i.cylinder_state === 'SWAPPED' ? `${esc(i.swap_brand)} empty (${esc(i.product_name)})` : esc(i.product_name)} x${i.qty}${i.cylinder_state === 'EMPTY' ? ' <span class="badge gray">EMPTY</span>' : i.cylinder_state === 'SWAPPED' ? ' <span class="badge warn">SWAPPED</span>' : ' <span class="badge green">FULL</span>'}`).join(", ")}</td>
        <td><span class="badge ${t.status === 'RECEIVED' ? 'green' : t.status === 'CANCELLED' ? 'danger' : 'warn'}">${t.status}</span></td>
        <td>${t.status === 'RELEASED' ? `<button class="btn small green" onclick="receiveXfer(${t.id})">Mark Received</button>` : ""}</td>
      </tr>`).join("")}
    </tbody></table></div>`;
  }
  const importBtn = document.getElementById("importStockBtn");
  if (importBtn) importBtn.addEventListener("click", () => openImportStockModal(load));
  document.getElementById("newXferBtn").addEventListener("click", () => {
    const locOpts = state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
    const prodOpts = state.products.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
    openModal("New Transfer / Dispatch", `
      <div class="field"><label>From</label><select id="xFrom">${locOpts}</select></div>
      <div class="field"><label>To</label><select id="xTo">${locOpts}<option value="PLANT">Plant</option></select></div>
      <div class="field hidden" id="xPlantWrap"><label>Plant Name</label><input id="xPlant" placeholder="e.g. Solane Plant"></div>
      <div class="field"><label>Items</label><div id="xRows"></div>
        <button class="btn small" id="xAddRow" type="button">+ Add Item</button>
        <div style="font-size:11.5px;color:var(--muted);margin-top:4px" id="xHint"></div></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="xSaveBtn">Dispatch</button></div>
    `, () => {
      const stock = { FULL: {}, EMPTY: {}, SWAPPED: {} };
      let swappedRows = [];
      const rowsEl = document.getElementById("xRows");
      const toPlant = () => document.getElementById("xTo").value === "PLANT";
      function stateOpts() {
        return `<option value="FULL">Full</option><option value="EMPTY">Empty</option><option value="SWAPPED">Swapped empty (other brand)</option>`;
      }
      // Product list depends on the row's state: swapped empties are picked
      // as "brand — size" from what's on hand at the origin.
      function prodOptsFor(st) {
        if (st !== "SWAPPED") return prodOpts;
        return swappedRows.length
          ? swappedRows.map(r => `<option value="${r.product_id}|${esc(r.brand)}">${esc(r.brand)} — ${esc(r.product_name)}</option>`).join("")
          : `<option value="">No swapped empties at this location</option>`;
      }
      function syncProd(r) {
        const st = r.querySelector(".xState").value, sel = r.querySelector(".xProd");
        if (sel.dataset.mode === st) return;
        sel.innerHTML = prodOptsFor(st); sel.dataset.mode = st;
      }
      function refreshHints() {
        rowsEl.querySelectorAll(".xRow").forEach(r => {
          syncProd(r);
          const st = r.querySelector(".xState").value, pid = r.querySelector(".xProd").value;
          r.querySelector(".xAvail").textContent = `avail: ${stock[st][pid] ?? 0}`;
        });
      }
      function addRow() {
        const row = document.createElement("div");
        row.className = "xRow";
        row.style.cssText = "display:flex;gap:6px;margin-bottom:6px;align-items:center;flex-wrap:wrap";
        row.innerHTML = `<select class="xProd" style="flex:2;min-width:140px" data-mode="FULL">${prodOpts}</select>
          <select class="xState" style="flex:1;min-width:80px">${stateOpts()}</select>
          <input type="number" class="xQty" value="1" min="1" style="width:80px">
          <span class="xAvail" style="font-size:11.5px;color:var(--muted);min-width:60px"></span>
          <button class="btn small danger xDel" type="button">&times;</button>`;
        row.querySelector(".xDel").addEventListener("click", () => { row.remove(); });
        row.querySelectorAll("select").forEach(el => el.addEventListener("change", refreshHints));
        rowsEl.appendChild(row);
        refreshHints();
      }
      async function loadStock() {
        const from = document.getElementById("xFrom").value;
        const [full, empty, swapped] = await Promise.all([api(`/inventory?location_id=${from}`),
          api(`/inventory/empties?location_id=${from}`), api(`/inventory/swapped-empties?location_id=${from}`)]);
        stock.FULL = {}; stock.EMPTY = {}; stock.SWAPPED = {};
        full.forEach(r => stock.FULL[r.product_id] = r.quantity);
        empty.forEach(r => stock.EMPTY[r.product_id] = r.quantity);
        swappedRows = swapped.filter(r => r.quantity > 0);
        swappedRows.forEach(r => stock.SWAPPED[`${r.product_id}|${r.brand}`] = r.quantity);
        rowsEl.querySelectorAll(".xProd").forEach(sel => { if (sel.dataset.mode === "SWAPPED") sel.dataset.mode = ""; });
        refreshHints();
      }
      document.getElementById("xFrom").addEventListener("change", loadStock);
      document.getElementById("xTo").addEventListener("change", () => {
        document.getElementById("xPlantWrap").classList.toggle("hidden", !toPlant());
        document.getElementById("xHint").textContent = toPlant()
          ? "Full, empty and swapped (other-brand) empties can all go to the plant. Mark Received once the plant confirms delivery." : "";
        refreshHints();
      });
      if (state.locations.length > 1) document.getElementById("xTo").selectedIndex = 1;
      document.getElementById("xAddRow").addEventListener("click", addRow);
      addRow();
      loadStock();

      document.getElementById("xSaveBtn").addEventListener("click", async () => {
        const from = document.getElementById("xFrom").value, to = document.getElementById("xTo").value;
        const plant = document.getElementById("xPlant").value.trim();
        if (to === "PLANT" && !plant) { toast("Enter the plant name", "error"); return; }
        if (to !== "PLANT" && from === to) { toast("Origin and destination must differ", "error"); return; }
        const items = [...rowsEl.querySelectorAll(".xRow")].map(r => {
          const st = r.querySelector(".xState").value, v = r.querySelector(".xProd").value;
          const qty = parseInt(r.querySelector(".xQty").value, 10);
          if (st !== "SWAPPED") return { product_id: v, cylinder_state: st, qty };
          const cut = v.indexOf("|");
          return { product_id: v.slice(0, cut), swap_brand: v.slice(cut + 1), cylinder_state: st, qty };
        });
        if (!items.length) { toast("Add at least one item", "error"); return; }
        if (items.some(i => i.cylinder_state === "SWAPPED" && !i.swap_brand)) { toast("No swapped empties to dispatch from this location", "error"); return; }
        if (items.some(i => !(i.qty > 0))) { toast("Quantities must be at least 1", "error"); return; }
        try {
          await api("/transfers", { method: "POST", body: {
            from_location: from,
            to_location: to === "PLANT" ? null : to,
            to_external: to === "PLANT" ? plant : null,
            items,
          }});
          closeModal(); toast("Dispatched", "success"); load();
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

// ================================================================== END OF DAY REPORT
async function renderEndOfDay(c) {
  const today = new Date().toISOString().slice(0, 10);
  let date = today, userId = "", locationId = "";
  async function load() {
    c.innerHTML = "Loading...";
    const qs = new URLSearchParams({ date });
    if (userId) qs.set("user_id", userId);
    if (locationId) qs.set("location_id", locationId);
    const d = await api("/reports/end-of-day?" + qs.toString());
    const s = d.sales, t = d.totals;
    c.innerHTML = `
      <div class="card" style="margin-bottom:16px">
        <div style="display:flex;gap:8px;align-items:end;flex-wrap:wrap">
          <div class="field" style="margin:0"><label>Date</label><input type="date" id="eodDate" value="${date}" max="${today}"></div>
          ${d.can_pick_staff ? `
          <div class="field" style="margin:0"><label>Staff</label><select id="eodUser">
            <option value="">All staff</option>
            ${d.staff.map(u => `<option value="${u.id}" ${String(u.id) === String(userId) ? "selected" : ""}>${esc(u.name)} (${esc(u.role)})</option>`).join("")}
          </select></div>
          <div class="field" style="margin:0"><label>Location</label><select id="eodLoc">
            <option value="">All locations</option>
            ${state.locations.map(l => `<option value="${l.id}" ${String(l.id) === String(locationId) ? "selected" : ""}>${esc(l.name)}</option>`).join("")}
          </select></div>` : ""}
          <button class="btn" id="eodRun">Run</button>
          <button class="btn green" id="eodPrint">Print</button>
          <a class="btn" href="/api/reports/end-of-day/export?format=pdf&${qs.toString()}">PDF</a>
          <a class="btn" href="/api/reports/end-of-day/export?format=xlsx&${qs.toString()}">Excel</a>
        </div>
        <p style="color:var(--muted);font-size:13px;margin:10px 0 0">
          <b>${esc(d.staff_name)}</b>${d.location_name ? " — " + esc(d.location_name) : ""} · ${d.date}</p>
      </div>
      <div class="grid grid-4">
        <div class="card stat-card green"><div class="label">Total Collected</div><div class="value">${money(t.total)}</div></div>
        <div class="card stat-card"><div class="label">From POS Sales</div><div class="value">${money(t.sales)}</div></div>
        <div class="card stat-card"><div class="label">AR Collections</div><div class="value">${money(t.collections)}</div></div>
        <div class="card stat-card"><div class="label">Charged to Accounts</div><div class="value">${money(s.charged_to_accounts)}</div></div>
      </div>
      <div class="grid grid-2" style="margin-top:16px">
        <div class="card">
          <h3 style="margin-top:0">Collections by Payment Method</h3>
          <div class="table-wrap"><table><thead><tr><th>Method</th><th>#</th><th>POS Sales</th><th>AR Collections</th><th>Total</th></tr></thead><tbody>
            ${d.methods.length ? d.methods.map(m => `<tr><td><b>${esc(m.method)}</b></td><td>${m.count}</td><td>${money(m.sales)}</td><td>${money(m.collections)}</td><td><b>${money(m.total)}</b></td></tr>`).join("")
              : `<tr><td colspan="5" style="color:var(--muted)">No payments received.</td></tr>`}
            <tr style="font-weight:700;background:var(--bg)"><td>TOTAL</td><td>${t.count}</td><td>${money(t.sales)}</td><td>${money(t.collections)}</td><td>${money(t.total)}</td></tr>
          </tbody></table></div>
        </div>
        <div class="card">
          <h3 style="margin-top:0">Sales Summary</h3>
          <div class="cart-total-row"><span>Completed sales (${s.count})</span><span>${money(s.total)}</span></div>
          <div class="cart-total-row"><span>&nbsp;&nbsp;Cash sales</span><span>${money(s.cash_sales)}</span></div>
          <div class="cart-total-row"><span>&nbsp;&nbsp;Credit / terms sales</span><span>${money(s.credit_sales)}</span></div>
          <div class="cart-total-row"><span>&nbsp;&nbsp;Charged to accounts (unpaid)</span><span>${money(s.charged_to_accounts)}</span></div>
          <div class="cart-total-row"><span>Discounts given</span><span>${money(s.discount)}</span></div>
          <div class="cart-total-row"><span>Voided sales (${s.voided_count})</span><span>${money(s.voided_total)}</span></div>
          ${eodReconcileHtml(s)}
        </div>
      </div>
      <div class="card" style="margin-top:16px">
        <h3 style="margin-top:0">Products Sold</h3>
        <div class="table-wrap"><table><thead><tr><th>Product</th><th>Type</th><th>Qty</th><th>Amount</th></tr></thead><tbody>
          ${d.items.length ? d.items.map(i => `<tr><td>${esc(i.product_name)}</td><td>${i.sale_mode}</td><td>${i.qty}</td><td>${money(i.amount)}</td></tr>`).join("")
            : `<tr><td colspan="4" style="color:var(--muted)">No products sold on this date.</td></tr>`}
          ${d.items.length ? `<tr style="font-weight:700;background:var(--bg)"><td>TOTAL</td><td></td><td>${eodItemQty(d)}</td><td>${money(eodItemAmount(d))}</td></tr>` : ""}
        </tbody></table></div>
      </div>
      <div class="card" style="margin-top:16px">
        <h3 style="margin-top:0">Payments Received</h3>
        <div class="table-wrap"><table><thead><tr><th>Time</th><th>Sale / Invoice</th><th>Customer</th><th>Type</th><th>Method</th><th>Reference</th>${d.user_id ? "" : "<th>Collected By</th>"}<th>Amount</th></tr></thead><tbody>
          ${d.payments.length ? d.payments.map(p => `<tr><td>${p.ts.slice(11, 16)}</td>
            <td>${esc(p.payment_source === 'AR_COLLECTION' && p.invoice_no ? p.invoice_no : p.sale_no)}</td>
            <td>${esc(p.customer_name || "Walk-in")}</td>
            <td><span class="badge ${p.payment_source === 'AR_COLLECTION' ? 'warn' : 'blue'}">${p.payment_source === 'AR_COLLECTION' ? 'AR' : 'POS'}</span></td>
            <td>${esc(p.method)}</td><td>${esc(p.reference || "")}</td>${d.user_id ? "" : `<td>${esc(p.collected_by || "")}</td>`}<td>${money(p.amount)}</td></tr>`).join("")
            : `<tr><td colspan="8" style="color:var(--muted)">No payments received on this date.</td></tr>`}
        </tbody></table></div>
      </div>`;

    document.getElementById("eodRun").addEventListener("click", () => {
      date = document.getElementById("eodDate").value || today;
      if (d.can_pick_staff) {
        userId = document.getElementById("eodUser").value;
        locationId = document.getElementById("eodLoc").value;
      }
      load();
    });
    document.getElementById("eodPrint").addEventListener("click", () => printEndOfDay(d));
  }
  load();
}

const eodItemQty = d => d.items.reduce((a, i) => a + (i.qty || 0), 0);
const eodItemAmount = d => d.items.reduce((a, i) => a + (i.amount || 0), 0);

function printEndOfDay(d) {
  const t = d.totals, s = d.sales;
  const row = (a, b, bold) => `<tr${bold ? ' style="font-weight:700"' : ""}><td>${a}</td><td style="text-align:right">${b}</td></tr>`;
  printDocument(`<div style="font-family:Arial,sans-serif;font-size:12px;max-width:720px;margin:auto">
    <div style="text-align:center"><img src="/images/logo.png" style="height:50px"><h2 style="margin:4px 0">GREAT UNLI FOOD CORP.</h2>
      <div>Solane LPG Distributor</div><h3 style="margin:10px 0 2px">END OF DAY REPORT</h3>
      <div>${esc(d.staff_name)}${d.location_name ? " — " + esc(d.location_name) : ""} · ${d.date}</div></div>
    <h4>Collections by Payment Method</h4>
    <table style="width:100%;border-collapse:collapse" border="1" cellpadding="4">
      <tr><th align="left">Method</th><th>#</th><th align="right">POS Sales</th><th align="right">AR Collections</th><th align="right">Total</th></tr>
      ${d.methods.map(m => `<tr><td>${esc(m.method)}</td><td align="center">${m.count}</td><td align="right">${money(m.sales)}</td><td align="right">${money(m.collections)}</td><td align="right">${money(m.total)}</td></tr>`).join("")}
      <tr style="font-weight:700"><td>TOTAL</td><td align="center">${t.count}</td><td align="right">${money(t.sales)}</td><td align="right">${money(t.collections)}</td><td align="right">${money(t.total)}</td></tr>
    </table>
    <h4>Sales Summary</h4>
    <table style="width:100%;border-collapse:collapse" border="1" cellpadding="4">
      ${row(`Completed sales (${s.count})`, money(s.total), true)}
      ${row("&nbsp;&nbsp;Cash sales", money(s.cash_sales))}
      ${row("&nbsp;&nbsp;Credit / terms sales", money(s.credit_sales))}
      ${row("&nbsp;&nbsp;Charged to accounts (unpaid)", money(s.charged_to_accounts))}
      ${row("Discounts given", money(s.discount))}
      ${row(`Voided sales (${s.voided_count})`, money(s.voided_total))}
    </table>
    ${eodReconcileHtml(s, true)}
    <h4>Products Sold</h4>
    <table style="width:100%;border-collapse:collapse" border="1" cellpadding="4">
      <tr><th align="left">Product</th><th align="left">Type</th><th align="right">Qty</th><th align="right">Amount</th></tr>
      ${d.items.length ? d.items.map(i => `<tr><td>${esc(i.product_name)}</td><td>${i.sale_mode}</td><td align="right">${i.qty}</td><td align="right">${money(i.amount)}</td></tr>`).join("")
        : `<tr><td colspan="4">No products sold.</td></tr>`}
      ${d.items.length ? `<tr style="font-weight:700"><td>TOTAL</td><td></td><td align="right">${eodItemQty(d)}</td><td align="right">${money(eodItemAmount(d))}</td></tr>` : ""}
    </table>
    <h4>Payments Received</h4>
    <table style="width:100%;border-collapse:collapse" border="1" cellpadding="3">
      <tr><th>Time</th><th align="left">Sale / Invoice</th><th align="left">Customer</th><th>Type</th><th align="left">Method</th><th align="left">Ref</th><th align="right">Amount</th></tr>
      ${d.payments.length ? d.payments.map(p => `<tr><td>${p.ts.slice(11, 16)}</td>
        <td>${esc(p.payment_source === 'AR_COLLECTION' && p.invoice_no ? p.invoice_no : p.sale_no)}</td><td>${esc(p.customer_name || "Walk-in")}</td>
        <td align="center">${p.payment_source === 'AR_COLLECTION' ? 'AR' : 'POS'}</td><td>${esc(p.method)}</td><td>${esc(p.reference || "")}</td>
        <td align="right">${money(p.amount)}</td></tr>`).join("") : `<tr><td colspan="7">No payments received.</td></tr>`}
    </table>
    <table style="width:100%;margin-top:50px"><tr>
      <td style="text-align:center">______________________________<br>Prepared by: ${esc(d.staff_name)}</td>
      <td style="text-align:center">______________________________<br>Received / verified by</td></tr></table>
    <div style="text-align:center;color:#888;margin-top:16px;font-size:10px">Printed ${new Date().toLocaleString()} by ${esc(d.generated_by)}</div>
  </div>`);
}

function printSwappedReport(d) {
  const t = d.totals;
  const tbl = `style="width:100%;border-collapse:collapse" border="1" cellpadding="3"`;
  printDocument(`<style>@page{size:letter landscape;margin:10mm}</style>
  <div style="font-family:Arial,sans-serif;font-size:11px">
    <div style="text-align:center"><img src="/images/logo.png" style="height:44px"><h2 style="margin:4px 0">GREAT UNLI FOOD CORP.</h2>
      <div>Solane LPG Distributor</div><h3 style="margin:8px 0 2px">SWAPPED CYLINDER REPORT</h3>
      <div>${d.date_from || "All dates"} to ${d.date_to || "present"}${d.location_name ? " — " + esc(d.location_name) : ""}</div></div>
    <h4>Summary by Brand / Size</h4>
    <table ${tbl}><tr><th align="left">Brand Swapped</th><th align="left">Size (swapped for)</th><th align="right">Qty</th><th align="right">Swap Fees</th><th align="right">Amount</th></tr>
      ${d.summary.map(r => `<tr><td>${esc(r.swap_brand)}</td><td>${esc(r.product_name)}</td><td align="right">${r.qty}</td><td align="right">${money(r.swap_fees)}</td><td align="right">${money(r.amount)}</td></tr>`).join("")}
      <tr style="font-weight:700"><td>TOTAL</td><td></td><td align="right">${t.qty}</td><td align="right">${money(t.swap_fees)}</td><td align="right">${money(t.amount)}</td></tr></table>
    <h4>Swap Transactions</h4>
    <table ${tbl}><tr><th align="left">Date</th><th align="left">Sale #</th><th align="left">Location</th><th align="left">Cashier</th><th align="left">Customer</th><th align="left">Brand</th><th align="left">Size</th><th align="right">Qty</th><th align="right">Swap Fees</th><th align="right">Amount</th></tr>
      ${d.rows.length ? d.rows.map(r => `<tr><td>${r.ts.slice(0, 16)}</td><td>${esc(r.sale_no)}</td><td>${esc(r.location_name)}</td><td>${esc(r.cashier)}</td><td>${esc(r.customer_name || "Walk-in")}</td>
        <td>${esc(r.swap_brand)}</td><td>${esc(r.product_name)}</td><td align="right">${r.qty}</td><td align="right">${money(r.swap_fee_total)}</td><td align="right">${money(r.amount)}</td></tr>`).join("")
        : `<tr><td colspan="10">No swap sales in this period.</td></tr>`}</table>
    <h4>Swapped Empties On Hand (not yet dispatched)</h4>
    <table ${tbl}><tr><th align="left">Location</th><th align="left">Brand</th><th align="left">Size</th><th align="right">Qty on Hand</th></tr>
      ${d.on_hand.map(r => `<tr><td>${esc(r.location_name)}</td><td>${esc(r.brand)}</td><td>${esc(r.product_name)}</td><td align="right">${r.quantity}</td></tr>`).join("")}
      <tr style="font-weight:700"><td>TOTAL</td><td></td><td></td><td align="right">${t.on_hand}</td></tr></table>
    <div style="text-align:center;color:#888;margin-top:16px;font-size:10px">Printed ${new Date().toLocaleString()} by ${esc(state.user.name)}</div>
  </div>`);
}

// ================================================================== CUSTOMER GROWTH (LPG cylinders only)
const growthPct = v => v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;

function growthChartSvg(rows) {
  // Stacked bars above the axis: new (green) + returning (blue) = active.
  // Red bars below the axis: customers lapsed vs the period before.
  const W = 760, H = 260, padL = 34, padR = 8, padT = 14, padB = 46;
  const maxUp = Math.max(1, ...rows.map(r => r.active));
  const maxDown = Math.max(0, ...rows.map(r => r.lapsed));
  const plotH = H - padT - padB;
  const scale = plotH / (maxUp + maxDown || 1);
  const zeroY = padT + maxUp * scale;
  const slot = (W - padL - padR) / rows.length;
  const bw = Math.max(6, Math.min(40, slot * 0.62));
  const step = Math.max(1, Math.ceil(rows.length / 12));
  let bars = "";
  rows.forEach((r, i) => {
    const x = padL + i * slot + (slot - bw) / 2;
    const ret = r.active - r.new;
    const hRet = ret * scale, hNew = r.new * scale, hLap = r.lapsed * scale;
    const tip = `${esc(r.label)}: ${r.active} active (${r.new} new, ${ret} returning), ${r.lapsed} lapsed`;
    bars += `<g><title>${tip}</title>
      ${hRet ? `<rect x="${x}" y="${zeroY - hRet}" width="${bw}" height="${hRet}" fill="var(--blue)" rx="2"/>` : ""}
      ${hNew ? `<rect x="${x}" y="${zeroY - hRet - hNew}" width="${bw}" height="${hNew}" fill="var(--green)" rx="2"/>` : ""}
      ${hLap ? `<rect x="${x}" y="${zeroY}" width="${bw}" height="${hLap}" fill="var(--danger)" opacity=".75" rx="2"/>` : ""}
      ${r.active ? `<text x="${x + bw / 2}" y="${zeroY - hRet - hNew - 4}" text-anchor="middle" font-size="10" fill="var(--text)">${r.active}</text>` : ""}
    </g>`;
    if (i % step === 0 || i === rows.length - 1) {
      const short = r.label.includes("–") ? r.start.slice(5).replace("-", "/") : r.label;
      bars += `<text x="${x + bw / 2}" y="${H - padB + 16}" text-anchor="middle" font-size="10" fill="var(--muted)">${esc(short)}</text>`;
    }
  });
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block" role="img" aria-label="Active, new and lapsed customers by period">
    <line x1="${padL}" x2="${W - padR}" y1="${zeroY}" y2="${zeroY}" stroke="var(--border)"/>
    <text x="${padL - 6}" y="${padT + 4}" text-anchor="end" font-size="10" fill="var(--muted)">${maxUp}</text>
    <text x="${padL - 6}" y="${zeroY + 3}" text-anchor="end" font-size="10" fill="var(--muted)">0</text>
    ${maxDown ? `<text x="${padL - 6}" y="${H - padB}" text-anchor="end" font-size="10" fill="var(--muted)">-${maxDown}</text>` : ""}
    ${bars}
    <g font-size="11" fill="var(--text)" transform="translate(${padL},${H - 12})">
      <rect width="10" height="10" y="-9" fill="var(--green)" rx="2"/><text x="14">New</text>
      <rect x="54" width="10" height="10" y="-9" fill="var(--blue)" rx="2"/><text x="68">Returning</text>
      <rect x="140" width="10" height="10" y="-9" fill="var(--danger)" opacity=".75" rx="2"/><text x="154">Lapsed (vs. previous period)</text>
    </g></svg>`;
}

async function renderCustomerGrowth(c) {
  let categories = [];
  try { categories = await api("/customer-categories"); } catch (e) { /* filter just stays empty */ }
  c.innerHTML = `
    <div class="card" style="margin-bottom:16px">
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <select id="cgPeriod"><option value="month">Monthly</option><option value="week">Weekly</option></select>
        <select id="cgCount"></select>
        <label style="font-size:13px;color:var(--muted)">up to</label><input type="date" id="cgEnd">
        <select id="cgLoc"><option value="">All locations</option>
          ${state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("")}</select>
        <select id="cgCat"><option value="">All customer categories</option>
          ${categories.map(k => `<option value="${esc(k.name)}">${esc(k.name)}</option>`).join("")}</select>
        <button class="btn" id="cgRun">Refresh</button>
        <a class="btn small" id="cgXlsx" href="#">Export Excel</a>
        <a class="btn small" id="cgPdf" href="#">Export PDF</a>
      </div>
      <p style="color:var(--muted);font-size:12.5px;margin:10px 0 0">Only <b>LPG cylinder</b> purchases (refill, deposit or swap) count.
        Accessories and other items are ignored. 50kg Commercial Billings count for their customer too. Walk-in sales can't be tracked per customer, so they're shown only as a cylinder count —
        a sale rung up as walk-in by mistake can be linked to its customer with <b>Edit</b> in Sales (Operation Head / Admin).</p>
    </div>
    <div id="cgOut">Loading...</div>
    <div class="card" style="margin-top:16px;min-width:0">
      <h3 style="margin-top:0">Monthly LPG per Customer (kg)</h3>
      <p style="color:var(--muted);font-size:12.5px;margin:0 0 10px">Each customer's completed LPG cylinder orders per month, in kg
        (cylinders × size, e.g. 3 × 11kg = 33 kg; 50kg weight-billed accounts use the actual kg billed; a 50kg delivery not yet billed counts as delivered until it is). Voided sales are excluded.</p>
      <div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;align-items:center">
        <label style="font-size:13px;color:var(--muted)">From</label><input type="month" id="kgFrom">
        <label style="font-size:13px;color:var(--muted)">to</label><input type="month" id="kgTo">
        <select id="kgLoc"><option value="">All locations</option>
          ${state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("")}</select>
        <select id="kgCat"><option value="">All customer categories</option>
          ${categories.map(k => `<option value="${esc(k.name)}">${esc(k.name)}</option>`).join("")}</select>
        <input id="kgSearch" placeholder="Search customer..." style="max-width:180px">
        <button class="btn" id="kgRun">Run</button>
        <a class="btn small" id="kgXlsx" href="#">Export Excel</a>
        <a class="btn small" id="kgPdf" href="#">Export PDF</a>
      </div>
      <div id="kgOut"></div>
    </div>`;

  const periodSel = document.getElementById("cgPeriod"), countSel = document.getElementById("cgCount");
  function fillCounts() {
    const opts = periodSel.value === "week" ? [8, 12, 26, 52] : [6, 12, 24, 36];
    const def = periodSel.value === "week" ? 12 : 12;
    countSel.innerHTML = opts.map(n => `<option value="${n}" ${n === def ? "selected" : ""}>Last ${n} ${periodSel.value === "week" ? "weeks" : "months"}</option>`).join("");
  }
  fillCounts();
  const qs = () => {
    const q = new URLSearchParams({ period: periodSel.value, count: countSel.value });
    const e = document.getElementById("cgEnd").value, l = document.getElementById("cgLoc").value, k = document.getElementById("cgCat").value;
    if (e) q.set("end", e); if (l) q.set("location_id", l); if (k) q.set("category", k);
    return q.toString();
  };
  function updateLinks() {
    document.getElementById("cgXlsx").href = "/api/reports/customer-growth/export?format=xlsx&" + qs();
    document.getElementById("cgPdf").href = "/api/reports/customer-growth/export?format=pdf&" + qs();
  }

  async function load() {
    updateLinks();
    const out = document.getElementById("cgOut");
    let d;
    try { d = await api("/reports/customer-growth?" + qs()); } catch (e) { out.innerHTML = ""; return; }
    const cur = d.current, unit = d.period === "week" ? "week" : "month";
    const delta = (now, before, fmt = v => v) => before === undefined || before === null ? "" :
      `<div style="font-size:12px;margin-top:4px;color:${now > before ? "var(--green)" : now < before ? "var(--danger)" : "var(--muted)"}">
        ${now > before ? "▲" : now < before ? "▼" : "■"} ${fmt(Math.abs(now - before))} vs last ${unit}</div>`;
    const prev = d.previous || {};
    const latestRows = [...d.rows].reverse();
    out.innerHTML = `
      <div style="color:var(--muted);font-size:13px;margin-bottom:8px">This ${unit}: <b style="color:var(--text)">${esc(cur.label)}</b>
        (${cur.start} to ${cur.end})${d.location_name ? " · " + esc(d.location_name) : ""}${d.category ? " · " + esc(d.category) + " customers" : ""}</div>
      <div class="grid grid-4">
        <div class="card stat-card"><div class="label">Active Customers</div><div class="value">${cur.active}</div>${delta(cur.active, prev.active)}</div>
        <div class="card stat-card green"><div class="label">New Customers</div><div class="value">${cur.new}</div>${delta(cur.new, prev.new)}</div>
        <div class="card stat-card"><div class="label">Growth vs Last ${unit === "week" ? "Week" : "Month"}</div>
          <div class="value" style="color:${(cur.growth_pct || 0) > 0 ? "var(--green)" : (cur.growth_pct || 0) < 0 ? "var(--danger)" : "inherit"}">${growthPct(cur.growth_pct)}</div>
          <div style="font-size:12px;margin-top:4px;color:var(--muted)">Net ${cur.net_change >= 0 ? "+" : ""}${cur.net_change} customers</div></div>
        <div class="card stat-card"><div class="label">Retention</div><div class="value">${cur.retention_pct === null ? "—" : cur.retention_pct.toFixed(1) + "%"}</div>
          <div style="font-size:12px;margin-top:4px;color:var(--muted)">${cur.retained} of ${cur.prev_active} came back · ${cur.lapsed} lapsed</div></div>
        <div class="card stat-card"><div class="label">Cylinders to Customers</div><div class="value">${cur.cylinders}</div>${delta(cur.cylinders, prev.cylinders)}</div>
        <div class="card stat-card"><div class="label">Cylinders / Customer</div><div class="value">${cur.cylinders_per_customer.toFixed(2)}</div></div>
        <div class="card stat-card"><div class="label">LPG Sales (customers)</div><div class="value">${money(cur.amount)}</div>${delta(cur.amount, prev.amount, money)}</div>
        <div class="card stat-card"><div class="label">Total Customer Base</div><div class="value">${cur.customer_base}</div>
          <div style="font-size:12px;margin-top:4px;color:var(--muted)">ever bought an LPG cylinder${d.category ? "" : ` · ${cur.walkin_cylinders} walk-in cyl. this ${unit}`}</div></div>
      </div>

      <div class="card" style="margin-top:16px">
        <h3 style="margin-top:0">Customer Growth Trend — ${d.period === "week" ? "Weekly" : "Monthly"}</h3>
        ${growthChartSvg(d.rows)}
      </div>

      <div class="card" style="margin-top:16px">
        <h3 style="margin-top:0">By ${d.period === "week" ? "Week" : "Month"}</h3>
        <div class="table-wrap"><table><thead><tr><th>${d.period === "week" ? "Week" : "Month"}</th><th>Active</th><th>New</th><th>Retained</th><th>Reactivated</th>
          <th>Lapsed</th><th>Net</th><th>Growth</th><th>Retention</th><th>Cylinders</th><th>Cyl/Cust</th>${d.category ? "" : "<th>Walk-in Cyl</th>"}<th>LPG Sales</th><th>Customer Base</th></tr></thead><tbody>
          ${latestRows.map(r => `<tr><td><b>${esc(r.label)}</b></td><td>${r.active}</td><td>${r.new ? `<span class="badge green">+${r.new}</span>` : 0}</td>
            <td>${r.retained}</td><td>${r.reactivated}</td><td>${r.lapsed ? `<span class="badge danger">${r.lapsed}</span>` : 0}</td>
            <td>${r.net_change > 0 ? "+" : ""}${r.net_change}</td>
            <td style="color:${(r.growth_pct || 0) > 0 ? "var(--green)" : (r.growth_pct || 0) < 0 ? "var(--danger)" : "inherit"}">${growthPct(r.growth_pct)}</td>
            <td>${r.retention_pct === null ? "—" : r.retention_pct.toFixed(1) + "%"}</td><td>${r.cylinders}</td><td>${r.cylinders_per_customer.toFixed(2)}</td>
            ${d.category ? "" : `<td>${r.walkin_cylinders}</td>`}<td>${money(r.amount)}</td><td>${r.customer_base}</td></tr>`).join("")}
          <tr style="font-weight:700;background:var(--bg)"><td>TOTAL</td><td></td><td>${d.totals.new}</td><td></td><td></td><td></td><td></td><td></td><td></td>
            <td>${d.totals.cylinders}</td><td></td>${d.category ? "" : `<td>${d.totals.walkin_cylinders}</td>`}<td>${money(d.totals.amount)}</td><td></td></tr>
        </tbody></table></div>
        <p style="color:var(--muted);font-size:12px;margin:8px 0 0"><b>Active</b> = bought at least one LPG cylinder in the period. <b>New</b> = first-ever LPG cylinder purchase.
          <b>Retained</b> = also bought the period before. <b>Reactivated</b> = came back after skipping the period before. <b>Lapsed</b> = bought the period before but not this one.</p>
      </div>

      <div class="grid grid-2" style="margin-top:16px">
        <div class="card" style="min-width:0"><h3 style="margin-top:0">New Customers This ${unit === "week" ? "Week" : "Month"} (${d.new_customers.length})</h3>
          <div class="table-wrap scroll-box"><table><thead><tr><th>Customer</th><th>Category</th><th>First Purchase</th><th>Cylinders</th><th>Amount</th></tr></thead><tbody>
            ${d.new_customers.length ? d.new_customers.map(x => `<tr><td><b>${esc(x.name)}</b><br><span style="color:var(--muted);font-size:12px">${esc(x.account_no)}${x.phone ? " · " + esc(x.phone) : ""}</span></td>
              <td><span class="badge blue">${esc(x.category || "")}</span></td><td>${x.first_purchase}</td><td>${x.cylinders}</td><td>${money(x.amount)}</td></tr>`).join("")
              : `<tr><td colspan="5" style="color:var(--muted)">No new customers yet this ${unit}.</td></tr>`}
          </tbody></table></div></div>
        <div class="card" style="min-width:0"><h3 style="margin-top:0">Lapsed — Follow Up (${d.lapsed_customers.length})</h3>
          <p style="color:var(--muted);font-size:12.5px;margin:0 0 8px">Bought LPG last ${unit} but not yet this ${unit}.</p>
          <div class="table-wrap scroll-box"><table><thead><tr><th>Customer</th><th>Category</th><th>Last Purchase</th><th>Cyl. Last ${unit === "week" ? "Week" : "Month"}</th></tr></thead><tbody>
            ${d.lapsed_customers.length ? d.lapsed_customers.map(x => `<tr><td><b>${esc(x.name)}</b><br><span style="color:var(--muted);font-size:12px">${esc(x.account_no)}${x.phone ? " · " + esc(x.phone) : ""}</span></td>
              <td><span class="badge blue">${esc(x.category || "")}</span></td><td>${x.last_purchase || ""}</td><td>${x.prev_cylinders}</td></tr>`).join("")
              : `<tr><td colspan="4" style="color:var(--muted)">Nobody lapsed.</td></tr>`}
          </tbody></table></div></div>
      </div>

      <div class="grid grid-2" style="margin-top:16px">
        <div class="card" style="min-width:0"><h3 style="margin-top:0">This ${unit === "week" ? "Week" : "Month"} by Customer Category</h3>
          <div class="table-wrap"><table><thead><tr><th>Category</th><th>Active</th><th>New</th><th>Cylinders</th></tr></thead><tbody>
            ${d.by_category.length ? d.by_category.map(b => `<tr><td>${esc(b.category)}</td><td>${b.active}</td><td>${b.new}</td><td>${b.cylinders}</td></tr>`).join("")
              : `<tr><td colspan="4" style="color:var(--muted)">No customer LPG purchases yet this ${unit}.</td></tr>`}
          </tbody></table></div></div>
        <div class="card" style="min-width:0"><h3 style="margin-top:0">Top Customers This ${unit === "week" ? "Week" : "Month"}</h3>
          <div class="table-wrap"><table><thead><tr><th>Customer</th><th>Category</th><th>Cylinders</th><th>Amount</th></tr></thead><tbody>
            ${d.top_customers.length ? d.top_customers.map(x => `<tr><td>${esc(x.name)}</td><td>${esc(x.category || "")}</td><td>${x.cylinders}</td><td>${money(x.amount)}</td></tr>`).join("")
              : `<tr><td colspan="4" style="color:var(--muted)">No customer LPG purchases yet this ${unit}.</td></tr>`}
          </tbody></table></div></div>
      </div>
      <p style="color:var(--muted);font-size:12px;margin-top:12px">Products counted as LPG cylinders:
        ${d.products_counted.length ? d.products_counted.map(p => esc(p.name)).join(", ") : "none — set a product's category to “LPG Cylinder” (or give it a deposit price) to count it."}</p>`;
  }

  // ---- Monthly LPG per customer (kg)
  const ymStr = dt => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}`;
  const nowDt = new Date();
  document.getElementById("kgTo").value = ymStr(nowDt);
  document.getElementById("kgFrom").value = ymStr(new Date(nowDt.getFullYear(), nowDt.getMonth() - 11, 1));
  const kgQs = () => {
    const q = new URLSearchParams();
    [["from", "kgFrom"], ["to", "kgTo"], ["location_id", "kgLoc"], ["category", "kgCat"]].forEach(([k, id]) => {
      const v = document.getElementById(id).value; if (v) q.set(k, v);
    });
    return q.toString();
  };
  const fmtKg = v => v ? Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }) : "–";
  let kgData = null;
  function drawKg() {
    const d = kgData; if (!d) return;
    const term = document.getElementById("kgSearch").value.trim().toLowerCase();
    const rows = d.rows.filter(r => !term || `${r.name} ${r.account_no} ${r.business_name}`.toLowerCase().includes(term));
    const t = d.totals, M = d.months;
    const shortLbl = ym => new Date(ym + "-01T00:00:00").toLocaleString("en", { month: "short", year: "2-digit" });
    document.getElementById("kgOut").innerHTML = `
      <div class="table-wrap"><table><thead><tr><th>#</th><th>Customer</th><th>Category</th>
        ${M.map(ym => `<th style="text-align:right">${shortLbl(ym)}</th>`).join("")}
        <th style="text-align:right">Total kg</th><th style="text-align:right">Cyl.</th><th style="text-align:right">Avg kg/mo</th></tr></thead><tbody>
        ${rows.length ? rows.map((r, i) => `<tr><td>${i + 1}</td><td><b>${esc(r.name)}</b><br><span style="color:var(--muted);font-size:12px">${esc(r.account_no)}${r.business_name ? " · " + esc(r.business_name) : ""}</span></td>
          <td><span class="badge blue">${esc(r.category)}</span></td>
          ${M.map(ym => `<td style="text-align:right;${r.months[ym] ? "" : "color:var(--muted)"}">${fmtKg(r.months[ym])}</td>`).join("")}
          <td style="text-align:right"><b>${fmtKg(r.total_kg)}</b></td><td style="text-align:right">${r.cylinders}</td><td style="text-align:right">${fmtKg(r.avg_kg_per_month)}</td></tr>`).join("")
          : `<tr><td colspan="${M.length + 6}" style="color:var(--muted)">No completed LPG orders by customers in this period.</td></tr>`}
        <tr style="font-weight:700;background:var(--bg)"><td></td><td>TOTAL — CUSTOMERS</td><td></td>
          ${M.map(ym => `<td style="text-align:right">${fmtKg(t.customers_months[ym])}</td>`).join("")}
          <td style="text-align:right">${fmtKg(t.customers_kg)}</td><td style="text-align:right">${t.customers_cylinders}</td><td></td></tr>
        ${d.walkin ? `<tr><td></td><td>Walk-in (no account)</td><td></td>${M.map(ym => `<td style="text-align:right">${fmtKg(d.walkin.months[ym])}</td>`).join("")}
          <td style="text-align:right">${fmtKg(d.walkin.total_kg)}</td><td style="text-align:right">${d.walkin.cylinders}</td><td></td></tr>
          <tr style="font-weight:700;background:var(--bg)"><td></td><td>GRAND TOTAL</td><td></td>${M.map(ym => `<td style="text-align:right">${fmtKg(t.months[ym])}</td>`).join("")}
          <td style="text-align:right">${fmtKg(t.kg)}</td><td></td><td></td></tr>` : ""}
      </tbody></table></div>
      ${term ? `<p style="color:var(--muted);font-size:12px;margin:6px 0 0">Showing ${rows.length} of ${d.rows.length} customers; totals are for all customers.</p>` : ""}
      ${d.unknown_size_products.length ? `<p style="color:var(--warn);font-size:12.5px;margin:8px 0 0">⚠ Not included — no kg size in the product name:
        ${d.unknown_size_products.map(esc).join(", ")}. Add the size to the product name (e.g. "Solane LPG 11kg").</p>` : ""}`;
  }
  async function loadKg() {
    document.getElementById("kgXlsx").href = "/api/reports/customer-kg/export?format=xlsx&" + kgQs();
    document.getElementById("kgPdf").href = "/api/reports/customer-kg/export?format=pdf&" + kgQs();
    try { kgData = await api("/reports/customer-kg?" + kgQs()); } catch (e) { return; }
    drawKg();
  }
  ["kgFrom", "kgTo", "kgLoc", "kgCat"].forEach(id => document.getElementById(id).addEventListener("change", loadKg));
  document.getElementById("kgRun").addEventListener("click", loadKg);
  document.getElementById("kgSearch").addEventListener("input", drawKg);
  loadKg();

  periodSel.addEventListener("change", () => { fillCounts(); load(); });
  ["cgCount", "cgEnd", "cgLoc", "cgCat"].forEach(id => document.getElementById(id).addEventListener("change", load));
  document.getElementById("cgRun").addEventListener("click", load);
  load();
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
    </div>
    <div class="card" style="margin-top:16px">
      <h3 style="margin-top:0">Swapped Cylinder Report</h3>
      <p style="color:var(--muted);font-size:13px;margin:0 0 10px">Other-brand cylinders taken in on swap sales, totalled per brand and size, plus swapped empties still on hand.</p>
      <div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;align-items:center">
        <input type="date" id="swFrom"><input type="date" id="swTo">
        <select id="swLoc"><option value="">All locations</option>
          ${state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("")}</select>
        <button class="btn" id="runSwapRep">Run</button>
        <button class="btn small green" id="swPrint">Print</button>
        <a class="btn small" id="swXlsx" href="#">Export Excel</a>
        <a class="btn small" id="swPdf" href="#">Export PDF</a>
      </div>
      <div id="swapRepOut"></div>
    </div>`;

  const swQs = () => {
    const qs = new URLSearchParams();
    const f = document.getElementById("swFrom").value, t = document.getElementById("swTo").value, l = document.getElementById("swLoc").value;
    if (f) qs.set("from", f); if (t) qs.set("to", t); if (l) qs.set("location_id", l);
    return qs.toString();
  };
  function updateSwapLinks() {
    document.getElementById("swXlsx").href = "/api/reports/swapped/export?format=xlsx&" + swQs();
    document.getElementById("swPdf").href = "/api/reports/swapped/export?format=pdf&" + swQs();
  }
  ["swFrom", "swTo", "swLoc"].forEach(id => document.getElementById(id).addEventListener("change", updateSwapLinks));
  updateSwapLinks();
  let swapData = null;
  document.getElementById("runSwapRep").addEventListener("click", async () => {
    try { swapData = await api("/reports/swapped?" + swQs()); } catch (e) { return; }
    const d = swapData, t = d.totals;
    document.getElementById("swapRepOut").innerHTML = `
      <div class="grid grid-2">
        <div><h4 style="margin:6px 0">Summary by Brand / Size</h4>
          <div class="table-wrap"><table><thead><tr><th>Brand Swapped</th><th>Size</th><th>Qty</th><th>Swap Fees</th><th>Amount</th></tr></thead><tbody>
            ${d.summary.length ? d.summary.map(r => `<tr><td><b>${esc(r.swap_brand)}</b></td><td>${esc(r.product_name)}</td><td>${r.qty}</td><td>${money(r.swap_fees)}</td><td>${money(r.amount)}</td></tr>`).join("")
              : `<tr><td colspan="5" style="color:var(--muted)">No swap sales in this period.</td></tr>`}
            <tr style="font-weight:700;background:var(--bg)"><td>TOTAL</td><td></td><td>${t.qty}</td><td>${money(t.swap_fees)}</td><td>${money(t.amount)}</td></tr>
          </tbody></table></div></div>
        <div><h4 style="margin:6px 0">Swapped Empties On Hand</h4>
          <div class="table-wrap"><table><thead><tr><th>Location</th><th>Brand</th><th>Size</th><th>Qty</th></tr></thead><tbody>
            ${d.on_hand.length ? d.on_hand.map(r => `<tr><td>${esc(r.location_name)}</td><td>${esc(r.brand)}</td><td>${esc(r.product_name)}</td><td>${r.quantity}</td></tr>`).join("")
              : `<tr><td colspan="4" style="color:var(--muted)">None on hand.</td></tr>`}
            <tr style="font-weight:700;background:var(--bg)"><td>TOTAL</td><td></td><td></td><td>${t.on_hand}</td></tr>
          </tbody></table></div></div>
      </div>
      <h4 style="margin:14px 0 6px">Swap Transactions</h4>
      <div class="table-wrap"><table><thead><tr><th>Date</th><th>Sale #</th><th>Location</th><th>Cashier</th><th>Customer</th><th>Brand</th><th>Size</th><th>Qty</th><th>Swap Fees</th><th>Amount</th></tr></thead><tbody>
        ${d.rows.length ? d.rows.map(r => `<tr><td>${r.ts.slice(0, 16)}</td><td>${esc(r.sale_no)}</td><td>${esc(r.location_name)}</td><td>${esc(r.cashier)}</td>
          <td>${esc(r.customer_name || "Walk-in")}</td><td>${esc(r.swap_brand)}</td><td>${esc(r.product_name)}</td><td>${r.qty}</td>
          <td>${money(r.swap_fee_total)}</td><td>${money(r.amount)}</td></tr>`).join("")
          : `<tr><td colspan="10" style="color:var(--muted)">No swap sales in this period.</td></tr>`}
      </tbody></table></div>`;
  });
  document.getElementById("swPrint").addEventListener("click", async () => {
    if (!swapData) { try { swapData = await api("/reports/swapped?" + swQs()); } catch (e) { return; } }
    printSwappedReport(swapData);
  });

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
    document.getElementById("salesRepOut").innerHTML = `<table><thead><tr><th>Sale#</th><th>Date</th><th>Location</th><th>Customer</th><th>Category</th>
      <th>Item</th><th>Qty</th><th>Amount</th><th>Discount</th><th>Net Amount</th><th>Status</th><th>Remarks</th></tr></thead><tbody>
      ${rows.map(r => `<tr><td>${r.sale_no}</td><td>${r.ts}</td><td>${esc(r.location_name)}</td><td>${esc(r.customer || "Walk-in")}</td><td>${esc(r.customer_category)}</td>
        <td>${esc(r.item || "")}</td><td>${r.qty ?? ""}</td><td>${money(r.amount)}</td><td>${r.discount > 0 ? money(r.discount) : ""}</td>
        <td>${money(r.net_amount)}</td><td>${r.status === "VOIDED" ? '<span class="badge danger">VOIDED</span>' : r.status}</td>
        <td>${esc(r.remarks)}</td></tr>`).join("")}
    </tbody></table>`;
  });
  const inv = await api("/reports/inventory");
  document.getElementById("invRepOut").innerHTML = `<table><thead><tr><th>Location</th><th>Product</th><th>Qty</th></tr></thead><tbody>
    ${inv.map(r => `<tr><td>${esc(r.location_name)}</td><td>${esc(r.product_name)}</td><td>${r.quantity}</td></tr>`).join("")}
  </tbody></table>`;
}

// ================================================================== ADMIN: USERS
async function renderUsers(c) {
  const canManage = isAdminLike() || state.user.role === "MANAGER";
  c.innerHTML = `<div style="margin-bottom:12px">${canManage ? `<button class="btn green" id="newUserBtn">+ New User</button>` : ""}</div><div id="usersTable">Loading...</div>`;
  let allRoles = [];
  function roleOptions(selected) {
    // Only Admin can give out ADMIN; only Admin / Operation Head can give out OPERATION_HEAD.
    const me = state.user.role;
    return allRoles
      .filter(r => r.name === selected || (r.name !== "ADMIN" || me === "ADMIN") && (r.name !== "OPERATION_HEAD" || isAdminLike(me)))
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
        <td style="display:flex;gap:6px"><button class="btn small" onclick="editProduct(${p.id})">Edit</button>
          <button class="btn small danger" onclick="deleteProduct(${p.id})">Delete</button></td></tr>`).join("")}
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
  window.deleteProduct = async (id) => {
    const p = state.products.find(x => x.id === id);
    if (!confirm(`Delete "${p.name}"? Its stock must be 0. If it was already used in sales or other records, it will be hidden instead so past records stay correct.`)) return;
    try {
      const r = await api(`/products/${id}`, { method: "DELETE" });
      toast(r.archived ? "Product removed from the list (kept in past records)" : "Product deleted", "success");
      load();
    } catch (e) {}
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

// ================================================================== ADMIN: SYSTEM RESET (GO LIVE)
async function renderSystemReset(c) {
  c.innerHTML = `<div id="resetBox">Loading...</div>`;
  const n = await api("/admin/reset-summary");
  const opt = (id, label, count, hint) => `<label style="display:flex;gap:8px;align-items:flex-start;margin:8px 0">
    <input type="checkbox" id="${id}" style="margin-top:3px">
    <span><b>${label}</b> <span class="badge gray">${count}</span><br>
    <span style="font-size:12.5px;color:var(--muted)">${hint}</span></span></label>`;
  document.getElementById("resetBox").innerHTML = `
    <div class="card" style="margin-bottom:14px;border-color:var(--danger)">
      <h3 style="margin-top:0;color:var(--danger)">Clear test data and start real operations</h3>
      <p style="font-size:13.5px">Use this <b>once</b>, when testing is finished. It permanently removes test data so the client starts clean.
      A full backup copy of the database is saved on the server automatically before anything is deleted.</p>
      <button class="btn" id="dlBackupBtn">&#11015; Download backup first (optional)</button>
    </div>

    <div class="card" style="margin-bottom:14px">
      <h4 style="margin-top:0">Always cleared</h4>
      <ul style="font-size:13.5px;margin:0;padding-left:18px;line-height:1.7">
        <li>Sales <span class="badge gray">${n.sales}</span>, payments <span class="badge gray">${n.payments}</span>, receivables / previous arrears <span class="badge gray">${n.receivables}</span></li>
        <li>Transfers / dispatches <span class="badge gray">${n.transfers}</span>, inventory ledger <span class="badge gray">${n.inventory_movements}</span>, cylinder deposits</li>
        <li>Stock on hand set to <b>0</b> (now ${n.stock_on_hand} full, ${n.empties_on_hand} empty). Encode the real opening stock afterwards (Inventory → + Adjust Stock).</li>
        <li>Booked orders <span class="badge gray">${n.booked_orders}</span>, commercial deliveries <span class="badge gray">${n.commercial_deliveries}</span>, hauling <span class="badge gray">${n.hauling}</span></li>
        <li>Incident reports <span class="badge gray">${n.incident_reports}</span>, staff schedule <span class="badge gray">${n.staff_schedule}</span>, audit log <span class="badge gray">${n.audit_logs}</span></li>
      </ul>
      <h4>Always kept</h4>
      <p style="font-size:13.5px;margin:0">Locations (${n.locations}), roles and permissions, customer categories, and <b>your own admin account</b>. Products are kept unless ticked below.</p>
    </div>

    <div class="card" style="margin-bottom:14px">
      <h4 style="margin-top:0">Also clear (optional)</h4>
      ${opt("rsCustomers", "Customers", n.customers, "Tick if the customer list is only test/demo data. Leave unticked if real customers were already encoded.")}
      ${opt("rsUsers", "All other user accounts", n.other_users, "Deletes every login except yours (including demo accounts like mgarcia, otaker, jsoliven). Create the real staff accounts in Users afterwards.")}
      ${opt("rsStaff", "Employees and vehicles", `${n.employees} / ${n.vehicles}`, "Tick only if these were test entries.")}
      ${opt("rsDiscounts", "Discounts & promos", n.discounts, "Tick if the discounts were only for testing.")}
      ${opt("rsProducts", "Products", n.products, "Deletes every product (and its stock rows and item-specific discounts). Add the real products in Products afterwards, by hand or with Import CSV.")}
    </div>

    <div class="card">
      <h4 style="margin-top:0">Confirm</h4>
      <div class="field"><label>Type <b>RESET</b> to confirm</label><input id="rsConfirm" autocomplete="off"></div>
      <div class="field"><label>Your admin password</label><input type="password" id="rsPassword" autocomplete="current-password"></div>
      <button class="btn danger" id="rsGoBtn">Reset system now</button>
    </div>`;

  document.getElementById("dlBackupBtn").addEventListener("click", () => { window.location = "/api/admin/backup"; });
  document.getElementById("rsGoBtn").addEventListener("click", async () => {
    const body = {
      confirm_text: document.getElementById("rsConfirm").value,
      password: document.getElementById("rsPassword").value,
      options: {
        customers: document.getElementById("rsCustomers").checked,
        users: document.getElementById("rsUsers").checked,
        employees_vehicles: document.getElementById("rsStaff").checked,
        discounts: document.getElementById("rsDiscounts").checked,
        products: document.getElementById("rsProducts").checked,
      },
    };
    if (body.confirm_text.trim().toUpperCase() !== "RESET") { toast("Type RESET to confirm", "error"); return; }
    if (!confirm("This permanently deletes all test transactions and sets stock to zero. Continue?")) return;
    const btn = document.getElementById("rsGoBtn");
    btn.disabled = true; btn.textContent = "Resetting...";
    try {
      const r = await api("/admin/reset", { method: "POST", body });
      toast("System reset complete. Ready for live use.", "success");
      openModal("Reset complete", `<p>All test data has been cleared. Backup saved on the server as:</p>
        <p><code>${esc(r.backup_file)}</code></p>
        <p><b>Next steps:</b></p>
        <ol style="padding-left:18px;line-height:1.7">
          ${body.options.products ? "<li>Products: add the real products (Products → + New Product or Import CSV).</li>" : ""}
          <li>Inventory: encode the actual opening stock per location (Inventory → + Adjust Stock, or Transfers → + Import Stock from Supplier).</li>
          <li>Users: create the real staff accounts${body.options.users ? "" : " and disable demo accounts"}.</li>
          <li>Customers: add or import real customers and their previous arrears.</li>
          <li>Change your admin password if it is still the default.</li>
        </ol>
        <div class="modal-actions"><button class="btn green" onclick="closeModal()">OK</button></div>`);
      state.products = await api("/products");
      renderSystemReset(c);
    } catch (e) {
      btn.disabled = false; btn.textContent = "Reset system now";
    }
  });
}

// ================================================================== ADMIN: DISCOUNTS & PROMOS
async function renderDiscounts(c) {
  c.innerHTML = `<div style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">
    <button class="btn green" id="newDiscBtn">+ New Discount / Promo</button>
    <span style="font-size:12.5px;color:var(--muted)">Discounts apply per item. The cashier picks one (optional) for each cart line in POS.</span>
  </div><div id="discTable">Loading...</div>`;
  let rows = [];
  const today = new Date().toISOString().slice(0, 10);
  function statusOf(d) {
    if (!d.active) return `<span class="badge danger">INACTIVE</span>`;
    if (d.start_date && today < d.start_date) return `<span class="badge warn">SCHEDULED</span>`;
    if (d.end_date && today > d.end_date) return `<span class="badge danger">EXPIRED</span>`;
    return `<span class="badge green">ACTIVE</span>`;
  }
  async function load() {
    rows = await api("/discounts");
    document.getElementById("discTable").innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr>
      <th>Name</th><th>Discount</th><th>Applies To</th><th>Min Qty</th><th>Valid</th><th>Status</th><th></th>
    </tr></thead><tbody>
      ${rows.map(d => `<tr>
        <td>${esc(d.name)}${d.notes ? `<br><span style="font-size:12px;color:var(--muted)">${esc(d.notes)}</span>` : ""}</td>
        <td>${d.type === "PERCENT" ? `${d.value}% off` : `${money(d.value)} off per unit`}</td>
        <td>${d.product_id ? esc(d.product_name || "(deleted product)") : "All items"}</td>
        <td>${d.min_qty}</td>
        <td>${d.start_date || d.end_date ? `${d.start_date || "…"} to ${d.end_date || "…"}` : "Always"}</td>
        <td>${statusOf(d)}</td>
        <td style="display:flex;gap:6px">
          <button class="btn small" onclick="editDiscount(${d.id})">Edit</button>
          <button class="btn small danger" onclick="deleteDiscount(${d.id})">Delete</button>
        </td></tr>`).join("")}
    </tbody></table></div>` : `<p style="color:var(--muted)">No discounts or promos yet. Click "+ New Discount / Promo" to add one.</p>`;
  }
  function formFields(d = {}) {
    const prodOpts = `<option value="">All items</option>` + state.products.map(p =>
      `<option value="${p.id}" ${d.product_id === p.id ? "selected" : ""}>${esc(p.name)}</option>`).join("");
    return `
      <div class="field"><label>Name (shown on POS and receipt)</label><input id="dName" value="${esc(d.name || "")}" placeholder="e.g. Senior Citizen, Holiday Promo"></div>
      <div class="field"><label>Type</label><select id="dType">
        <option value="PERCENT" ${d.type !== "FIXED" ? "selected" : ""}>Percent off (%)</option>
        <option value="FIXED" ${d.type === "FIXED" ? "selected" : ""}>Fixed amount off per unit (₱)</option>
      </select></div>
      <div class="field"><label>Value</label><input type="number" step="0.01" min="0" id="dValue" value="${d.value ?? ""}"></div>
      <div class="field"><label>Applies to item</label><select id="dProduct">${prodOpts}</select></div>
      <div class="field"><label>Minimum qty per line</label><input type="number" min="1" id="dMinQty" value="${d.min_qty ?? 1}"></div>
      <div class="field"><label>Start date (optional)</label><input type="date" id="dStart" value="${d.start_date || ""}"></div>
      <div class="field"><label>End date (optional)</label><input type="date" id="dEnd" value="${d.end_date || ""}"></div>
      <div class="field"><label>Notes</label><input id="dNotes" value="${esc(d.notes || "")}"></div>
      <div class="field"><label><input type="checkbox" id="dActive" ${d.active === 0 ? "" : "checked"}> Active</label></div>
      <p style="font-size:12px;color:var(--muted)">Discounts apply to the gas price only, never to the cylinder deposit fee.</p>`;
  }
  function readForm() {
    return {
      name: document.getElementById("dName").value, type: document.getElementById("dType").value,
      value: document.getElementById("dValue").value, product_id: document.getElementById("dProduct").value || null,
      min_qty: document.getElementById("dMinQty").value, start_date: document.getElementById("dStart").value,
      end_date: document.getElementById("dEnd").value, notes: document.getElementById("dNotes").value,
      active: document.getElementById("dActive").checked,
    };
  }
  function openForm(d) {
    openModal(d ? "Edit Discount / Promo" : "New Discount / Promo", formFields(d || {}) + `<div class="modal-actions">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn green" id="dSaveBtn">Save</button></div>`, () => {
      document.getElementById("dSaveBtn").addEventListener("click", async () => {
        try {
          if (d) await api(`/discounts/${d.id}`, { method: "PUT", body: readForm() });
          else await api("/discounts", { method: "POST", body: readForm() });
          closeModal(); toast("Discount saved", "success"); load();
        } catch (e) {}
      });
    });
  }
  document.getElementById("newDiscBtn").addEventListener("click", () => openForm(null));
  window.editDiscount = (id) => openForm(rows.find(r => r.id === id));
  window.deleteDiscount = async (id) => {
    if (!confirm("Delete this discount? Past sales keep their recorded discount.")) return;
    try { await api(`/discounts/${id}`, { method: "DELETE" }); toast("Discount deleted", "success"); load(); } catch (e) {}
  };
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
      ${isAdminLike() ? `<div class="field"><label>Receiving Location</label><select id="retLoc">${locOpts}</select></div>` : ""}
      <div class="field"><label>Quantity Returned</label><input type="number" id="retQty" value="1" min="1" max="${maxQty}"></div>
      <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn green" id="retSaveBtn">Confirm Return</button></div>
    `, () => {
      document.getElementById("retSaveBtn").addEventListener("click", async () => {
        const body = { customer_id: customerId, product_id: productId, qty: document.getElementById("retQty").value };
        if (isAdminLike()) body.location_id = document.getElementById("retLoc").value;
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
function printHaulingCalendar(rows, month) {
  const [y, m] = month.split("-").map(Number);
  const first = new Date(y, m - 1, 1);
  const daysInMonth = new Date(y, m, 0).getDate();
  const monthName = first.toLocaleString("en-US", { month: "long", year: "numeric" });
  const byDay = {};
  rows.filter(h => (h.scheduled_date || "").slice(0, 7) === month)
    .forEach(h => (byDay[parseInt(h.scheduled_date.slice(8, 10))] ||= []).push(h));
  const total = Object.values(byDay).reduce((a, l) => a + l.length, 0);
  const cells = [];
  for (let i = 0; i < first.getDay(); i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);
  while (cells.length % 7) cells.push(null);
  const weeks = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  const rowH = weeks.length > 5 ? 108 : 128;
  const trip = h => `<div style="border-left:3px solid ${h.status === "CANCELLED" ? "#c0392b" : h.status === "COMPLETED" ? "#1e9e5a" : "#0b3d66"};
      background:#f4f6f8;padding:2px 4px;margin-bottom:3px;font-size:9px;line-height:1.25;${h.status === "CANCELLED" ? "text-decoration:line-through;" : ""}">
    <b>${esc(h.hauling_no)}</b> · ${esc(h.status)}<br>
    ${esc(h.plant_name)}${h.destination_name ? " → " + esc(h.destination_name) : ""}<br>
    ${h.plate_number ? esc(h.plate_number) : "No vehicle"}${h.driver ? " · " + esc(h.driver) : ""}<br>
    ${h.items.map(i => `${esc(i.product_name)} x${i.qty}`).join(", ")}</div>`;
  printDocument(`<style>@page{size:letter landscape;margin:8mm}
    .haul-cal, .haul-cal *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
    .haul-cal td div{overflow-wrap:anywhere;word-break:break-word}
    .haul-cal th, .haul-cal td{white-space:normal;text-transform:none}</style>
  <div class="haul-cal" style="font-family:Arial,sans-serif;box-sizing:border-box">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
      <img src="/images/logo.png" style="height:36px">
      <div style="flex:1"><div style="font-weight:700;font-size:14px">GREAT UNLI FOOD CORP. — Hauling Schedule</div>
        <div style="font-size:11px;color:#555">Solane LPG Distributor · ${total} trip${total === 1 ? "" : "s"}</div></div>
      <div style="font-size:20px;font-weight:700;color:#0b3d66">${monthName}</div>
    </div>
    <table style="width:100%;border-collapse:collapse;table-layout:fixed">
      <tr>${["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map(d => `<th style="background:#0b3d66;color:#fff;font-size:11px;padding:4px;border:1px solid #0b3d66">${d}</th>`).join("")}</tr>
      ${weeks.map(w => `<tr>${w.map(d => `<td style="border:1px solid #bbb;vertical-align:top;height:${rowH}px;padding:3px;overflow:hidden;${d ? "" : "background:#fafafa"}">
        ${d ? `<div style="font-weight:700;font-size:11px;text-align:right">${d}</div>${(byDay[d] || []).map(trip).join("")}` : ""}</td>`).join("")}</tr>`).join("")}
    </table>
    <div style="text-align:right;color:#888;margin-top:4px;font-size:9px">Printed ${new Date().toLocaleString()} by ${esc(state.user.name)}</div>
  </div>`);
}

async function renderHauling(c) {
  const thisMonth = new Date().toISOString().slice(0, 7);
  c.innerHTML = `<div style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <button class="btn green" id="newHaulBtn">+ Schedule Hauling</button>
      <span style="flex:1"></span>
      <input type="month" id="haulMonth" value="${thisMonth}">
      <label style="font-size:13px;display:flex;gap:4px;align-items:center"><input type="checkbox" id="haulInclCancelled"> Include cancelled</label>
      <button class="btn" id="haulPrintCal">Print Calendar</button>
    </div><div id="haulTable">Loading...</div>`;
  const statusColor = s => ({ SCHEDULED: "blue", LOADING: "warn", "IN TRANSIT": "warn", COMPLETED: "green", CANCELLED: "danger" }[s] || "gray");
  let haulRows = [];
  document.getElementById("haulPrintCal").addEventListener("click", () => {
    const month = document.getElementById("haulMonth").value || thisMonth;
    const incl = document.getElementById("haulInclCancelled").checked;
    printHaulingCalendar(haulRows.filter(h => incl || h.status !== "CANCELLED"), month);
  });
  async function load() {
    const rows = haulRows = await api("/hauling");
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
  const canEdit = isAdminLike() || state.user.role === "MANAGER";
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
  const canCreate = ["ADMIN", "OPERATION_HEAD", "MANAGER", "ORDER_TAKER"].includes(role);
  const canAssign = canCreate;
  const isAttendant = !["ADMIN", "OPERATION_HEAD", "MANAGER", "ORDER_TAKER"].includes(role);
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
        <td><span class="badge ${o.source === 'CUSTOMER' ? 'warn' : 'blue'}">${o.source === 'CUSTOMER' ? (o.customer_id ? 'Portal' : 'Online') : 'Staff'}</span></td>
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
  const canResolve = isAdminLike() || state.user.role === "MANAGER";
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
    const isAdminOrMgr = isAdminLike() || state.user.role === "MANAGER";
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
    printDocument(html);
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
      commercial_deliveries: "50kg Commercial Billing", eod_report: "End of Day Report",
      receivables_summary: "Dashboard: Receivables Totals", customer_growth: "Customer Growth",
      stock_import: "Import Stock from Supplier", sales_targets: "Sales Targets",
    };
    openModal(`${locked ? "View" : "Edit"} Modules — ${name}`, `
      ${locked ? `<p style="color:var(--muted);font-size:13px">${name === "OPERATION_HEAD" ? "Operation Head always has full access to every module (everything except the System Reset)" : "Admin always has full access to every module"} — this can't be changed.</p>` : ""}
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
  const canCreate = hasModule("commercial_deliveries");
  const canBill = isAdminLike(role) || role === "MANAGER" || role === "ACCOUNTING";
  const canVoidBilling = canCommercialBilling();
  let customersById = {};
  // Who may type their own price per kg on a DR billing (others use the product's default).
  const canRate = ["ADMIN", "OPERATION_HEAD", "MANAGER", "ACCOUNTING"].includes(role);
  const typeBadge = t => ({ WIWO: "blue", DR: "green" }[t] || "warn");
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
        <td><span class="badge ${typeBadge(d.billing_type)}">${d.billing_type}</span></td>
        <td>${esc(d.product_name)}</td>
        <td>${d.source_sale_no ? `<span class="badge gray" title="Generated from this POS sale">${esc(d.source_sale_no)}</span><br>` : ""}${d.billing_type === 'WIWO' ? `${d.cylinders.length} cylinder(s), ${d.total_net_weight_in}kg in`
              : d.billing_type === 'DR' ? `${d.dr_ref ? 'DR ' + esc(d.dr_ref) + ': ' : ''}${d.delivered_qty} delivered, ${d.cylinders.length} pulled out (${d.total_residual_kg || 0}kg residual)`
              : `${esc(d.meter_label || 'meter')}: ${d.previous_reading} \u2192 ${d.current_reading}`}</td>
        <td>${d.total_consumption_weight !== null ? d.total_consumption_weight + (d.billing_type !== 'READING' ? ' kg' : '') : '—'}</td>
        <td>${d.billed_amount ? money(d.billed_amount) : '—'}</td>
        <td><span class="badge ${statusColor(d.status)}">${d.status}</span></td>
        <td style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn small" onclick="viewCommercialDelivery(${d.id})">View</button>
          <button class="btn small" onclick="printCommercialDelivery(${d.id})">Print</button>
          <a class="btn small" href="/api/commercial-deliveries/${d.id}/export/pdf">PDF</a>
          ${d.billing_type === 'DR' && d.sale_id ? `<button class="btn small" onclick="printSaleDR(${d.source_sale_id || d.sale_id})">DR</button>` : ""}
          ${d.status === 'BILLED' && canVoidBilling ? `<button class="btn small danger" onclick="voidCommercialBilling(${d.id})" title="Undo a wrong billing (only if unpaid)">Void Billing</button>` : ""}
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
      customersById = Object.fromEntries(customers.map(cu => [String(cu.id), cu]));
      const custOpts = customers.map(cu => `<option value="${cu.id}">${esc(cu.name)} (${cu.account_no})</option>`).join("");
      const locOpts = state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
      const prodOpts = state.products.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
      // Admins/Managers pick any branch; users without an assigned branch
      // (e.g. Accounting) must pick one too, or the server has no location.
      const isAdminOrMgr = isAdminLike(role) || role === "MANAGER" || !state.user.location_id;
      openModal("New Commercial Billing", `
        <div class="field"><label>Billing Type</label><select id="cdType">
          <option value="DR">DR — Manual billing on delivery (delivered less residual of pull-outs)</option>
          <option value="WIWO">WIWO — Weight In / Weight Out (cylinders)</option>
          <option value="READING">READING — Meter Reading</option>
        </select></div>
        <div class="field"><label>Customer</label><select id="cdCust">${custOpts}</select></div>
        ${isAdminOrMgr ? `<div class="field"><label>Location</label><select id="cdLoc">${locOpts}</select></div>` : ""}
        <div class="field"><label>Product</label><select id="cdProd">${prodOpts}</select></div>

        <div id="drFields">
          <div class="field"><label>DR No. (from the printed Delivery Receipt)</label><input id="drRef" placeholder="e.g. 012345"></div>
          <div style="display:flex;gap:10px;flex-wrap:wrap">
            <div class="field" style="flex:1;min-width:140px"><label>50kg cylinders delivered</label><input type="number" id="drQty" min="1" value="1"></div>
            <div class="field" style="flex:1;min-width:140px"><label>kg per cylinder</label><input type="number" step="0.01" id="drKg" value="50"></div>
          </div>
          <div class="field"><label>Cylinders pulled out (serial numbers don't need to match)</label>
            <p style="font-size:12px;color:var(--muted);margin:0 0 6px">Enter gross and tare weight, or type the residual kg directly. The residual is deducted from the bill.</p>
            <div style="display:flex;gap:8px;font-size:11.5px;color:var(--muted);font-weight:600">
              <span style="flex:1">Serial no.</span><span style="flex:1">Gross (kg)</span><span style="flex:1">Tare (kg)</span><span style="flex:1">Residual (kg)</span><span style="width:30px"></span></div>
            <div id="drPullRows"></div>
            <div style="display:flex;gap:6px;flex-wrap:wrap">
              <button class="btn small" id="drAddPullBtn" type="button">+ Add Pull-out</button>
              <button class="btn small" id="drMatchBtn" type="button">Same count as delivered</button>
            </div>
          </div>
          ${canRate ? `<div class="field"><label>Price per kg <span id="drRateNote" style="font-weight:400;color:var(--muted)"></span></label><input type="number" step="0.01" id="drRate"></div>` : ""}
          <div class="field" id="drSummary" style="background:var(--bg);padding:10px;border-radius:8px;font-size:13.5px"></div>
          <p style="font-size:12px;color:var(--muted);margin:0">Saving bills the customer right away: an invoice and DR are created (Receivables / SOA). It is not recorded as a sale.</p>
        </div>
        <div id="wiwoFields" class="hidden">
          <div class="field"><label>Cylinders</label>
            <div id="cdCylRows"></div>
            <button class="btn small" id="cdAddCylBtn" type="button">+ Add Cylinder</button>
          </div>
        </div>
        <div id="readingFields" class="hidden">
          <div class="field"><label>Meter / Tank Label</label><input id="cdMeterLabel" placeholder="e.g. Bulk Tank A" list="cdMeterList">
            <datalist id="cdMeterList"></datalist></div>
          <div class="field" id="cdBeginBox" style="display:none;background:#fff4e5;padding:10px;border-radius:8px">
            <label>Beginning Reading</label>
            <p style="font-size:12px;margin:0 0 6px">No reading on record for this meter yet. Enter its beginning reading first — it becomes the previous reading of the first bill.</p>
            <div style="display:flex;gap:6px"><input type="number" step="0.01" min="0" id="cdBeginReading" style="flex:1">
              <button class="btn small green" type="button" id="cdBeginSave">Save Beginning Reading</button></div>
          </div>
          <div class="field"><label>Previous Reading <span id="cdPrevSrc" style="font-weight:400;color:var(--muted)"></span></label>
            <input type="number" step="0.01" id="cdPrevReading" readonly placeholder="Set the beginning reading first" style="background:var(--bg)"></div>
          <div class="field"><label>Current Reading</label><input type="number" step="0.01" id="cdCurrReading"></div>
          <div class="field"><b>Consumption:</b> <span id="cdConsumptionPreview">—</span></div>
        </div>

        <div class="field"><label>Notes</label><input id="cdNotes"></div>
        <div class="modal-actions"><button class="btn" onclick="closeModal()">Cancel</button>
          <button class="btn green" id="cdSaveBtn">Save</button></div>
      `, () => {
        // ---- DR (manual) billing
        const pullRows = document.getElementById("drPullRows");
        const prodById = id => state.products.find(p => String(p.id) === String(id));
        function kgFromName(p) { const m = /(\d+(?:\.\d+)?)\s*-?\s*kg\b/i.exec((p && (p.name + " " + (p.sku || ""))) || ""); return m ? parseFloat(m[1]) : 50; }
        // The rate Accounting set on the Commercial account wins; else the product default.
        const custRate = () => { const cu = customersById[document.getElementById("cdCust").value]; return cu && cu.commercial_rate > 0 ? cu.commercial_rate : 0; };
        function syncRate() {
          const p = prodById(document.getElementById("cdProd").value);
          const rateEl = document.getElementById("drRate"), cr = custRate();
          if (rateEl) {
            rateEl.value = cr || (p ? (p.price_per_kg || "") : "");
            rateEl.readOnly = !!cr;
            document.getElementById("drRateNote").textContent = cr ? "(account rate)" : "";
          }
        }
        function syncProductDefaults() {
          const p = prodById(document.getElementById("cdProd").value);
          document.getElementById("drKg").value = kgFromName(p);
          syncRate();
          drRecalc();
        }
        function rowResidual(row) {
          const gw = parseFloat(row.querySelector(".poGW").value), tw = parseFloat(row.querySelector(".poTW").value);
          if (!isNaN(gw) && !isNaN(tw)) return gw - tw;
          return parseFloat(row.querySelector(".poRes").value) || 0;
        }
        function drRecalc() {
          const qty = parseInt(document.getElementById("drQty").value) || 0;
          const kg = parseFloat(document.getElementById("drKg").value) || 0;
          const rows = [...pullRows.querySelectorAll(".pay-row")];
          rows.forEach(r => {
            const gw = parseFloat(r.querySelector(".poGW").value), tw = parseFloat(r.querySelector(".poTW").value);
            if (!isNaN(gw) && !isNaN(tw)) r.querySelector(".poRes").value = (gw - tw).toFixed(2);
          });
          const delivered = qty * kg;
          const residual = rows.reduce((t, r) => t + rowResidual(r), 0);
          const billable = delivered - residual;
          const p = prodById(document.getElementById("cdProd").value);
          const rateEl = document.getElementById("drRate");
          const rate = rateEl ? (parseFloat(rateEl.value) || 0) : (custRate() || (p ? p.price_per_kg || 0 : 0));
          document.getElementById("drSummary").innerHTML = `
            Delivered: <b>${qty} x ${kg} kg = ${delivered.toFixed(2)} kg</b><br>
            Less residual of ${rows.length} pulled-out cylinder(s): <b>${residual.toFixed(2)} kg</b><br>
            Billable: <b>${billable.toFixed(2)} kg</b> @ ${money(rate)}/kg = <b style="color:var(--green-dark)">${money(billable * rate)}</b>
            ${!rate ? `<br><span style="color:var(--danger)">No price per kg set on this product${canRate ? " — enter one above" : " — ask Admin/Accounting to set it"}.</span>` : ""}
            ${rows.length !== qty ? `<br><span style="color:var(--muted)">Note: ${rows.length} pulled out vs ${qty} delivered.</span>` : ""}`;
        }
        function addPullRow() {
          const row = document.createElement("div");
          row.className = "pay-row";
          row.innerHTML = `
            <input type="text" class="poSerial" placeholder="Serial no." style="flex:1;min-width:0">
            <input type="number" step="0.01" class="poGW" placeholder="Gross Wt" style="flex:1;min-width:0">
            <input type="number" step="0.01" class="poTW" placeholder="Tare Wt" style="flex:1;min-width:0">
            <input type="number" step="0.01" class="poRes" placeholder="Residual" style="flex:1;min-width:0">
            <button class="btn small danger" type="button">&times;</button>`;
          row.querySelectorAll("input").forEach(i => i.addEventListener("input", drRecalc));
          row.querySelector("button").addEventListener("click", () => { row.remove(); drRecalc(); });
          pullRows.appendChild(row);
        }
        document.getElementById("drAddPullBtn").addEventListener("click", () => { addPullRow(); drRecalc(); });
        document.getElementById("drMatchBtn").addEventListener("click", () => {
          const qty = parseInt(document.getElementById("drQty").value) || 0;
          while (pullRows.children.length < qty) addPullRow();
          while (pullRows.children.length > qty) pullRows.lastElementChild.remove();
          drRecalc();
        });
        ["drQty", "drKg", "drRate"].forEach(id => { const el = document.getElementById(id); if (el) el.addEventListener("input", drRecalc); });
        document.getElementById("cdProd").addEventListener("change", syncProductDefaults);
        const p50 = state.products.find(p => kgFromName(p) === 50 && /50\s*-?\s*kg/i.test(p.name + " " + (p.sku || "")));
        if (p50) document.getElementById("cdProd").value = p50.id;
        syncProductDefaults();

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

        // Previous reading always comes from the system: the last bill's
        // current reading, or the meter's beginning reading for its first bill.
        async function suggestPreviousReading() {
          const custId = document.getElementById("cdCust").value;
          if (!custId) return;
          const label = document.getElementById("cdMeterLabel").value.trim();
          try {
            const r = await api(`/commercial-deliveries/last-reading?customer_id=${custId}&meter_label=${encodeURIComponent(label)}`);
            document.getElementById("cdMeterList").innerHTML = r.meters.filter(m => m).map(m => `<option value="${esc(m)}">`).join("");
            const has = r.previous_reading !== null;
            document.getElementById("cdPrevReading").value = has ? r.previous_reading : "";
            document.getElementById("cdPrevSrc").textContent = !has ? "" : r.source === "BEGINNING" ? "(beginning reading)" : "(from the last bill)";
            document.getElementById("cdBeginBox").style.display = has ? "none" : "";
            updateConsumptionPreview();
          } catch (e) {}
        }
        document.getElementById("cdMeterLabel").addEventListener("change", suggestPreviousReading);
        document.getElementById("cdBeginSave").addEventListener("click", async () => {
          const v = document.getElementById("cdBeginReading").value;
          if (v === "") { toast("Enter the beginning reading", "error"); return; }
          try {
            await api("/commercial-meters", { method: "POST", body: {
              customer_id: document.getElementById("cdCust").value,
              meter_label: document.getElementById("cdMeterLabel").value.trim(), beginning_reading: v } });
            toast("Beginning reading saved", "success");
            suggestPreviousReading();
          } catch (e) {}
        });

        document.getElementById("cdType").addEventListener("change", (e) => {
          const isReading = e.target.value === "READING";
          document.getElementById("drFields").classList.toggle("hidden", e.target.value !== "DR");
          document.getElementById("wiwoFields").classList.toggle("hidden", e.target.value !== "WIWO");
          document.getElementById("readingFields").classList.toggle("hidden", !isReading);
          if (isReading) suggestPreviousReading();
        });
        document.getElementById("cdCust").addEventListener("change", () => {
          syncRate(); drRecalc();
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
          if (billingType === "DR") {
            base.dr_ref = document.getElementById("drRef").value.trim();
            base.delivered_qty = document.getElementById("drQty").value;
            base.kg_per_cylinder = document.getElementById("drKg").value;
            const rateEl = document.getElementById("drRate");
            if (rateEl && rateEl.value) base.price_per_kg = rateEl.value;
            base.pullouts = [...pullRows.querySelectorAll(".pay-row")].map(r => ({
              serial_no: r.querySelector(".poSerial").value,
              gross_weight: r.querySelector(".poGW").value,
              tare_weight: r.querySelector(".poTW").value,
              residual_kg: r.querySelector(".poRes").value,
            })).filter(po => po.serial_no || po.gross_weight || po.tare_weight || po.residual_kg);
            if (!base.delivered_qty || parseInt(base.delivered_qty) < 1) { toast("Enter the number of cylinders delivered", "error"); return; }
            try {
              const r = await api("/commercial-deliveries", { method: "POST", body: base });
              closeModal(); load();
              toast(`Billed ${r.billable_kg} kg = ${money(r.billed_amount)} (${r.delivery_no}, ${r.dr_no})`, "success");
              if (confirm("Billing saved. Print the Delivery Receipt now?")) printSaleDR(r.sale_id);
            } catch (e) {}
            return;
          }
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
            if (prev === "") { toast("Save the beginning reading of this meter first", "error"); return; }
            if (!curr) { toast("Enter the current reading", "error"); return; }
            base.meter_label = document.getElementById("cdMeterLabel").value.trim();
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
    const drHtml = d.billing_type === "DR" ? `
      <div class="field"><b>DR No.:</b> ${esc(d.dr_ref || '—')} ${d.sale_dr_no ? `&nbsp; <b>System DR:</b> ${esc(d.sale_dr_no)}` : ""} ${d.sale_no ? `&nbsp; <b>Sale:</b> ${esc(d.sale_no)}` : ""}</div>
      <div class="field"><b>Delivered:</b> ${d.delivered_qty} x ${d.kg_per_cylinder} kg = ${d.delivered_kg} kg</div>
      <div class="table-wrap"><table><thead><tr><th>Pulled-out Serial</th><th>Gross Wt</th><th>Tare</th><th>Residual (kg)</th></tr></thead><tbody>
        ${d.cylinders.length ? d.cylinders.map(po => `<tr><td>${esc(po.serial_no || '—')}</td><td>${po.gross_weight ?? '—'}</td><td>${po.tare_weight ?? '—'}</td><td>${po.residual_kg}</td></tr>`).join("")
          : `<tr><td colspan="4" style="color:var(--muted)">No pull-outs</td></tr>`}
      </tbody></table></div>
      <div class="field"><b>Total residual:</b> ${d.total_residual_kg || 0} kg &nbsp; <b>Billable:</b> ${d.total_consumption_weight} kg</div>` : "";
    const detailHtml = d.billing_type === "DR" ? drHtml : d.billing_type === "WIWO" ? `
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
      <div class="field"><b>Type:</b> <span class="badge ${typeBadge(d.billing_type)}">${d.billing_type}</span></div>
      <div class="field"><b>Customer:</b> ${esc(d.customer_name)} (${d.account_no})</div>
      <div class="field"><b>Location:</b> ${esc(d.location_name)} &nbsp; <b>Product:</b> ${esc(d.product_name)}</div>
      <div class="field"><b>Status:</b> <span class="badge ${statusColor(d.status)}">${d.status}</span></div>
      ${detailHtml}
      ${d.billed_amount ? `<div class="field" style="margin-top:10px"><b>Billed:</b> ${money(d.price_per_kg)}/unit x ${d.total_consumption_weight} = ${money(d.billed_amount)}</div>` : ""}
      <div class="modal-actions">
        <button class="btn" onclick="closeModal()">Close</button>
        <button class="btn" onclick="printCommercialDelivery(${id})">Print</button>
        ${d.billing_type === "DR" && d.sale_id ? `<button class="btn" onclick="printSaleDR(${d.sale_id})">Print DR</button>` : ""}
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
    let acctRate = 0;
    try { const cu = (await api("/customers?q=" + encodeURIComponent(d.account_no))).find(x => x.id === d.customer_id); acctRate = cu && cu.commercial_rate > 0 ? cu.commercial_rate : 0; } catch (e) {}
    const defaultRate = acctRate || (prod ? prod.price_per_kg : 0);
    const unitLabel = d.billing_type === "WIWO" ? "kg" : "unit(s)";
    openModal("Generate Invoice", `
      <div class="field"><b>Total Consumption:</b> ${totalConsumption} ${unitLabel}</div>
      <div class="field"><label>Price per ${d.billing_type === 'WIWO' ? 'KG' : 'Unit'} ${acctRate ? `<span style="font-weight:400;color:var(--muted)">(account rate)</span>` : ""}</label>
        <input type="number" step="0.01" id="billRate" value="${defaultRate}" ${acctRate ? "readonly" : ""}></div>
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
          toast(`Invoice created for ${money(result.billed_amount)}`, "success");
          load();
        } catch (e) {}
      });
    });
  };

  window.voidCommercialBilling = async (id) => {
    if (!confirm("Void this billing? Its invoice is cancelled (only allowed while nothing is paid). A billing generated from a sale can then be generated again.")) return;
    try { await api(`/commercial-deliveries/${id}/void-billing`, { method: "POST" }); toast("Billing voided", "success"); load(); }
    catch (e) {}
  };

  window.cancelCommercialDelivery = async (id) => {
    if (!confirm("Cancel this commercial delivery?")) return;
    try { await api(`/commercial-deliveries/${id}/cancel`, { method: "POST" }); toast("Delivery cancelled", "success"); load(); }
    catch (e) {}
  };

  window.printCommercialDelivery = async (id) => {
    const d = await api(`/commercial-deliveries/${id}`);
    const detailRows = d.billing_type === "DR"
      ? `<div style="margin:10px 0">DR No.: ${esc(d.dr_ref || '—')}${d.sale_dr_no ? ` (system ${esc(d.sale_dr_no)})` : ""}<br>
          Delivered: ${d.delivered_qty} x ${d.kg_per_cylinder} kg = ${d.delivered_kg} kg</div>
        <table style="width:100%;border-collapse:collapse;font-size:12px;margin:10px 0">
          <tr style="border-bottom:1px solid #ccc"><th style="text-align:left">Pulled-out Serial</th><th>Gross Wt</th><th>Tare</th><th>Residual (kg)</th></tr>
          ${d.cylinders.map(po => `<tr><td>${esc(po.serial_no || '—')}</td><td>${po.gross_weight ?? '—'}</td><td>${po.tare_weight ?? '—'}</td><td>${po.residual_kg}</td></tr>`).join("")}
        </table>
        <div>Total residual deducted: ${d.total_residual_kg || 0} kg</div>`
      : d.billing_type === "WIWO"
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
          <div style="color:#6b7785">Commercial LPG Billing — ${d.billing_type === 'WIWO' ? 'Weight In / Weight Out' : d.billing_type === 'DR' ? 'DR Billing (delivered less residual)' : 'Meter Reading'}</div>
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
          <b>${d.billing_type === 'DR' ? 'Billable' : 'Total Consumption'}:</b> ${d.total_consumption_weight ?? '—'} ${d.billing_type !== 'READING' ? 'kg' : ''}<br>
          ${d.price_per_kg ? `<b>Rate:</b> ${money(d.price_per_kg)}<br>` : ""}
          ${d.billed_amount ? `<b style="color:#1e9e5a">Billed Amount: ${money(d.billed_amount)}</b>` : ""}
        </div>
        <div style="margin-top:24px;color:#6b7785;font-size:11px;text-align:center">Printed ${new Date().toLocaleString()}</div>
      </div>`;
    printDocument(html);
  };

  load();
}

// ================================================================== SALES DEPARTMENT: TARGETS
// 4 tiers per month, Domestic (11kg & 1.4kg) and Commercial (50kg), for the
// whole business, a location, or a customer (MRTO / ERTO). Quantities are
// cylinders.
async function renderSalesTargets(c) {
  const SEGMENTS = [["DOMESTIC", "Domestic (11kg & 1.4kg)"], ["COMMERCIAL", "Commercial (50kg)"]];
  const TIERS = [1, 2, 3, 4];
  const now = new Date();
  const thisMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const customers = await api("/customers?q=");
  const locOpts = state.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
  c.innerHTML = `
    <div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap">
      <button class="btn green" data-tab="ach">Achievement</button>
      <button class="btn" data-tab="set">Set Targets</button>
    </div>
    <div id="stAch">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
        <label style="margin:0">Month</label><input type="month" id="stMonth" value="${thisMonth}" style="width:auto">
      </div>
      <p style="color:var(--muted);font-size:13px;margin-top:0">Actual = cylinders on completed sales in the month (voided sales excluded). Domestic counts 11kg and 1.4kg cylinders; Commercial counts 50kg cylinders and 50kg commercial billings (cylinders delivered). Green = tier reached.</p>
      <div id="stAchTable">Loading...</div>
    </div>
    <div id="stSet" class="hidden">
      <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-bottom:10px">
        <div class="field" style="margin:0"><label>Target for</label><select id="stScope" style="width:auto">
          <option value="OVERALL">Overall business</option><option value="LOCATION">A location</option><option value="CUSTOMER">A customer</option>
        </select></div>
        <div class="field hidden" id="stLocWrap" style="margin:0"><label>Location</label><select id="stLoc" style="width:auto">${locOpts}</select></div>
        <div class="field hidden" id="stCustWrap" style="margin:0;min-width:240px"><label>Customer</label>
          <select id="stCust"></select>
          <label style="display:flex;align-items:center;gap:6px;font-weight:400;font-size:12.5px;margin-top:4px">
            <input type="checkbox" id="stCustAll" style="width:auto"> Show all categories (default: MRTO &amp; ERTO only)</label>
        </div>
        <div class="field" style="margin:0"><label>Year</label><input type="number" id="stYear" value="${now.getFullYear()}" style="width:100px"></div>
        <button class="btn" id="stLoadBtn">Load</button>
      </div>
      <div id="stGrid"></div>
    </div>`;

  c.querySelectorAll("[data-tab]").forEach(b => b.addEventListener("click", () => {
    c.querySelectorAll("[data-tab]").forEach(x => x.classList.toggle("green", x === b));
    document.getElementById("stAch").classList.toggle("hidden", b.dataset.tab !== "ach");
    document.getElementById("stSet").classList.toggle("hidden", b.dataset.tab !== "set");
    if (b.dataset.tab === "ach") loadAch(); else loadGrid();
  }));

  const fmt = n => (n === null || n === undefined) ? "" : Number(n).toLocaleString("en-PH", { maximumFractionDigits: 2 });
  function tierCells(r) {
    return TIERS.map(t => {
      const q = r.tiers[String(t)];
      if (!q) return `<td style="color:var(--muted)">—</td>`;
      const hit = r.actual >= q;
      return `<td style="${hit ? "background:#e3f6ec;color:var(--green-dark);font-weight:700" : ""}">${fmt(q)}<div style="font-size:11px;color:var(--muted);font-weight:400">${r.pct[String(t)]}%</div></td>`;
    }).join("");
  }
  function reachedCell(r) {
    const any = TIERS.some(t => r.tiers[String(t)]);
    if (!any) return `<td style="color:var(--muted)">No target</td>`;
    return `<td>${r.reached_tier ? `<span class="badge green">Tier ${r.reached_tier}</span>` : `<span class="badge gray">None yet</span>`}
      ${r.next_tier ? `<div style="font-size:11.5px;color:var(--muted);margin-top:2px">${fmt(r.to_next_tier)} more to Tier ${r.next_tier}</div>` : ""}</td>`;
  }

  async function loadAch() {
    const month = document.getElementById("stMonth").value || thisMonth;
    const el = document.getElementById("stAchTable");
    el.textContent = "Loading...";
    let data;
    try { data = await api(`/sales-targets/achievement?month=${month}`); } catch (e) { el.textContent = ""; return; }
    const scopeLabel = { OVERALL: "Overall", LOCATION: "Location", CUSTOMER: "Customer" };
    el.innerHTML = `<h3 style="margin:4px 0 8px">${esc(data.month_label)}</h3>
      <div class="table-wrap"><table><thead>
        <tr><th rowspan="2">Target for</th>${SEGMENTS.map(([, l]) => `<th colspan="6" style="text-align:center">${l}</th>`).join("")}</tr>
        <tr>${SEGMENTS.map(() => `<th>Actual</th>${TIERS.map(t => `<th>Tier ${t}</th>`).join("")}<th>Reached</th>`).join("")}</tr>
      </thead><tbody>
        ${data.rows.map(r => `<tr>
          <td><span class="badge ${r.scope === 'OVERALL' ? 'blue' : r.scope === 'LOCATION' ? 'warn' : 'gray'}">${scopeLabel[r.scope]}</span> ${esc(r.name)}</td>
          ${SEGMENTS.map(([k]) => { const sg = r.segments[k]; return `<td style="font-weight:700">${fmt(sg.actual)}</td>${tierCells(sg)}${reachedCell(sg)}`; }).join("")}
        </tr>`).join("")}
      </tbody></table></div>
      ${data.rows.length <= 1 ? `<p style="color:var(--muted);font-size:13px">No location or customer targets for this month yet — add them under Set Targets.</p>` : ""}`;
  }
  document.getElementById("stMonth").addEventListener("change", loadAch);

  function fillCustomers() {
    const all = document.getElementById("stCustAll").checked;
    const list = customers.filter(cu => all || ["MRTO", "ERTO"].includes((cu.category || "").toUpperCase()));
    document.getElementById("stCust").innerHTML = list.length
      ? list.map(cu => `<option value="${cu.id}">${esc(cu.name)} (${esc(cu.account_no)}) — ${esc(cu.category || "")}</option>`).join("")
      : `<option value="">No ${all ? "" : "MRTO / ERTO "}customers</option>`;
  }
  fillCustomers();
  document.getElementById("stCustAll").addEventListener("change", () => { fillCustomers(); loadGrid(); });
  document.getElementById("stScope").addEventListener("change", e => {
    document.getElementById("stLocWrap").classList.toggle("hidden", e.target.value !== "LOCATION");
    document.getElementById("stCustWrap").classList.toggle("hidden", e.target.value !== "CUSTOMER");
    loadGrid();
  });
  ["stLoc", "stCust"].forEach(id => document.getElementById(id).addEventListener("change", loadGrid));
  document.getElementById("stLoadBtn").addEventListener("click", loadGrid);

  function scopeParams() {
    const scope = document.getElementById("stScope").value;
    const scope_id = scope === "LOCATION" ? document.getElementById("stLoc").value
      : scope === "CUSTOMER" ? document.getElementById("stCust").value : 0;
    return { scope, scope_id, year: parseInt(document.getElementById("stYear").value) || now.getFullYear() };
  }

  async function loadGrid() {
    const el = document.getElementById("stGrid");
    const p = scopeParams();
    if (p.scope !== "OVERALL" && !p.scope_id) { el.innerHTML = `<p style="color:var(--muted)">Pick a ${p.scope.toLowerCase()}.</p>`; return; }
    el.textContent = "Loading...";
    let data;
    try { data = await api(`/sales-targets?scope=${p.scope}&scope_id=${p.scope_id}&year=${p.year}`); } catch (e) { el.textContent = ""; return; }
    const shortMonth = data.month_labels.map(l => l.split(" ")[0]);
    el.innerHTML = `<h3 style="margin:4px 0 8px">${esc(data.scope_name)} — ${data.year}</h3>
      <p style="color:var(--muted);font-size:13px;margin-top:0">Cylinders per month. Each month can have different quantities. Leave a cell blank for no target.</p>
      <div class="table-wrap"><table class="st-grid"><thead><tr><th style="min-width:150px"></th>${shortMonth.map(m => `<th>${m} ${data.year}</th>`).join("")}</tr></thead><tbody>
        ${SEGMENTS.map(([seg, label]) => `
          <tr><td colspan="13" style="background:var(--bg);font-weight:800">${label}</td></tr>
          ${TIERS.map(t => `<tr><td>Tier ${t}</td>${data.months.map(ym => {
            const q = data.segments[seg][ym].tiers[String(t)];
            return `<td><input type="number" min="0" step="1" class="stCell" data-seg="${seg}" data-month="${ym}" data-tier="${t}" value="${q ?? ""}" style="width:78px"></td>`;
          }).join("")}</tr>`).join("")}
          <tr style="font-weight:700"><td>Actual</td>${data.months.map(ym => {
            const r = data.segments[seg][ym];
            return `<td>${fmt(r.actual)}${r.reached_tier ? `<div><span class="badge green">T${r.reached_tier}</span></div>` : ""}</td>`;
          }).join("")}</tr>`).join("")}
      </tbody></table></div>
      <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn green" id="stSaveBtn">Save Targets</button>
        <button class="btn" id="stCopyBtn" title="Copy each tier's January value to every blank month">Fill blank months from January</button>
      </div>`;
    document.getElementById("stCopyBtn").addEventListener("click", () => {
      SEGMENTS.forEach(([seg]) => TIERS.forEach(t => {
        const cells = [...el.querySelectorAll(`.stCell[data-seg="${seg}"][data-tier="${t}"]`)];
        const jan = cells[0] && cells[0].value;
        if (jan) cells.slice(1).forEach(ci => { if (!ci.value) ci.value = jan; });
      }));
    });
    document.getElementById("stSaveBtn").addEventListener("click", async () => {
      const targets = [...el.querySelectorAll(".stCell")].map(i => ({
        segment: i.dataset.seg, month: i.dataset.month, tier: parseInt(i.dataset.tier), qty: i.value,
      }));
      try {
        await api("/sales-targets", { method: "PUT", body: { ...p, targets } });
        toast("Targets saved", "success"); loadGrid();
      } catch (e) {}
    });
  }

  loadAch();
}
