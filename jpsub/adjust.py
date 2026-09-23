"""译文调整系统:浏览器编辑 translate-out.txt(改译文/删字幕/新增字幕),保存后可直接生成 ASS。

用法:`jpsub edit <工作目录或视频>`,模仿打码选取器的交互:
- 每行 = 一条字幕(时间轴 + 日文原文 + 中文译文),译文改动即写回 out 文件;
- 译文置空/删除行 = 删除该条字幕(out 缺行即删除,与 render 语义一致);
- 可新增字幕(时间轴+文本);时间轴输入兼容 mm:ss / 秒数,保存时统一为军方时间键;
- 「生成字幕」按钮等价于 `jpsub render <工作目录>`。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from . import handoff
from .mask import fmt_t, frame_jpeg, video_duration

_PAGE = """<!doctype html><html lang=zh><meta charset=utf-8>
<title>译文调整</title><style>
body{font:14px sans-serif;margin:12px;background:#1e1e1e;color:#ddd}
#main{display:flex;gap:16px;align-items:flex-start}
#left{flex:0 0 auto}
canvas{display:block;background:#000;width:min(46vw,80vh);height:auto}
#right{flex:1;min-width:0;max-height:calc(100vh - 90px);overflow-y:auto}
button,input,textarea{background:#333;color:#ddd;border:1px solid #555;border-radius:4px;padding:3px 8px;font:inherit}
button{cursor:pointer}button:hover{background:#444}
input[type=text]{width:100%;box-sizing:border-box}
table{border-collapse:collapse;width:100%;table-layout:fixed}
th{position:sticky;top:0;background:#1e1e1e;z-index:1}
td,th{border:1px solid #444;padding:3px 6px;text-align:left;vertical-align:top}
tr.act{color:#8f8}
.jump{cursor:pointer;color:#8cf;padding:0 4px}
.del{color:#f88;cursor:pointer}
.src{color:#999;white-space:pre-wrap;word-break:break-all;overflow-wrap:anywhere}
td:nth-child(1),th:nth-child(1){width:200px}
tr.unt .tr{color:#f80}
#msg{color:#fc6;min-height:1.2em}
#right{flex:1;min-width:0;max-height:calc(100vh - 90px);overflow-y:auto}
.trow{display:flex;gap:3px;align-items:center;flex-wrap:wrap}
input.tin{width:60px}
</style>
<h3>译文调整 - @@TITLE@@</h3>
<div id=main>
<div id=left>
<canvas id=cv width=@@W@@ height=@@H@@></canvas>
<div style=margin-top:4px>
时间 <input id=t type=text value=0:00.0 style=width:70px> / @@DURF@@
<input type=range id=slider min=0 max=@@DUR@@ step=0.1 value=0 style=width:220px>
</div>
<div style=margin-top:6px><button id=add>＋在当前时间新增字幕</button></div>
<div style=margin-top:6px>
<button id=save>保存</button> <button id=render>生成字幕</button>
@@BURN@@
<div id=msg></div>
<span style=color:#888>译文留空 = 删除该字幕;保存时未列出/已删的行不写回</span>
</div>
</div>
<div id=right>
<table id=list><tr><th>时间</th><th>原文</th><th>译文</th></tr></table>
</div>
</div>
<p>点时间旁的 ▶ 跳到该句开头;时间栏「←起」「止→」把起止设为当前预览时间;键盘 ←/→ ±0.1秒(Shift ±1秒,Alt ±5秒)。</p>
<script>
const DUR=@@DUR@@,HASVID=@@HASVID@@;
const cv=document.getElementById('cv'),ctx=cv.getContext('2d');
const msg=document.getElementById('msg');
let rows=@@ROWS@@, t=0, img=null;
const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
const fmt=s=>`${Math.floor(s/60)}:${(s%60).toFixed(1).padStart(4,'0')}`;
const parseT=s=>{s=String(s).trim();if(/^\\d+(\\.\\d+)?$/.test(s))return+s;
  const p=s.split(':');if(p.length>3||p.some(v=>v===''))return NaN;
  return p.reduce((a,v)=>a*60+(+v||0),0)};
async function loadFrame(){
  if(!HASVID)return;
  const im=new Image();im.src='/frame?t='+t.toFixed(3);
  try{await im.decode()}catch(e){return}
  img=im;ctx.canvas.width=im.naturalWidth;ctx.canvas.height=im.naturalHeight;
  ctx.drawImage(im,0,0);
}
function cur(){return rows.findIndex(r=>r.start<=t&&t<=r.end)}
function syncList(){
  const tb=$('list');tb.innerHTML='<tr><th>时间</th><th>原文</th><th>译文</th></tr>';
  const ai=cur();
  rows.forEach((r,i)=>{
    const tr=document.createElement('tr');if(i===ai)tr.className='act';
    if(r.text.trim()==='[[未译]]')tr.className+=' unt';
    tr.innerHTML=`<td><div class=trow>`+
      `<button class=jump data-i=${i} title=跳到该句开头>▶</button>`+
      `<input class=tin value='${fmt(r.start)}' data-k=start data-i=${i}>`+
      `<button class=st data-i=${i} data-k=start title=设为当前时间>←起</button>`+
      `<span>~</span>`+
      `<input class=tin value='${fmt(r.end)}' data-k=end data-i=${i}>`+
      `<button class=st data-i=${i} data-k=end title=设为当前时间>止→</button>`+
      `<button class=del data-i=${i} title=删除该字幕>✕</button>`+
      `</div></td>`+
      `<td class=src>${esc(r.src)}</td>`+
      `<td><textarea class=tr data-i=${i} rows=2 style=width:100%;box-sizing:border-box>${esc(r.text)}</textarea></td>`;
    tb.appendChild(tr);
  });
  tb.querySelectorAll('input.tin').forEach(inp=>inp.onchange=()=>{
    const v=parseT(inp.value);if(!isNaN(v))rows[+inp.dataset.i][inp.dataset.k]=v;
    syncList();});
  tb.querySelectorAll('button.st').forEach(b=>b.onclick=()=>{
    rows[+b.dataset.i][b.dataset.k]=+t.toFixed(2);syncList();});
  tb.querySelectorAll('textarea.tr').forEach(ta=>ta.onchange=()=>{
    rows[+ta.dataset.i].text=ta.value});
  tb.querySelectorAll('.del').forEach(d=>d.onclick=()=>{rows.splice(+d.dataset.i,1);syncList();});
  tb.querySelectorAll('.jump').forEach(n=>n.onclick=()=>setT(rows[+n.dataset.i].start));
}
$('add').onclick=()=>{
  const row={start:+t.toFixed(2),end:+(t+2).toFixed(2),src:'',text:''};
  let idx=rows.findIndex(r=>r.start>t);
  if(idx<0)idx=rows.length;
  rows.splice(idx,0,row);
  syncList();msg.textContent='已新增,记得填写时间与译文'};
async function save(){
  const r=await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(rows)});
  const j=await r.json();msg.textContent=j.ok?`已保存 ${j.n} 条`:'保存失败:'+j.err;return j.ok;
}
$('save').onclick=save;
$('render').onclick=async()=>{
  if(!await save())return;
  msg.textContent='生成字幕中...';
  const j=await(await fetch('/render',{method:'POST'})).json();
  msg.textContent=j.ok?'完成:'+j.out:'失败:'+j.err;
};
const burn=document.getElementById('burn');
if(burn)burn.onclick=async()=>{
  if(!await save())return;
  burn.disabled=true;msg.textContent='烧录中(渲染+ffmpeg,需要一会儿)...';
  const j=await(await fetch('/burn',{method:'POST'})).json();
  burn.disabled=false;msg.textContent=j.ok?'完成:'+j.out:'失败:'+j.err;
};
function scrollList(){
  const box=$('right');if(!box)return;
  let idx=cur();
  if(idx<0)idx=rows.findIndex(r=>r.start>=t);
  if(idx<0)return;
  const tr=box.querySelectorAll('table tr')[idx+1]; // +1 跳过表头行
  if(!tr)return;
  const top=tr.getBoundingClientRect().top-box.getBoundingClientRect().top;
  box.scrollTop+=top-30; // 表头吸顶高度
}
function setT(v){t=Math.min(DUR,Math.max(0,v));$('t').value=fmt(t);$('slider').value=t;loadFrame();syncList();scrollList();}
$('t').onchange=()=>{const v=parseT($('t').value);if(!isNaN(v))setT(v)};
$('slider').oninput=()=>{t=+$('slider').value;$('t').value=fmt(t);loadFrame();syncList();scrollList()};
document.onkeydown=e=>{
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA')return;
  const step=e.shiftKey?1:(e.altKey?5:0.1);
  if(e.key==='ArrowLeft'){setT(t-step);e.preventDefault()}
  else if(e.key==='ArrowRight'){setT(t+step);e.preventDefault()}
};
syncList();loadFrame();
</script>"""


def _read_rows(work: Path) -> list[dict]:
    """合并 in(原文)与 out(译文)为行列表,按时间排序。"""
    in_map = (
        handoff.pending_map_from_in(work / "translate-in.txt")
        if (work / "translate-in.txt").exists()
        else {}
    )
    out_map: dict[str, str] = {}
    out_path = work / "translate-out.txt"
    if out_path.exists():
        for ln in out_path.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            k, _, t = ln.partition("\t")
            k = handoff.norm_key(k.strip())
            if k not in out_map:  # 重复键取首行
                out_map[k] = t
    keys = list(dict.fromkeys(list(out_map) + list(in_map)))
    rows = []
    for k in keys:
        r = handoff.parse_key(k)
        start, end = r if r else (float("inf"), float("inf"))
        rows.append(
            {
                "key": k,
                "start": start,
                "end": end,
                "src": in_map.get(k, ""),
                "text": out_map.get(k, ""),
            }
        )
    rows.sort(key=lambda r: (r["start"], r["end"]))
    return rows


def _save_rows(work: Path, rows: list[dict]) -> int:
    items = []
    for r in rows:
        r2 = handoff.parse_key(f"{fmt_t(r['start'])}-{fmt_t(r['end'])}")
        k = handoff.make_key(*r2) if r2 else str(r.get("key", "")).strip()
        if not k:
            continue
        t = str(r.get("text", "")).strip()
        if not t or "\t" in t or "\n" in t:
            continue  # 空/损坏行不写 = 删除
        items.append((r2[0] if r2 else float("inf"), f"{k}\t{t}"))
    items.sort(key=lambda x: x[0])
    (work / "translate-out.txt").write_text(
        "".join(ln + "\n" for _, ln in items), encoding="utf-8"
    )
    return len(items)


def _find_video(work: Path) -> Path | None:
    for ext in (".mp4", ".mkv", ".webm"):
        v = work.with_suffix(ext)
        if v.exists():
            return v
    return None


class _Editor:
    def __init__(self, work: Path):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.work = work
        self.video = _find_video(work)
        self.duration = video_duration(self.video) if self.video else 0.0
        self.render_msg = ""

        editor = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj):
                body = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/":
                    body = _render_page(editor).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path.startswith("/frame?"):
                    import urllib.parse

                    q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    t = float(q.get("t", ["0"])[0])
                    try:
                        data = frame_jpeg(editor.video, t)
                    except Exception:
                        self.send_error(500)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_error(404)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n)
                if self.path == "/save":
                    try:
                        rows = json.loads(body)
                        cnt = _save_rows(editor.work, rows)
                        editor.render_msg = ""
                        self._json({"ok": True, "n": cnt})
                    except Exception as e:  # noqa: BLE001
                        self._json({"ok": False, "err": str(e)})
                elif self.path == "/render":
                    try:
                        out = editor.run_render()
                        self._json({"ok": True, "out": str(out)})
                    except Exception as e:  # noqa: BLE001
                        self._json({"ok": False, "err": str(e)})
                elif self.path == "/burn":
                    try:
                        out = editor.run_burn()
                        self._json({"ok": True, "out": str(out)})
                    except Exception as e:  # noqa: BLE001
                        self._json({"ok": False, "err": str(e)})
                else:
                    self.send_error(404)
        for p in range(8775, 8785):
            try:
                self.srv = ThreadingHTTPServer(("127.0.0.1", p), H)
                port = p
                break
            except OSError:
                continue
        else:
            raise SystemExit("错误:8775~8784 端口都被占用")

    def rows(self) -> list[dict]:
        return _read_rows(self.work)

    def run_render(self) -> Path:
        from . import cli

        args = cli.parse_args(["render", str(self.work)])
        return cli._render(args)

    def run_burn(self) -> Path:
        from . import cli

        if not self.video:
            raise SystemExit("错误:找不到视频文件,无法烧录")
        ass = self.run_render()
        return cli._burn(self.video, ass)


def _render_page(p: _Editor) -> str:
    page = _PAGE
    for key, val in {
        "@@TITLE@@": p.work.name,
        "@@DURF@@": fmt_t(p.duration) if p.video else "(无视频预览)",
        "@@DUR@@": f"{p.duration:.3f}" if p.video else "1",
        "@@W@@": "640" if p.video else "0",
        "@@H@@": "360" if p.video else "0",
        "@@HASVID@@": "true" if p.video else "false",
        "@@BURN@@": ('<button id=burn>烧录进视频</button>' if p.video else ""),
        "@@ROWS@@": json.dumps(
            [{k: r[k] for k in ("start", "end", "src", "text")} for r in p.rows()],
            ensure_ascii=False,
        ),
    }.items():
        page = page.replace(key, val)
    return page


def editor(target: Path) -> None:
    """启动浏览器译文调整器。target = 工作目录(<视频>.jpsub)或视频文件。"""
    import webbrowser

    target = Path(target)
    if target.is_file():
        target = target.parent / (target.stem + ".jpsub")
    if not target.is_dir():
        raise SystemExit(f"错误:工作目录不存在:{target}")
    if (
        not (target / "translate-in.txt").exists()
        and not (target / "translate-out.txt").exists()
    ):
        raise SystemExit(f"错误:{target} 里没有 translate-in/out.txt,先运行 extract")
    e = _Editor(target)
    url = f"http://127.0.0.1:{e.srv.server_address[1]}/"
    print(f"译文调整器:{url}(浏览器未自动打开时手动访问;Ctrl+C 退出)")
    threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        e.srv.serve_forever()
    except KeyboardInterrupt:
        pass
