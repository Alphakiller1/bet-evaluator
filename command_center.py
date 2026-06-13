"""
Chase Command Center — one local visual app over the whole toolkit. Zero
dependencies (Python stdlib only). Wraps every tool and reads the Supabase
warehouse, so you interact with the system in a browser instead of the terminal.

    python command_center.py            # then open http://localhost:8787

Read-only analysis tools run on click (free). The fetch tools that spend Odds-API
credits (sharp / predict) are intentionally NOT auto-run here — run those from the
CLI when you want to refresh the market.
"""

from __future__ import annotations

import contextlib
import http.server
import io
import json
import socketserver
import urllib.parse
import webbrowser

PORT = 8787


# ── run a print-based tool and capture its text output ───────────────────────
def _capture(fn, *a, **k) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            fn(*a, **k)
        except SystemExit as e:
            print(e)
        except Exception as e:  # noqa
            print(f"(error: {e})")
    return buf.getvalue().strip() or "(no output)"


def value_text():
    from backtest import value_board
    return _capture(value_board.run)


def edge_text():
    from backtest import market_edge
    return _capture(market_edge.run)


def regression_text():
    import regression
    return _capture(regression.run)


def cross_text():
    from backtest import cross_venue
    return _capture(cross_venue.run)


def book_text():
    from backtest import book_intel
    return _capture(book_intel.run)


def game_text(g: str):
    import game_report
    away, home = (s.strip().upper() for s in g.split("@", 1))
    return _capture(game_report.run, away, home)


def sharp_rows():
    from backtest import db
    try:
        return db.select("sharp_signals",
                         "?select=market_type,selection,divergence,steam_flag,"
                         "sharp_novig_prob,soft_novig_prob&order=snapshot_time.desc&limit=25")
    except SystemExit:
        return []


def ecosystem():
    from backtest import db
    def c(t):
        try:
            return db.count(t)
        except Exception:
            return -1
    return {
        "games": c("games"), "outcomes": c("game_outcomes"),
        "odds": c("odds_snapshots"), "pm": c("prediction_market_snapshots"),
        "sharp_obs": c("sharp_observations"), "sharp_sig": c("sharp_signals"),
        "predictions": c("model_predictions"), "reports": c("daily_reports"),
    }


def _md_to_html(md: str) -> str:
    """Minimal markdown -> HTML for the bet-analysis note (headings, bold, tables,
    lists, blockquotes, rules). Stdlib only."""
    import html as _h
    import re

    def inline(s: str) -> str:
        s = _h.escape(s)
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)

    out, in_list, in_table = [], False, False
    for ln in md.split("\n"):
        if ln.startswith("|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):          # table separator row
                continue
            if not in_table:
                if in_list:
                    out.append("</ul>"); in_list = False
                out.append("<table>"); in_table = True
            out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>"); in_table = False
        if ln.startswith("### "):
            out.append(f"<h4>{inline(ln[4:])}</h4>")
        elif ln.startswith("## "):
            out.append(f"<h3>{inline(ln[3:])}</h3>")
        elif ln.startswith("# "):
            txt = ln[2:]
            cls = "play" if "PLAY" in txt else ("pass" if "PASS" in txt else "")
            out.append(f"<h2 class='vh {cls}'>{inline(txt)}</h2>")
        elif ln.startswith("> "):
            out.append(f"<blockquote>{inline(ln[2:])}</blockquote>")
        elif ln.startswith("- "):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{inline(ln[2:])}</li>")
        elif ln.strip() == "---":
            if in_list:
                out.append("</ul>"); in_list = False
            out.append("<hr>")
        elif ln.strip() == "":
            if in_list:
                out.append("</ul>"); in_list = False
        else:
            if in_list:
                out.append("</ul>"); in_list = False
            out.append(f"<p>{inline(ln)}</p>")
    if in_list:
        out.append("</ul>")
    if in_table:
        out.append("</table>")
    return "\n".join(out)


def bet_eval_payload(qs: dict) -> dict:
    """Evaluate one bet from form params. Side-effect-free: builds the analysis and
    renders it, but does NOT log to Supabase, write a vault note, or hit the odds API."""
    import bet_evaluator as BE
    g = qs.get("g", [""])[0].strip()
    market = qs.get("market", ["ml"])[0].strip()
    side = qs.get("side", [""])[0].strip()
    line = qs.get("line", [""])[0].strip()
    ou = (qs.get("ou", [""])[0].strip() or None)
    odds = qs.get("odds", [""])[0].strip()
    if "@" not in g or not side:
        return {"error": "Enter a game as AWAY@HOME and a side (team / over / under)."}
    if not odds:
        return {"error": "Enter American odds, e.g. -120 or +105."}
    try:
        odds_i = int(odds)
    except ValueError:
        return {"error": f"Odds must be a whole number like -120 or +105 (got {odds!r})."}
    line_f = None
    if line:
        try:
            line_f = float(line)
        except ValueError:
            return {"error": f"Line must be a number (got {line!r})."}
    away, home = (s.strip().upper() for s in g.split("@", 1))
    try:
        gd = BE.load_game(away, home)
        try:
            market_info = BE.lookup_market(gd, market, side, line_f, ou)
        except Exception:
            market_info = None
        a = BE.build_analysis(gd, market, side, line_f, ou, odds_i, market_info)
        md = BE.render_markdown(a)
    except SystemExit as e:
        return {"error": str(e)}
    except Exception as e:  # noqa
        return {"error": f"{type(e).__name__}: {e}"}
    body = md.split("---", 2)[-1].strip() if md.startswith("---") else md
    return {"html": _md_to_html(body)}


PAGE = """<!DOCTYPE html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Chase Command Center</title><style>
:root{--bg:#0a0e16;--panel:#121a2b;--ink:#e9eef7;--mut:#8a97ad;--teal:#2dd4bf;--purple:#9a6bff;--line:#1e293b;--green:#22c55e;--red:#ef4444;--amber:#f59e0b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:"DM Sans",Segoe UI,system-ui,sans-serif;display:flex;height:100vh}
#side{width:220px;background:#0c1422;border-right:1px solid var(--line);padding:16px 12px;flex-shrink:0;overflow:auto}
#side h1{font-family:"Roboto Condensed",sans-serif;font-size:18px;margin:0 0 4px}
#side .sub{color:var(--mut);font-size:11px;margin-bottom:14px}
.navbtn{display:block;width:100%;text-align:left;background:#101a2c;color:var(--ink);border:1px solid var(--line);border-radius:9px;padding:9px 11px;margin:5px 0;cursor:pointer;font-size:13.5px}
.navbtn:hover{border-color:var(--purple)}.navbtn.on{background:#1c2740;border-color:var(--purple)}
#gamebox{display:flex;gap:5px;margin:10px 0}#gamebox input{flex:1;min-width:0;background:#101a2c;border:1px solid var(--line);border-radius:8px;color:var(--ink);padding:8px}
#gamebox button{background:var(--purple);border:0;border-radius:8px;color:#fff;padding:0 12px;cursor:pointer}
#main{flex:1;overflow:auto;padding:22px 26px}
#bar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}
.stat{background:#101a2c;border:1px solid var(--line);border-radius:10px;padding:8px 14px}
.stat b{font-family:"Roboto Condensed";font-size:20px;color:var(--teal)}.stat span{display:block;color:var(--mut);font-size:11px}
h2{font-family:"Roboto Condensed";font-weight:600;letter-spacing:.4px;margin:0 0 10px}
pre{white-space:pre-wrap;font-family:"JetBrains Mono",Consolas,monospace;font-size:12.7px;line-height:1.55;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;overflow:auto}
.tg{color:var(--green);font-weight:700}.tr{color:var(--red);font-weight:700}.ta{color:var(--amber);font-weight:700}.tp{color:var(--purple);font-weight:700}
.glink{color:var(--teal);cursor:pointer;text-decoration:underline}
.spin{color:var(--mut)}
.betform{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-bottom:16px;display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end}
.betform label{display:flex;flex-direction:column;font-size:11px;color:var(--mut);gap:4px}
.betform input,.betform select{background:#101a2c;border:1px solid var(--line);border-radius:8px;color:var(--ink);padding:8px 10px;font-size:13px;min-width:92px}
.betform input::placeholder{color:#4a576e}
.betform .go{background:var(--purple);border:0;color:#fff;padding:9px 20px;border-radius:8px;cursor:pointer;font-size:13.5px}
.betform .hint{flex-basis:100%;color:var(--mut);font-size:11.5px;margin-top:-2px}
.analysis{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px 24px;font-size:14px;line-height:1.6}
.analysis h2.vh{font-family:"Roboto Condensed";font-size:22px;margin:0 0 12px;padding-bottom:10px;border-bottom:1px solid var(--line)}
.analysis h2.vh.play{color:var(--green)}.analysis h2.vh.pass{color:var(--red)}
.analysis h3{font-family:"Roboto Condensed";font-size:14px;letter-spacing:.5px;text-transform:uppercase;color:var(--purple);margin:20px 0 8px}
.analysis h4{font-size:13px;color:var(--mut);margin:14px 0 6px}
.analysis blockquote{margin:0 0 14px;padding:12px 16px;background:#0c1422;border-left:3px solid var(--purple);border-radius:8px}
.analysis table{border-collapse:collapse;margin:8px 0 14px}
.analysis td{border:1px solid var(--line);padding:6px 13px}.analysis tr:first-child td{color:var(--mut);font-size:12px}
.analysis ul{margin:6px 0 14px;padding-left:20px}.analysis li{margin:3px 0}
.analysis hr{border:0;border-top:1px solid var(--line);margin:16px 0}.analysis b{color:#fff}
.berr{color:var(--red);padding:12px 16px;background:#1a0e12;border:1px solid #3a1a20;border-radius:10px}
</style></head><body>
<div id=side>
  <h1>Chase</h1><div class=sub>Command Center</div>
  <div id=gamebox><input id=gi placeholder="LAD@ARI" /><button onclick="game()">Go</button></div>
  <button class="navbtn" data-k=bet onclick="betForm(this)">★ Evaluate a Bet</button>
  <button class="navbtn on" data-k=value onclick="load('value',this)">Value Board</button>
  <button class="navbtn" data-k=regression onclick="load('regression',this)">Regression / Progression</button>
  <button class="navbtn" data-k=edge onclick="load('edge',this)">Market Edge</button>
  <button class="navbtn" data-k=cross onclick="load('cross',this)">Cross-Venue</button>
  <button class="navbtn" data-k=book onclick="load('book',this)">Book Intel</button>
  <button class="navbtn" data-k=sharp onclick="load('sharp',this)">Sharp Signals</button>
</div>
<div id=main>
  <div id=bar class=spin>loading warehouse…</div>
  <h2 id=title>Value Board</h2>
  <div id=out class=spin>…</div>
</div>
<script>
const out=document.getElementById('out'),title=document.getElementById('title');
function hl(t){return t
 .replace(/\\*\\* TRADEABLE \\*\\*/g,'<span class=tg>** TRADEABLE **</span>')
 .replace(/\\*\\*/g,'<span class=ta>**</span>')
 .replace(/REGRESSION/g,'<span class=tr>REGRESSION</span>')
 .replace(/PROGRESSION/g,'<span class=tg>PROGRESSION</span>')
 .replace(/\\bOVER\\b/g,'<span class=tg>OVER</span>').replace(/\\bUNDER\\b/g,'<span class=tr>UNDER</span>')
 .replace(/STEAM/g,'<span class=tp>STEAM</span>')
 .replace(/\\bARBITRAGE\\b/g,'<span class=tp>ARBITRAGE</span>');}
function linkGames(t){return t.replace(/\\b([A-Z]{2,3})@([A-Z]{2,3})\\b/g,
  '<span class=glink onclick="gameDirect(\\'$1@$2\\')">$1@$2</span>');}
async function load(k,btn){
  document.querySelectorAll('.navbtn').forEach(b=>b.classList.remove('on'));
  if(btn)btn.classList.add('on');
  title.textContent=btn?btn.textContent:k; out.className='spin'; out.textContent='running…';
  const d=await (await fetch('/api/'+k)).json();
  out.className=''; out.innerHTML='<pre>'+linkGames(hl(d.text||JSON.stringify(d,null,2)))+'</pre>';
}
function game(){const g=document.getElementById('gi').value.trim();if(g)gameDirect(g);}
async function gameDirect(g){
  document.querySelectorAll('.navbtn').forEach(b=>b.classList.remove('on'));
  title.textContent='Game — '+g.toUpperCase(); out.className='spin'; out.textContent='running…';
  const d=await (await fetch('/api/game?g='+encodeURIComponent(g))).json();
  out.className=''; out.innerHTML='<pre>'+linkGames(hl(d.text))+'</pre>';
}
function betForm(btn){
  document.querySelectorAll('.navbtn').forEach(b=>b.classList.remove('on'));
  if(btn)btn.classList.add('on');
  title.textContent='Evaluate a Bet'; out.className='';
  out.innerHTML=`<form class=betform onsubmit="runBet(event)">
    <label>Game<input id=bg placeholder="NYY@TOR"></label>
    <label>Market<select id=bm onchange="betFields()">
      <option value=ml>Moneyline</option><option value=total>Total</option>
      <option value=team_total>Team total</option><option value=runline>Run line</option></select></label>
    <label>Side<input id=bs placeholder="team / over / under"></label>
    <label id=blw>Line<input id=bl placeholder="8.5" style="min-width:70px"></label>
    <label id=bouw style="display:none">O/U<select id=bou><option value="">-</option><option value=over>over</option><option value=under>under</option></select></label>
    <label>Odds<input id=bo placeholder="-120" style="min-width:74px"></label>
    <button class=go type=submit>Evaluate</button>
    <div class=hint>Moneyline: side = team (e.g. PIT). Total: side = over/under + line. Team total: side = team, set O/U + line. Run line: side = team, set line (-1.5). Read-only - nothing is logged or fetched.</div>
  </form><div id=betresult></div>`;
  betFields();
}
function betFields(){
  const m=document.getElementById('bm').value;
  document.getElementById('bouw').style.display=(m==='team_total')?'flex':'none';
  document.getElementById('blw').style.display=(m==='ml')?'none':'flex';
}
async function runBet(e){
  e.preventDefault();
  const q=new URLSearchParams({g:bg.value.trim(),market:bm.value,side:bs.value.trim(),line:bl.value.trim(),ou:bou.value,odds:bo.value.trim()});
  const r=document.getElementById('betresult');
  r.innerHTML='<div class=spin style="padding:12px">evaluating...</div>';
  const d=await (await fetch('/api/bet?'+q)).json();
  r.innerHTML=d.error?('<div class=berr>'+d.error+'</div>'):('<div class=analysis>'+d.html+'</div>');
}
async function eco(){
  const d=await (await fetch('/api/ecosystem')).json();
  const items=[['Games',d.games],['Outcomes',d.outcomes],['Odds',d.odds],['PM contracts',d.pm],
   ['Sharp sig',d.sharp_sig],['Predictions',d.predictions]];
  document.getElementById('bar').className='';
  document.getElementById('bar').innerHTML=items.map(([k,v])=>
   `<div class=stat><b>${v<0?'—':v.toLocaleString()}</b><span>${k}</span></div>`).join('');
}
eco(); load('value',document.querySelector('.navbtn[data-k=value]'));
</script></body></html>"""


ROUTES = {
    "/api/value": value_text, "/api/edge": edge_text, "/api/regression": regression_text,
    "/api/cross": cross_text, "/api/book": book_text,
}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: str, ct="application/json"):
        b = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if u.path == "/":
            return self._send(PAGE, "text/html; charset=utf-8")
        if u.path in ROUTES:
            return self._send(json.dumps({"text": ROUTES[u.path]()}))
        if u.path == "/api/game":
            return self._send(json.dumps({"text": game_text(qs.get("g", ["LAD@ARI"])[0])}))
        if u.path == "/api/bet":
            return self._send(json.dumps(bet_eval_payload(qs)))
        if u.path == "/api/sharp":
            rows = sharp_rows()
            if not rows:
                return self._send(json.dumps({"text": "No sharp signals stored. Run sharp_tracker.py pre-game."}))
            lines = ["  SHARP SIGNALS (latest)", f"  {'MKT':<10}{'SIDE':<8}{'DIV':>6}{'SHARP%':>8}{'SOFT%':>8}  STEAM"]
            for r in rows:
                lines.append(f"  {r['market_type']:<10}{str(r['selection']):<8}"
                             f"{(r['divergence'] or 0)*100:>5.1f}{(r['sharp_novig_prob'] or 0)*100:>7.1f}"
                             f"{(r['soft_novig_prob'] or 0)*100:>7.1f}  {'STEAM' if r['steam_flag'] else ''}")
            return self._send(json.dumps({"text": "\n".join(lines)}))
        if u.path == "/api/ecosystem":
            return self._send(json.dumps(ecosystem()))
        self._send(json.dumps({"error": "not found"}))


def main():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        url = f"http://localhost:{PORT}"
        print(f"  Chase Command Center -> {url}  (Ctrl+C to stop)")
        try:
            webbrowser.open(url)
        except Exception:
            pass
        httpd.serve_forever()


if __name__ == "__main__":
    main()
