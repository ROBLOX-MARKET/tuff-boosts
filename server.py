"""
Coin Buzzer Server — 24/7 crypto price watcher that PUSHES to your phone.

Why this reaches a locked/asleep phone and the web page can't:
  * A web page only runs while it's open. This runs on a host 24/7.
  * When a coin hits your target it sends a push via ntfy (https://ntfy.sh),
    and the free ntfy app rings your phone LOUD even when locked / on silent
    (max priority), the same way an alarm app does.
  * It also serves a no-login web page so you can add/remove alerts from a browser.

(A phone that is fully powered OFF can't receive anything — nothing can fix that.
 This covers locked, asleep, screen-off, silent, pocket.)

Environment variables (set on your host — bot-hosting panel, etc.):
  NTFY_TOPIC    - a SECRET topic name you make up, e.g. munzy-coins-9f3k2  (REQUIRED)
  NTFY_SERVER   - default https://ntfy.sh (leave as is unless self-hosting)
  POLL_SECONDS  - how often to check prices (default 60)
  PORT          - web port (your host usually sets this automatically)
"""

import asyncio
import json
import os
from pathlib import Path

import aiohttp
from aiohttp import web

BASE = Path(__file__).resolve().parent
ALERTS_FILE = BASE / "alerts.json"

NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "").strip()
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60") or "60")
PORT = int(os.getenv("PORT") or os.getenv("SERVER_PORT") or "8080")

COINS = [
    {"id": "bitcoin", "sym": "BTC", "name": "Bitcoin"},
    {"id": "ethereum", "sym": "ETH", "name": "Ethereum"},
    {"id": "solana", "sym": "SOL", "name": "Solana"},
    {"id": "litecoin", "sym": "LTC", "name": "Litecoin"},
    {"id": "tether", "sym": "USDT", "name": "Tether"},
    {"id": "binancecoin", "sym": "BNB", "name": "BNB"},
    {"id": "ripple", "sym": "XRP", "name": "XRP"},
    {"id": "cardano", "sym": "ADA", "name": "Cardano"},
    {"id": "dogecoin", "sym": "DOGE", "name": "Dogecoin"},
    {"id": "tron", "sym": "TRX", "name": "TRON"},
    {"id": "chainlink", "sym": "LINK", "name": "Chainlink"},
    {"id": "polkadot", "sym": "DOT", "name": "Polkadot"},
    {"id": "avalanche-2", "sym": "AVAX", "name": "Avalanche"},
    {"id": "shiba-inu", "sym": "SHIB", "name": "Shiba Inu"},
    {"id": "pepe", "sym": "PEPE", "name": "Pepe"},
    {"id": "dogwifcoin", "sym": "WIF", "name": "dogwifhat"},
]
BY_ID = {c["id"]: c for c in COINS}


def load_alerts():
    try:
        return json.loads(ALERTS_FILE.read_text("utf-8"))
    except Exception:
        return []


def save_alerts(data):
    tmp = ALERTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), "utf-8")
    tmp.replace(ALERTS_FILE)


def fmt(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    if n >= 1000:
        return "$" + format(round(n), ",")
    if n >= 1:
        return "$" + format(round(n, 2), ",")
    if n >= 0.01:
        return "$" + format(n, ".4f")
    return "$" + format(n, ".8f").rstrip("0")


async def send_push(session, title, message, priority="urgent"):
    if not NTFY_TOPIC:
        print("[push] NTFY_TOPIC not set — cannot send:", title)
        return
    url = f"{NTFY_SERVER}/{NTFY_TOPIC}"
    headers = {
        "Title": title,
        "Priority": priority,          # max/urgent rings through silent + DND
        "Tags": "rotating_light",
    }
    try:
        async with session.post(url, data=message.encode("utf-8"), headers=headers) as r:
            if r.status >= 300:
                print("[push] failed", r.status, await r.text())
    except Exception as ex:
        print("[push] error:", ex)


async def watcher():
    print(f"[watcher] polling every {POLL_SECONDS}s; ntfy topic set: {bool(NTFY_TOPIC)}")
    async with aiohttp.ClientSession() as session:
        # tell the phone the watcher is alive, once at startup
        await send_push(session, "✅ Coin Buzzer is watching",
                        "Price alerts are armed. You'll get a loud push when one hits.",
                        priority="default")
        while True:
            try:
                alerts = load_alerts()
                ids = sorted({a["coin"] for a in alerts if a.get("status") == "armed"})
                if ids:
                    url = ("https://api.coingecko.com/api/v3/simple/price?ids="
                           + ",".join(ids) + "&vs_currencies=usd")
                    async with session.get(url) as r:
                        prices = await r.json() if r.status == 200 else {}
                    changed = False
                    for a in alerts:
                        if a.get("status") != "armed":
                            continue
                        p = prices.get(a["coin"], {}).get("usd")
                        if p is None:
                            continue
                        hit = (a["dir"] == "above" and p >= a["target"]) or \
                              (a["dir"] == "below" and p <= a["target"])
                        if hit:
                            c = BY_ID.get(a["coin"], {"sym": a["coin"], "name": a["coin"]})
                            arrow = "≥" if a["dir"] == "above" else "≤"
                            await send_push(
                                session,
                                f"🔔 {c['sym']} hit {fmt(a['target'])}",
                                f"{c['name']} is now {fmt(p)}  (target {arrow} {fmt(a['target'])})",
                                priority="max")
                            a["status"] = "fired"
                            changed = True
                    if changed:
                        save_alerts(alerts)
            except Exception as ex:
                print("[watcher] loop error:", ex)
            await asyncio.sleep(POLL_SECONDS)


# ---------------------------------------------------------------- web UI / API

async def h_index(request):
    return web.Response(text=PAGE, content_type="text/html",
                        headers={"Cache-Control": "no-store"})


async def h_list(request):
    out = []
    for a in load_alerts():
        c = BY_ID.get(a["coin"], {"sym": a["coin"], "name": a["coin"]})
        out.append({**a, "sym": c["sym"], "name": c["name"]})
    return web.json_response({"alerts": out, "topic_set": bool(NTFY_TOPIC)})


async def h_add(request):
    d = await request.json()
    coin = d.get("coin")
    direction = d.get("dir")
    try:
        target = float(d.get("target"))
    except (TypeError, ValueError):
        return web.json_response({"error": "bad target"}, status=400)
    if coin not in BY_ID or direction not in ("above", "below") or target <= 0:
        return web.json_response({"error": "bad input"}, status=400)
    alerts = load_alerts()
    alerts.append({"id": "a" + os.urandom(4).hex(), "coin": coin,
                   "dir": direction, "target": target, "status": "armed"})
    save_alerts(alerts)
    return web.json_response({"ok": True})


async def h_del(request):
    d = await request.json()
    alerts = [a for a in load_alerts() if a.get("id") != d.get("id")]
    save_alerts(alerts)
    return web.json_response({"ok": True})


async def h_rearm(request):
    d = await request.json()
    alerts = load_alerts()
    for a in alerts:
        if a.get("id") == d.get("id"):
            a["status"] = "armed"
    save_alerts(alerts)
    return web.json_response({"ok": True})


async def h_test(request):
    async with aiohttp.ClientSession() as s:
        await send_push(s, "🔔 Test alert",
                        "If this rang your phone, you're all set.", priority="max")
    return web.json_response({"ok": True})


def build_app():
    app = web.Application()
    app.add_routes([
        web.get("/", h_index),
        web.get("/api/alerts", h_list),
        web.post("/api/add", h_add),
        web.post("/api/del", h_del),
        web.post("/api/rearm", h_rearm),
        web.post("/api/test", h_test),
    ])

    async def on_start(app):
        app["watcher"] = asyncio.create_task(watcher())
    app.on_startup.append(on_start)
    return app


COIN_OPTS = "".join(
    f'<option value="{c["id"]}">{c["sym"]} · {c["name"]}</option>' for c in COINS)

PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Coin Buzzer</title>
<style>
:root{--bg:#0a0e13;--surface:#121922;--surface2:#0e151d;--line:#1f2b38;--fg:#e9f0f7;--muted:#8498a9;--accent:#19e0ff;--accent2:#3ba9ff;--good:#37d98a;--bad:#ff5d7a;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font-family:Inter,system-ui,sans-serif;line-height:1.5}
.wrap{max-width:560px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:1.6rem;margin:0}.sub{color:var(--muted);font-size:.86rem;margin:4px 0 0}
.topic{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:16px 0;font-size:.82rem;color:var(--muted)}
.topic.bad{border-color:var(--bad);color:#ffd4dc}
.card{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:16px;margin-bottom:16px}
.lab{font-size:.72rem;font-weight:600;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);margin-bottom:8px}
.row{display:flex;gap:10px;flex-wrap:wrap}
select,input{flex:1;min-width:0;background:var(--surface2);border:1px solid var(--line);color:var(--fg);font-size:1rem;padding:12px;border-radius:11px}
.f-coin{flex:2 1 150px}
button{font-family:inherit;cursor:pointer}
.add{margin-top:12px;width:100%;font-weight:600;font-size:1rem;color:#04222b;background:linear-gradient(135deg,var(--accent),var(--accent2));border:0;border-radius:12px;padding:14px}
.alert{display:flex;align-items:center;gap:12px;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:13px 14px;margin-top:10px}
.alert.fired{opacity:.6}
.sym{width:40px;height:40px;flex:0 0 40px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:.78rem;background:var(--surface2);border:1px solid var(--line)}
.ainfo{flex:1;min-width:0}.aname{font-weight:600}.acond{font-size:.82rem;color:var(--muted)}
.astat{font-size:.72rem;text-transform:uppercase;color:var(--good)}.astat.fired{color:var(--muted)}
.mini{background:var(--surface2);border:1px solid var(--line);color:var(--fg);border-radius:8px;font-size:.74rem;padding:5px 9px}
.x{background:none;border:0;color:var(--muted);font-size:1.3rem;padding:0 4px}
.test{width:100%;margin-top:4px;background:var(--surface);border:1px solid var(--line);color:var(--fg);border-radius:12px;padding:13px;font-weight:600}
.note{color:var(--muted);font-size:.78rem;margin-top:18px}
.empty{color:var(--muted);text-align:center;padding:20px}
</style></head><body>
<div class="wrap">
  <h1>Coin Buzzer</h1>
  <p class="sub">Pushes a loud alert to your phone — even locked or asleep.</p>
  <div class="topic" id="topic"></div>
  <div class="card">
    <div class="lab">New alert</div>
    <div class="row">
      <select class="f-coin" id="coin">__COINS__</select>
      <select id="dir"><option value="above">rises to ≥</option><option value="below">drops to ≤</option></select>
    </div>
    <div class="row" style="margin-top:10px"><input id="target" inputmode="decimal" placeholder="$ target price"></div>
    <button class="add" id="add">+ Add alert</button>
  </div>
  <div id="list"></div>
  <button class="test" id="test">Send test push</button>
  <p class="note">Install the free <b>ntfy</b> app, subscribe to your topic above, and set that topic's notifications to a loud/alarm sound with "override Do Not Disturb". Then this rings you anywhere. Prices are live (CoinGecko).</p>
</div>
<script>
function fmt(n){n=Number(n);if(n>=1000)return '$'+n.toLocaleString('en-US',{maximumFractionDigits:0});if(n>=1)return '$'+n.toLocaleString('en-US',{maximumFractionDigits:2});if(n>=0.01)return '$'+n.toFixed(4);return '$'+n.toPrecision(3);}
async function refresh(){
  var r=await fetch('/api/alerts');var d=await r.json();
  var t=document.getElementById('topic');
  t.className='topic'+(d.topic_set?'':' bad');
  t.innerHTML=d.topic_set?'🔔 Alerts push to your ntfy topic. Make sure the ntfy app is subscribed to it.':'⚠️ No NTFY_TOPIC set on the server — pushes won\\'t send until you add it in the host settings.';
  var list=document.getElementById('list');list.innerHTML='';
  if(!d.alerts.length){list.innerHTML='<div class="empty">No alerts yet. Add one above ☝️</div>';return;}
  d.alerts.forEach(function(a){
    var el=document.createElement('div');el.className='alert'+(a.status==='fired'?' fired':'');
    el.innerHTML='<div class="sym">'+a.sym+'</div><div class="ainfo"><div class="aname">'+a.name+'</div>'+
      '<div class="acond">'+(a.dir==='above'?'≥':'≤')+' '+fmt(a.target)+'</div></div>'+
      '<div style="text-align:right">'+(a.status==='fired'?'<span class="astat fired">✓ sent</span> <button class="mini" data-rearm="'+a.id+'">Re-arm</button>':'<span class="astat">● watching</span>')+'</div>'+
      '<button class="x" data-del="'+a.id+'">×</button>';
    list.appendChild(el);
  });
}
document.getElementById('add').addEventListener('click',async function(){
  var coin=document.getElementById('coin').value,dir=document.getElementById('dir').value;
  var target=parseFloat(String(document.getElementById('target').value).replace(/[, $]/g,''));
  if(!(target>0)){document.getElementById('target').focus();return;}
  await fetch('/api/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({coin:coin,dir:dir,target:target})});
  document.getElementById('target').value='';refresh();
});
document.getElementById('list').addEventListener('click',async function(e){
  var del=e.target.getAttribute('data-del'),re=e.target.getAttribute('data-rearm');
  if(del){await fetch('/api/del',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:del})});refresh();}
  if(re){await fetch('/api/rearm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:re})});refresh();}
});
document.getElementById('test').addEventListener('click',function(){fetch('/api/test',{method:'POST'});});
refresh();setInterval(refresh,15000);
</script></body></html>""".replace("__COINS__", COIN_OPTS)


if __name__ == "__main__":
    print(f"Coin Buzzer server on :{PORT}")
    web.run_app(build_app(), host="0.0.0.0", port=PORT)
