"""打码系统:鼠标框选时间轴区域 -> masks.json -> ffmpeg 应用马赛克/纯色/图片。

masks.json 为 JSON 数组,每条:{"start","end","x","y","w","h","effect"}
effect 可为: blur[:强度] / color:RRGGBB / 图片路径(png/jpg)
坐标为原视频像素,区域在起止时间内全程生效。旧版 masks.txt 文本格式仍可读取。
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from . import settings


@dataclass
class MaskEntry:
    start: float
    end: float
    x: int
    y: int
    w: int
    h: int
    effect: str  # blur / blur:N / color:RRGGBB / 图片路径


def _t(s: str) -> float:
    """时刻:裸数字=秒,也支持 分:秒 / 时:分:秒。"""
    s = s.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return float(s)
    parts = s.split(":")
    if all(re.fullmatch(r"\d+(?:\.\d+)?", p) for p in parts) and 2 <= len(parts) <= 3:
        nums = [float(p) for p in parts]
        return (
            (nums[0] * 3600 + nums[1] * 60 + nums[2])
            if len(nums) == 3
            else nums[0] * 60 + nums[1]
        )
    raise ValueError(f"无法识别的时刻:{s!r}")


def _upload_dir() -> Path:
    """上传图片的临时目录(不污染视频所在目录)。"""
    d = Path(tempfile.gettempdir()) / "jpsub_mask_img"
    d.mkdir(exist_ok=True)
    return d


def parse_masks(path: Path) -> list[MaskEntry]:
    """读打码清单:masks.json(JSON);旧 masks.txt 文本格式兼容读取。"""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text[0] == "[":  # JSON 格式
        items = json.loads(text)
        return [
            MaskEntry(
                float(m["start"]),
                float(m["end"]),
                int(m["x"]),
                int(m["y"]),
                int(m["w"]),
                int(m["h"]),
                str(m["effect"]),
            )
            for m in items
        ]
    return _parse_txt_masks(text)


def _parse_txt_masks(text: str) -> list[MaskEntry]:
    """旧版 masks.txt:每行 `起-止<TAB>X,Y,W,H<TAB>效果`。"""
    out = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        span, pos, effect = (ln.split("\t") + ["", ""])[:3]
        s, _, e = span.partition("-")
        x, y, w, h = (int(v) for v in pos.replace("*", ",").split(","))
        out.append(MaskEntry(_t(s), _t(e), x, y, w, h, effect.strip()))
    return out


def save_masks(entries: list[MaskEntry], path: Path) -> None:
    entries = sorted(entries, key=lambda m: m.start)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "start": m.start,
                    "end": m.end,
                    "x": m.x,
                    "y": m.y,
                    "w": m.w,
                    "h": m.h,
                    "effect": m.effect,
                }
                for m in entries
            ],
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )


def video_size(video: Path) -> tuple[int, int]:
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(video),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    w, h = r.stdout.strip().split(",")[:2]
    return int(w), int(h)


def video_duration(video: Path) -> float:
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(video),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(r.stdout.strip())


def build_filter(
    entries: list[MaskEntry], base: Path | None = None
) -> tuple[str, list[Path]]:
    """生成 filter_complex 与按顺序追加的图片输入列表;图片相对路径基于 base。"""
    chains: list[str] = []
    images: list[Path] = []
    cur = "0:v"
    base = base or Path.cwd()
    for i, m in enumerate(entries):
        nxt = f"v{i + 1}"
        en = f"enable='between(t,{m.start:g},{m.end:g})'"
        eff = m.effect
        if eff == "blur" or eff.startswith("blur:"):
            # boxblur=R:2:0:0 只模糊亮度通道(跳过色度,效果更好且不受色度限制);
            # luma radius 上限还随区域尺寸变化(约 min(w,h)/5),一并钳制
            radius = max(
                2, min(58, int(eff.partition(":")[2] or 20), min(m.w, m.h) // 5)
            )
            chains.append(
                f"[{cur}]split[sa{i}][sb{i}];"
                f"[sb{i}]crop={m.w}:{m.h}:{m.x}:{m.y},boxblur={radius}:2:0:0[bb{i}];"
                f"[sa{i}][bb{i}]overlay={m.x}:{m.y}:{en}[{nxt}]"
            )
        elif eff.startswith("color:"):
            rgb = eff.partition(":")[2].lstrip("#")
            chains.append(
                f"[{cur}]drawbox=x={m.x}:y={m.y}:w={m.w}:h={m.h}:"
                f"color=0x{rgb}@1:t=fill:{en}[{nxt}]"
            )
        else:
            img = Path(eff)
            if not img.is_absolute():
                img = base / img
            if not img.exists():
                raise SystemExit(f"错误:图片不存在:{img}")
            idx = 1 + len(images)
            images.append(img)
            chains.append(f"[{cur}][{idx}:v]overlay={m.x}:{m.y}:{en}[{nxt}]")
        cur = nxt
    return ";".join(chains), images


def apply_masks(video: Path, masks_path: Path, output: Path, progress=None) -> Path:
    """应用打码;progress(ffmpeg进度行文本) 回调用于网页显示进度。"""
    entries = parse_masks(masks_path)
    if not entries:
        raise SystemExit(f"错误:{masks_path} 里没有打码条目")
    fc, images = build_filter(entries, masks_path.parent)
    cmd = [settings.binary("ffmpeg"), "-y", "-nostdin", "-i", str(video)]
    for img in images:
        cmd += ["-i", str(img)]
    cmd += [
        "-filter_complex",
        fc,
        "-map",
        f"[v{len(entries)}]",
        "-map",
        "0:a?",
        "-c:a",
        "copy",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd.append(str(output))
    print(f"应用 {len(entries)} 条打码:{video} -> {output}")
    if progress is None:
        subprocess.run(cmd, check=True)
    else:
        cmd += ["-progress", "pipe:1", "-nostats"]
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        cur: dict[str, str] = {}
        for ln in p.stdout:
            k, _, v = ln.strip().partition("=")
            if not _:
                continue
            cur[k] = v
            if k == "progress":  # 每块结尾键,此时该块信息齐全
                parts = [
                    f"{k2}={cur[k2]}"
                    for k2 in ("out_time", "fps", "speed")
                    if k2 in cur
                ]
                progress(" ".join(parts) if parts else ln.strip())
                cur = {}
        if p.wait() != 0:
            raise RuntimeError(f"ffmpeg 失败(退出码 {p.returncode})")
    print(f"完成:{output}")
    return output


# ---------------------------------------------------------------- 选取器(浏览器)

_PAGE = """<!doctype html><html lang=zh><meta charset=utf-8>
<title>打码选取</title><style>
body{font:14px sans-serif;margin:12px;background:#1e1e1e;color:#ddd}
#main{display:flex;gap:16px;align-items:flex-start}
#left{flex:0 0 auto}
#wrap{position:relative;display:inline-block}canvas{display:block;background:#000;width:min(46vw,80vh);height:auto}
#overlay{position:absolute;left:0;top:0;width:100%;height:100%;cursor:crosshair}
#right{flex:1;min-width:0;max-height:calc(100vh - 60px);overflow-y:auto}
button,input,select{background:#333;color:#ddd;border:1px solid #555;border-radius:4px;padding:3px 8px;font:inherit}
button{cursor:pointer}button:hover{background:#444}
table{border-collapse:collapse;width:100%}
th{position:sticky;top:0;background:#1e1e1e;z-index:1}
td,th{border:1px solid #444;padding:2px 6px;text-align:left}
tr.act{color:#8f8}.del{color:#f88;cursor:pointer;padding:0 4px}
.num{cursor:pointer;color:#8cf;text-decoration:underline}
#msg{color:#fc6;min-height:1.2em;clear:both}
.hint{color:#888;margin-top:6px}
</style>
<h3>打码选取 - @@TITLE@@</h3>
<div id=main>
<div id=left>
<div id=wrap><canvas id=cv></canvas><div id=overlay></div></div>
<div style=margin-top:4px>
时间 <input id=t type=text value=0:00.0 style=width:70px> / @@DURF@@
<input type=range id=slider min=0 max=@@DUR@@ step=0.1 value=0 style=width:220px>
</div>
<div style=margin-top:6px>
效果 <select id=eff><option value=blur>模糊</option><option value=color>纯色</option><option value=image>图片</option></select>
强度 <input id=strength type=number value=20 min=2 max=58 style=width:56px>
<input id=color type=color value=#000000>
<button id=pickimg>选图片…</button><span id=selimg style=color:#888></span>
<input id=imgfile type=file accept="image/*" style=display:none>
</div>
<div style=margin-top:6px>
<button id=save>保存</button> <button id=apply>应用打码</button>
<span class=hint style=margin-left:8px>列表序号可点击跳转;拖框新增,框内左键移动/右键调大小</span>
</div>
<div class=hint style=margin-top:4px>新条目默认 当前帧 ~ 当前帧+5秒;时间可改(支持 mm:ss.s 或秒数)。<br>
键盘:←/→ ±0.1秒(Shift ±1秒,Alt ±5秒),PageUp/PageDown ±10秒,Home/End 跳到首尾。</div>
</div>
<div id=right>
<table id=list><tr><th>#</th><th>时间</th><th>区域</th><th>效果</th><th></th></tr></table>
</div>
</div>
<div id=msg></div>
<script>
const W=@@W@@,H=@@H@@,DUR=@@DUR@@;
const cv=document.getElementById('cv'),ctx=cv.getContext('2d');
const ov=document.getElementById('overlay'),msg=document.getElementById('msg');
let masks=@@MASKS@@, t=0, img=null, drag=null, hover=null, applying=false;
const $=id=>document.getElementById(id);
const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;');
const fmt=s=>`${Math.floor(s/60)}:${(s%60).toFixed(1).padStart(4,'0')}`;
const parseT=s=>{s=s.trim();if(/^\\d+(\\.\\d+)?$/.test(s))return+s;
  const p=s.split(':');if(p.length>3)return NaN;
  return p.reduce((a,v)=>a*60+(+v||0),0)};
async function loadFrame(){
  const im=new Image();im.src='/frame?t='+t.toFixed(3);
  await im.decode();img=im;draw();
}
function active(m){return m.start<=t&&t<=m.end}
function sc(){return cv.getBoundingClientRect().width/W}
const imgCache={};
function imgFor(name){
  if(!imgCache[name]){const im=new Image();im.src='/img?name='+encodeURIComponent(name);imgCache[name]=im}
  return imgCache[name];
}
function draw(){
  if(!img)return;
  const tmp=document.createElement('canvas');tmp.width=W;tmp.height=H;
  tmp.getContext('2d').drawImage(img,0,0,W,H);
  for(const m of masks){ if(!active(m))continue;
    const e=m.effect;
    if(e==='blur'||e.startsWith('blur:')){
      const req=parseFloat((e.split(':')[1]||20));
      const r=Math.max(2,Math.min(58,req,Math.floor(Math.min(m.w,m.h)/5)));
      const sub=document.createElement('canvas');sub.width=m.w;sub.height=m.h;
      sub.getContext('2d').drawImage(tmp,m.x,m.y,m.w,m.h,0,0,m.w,m.h);
      const c=tmp.getContext('2d');c.save();c.filter=`blur(${r}px)`;
      c.drawImage(sub,m.x,m.y);c.restore();
    }else if(e.startsWith('color:')){
      tmp.getContext('2d').fillStyle='#'+e.slice(6);
      tmp.getContext('2d').fillRect(m.x,m.y,m.w,m.h);
    }else{
      const im=imgFor(e);
      if(im.complete&&im.naturalWidth)tmp.getContext('2d').drawImage(im,m.x,m.y,m.w,m.h);
      else im.decode().then(draw).catch(()=>{});
    }
    const c=tmp.getContext('2d');
    c.strokeStyle='#0f0';c.strokeRect(m.x,m.y,m.w,m.h);
    c.font='bold 22px sans-serif';
    c.fillStyle='#0f0';c.strokeStyle='#000';c.lineWidth=3;c.textBaseline='top';
    const idx=masks.indexOf(m)+1;
    c.strokeText(idx,m.x+4,m.y+4);c.fillText(idx,m.x+4,m.y+4);
  }
  if(hover){const c=tmp.getContext('2d');c.strokeStyle='#f44';c.setLineDash([5,3]);
    c.strokeRect(Math.min(hover[0],hover[2]),Math.min(hover[1],hover[3]),
      Math.abs(hover[2]-hover[0]),Math.abs(hover[3]-hover[1]));c.setLineDash([]);}
  ctx.canvas.width=W;ctx.canvas.height=H;ctx.drawImage(tmp,0,0);
}
function syncList(){
  const tb=$('list');tb.innerHTML='<tr><th>#</th><th>时间</th><th>区域</th><th>效果</th><th></th></tr>';
  masks.forEach((m,i)=>{
    const tr=document.createElement('tr');if(active(m))tr.className='act';
    tr.innerHTML=`<td class=num data-i=${i} title=点击跳转>${i+1}</td>`+
      `<td><input value='${fmt(m.start)}' data-k=start data-i=${i} style=width:64px>`+
      `<button class=st data-i=${i} title=设为当前时间>←起</button>`+
      ` ~ <input value='${fmt(m.end)}' data-k=end data-i=${i} style=width:64px>`+
      `<button class=st data-i=${i} data-k=end title=设为当前时间>止→</button></td>`+
      `<td>${m.x},${m.y} ${m.w}×${m.h}</td><td>${esc(m.effect)}</td><td class=del data-i=${i}>✕</td>`;
    tb.appendChild(tr);
  });
  tb.querySelectorAll('input').forEach(inp=>inp.onchange=()=>{
    const v=parseT(inp.value);if(!isNaN(v))masks[+inp.dataset.i][inp.dataset.k]=v;
    syncList();draw();save();});
  tb.querySelectorAll('button.st').forEach(b=>b.onclick=()=>{
    masks[+b.dataset.i][b.dataset.k||'start']=+t.toFixed(2);
    syncList();draw();save();});
  tb.querySelectorAll('.del').forEach(d=>d.onclick=()=>{masks.splice(+d.dataset.i,1);syncList();draw();save();});
  tb.querySelectorAll('.num').forEach(n=>n.onclick=()=>{setT(masks[+n.dataset.i].start)});
}
let act=null, actMode=null, actOff=null;
ov.oncontextmenu=e=>e.preventDefault();
ov.onpointerdown=e=>{
  const r=cv.getBoundingClientRect(),s=sc();
  const px=(e.clientX-r.left)/s, py=(e.clientY-r.top)/s;
  const hit=masks.find(m=>active(m)&&px>=m.x&&px<=m.x+m.w&&py>=m.y&&py<=m.y+m.h);
  if(hit&&(e.button===0||e.button===2)){
    act=hit; actMode=e.button===0?'move':'resize';
    actOff=[px-hit.x,py-hit.y]; hover=null;
  }else if(e.button===0){
    act=null; actMode='new';
    drag=[px,py];hover=[...drag,...drag];
  }else return;
  ov.setPointerCapture(e.pointerId);
};
ov.onpointermove=e=>{
  const r=cv.getBoundingClientRect(),s=sc();
  const px=(e.clientX-r.left)/s, py=(e.clientY-r.top)/s;
  if(act&&actMode==='move'){act.x=Math.round(Math.max(0,Math.min(W-act.w,px-actOff[0])));
    act.y=Math.round(Math.max(0,Math.min(H-act.h,py-actOff[1])));draw()}
  else if(act&&actMode==='resize'){
    act.w=Math.round(Math.max(4,Math.min(W-act.x,px-act.x)));
    act.h=Math.round(Math.max(4,Math.min(H-act.y,py-act.y)));draw()}
  else if(drag){hover=[drag[0],drag[1],px,py];draw()}
};
ov.onpointerup=e=>{
  if(act){syncList();save();act=null;actMode=null;return}
  if(!drag)return;
  const s=sc();
  const p2=[(e.clientX-cv.getBoundingClientRect().left)/s,(e.clientY-cv.getBoundingClientRect().top)/s];
  const x0=Math.min(drag[0],p2[0]),y0=Math.min(drag[1],p2[1]),w=Math.abs(p2[0]-drag[0]),h=Math.abs(p2[1]-drag[1]);
  drag=null;hover=null;
  if(w<5||h<5){draw();return}
  const eff=$('eff').value;
  const me={start:+t.toFixed(2),end:+(t+5).toFixed(2),
    x:Math.max(0,Math.round(x0)),y:Math.max(0,Math.round(y0)),effect:eff};
  me.w=Math.min(Math.round(w),W-me.x);me.h=Math.min(Math.round(h),H-me.y);
  if(eff==='blur')me.effect='blur:'+$('strength').value;
  if(eff==='color')me.effect='color:'+$('color').value.slice(1);
  if(eff==='image'&&curImg)me.effect=curImg;
  if(eff==='image'&&!curImg){msg.textContent='请先选择图片文件';return}
  masks.push(me);syncList();draw();save();
};
let curImg=null;
$('pickimg').onclick=()=>$('imgfile').click();
$('imgfile').onchange=async()=>{const f=$('imgfile').files[0];if(!f)return;
  const fd=new FormData();fd.append('f',f);
  const r=await(await fetch('/upload',{method:'POST',body:fd})).json();
  curImg=r.name;$('selimg').textContent=r.name;
  if($('eff').value!=='image'){$('eff').value='image'}msg.textContent='图片已就绪:'+r.name;};
async function save(){
  const r=await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(masks)});
  msg.textContent=(await r.json()).ok?'已保存':'保存失败';
}
$('save').onclick=save;
$('apply').onclick=async()=>{
  await save();
  if((await(await fetch('/exists')).json()).exists&&!confirm('输出文件(.masked.mp4)已存在,覆盖?'))return;
  applying=true;msg.textContent='应用打码中...';
  await fetch('/apply',{method:'POST'});
  applying=false;msg.textContent=(await(await fetch('/apply-status')).json()).msg;};
let dead=false;  // 服务端退出后全屏遮罩提示
setInterval(async()=>{  // /apply 是异步线程,持续轮询状态直到完成退出
  if(dead)return;
  try{
    const j=await(await fetch('/apply-status')).json();
    if(j.msg)msg.textContent=j.msg;
  }catch(e){dead=true;
    const d=document.createElement('div');
    d.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.78);color:#eee;display:flex;align-items:center;justify-content:center;font-size:22px;z-index:9999';
    d.textContent='程序已退出,请关闭此页面';
    document.body.appendChild(d);}
},500);
addEventListener('pagehide',()=>navigator.sendBeacon('/quit'));
function setT(v){t=Math.min(DUR,Math.max(0,v));$('t').value=fmt(t);$('slider').value=t;loadFrame()}
$('t').onchange=()=>{const v=parseT($('t').value);if(!isNaN(v))setT(v)};
$('slider').oninput=()=>{t=+$('slider').value;$('t').value=fmt(t);loadFrame()};
document.onkeydown=e=>{
  if(e.target.tagName==='INPUT'||e.target.tagName==='SELECT')return;
  const step=e.shiftKey?1:(e.altKey?5:0.1);
  if(e.key==='ArrowLeft'){setT(t-step);e.preventDefault()}
  else if(e.key==='ArrowRight'){setT(t+step);e.preventDefault()}
  else if(e.key==='PageUp'){setT(t-10);e.preventDefault()}
  else if(e.key==='PageDown'){setT(t+10);e.preventDefault()}
  else if(e.key==='Home'){setT(0);e.preventDefault()}
  else if(e.key==='End'){setT(DUR);e.preventDefault()}
};
$('strength').onchange=draw;$('color').oninput=draw;
syncList();loadFrame();
</script>"""


class _Picker:
    def __init__(self, video: Path, masks_path: Path):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.video = video
        self.masks_path = masks_path
        self.vw, self.vh = video_size(video)
        self.duration = video_duration(video)
        self.apply_msg = "空闲"
        self._img_seq = 0

        import time

        self._last = time.time()  # 最近一次页面请求时间(判断页面是否已关闭)

        def _maybe_quit():
            """页面关闭后 2s 内没有新请求才退出(排除刷新误触)。"""
            if time.time() - picker._last > 1.5:
                self.srv.shutdown()

        def _touch():
            picker._last = time.time()

        picker = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # 静默访问日志
                pass

            def _json(self, obj):
                import json

                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                _touch()
                if self.path == "/":
                    body = _render_page(picker).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path.startswith("/frame?"):
                    import json
                    import urllib.parse

                    q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    t = float(q.get("t", ["0"])[0])
                    try:
                        data = frame_jpeg(video, t)
                    except subprocess.CalledProcessError:
                        self.send_error(500)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif self.path.startswith("/img?"):
                    import urllib.parse

                    name = Path(
                        urllib.parse.parse_qs(
                            urllib.parse.urlparse(self.path).query
                        ).get("name", [""])[0]
                    ).name
                    fp = _upload_dir() / name
                    if not fp.is_file():  # 兼容旧 masks.txt 里存视频目录相对路径的写法
                        fp = picker.masks_path.parent / name
                    if not name or not fp.exists() or not fp.is_file():
                        self.send_error(404)
                        return
                    body = fp.read_bytes()
                    ctype = "image/png" if fp.suffix.lower() == ".png" else "image/jpeg"
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/apply-status":
                    self._json({"msg": picker.apply_msg})
                elif self.path == "/exists":
                    out = picker.video.with_name(picker.video.stem + ".masked.mp4")
                    self._json({"exists": out.exists()})
                else:
                    self.send_error(404)

            def do_POST(self):
                import json

                _touch()
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n)
                if self.path == "/quit":
                    self._json({"ok": True})
                    threading.Timer(2.0, _maybe_quit).start()
                elif self.path == "/save":
                    items = json.loads(body)
                    entries = [
                        MaskEntry(
                            float(m["start"]),
                            float(m["end"]),
                            int(m["x"]),
                            int(m["y"]),
                            int(m["w"]),
                            int(m["h"]),
                            m["effect"],
                        )
                        for m in items
                    ]
                    save_masks(entries, picker.masks_path)
                    self._json({"ok": True})
                elif self.path == "/upload":
                    ctype = self.headers.get("Content-Type", "")
                    if "boundary=" not in ctype:
                        self.send_error(400)
                        return
                    # 手动解析 multipart(只取第一个文件部分)
                    boundary = ctype.partition("boundary=")[2].encode()
                    name = picker._save_upload(body, boundary)
                    self._json({"name": name})
                elif self.path == "/apply":
                    threading.Thread(target=picker._apply, daemon=True).start()
                    self._json({"ok": True})
                else:
                    self.send_error(404)

        port = 8765
        for p in range(8765, 8775):
            try:
                self.srv = ThreadingHTTPServer(("127.0.0.1", p), H)
                port = p
                break
            except OSError:
                continue
        else:
            raise SystemExit("错误:8765~8774 端口都被占用")

    def entries(self) -> list[MaskEntry]:
        return parse_masks(self.masks_path) if self.masks_path.exists() else []

    def _save_upload(self, body: bytes, boundary: bytes) -> str:
        # 极简 multipart:取第一个二进制段;存临时目录,masks.json 记绝对路径
        parts = body.split(boundary)
        for part in parts:
            if b"Content-Type" not in part or b"filename" not in part:
                continue
            data = part.partition(b"\r\n\r\n")[2].rsplit(b"\r\n", 1)[0]
            ext = ".png" if b"png" in part[:200] else ".jpg"
            self._img_seq += 1
            fp = _upload_dir() / f"mask_img_{self._img_seq}{ext}"
            fp.write_bytes(data)
            return str(fp)
        raise SystemExit("上传解析失败")

    def _apply(self):
        try:
            self.apply_msg = "应用打码中..."
            from .cli import _product_out

            out = _product_out(self.video, ".masked.mp4")
            apply_masks(
                self.video,
                self.masks_path,
                out,
                progress=lambda s: setattr(self, "apply_msg", f"应用打码中 {s}"),
            )
            self.apply_msg = f"完成:{out},选择器即将退出"
            self._shutdown_soon()
        except Exception as e:  # noqa: BLE001
            self.apply_msg = f"失败:{e}"

    def _shutdown_soon(self):
        """给页面留 3 秒拉取最终状态,然后关闭服务(jpsub mask 进程随之退出)。"""

        def _stop():
            self.srv.shutdown()
            self.srv.server_close()

        threading.Timer(3, _stop).start()


def _entries_json(entries: list[MaskEntry]) -> str:
    import json

    return json.dumps(
        [
            {
                "start": m.start,
                "end": m.end,
                "x": m.x,
                "y": m.y,
                "w": m.w,
                "h": m.h,
                "effect": m.effect,
            }
            for m in entries
        ],
        ensure_ascii=False,
    )


def _render_page(p: "_Picker") -> str:
    page = _PAGE
    for key, val in {
        "@@TITLE@@": p.video.name,
        "@@DURF@@": fmt_t(p.duration),
        "@@DUR@@": f"{p.duration:.3f}",
        "@@W@@": str(p.vw),
        "@@H@@": str(p.vh),
        "@@MASKS@@": _entries_json(p.entries()),
    }.items():
        page = page.replace(key, val)
    return page


def fmt_t(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def frame_jpeg(video: Path, t: float) -> bytes:
    """抓取指定时刻帧,返回 JPEG 字节(不经临时文件)。"""
    r = subprocess.run(
        [
            settings.binary("ffmpeg"),
            "-ss",
            f"{t:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-f",
            "mjpeg",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    return r.stdout


def default_masks_path(video: Path) -> Path:
    """masks.json 默认放在视频工作目录 <视频名>.jpsub/ 下(旧 masks.txt 兼容读取)。"""
    from .cli import _work_of

    return _work_of(video) / "masks.json"


def picker(video: Path, masks_path: Path | None = None) -> None:
    """启动浏览器框选取景器;masks.json 默认在视频工作目录 <视频名>.jpsub/ 下。"""
    import threading
    import webbrowser

    if not video.exists():
        raise SystemExit(f"错误:视频不存在:{video}")
    masks_path = masks_path or default_masks_path(video)
    # 兼容旧版:指定的是 masks.txt 或旧文件存在时,沿用旧路径读取,保存仍写该路径
    if masks_path == default_masks_path(video) and not masks_path.exists():
        old = masks_path.with_suffix(".txt")
        if old.exists():
            masks_path = old
    masks_path.parent.mkdir(parents=True, exist_ok=True)
    # 清理旧版本遗留在视频目录里的上传图片(现在统一存临时目录)
    for old in video.parent.glob("mask_img_*"):
        old.unlink(missing_ok=True)
    p = _Picker(video, masks_path)
    url = f"http://127.0.0.1:{p.srv.server_address[1]}/"
    print(
        f"打码选取器:{url}(浏览器未自动打开时手动访问;关闭页面即退出;应用打码完成后自动退出)"
    )
    threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        p.srv.serve_forever()
    except KeyboardInterrupt:
        pass
