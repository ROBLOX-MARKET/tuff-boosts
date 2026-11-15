"""Web dashboard served by the bot on SERVER_PORT. Owner-only (password)."""
import secrets

from aiohttp import web


def make_app(ctx) -> web.Application:
    app = web.Application()
    sessions: set[str] = set()
    open_mode = bool(ctx.get("open"))

    def authed(req) -> bool:
        if open_mode:
            return True
        tok = req.headers.get("X-Dash-Token") or req.cookies.get("dash") or req.query.get("t")
        return tok in sessions

    async def guard(req):
        return None if authed(req) else web.json_response({"error": "auth"}, status=401)

    async def index(req):
        html = PAGE.replace("{{STORE}}", ctx["store_name"]).replace("{{OPEN}}", "true" if open_mode else "false")
        return web.Response(text=html, content_type="text/html",
                            headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"})

    async def login(req):
        data = await req.post()
        if secrets.compare_digest(str(data.get("password", "")), str(ctx["password"]())):
            tok = secrets.token_hex(16)
            sessions.add(tok)
            r = web.json_response({"ok": True, "token": tok})
            r.set_cookie("dash", tok, httponly=True, max_age=604800, samesite="Lax")
            return r
        return web.json_response({"ok": False}, status=401)

    async def overview(req):
        if (g := await guard(req)):
            return g
        return web.json_response(ctx["overview"]())

    async def channels(req):
        if (g := await guard(req)):
            return g
        return web.json_response({"channels": ctx["channels"]()})

    async def prices_get(req):
        if (g := await guard(req)):
            return g
        return web.json_response(ctx["get_prices"]())

    async def prices_set(req):
        if (g := await guard(req)):
            return g
        await ctx["set_prices"](await req.json())
        return web.json_response({"ok": True})

    async def panel(req):
        if (g := await guard(req)):
            return g
        body = await req.json()
        try:
            cid = int(body.get("channel"))
        except (TypeError, ValueError):
            return web.json_response({"ok": False, "error": "pick a channel"})
        return web.json_response(await ctx["post_panel"](body.get("panel"), cid))

    app.add_routes([
        web.get("/", index),
        web.post("/login", login),
        web.get("/api/overview", overview),
        web.get("/api/channels", channels),
        web.get("/api/prices", prices_get),
        web.post("/api/prices", prices_set),
        web.post("/api/panel", panel),
    ])
    return app


async def start(ctx, port: int):
    app = make_app(ctx)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{STORE}} — Dashboard</title>
<style>
:root{--bg:#0f1115;--card:#181b20;--card2:#1f232a;--line:#2a2f37;--txt:#e7e9ee;--mut:#9aa3b0;--grn:#2dce70;--grn2:#27b863;--red:#ed5356}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
a{color:var(--grn)}
.wrap{max-width:900px;margin:0 auto;padding:20px 16px 60px}
header{display:flex;align-items:center;gap:12px;margin:8px 0 22px}
header .dot{width:12px;height:12px;border-radius:50%;background:var(--grn);box-shadow:0 0 12px var(--grn)}
header h1{font-size:20px;margin:0;font-weight:800}
header span{color:var(--mut);font-size:13px}
.tabs{display:flex;gap:6px;margin-bottom:18px;flex-wrap:wrap}
.tab{padding:9px 16px;border-radius:10px;background:var(--card);color:var(--mut);cursor:pointer;border:1px solid transparent;font-weight:600;font-size:14px}
.tab.on{background:var(--card2);color:var(--txt);border-color:var(--line)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:16px}
.card h2{font-size:15px;margin:0 0 12px;font-weight:700}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.stat{background:var(--card2);border:1px solid var(--line);border-radius:12px;padding:14px}
.stat .n{font-size:24px;font-weight:800}.stat .l{color:var(--mut);font-size:12px;margin-top:2px}
label{display:block;color:var(--mut);font-size:12px;margin:12px 0 5px;font-weight:600}
input,select,textarea{width:100%;background:var(--bg);border:1px solid var(--line);color:var(--txt);border-radius:9px;padding:10px 12px;font:inherit}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--grn)}
button{background:var(--grn);color:#06240f;border:0;border-radius:10px;padding:11px 18px;font-weight:800;cursor:pointer;font-size:14px}
button:hover{background:var(--grn2)}button.sec{background:var(--card2);color:var(--txt);border:1px solid var(--line)}
button:disabled{opacity:.5;cursor:default}
.row{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap}.row>div{flex:1;min-width:140px}
table{width:100%;border-collapse:collapse}td,th{padding:7px 6px;text-align:left}th{color:var(--mut);font-size:12px;font-weight:600}
td input{padding:7px 9px}
.btable td:first-child{width:90px}.btable td:last-child{width:44px}
.msg{margin-top:12px;font-size:13px;color:var(--grn);min-height:18px}
.msg.err{color:var(--red)}
.pill{display:inline-block;background:var(--card2);border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:12px;color:var(--mut);margin:3px 4px 0 0}
#login{position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:9}
#login .box{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:26px;width:320px;max-width:90vw}
.hide{display:none}.small{color:var(--mut);font-size:12px}
</style></head>
<body>
<div id="login"><div class="box">
  <header style="margin:0 0 16px"><span class="dot"></span><h1>{{STORE}}</h1></header>
  <div class="small" style="margin-bottom:12px">Enter the dashboard password.</div>
  <input id="pw" type="password" placeholder="Password" autofocus>
  <button style="width:100%;margin-top:12px" onclick="doLogin()">Unlock</button>
  <div id="lerr" class="msg err"></div>
</div></div>

<div id="app" class="hide"><div class="wrap">
  <header><span class="dot"></span><h1>{{STORE}}</h1><span id="sub"></span></header>
  <div class="tabs">
    <div class="tab on" data-t="overview" onclick="tab('overview')">Overview</div>
    <div class="tab" data-t="panels" onclick="tab('panels')">Panels</div>
    <div class="tab" data-t="prices" onclick="tab('prices')">Prices</div>
  </div>

  <div id="t-overview">
    <div class="card"><h2>Stats</h2><div id="stats" class="grid"></div></div>
    <div class="card"><h2>Configured channels</h2><div id="chans"></div></div>
  </div>

  <div id="t-panels" class="hide">
    <div class="card">
      <h2>Post a panel</h2>
      <div class="small">Sends a fresh copy of the panel to the channel you pick.</div>
      <div class="row" style="margin-top:12px">
        <div><label>Panel</label>
          <select id="panel">
            <option value="ticket">🎫 Ticket panel</option>
            <option value="prices">🏷️ Prices panel</option>
            <option value="payments">💳 Payments panel</option>
            <option value="tos">📜 Terms of Service</option>
            <option value="info">ℹ️ Server info</option>
            <option value="roles">🔔 Ping roles</option>
            <option value="legit">✅ Are we legit</option>
          </select></div>
        <div><label>Channel</label><select id="pchan"></select></div>
        <div style="flex:0 0 auto"><button onclick="postPanel()">Post</button></div>
      </div>
      <div id="pmsg" class="msg"></div>
    </div>
  </div>

  <div id="t-prices" class="hide">
    <div class="card">
      <h2>Nitro</h2>
      <label>Nitro Premium (1 Month) price</label>
      <input id="nitro" placeholder="$6.00" style="max-width:200px">
    </div>
    <div class="card">
      <h2>Boosts</h2>
      <table class="btable"><thead><tr><th>Pack</th><th>1 Month</th><th>3 Months</th><th></th></tr></thead>
      <tbody id="boosts"></tbody></table>
      <button class="sec" style="margin-top:10px" onclick="addBoost()">+ Add row</button>
    </div>
    <div class="card">
      <h2>Decorations</h2>
      <label>Text (leave blank for “Coming soon!”)</label>
      <textarea id="decos" rows="2" placeholder="Coming soon!"></textarea>
    </div>
    <div class="row"><div style="flex:0 0 auto"><button onclick="savePrices()">Save &amp; repost prices panel</button></div></div>
    <div id="prmsg" class="msg"></div>
  </div>
</div></div>

<script>
let CH=[];
let TOKEN=localStorage.getItem('dashtok')||'';
const $=s=>document.querySelector(s);
async function api(p,o){
  o=o||{};o.headers=Object.assign({'X-Dash-Token':TOKEN},o.headers||{});
  const r=await fetch(p,o);
  if(r.status===401){TOKEN='';localStorage.removeItem('dashtok');show('#login');$('#app').classList.add('hide');throw 0;}
  return r;
}
function show(s){$(s).classList.remove('hide');}
async function doLogin(){
  $('#lerr').textContent='';
  let r;
  try{r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'password='+encodeURIComponent($('#pw').value)});}
  catch(e){$('#lerr').textContent='Could not reach the bot.';return;}
  if(r.ok){const d=await r.json();TOKEN=d.token||'';localStorage.setItem('dashtok',TOKEN);$('#login').classList.add('hide');$('#app').classList.remove('hide');boot();}
  else $('#lerr').textContent='Wrong password.';
}
$('#pw')&&$('#pw').addEventListener('keydown',e=>{if(e.key==='Enter')doLogin();});
function tab(t){document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x.dataset.t===t));
  ['overview','panels','prices'].forEach(x=>$('#t-'+x).classList.toggle('hide',x!==t));}
async function boot(){
  try{
    const ov=await(await api('/api/overview')).json();
    $('#sub').textContent=ov.guild+' · '+ov.members+' members · '+ov.bot;
    $('#stats').innerHTML=Object.entries(ov.stats).map(([l,n])=>`<div class="stat"><div class="n">${n}</div><div class="l">${l}</div></div>`).join('');
    $('#chans').innerHTML=Object.entries(ov.channels).map(([l,id])=>`<span class="pill">${l}: #${id}</span>`).join('');
    CH=(await(await api('/api/channels')).json()).channels;
    $('#pchan').innerHTML=CH.map(c=>`<option value="${c.id}">#${c.name}</option>`).join('');
    loadPrices();
  }catch(e){}
}
async function postPanel(){
  const p=$('#pmsg');p.className='msg';p.textContent='Posting…';
  const r=await(await api('/api/panel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({panel:$('#panel').value,channel:$('#pchan').value})})).json();
  if(r.ok){p.textContent='✅ Posted to #'+(CH.find(c=>c.id==$('#pchan').value)||{}).name;}
  else{p.className='msg err';p.textContent='❌ '+(r.error||'failed');}
}
function boostRow(r){return `<tr><td><input value="${r[0]||''}" placeholder="2x"></td><td><input value="${r[1]||''}" placeholder="$0.80"></td><td><input value="${r[2]||''}" placeholder="$1.60"></td><td><button class="sec" onclick="this.closest('tr').remove()">✕</button></td></tr>`;}
function addBoost(){$('#boosts').insertAdjacentHTML('beforeend',boostRow(['','','']));}
async function loadPrices(){
  const pr=await(await api('/api/prices')).json();
  $('#nitro').value=pr.nitro||'';$('#decos').value=pr.decorations||'';
  $('#boosts').innerHTML=(pr.boosts||[]).map(boostRow).join('');
}
async function savePrices(){
  const m=$('#prmsg');m.className='msg';m.textContent='Saving…';
  const boosts=[...$('#boosts').querySelectorAll('tr')].map(tr=>[...tr.querySelectorAll('input')].map(i=>i.value.trim())).filter(r=>r[0]);
  const r=await(await api('/api/prices',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({nitro:$('#nitro').value.trim(),boosts,decorations:$('#decos').value.trim()})})).json();
  m.textContent=r.ok?'✅ Saved — prices panel updated.':'❌ failed';if(!r.ok)m.className='msg err';
}
// open mode = no password; otherwise auto-skip login if a saved token still works
const OPEN={{OPEN}};
if(OPEN){$('#login').classList.add('hide');$('#app').classList.remove('hide');boot();}
else if(TOKEN){fetch('/api/overview',{headers:{'X-Dash-Token':TOKEN}}).then(r=>{if(r.ok){$('#login').classList.add('hide');$('#app').classList.remove('hide');boot();}});}
</script>
</body></html>"""
