const state = {
  currency: localStorage.getItem("currency") || "USD",
  games: [],
  inventoryItems: [],
  inventoryAppid: null,
  priceCache: {},
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function symbolFor(currency) {
  return currency === "ILS" ? "₪" : "$";
}

function fmtMoney(v) {
  if (v === null || v === undefined) return "-";
  return `${symbolFor(state.currency)}${v.toFixed(2)}`;
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = `שגיאה (${res.status})`;
    try {
      const j = await res.json();
      if (j.detail) msg = j.detail;
    } catch (e) {}
    throw new Error(msg);
  }
  return res.json();
}

/* ---------- Tabs ---------- */
$$(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".tab-btn").forEach((b) => b.classList.remove("active"));
    $$(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`#tab-${btn.dataset.tab}`).classList.add("active");
  });
});

/* ---------- Currency ---------- */
$("#currencySelect").value = state.currency;
$("#currencySelect").addEventListener("change", (e) => {
  state.currency = e.target.value;
  localStorage.setItem("currency", state.currency);
  state.priceCache = {};
  renderInventoryTable();
});

/* ---------- Steam login status ---------- */
let loginPollTimer = null;

function renderLoginStatus(status) {
  const el = $("#loginStatus");
  const btn = $("#loginBtn");
  el.classList.remove("status-on", "status-off", "status-pending");

  if (status.in_progress) {
    el.textContent = "מתחבר... (השלם בחלון שנפתח)";
    el.classList.add("status-pending");
    btn.disabled = true;
  } else if (status.configured) {
    el.textContent = "מחובר ל-Steam - היסטוריה אמיתית";
    el.classList.add("status-on");
    btn.disabled = false;
    btn.textContent = "התחבר מחדש";
  } else {
    el.textContent = "לא מחובר - היסטוריה מקומית בלבד";
    el.classList.add("status-off");
    btn.disabled = false;
    btn.textContent = "התחבר ל-Steam";
  }
}

async function refreshLoginStatus() {
  try {
    const status = await api("/api/login/status");
    renderLoginStatus(status);
    return status;
  } catch (e) {
    return null;
  }
}

function pollLoginStatus() {
  if (loginPollTimer) clearInterval(loginPollTimer);
  let ticks = 0;
  loginPollTimer = setInterval(async () => {
    ticks++;
    const status = await refreshLoginStatus();
    if (!status || !status.in_progress || ticks > 60) { // ~3 min max (60 * 3s)
      clearInterval(loginPollTimer);
      loginPollTimer = null;
    }
  }, 3000);
}

$("#loginBtn").addEventListener("click", async () => {
  try {
    const res = await api("/api/login/start", { method: "POST" });
    if (res.started) {
      renderLoginStatus({ configured: false, in_progress: true });
      pollLoginStatus();
    }
  } catch (e) {
    alert(e.message);
  }
});

refreshLoginStatus();

/* ---------- Load games into both selects ---------- */
async function loadGames() {
  state.games = await api("/api/games");
  for (const sel of [$("#itemGameSelect"), $("#invGameSelect")]) {
    sel.innerHTML = "";
    for (const g of state.games) {
      const opt = document.createElement("option");
      opt.value = g.appid;
      opt.textContent = `${g.name} (${g.appid})`;
      opt.dataset.contextid = g.contextid;
      sel.appendChild(opt);
    }
  }
}
loadGames();

/* ---------- Item analysis ---------- */
function currentItemAppid() {
  const custom = $("#itemAppidCustom").value.trim();
  if (custom) return parseInt(custom, 10);
  return parseInt($("#itemGameSelect").value, 10);
}

function drawSparkline(canvas, points) {
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * dpr; canvas.height = h * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  if (!points.length) {
    ctx.fillStyle = "#93a4b8";
    ctx.font = "13px sans-serif";
    ctx.fillText("אין עדיין מספיק נתונים היסטוריים לגרף", 10, h / 2);
    return;
  }
  const prices = points.map((p) => p.price);
  const min = Math.min(...prices), max = Math.max(...prices);
  const pad = 10;
  const range = max - min || 1;
  ctx.beginPath();
  points.forEach((p, i) => {
    const x = pad + (i / Math.max(points.length - 1, 1)) * (w - pad * 2);
    const y = h - pad - ((p.price - min) / range) * (h - pad * 2);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = "#66c0f4";
  ctx.lineWidth = 2;
  ctx.stroke();
}

$("#analyzeBtn").addEventListener("click", async () => {
  const appid = currentItemAppid();
  const name = $("#itemNameInput").value.trim();
  const resultBox = $("#itemResult");
  if (!appid || !name) {
    alert("יש לבחור משחק ולהזין שם פריט");
    return;
  }
  resultBox.classList.remove("hidden");
  resultBox.innerHTML = "<p>טוען...</p>";
  try {
    const [predictData, histData] = await Promise.all([
      api(`/api/predict?appid=${appid}&name=${encodeURIComponent(name)}&currency=${state.currency}`),
      api(`/api/history?appid=${appid}&name=${encodeURIComponent(name)}&currency=${state.currency}`),
    ]);
    renderItemResult(predictData, histData);
  } catch (e) {
    resultBox.innerHTML = `<div class="error-box">${e.message}</div>`;
  }
});

function recBadgeClass(rec) {
  if (rec === "מכור") return "rec-sell";
  if (rec === "החזק") return "rec-hold";
  return "rec-wait";
}

function renderItemResult(p, hist) {
  const box = $("#itemResult");
  let html = "";
  if (p.status === "no_price") {
    html = `<div class="error-box">${p.message}</div>`;
    box.innerHTML = html;
    return;
  }

  const sourceTag = p.source === "official"
    ? `<span class="source-tag official">מקור: היסטוריה אמיתית מ-Steam</span>`
    : `<span class="source-tag local">מקור: היסטוריה מקומית שנצברה</span>`;

  html += `<div><span class="rec-badge ${recBadgeClass(p.recommendation)}">${p.recommendation}</span>`;
  html += `<strong style="font-size:20px">${fmtMoney(p.current_price)}</strong>${sourceTag}</div>`;

  if (p.factors && p.factors.length) {
    html += `<p class="factors-title">הגורמים ששקלנו:</p><ul class="factors-list">`;
    html += p.factors.map((f) => `<li>${f}</li>`).join("");
    html += `</ul>`;
  } else {
    html += `<p>${p.message}</p>`;
  }
  if (p.warnings && p.warnings.length) {
    html += `<p class="factors-note">⚠ ${p.warnings.join(" ")}</p>`;
  }

  if (p.status === "ok") {
    html += `<div class="stat-grid">
      <div class="stat-box"><div class="label">ממוצע נע</div><div class="value">${fmtMoney(p.moving_average)}</div></div>
      <div class="stat-box"><div class="label">סטיית תקן</div><div class="value">${fmtMoney(p.std_dev)}</div></div>
      <div class="stat-box"><div class="label">אחוזון מחיר</div><div class="value">${p.percentile_rank ?? "-"}</div></div>
      <div class="stat-box"><div class="label">מגמה ארוכה (יומי)</div><div class="value">${p.trend_pct_per_day}%</div></div>
      <div class="stat-box"><div class="label">מגמה קצרה (יומי)</div><div class="value">${p.short_trend_pct_per_day ?? "-"}${p.short_trend_pct_per_day !== null ? "%" : ""}</div></div>
      <div class="stat-box"><div class="label">מגמת נפח</div><div class="value">${p.volume_trend ?? "-"}</div></div>
      <div class="stat-box"><div class="label">תחזית ל-7 ימים</div><div class="value">${fmtMoney(p.predicted_price_7d)}</div></div>
      <div class="stat-box"><div class="label">נפח מסחר</div><div class="value">${p.volume ?? "-"}</div></div>
      <div class="stat-box"><div class="label">נקודות נתונים</div><div class="value">${p.data_points}</div></div>
      <div class="stat-box"><div class="label">רמת ביטחון</div><div class="value">${p.confidence}</div></div>
    </div>`;
  }

  html += `<canvas id="sparkline"></canvas>`;
  box.innerHTML = html;
  drawSparkline($("#sparkline"), hist.points || []);
}

/* ---------- Inventory ---------- */
function currentInvContextid() {
  const opt = $("#invGameSelect").selectedOptions[0];
  return opt ? parseInt(opt.dataset.contextid, 10) : 2;
}

$("#loadInventoryBtn").addEventListener("click", async () => {
  const steamid = $("#steamIdInput").value.trim();
  const appid = parseInt($("#invGameSelect").value, 10);
  const contextid = currentInvContextid();
  if (!steamid) { alert("יש להזין SteamID64"); return; }

  $("#loadInventoryBtn").disabled = true;
  $("#loadInventoryBtn").textContent = "טוען...";
  try {
    const data = await api(`/api/inventory/list?steamid=${encodeURIComponent(steamid)}&appid=${appid}&contextid=${contextid}`);
    state.inventoryItems = data.items;
    state.inventoryAppid = appid;
    state.priceCache = {};

    const typeSel = $("#typeFilter");
    typeSel.innerHTML = '<option value="">הכל</option>';
    for (const t of data.types) {
      const opt = document.createElement("option");
      opt.value = t; opt.textContent = t;
      typeSel.appendChild(opt);
    }

    $("#inventoryControls").classList.remove("hidden");
    $("#inventoryTableWrap").classList.remove("hidden");
    renderInventoryTable();
  } catch (e) {
    alert(e.message);
  } finally {
    $("#loadInventoryBtn").disabled = false;
    $("#loadInventoryBtn").textContent = "טען Inventory";
  }
});

$("#typeFilter").addEventListener("change", renderInventoryTable);
$("#searchFilter").addEventListener("input", renderInventoryTable);

function filteredItems() {
  const type = $("#typeFilter").value;
  const search = $("#searchFilter").value.trim().toLowerCase();
  return state.inventoryItems.filter((it) => {
    if (type && it.type !== type) return false;
    if (search && !it.name.toLowerCase().includes(search)) return false;
    return true;
  });
}

function renderInventoryTable() {
  const items = filteredItems();
  const tbody = $("#inventoryTable tbody");
  tbody.innerHTML = "";
  let total = 0;
  let anyPriced = false;

  for (const it of items) {
    const cached = state.priceCache[it.market_hash_name];
    const hasPrice = typeof cached?.price === "number";
    const price = hasPrice ? cached.price : null;
    const value = hasPrice ? price * it.qty : null;
    if (hasPrice) { total += value; anyPriced = true; }

    let priceCell;
    if (hasPrice) {
      priceCell = fmtMoney(price);
    } else if (!it.marketable) {
      priceCell = "לא ניתן למכירה";
    } else if (!cached) {
      priceCell = "לא נבדק";
    } else if (cached.error) {
      priceCell = "שגיאה בבדיקה";
    } else {
      priceCell = "אין מחיר בשוק";
    }

    const rec = hasPrice && cached.recommendation
      ? `<span class="rec-badge ${recBadgeClass(cached.recommendation)}">${cached.recommendation}</span>`
      : "-";

    const tr = document.createElement("tr");
    if (!hasPrice) tr.className = "na-row";
    tr.innerHTML = `
      <td>${it.icon_url ? `<img src="https://community.akamai.steamstatic.com/economy/image/${it.icon_url}">` : ""}</td>
      <td>${it.name}</td>
      <td>${it.type}</td>
      <td>${it.qty}</td>
      <td>${priceCell}</td>
      <td>${hasPrice ? fmtMoney(value) : "-"}</td>
      <td>${rec}</td>
      <td>${it.marketable ? `<button class="link-btn" data-name="${encodeURIComponent(it.market_hash_name)}">נתח</button>` : ""}</td>
    `;
    tbody.appendChild(tr);
  }

  $$(".link-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const name = decodeURIComponent(btn.dataset.name);
      $$(".tab-btn").forEach((b) => b.classList.remove("active"));
      $$(".tab-panel").forEach((p) => p.classList.remove("active"));
      $('.tab-btn[data-tab="item"]').classList.add("active");
      $("#tab-item").classList.add("active");
      $("#itemAppidCustom").value = state.inventoryAppid;
      $("#itemNameInput").value = name;
      $("#analyzeBtn").click();
    });
  });

  $("#inventoryTotal").textContent = anyPriced
    ? `שווי כולל לפריטים המוצגים: ${fmtMoney(total)} (${items.length} סוגי פריטים)`
    : `${items.length} סוגי פריטים מוצגים - לחץ "חשב שווי" כדי לקבל מחירים`;
}

$("#evaluateBtn").addEventListener("click", async () => {
  const items = filteredItems().filter((it) => it.marketable && !(it.market_hash_name in state.priceCache));
  if (!items.length) { renderInventoryTable(); return; }

  const statusEl = $("#evalStatus");
  $("#evaluateBtn").disabled = true;
  let done = 0;

  const names = items.map((it) => it.market_hash_name);
  statusEl.textContent = ` מעריך ${names.length} פריטים... ייתכן שיקח זמן עקב הגבלות קצב של Steam`;

  try {
    const results = await api("/api/inventory/evaluate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ appid: state.inventoryAppid, currency: state.currency, items: names }),
    });
    for (const [name, r] of Object.entries(results)) {
      state.priceCache[name] = r;
    }
    statusEl.textContent = "";
  } catch (e) {
    statusEl.textContent = ` שגיאה: ${e.message}`;
  } finally {
    $("#evaluateBtn").disabled = false;
    renderInventoryTable();
  }
});
