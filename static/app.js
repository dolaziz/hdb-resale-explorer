/* sgp.fyi — HDB resale explorer (vanilla JS + Chart.js) */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  const money = (n, dp = 0) =>
    n == null || isNaN(n) ? "—" :
      "$" + Number(n).toLocaleString("en-SG", { minimumFractionDigits: dp, maximumFractionDigits: dp });
  const pctTxt = (v) => v == null ? "—" : (v > 0 ? "+" : "") + v.toFixed(1) + "%";
  const pctCls = (v) => v == null ? "" : v > 0.05 ? "up" : v < -0.05 ? "down" : "";
  const num = (n) => n == null ? "—" : Number(n).toLocaleString("en-SG");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const themeColors = () => ({
    ink: cssVar("--ink"), muted: cssVar("--muted"), line: cssVar("--line"),
    grid: cssVar("--rowsep"), accent: cssVar("--accent"), blue: cssVar("--blue"),
    tipBg: cssVar("--tip-bg"), tipInk: cssVar("--tip-ink"),
  });

  const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const shortMonth = (m) => {
    if (!m) return "";
    const [y, mo] = m.split("-");
    return `${MONTHS[+mo - 1]} '${y.slice(2)}`;
  };

  const monthLabel = (m) => {
    if (!m) return "";
    const [y, mo] = m.split("-");
    return new Date(+y, +mo - 1, 1).toLocaleString("en-SG", { month: "short", year: "numeric" });
  };

  let META = null;
  let CURRENT = null;
  let charts = {};
  let activeType = null;     // null = all flat types
  let txSort = { col: "month", dir: -1 };
  let txFilter = "ALL";

  /* ───────────────────────── search + routing ───────────────────────── */
  const input = $("#search");
  const suggestBox = $("#suggest");
  let items = [];
  let sel = -1;
  let timer = null;

  function refreshClear() {
    $("#clearbtn").hidden = !input.value;
  }

  function closeSuggest() {
    suggestBox.hidden = true;
    sel = -1;
  }

  function paintSuggest() {
    if (!items.length) { closeSuggest(); return; }
    suggestBox.innerHTML = items.map((b, i) => `
      <li role="option" data-i="${i}" aria-selected="${i === sel}">
        <span class="s-label">${esc(b.label)}</span>
        <span class="s-postal">${esc(b.postal || "")}</span>
        <span class="s-sub">${esc(b.town || "")} · ${num(b.n_tx)} tx</span>
      </li>`).join("");
    suggestBox.hidden = false;
  }

  async function doSuggest(q) {
    if (!q || q.trim().length < 2) { items = []; closeSuggest(); return; }
    try {
      const r = await fetch(`api/suggest?q=${encodeURIComponent(q)}`);
      const d = await r.json();
      items = d.results || [];
      sel = items.length ? 0 : -1;
      paintSuggest();
    } catch (e) { items = []; closeSuggest(); }
  }

  input.addEventListener("input", () => {
    refreshClear();
    clearTimeout(timer);
    const q = input.value;
    timer = setTimeout(() => doSuggest(q), 110);
  });

  input.addEventListener("keydown", (e) => {
    if (suggestBox.hidden) return;
    if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(sel + 1, items.length - 1); paintSuggest(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(sel - 1, 0); paintSuggest(); }
    else if (e.key === "Enter") {
      if (sel >= 0 && items[sel]) { e.preventDefault(); choose(items[sel]); }
      else if (items[0]) { e.preventDefault(); choose(items[0]); }
    } else if (e.key === "Escape") closeSuggest();
  });

  suggestBox.addEventListener("mousedown", (e) => {
    const li = e.target.closest("li[data-i]");
    if (!li) return;
    e.preventDefault();
    choose(items[+li.dataset.i]);
  });

  $("#clearbtn").addEventListener("click", () => {
    input.value = ""; refreshClear(); closeSuggest(); input.focus();
  });

  $$("#examples .chip").forEach((c) =>
    c.addEventListener("click", () => doSuggest(c.dataset.q)));

  function choose(b) {
    closeSuggest();
    input.value = "";
    refreshClear();
    location.hash = "#/b/" + encodeURIComponent(b.key);
  }

  window.addEventListener("hashchange", route);

  function route() {
    const h = decodeURIComponent(location.hash || "");
    const m = h.match(/^#\/b\/(.+)$/);
    if (!m) {
      $("#result").hidden = true;
      $("#hero").hidden = false;
      window.scrollTo({ top: 0 });
      return;
    }
    loadBlock(m[1]);
  }

  async function loadBlock(key) {
    $("#hero").hidden = true;
    $("#result").hidden = false;
    $("#result").classList.add("loading");
    try {
      const r = await fetch(`api/block?key=${encodeURIComponent(key)}`);
      if (!r.ok) throw new Error("not found");
      const d = await r.json();
      CURRENT = d;
      activeType = null;
      render(d);
      $("#result").classList.remove("loading");
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      $("#result").classList.remove("loading");
      $("#result").hidden = true;
      $("#hero").hidden = false;
      toast("Block not found — try the search box.");
    }
  }

  /* ───────────────────────── render ───────────────────────── */
  function render(d) {
    document.title = `${d.block} ${d.street} — HDB resale prices · sgp.fyi`;
    $("#addr").textContent = `Blk ${d.block} ${d.street}`;
    $("#townpill").textContent = d.town || "";

    const metaBits = [];
    if (d.postal) metaBits.push(`<span>Singapore <b>${esc(d.postal)}</b></span>`);
    if (d.lease_year) metaBits.push(`<span>Lease from <b>${d.lease_year}</b></span>`);
    if (d.remaining_lease != null) metaBits.push(`<span>≈ <b>${d.remaining_lease.toFixed(0)} yrs</b> remaining</span>`);
    metaBits.push(`<span><b>${num(d.overall.n_total)}</b> transactions</span>`);
    metaBits.push(`<span>${monthLabel(d.first_month)} – ${monthLabel(d.last_month)}</span>`);
    $("#blockmeta").innerHTML = metaBits.join("");

    renderTiles(d);
    renderTypeChips(d);
    renderCharts(d);
    renderTypeTable(d);
    renderNearby(d);
    renderTx(d);
    setupMap(d);
    $("#copybtn").onclick = () => {
      const url = location.href;
      navigator.clipboard?.writeText(url).then(() => toast("Link copied"), () => toast(url));
    };
    $("#maphbtn").onclick = () => {
      const w = $("#mapwrap");
      w.hidden = !w.hidden;
      if (!w.hidden) {
        paintTiles(CURRENT);
        w.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    };
  }

  function tile(label, value, note, cls = "") {
    return `<div class="tile"><div class="t-label">${label}</div>
      <div class="t-value ${cls}">${value}</div>
      <div class="t-note">${note || ""}</div></div>`;
  }

  function renderTiles(d) {
    const o = d.overall;
    const yoyPrice = o.yoy_price;
    const psfVsTown = (o.median_psf_12m && d.town_median_psf)
      ? (o.median_psf_12m - d.town_median_psf) / d.town_median_psf * 100 : null;
    $("#tiles").innerHTML = [
      tile("Median price <span style='text-transform:none'>· last 12m</span>",
        money(o.median_price_12m || o.median_price_prev),
        `${num(o.n_12m)} transactions in 12 months`),
      tile("12-month change", pctTxt(yoyPrice), "median price vs previous year", pctCls(yoyPrice)),
      tile("Median PSF", o.median_psf_12m ? money(o.median_psf_12m) : "—",
        psfVsTown == null ? "per square foot" :
          `<span class="${pctCls(psfVsTown)}">${pctTxt(psfVsTown)}</span> vs ${esc(titleCase(d.town))}`),
      tile("Latest transaction",
        o.last_tx ? money(o.last_tx.price) : "—",
        o.last_tx ? `${monthLabel(o.last_tx.month)} · ${esc(titleCase(o.last_tx.flat_type))} · floor ${esc(o.last_tx.storey_range.replace(" TO ", "–"))}` : ""),
    ].join("");
  }

  function renderTypeChips(d) {
    const types = (d.types || []).filter((t) => (d.series.by_type[t] || []).length >= 2);
    const box = $("#typechips");
    box.innerHTML = `<button data-t="" aria-pressed="${activeType === null}">All types</button>` +
      types.map((t) => `<button data-t="${esc(t)}" aria-pressed="${activeType === t}">${esc(titleCase(t))}</button>`).join("");
    $$("button", box).forEach((b) => b.addEventListener("click", () => {
      activeType = b.dataset.t || null;
      $$("button", box).forEach((x) => x.setAttribute("aria-pressed", x === b));
      renderCharts(d);
    }));
  }

  const chartOpts = (label) => {
    const C = themeColors();
    return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: (c) => `${c.dataset.label}: ${label(c.parsed.y)}`,
          afterLabel: (c) => {
            const n = c.dataset.meta?.[c.dataIndex];
            return n ? `${n} transactions` : "";
          },
        },
        backgroundColor: C.tipBg,
        titleColor: C.tipInk,
        bodyColor: C.tipInk,
        padding: 10,
        cornerRadius: 8,
        displayColors: false,
        titleFont: { size: 12 },
        bodyFont: { size: 12 },
      },
    },
    scales: {
      x: {
        grid: { display: false },
        ticks: { color: C.muted, maxRotation: 0, autoSkipPadding: 22, font: { size: 11 } },
        border: { color: C.line },
      },
      y: {
        grid: { color: C.grid },
        border: { display: false },
        ticks: {
          color: C.muted, font: { size: 11 },
          callback: (v) => (v >= 1000 ? "$" + Math.round(v / 1000) + "k" : label(v)),
        },
      },
    },
    elements: { point: { radius: 0, hoverRadius: 4 }, line: { tension: 0.25, borderWidth: 2 } },
    };
  };

  function seriesFor(d, field) {
    const pts = activeType ? (d.series.by_type[activeType] || []) : (d.series.all || []);
    return {
      labels: pts.map((p) => p.q.replace(" Q", " Q")),
      data: pts.map((p) => p[field]),
      meta: pts.map((p) => p.n),
    };
  }

  function renderCharts(d) {
    const mk = (id, field, color, labelFn) => {
      const s = seriesFor(d, field);
      const cfg = {
        type: "line",
        data: {
          labels: s.labels,
          datasets: [{
            label: field === "price" ? "Median price" : "Median PSF",
            data: s.data,
            meta: s.meta,
            borderColor: color,
            backgroundColor: color + "1f",
            fill: true,
          }],
        },
        options: chartOpts(labelFn),
      };
      if (charts[id]) { charts[id].data = cfg.data; charts[id].options = cfg.options; charts[id].update("none"); }
      else charts[id] = new Chart($("#" + id), cfg);
    };
    const C = themeColors();
    mk("pricechart", "price", C.accent, (v) => money(v));
    mk("psfchart", "psf", C.blue, (v) => money(v));

    const pts = activeType ? (d.series.by_type[activeType] || []) : (d.series.all || []);
    const lastPt = pts[pts.length - 1];
    if (lastPt) {
      $("#pricehint").innerHTML = `quarterly median · latest <b>${money(lastPt.price)}</b> (${esc(lastPt.q)})`;
      $("#psfhint").innerHTML = `quarterly median · latest <b>${money(lastPt.psf)} psf</b> (${esc(lastPt.q)})`;
    }
  }

  function renderTypeTable(d) {
    const rows = (d.type_stats || []).filter((r) => r.n >= 3);
    $("#typestats-hint").textContent = "medians, last 12 months";
    if (!rows.length) { $("#typetable").innerHTML = ""; return; }
    $("#typetable").innerHTML = `
      <thead><tr>
        <th class="c-type">Flat type</th><th class="c-n">All-time txns</th><th class="c-area">Median area</th>
        <th class="c-price">Median price</th><th class="c-psf">Median PSF</th>
        <th class="c-yoy">1-yr change</th><th class="c-range">Typical range</th>
      </tr></thead>
      <tbody>${rows.map((r) => {
        const area = r.median_area ? r.median_area.toFixed(0) + " m²" : "—";
        const range = `${money(r.p25_price)}–${money(r.p75_price)}`;
        return `
        <tr>
          <td class="strong c-type">${esc(titleCase(r.flat_type))}<span class="sub">${num(r.n)} txns · ${area} · ${range}</span></td>
          <td class="num muted c-n">${num(r.n)}</td>
          <td class="num c-area">${area}</td>
          <td class="num strong c-price">${money(r.median_price)}</td>
          <td class="num c-psf">${money(r.median_psf)}</td>
          <td class="num c-yoy ${pctCls(r.yoy_price)}">${pctTxt(r.yoy_price)}</td>
          <td class="num muted c-range">${range}</td>
        </tr>`;
      }).join("")}</tbody>`;
  }

  function renderNearby(d) {
    const rows = (d.nearby || []).slice(0, 25);
    const ref = d.nearby_ref || {};
    const hint = $("#nearhint");
    if (hint) {
      hint.textContent = ref.flat_type
        ? `nearest first · within 600 m · ${titleCase(ref.flat_type)} where available · median PSF, 18 months`
        : "nearest first · within 600 m · median PSF, last 18 months";
    }
    if (!rows.length) {
      $("#neartable").innerHTML = `<tbody><tr><td class="muted">No geocoded neighbours for this block.</td></tr></tbody>`;
      return;
    }
    $("#neartable").innerHTML = `
      <thead><tr>
        <th class="c-block">Block</th><th class="c-dist">Distance</th><th class="c-ftype">Flat type</th>
        <th class="c-txns">Txns</th><th class="c-price">Median price</th>
        <th class="c-psf">Median PSF</th><th class="c-vs">vs this block</th>
      </tr></thead>
      <tbody>${rows.map((r) => {
        const ftype = r.match ? esc(titleCase(r.flat_type)) : "mixed";
        return `
        <tr class="clickable" data-key="${esc(r.key)}">
          <td class="strong c-block">Blk ${esc(r.block)} ${esc(r.street)}${r.postal ? ` <span class="muted">· ${esc(r.postal)}</span>` : ""}<span class="sub">${r.dist_m} m · ${ftype} · ${num(r.n)} txns · ${money(r.median_price)}</span></td>
          <td class="num muted c-dist">${r.dist_m} m</td>
          <td class="muted c-ftype">${ftype}</td>
          <td class="num muted c-txns">${num(r.n)}</td>
          <td class="num c-price">${money(r.median_price)}</td>
          <td class="num strong c-psf">${money(r.median_psf)}</td>
          <td class="num ${pctCls(r.vs_this)} c-vs">${pctTxt(r.vs_this)}</td>
        </tr>`;
      }).join("")}</tbody>`;
    $$("#neartable tbody tr[data-key]").forEach((tr) =>
      tr.addEventListener("click", () => { location.hash = "#/b/" + encodeURIComponent(tr.dataset.key); }));
  }

  function renderTx(d) {
    const box = $("#txfilter");
    const types = ["ALL", ...new Set(d.transactions.map((t) => t.flat_type))];
    if (!types.includes(txFilter)) txFilter = "ALL";   // block may not have that type
    box.innerHTML = types.map((t) =>
      `<button type="button" data-t="${esc(t)}" aria-pressed="${t === txFilter}">${t === "ALL" ? "All" : esc(titleCase(t))}</button>`).join("");
    $$("button", box).forEach((b) => b.addEventListener("click", () => {
      txFilter = b.dataset.t;
      $$("button", box).forEach((x) => x.setAttribute("aria-pressed", x === b));
      drawTx(d);
    }));
    drawTx(d);
  }

  function drawTx(d) {
    const filter = txFilter;
    let rows = d.transactions.filter((t) => filter === "ALL" || t.flat_type === filter);
    const { col, dir } = txSort;
    rows = rows.slice().sort((a, b) => {
      const av = a[col], bv = b[col];
      if (av == null) return 1;
      if (bv == null) return -1;
      return (av > bv ? 1 : av < bv ? -1 : 0) * dir;
    });
    const shown = rows.slice(0, 300);
    const cols = [
      ["month", "Month", "c-month"], ["flat_type", "Flat type", "c-type"],
      ["storey_range", "Storey", "c-storey"], ["area", "Area (m²)", "c-area"],
      ["model", "Model", "c-model"], ["price", "Price", "c-price"], ["psf", "$ PSF", "c-psf"],
    ];
    $("#txtable").innerHTML = `
      <thead><tr>${cols.map(([c, l, cls]) =>
        `<th data-c="${c}" class="${cls}" ${c === "model" || c === "flat_type" ? 'style="text-align:left"' : ""}>${l}${col === c ? (dir < 0 ? " ↓" : " ↑") : ""}</th>`).join("")}</tr></thead>
      <tbody>${shown.map((t) => {
        const storey = esc((t.storey_range || "").replace(" TO ", "–"));
        const area = t.area ? t.area.toFixed(0) : "—";
        const model = esc(t.model || "");
        return `
        <tr>
          <td class="num c-month">${shortMonth(t.month)}</td>
          <td class="c-type">${esc(titleCase(t.flat_type))}<span class="sub">${[model, storey, area + " m²", money(t.psf) + " psf"].filter(Boolean).join(" · ")}</span></td>
          <td class="num c-storey">${storey}</td>
          <td class="num c-area">${area}</td>
          <td class="muted c-model">${model}</td>
          <td class="num strong c-price">${money(t.price)}</td>
          <td class="num muted c-psf">${money(t.psf)}</td>
        </tr>`;
      }).join("")}</tbody>`;
    $("#txfoot").textContent =
      `${num(rows.length)} transactions${filter !== "ALL" ? " (" + titleCase(filter) + ")" : ""}` +
      (rows.length > shown.length ? ` · showing newest/most relevant ${shown.length} — narrow with the filter` : "") +
      (d.tx_truncated ? " · oldest rows trimmed at 600" : "");
    $$("#txtable thead th").forEach((th) => th.addEventListener("click", () => {
      const c = th.dataset.c;
      txSort = { col: c, dir: txSort.col === c ? -txSort.dir : (c === "month" ? -1 : 1) };
      drawTx(d);
    }));
  }

  /* Raster OSM tiles (no API key, no WebGL): 3x2 tiles, pin in the middle. */
  const TILE = 256;
  function latLonToWorld(lat, lon, z) {
    const n = 2 ** z;
    const x = (lon + 180) / 360 * n * TILE;
    const s = Math.sin(lat * Math.PI / 180);
    const y = (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * n * TILE;
    return { x, y };
  }

  function setupMap(d) {
    const w = $("#mapwrap");
    if (!d.lat || !d.lon) { w.hidden = true; $("#maphbtn").disabled = true; return; }
    $("#maphbtn").disabled = false;
    $("#maplink").href =
      `https://www.openstreetmap.org/?mlat=${d.lat}&mlon=${d.lon}#map=18/${d.lat}/${d.lon}`;
    paintTiles(d);
  }

  function paintTiles(d) {
    const box = $("#maptiles");
    const host = $("#maplink");
    if (!box || !host || host.clientWidth === 0) return;
    const z = 17;
    const { x, y } = latLonToWorld(d.lat, d.lon, z);
    const W = host.clientWidth, H = host.clientHeight || 300;
    const left = W / 2 - x, top = H / 2 - y;
    const x0 = Math.floor((x - W / 2) / TILE), x1 = Math.floor((x + W / 2) / TILE);
    const y0 = Math.floor((y - H / 2) / TILE), y1 = Math.floor((y + H / 2) / TILE);
    const n = 2 ** z;
    let html = "";
    for (let tx = x0; tx <= x1; tx++) {
      for (let ty = y0; ty <= y1; ty++) {
        const wrapped = ((tx % n) + n) % n;
        if (ty < 0 || ty >= n) continue;
        const px = Math.round(left + tx * TILE), py = Math.round(top + ty * TILE);
        html += `<img src="api/tile/${z}/${wrapped}/${ty}.png" ` +
          `style="left:${px}px;top:${py}px" alt="" loading="lazy" referrerpolicy="no-referrer">`;
      }
    }
    box.innerHTML = html;
  }

  function titleCase(s) {
    // "EXECUTIVE" -> "Executive", "MULTI-GENERATION" -> "Multi-Generation".
    // Do NOT substring-replace "Exec": it already contains it.
    return String(s || "").toLowerCase().replace(/\b([a-z])/g, (m) => m.toUpperCase());
  }

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    if (!CURRENT || $("#mapwrap").hidden) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => paintTiles(CURRENT), 200);
  });

  let toastTimer = null;
  function toast(msg) {
    let el = $(".toast");
    if (!el) { el = document.createElement("div"); el.className = "toast"; document.body.appendChild(el); }
    el.textContent = msg;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.remove(), 2200);
  }

  /* ───────────────────────── theme ───────────────────────── */
  function applyTheme(theme, persist = true) {
    document.documentElement.setAttribute("data-theme", theme);
    if (persist) { try { localStorage.setItem("sgpfyi-theme", theme); } catch (e) {} }
    // keep the phone's status/address bar in step with the page
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "dark" ? "#000000" : "#ffffff");
    const icon = document.getElementById("themeicon");
    const btn = document.getElementById("themetoggle");
    if (icon) icon.textContent = theme === "dark" ? "☀️" : "🌙";
    if (btn) {
      btn.setAttribute("aria-label", theme === "dark" ? "Switch to light mode" : "Switch to dark mode");
      btn.title = theme === "dark" ? "Light mode" : "Dark mode";
    }
    if (CURRENT) {
      Object.values(charts).forEach((c) => { try { c.destroy(); } catch (e) {} });
      charts = {};
      renderCharts(CURRENT);
    }
  }

  function initTheme() {
    const cur = document.documentElement.getAttribute("data-theme") || "light";
    applyTheme(cur, false);
    const btn = document.getElementById("themetoggle");
    if (btn) btn.addEventListener("click", () => {
      const dark = document.documentElement.getAttribute("data-theme") === "dark";
      applyTheme(dark ? "light" : "dark");
    });
  }

  /* ───────────────────────── boot ───────────────────────── */
  (async function boot() {
    initTheme();
    try {
      const r = await fetch("api/meta");
      META = await r.json();
      const range = `${monthLabel(META.first_month)} – ${monthLabel(META.last_month)}`;
      $(".topmeta").textContent = `HDB resale prices · ${range}`;
      $("#datastamp").textContent =
        `${num(META.transactions)} transactions across ${num(META.blocks)} blocks · data to ${monthLabel(META.last_month)}`;
    } catch (e) { /* ignore */ }
    route();
  })();
})();
