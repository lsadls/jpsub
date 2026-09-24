"""主页系统:浏览器总控台,选择视频/输入 sm 号后用按钮发起各种操作。

用法:`jpsub home`,模仿译文调整器的交互:
- 自动列出 output/ 下的视频与 <视频>.jpsub 工作目录(部分刷新,不整页重载);
- 输入 sm 号/URL 点「下载并翻译」后台跑完整流水线;
- 输入本地视频路径点「翻译」后台跑 run;
- 列表每行按钮:编辑(译文调整器)/ 打码(选取器)/ 烧录(--burn);
- 任务在后台子进程执行,页面轮询进度与完成状态。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from . import cli, handoff

VID_EXTS = (".mp4", ".mkv", ".webm")

# 任务标识用:子命令 → 操作名(任务显示为「视频名 操作」)
_OP = {
    "download": "下载",
    "run": "翻译",
    "extract": "抽帧",
    "render": "生成字幕",
    "translate": "续翻",
    "edit": "编辑",
    "mask": "打码",
    "maskapply": "打码应用",
    "burn": "烧录",
}

_PAGE = """<!doctype html><html lang=zh><meta charset=utf-8>
<title>jpsub 主页</title><style>
body{font:14px sans-serif;margin:16px;background:#1e1e1e;color:#ddd}
button,input{background:#333;color:#ddd;border:1px solid #555;border-radius:4px;padding:4px 10px;font:inherit}
button{cursor:pointer}button:hover{background:#444}
input[type=text]{box-sizing:border-box}
table{border-collapse:collapse;width:100%;margin-top:8px}
#listbox{max-height:50vh;overflow-y:auto}
th,td{border:1px solid #444;padding:4px 8px;text-align:left}
th{background:#252525}
tr.sel{background:#3a3a26}
.badge{display:inline-block;padding:0 6px;border-radius:8px;font-size:12px;margin-left:4px}
.ok{background:#363;color:#cfc}.warn{background:#a62;color:#fed}
.job{padding:2px 0}.job.run{color:#fc6}.job.done{color:#8c8}.job.fail{color:#f88}
h3{margin:0 0 6px;border-bottom:1px solid #444;padding-bottom:4px}
#grid{display:grid;grid-template-columns:1fr 1fr;gap:14px 20px;margin-top:10px;align-items:start}
.pane{border:1px solid #444;border-radius:6px;padding:10px}
#jobs{margin-top:6px;max-height:40vh;overflow-y:auto;font-size:13px}
.row{display:flex;gap:6px;align-items:center;margin-top:8px;flex-wrap:wrap}
.row input[type=text]{flex:1;min-width:100px;width:auto}
.lbl{white-space:nowrap}
button{white-space:nowrap}
.btns{margin-top:6px;display:flex;flex-wrap:wrap;gap:4px}
.grp{margin-top:12px;color:#aaa}
.outbox{margin-top:6px;border:1px solid #444;border-radius:4px;background:#181818;padding:6px;max-height:45vh;overflow-y:auto}
.outbox pre{margin:0;font:12px monospace;color:#9c9;white-space:pre-wrap}
.job{padding:2px 4px;cursor:pointer}
.job.sel{background:#3a3a26}
</style>
<h2>jpsub 主页</h2>
<div id=grid>
<div class=pane>
<h3>① 操作</h3>
<div class=row>
<span class=lbl>sm号或URL</span>
<input id=url type=text placeholder="如 sm43168834">
</div>
<div class=btns>
<button onclick=cmd('download',[])>下载并翻译</button>
<button onclick=cmd('download',['--notrans'])>下载不翻译</button>
<button onclick=cmd('download',['--burn'])>下载+烧录</button>
</div>
<div class=row>
<span class=lbl>本地视频</span>
<input id=vfile type=file accept=".mp4,.mkv,.webm,video/mp4,video/x-matroska,video/webm" style=display:none>
<button onclick=$('vfile').click()>选视频…</button>
<span id=locline style="color:#888;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">未选择</span>
</div>
<div class=btns>
<button onclick=cmd('run',[])>翻译(run)</button>
<button onclick=cmd('run',['--burn'])>翻译+烧录</button>
<button onclick=cmd('run',['--notrans'])>OCR</button>
<button onclick=cmd('run',['--force'])>重新OCR翻译</button>
<button onclick=cmd('extract',['--force'])>重新OCR</button>
</div>
<div class=btns>
<button onclick=cmd('edit',[])>编辑</button>
<button onclick=cmd('mask',[])>打码</button>
<button onclick=cmd('burn',[])>烧录</button>
<button onclick=cmd('render',[])>生成字幕</button>
<button onclick=cmd('translate',[])>续翻</button>
<button onclick=cmd('translate',['--force'])>重翻</button>
<button onclick=delWork()>删除工作目录</button>
</div>
</div>
<div class=pane>
<h3>② output/ 视频与工作目录（点击行选中） <button onclick=openDir()>打开文件夹</button></h3>
<div id=listbox><table id=list><tr><th style=width:50%>名称</th><th>状态</th></tr></table></div>
</div>
<div class=pane>
<h3>③ 批量脚本（等价 jpsub -s,每行一条调用,# 注释）</h3>
<textarea id=stext rows=10 placeholder="sm43168834
https://www.nicovideo.jp/watch/sm12345678 --comment 剧场
# 一行一个任务,# 是注释" style="width:100%;box-sizing:border-box;background:#333;color:#ddd;border:1px solid #555;border-radius:4px;font:inherit;padding:4px 8px;resize:vertical"></textarea>
<div style=margin-top:6px>
<button onclick=runScript()>运行脚本</button>
自定义参数(可选) <input id=extra type=text style=width:220px placeholder="--fps 2.0 --comment 剧场">
</div>
<div class=outbox><pre id=sout>(输出显示在这里)</pre></div>
</div>
<div class=pane>
<h3>④ 任务（点击选中,可终止） <button onclick=killJob()>终止选中</button></h3>
<div id=jobs></div>
</div>
</div>
<div style="color:#888;font-size:13px;line-height:2">
<b style=color:#aaa>① 下载组</b><br>
<b>下载并翻译</b> — 完整流水线:下载→抽帧→OCR→翻译→生成ASS<br>
<b>下载不翻译</b> — --notrans,停在译文待编辑<br>
<b>下载+烧录</b> — --burn,字幕直接烧进视频<br>
<b style=color:#aaa>① 本地视频组</b><br>
<b>翻译(run)</b> — 本地视频完整流水线<br>
<b>翻译+烧录</b> — run --burn,已有ASS直接烧<br>
<b>OCR</b> — --notrans,抽帧+OCR产出 segments.json,不翻译<br>
<b>重新OCR翻译</b> — run --force,无视已有结果重跑全流程<br>
<b>重新OCR</b> — extract --force,只重跑抽帧OCR<br>
<b style=color:#aaa>① 对选中项</b><br>
<b>编辑</b> — 浏览器译文调整器(可一键还原OCR原始结果)<br>
<b>打码</b> — 打码选取器(生成masks.json)<br>
<b>烧录</b> — 把ASS烧进视频;有masks.json时自动先打码再烧字幕<br>
<b>生成字幕</b> — render,用现有译文生成ASS<br>
<b>续翻</b> — translate,只翻没翻过的句子<br>
<b>重翻</b> — translate --force,忽略译文和缓存全部重翻<br>
<b>删除工作目录</b> — 删除选中条目的 <名称>.jpsub(字幕/译文全删,视频保留)<br>
<b style=color:#aaa>其他</b><br>
<b>② 打开文件夹</b> — 用系统文件管理器打开 output 目录<br>
<b>③ 运行脚本</b> — 每行一条任务(等价 jpsub -s)批量执行,输出显示在下方<br>
<b>④ 终止选中</b> — 结束选中的任务;自定义参数框的内容会追加到所有命令后<br>
force 操作与覆盖旧文件前都会自动备份到工作目录 backup/时间戳/ 文件夹
</div>
<script>
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
const $=id=>document.getElementById(id);
async function post(path,body){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  return r.json();
}
let selName=null;
function setSel(n){selName=n;$('selname').textContent=n||'未选择';
  document.querySelectorAll('#list tr').forEach(tr=>
    tr.classList.toggle('sel',tr.dataset.name===n));}
async function delWork(){
  if(!selName)return alert('请先在 ② 点击选中一个条目');
  if(!confirm('确定删除 '+selName+' 的工作目录?\\n字幕、译文等将全部删除,视频保留'))return;
  const j=await post('/del',{name:selName});
  if(!j.ok)alert('失败:'+j.err);else{setSel(null);refresh()}
}
async function openDir(){
  const j=await post('/open',{});
  if(!j.ok)alert('失败:'+j.err);
}
async function runScript(){
  const t=$('stext').value;
  if(!t.trim())return alert('脚本为空');
  const j=await post('/script',{text:t});
  if(!j.ok)alert('失败:'+j.err);else refreshJobs();
}
let localPath='';
function pickDone(p){localPath=p;$('locline').textContent=p}
$('vfile').onchange=async e=>{  // 同打码选图片:原生对话框选文件 -> 上传临时目录
  const f=e.target.files[0];if(!f)return;
  const line=$('locline');const old=localPath;
  line.textContent='上传中…';
  try{
    const r=await fetch('/upload-video?name='+encodeURIComponent(f.name),
      {method:'POST',body:f});
    const j=await r.json();
    if(!j.ok)throw new Error(j.err||'上传失败');
    pickDone(j.path);
  }catch(err){pickDone(old);alert('失败:'+err.message)}
  e.target.value='';
};
async function cmd(sub,flags){
  const j=await post('/cmd',{sub,flags,name:selName,
    url:$('url').value.trim(),path:localPath,
    extra:$('extra').value.trim()});
  if(!j.ok)alert('失败:'+j.err);else{refresh();refreshJobs()}
}
let jobsLen=-1,selJob=-1;
async function refreshJobs(){
  const j=await(await fetch('/jobs')).json();
  const box=$('jobs');
  if(!j.jobs.length){box.innerHTML='';jobsLen='';selJob=-1;return}
  jobsLen=j.n;
  box.innerHTML=j.jobs.map((x,i)=>`<div class="job ${x.st}${i===selJob?' sel':''}" ${x.st==='fail'&&x.line?`title="${esc(x.line)}"`:''} onclick=pickJob(${i})>`+
    (x.st==='run'?'⏳ ':x.st==='done'?'✅ ':'❌ ')+
    (x.label?`<b>${esc(x.label)}</b> `:'')+
    esc(x.st==='run'?(x.line||'启动中…'):x.st==='done'?'已完成':(x.line?`失败:${x.line}`:'失败'))+`</div>`).join('');
  const sj=j.jobs.find(x=>x.lines&&x.lines.length);
  const so=$('sout');
  so.textContent=sj?sj.lines.join('\\n'):'(输出显示在这里)';
  so.scrollTop=so.scrollHeight;
}
function pickJob(i){selJob=i;refreshJobs()}
async function killJob(){
  if(selJob<0)return alert('请先在 ④ 点击选中一个任务');
  const j=await post('/kill',{idx:selJob});
  if(!j.ok)alert('失败:'+j.err);else refreshJobs();
}
async function refresh(){
  const j=await(await fetch('/list')).json();
  const tb=$('list');
  tb.innerHTML='<tr><th style=width:50%>名称</th><th>状态</th></tr>';
  j.items.forEach(it=>{
    const tr=document.createElement('tr');
    tr.dataset.name=it.name;
    if(it.name===selName)tr.classList.add('sel');
    let badge='';
    if(!it.work)badge='<span class="badge warn">未抽取</span>';
    else if(it.ass)badge='<span class="badge ok">已生成字幕</span>';
    if(it.unt>0)badge+=` <span class="badge warn">${it.unt} 条未译</span>`;
    tr.innerHTML=`<td>${esc(it.name)}${badge}</td>`+
      `<td>${it.video?'有视频':'无视频'}${it.work?' / 有工作目录':''}</td>`;
    tr.onclick=()=>setSel(it.name);
    tb.appendChild(tr);
  });
}
refresh();refreshJobs();
setInterval(refresh,3000);setInterval(refreshJobs,1000);
</script>"""


def _scan_output(root: Path) -> list[dict]:
    """扫描 output/,以 <视频>.jpsub 工作目录为主条目。

    新布局:视频/ASS 在工作目录内;旧布局(工作目录旁)也兼容识别。
    """
    stems: dict[str, dict] = {}
    if not root.is_dir():
        return []
    for p in sorted(root.iterdir()):
        if p.name.startswith(("_", ".")):
            continue
        if p.is_dir() and p.name.endswith(".jpsub"):
            stems.setdefault(
                p.name[: -len(".jpsub")], {"name": p.name[: -len(".jpsub")]}
            )
        elif p.is_file() and p.suffix.lower() in VID_EXTS:
            d = stems.setdefault(p.stem, {"name": p.stem})  # 旧布局散视频
            d["legacy_video"] = True
    items = []
    for d in stems.values():
        name = d["name"]
        work = root / (name + ".jpsub")
        video = None
        for base in (work, root):  # 新布局优先,旧布局回退
            for ext in VID_EXTS:
                v = base / (name + ext)
                if v.exists():
                    video = v
                    break
            if video:
                break
        d["video"] = video is not None
        d.setdefault("work", work.is_dir())
        d["ass"] = (work / (name + ".ass")).exists() or (
            root / (name + ".ass")
        ).exists()
        unt = 0
        if work.is_dir():
            seg_file = work / "segments.json"
            if seg_file.exists():
                try:
                    segs = handoff.read_segments(seg_file)
                    from .cache import TranslationCache

                    cache = TranslationCache(work / "cache.json")
                    unt = sum(
                        1
                        for s in segs
                        if s.text
                        and handoff.is_untranslated(s.tr or "")
                        and cache.get(s.text) is None
                    )
                except Exception:  # noqa: BLE001
                    unt = 0
        d["unt"] = unt
        items.append(d)
    items.sort(key=lambda d: d["name"])
    return items


class _Home:
    def __init__(self, root: Path):
        import contextlib
        import queue
        import re

        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.root = root
        self.jobs: list[dict] = []  # {proc|inline, desc, st, line}
        home = self

        def _sanitize(line: str) -> str:
            """去 ANSI 颜色码和进度条图形,只留名称+百分比+数字。"""
            line = re.sub(r"\x1b\[[0-9;]*m", "", line)
            m = re.match(r"^(.*?):\s*(\d+)%\|[^|]*\|\s*([^[\s]+)", line)
            if m:
                return f"{m.group(1).strip()} {m.group(2)}% ({m.group(3)})"
            return line

        # ---- 进程内任务队列:OCR 模型只加载一次,任务串行复用 ----
        self._engine = None

        def _get_engine():
            if home._engine is None:
                from .ocr import PaddleOcrEngine

                home._engine = PaddleOcrEngine()
            return home._engine

        def _cap_write(j: dict, buf: list, s: str) -> None:
            buf[0] += s
            parts = re.split(r"[\r\n]", buf[0])
            buf[0] = parts[-1]
            for p in parts[:-1]:
                line = _sanitize(p.strip())
                if line:
                    j["line"] = line
                    if j.get("show"):
                        lines = j.setdefault("lines", [])
                        lines.append(line)
                        del lines[:-200]

        class _Cap:
            """线程安全的 stdout 替身:print 进度转成 j['line'](仅内联任务运行期间生效)。"""

            def __init__(self, j: dict):
                self.j = j
                self.buf = [""]
                self._real = sys.stdout

            def write(self, s: str) -> int:
                if self._real is not None:
                    try:
                        self._real.write(s)  # 同时落到服务器终端,便于排查
                    except Exception:  # noqa: BLE001
                        pass
                if self.j.get("cancel"):
                    raise RuntimeError("已被用户终止")
                _cap_write(self.j, self.buf, s)
                return len(s)

            def flush(self):
                pass

        def _worker():
            while True:
                j, args = home._q.get()
                if j.get("cancel"):
                    j["st"] = "fail"
                    j["line"] = "已终止"
                    home._q.task_done()
                    continue
                home._cur = j
                try:
                    if home._engine is None:
                        j["line"] = "加载 OCR 模型中…"
                    with (
                        contextlib.redirect_stdout(_Cap(j)),
                        contextlib.redirect_stderr(_Cap(j)),  # tqdm 走 stderr
                    ):
                        cli.run(args, engine=home)  # home 自己充当引擎代理
                    j["st"] = "done"
                    j["line"] = "已完成"
                except SystemExit as e:  # noqa: BLE001
                    j["st"] = "fail"
                    j["line"] = str(e) or "失败"
                except Exception as e:  # noqa: BLE001
                    j["st"] = "fail"
                    j["line"] = f"{e}"
                finally:
                    home._cur = None
                    home._q.task_done()
                    if home._q.empty():
                        home._engine = None  # 空闲即卸载模型,释放内存

        self._q = queue.Queue()
        self._cur = None
        threading.Thread(target=_worker, daemon=True).start()

        # 引擎代理接口(cli.run 需要 engine.run(path))
        def engine_run(path: str) -> str:
            j = home._cur
            if j is not None and j.get("cancel"):
                raise RuntimeError("已被用户终止")
            return _get_engine().run(path)

        self.run = engine_run

        def _spawn_inline(args, op: str = "") -> None:
            tgt = getattr(args, "video", None) or getattr(args, "work", None)
            base = Path(tgt).name if tgt else "内联"
            j = {
                "proc": None,
                "inline": True,
                "desc": "内联",
                "st": "run",
                "label": f"{base} {op}".strip(),
                "line": "排队/加载模型中…",
                "show": False,
            }
            home.jobs.append(j)
            home._q.put((j, args))

        def _pump(proc: subprocess.Popen, j: dict) -> None:
            """后台线程:读子进程输出,按 \r/\n 切行,保留最后一行作进度。

            show 任务(如 -s 脚本)额外保留最近 200 行供页面输出区显示。
            """
            import os as _os
            import re

            buf = b""
            fd = proc.stdout.fileno()
            while True:
                try:
                    chunk = _os.read(fd, 4096)  # 有数据就返回,不等凑满(实时进度)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                parts = re.split(rb"[\r\n]", buf)
                buf = parts[-1]
                for p in parts[:-1]:
                    line = _sanitize(p.decode("utf-8", "replace").strip())
                    if line:
                        j["line"] = line
                        if j.get("show"):
                            lines = j.setdefault("lines", [])
                            lines.append(line)
                            del lines[:-200]

        def spawn(
            desc: str,
            cmd: list[str],
            label: str = "",
            kind: str = "long",
            show: bool = False,
        ) -> int:
            """kind: long=长任务等退出; launch=启动型(编辑/打码,常驻),立即标完成。"""
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            if kind == "launch":
                j = {
                    "proc": proc,
                    "desc": desc,
                    "st": "done",
                    "label": label,
                    "line": "已在浏览器打开",
                    "show": show,
                }
            else:
                j = {
                    "proc": proc,
                    "desc": desc,
                    "st": "run",
                    "label": label,
                    "line": "",
                    "show": show,
                }
            home.jobs.append(j)
            threading.Thread(target=_pump, args=(proc, j), daemon=True).start()
            return len(home.jobs) - 1

        self.spawn = spawn

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
                    body = _PAGE.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/list":
                    self._json({"items": _scan_output(home.root)})
                elif self.path.startswith("/upload-video"):
                    # 原生文件对话框选中的视频上传到临时目录(同打码选图片机制)
                    import tempfile
                    import urllib.parse

                    q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    name = Path(q.get("name", ["video.mp4"])[0].replace("\\", "/")).name
                    if Path(name).suffix.lower() not in VID_EXTS:
                        self._json({"ok": False, "err": f"不是视频文件:{name}"})
                        return
                    d = Path(tempfile.gettempdir()) / "jpsub_home_vid"
                    d.mkdir(exist_ok=True)
                    dst = d / name
                    try:
                        n = int(self.headers.get("Content-Length", 0))
                        with open(dst, "wb") as fp:  # 分块写盘,不占内存
                            rem = n
                            while rem > 0:
                                chunk = self.rfile.read(min(1 << 20, rem))
                                if not chunk:
                                    break
                                fp.write(chunk)
                                rem -= len(chunk)
                        self._json({"ok": True, "path": str(dst)})
                    except Exception as e:  # noqa: BLE001
                        self._json({"ok": False, "err": str(e)})
                elif self.path == "/jobs":
                    for j in home.jobs:
                        if (
                            j["proc"] is not None
                            and j["st"] == "run"
                            and j["proc"].poll() is not None
                        ):
                            j["st"] = "done" if j["proc"].returncode == 0 else "fail"
                    self._json(
                        {
                            "n": len(home.jobs),
                            "jobs": [
                                {
                                    "idx": i,
                                    "st": j["st"],
                                    "label": j["label"],
                                    "line": j["line"],
                                    "lines": j.get("lines", [])
                                    if j.get("show")
                                    else [],
                                }
                                for i, j in enumerate(home.jobs)
                            ],
                        }
                    )
                else:
                    self.send_error(404)

            def do_POST(self):
                import shlex

                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                exe = [sys.executable, "-m", "jpsub.cli"]

                def extra() -> list[str]:
                    """自定义参数框:shlex 解析(支持引号),解析失败忽略。"""
                    try:
                        return shlex.split(str(body.get("extra", "")).strip())
                    except ValueError:
                        return []

                try:
                    if self.path == "/open":
                        # 用系统文件管理器打开 output 目录(Windows/Linux/mac 兼容)
                        import subprocess as sp

                        if sys.platform == "win32":
                            sp.Popen(["explorer", str(home.root)])
                        elif sys.platform == "darwin":
                            sp.Popen(["open", str(home.root)])
                        else:
                            sp.Popen(["xdg-open", str(home.root)])
                        self._json({"ok": True})
                    elif self.path == "/del":
                        name = str(body.get("name", "")).strip()
                        work = home.root / (name + ".jpsub")
                        if not name or not work.is_dir():
                            self._json({"ok": False, "err": "工作目录不存在"})
                            return
                        import shutil

                        shutil.rmtree(work)
                        self._json({"ok": True})
                    elif self.path == "/kill":
                        idx = int(body.get("idx", -1))
                        if not 0 <= idx < len(home.jobs):
                            self._json({"ok": False, "err": "请先在 ④ 选中一个任务"})
                            return
                        j = home.jobs[idx]
                        if j["proc"] is None:  # 内联任务:排队/运行中打取消标记
                            j["cancel"] = True
                            self._json({"ok": True, "note": "将在下一个 OCR 阶段终止"})
                            return
                        if j["proc"].poll() is not None:
                            self._json({"ok": False, "err": "该任务已结束"})
                            return
                        j["proc"].terminate()
                        self._json({"ok": True})
                    elif self.path == "/script":
                        text = str(body.get("text", ""))
                        if not text.strip():
                            self._json({"ok": False, "err": "脚本为空"})
                            return
                        path = home.root / ".home-batch.txt"
                        path.write_text(text, encoding="utf-8")
                        home.spawn(
                            "批量脚本",
                            [*exe, "-s", str(path)],
                            label="批量脚本",
                            show=True,
                        )
                        self._json({"ok": True})
                    elif self.path == "/cmd":
                        # 通用子命令入口:{sub, flags[], url, path, name}
                        sub = str(body.get("sub", "")).strip()
                        flags = [str(f) for f in body.get("flags", [])]
                        url = str(body.get("url", "")).strip()
                        name = str(body.get("name", "")).strip()
                        work = home.root / (name + ".jpsub")
                        video = home.root / name
                        for base in (work, home.root):  # 新布局优先,旧布局回退
                            for ext in VID_EXTS:
                                if (base / (name + ext)).exists():
                                    video = base / (name + ext)
                                    break
                            if video.is_file():
                                break

                        def need(cond: bool, err: str) -> bool:
                            if not cond:
                                self._json({"ok": False, "err": err})
                                return False
                            return True

                        if sub in ("download", "run", "extract"):
                            if sub == "download":
                                if not need(bool(url), "请先在 ① 填写 sm 号或 URL"):
                                    return
                                home.spawn(
                                    f"{url} {_OP[sub]}",
                                    [
                                        *exe,
                                        "download",
                                        url,
                                        "-o",
                                        str(home.root),
                                        *flags,
                                        *extra(),
                                    ],
                                    label=f"{url} {_OP[sub]}",
                                )
                            else:
                                # 视频来源优先级:① 下拉框(名称或路径) > ② 选中条目
                                v = str(body.get("path", "")).strip()
                                p = Path(v) if v else None
                                if p is not None and not p.is_file():
                                    p = None  # 下拉框传的是名称,不是路径
                                    name = v
                                if p is None:
                                    name = name or str(body.get("name", "")).strip()
                                    w2 = home.root / (name + ".jpsub")
                                    for base in (w2, home.root):
                                        for ext in VID_EXTS:
                                            cand = base / (name + ext)
                                            if cand.is_file():
                                                p = cand
                                                break
                                        if p:
                                            break
                                if not need(
                                    p is not None,
                                    "请先在 ① 选择视频,或在 ② 选中条目",
                                ):
                                    return
                                # run/extract 进程内执行:OCR 模型只加载一次,复用
                                _spawn_inline(
                                    cli.parse_args([sub, str(p), *flags, *extra()]),
                                    op=_OP.get(sub, sub),
                                )
                        elif sub in ("render", "translate", "edit"):
                            if not need(work.is_dir(), "请先在 ② 选择有工作目录的条目"):
                                return
                            home.spawn(
                                f"{name} {_OP[sub]}",
                                [*exe, sub, str(work), *flags, *extra()],
                                label=f"{name} {_OP[sub]}",
                                kind="launch" if sub == "edit" else "long",
                            )
                        elif sub in ("mask", "maskapply", "burn"):
                            if not need(video.is_file(), "请先在 ② 选择有视频的条目"):
                                return
                            if sub == "burn" and (
                                (work / "masks.json").is_file()
                                or (work / "masks.txt").is_file()
                            ):
                                # 链式:工作目录里有打码清单 → 先打码再烧字幕
                                pre = ["maskapply", str(video), "--burn"]
                                op = "打码+烧录"
                            elif sub == "burn":
                                pre = ["run", str(video), "--burn"]
                                op = _OP[sub]
                            else:
                                pre = [sub, str(video)]
                                op = _OP[sub]
                            home.spawn(
                                f"{name} {op}",
                                [*exe, *pre, *flags, *extra()],
                                label=f"{name} {op}",
                            )
                        else:
                            self._json({"ok": False, "err": f"未知子命令:{sub}"})
                            return
                        self._json({"ok": True})
                    else:
                        self.send_error(404)
                except Exception as e:  # noqa: BLE001
                    self._json({"ok": False, "err": str(e)})

        for p in range(8790, 8800):
            try:
                self.srv = ThreadingHTTPServer(("127.0.0.1", p), H)
                break
            except OSError:
                continue
        else:
            raise SystemExit("错误:8790~8799 端口都被占用")


def home_page(root: Path | None = None) -> None:
    """启动浏览器主页。root = 产物根目录(默认 output/)。"""
    root = root or cli._output_root()
    root.mkdir(parents=True, exist_ok=True)
    h = _Home(root)
    url = f"http://127.0.0.1:{h.srv.server_address[1]}/"
    print(f"jpsub 主页:{url}(浏览器未自动打开时手动访问;Ctrl+C 退出)")
    threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        h.srv.serve_forever()
    except KeyboardInterrupt:
        pass
