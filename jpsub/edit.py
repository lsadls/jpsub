"""译文调整系统:浏览器编辑 segments.json(改译文/删字幕/新增字幕),保存后可直接生成 ASS。

用法:`jpsub edit <工作目录或视频>`,模仿打码选取器的交互:
- 每行 = 一条字幕(时间轴 + 日文原文 + 中文译文),改动即写回 segments.json;
- 译文置空/删除行 = 删除该条字幕;`[[未译]]` 行橙色提示;
- 可新增字幕(时间轴+文本);时间轴输入兼容 mm:ss / 秒数,保存时统一为军方时间键;
- 「还原改动」从 OCR 原始备份 segments.orig.json 一键恢复;
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
button,input,textarea,select{background:#333;color:#ddd;border:1px solid #555;border-radius:4px;padding:3px 8px;font:inherit}
select option{background:#333;color:#ddd}
button{cursor:pointer}button:hover{background:#444}
input[type=text]{width:100%;box-sizing:border-box}
table{border-collapse:collapse;width:100%;table-layout:fixed}
th{position:sticky;top:0;background:#1e1e1e;z-index:1}
td,th{border:1px solid #444;padding:3px 6px;text-align:left;vertical-align:top}
tr.act{color:#8f8}
.jump{cursor:pointer;color:#8cf;padding:0 4px}
.del{color:#f88;cursor:pointer}
textarea.src,textarea.tr{overflow:hidden;resize:none;white-space:pre-wrap;word-break:break-all;overflow-wrap:anywhere}
.grp{display:inline-flex;gap:4px;align-items:center;border:1px solid #555;border-radius:6px;padding:4px 6px}
.src{color:#999}
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
<div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:6px;align-items:flex-start">
<span class=grp>
<button id=continue-tr title="翻译标为[[未译]]的句子,已译的跳过">续翻</button>
<button id=force-tr title="重新翻译全部句子(含已译)">重翻</button>
<label style=cursor:pointer><input type=checkbox id=hardtr>硬核</label>
</span>
<span class=grp><button id=save>保存</button> <button id=render>生成字幕</button></span>
<span class=grp>
<button id=b2a>转换</button>
<input type=file id=convpick accept=".bcc,.ass" style=display:none>
<button onclick=$('convpick').click() title="选择字幕文件(.bcc/.ass),供转换和烧录使用">选字幕…</button>
<input id=convf type=hidden value="">
@@BURN@@
</span>
</div>
<div class=grp style="flex-direction:column;align-items:stretch;width:300px;margin-top:6px">
<span><button id=restore>还原改动</button> <span class=hint>选中历史备份后点击还原</span></span>
<select id=bksel size=8 style="width:100%" title="历史备份:每次保存前自动生成;还原时选中其一即还原到该备份,不选则还原到 OCR 原始"></select>
</div>
<div id=msg></div>
<span style=color:#888>译文留空 = 删除该字幕;保存时未列出/已删的行不写回</span>
</div>
<div id=right>
<table id=list><tr><th>时间</th><th>原文</th><th>译文</th></tr></table>
</div>
</div>
<p>点时间旁的 ▶ 跳到该句开头;时间栏「←起」「止→」把起止设为当前预览时间,键盘 ←/→ ±0.1秒(Shift ±1秒,Alt ±5秒);「✕」删除该行字幕;「↺」把该句标为未译并清掉缓存旧译文。
「续翻」只翻译未译句子,「重翻」全部重翻,勾「硬核」则按「。」拆成单句逐句翻(单句翻坏不连累整条);翻完可点「生成字幕」;「转换」把所选 .bcc/.ass 互转,「烧录」把字幕烧进视频;「还原改动」从备份列表选中一份恢复。</p>
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
    if(r.text.trim()==='[[未译]]'||r.bad)tr.className+=' unt';
    tr.innerHTML=`<td><div class=trow>`+
      `<button class=jump data-i=${i} title=跳到该句开头>▶</button>`+
      `<input class=tin value='${fmt(r.start)}' data-k=start data-i=${i}>`+
      `<button class=st data-i=${i} data-k=start title=设为当前时间>←起</button>`+
      `<span>~</span>`+
      `<input class=tin value='${fmt(r.end)}' data-k=end data-i=${i}>`+
      `<button class=st data-i=${i} data-k=end title=设为当前时间>止→</button>`+
      `<button class=del data-i=${i} title=删除该字幕>✕</button>`+
      `<button class=unt data-i=${i} title=标为未译,配合主页「续翻」重翻该句>↺</button>`+
      `</div></td>`+
      `<td><textarea class=src data-i=${i} style=width:100%;box-sizing:border-box>${esc(r.src)}</textarea></td>`+
      `<td><textarea class=tr data-i=${i} style=width:100%;box-sizing:border-box>${esc(r.text)}</textarea></td>`;
    tb.appendChild(tr);
  });
  const fit=ta=>{ta.style.height='auto';ta.style.height=ta.scrollHeight+'px'};
  tb.querySelectorAll('textarea').forEach(ta=>{fit(ta);ta.oninput=()=>fit(ta)});
  tb.querySelectorAll('input.tin').forEach(inp=>inp.onchange=()=>{
    const v=parseT(inp.value);if(!isNaN(v))rows[+inp.dataset.i][inp.dataset.k]=v;
    syncList();});
  tb.querySelectorAll('button.st').forEach(b=>b.onclick=()=>{
    rows[+b.dataset.i][b.dataset.k]=+t.toFixed(2);syncList();});
  tb.querySelectorAll('textarea.src').forEach(ta=>ta.onchange=()=>{
    rows[+ta.dataset.i].src=ta.value});
  tb.querySelectorAll('textarea.tr').forEach(ta=>ta.onchange=()=>{
    rows[+ta.dataset.i].text=ta.value});
  tb.querySelectorAll('.del').forEach(d=>d.onclick=()=>{rows.splice(+d.dataset.i,1);syncList();});
  tb.querySelectorAll('button.unt').forEach(b=>b.onclick=async()=>{
    const i=+b.dataset.i;
    const j=await post2('/unt',{i:i,rows:rows});
    if(j.ok){rows[i].text='[[未译]]';syncList();msg.textContent='已标为未译,去主页点「续翻」重翻该句'}
    else msg.textContent='失败:'+j.err;
  });
  tb.querySelectorAll('.jump').forEach(n=>n.onclick=()=>setT(rows[+n.dataset.i].start));
}
$('add').onclick=()=>{
  const row={start:+t.toFixed(2),end:+(t+2).toFixed(2),src:'',text:''};
  let idx=rows.findIndex(r=>r.start>t);
  if(idx<0)idx=rows.length;
  rows.splice(idx,0,row);
  syncList();msg.textContent='已新增,记得填写时间与译文'};
$('b2a').onclick=async()=>{
  if(!$('convf').value.trim())return msg.textContent='请先点「选字幕…」选择 .bcc/.ass 文件';
  msg.textContent='转换中...';
  const j=await post2('/conv',{file:$('convf').value.trim()});
  msg.textContent=j.ok?'完成:'+j.out:'失败:'+j.err;
};
function post2(path,body){
  return fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)}).then(r=>r.json());
}
async function save(){
  const r=await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(rows)});
  const j=await r.json();msg.textContent=j.ok?`已保存 ${j.n} 条`:'保存失败:'+j.err;
  if(j.ok)loadBks();
  return j.ok;
}
$('save').onclick=save;
async function pollTr(){
  while(true){
    const j=await(await fetch('/trstatus')).json();
    msg.textContent=j.log[j.log.length-1]||'';
    if(!j.busy){
      if(j.log.some(l=>l.startsWith('✅')))location.reload();
      return;
    }
    await new Promise(r=>setTimeout(r,1000));
  }
}
async function startTr(force){
  if(!await save())return;
  const j=await post2('/translate',{hard:$('hardtr').checked,force:force});
  if(!j.ok)return msg.textContent='失败:'+j.err;
  msg.textContent='翻译启动中...';
  pollTr();
}
$('continue-tr').onclick=()=>startTr(false);
$('force-tr').onclick=()=>startTr(true);
$('render').onclick=async()=>{
  if(!await save())return;
  msg.textContent='生成字幕中...';
  const j=await(await fetch('/render',{method:'POST'})).json();
  msg.textContent=j.ok?'完成:'+j.out:'失败:'+j.err;
};
$('restore').onclick=async()=>{
  const bk=$('bksel').value;
  if(!confirm(bk?'确定还原到备份 '+bk+'?未保存的改动会丢失':'确定还原到 OCR 原始结果?所有编辑(译文/增删)都会撤销'))return;
  const j=await post2('/restore',{name:bk||''});
  if(j.ok)location.reload();
  else msg.textContent='失败:'+j.err;
};
async function loadBks(){
  const j=await(await fetch('/backups')).json();
  const s=$('bksel');s.innerHTML='';
  (j.names||[]).forEach(n=>{
      const o=document.createElement('option');o.value=n;o.textContent=n;s.appendChild(o);});
}
loadBks();
$('convpick').onchange=e=>{
  if(e.target.files.length)$('convf').value=e.target.files[0].name;
};
const burn=document.getElementById('burn');
if(burn)burn.onclick=async()=>{
  if(!await save())return;
  burn.disabled=true;msg.textContent='烧录中(ffmpeg,需要一会儿)...';
  const j=await post2('/burn',{file:$('convf')?$('convf').value.trim():''});
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
let dead=false;  // 服务端退出后全屏遮罩提示
setInterval(async()=>{
  if(dead)return;
  try{await fetch('/ping')}
  catch(e){dead=true;
    const d=document.createElement('div');
    d.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.78);color:#eee;display:flex;align-items:center;justify-content:center;font-size:22px;z-index:9999';
    d.textContent='程序已退出,请关闭此页面';
    document.body.appendChild(d);}
},1000);
addEventListener('pagehide',()=>navigator.sendBeacon('/quit'));
</script>"""


def _read_rows(work: Path) -> list[dict]:
    """读 segments.json 为行列表,按时间排序;未译段显示占位标记。"""
    segs = handoff.read_segments(work / "segments.json")
    rows = [
        {
            "start": s.start,
            "end": s.end,
            "src": s.text or "",
            "text": s.tr or "" if s.tr and not handoff.is_untranslated(s.tr) else handoff.UNTRANSLATED_MARK,
            "bad": bool(s.tr and handoff.is_junk_tr(s.tr)),  # 混入拒绝语:显示原文但标橙,续翻会重翻
        }
        for s in segs
    ]
    rows.sort(key=lambda r: (r["start"], r["end"]))
    return rows


def _save_rows(work: Path, rows: list[dict]) -> int:
    """行列表写回 segments.json:译文置空/删除行 = 删除该字幕;
    译文为占位标记 = 保持未译。新增行(src 为空)文本即译文。"""
    from . import cli
    from .segment import Segment

    segs: list[Segment] = []
    for r in rows:
        r2 = handoff.parse_key(f"{fmt_t(r['start'])}-{fmt_t(r['end'])}")
        if not r2:
            continue
        start, end = r2
        src = str(r.get("src", "")).strip()
        t = str(r.get("text", "")).strip()
        if t == handoff.UNTRANSLATED_MARK:
            segs.append(Segment(start, end, src, tr=None))  # 保持未译
            continue
        if not t or "\t" in t or "\n" in t:
            continue  # 空/损坏行不写 = 删除
        segs.append(Segment(start, end, src or t, tr=t))
    segs.sort(key=lambda s: s.start)
    cli._backup(work, work / "segments.json")  # 覆盖前备份
    handoff.write_segments(
        segs,
        work / "segments.json",
        comment=handoff.read_meta(work / "segments.json").get("comment"),
    )
    return len(segs)


def _tr_worker(editor, argv: list[str]) -> None:
    """后台翻译线程:跑 cli._translate,输出经共享 LineCapture 收集进 tr_log。"""
    import contextlib

    from . import cli as _cli
    from . import progress

    def _on_line(line: str) -> None:
        log = editor.tr_log
        if line.startswith("[第 ") and log and log[-1].startswith("[第 "):
            log[-1] = line  # 翻译进度原地刷新,不刷屏
        else:
            log.append(line)
            if len(log) > 500:
                del log[:-500]

    editor.tr_busy = True
    editor.tr_log = []
    try:
        with (
            contextlib.redirect_stdout(progress.LineCapture(_on_line)),
            contextlib.redirect_stderr(progress.LineCapture(_on_line)),
        ):
            _cli._translate(_cli.parse_args(argv))
        editor.tr_log.append("✅ 已完成")
    except SystemExit as e:
        editor.tr_log.append(f"❌ {e}")
    except Exception as e:  # noqa: BLE001
        editor.tr_log.append(f"❌ {e}")
    finally:
        editor.tr_busy = False


def _find_video(work: Path) -> Path | None:
    """新布局:视频在工作目录内;旧布局:工作目录旁。"""
    from .cli import _find_video as _cli_find

    return _cli_find(work)


def _make_ass_style(subs) -> None:
    """给 SSAFile 配置与 ass.write_ass 相同的 Default 样式。"""
    import pysubs2

    from . import settings

    style = pysubs2.SSAStyle()
    style.fontname = "Noto Sans CJK SC"
    style.fontsize = 54
    style.primarycolor = pysubs2.Color(255, 255, 255, 0)
    r, g, b = settings.OUTLINE_COLOR
    style.outlinecolor = pysubs2.Color(r, g, b, 0)
    style.outline = settings.OUTLINE_WIDTH
    style.shadow = settings.SHADOW
    subs.styles["Default"] = style


def _bcc2ass(src: Path) -> Path:
    """必剪 .bcc(JSON) → .ass,输出在源文件旁。"""
    import pysubs2

    data = json.loads(src.read_text(encoding="utf-8"))
    subs = pysubs2.SSAFile()
    _make_ass_style(subs)
    for ev in data.get("body", []):
        text = str(ev.get("content", "")).replace("\n", "\\N").strip()
        if not text:
            continue
        subs.append(pysubs2.SSAEvent(
            start=int(float(ev.get("from", 0)) * 1000),
            end=int(float(ev.get("to", 0)) * 1000),
            text=text,
        ))
    out = src.with_suffix(".ass")
    subs.save(str(out), encoding="utf-8")
    return out


def _ass2bcc(src: Path) -> Path:
    """.ass → 必剪 .bcc(JSON),输出在源文件旁。"""
    import pysubs2

    subs = pysubs2.load(str(src), encoding="utf-8")
    body = [
        {
            "from": round(e.start / 1000, 3),
            "to": round(e.end / 1000, 3),
            "location": 1,
            "content": e.plaintext.strip(),
        }
        for e in subs.events
        if not e.is_comment and e.plaintext.strip()
    ]
    out = src.with_suffix(".bcc")
    out.write_text(json.dumps({
        "float": 1, "font_size": 0.4, "font_opacity": 70,
        "spatial_type": 0.6, "line_alignment": 1,
        "font_border_width": 0, "font_border_opacity": 70,
        "shadow_width": 0, "font_spacing": 0,
        "body": body,
    }, ensure_ascii=False), encoding="utf-8")
    return out


class _Editor:
    def __init__(self, work: Path):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.work = work
        self.video = _find_video(work)
        self.duration = video_duration(self.video) if self.video else 0.0
        self.render_msg = ""
        self.tr_busy = False  # 翻译任务进行中标记
        self.tr_log: list[str] = []  # 翻译任务输出(进度)

        import time
        self._last = time.time()  # 最近一次页面请求时间(判断页面是否已关闭)

        def _maybe_quit():
            """页面关闭后 2s 内没有新请求才退出(排除刷新误触)。"""
            if time.time() - editor._last > 1.5:
                self.srv.shutdown()

        def _touch():
            editor._last = time.time()

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
                _touch()
                if self.path == "/ping":
                    self._json({})
                elif self.path == "/trstatus":
                    self._json({"busy": editor.tr_busy, "log": editor.tr_log[-8:]})
                elif self.path == "/backups":
                    bdir = editor.work / "backup"
                    names = (
                        sorted(
                            (
                                p.name
                                for p in bdir.iterdir()
                                if p.is_dir() and (p / "segments.json").is_file()
                            ),
                            reverse=True,
                        )
                        if bdir.is_dir()
                        else []
                    )
                    self._json({"ok": True, "names": names})
                elif self.path == "/":
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

            def _resolve_file(self, f: str) -> Path:
                """按文件选择器的值找文件:绝对路径直用,否则在工作目录/工作目录旁找。"""
                f = str(f).strip()
                src = Path(f) if Path(f).is_absolute() else None
                if src is None:
                    for base in (editor.work, editor.work.parent):
                        if (base / f).exists():
                            return base / f
                    src = editor.work / f
                if not src.is_file():
                    raise FileNotFoundError(f"找不到文件:{src}")
                return src

            def do_POST(self):
                _touch()
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n)
                if self.path == "/quit":
                    self._json({"ok": True})
                    threading.Timer(2.0, _maybe_quit).start()
                elif self.path == "/save":
                    try:
                        rows = json.loads(body)
                        cnt = _save_rows(editor.work, rows)
                        editor.render_msg = ""
                        self._json({"ok": True, "n": cnt})
                    except Exception as e:  # noqa: BLE001
                        self._json({"ok": False, "err": str(e)})
                elif self.path == "/unt":
                    # 标为未译:该行译文清空写回 segments,并删除缓存里的旧译文,
                    # 之后主页「续翻」按钮只会重翻这句
                    try:
                        b = json.loads(body)
                        rows = b["rows"]
                        i = int(b["i"])
                        rows[i]["text"] = handoff.UNTRANSLATED_MARK
                        _save_rows(editor.work, rows)
                        from .cache import TranslationCache

                        cache = TranslationCache(editor.work / "cache.json")
                        cache.remove(str(rows[i].get("src", "")))
                        cache.save()
                        self._json({"ok": True})
                    except Exception as e:  # noqa: BLE001
                        self._json({"ok": False, "err": str(e)})
                elif self.path == "/translate":
                    # 后台线程跑翻译(续翻/重翻),进度经 /trstatus 轮询
                    if editor.tr_busy:
                        self._json({"ok": False, "err": "已有翻译任务在进行"})
                    else:
                        b = json.loads(body) if body else {}
                        argv = ["translate", str(editor.work)]
                        if b.get("hard"):
                            argv.append("--hard")
                        if b.get("force"):
                            argv.append("--force")
                        threading.Thread(
                            target=_tr_worker, args=(editor, argv), daemon=True
                        ).start()
                        self._json({"ok": True})
                elif self.path == "/conv":
                    try:
                        src = self._resolve_file(json.loads(body).get("file", ""))
                        ext = src.suffix.lower()
                        if ext == ".bcc":
                            out = _bcc2ass(src)
                        elif ext == ".ass":
                            out = _ass2bcc(src)
                        else:
                            raise ValueError("扩展名必须是 .bcc 或 .ass")
                        self._json({"ok": True, "out": str(out)})
                    except Exception as e:  # noqa: BLE001
                        self._json({"ok": False, "err": str(e)})
                elif self.path == "/render":
                    try:
                        out = editor.run_render()
                        self._json({"ok": True, "out": str(out)})
                    except Exception as e:  # noqa: BLE001
                        self._json({"ok": False, "err": str(e)})
                elif self.path == "/restore":
                    try:
                        name = ""
                        try:
                            name = str(json.loads(body).get("name", "") or "").strip()
                        except Exception:  # noqa: BLE001
                            pass
                        if name:
                            src = editor.work / "backup" / name / "segments.json"
                            if not src.is_file():
                                raise FileNotFoundError(f"找不到备份:{src}")
                            from . import cli
                            from .segment import Segment as _Seg

                            segs = handoff.read_segments(src)
                            cur = editor.work / "segments.json"
                            cli._backup(editor.work, cur)  # 还原前备份当前状态
                            handoff.write_segments(
                                [
                                    _Seg(s.start, s.end, s.text, s.tr)
                                    for s in segs
                                ],
                                cur,
                                comment=handoff.read_meta(cur).get("comment"),
                            )
                            n = len(segs)
                        else:
                            n = handoff.restore_from_orig(editor.work)
                        self._json({"ok": True, "n": n})
                    except FileNotFoundError as e:
                        self._json({"ok": False, "err": str(e)})
                    except Exception as e:  # noqa: BLE001
                        self._json({"ok": False, "err": str(e)})
                elif self.path == "/burn":
                    try:
                        f = ""
                        try:
                            f = str(json.loads(body).get("file", "") or "").strip()
                        except Exception:  # noqa: BLE001
                            pass
                        ass_file = self._resolve_file(f) if f else None
                        out = editor.run_burn(ass_file)
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

    def run_burn(self, ass_file: Path | None = None) -> Path:
        from . import cli

        if not self.video:
            raise SystemExit("错误:找不到视频文件,无法烧录")
        if ass_file is None:
            ass = self.run_render()
        else:
            # 文件选择器指定的字幕:.bcc 先转 .ass,再直接烧录
            ass = _bcc2ass(ass_file) if ass_file.suffix.lower() == ".bcc" else ass_file
        video = self.video
        masks = self.work / "masks.json"
        if not masks.exists():  # 兼容旧版 masks.txt
            old = self.work / "masks.txt"
            if old.exists():
                masks = old
        if masks.is_file():  # 与 home「烧录」一致:先打码再烧字幕
            from .mask import apply_masks

            out = cli._product_out(video, ".masked.mp4")
            print(f"先应用打码:{masks} -> {out}")
            apply_masks(video, masks, out)
            video = out
        return cli._burn(video, ass)


def _render_page(p: _Editor) -> str:
    page = _PAGE
    for key, val in {
        "@@TITLE@@": p.work.name,
        "@@STEM@@": p.work.name[: -len(".jpsub")],
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
    if not (target / "segments.json").exists():
        raise SystemExit(f"错误:{target} 里没有 segments.json,先运行 extract")
    # 旧目录一次性迁移:translate-out.txt 译文补进 segments(有 out 才生效)
    from .cache import TranslationCache

    handoff.import_out_to_segments(target, TranslationCache(target / "cache.json"))
    e = _Editor(target)
    url = f"http://127.0.0.1:{e.srv.server_address[1]}/"
    print(f"译文调整器:{url}(浏览器未自动打开时手动访问;关闭页面即退出)")
    threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        e.srv.serve_forever()
    except KeyboardInterrupt:
        pass
