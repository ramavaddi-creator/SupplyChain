const API = "";

function getCity() {
  return localStorage.getItem("hpi_city") || "Hyderabad";
}
function setCity(city) {
  localStorage.setItem("hpi_city", city);
}

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Request failed: ${url}`);
  return res.json();
}

function fmtPct(val, decimals = 1) {
  if (val === null || val === undefined) return "—";
  const sign = val > 0 ? "+" : "";
  return `${sign}${val.toFixed(decimals)}%`;
}

function pctClass(val) {
  if (val === null || val === undefined) return "flat";
  return val > 0.05 ? "up" : val < -0.05 ? "down" : "flat";
}

function arrow(val) {
  if (val === null || val === undefined) return "";
  return val > 0.05 ? "↑" : val < -0.05 ? "↓" : "→";
}

function fmtMoney(val) {
  if (val === null || val === undefined) return "—";
  return `₹${val.toFixed(2)}`;
}

function lineChartSVG(series, opts = {}) {
  const width = opts.width || 900;
  const height = opts.height || 220;
  const color = opts.color || "#1A3C40";
  const padL = 54, padR = 16, padT = 16, padB = 28;
  if (!series || series.length < 2) return `<p class="footnote">Not enough history to chart.</p>`;

  const prices = series.map(d => d.price);
  const min = Math.min(...prices), max = Math.max(...prices);
  const range = (max - min) || 1;
  const plotW = width - padL - padR, plotH = height - padT - padB;
  const step = plotW / (series.length - 1);

  const xy = (i, v) => {
    const x = padL + i * step;
    const y = padT + plotH - ((v - min) / range) * plotH;
    return [x, y];
  };

  const linePoints = series.map((d, i) => xy(i, d.price).join(",")).join(" ");
  const areaPoints = `${padL},${padT + plotH} ${linePoints} ${padL + (series.length - 1) * step},${padT + plotH}`;

  // y gridlines: min, mid, max
  const yTicks = [min, (min + max) / 2, max];
  const gridlines = yTicks.map(v => {
    const [, y] = xy(0, v);
    return `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${width - padR}" y2="${y.toFixed(1)}" stroke="#E3E1D9" stroke-width="1"/>
            <text x="${padL - 8}" y="${(y + 4).toFixed(1)}" font-size="10" text-anchor="end" fill="#8a8c8a" font-family="JetBrains Mono, monospace">₹${v.toFixed(0)}</text>`;
  }).join("");

  // x tick labels: ~6 evenly spaced dates
  const nTicks = Math.min(6, series.length);
  const tickIdxs = Array.from({length: nTicks}, (_, k) => Math.round(k * (series.length - 1) / (nTicks - 1)));
  const xTicks = tickIdxs.map(i => {
    const [x] = xy(i, series[i].price);
    const d = new Date(series[i].date);
    const label = d.toLocaleDateString('en-IN', { month: 'short', year: '2-digit' });
    return `<text x="${x.toFixed(1)}" y="${height - 6}" font-size="10" text-anchor="middle" fill="#8a8c8a" font-family="JetBrains Mono, monospace">${label}</text>`;
  }).join("");

  // sparse hover markers (roughly weekly) with native tooltips
  const markerEvery = Math.max(1, Math.round(series.length / 60));
  const markers = series.map((d, i) => {
    if (i % markerEvery !== 0 && i !== series.length - 1) return "";
    const [x, y] = xy(i, d.price);
    return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${i === series.length - 1 ? 3.5 : 2}" fill="${i === series.length - 1 ? color : 'white'}" stroke="${color}" stroke-width="1.3">
              <title>${d.date}: ₹${d.price.toFixed(2)}</title>
            </circle>`;
  }).join("");

  const gradId = `grad-${Math.random().toString(36).slice(2, 9)}`;
  return `<svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}">
    <defs>
      <linearGradient id="${gradId}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${color}" stop-opacity="0.18"/>
        <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
      </linearGradient>
    </defs>
    ${gridlines}
    <polygon points="${areaPoints}" fill="url(#${gradId})" stroke="none"/>
    <polyline points="${linePoints}" fill="none" stroke="${color}" stroke-width="2"/>
    ${markers}
    ${xTicks}
  </svg>`;
}

function sparklineSVG(values, width = 560, height = 140, color = "#1A3C40") {
  // legacy simple sparkline (values only, no dates) -- kept for callers that
  // don't have per-point dates available.
  if (!values || values.length < 2) return `<p class="footnote">Not enough history to chart.</p>`;
  const min = Math.min(...values), max = Math.max(...values);
  const range = (max - min) || 1;
  const pad = 6;
  const step = (width - pad * 2) / (values.length - 1);
  const points = values.map((v, i) => {
    const x = pad + i * step;
    const y = height - pad - ((v - min) / range) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return `<svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}">
    <polyline points="${points}" fill="none" stroke="${color}" stroke-width="2"/>
  </svg>`;
}

function barChartSVG(items, opts = {}) {
  // items: [{label, value}] where value is a signed % deviation
  const width = opts.width || 700, height = opts.height || 160;
  const padL = 8, padR = 8, padT = 10, padB = 20;
  if (!items || !items.length) return `<p class="footnote">No data.</p>`;
  const vals = items.map(i => i.value);
  const maxAbs = Math.max(...vals.map(v => Math.abs(v)), 1);
  const plotW = width - padL - padR, plotH = height - padT - padB;
  const barW = plotW / items.length * 0.6;
  const gap = plotW / items.length;
  const zeroY = padT + plotH / 2;

  const bars = items.map((it, i) => {
    const x = padL + i * gap + (gap - barW) / 2;
    const barH = (Math.abs(it.value) / maxAbs) * (plotH / 2 - 4);
    const y = it.value >= 0 ? zeroY - barH : zeroY;
    const color = it.value >= 0 ? "#B23A2E" : "#2E7D52";
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${barH.toFixed(1)}" fill="${color}" opacity="0.85">
              <title>${it.label}: ${it.value > 0 ? '+' : ''}${it.value}%</title>
            </rect>
            <text x="${(x + barW/2).toFixed(1)}" y="${height - 4}" font-size="10" text-anchor="middle" fill="#8a8c8a" font-family="JetBrains Mono, monospace">${it.label}</text>`;
  }).join("");

  return `<svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}">
    <line x1="${padL}" y1="${zeroY}" x2="${width-padR}" y2="${zeroY}" stroke="#E3E1D9" stroke-width="1"/>
    ${bars}
  </svg>`;
}

async function initTopNav(activePage) {
  const el = document.getElementById("topnav");
  el.innerHTML = `
    <a class="brand" href="/">Sovereign Intelligence</a>
    <nav>
      <a href="/" class="${activePage === 'executive' ? 'active' : ''}">Executive</a>
      <a href="/commodity.html" class="${activePage === 'commodity' ? 'active' : ''}">Commodity</a>
      <a href="/consumption.html" class="${activePage === 'consumption' ? 'active' : ''}">Consumption</a>
      <a href="/intelligence.html" class="${activePage === 'intelligence' ? 'active' : ''}">Intelligence</a>
      <a href="/report.html" class="${activePage === 'report' ? 'active' : ''}">Weekly Report</a>
      <a href="/sources.html" class="${activePage === 'sources' ? 'active' : ''}">Sources</a>
    </nav>
    <select class="city-select" id="citySelect"></select>
  `;
  const cities = await fetchJSON("/api/cities");
  const sel = document.getElementById("citySelect");
  sel.innerHTML = cities.map(c => `<option value="${c.city}">${c.city}${c.tier === 'telangana_regional' ? ' (TS)' : ''}</option>`).join("");
  sel.value = getCity();
  sel.addEventListener("change", () => {
    setCity(sel.value);
    window.location.reload();
  });
}
