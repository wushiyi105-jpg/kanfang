"""Web 入口:零依赖 http.server,单页 + JSON API。

启动:`python -m kanfang web --port 8000`,浏览器打开 http://127.0.0.1:8000
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import agent, llm, rules
from . import db as dbmod

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>看房 · 首月要备多少</title>
<style>
  :root {
    --bg:#f4f6fb; --card:#fff; --ink:#1f2937; --muted:#6b7280;
    --blue:#2563eb; --blue-soft:#dbeafe; --amber:#f59e0b; --amber-soft:#fef3c7;
    --line:#e5e7eb; --radius:16px; --red:#b91c1c; --green:#15803d;
  }
  * { box-sizing:border-box; }
  body { margin:0; font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
         background:var(--bg); color:var(--ink); }
  .wrap { max-width:900px; margin:0 auto; padding:32px 20px 80px; }
  header h1 { font-size:26px; margin:0 0 4px; }
  header p { color:var(--muted); margin:0 0 24px; }
  .badge { display:inline-block; font-size:12px; padding:3px 10px; border-radius:999px;
           background:var(--blue-soft); color:var(--blue); margin-left:8px; vertical-align:2px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
          padding:20px; margin-bottom:20px; box-shadow:0 1px 2px rgba(16,24,40,.04); }
  label { display:block; font-size:13px; color:var(--muted); margin:0 0 6px; }
  textarea, input[type=text], input[type=number], select {
    width:100%; padding:12px 14px; border:1px solid var(--line); border-radius:12px;
    font-size:15px; font-family:inherit; background:#fff; }
  textarea:focus, input:focus, select:focus { outline:none; border-color:var(--blue);
    box-shadow:0 0 0 3px var(--blue-soft); }
  .row { display:flex; gap:12px; flex-wrap:wrap; }
  .row > div { flex:1; min-width:160px; }
  button { background:var(--blue); color:#fff; border:none; padding:12px 22px; border-radius:12px;
           font-size:15px; cursor:pointer; }
  button:hover { filter:brightness(1.06); }
  .hint { color:var(--muted); font-size:12px; margin-top:8px; }
  .spin { display:inline-block; width:16px; height:16px; border:2px solid #fff;
          border-top-color:transparent; border-radius:50%; animation:sp .7s linear infinite; }
  @keyframes sp { to { transform:rotate(360deg); } }
  .result { display:none; } .result.show { display:block; }
  .cost { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin:12px 0; }
  .cell { border:1px solid var(--line); border-radius:12px; padding:12px 14px; }
  .cell b { display:block; font-size:22px; margin-top:4px; }
  .cell span { font-size:12px; color:var(--muted); }
  .big { font-size:22px; font-weight:600; }
  .item { border-left:3px solid var(--amber); padding:6px 0 6px 12px; margin:10px 0; }
  .item.high { border-color:var(--red); }
  .item.low { border-color:var(--line); }
  .item p { margin:2px 0; font-size:14px; }
  .ev { color:var(--muted); font-size:12px; }
  .q { margin:6px 0; font-size:14px; }
  .q i { color:var(--muted); font-style:normal; font-size:12px; display:block; }
  h3 { font-size:15px; margin:18px 0 6px; color:var(--muted); font-weight:600; }
  .msg { background:var(--blue-soft); border-radius:12px; padding:12px 14px; margin:8px 0;
         font-size:14px; line-height:1.7; }
  details { margin-top:10px; font-size:14px; }
  details li { margin:4px 0; color:#374151; }

.prod{display:block;font-size:11px;line-height:1.4;letter-spacing:.18em;font-weight:650;margin-bottom:8px;opacity:.72}
.lede{max-width:62ch;line-height:1.75}
.route{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1px;margin:0 0 20px;border:1px solid var(--line);background:var(--line)}
.route span{padding:11px 13px;background:var(--card);font-size:12px;line-height:1.5}
.route b{display:block;font-size:10px;letter-spacing:.1em;margin-bottom:3px;opacity:.66}
button{min-height:46px}
input,select{min-height:44px}
@media(max-width:680px){
  .wrap{padding:20px 16px 56px}
  .route{grid-template-columns:1fr}
  header h1{font-size:30px!important}
  .row>div{min-width:100%!important}
  button{width:100%}
}

body{background:#efece4;color:#17283b;font-family:"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:950px}
header{padding:8px 0 22px;border-bottom:2px solid #17283b;margin-bottom:22px}
header h1{font-family:"Songti SC","SimSun",Georgia,serif;font-size:42px;line-height:1.22;letter-spacing:-.02em}
header p{color:#626963;font-size:14px}
.card{background:#fbfaf6;border:1px solid #d8d0c0;border-radius:2px;box-shadow:none}
button{background:#17283b;border-radius:2px;letter-spacing:.06em}
button:hover{background:#24415e}
textarea,input,select{background:#fffdf8;border-color:#cfc5b4;border-radius:2px}
textarea:focus,input:focus,select:focus{border-color:#17283b;box-shadow:0 0 0 2px #d9d2c4}
.cost{grid-template-columns:1fr 1fr;gap:0;border-top:1px solid #b9ae9b;border-bottom:1px solid #b9ae9b}
.cost .cell{border:0;border-right:1px solid #d8d0c0;border-radius:0;padding:16px 18px;background:transparent}
.cost .cell:last-child{border-right:0}
.cost .cell b{font-family:"Cascadia Mono",Consolas,monospace;font-size:30px;letter-spacing:-.03em}
.item{border-left:3px solid #b06b32}
h3{text-transform:none;letter-spacing:.06em;color:#3d4b52}


/* palette-normalized */
:root{--bg:#efece4;--card:#fbfaf6;--ink:#17283b;--text:#17283b;--dim:#626963;--line:#d8d0c0;--accent:#b06b32}

/* polish-batch1 */
button.ghost,button.secondary{background:transparent!important;color:#b06b32!important;border:1px solid #d8d0c0!important}

/* mobile-32 */

@media(max-width:760px){
  main{grid-template-columns:1fr!important}
  aside{border-right:0!important;border-bottom:1px solid #d8d0c0;width:auto!important}
  section{padding:16px!important}
  .row{flex-wrap:wrap!important}
  .row>div{min-width:100%!important}
  h1{font-size:30px!important}
  .route{grid-template-columns:1fr!important}
  button{min-height:48px}
  pre{overflow-x:auto}
}

/* mobile-height */
@media(max-width:760px){main{min-height:auto!important}aside{min-height:0!important}}

/* mobile-sticky-cta */
.mobile-cta{display:none}
@media(max-width:760px){
  body{padding-bottom:88px}
  .route{grid-template-columns:repeat(3,minmax(0,1fr))!important}
  .route span{padding:8px 8px!important;font-size:11px!important;line-height:1.35!important}
  .route b{font-size:9px!important;margin-bottom:1px!important}
  .mobile-cta{position:fixed;left:12px;right:12px;bottom:12px;z-index:80;display:flex;
    padding:8px;border:1px solid rgba(0,0,0,.08);border-radius:12px;
    background:rgba(255,255,255,.94);backdrop-filter:blur(10px);
    box-shadow:0 10px 28px rgba(16,24,40,.18)}
  .mobile-cta button{width:100%;min-height:52px;margin:0!important;font-size:15px}
}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1><span class="prod">看房</span>这两套房，首月各要备多少 <span class="badge" id="status-badge">…</span></h1>
    <p class="lede">给近期要签租约的人：贴两段房源介绍，把押金、服务费、物业和一次性成本拆开。改租期，首月现金和摊到每月一起变。</p>
  </header>
  <div class="route" aria-label="任务步骤"><span><b>01</b>贴两段房源</span><span><b>02</b>拆五项成本</span><span><b>03</b>带走看房问题单</span></div>


  <div class="card">
    <label>房源文案(原样贴,含费用和话术)</label>
    <textarea id="text" rows="6" placeholder="例:望京 精装全配 一室一厅 45平 4500元/月 押一付三 中介费半个月 服务费300/月 水电燃气自理 随时看房"></textarea>
    <div class="row" style="margin-top:12px">
      <div><label>城市(对租金区间)</label>
        <select id="city">
          <option value="">不限</option>
          <option>北京</option><option>上海</option><option>深圳</option><option>广州</option>
          <option>杭州</option><option>南京</option><option>成都</option><option>武汉</option>
          <option>西安</option><option>重庆</option>
        </select></div>
      <div><label>月预算(可选)</label><input type="number" id="budget" placeholder="例:4000"></div>
      <div><label>通勤目的地(可选)</label><input type="text" id="commute" placeholder="例:天府三街"></div>
    </div>
    <div style="margin-top:16px"><button id="btn">开始核查</button></div>
    <div class="hint">费用与矛盾由本地规则算,模型只补语义矛盾和砍价话术;没有 key 也能用。</div>
  </div>

  <div class="result" id="res"></div>
</div>

<script>
const $ = s => document.querySelector(s);
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function money(v){ return v==null ? '—' : (Math.round(v*10)/10).toString() + ' 元'; }

function btnLoading(btn,on){ btn.disabled=on; if(on){ btn.dataset.label=btn.dataset.label||btn.textContent.trim(); btn.innerHTML='<span class="spin"></span>'; } else { btn.innerHTML=btn.dataset.label||btn.textContent; } }

$('#btn').onclick=async()=>{
  const el=$('#res'); el.classList.remove('show'); btnLoading($('#btn'),true);
  try{
    const r = await (await fetch('/api/scan',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:$('#text').value, city:$('#city').value,
                           budget:$('#budget').value, commute:$('#commute').value})})).json();
    if(r.error){ throw new Error(r.error); }
    const it=r.item;
    let h='<div class="card"><div class="hint">'+(r.mode==='model'?'模型已连接':'离线模式')+' · 已入库 #'+r.saved_id+'</div>';
    h+='<p style="font-size:15px;line-height:1.7">'+esc(r.summary)+'</p>';
    h+='<div class="cost">';
    h+='<div class="cell"><span>真实月成本(首年÷12)</span><b>'+money(it.monthly_cost)+'</b></div>';
    h+='<div class="cell"><span>首次支付(租金+押金+中介费)</span><b>'+money(it.first_payment)+'</b></div>';
    h+='<div class="cell"><span>月租</span><b>'+money(it.rent)+'</b><span>'+esc(it.rent_raw)+'</span></div>';
    h+='<div class="cell"><span>面积 / 折算单价</span><b>'+(it.area?it.area+' ㎡':'—')+'</b><span>'+(it.price_per_sqm?it.price_per_sqm+' 元/㎡·月':'—')+'</span></div>';
    h+='</div>';
    if(it.over_budget) h+='<p style="color:#b91c1c">⚠️ 真实月成本超出预算 '+money(it.budget)+'</p>';

    if(r.contradictions.length){ h+='<h3>疑点</h3>';
      r.contradictions.forEach(c=>{ const cls=c.level==='高'?'high':(c.level==='低'?'low':'');
        h+='<div class="item '+cls+'"><p><b>['+esc(c.level)+'] '+esc(c.kind)+'</b>:'+esc(c.detail)+'</p>';
        if(c.evidence) h+='<p class="ev">原文:'+esc(c.evidence)+'</p></div>'; }); }

    if(r.talks.length){ h+='<h3>话术命中</h3>';
      r.talks.forEach(t=>{ h+='<div class="item"><p><b>'+esc(t.phrase)+'</b>('+esc(t.risk)+')</p><p>'+esc(t.meaning)+'</p></div>'; }); }

    h+='<h3>看房清单</h3>';
    ['房间','费用','合同'].forEach(g=>{ const items=r.checklist.filter(c=>c.group===g); if(!items.length) return;
      h+='<p style="margin:10px 0 2px"><b>'+g+'</b></p>';
      items.forEach(c=>h+='<div class="q">□ '+esc(c.q)+'<i>风险:'+esc(c.risk)+'</i></div>'); });

    if(r.negotiation.length){ h+='<h3>砍价话术</h3>';
      r.negotiation.forEach(m=>h+='<div class="msg">'+esc(m)+'</div>'); }

    h+='<details><summary>签约前写进合同的 '+r.clauses.length+' 条</summary><ul>';
    r.clauses.forEach(c=>h+='<li><b>'+esc(c.clause)+'</b> —— '+esc(c.why)+'</li>');
    h+='</ul></details>';
    h+='</div>';
    el.innerHTML=h;
  }catch(e){ el.innerHTML='<div class="card" style="color:#b91c1c">请求失败:'+esc(e.message)+'</div>'; }
  el.classList.add('show'); btnLoading($('#btn'),false);
};

fetch('/api/status').then(r=>r.json()).then(s=>{
  const b=$('#status-badge');
  if(s.mock){ b.textContent='离线模式'; b.style.background='#fee2e2'; b.style.color='#b91c1c'; }
  else { b.textContent=s.model||'模型已连接'; b.style.background='#dcfce7'; b.style.color='#15803d'; }
  b.title='已存 '+s.scans+' 条房源';
}).catch(()=>{});
</script>

<script>
(function(){
  var want = "开始核查";
  function norm(t){ return String(t||'').replace(/\s+/g,''); }
  var all=[].slice.call(document.querySelectorAll('button'));
  var src=null, fallback=null;
  for(var i=0;i<all.length;i++){
    var b=all[i];
    if(b.offsetParent===null||b.disabled) continue;
    if(!fallback) fallback=b;
    if(norm(b.textContent).indexOf(norm(want))>=0){ src=b; break; }
  }
  if(!src) src=fallback;
  if(!src) return;
  var bar=document.createElement('div'); bar.className='mobile-cta';
  var btn=src.cloneNode(true); btn.removeAttribute('id'); btn.type='button';
  btn.addEventListener('click',function(e){e.preventDefault(); src.click();});
  bar.appendChild(btn); document.body.appendChild(bar);
})();
</script>
</body>
</html>
"""


def _make_handler(db, cfg, force_mock):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, ctype: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status: int = 200):
            self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8", status)

        def _read_json(self):
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("gbk", errors="replace")
            return json.loads(text) if text else {}

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/status":
                mock_mode = force_mock or not cfg.get("api_key")
                self._json({"mock": mock_mode, "model": None if mock_mode else cfg.get("model"),
                            "scans": len(db.latest(1000))})
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                body = self._read_json()
                if path == "/api/scan":
                    budget = body.get("budget")
                    budget = float(budget) if budget not in (None, "", "null") else None
                    report = agent.analyze(body.get("text", ""), city=body.get("city"),
                                           budget=budget, commute=body.get("commute"),
                                           config=cfg, db=db, force_mock=force_mock, save=True)
                    self._json(report)
                else:
                    self._json({"error": "not found"}, 404)
            except llm.LLMError as e:
                self._json({"error": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, 500)

        def log_message(self, *args):
            pass

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8000, db_path: str | None = None,
          force_mock: bool = False):
    db = dbmod.Database(db_path)
    rules.load_seed("talks.json")  # 启动即校验种子文件存在
    cfg = llm.default_config()
    handler = _make_handler(db, cfg, force_mock)
    httpd = ThreadingHTTPServer((host, port), handler)
    mode = "离线模式(未配置 key)" if (force_mock or not cfg.get("api_key")) else f"模型:{cfg.get('model')}"
    print(f"🏠 看房已启动:http://{host}:{port}  [{mode}]")
    print("   按 Ctrl+C 停止")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
