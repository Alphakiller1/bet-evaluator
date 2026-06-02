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
</style></head><body>
<div id=side>
  <h1>Chase</h1><div class=sub>Command Center</div>
  <div id=gamebox><input id=gi placeholder="LAD@ARI" /><button onclick="game()">Go</button></div>
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
  <pre id=out class=spin>…</pre>
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
  out.className=''; out.innerHTML=linkGames(hl(d.text||JSON.stringify(d,null,2)));
}
function game(){const g=document.getElementById('gi').value.trim();if(g)gameDirect(g);}
async function gameDirect(g){
  document.querySelectorAll('.navbtn').forEach(b=>b.classList.remove('on'));
  title.textContent='Game — '+g.toUpperCase(); out.className='spin'; out.textContent='running…';
  const d=await (await fetch('/api/game?g='+encodeURIComponent(g))).json();
  out.className=''; out.innerHTML=linkGames(hl(d.text));
}
async function eco(){
  const d=await (await fetch('/api/ecosystem')).json();
  const items=[['Games',d.games],['Outcomes',d.outcomes],['Odds',d.odds],['PM contracts',d.pm],
   ['Sharp sig',d.sharp_sig],['Predictions',d.predictions]];
  document.getElementById('bar').className='';
  document.getElementById('bar').innerHTML=items.map(([k,v])=>
   `<div class=stat><b>${v<0?'—':v.toLocaleString()}</b><span>${k}</span></div>`).join('');
}
eco(); load('value',document.querySelector('.navbtn'));
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
