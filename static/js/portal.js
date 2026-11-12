// GUFC Customer Portal: login, place orders, order status, purchases, billing & SOA, rate per kg.
const P = { me: null, view: "home", products: [] };

async function api(path, opts = {}) {
  const res = await fetch("/api/portal" + path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch (e) { /* no body */ }
  if (res.status === 401 && path !== "/login") { showLogin(path === "/me" ? null : data.error); throw new Error("logged out"); }
  if (!res.ok) throw new Error(data.error || "Something went wrong. Please try again.");
  return data;
}

const esc = s => (s ?? "").toString().replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = n => "₱" + Number(n || 0).toLocaleString("en-PH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const day = ts => (ts || "").slice(0, 10);
const qtyFmt = q => Number.isInteger(Number(q)) ? q : Number(q).toLocaleString(undefined, { maximumFractionDigits: 2 }) + " kg";

function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind; t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

// ---------------------------------------------------------------- login
function showLogin(message) {
  P.me = null;
  document.getElementById("appView").classList.add("hidden");
  document.getElementById("loginView").classList.remove("hidden");
  const err = document.getElementById("loginErr");
  err.textContent = message || ""; err.classList.toggle("hidden", !message);
}

document.getElementById("loginForm").addEventListener("submit", async e => {
  e.preventDefault();
  const btn = document.getElementById("loginBtn"), err = document.getElementById("loginErr");
  btn.disabled = true; err.classList.add("hidden");
  try {
    await api("/login", { method: "POST", body: {
      username: document.getElementById("lUser").value, password: document.getElementById("lPass").value } });
    document.getElementById("lPass").value = "";
    await start();
  } catch (ex) {
    err.textContent = ex.message; err.classList.remove("hidden");
  } finally { btn.disabled = false; }
});

document.getElementById("logoutBtn").addEventListener("click", async () => {
  try { await api("/logout", { method: "POST" }); } catch (e) { /* already out */ }
  showLogin();
});

// ---------------------------------------------------------------- shell
async function start() {
  P.me = await api("/me");
  document.getElementById("custName").textContent = P.me.business_name || P.me.name;
  document.getElementById("custSub").textContent =
    `${P.me.account_no}${P.me.business_name ? " · " + P.me.name : ""}${P.me.terms_days ? ` · ${P.me.terms_days}-day terms` : ""}`;
  const tabs = [["home", "Home"], ["order", "Place Order"], ["orders", "My Orders"], ["purchases", "Purchases"]];
  if (P.me.has_rate) tabs.push(["rate", "My Rate"]);
  if (P.me.is_credit) tabs.push(["billing", "Billing & SOA"]);
  tabs.push(["account", "Account"]);
  document.getElementById("tabs").innerHTML = tabs.map(([id, l]) => `<button data-v="${id}">${l}</button>`).join("");
  document.querySelectorAll("#tabs button").forEach(b => b.addEventListener("click", () => go(b.dataset.v)));
  document.getElementById("loginView").classList.add("hidden");
  document.getElementById("appView").classList.remove("hidden");
  go(location.hash.slice(1) || "home");
}

function go(view) {
  if (!document.querySelector(`#tabs button[data-v="${view}"]`)) view = "home";
  P.view = view;
  history.replaceState(null, "", "#" + view);
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("active", b.dataset.v === view));
  const main = document.getElementById("main");
  main.innerHTML = `<p class="muted">Loading...</p>`;
  ({ home: viewHome, order: viewOrder, orders: viewOrders, purchases: viewPurchases, rate: viewRate, billing: viewBilling, account: viewAccount }[view])(main)
    .catch(e => { if (e.message !== "logged out") main.innerHTML = `<div class="msg err">${esc(e.message)}</div>`; });
}

const statusBadge = s => ({ Received: "b-warn", Confirmed: "b-blue", Completed: "b-green", Cancelled: "b-danger" }[s] || "b-gray");
const invBadge = s => ({ PAID: "b-green", CURRENT: "b-blue", "DUE SOON": "b-warn", "PARTIALLY PAID": "b-warn", OVERDUE: "b-danger" }[s] || "b-gray");

function orderCard(o) {
  const step = { Received: 1, Confirmed: 2, Completed: 3, Cancelled: 3 }[o.status_label] || 1;
  return `<div class="order">
    <div class="hd"><div><b>${esc(o.order_no)}</b> <span class="muted small">· ${day(o.order_date)}</span></div>
      <span class="badge ${statusBadge(o.status_label)}">${esc(o.status_label)}</span></div>
    <div class="steps ${o.status_label === "Cancelled" ? "cancel" : ""}">${[1, 2, 3].map(i => `<div class="${i <= step ? "on" : ""}"></div>`).join("")}</div>
    <div class="steplbl"><span>Received</span><span>Confirmed</span><span>${o.status_label === "Cancelled" ? "Cancelled" : "Completed"}</span></div>
    <p class="small" style="margin:8px 0">${esc(o.status_detail)}</p>
    <div class="small">${o.items.map(i => `${esc(i.product_name)} × ${i.qty}`).join("<br>")}</div>
    <div class="small muted" style="margin-top:6px">Est. ${money(o.estimated_total)} at current prices${o.delivery_address ? ` · Deliver to: ${esc(o.delivery_address)}` : ""}</div>
    ${o.can_cancel ? `<div style="margin-top:10px"><button class="btn small danger" data-cancel="${o.id}" data-no="${esc(o.order_no)}">Cancel order</button></div>` : ""}
  </div>`;
}

function wireCancel(root, after) {
  root.querySelectorAll("[data-cancel]").forEach(b => b.addEventListener("click", async () => {
    if (!confirm(`Cancel order ${b.dataset.no}?`)) return;
    try { await api(`/orders/${b.dataset.cancel}/cancel`, { method: "POST" }); toast("Order cancelled", "ok"); after(); }
    catch (e) { toast(e.message, "err"); }
  }));
}

// ---------------------------------------------------------------- home
async function viewHome(main) {
  const [orders, billing, rates] = await Promise.all([api("/orders"), P.me.is_credit ? api("/billing") : Promise.resolve(null),
    P.me.has_rate ? api("/rates").catch(() => null) : Promise.resolve(null)]);
  const rc = rates && rates.current;
  const open = orders.filter(o => o.status === "PENDING");
  main.innerHTML = `
    <div class="grid">
      <div class="stat"><div class="l">Open Orders</div><div class="v">${open.length}</div><div class="s">being processed</div></div>
      ${billing ? `
        <div class="stat ${billing.outstanding > 0 ? "" : "green"}"><div class="l">Balance Due</div><div class="v">${money(billing.outstanding)}</div>
          <div class="s">${billing.open_count} open invoice(s)</div></div>
        <div class="stat ${billing.overdue > 0 ? "danger" : ""}"><div class="l">Overdue</div><div class="v">${money(billing.overdue)}</div>
          <div class="s">${billing.overdue > 0 ? "please settle soon" : "nothing overdue"}</div></div>
        <div class="stat"><div class="l">Next Due</div><div class="v" style="font-size:17px">${billing.next_due ? billing.next_due.due_date : "—"}</div>
          <div class="s">${billing.next_due ? money(billing.next_due.balance) + " · " + esc(billing.next_due.invoice_no) : "no upcoming due"}</div></div>`
        : `<div class="stat" style="grid-column:span 3"><div class="l">Account</div><div class="v" style="font-size:16px">${esc(P.me.name)}</div>
          <div class="s">${esc(P.me.address || "")}</div></div>`}
    </div>
    ${rc && rc.rate ? `<div class="card rate-home">
      <div><div class="muted small">Your current rate${rc.product_name ? " · " + esc(rc.product_name) : ""}</div>
        <div class="rate-big">${rateFmt(rc.rate)}<span>/kg</span></div>
        <div class="small">${rc.change ? `${moveBadge(rc.change, rc.change_pct)} from ${rateFmt(rc.previous_rate)} since ${day(rc.since)}` : rc.since ? `since ${day(rc.since)}` : ""}</div></div>
      <button class="btn small" id="homeRate">See rate movement</button></div>` : ""}
    <div class="card" style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
      <div><h2 style="margin:0">Need LPG?</h2><div class="muted small">Order online and we'll confirm it shortly.</div></div>
      <button class="btn green" id="homeOrder">Place an Order</button>
    </div>
    <div class="card"><h2>Recent Orders</h2>
      <div id="homeOrders">${orders.length ? orders.slice(0, 3).map(orderCard).join("") : `<p class="muted">No orders yet.</p>`}</div>
      ${orders.length > 3 ? `<button class="btn small" id="allOrders">See all orders</button>` : ""}</div>`;
  document.getElementById("homeOrder").addEventListener("click", () => go("order"));
  const hr = document.getElementById("homeRate"); if (hr) hr.addEventListener("click", () => go("rate"));
  const all = document.getElementById("allOrders"); if (all) all.addEventListener("click", () => go("orders"));
  wireCancel(main, () => go("home"));
}

// ---------------------------------------------------------------- place order
async function viewOrder(main) {
  P.products = await api("/products");
  main.innerHTML = `<div class="card" style="max-width:620px">
    <h2>Place an Order</h2>
    <div id="oErr" class="msg err hidden"></div>
    <div id="itemRows"></div>
    <button type="button" class="add-btn" id="addItem">+ Add another product</button>
    <div style="display:flex;justify-content:space-between;margin:14px 0;font-weight:700"><span>Estimated total</span><span id="oTotal">₱0.00</span></div>
    <div class="field"><label for="oAddr">Delivery address</label><textarea id="oAddr" rows="2">${esc(P.me.address || "")}</textarea></div>
    <div class="field"><label for="oNotes">Notes (preferred time, landmarks, etc.)</label><textarea id="oNotes" rows="2"></textarea></div>
    <button class="btn green block" id="oSubmit">Submit Order</button>
    <p class="muted small" style="margin:10px 0 0">Prices shown are current prices. The final amount is on your receipt / delivery receipt${P.me.is_credit ? " and will be charged to your account per your terms" : ""}.</p>
  </div>`;
  const rows = document.getElementById("itemRows");
  const opts = P.products.map(p => `<option value="${p.id}" data-price="${p.price}">${esc(p.name)} — ${money(p.price)}</option>`).join("");
  function total() {
    let t = 0;
    rows.querySelectorAll(".item-row").forEach(r => {
      const o = r.querySelector("select").selectedOptions[0];
      t += (o ? Number(o.dataset.price) : 0) * (parseInt(r.querySelector("input").value, 10) || 0);
    });
    document.getElementById("oTotal").textContent = money(t);
  }
  function addRow() {
    const r = document.createElement("div");
    r.className = "item-row";
    r.innerHTML = `<select aria-label="Product">${opts}</select><input type="number" min="1" max="500" value="1" aria-label="Quantity"><button class="rm" aria-label="Remove">&times;</button>`;
    r.querySelector(".rm").addEventListener("click", () => { if (rows.children.length > 1) { r.remove(); total(); } });
    r.querySelector("select").addEventListener("change", total);
    r.querySelector("input").addEventListener("input", total);
    rows.appendChild(r); total();
  }
  if (!P.products.length) { rows.innerHTML = `<p class="muted">No products available right now.</p>`; return; }
  addRow();
  document.getElementById("addItem").addEventListener("click", addRow);
  document.getElementById("oSubmit").addEventListener("click", async () => {
    const btn = document.getElementById("oSubmit"), err = document.getElementById("oErr");
    const items = [...rows.querySelectorAll(".item-row")].map(r => ({
      product_id: r.querySelector("select").value, qty: parseInt(r.querySelector("input").value, 10) || 0 }));
    if (items.some(i => i.qty < 1)) { err.textContent = "Quantity must be at least 1."; err.classList.remove("hidden"); return; }
    btn.disabled = true; err.classList.add("hidden");
    try {
      const r = await api("/orders", { method: "POST", body: {
        items, delivery_address: document.getElementById("oAddr").value, notes: document.getElementById("oNotes").value } });
      main.innerHTML = `<div class="card" style="max-width:620px;text-align:center;padding:30px 16px">
        <div style="font-size:40px">✅</div><h2>Order received!</h2>
        <p>Your order number is <b>${esc(r.order_no)}</b>.</p>
        <p class="muted small">We'll confirm it shortly. You can follow its status under My Orders.</p>
        <button class="btn green" id="toOrders">View My Orders</button></div>`;
      document.getElementById("toOrders").addEventListener("click", () => go("orders"));
    } catch (e) {
      err.textContent = e.message; err.classList.remove("hidden"); btn.disabled = false;
    }
  });
}

// ---------------------------------------------------------------- my orders
async function viewOrders(main) {
  const orders = await api("/orders");
  main.innerHTML = `<h2>My Orders</h2>${orders.length ? orders.map(orderCard).join("")
    : `<div class="card"><p class="muted">No orders yet.</p><button class="btn green" id="first">Place your first order</button></div>`}`;
  const f = document.getElementById("first"); if (f) f.addEventListener("click", () => go("order"));
  wireCancel(main, () => go("orders"));
}

// ---------------------------------------------------------------- purchases
async function viewPurchases(main) {
  main.innerHTML = `<div class="card"><h2>My Purchases</h2>
    <div class="filters"><label class="small muted" for="pFrom">From</label><input type="date" id="pFrom">
      <label class="small muted" for="pTo">to</label><input type="date" id="pTo"><button class="btn small" id="pRun">Show</button></div>
    <div id="pOut"><p class="muted">Loading...</p></div></div>`;
  async function load() {
    const q = new URLSearchParams();
    const f = document.getElementById("pFrom").value, t = document.getElementById("pTo").value;
    if (f) q.set("from", f); if (t) q.set("to", t);
    const d = await api("/purchases?" + q);
    document.getElementById("pOut").innerHTML = `
      <p class="small"><b>${d.totals.count}</b> purchase(s) · total <b>${money(d.totals.amount)}</b></p>
      <div class="table-wrap"><table><thead><tr><th>Date</th><th>Receipt #</th><th>Items</th><th class="right">Total</th><th>Payment</th></tr></thead><tbody>
      ${d.purchases.length ? d.purchases.map(s => `<tr>
        <td>${day(s.ts)}</td>
        <td>${esc(s.sale_no)}${s.dr_no ? `<br><span class="muted small">DR ${esc(s.dr_no)}</span>` : ""}
          ${s.status === "VOIDED" ? `<br><span class="badge b-danger">VOIDED</span>` : ""}</td>
        <td class="small wrap">${s.items.map(i => `${esc(i.product_name)} × ${qtyFmt(i.qty)}${i.sale_mode === "DEPOSIT" ? " (with deposit)" : i.sale_mode === "SWAP" ? " (swap)" : ""}`).join("<br>")}</td>
        <td class="right">${money(s.total)}${s.discount > 0 ? `<br><span class="muted small">less ${money(s.discount)}</span>` : ""}</td>
        <td class="small">${s.sale_type === "CREDIT"
          ? `Charged to account${s.invoice_no ? `<br>${esc(s.invoice_no)} · due ${esc(s.due_date)}` : ""}${s.invoice_status ? `<br><span class="badge ${invBadge(s.invoice_status)}">${esc(s.invoice_status)}</span>` : ""}`
          : "Paid"}</td></tr>`).join("")
        : `<tr><td colspan="5" class="muted">No purchases in this period.</td></tr>`}
      </tbody></table></div>`;
  }
  document.getElementById("pRun").addEventListener("click", () => load().catch(e => toast(e.message, "err")));
  await load();
}

// ---------------------------------------------------------------- rate per kg & rate movement
const rateFmt = n => n === null || n === undefined ? "—"
  : "₱" + Number(n).toLocaleString("en-PH", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
function moveBadge(change, pct) {
  if (!change) return `<span class="badge b-gray">no change</span>`;
  const up = change > 0;
  return `<span class="badge ${up ? "b-danger" : "b-green"}">${up ? "▲" : "▼"} ${rateFmt(Math.abs(change))}${pct !== null && pct !== undefined ? ` (${up ? "+" : "−"}${Math.abs(pct).toFixed(2)}%)` : ""}</span>`;
}

// Step chart of the rate in effect over time, with the rate on each billing as a dot.
function rateChart(movements, billings) {
  const asc = [...movements].reverse();
  if (!asc.length) return "";
  const t = s => new Date((s || "").replace(" ", "T")).getTime();
  const now = Date.now(), yearAgo = now - 365 * 864e5;
  let start = Math.max(t(asc[0].date), yearAgo);
  // Rate in effect at the start of the window.
  let startRate = asc[0].new_rate;
  asc.forEach(m => { if (t(m.date) <= start) startRate = m.new_rate; });
  const pts = [[start, startRate], ...asc.filter(m => t(m.date) > start).map(m => [t(m.date), m.new_rate])];
  const dots = billings.filter(b => t(b.delivery_date) >= start).map(b => [t(b.delivery_date), b.price_per_kg]);
  if (now - start < 864e5) start = now - 30 * 864e5;
  const ys = [...pts.map(p => p[1]), ...dots.map(d => d[1])];
  let lo = Math.min(...ys), hi = Math.max(...ys);
  const pad = (hi - lo) * 0.15 || Math.max(hi * 0.05, 1); lo -= pad; hi += pad;
  // Draw at the card's real width so labels stay readable on phones.
  const W = Math.max(300, Math.min(920, (document.getElementById("main").clientWidth || 640) - 66)), H = 200, L = 52, R = 12, T = 12, B = 26;
  const x = v => L + (v - start) / (now - start) * (W - L - R), y = v => T + (hi - v) / (hi - lo) * (H - T - B);
  let d = `M${x(pts[0][0]).toFixed(1)},${y(pts[0][1]).toFixed(1)}`;
  for (let i = 1; i < pts.length; i++) d += ` H${x(pts[i][0]).toFixed(1)} V${y(pts[i][1]).toFixed(1)}`;
  d += ` H${x(now).toFixed(1)}`;
  const ticks = [0, 0.5, 1].map(f => lo + (hi - lo) * f);
  const fmtD = v => new Date(v).toLocaleDateString("en-PH", { month: "short", day: "numeric", year: "2-digit" });
  return `<div class="chart" role="img" aria-label="Rate per kg over time">
    <svg viewBox="0 0 ${W} ${H}">
      ${ticks.map(v => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="grid-l"/>
        <text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${Number(v).toFixed(2)}</text>`).join("")}
      <path d="${d}" class="line"/>
      ${pts.slice(1).map(p => `<circle cx="${x(p[0])}" cy="${y(p[1])}" r="3.5" class="chg"><title>${fmtD(p[0])}: ${rateFmt(p[1])}/kg</title></circle>`).join("")}
      ${dots.map(p => `<circle cx="${x(p[0])}" cy="${y(p[1])}" r="3" class="bill"><title>Billed ${fmtD(p[0])}: ${rateFmt(p[1])}/kg</title></circle>`).join("")}
      <text x="${L}" y="${H - 6}">${fmtD(start)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end">Today</text>
    </svg>
    <div class="legend small muted"><span><i class="k-line"></i>Rate in effect</span>${dots.length ? `<span><i class="k-bill"></i>Rate on your billings</span>` : ""}</div>
  </div>`;
}

async function viewRate(main) {
  const r = await api("/rates");
  const c = r.current, s = r.summary;
  const monthName = ym => ym ? new Date(ym + "-01T00:00").toLocaleDateString("en-PH", { month: "long", year: "numeric" }) : "";
  main.innerHTML = `
    <div class="grid">
      <div class="stat"><div class="l">Current Rate</div><div class="v">${c.rate ? rateFmt(c.rate) + `<span class="small muted">/kg</span>` : "—"}</div>
        <div class="s">${c.source === "ACCOUNT" ? "your account rate" : c.source === "STANDARD" ? "standard rate" : "not set yet"}${c.since ? " · since " + day(c.since) : ""}</div></div>
      <div class="stat"><div class="l">Last Change</div><div class="v" style="font-size:16px">${c.change ? moveBadge(c.change, c.change_pct) : "—"}</div>
        <div class="s">${c.change ? "from " + rateFmt(c.previous_rate) : "no change recorded"}</div></div>
      <div class="stat"><div class="l">12-Month Range</div><div class="v" style="font-size:16px">${s.low_12m ? `${rateFmt(s.low_12m)} – ${rateFmt(s.high_12m)}` : "—"}</div>
        <div class="s">${s.changes_12m} change(s) in 12 months</div></div>
      <div class="stat"><div class="l">Avg. Billed This Month</div><div class="v" style="font-size:16px">${rateFmt(s.avg_billed_this_month)}</div>
        <div class="s">${s.avg_billed_last_month ? `last month ${rateFmt(s.avg_billed_last_month)}` : "no billing last month"}</div></div>
    </div>
    ${c.rate ? "" : `<div class="msg err">Your rate per kg hasn't been set yet. Please contact GUFC.</div>`}
    <div class="card"><h2>Rate Movement</h2>
      ${r.movements.length ? rateChart(r.movements, r.billings) : `<p class="muted">No rate history yet.</p>`}
      ${r.movements.length ? `<div class="table-wrap" style="margin-top:12px"><table><thead><tr><th>Effective</th><th class="right">Old Rate</th><th class="right">New Rate</th><th>Movement</th></tr></thead><tbody>
        ${r.movements.map(m => `<tr><td>${day(m.date)}</td><td class="right">${m.old_rate ? rateFmt(m.old_rate) : "—"}</td>
          <td class="right"><b>${rateFmt(m.new_rate)}</b></td>
          <td>${m.old_rate ? moveBadge(m.change, m.change_pct) : `<span class="badge b-blue">starting rate</span>`}</td></tr>`).join("")}
      </tbody></table></div>` : ""}
      <p class="muted small" style="margin:10px 0 0">Rates are per kg of LPG${c.product_name ? ` (${esc(c.product_name)})` : ""}. A new rate applies to billings made from its effective date.</p></div>
    <div class="card"><h2>Rate on Your Billings</h2>
      ${r.billings.length ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Billing #</th><th class="right">kg</th><th class="right">Rate / kg</th><th class="right">Amount</th></tr></thead><tbody>
        ${r.billings.map(b => `<tr><td>${day(b.delivery_date)}</td><td>${esc(b.delivery_no)}${b.invoice_no ? `<br><span class="muted small">${esc(b.invoice_no)}</span>` : ""}</td>
          <td class="right">${Number(b.kg).toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
          <td class="right">${rateFmt(b.price_per_kg)}</td><td class="right">${money(b.billed_amount)}</td></tr>`).join("")}
      </tbody></table></div>` : `<p class="muted">No billings yet.</p>`}</div>`;
}

// ---------------------------------------------------------------- billing & SOA
async function viewBilling(main) {
  const b = await api("/billing");
  const open = b.invoices.filter(i => i.balance > 0.009), paid = b.invoices.filter(i => i.balance <= 0.009);
  const today = new Date(), first = new Date(today.getFullYear(), today.getMonth(), 1);
  const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const invTable = list => `<div class="table-wrap"><table><thead><tr><th>Invoice #</th><th>Date</th><th>Due</th><th class="right">Amount</th><th class="right">Paid</th><th class="right">Balance</th><th>Status</th></tr></thead><tbody>
    ${list.map(i => `<tr><td>${esc(i.invoice_no)}${i.is_opening_balance ? `<br><span class="muted small">previous balance</span>` : i.dr_no ? `<br><span class="muted small">DR ${esc(i.dr_no)}</span>` : ""}</td>
      <td>${esc(i.invoice_date)}</td><td>${esc(i.due_date)}</td><td class="right">${money(i.amount)}</td><td class="right">${money(i.paid)}</td>
      <td class="right"><b>${money(i.balance)}</b></td><td><span class="badge ${invBadge(i.status)}">${esc(i.status)}</span></td></tr>`).join("")}
  </tbody></table></div>`;
  main.innerHTML = `
    <div class="grid">
      <div class="stat"><div class="l">Balance Due</div><div class="v">${money(b.outstanding)}</div><div class="s">${b.open_count} open invoice(s)</div></div>
      <div class="stat ${b.overdue > 0 ? "danger" : ""}"><div class="l">Overdue</div><div class="v">${money(b.overdue)}</div></div>
      <div class="stat"><div class="l">Terms</div><div class="v" style="font-size:17px">${b.terms_days ? b.terms_days + " days" : "—"}</div>
        <div class="s">${b.next_due ? "next due " + esc(b.next_due.due_date) : ""}</div></div>
      <div class="stat"><div class="l">Credit Limit</div><div class="v" style="font-size:17px">${b.credit_limit ? money(b.credit_limit) : "—"}</div>
        <div class="s">${b.available_credit !== null ? "available " + money(b.available_credit) : ""}</div></div>
    </div>
    <div class="card"><h2>Unpaid Invoices</h2>${open.length ? invTable(open) : `<p class="muted">You have no unpaid invoices. Thank you!</p>`}</div>
    <div class="card"><h2>Statement of Account (SOA)</h2>
      <div class="filters"><label class="small muted" for="sFrom">From</label><input type="date" id="sFrom" value="${iso(first)}">
        <label class="small muted" for="sTo">to</label><input type="date" id="sTo" value="${iso(today)}">
        <button class="btn small" id="sRun">Show</button><a class="btn small green" id="sPdf" href="#">Download PDF</a></div>
      <div id="sOut"></div></div>
    <div class="card"><h2>Payments Received</h2>
      ${b.payments.length ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>For</th><th>Method</th><th>Reference</th><th class="right">Amount</th></tr></thead><tbody>
        ${b.payments.map(p => `<tr><td>${day(p.ts)}</td><td>${esc(p.invoice_no || p.sale_no)}</td><td>${esc(p.method)}</td><td>${esc(p.reference || "")}</td><td class="right">${money(p.amount)}</td></tr>`).join("")}
      </tbody></table></div>` : `<p class="muted">No payments recorded yet.</p>`}</div>
    ${paid.length ? `<div class="card"><details><summary>Paid invoices (${paid.length})</summary><div style="margin-top:10px">${invTable(paid)}</div></details></div>` : ""}`;
  const qs = () => {
    const q = new URLSearchParams(), f = document.getElementById("sFrom").value, t = document.getElementById("sTo").value;
    if (f) q.set("from", f); if (t) q.set("to", t); return q.toString();
  };
  async function soa() {
    document.getElementById("sPdf").href = "/api/portal/soa/export?" + qs();
    const d = await api("/soa?" + qs());
    document.getElementById("sOut").innerHTML = `
      <p class="small">Beginning balance: <b>${money(d.beginning_balance)}</b>${d.previous_arrears || d.balance_forward
        ? ` <span class="muted">(previous arrears ${money(d.previous_arrears)}${d.balance_forward ? `, balance forward ${money(d.balance_forward)}` : ""})</span>` : ""}</p>
      <div class="table-wrap"><table><thead><tr><th>Date</th><th>Type</th><th>Reference</th><th class="right">Charges</th><th class="right">Payments</th><th class="right">Balance</th></tr></thead><tbody>
        ${d.entries.length ? d.entries.map(e => `<tr><td>${day(e.date)}</td><td>${esc(e.type)}</td><td>${esc(e.ref)}</td>
          <td class="right">${e.debit ? money(e.debit) : ""}</td><td class="right">${e.credit ? money(e.credit) : ""}</td><td class="right">${money(e.balance)}</td></tr>`).join("")
          : `<tr><td colspan="6" class="muted">No transactions in this period.</td></tr>`}
      </tbody></table></div>
      <p style="font-weight:700;color:var(--green-dark)">Ending balance: ${money(d.ending_balance)}</p>`;
  }
  ["sFrom", "sTo"].forEach(id => document.getElementById(id).addEventListener("change", () => { document.getElementById("sPdf").href = "/api/portal/soa/export?" + qs(); }));
  document.getElementById("sRun").addEventListener("click", () => soa().catch(e => toast(e.message, "err")));
  await soa();
}

// ---------------------------------------------------------------- account
async function viewAccount(main) {
  const m = P.me;
  main.innerHTML = `<div class="card" style="max-width:620px"><h2>My Account</h2>
      <table><tbody>
        <tr><td class="muted">Account #</td><td>${esc(m.account_no)}</td></tr>
        <tr><td class="muted">Name</td><td>${esc(m.name)}</td></tr>
        ${m.business_name ? `<tr><td class="muted">Business</td><td>${esc(m.business_name)}</td></tr>` : ""}
        <tr><td class="muted">Phone</td><td>${esc(m.phone || "")}</td></tr>
        <tr><td class="muted">Address</td><td>${esc(m.address || "")}</td></tr>
        <tr><td class="muted">Payment terms</td><td>${m.terms_days ? m.terms_days + " days" : "Cash"}</td></tr>
        <tr><td class="muted">Username</td><td>${esc(m.username)}</td></tr>
      </tbody></table>
      <p class="muted small">To change your details, please contact GUFC.</p></div>
    <div class="card" style="max-width:620px"><h2>Change Password</h2>
      <div id="pwMsg" class="msg hidden"></div>
      <div class="field"><label for="pwCur">Current password</label><input type="password" id="pwCur" autocomplete="current-password"></div>
      <div class="field"><label for="pwNew">New password (at least 8 characters)</label><input type="password" id="pwNew" autocomplete="new-password"></div>
      <div class="field"><label for="pwNew2">Repeat new password</label><input type="password" id="pwNew2" autocomplete="new-password"></div>
      <button class="btn green" id="pwSave">Change Password</button></div>`;
  document.getElementById("pwSave").addEventListener("click", async () => {
    const msg = document.getElementById("pwMsg");
    const show = (t, ok) => { msg.textContent = t; msg.className = "msg " + (ok ? "ok" : "err"); };
    const n1 = document.getElementById("pwNew").value, n2 = document.getElementById("pwNew2").value;
    if (n1 !== n2) return show("The new passwords don't match.");
    try {
      await api("/change-password", { method: "POST", body: { current_password: document.getElementById("pwCur").value, new_password: n1 } });
      ["pwCur", "pwNew", "pwNew2"].forEach(id => document.getElementById(id).value = "");
      show("Password changed. Other devices have been logged out.", true);
    } catch (e) { show(e.message); }
  });
}

// ---------------------------------------------------------------- boot
(async () => {
  try { await start(); } catch (e) { if (e.message !== "logged out") showLogin(); }
})();
