/* Bet Evaluator — in-browser model (mirrors bet_evaluator.py) + UI. */

let DATA = null;
const state = { gameIdx: 0, market: "ml", side: "away", line: null, ou: "over" };

// ── Math (parity with Python) ────────────────────────────────────────────────
const clip = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
function erf(x) {
  const s = x < 0 ? -1 : 1; x = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * x);
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
  return s * y;
}
const normalCdf = (x) => 0.5 * (1 + erf(x / Math.SQRT2));
const amToImplied = (o) => (o > 0 ? 100 / (o + 100) : -o / (-o + 100));
const amToDecimal = (o) => (o > 0 ? 1 + o / 100 : 1 + 100 / -o);
function probToAm(p) {
  p = clip(p, 1e-4, 1 - 1e-4);
  return p >= 0.5 ? -Math.round(100 * p / (1 - p)) : Math.round(100 * (1 - p) / p);
}
const fmt = (v, n = 2) => (v == null ? "n/a" : Number(v).toFixed(n));

// ── Model ─────────────────────────────────────────────────────────────────────
function regress(factor) { return 1 + (factor - 1) * (1 - DATA.model.REGRESSION_TO_MEAN); }
function offenseFactor(osi) {
  if (osi == null) return 1.0;
  const [lo, hi] = DATA.model.OFF_FACTOR_CLIP;
  return regress(clip(1 + (osi - 50) / 100 * DATA.model.OSI_RUN_SENSITIVITY, lo, hi));
}
function pitchFactor(fip) {
  if (fip == null) return 1.0;
  const [lo, hi] = DATA.model.PITCH_FACTOR_CLIP;
  const sp = clip(fip / DATA.model.LEAGUE_FIP, lo, hi);
  return regress(DATA.model.SP_FIP_WEIGHT * sp + (1 - DATA.model.SP_FIP_WEIGHT));
}
function probabilities(g) {
  const a = DATA.anchors, L = a.league_runs;
  let expAway = L * offenseFactor(g.away_osi) * pitchFactor(g.home_fip) * g.park_factor;
  let expHome = L * offenseFactor(g.home_osi) * pitchFactor(g.away_fip) * g.park_factor + DATA.model.HFA_RUNS;
  const expTotal = expAway + expHome, expMargin = expHome - expAway;
  const pHomeModel = normalCdf(expMargin / a.margin_sd);
  const base = a.home_winp / (a.home_winp + a.away_winp);
  const pHome = clip(0.85 * pHomeModel + 0.15 * base, 0.05, 0.95);
  return { expAway, expHome, expTotal, expMargin, pHome, pAway: 1 - pHome };
}
function marketProb(g, p) {
  const a = DATA.anchors, m = state.market;
  if (m === "ml") {
    const team = sideTeam(g);
    return team === g.home ? [p.pHome, `${g.home} ML`] : [p.pAway, `${g.away} ML`];
  }
  if (m === "total") {
    const pOver = 1 - normalCdf((state.line - p.expTotal) / a.total_sd);
    return state.side === "over" ? [pOver, `Over ${state.line}`] : [1 - pOver, `Under ${state.line}`];
  }
  if (m === "team_total") {
    const team = sideTeam(g);
    const exp = team === g.home ? p.expHome : p.expAway;
    const pOver = 1 - normalCdf((state.line - exp) / a.team_sd);
    return state.ou === "over" ? [pOver, `${team} TT Over ${state.line}`] : [1 - pOver, `${team} TT Under ${state.line}`];
  }
  // runline
  const team = sideTeam(g);
  const teamMargin = team === g.home ? p.expMargin : -p.expMargin;
  const pCover = 1 - normalCdf((-state.line - teamMargin) / a.margin_sd);
  const sign = state.line > 0 ? `+${state.line}` : `${state.line}`;
  return [pCover, `${team} ${sign}`];
}
function valueLayer(modelP, odds) {
  const implied = amToImplied(odds), dec = amToDecimal(odds);
  const edge = modelP - implied;
  const ev = modelP * (dec - 1) - (1 - modelP);
  const b = dec - 1, kelly = b > 0 ? (b * modelP - (1 - modelP)) / b : 0;
  let tier = "Pass", units = "0u";
  for (const [emin, lab, u] of DATA.model.CONFIDENCE_TIERS) { if (edge >= emin) { tier = lab; units = u; break; } }
  let verdict, implausible = edge >= DATA.model.IMPLAUSIBLE_EDGE;
  if (implausible) { tier = "Review"; units = "verify inputs"; verdict = "REVIEW"; }
  else if (edge >= 0.02 && ev > 0) verdict = "PLAY";
  else verdict = "PASS";
  return { implied, edge, ev, fair: probToAm(modelP), kelly: Math.max(0, kelly / 4), tier, units, implausible, verdict };
}

// ── Helpers ────────────────────────────────────────────────────────────────
const game = () => DATA.games[state.gameIdx];
function sideTeam(g) { return (state.side === "home" || state.side === g.home) ? g.home : g.away; }
function sideKey(g) {
  if (state.market === "total") return state.side;
  if (state.market === "team_total") return `${sideTeam(g)}_${state.ou}`;
  return sideTeam(g);
}
function bestPrice(g) {
  const key = sideKey(g).toUpperCase();
  let rows = (g.odds || []).filter(o => o.market === state.market && String(o.side).toUpperCase() === key);
  if (state.market !== "ml" && state.line != null)
    rows = rows.filter(o => o.line !== "" && Math.abs(parseFloat(o.line) - state.line) < 1e-6);
  rows = rows.map(o => ({ ...o, n: parseFloat(o.odds) })).filter(o => !isNaN(o.n));
  if (!rows.length) return null;
  const best = rows.reduce((a, b) => (b.n > a.n ? b : a));
  return { odds: best.n, book: best.book, nBooks: new Set(rows.map(r => r.book)).size };
}
function metricColor(edgePts) { // edge in points (model - implied) * 100
  if (edgePts >= 8) return "var(--metric-elite)";
  if (edgePts >= 4.5) return "var(--metric-strong)";
  if (edgePts >= 2) return "var(--metric-above)";
  if (edgePts >= -2) return "var(--metric-neutral)";
  if (edgePts >= -6) return "var(--metric-weak)";
  return "var(--metric-very-weak)";
}

// ── Risk layer (mirrors Python) ──────────────────────────────────────────────
function riskLayer(g) {
  const out = [], a = DATA.anchors;
  [[g.home, g.home_hr9, g.home_k, g.home_sp], [g.away, g.away_hr9, g.away_k, g.away_sp]].forEach(([t, hr9, k, sp]) => {
    if (hr9 != null && hr9 >= 1.5) out.push(`${sp} (${t}) is HR-prone (HR/9 ${fmt(hr9)}) — crooked-number / boom-bust risk.`);
    if (k != null && k <= 18) out.push(`${sp} (${t}) is a low-K arm (K% ${fmt(k, 1)}%) — contact-dependent, BABIP variance.`);
  });
  [[g.away, g.away_risk], [g.home, g.home_risk]].forEach(([t, r]) => {
    if (!r) return;
    if (r.bullpen_era != null && r.bullpen_era >= 4.5) out.push(`${t} bullpen shaky (pen ERA ${fmt(r.bullpen_era)}) — late-inning leverage risk.`);
    if (r.window_direction === "rising") out.push(`${t} offense trending up (window=rising).`);
    if (r.window_direction === "falling") out.push(`${t} offense trending down (window=falling).`);
  });
  const w = g.weather || {};
  if (w.dome) out.push("Dome / roof — weather neutral.");
  else {
    if (w.wind_mph != null && w.wind_mph >= 12) out.push(`Wind ${fmt(w.wind_mph, 0)} mph ${w.wind_dir || ""} — ball-flight / total swing factor.`);
    if (w.temp_f != null && w.temp_f <= 50) out.push(`Cold (${fmt(w.temp_f, 0)}F) — suppresses offense, leans Under.`);
  }
  out.push(`League blown-save ~${fmt(a.blown_save * 100, 0)}% and run-margin SD ~${fmt(a.margin_sd, 1)} — single-game variance is high regardless of edge.`);
  return out;
}

// ── Render ────────────────────────────────────────────────────────────────────
function recompute() {
  const g = game();
  const bp = bestPrice(g);
  const oddsEl = document.getElementById("odds");
  const hint = document.getElementById("odds-hint");
  if (bp && oddsEl.dataset.auto !== "off") { oddsEl.value = bp.odds; }
  if (bp) hint.innerHTML = `Best <b>${bp.odds >= 0 ? "+" : ""}${bp.odds}</b> @ ${bp.book} across ${bp.nBooks} book(s). Override any time.`;
  else hint.textContent = "No scraped price for this bet — enter the odds you're getting.";

  const odds = parseInt(oddsEl.value, 10);
  const results = document.getElementById("results");
  if (isNaN(odds)) { results.innerHTML = boardMsg("Enter odds to evaluate this bet."); return; }
  if ((state.market !== "ml") && (state.line == null || isNaN(state.line))) {
    results.innerHTML = boardMsg("Enter a line for this market."); return;
  }

  const p = probabilities(g);
  let [modelP, pick] = marketProb(g, p);
  modelP = clip(modelP, 0.02, 0.98);
  const v = valueLayer(modelP, odds);
  results.innerHTML = renderResults(g, p, modelP, pick, odds, v, bp);
}

const boardMsg = (m) => `<div class="board"><div class="board-pad muted">${m}</div></div>`;

function renderResults(g, p, modelP, pick, odds, v, bp) {
  const edgePts = v.edge * 100, col = metricColor(edgePts);
  const vcl = v.verdict === "PLAY" ? "play" : v.verdict === "REVIEW" ? "review" : "pass";
  const oddsStr = (odds >= 0 ? "+" : "") + odds;

  const sigs = [];
  ["away", "home"].forEach(s => (g.signals[s] || []).forEach(x => {
    const t = s === "away" ? g.away : g.home;
    sigs.push(`<li><span class="lab">${t} · ${x.name}</span> (${x.direction}) — ${x.verdict}</li>`);
  }));
  const convs = [];
  ["away", "home"].forEach(s => { const c = g.convergence[s]; if (c) {
    const t = s === "away" ? g.away : g.home;
    convs.push(`<li><span class="lab">${t}</span>: ${c.fired || 0} fired, weighted ${fmt(c.count, 0)}, dir <i>${c.direction}</i>${c.is_play ? '<span class="tag-conv">convergence</span>' : ''}</li>`);
  }});
  const risks = riskLayer(g).map(r => `<li>${r}</li>`).join("");

  const w = g.weather || {};
  const wx = w.dome ? "Dome" : (w.temp_f != null ? `${fmt(w.temp_f, 0)}F, wind ${fmt(w.wind_mph, 0)} mph ${w.wind_dir || ""}` : "n/a");
  const mkt = bp
    ? `<div class="kv"><span class="k">Best available</span><span class="v">${oddsStr} @ ${bp.book} <span class="muted">(${bp.nBooks} books)</span></span></div>`
    : `<div class="kv"><span class="k">Price</span><span class="v">${oddsStr} <span class="muted">(manual)</span></span></div>`;

  return `
  <div class="verdict">
    <span class="pill ${vcl}">${v.verdict}</span>
    <span class="pick">${pick} @ ${oddsStr}<small>${g.away} @ ${g.home}</small></span>
  </div>

  <div class="stat-strip">
    <div class="stat"><div class="k">Model</div><div class="v">${(modelP*100).toFixed(1)}%</div></div>
    <div class="stat"><div class="k">Implied</div><div class="v">${(v.implied*100).toFixed(1)}%</div></div>
    <div class="stat"><div class="k">Edge</div><div class="v"><span class="chip" style="background:${col}">${edgePts>=0?"+":""}${edgePts.toFixed(1)}</span></div></div>
    <div class="stat"><div class="k">EV / unit</div><div class="v">${v.ev>=0?"+":""}${v.ev.toFixed(3)}</div></div>
    <div class="stat"><div class="k">Size</div><div class="v" style="font-size:1rem">${v.tier}<br><span class="muted" style="font-size:.78rem">${v.units}</span></div></div>
  </div>

  <div class="board"><div class="board-head"><span class="ico">⌑</span><h2>Matchup</h2></div><div class="board-pad">
    <p class="matchup-line"><b>${g.away}</b> — ${g.away_sp} (${g.away_hand}HP, FIP ${fmt(g.away_fip)}) · lineup OSI <b>${fmt(g.away_osi,1)}</b></p>
    <p class="matchup-line"><b>${g.home}</b> — ${g.home_sp} (${g.home_hand}HP, FIP ${fmt(g.home_fip)}) · lineup OSI <b>${fmt(g.home_osi,1)}</b></p>
    <p class="matchup-line muted">Park factor ${g.park_factor} · ${wx} · Fair odds ${v.fair>=0?"+":""}${v.fair} · ¼-Kelly ${(v.kelly*100).toFixed(1)}%</p>
  </div></div>

  <div class="board"><div class="board-head"><span class="ico">▦</span><h2>Statistical Probability</h2></div><div class="board-pad">
    <table class="prob">
      <tr><th></th><th>${g.away}</th><th>${g.home}</th></tr>
      <tr><td class="l">Expected runs</td><td>${p.expAway.toFixed(2)}</td><td>${p.expHome.toFixed(2)}</td></tr>
      <tr><td class="l">Win probability</td><td>${(p.pAway*100).toFixed(1)}%</td><td>${(p.pHome*100).toFixed(1)}%</td></tr>
    </table>
    <div class="kv" style="margin-top:8px"><span class="k">Expected total</span><span class="v">${p.expTotal.toFixed(2)}</span></div>
    <div class="kv"><span class="k">Expected margin (home−away)</span><span class="v">${p.expMargin>=0?"+":""}${p.expMargin.toFixed(2)}</span></div>
  </div></div>

  <div class="board"><div class="board-head"><span class="ico">◷</span><h2>Market</h2></div><div class="board-pad">${mkt}</div></div>

  <div class="board"><div class="board-head"><span class="ico">⚡</span><h2>Signals Fired</h2></div><div class="board-pad">
    <ul class="feed">${sigs.join("") || '<li class="muted">No signals fired for this game.</li>'}</ul>
    ${convs.length ? `<div style="margin-top:10px"><ul class="feed">${convs.join("")}</ul></div>` : ""}
  </div></div>

  <div class="board"><div class="board-head"><span class="ico">⚠</span><h2>Risk &amp; Variance</h2></div><div class="board-pad">
    <ul class="feed risk">${risks}</ul>
  </div></div>`;
}

// ── Controls ────────────────────────────────────────────────────────────────
function buildSideSeg() {
  const g = game(), seg = document.getElementById("side-seg");
  let opts;
  if (state.market === "total") opts = [["over", "Over"], ["under", "Under"]];
  else opts = [["away", g.away], ["home", g.home]];
  // keep a valid side
  if (!opts.some(o => o[0] === state.side)) state.side = opts[0][0];
  seg.innerHTML = opts.map(([v, lab]) => `<button data-s="${v}" class="${v === state.side ? "active" : ""}">${lab}</button>`).join("");
  seg.querySelectorAll("button").forEach(b => b.onclick = () => { state.side = b.dataset.s; setActive(seg, b); afterControlChange(); });

  document.getElementById("line-field").hidden = (state.market === "ml");
  document.getElementById("ou-field").hidden = (state.market !== "team_total");
  if (state.market === "runline" && (state.line == null || isNaN(state.line))) { state.line = -1.5; document.getElementById("line").value = -1.5; }
}
function setActive(seg, btn) { seg.querySelectorAll("button").forEach(b => b.classList.toggle("active", b === btn)); }
function afterControlChange() { document.getElementById("odds").dataset.auto = "on"; recompute(); }

function initControls() {
  const gameSel = document.getElementById("game");
  gameSel.innerHTML = DATA.games.map((g, i) => `<option value="${i}">${g.away} @ ${g.home}${(g.odds && g.odds.length) ? "" : "  (no live odds)"}</option>`).join("");
  gameSel.onchange = () => { state.gameIdx = +gameSel.value; buildSideSeg(); afterControlChange(); };

  const mseg = document.getElementById("market-seg");
  mseg.querySelectorAll("button").forEach(b => b.onclick = () => {
    state.market = b.dataset.m; setActive(mseg, b);
    if (state.market === "total") state.side = "over";
    buildSideSeg(); afterControlChange();
  });

  document.getElementById("ou-seg").querySelectorAll("button").forEach(b => b.onclick = () => {
    state.ou = b.dataset.ou; setActive(document.getElementById("ou-seg"), b); afterControlChange();
  });
  document.getElementById("line").oninput = (e) => { state.line = parseFloat(e.target.value); recompute(); };
  document.getElementById("odds").oninput = (e) => { e.target.dataset.auto = "off"; recompute(); };

  buildSideSeg();
  recompute();
}

fetch("data/site.json").then(r => r.json()).then(d => {
  DATA = d;
  document.getElementById("data-date").textContent = "Data: " + d.generated;
  document.getElementById("data-games").textContent = d.games.length + " games";
  initControls();
}).catch(e => {
  document.getElementById("results").innerHTML = boardMsg("Could not load data/site.json. Run: python export_web_data.py");
});
