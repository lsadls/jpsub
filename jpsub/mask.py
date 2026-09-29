"""打码系统:鼠标框选时间轴区域 -> masks.json -> ffmpeg 应用马赛克/纯色/图片/静音。

masks.json 为 JSON 数组,每条:{"start","end","x","y","w","h","effect","vol"}
effect 可为: blur[:强度] / color:RRGGBB / 图片路径(png/jpg) / mute(仅音频)
vol 为该段音量倍率(默认 1 不处理;0=静音),起止时间内生效。坐标为原视频像素。
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from . import settings, utils


@dataclass
class MaskEntry:
    start: float
    end: float
    x: int
    y: int
    w: int
    h: int
    effect: str  # blur / blur:N / color:RRGGBB / 图片路径 / mute(仅音频)
    vol: float = 1.0  # 该段音量倍率,1=不处理,0=静音


def _upload_dir() -> Path:
    """上传图片的临时目录(不污染视频所在目录)。"""
    d = Path(tempfile.gettempdir()) / "jpsub_mask_img"
    d.mkdir(exist_ok=True)
    return d


def parse_masks(path: Path) -> list[MaskEntry]:
    """读打码清单:masks.json(JSON 数组)。"""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    return _parse_items(json.loads(text))


def _parse_items(items: list) -> list[MaskEntry]:
    return [
        MaskEntry(
            float(m["start"]),
            float(m["end"]),
            int(m["x"]),
            int(m["y"]),
            int(m["w"]),
            int(m["h"]),
            str(m["effect"]),
            float(m.get("vol", 1.0)),
        )
        for m in items
    ]


def save_masks(entries: list[MaskEntry], path: Path) -> None:
    entries = sorted(entries, key=lambda m: m.start)
    path.parent.mkdir(parents=True, exist_ok=True)
    items = [
        {
            "start": m.start,
            "end": m.end,
            "x": m.x,
            "y": m.y,
            "w": m.w,
            "h": m.h,
            "effect": m.effect,
            "vol": m.vol,
        }
        for m in entries
    ]
    path.write_text(
        json.dumps(items, ensure_ascii=False, indent=1) + "\n",
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
) -> tuple[str, list[Path], str]:
    """生成 filter_complex 与按顺序追加的图片输入列表;图片相对路径基于 base。

    返回 (视频滤镜串, 图片列表, 音量滤镜串)。mute 条目无视频滤镜;
    vol!=1 的条目生成对应时间段的音量滤镜(顺序串联,重叠段倍率相乘)。"""
    chains: list[str] = []
    images: list[Path] = []
    achains: list[str] = []
    cur = "0:v"
    a_in = "0:a"
    base = base or Path.cwd()
    for m in entries:
        if m.vol != 1.0:
            a_out = f"a{len(achains)}"
            if len(achains) + 1 == sum(1 for x in entries if x.vol != 1.0):
                a_out = "aout"  # 最后一级输出固定标签供 -map 使用
            achains.append(
                f"[{a_in}]volume={m.vol:g}:"
                f"enable='between(t,{m.start:g},{m.end:g})'[{a_out}]"
            )
            a_in = a_out
        if m.effect in ("mute", "cut"):  # 仅音频/裁剪条目,不生成视频滤镜
            continue
        i = len(chains)
        nxt = f"v{i + 1}"
        en = f"enable='between(t,{m.start:g},{m.end:g})'"
        eff = m.effect
        if eff == "blur" or eff.startswith("blur:"):
            # 强度同时控制扭曲与模糊:先用 swaprect 把区域内随机方块多次互换打乱(破坏轮廓,
            # 防止隔着模糊看形状),强度越高互换次数越多、信息丢失越多;再 boxblur 收尾。
            # 坐标/尺寸取偶数避免 yuv420p 色度对齐问题;种子取条目序号保证可复现。
            import random

            strength = int(eff.partition(":")[2] or 20)
            rng = random.Random(i)
            scramble: list[str] = []
            n_swap = max(3, min(60, int(m.w * m.h / 6000 * strength / 20)))
            for _ in range(n_swap):
                sw = rng.randrange(8, max(9, m.w // 2)) // 2 * 2
                sh = rng.randrange(8, max(9, m.h // 2)) // 2 * 2
                x1 = rng.randrange(0, max(1, m.w - sw)) // 2 * 2
                y1 = rng.randrange(0, max(1, m.h - sh)) // 2 * 2
                x2 = rng.randrange(0, max(1, m.w - sw)) // 2 * 2
                y2 = rng.randrange(0, max(1, m.h - sh)) // 2 * 2
                scramble.append(f"swaprect={sw}:{sh}:{x1}:{y1}:{x2}:{y2}")
            # boxblur=R:2:0:0 只模糊亮度通道(跳过色度,效果更好且不受色度限制);
            # luma radius 上限还随区域尺寸变化(约 min(w,h)/5),一并钳制
            radius = max(
                2, min(58, strength, min(m.w, m.h) // 5)
            )
            scramble.append(f"boxblur={radius}:2:0:0")
            chains.append(
                f"[{cur}]split[sa{i}][sb{i}];"
                f"[sb{i}]crop={m.w}:{m.h}:{m.x}:{m.y},"
                + ",".join(scramble)
                + f"[bb{i}];"
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
    return ";".join(chains), images, ";".join(achains)


def _cut_select(entries: list[MaskEntry]) -> tuple[str, str]:
    """effect='cut' 的条目 → (视频 select 串, 音频 select 串);剔除这些区间,保留其余。
    无裁剪条目时返回 ("", "")。select 表达式非 0 保留帧;区间允许重叠。"""
    cuts = [m for m in entries if m.effect == "cut"]
    if not cuts:
        return "", ""
    expr = "+".join(f"between(t,{m.start:g},{m.end:g})" for m in cuts)
    return (
        f"select='1-({expr})',setpts=N/FRAME_RATE/TB",
        f"aselect='1-({expr})',asetpts=N/SR/TB",
    )


def apply_masks(
    video: Path,
    masks_path: Path,
    output: Path,
    progress=None,
    span: tuple[float, float] | None = None,
) -> Path:
    """应用打码并剔除「裁掉」区间(effect=cut,若有);progress 回调用于网页显示进度。

    span=(start,end) 时只导出该时间段(打码滤镜仍按原视频时间轴生效)。"""
    entries = parse_masks(masks_path)
    if not entries:
        raise SystemExit(f"错误:{masks_path} 里没有打码条目")
    fc, images, afc = build_filter(entries, masks_path.parent)
    n_vis = sum(1 for m in entries if m.effect not in ("mute", "cut"))
    # span 导出(片段)时不剔除 cut 区间——要导出的正是那一段
    v_sel, a_sel = ("", "") if span else _cut_select(entries)
    # 组装滤镜:打码视频链 → 剔除区间;音量链 → 剔除区间。裁切会改变时间轴,涉及其中的流必须重编码
    parts: list[str] = []
    v_map = "0:v"
    if fc:
        parts.append(f"{fc};[v{n_vis}]{v_sel}[vout]" if v_sel else fc)
        v_map = "[vout]" if v_sel else f"[v{n_vis}]"
    elif v_sel:
        parts.append(f"[0:v]{v_sel}[vout]")
        v_map = "[vout]"
    a_map = "0:a?"
    a_enc = None
    if afc:
        parts.append(f"{afc};[aout]{a_sel}[afinal]" if a_sel else afc)
        a_map, a_enc = ("[afinal]", "aac") if a_sel else ("[aout]", "aac")
    elif a_sel:
        parts.append(f"[0:a]{a_sel}[afinal]")
        a_map, a_enc = "[afinal]", "aac"
    cmd = [settings.binary("ffmpeg"), "-y", "-nostdin", "-threads", str(settings.ffmpeg_threads()), "-i", str(video)]
    for img in images:
        cmd += ["-i", str(img)]
    if parts:
        cmd += ["-filter_complex", ";".join(parts)]
    cmd += ["-map", v_map, "-map", a_map]
    if v_map == "0:v" and not (afc or a_sel):
        cmd += ["-c:v", "copy"]  # 视频未经过任何滤镜,直拷
    if a_enc:
        cmd += ["-c:a", a_enc]
    elif a_map == "0:a?":
        cmd += ["-c:a", "copy"]
    output.parent.mkdir(parents=True, exist_ok=True)
    if span:
        # 输出选项式截取:滤镜里的 enable 仍按原时间轴,不受 -ss 影响
        cmd += ["-ss", f"{span[0]:.3f}", "-t", f"{max(0.01, span[1] - span[0]):.3f}"]
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
#msg{position:fixed;top:4px;left:50%;transform:translateX(-50%);background:rgba(30,30,30,.94);border:1px solid #555;border-radius:6px;padding:4px 14px;z-index:999;color:#fc6;max-width:70vw}
#quit{position:fixed;top:4px;right:10px;z-index:999}
#main{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start}
#left{flex:0 1 auto;min-width:0}
#left>div:not(#wrap){max-width:560px}  /* 文本/控件行限宽换行,防止把右侧列表挤出视口 */
#wrap{position:relative;display:inline-block}canvas{display:block;background:#000;width:min(46vw,80vh);height:auto}
#overlay{position:absolute;left:0;top:0;width:100%;height:100%;cursor:crosshair}
#right{flex:1 1 320px;min-width:280px;max-height:calc(100vh - 60px);overflow-y:auto}
button,input,select{background:#333;color:#ddd;border:1px solid #555;border-radius:4px;padding:3px 8px;font:inherit}
button{cursor:pointer}button:hover{background:#444}
table{border-collapse:collapse;width:100%}
#list{display:block;max-height:66.7vh;overflow-y:auto}
th{position:sticky;top:0;background:#1e1e1e;z-index:1}
td,th{border:1px solid #444;padding:2px 6px;text-align:left}
tr.act{color:#8f8}.del{color:#f88;cursor:pointer;padding:0 4px}
.num{cursor:pointer;color:#8cf;text-decoration:underline}
#msg{color:#fc6;min-height:1.2em;clear:both}
.hint{color:#888;margin-top:6px}
.grp{display:inline-flex;gap:4px;align-items:center;border:1px solid #555;border-radius:6px;padding:4px 6px}
.sec{margin-top:10px}
.st{display:block;color:#9ab;font-size:12px;font-weight:600;margin-bottom:3px}
</style>
<h3>打码选取 - @@TITLE@@</h3>
<div id=main>
<div id=left>
<div id=wrap><canvas id=cv></canvas><div id=overlay></div></div>
<video id=vid src=/video preload=auto style=display:none></video>
<div class=sec><b class=st>时间与定位</b>
<div>
时间 <input id=t type=text value=0:00.0 style=width:70px> / @@DURF@@
<input type=range id=slider min=0 max=@@DUR@@ step=0.1 value=0 style=width:220px>
</div></div>
<div class=sec><b class=st>效果(拖框新增,框内左键移动/右键调大小)</b>
<div>
效果 <label><input type=radio name=eff value=blur>模糊</label>
<label><input type=radio name=eff value=color checked>纯色</label>
<label><input type=radio name=eff value=image>图片</label>
<label><input type=radio name=eff value=mute title=只处理该时间段声音,不遮挡画面>静音</label>
<label><input type=radio name=eff value=cut title=该时间段整段剔除,不占区域(拖框即可,区域无效)>裁掉</label>
强度 <input id=strength type=number value=20 min=2 max=58 style=width:56px>
<input id=color type=color value=#000000>
<button id=pickimg>选图片…</button><span id=selimg style=color:#888></span>
<input id=imgfile type=file accept="image/*" style=display:none>
</div></div>
<div class=sec><b class=st>保存与应用</b>
<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center">
<span class=grp><button id=save>保存</button><input id=bkname placeholder=备份名(可空) style="width:130px" title="留空用当前时间作为备份名"> <button id=apply>应用打码</button></span>
<span class=hint style=align-self:center>列表序号可点击跳转</span>
</div></div>
<div class=sec><b class=st>还原与备份</b>
<div class=grp style="flex-direction:column;align-items:stretch;width:300px">
<span><button id=restore>还原改动</button> <span class=hint>选中历史备份后点击还原</span></span>
<input id=bksearch placeholder=搜索备份 style="width:100%;margin:2px 0">
<select id=bksel size=8 style="width:100%" title="历史备份:每次保存前自动生成;还原时选中其一即还原到该备份,不选则还原到打开时状态"></select>
</div></div>
<div class=hint style=margin-top:4px>新条目默认 当前帧 ~ 当前帧+5秒;时间可改(支持 mm:ss.s 或秒数)。<br>
键盘:←/→ 或 A/D ±1秒(Shift ±0.1秒,Alt ±5秒),↑/↓、W/S 或 PageUp/PageDown ±10秒,Home/End 跳到首尾;Q 播放/暂停,C 新增 mask(当前帧~+5秒,沿用上次区域),Z/X 设当前条目的开始/结束,V 新增「裁掉」片段(应用/导出时整段剔除)。</div>
</div>
<div id=right>
<div class=sec style=margin-top:0><b class=st>导出</b>
<div>把当前生效的时间段打码后单独导出 <button id=cutexport title=保存后导出:当前生效时间段打码成短片段>导出片段</button></div>
</div>
<table id=list><tr><th>#</th><th>时间</th><th>区域</th><th>效果</th><th></th></tr></table>
</div>
</div>
<div id=msg></div>
<button id=quit title=关闭选取器(不应用打码)>退出</button>
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
const vid=$('vid');
let vidOk=true, fetching=false, pendingT=null;
async function loadFrameHttp(){  // 浏览器解不动视频时的回退:HTTP 抽帧(单飞+尾随)
  if(fetching){pendingT=t;return}
  fetching=true;
  const im=new Image();im.src='/frame?t='+t.toFixed(3);
  try{await im.decode();img=im;draw();}catch(e){}
  fetching=false;
  if(pendingT!==null){pendingT=null;loadFrameHttp()}
}
function loadFrame(){  // 优先 video 元素 seek:原生解码,长按方向键画面连续跟随
  if(!vidOk){loadFrameHttp();return}
  if(vid.readyState<1)return;
  if(vid.seeking){vid._pend=t;return}  // seek 中:记下最新时刻,完成后立刻补一次,不再叠加打断
  vid.currentTime=Math.min(t,vid.duration-0.001);
}
vid.onerror=()=>{vidOk=false;loadFrameHttp()};
vid.addEventListener('loadeddata',()=>{if(vidOk)draw()});
vid.addEventListener('seeked',()=>{
  if(!vidOk)return;
  draw();
  if(vid._pend!=null){  // seek 期间有新时刻:立即补 seek 并绘制,长按时逐帧连续
    const p=vid._pend;vid._pend=null;
    vid.currentTime=Math.min(p,vid.duration-0.001);
  }
});
function active(m){return m.start<=t&&t<=m.end}
function sc(){return cv.getBoundingClientRect().width/W}
const imgCache={};
function imgFor(name){
  if(!imgCache[name]){const im=new Image();im.src='/img?name='+encodeURIComponent(name);imgCache[name]=im}
  return imgCache[name];
}
const scratch=document.createElement('canvas');scratch.width=W;scratch.height=H;  // 复用,避免每帧新建大 canvas 拖慢 GC
function draw(){
  const src=vidOk&&vid.readyState>=2?vid:img;
  if(!src)return;
  const tmp=scratch;
  tmp.getContext('2d').drawImage(src,0,0,W,H);
  for(const m of masks){ if(!active(m)||m.effect==='cut')continue;
    const e=m.effect;
    const PAD=2;  // 效果区外扩:绿色边框整条落在打码内容上,不遮盖边缘判断是否漏盖
    if(e==='blur'||e.startsWith('blur:')){
      const req=parseFloat((e.split(':')[1]||20));
      const r=Math.max(2,Math.min(58,req,Math.floor(Math.min(m.w,m.h)/5)));
      const sub=document.createElement('canvas');sub.width=m.w+PAD*2;sub.height=m.h+PAD*2;
      sub.getContext('2d').drawImage(tmp,m.x-PAD,m.y-PAD,m.w+PAD*2,m.h+PAD*2,0,0,m.w+PAD*2,m.h+PAD*2);
      const c=tmp.getContext('2d');c.save();c.filter=`blur(${r}px)`;
      c.drawImage(sub,m.x-PAD,m.y-PAD);c.restore();
    }else if(e.startsWith('color:')){
      tmp.getContext('2d').fillStyle='#'+e.slice(6);
      tmp.getContext('2d').fillRect(m.x-PAD,m.y-PAD,m.w+PAD*2,m.h+PAD*2);
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
function syncAct(){
  const rows=$('list').querySelectorAll('tr');
  rows.forEach((tr,i)=>{ if(!i)return;
    tr.classList.toggle('act',active(masks[i-1]));});
}
function syncList(){
  const tb=$('list');tb.innerHTML='<tr><th>#</th><th>时间</th><th>区域</th><th>效果</th><th></th></tr>';
  masks.forEach((m,i)=>{
    const tr=document.createElement('tr');if(active(m))tr.className='act';
    let eff;
    if(m.effect==='cut')eff=`<span style=color:#f88>裁剪掉</span>`;
    else if(m.effect==='mute')  // 静音(音频)条目:滑动条调该段音量倍率
      eff=`<span style=color:#fc6>静音</span> <input type=range class=vol data-i=${i} min=0 max=2 step=0.05 value=${m.vol??0}`+
          ` style="width:80px;vertical-align:middle" title=该段音量倍率><span class=volt>${(m.vol??0).toFixed(2)}</span>`;
    else if(m.effect==='blur'||m.effect.startsWith('blur:'))
      eff=`模糊 <input type=number class=effb data-i=${i} min=2 max=58 value=${m.effect.split(':')[1]||20} style=width:52px title=模糊强度>`;
    else if(m.effect.startsWith('color:'))
      eff=`<input type=color class=effc data-i=${i} value=#${m.effect.split(':')[1].replace('#','')} title=纯色>`;
    else eff=`<span title=${esc(m.effect)}>图片:${esc(m.effect.split('/').pop())}</span>`;
    tr.innerHTML=`<td class=num data-i=${i} title=点击跳转>${i+1}</td>`+
      `<td><input value='${fmt(m.start)}' data-k=start data-i=${i} style=width:64px>`+
      `<button class=st data-i=${i} title=设为当前时间>←起</button>`+
      ` ~ <input value='${fmt(m.end)}' data-k=end data-i=${i} style=width:64px>`+
      `<button class=st data-i=${i} data-k=end title=设为当前时间>止→</button></td>`+
      `<td>${m.effect==='cut'?'—':`${m.x},${m.y} ${m.w}×${m.h}`}</td><td>${eff}</td>`+
      `<td class=del data-i=${i}>✕</td>`;
    tb.appendChild(tr);
  });
  tb.querySelectorAll('input[type=text]').forEach(inp=>inp.onchange=()=>{
    const v=parseT(inp.value);if(!isNaN(v))masks[+inp.dataset.i][inp.dataset.k]=v;
    syncList();draw();markDirty();});
  tb.querySelectorAll('input.vol').forEach(inp=>{
    const upd=()=>{const m=masks[+inp.dataset.i];m.vol=+inp.value;
      inp.nextElementSibling.textContent=(+inp.value).toFixed(2);};
    inp.oninput=upd;
    inp.onchange=()=>{upd();markDirty();};});
  tb.querySelectorAll('input.effb').forEach(inp=>inp.onchange=()=>{
    masks[+inp.dataset.i].effect='blur:'+(+inp.value||20);markDirty();});
  tb.querySelectorAll('input.effc').forEach(inp=>inp.onchange=()=>{
    masks[+inp.dataset.i].effect='color:'+inp.value.slice(1);markDirty();});
  tb.querySelectorAll('button.st').forEach(b=>b.onclick=()=>{
    masks[+b.dataset.i][b.dataset.k||'start']=+t.toFixed(2);
    syncList();draw();markDirty();});
  // 点击列表行跳转时间轴到该条目开始(输入框/按钮/删除除外)
  tb.querySelectorAll('tr:not(:first-child)').forEach(tr=>{
    tr.onclick=e=>{
      if(e.target.closest('input,button,.del'))return;
      const i=[...tb.rows].indexOf(tr)-1;
      const m=masks[i];if(!m)return;
      setT(m.start);draw();
    };
  });
  tb.querySelectorAll('.del').forEach(d=>d.onclick=()=>{masks.splice(+d.dataset.i,1);syncList();draw();markDirty();});
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
  if(act){syncList();markDirty();act=null;actMode=null;return}
  if(!drag)return;
  const s=sc();
  const p2=[(e.clientX-cv.getBoundingClientRect().left)/s,(e.clientY-cv.getBoundingClientRect().top)/s];
  const x0=Math.min(drag[0],p2[0]),y0=Math.min(drag[1],p2[1]),w=Math.abs(p2[0]-drag[0]),h=Math.abs(p2[1]-drag[1]);
  drag=null;hover=null;
  if(w<5||h<5){draw();return}
  const eff=document.querySelector('input[name=eff]:checked').value;
  const me={start:+t.toFixed(2),end:+(t+5).toFixed(2),
    x:Math.max(0,Math.round(x0)),y:Math.max(0,Math.round(y0)),effect:eff};
  me.w=Math.min(Math.round(w),W-me.x);me.h=Math.min(Math.round(h),H-me.y);
  if(eff==='blur')me.effect='blur:'+$('strength').value;
  if(eff==='color')me.effect='color:'+$('color').value.slice(1);
  if(eff==='image'&&curImg)me.effect=curImg;
  me.vol=eff==='mute'?0:1;
  if(eff==='image'&&!curImg){msg.textContent='请先选择图片文件';return}
  masks.push(me);syncList();draw();markDirty();
};
let curImg=null;
$('pickimg').onclick=()=>$('imgfile').click();
$('imgfile').onchange=async()=>{const f=$('imgfile').files[0];if(!f)return;
  const fd=new FormData();fd.append('f',f);
  const r=await(await fetch('/upload',{method:'POST',body:fd})).json();
  curImg=r.name;$('selimg').textContent=r.name;
  if(document.querySelector('input[name=eff]:checked').value!=='image'){document.querySelector('input[name=eff][value=image]').checked=true}msg.textContent='图片已就绪:'+r.name;};
async function save(auto){
  const r=await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:auto?'':$('bkname').value.trim(),items:masks,auto})});
  const j=await r.json();
  if(!auto){msg.textContent=j.ok?'已保存':'保存失败';
    if(j.ok){$('bkname').value='';loadBks();}}
  return j.ok;
}
$('save').onclick=()=>save(false);
@@AUTOSAVE@@
mkAutosave(a=>save(a));
async function loadBks(){
  const j=await(await fetch('/backups')).json();
  allBks=j.names||[];renderBks();
}
let allBks=[];
function renderBks(){
  const q=$('bksearch').value.trim().toLowerCase();
  const s=$('bksel');s.innerHTML='';
  allBks.filter(n=>n.toLowerCase().includes(q)).forEach(n=>{
    const o=document.createElement('option');o.value=n;o.textContent=n;s.appendChild(o);});
}
$('bksearch').oninput=renderBks;
loadBks();
$('restore').onclick=async()=>{
  const bk=$('bksel').value;
  if(!confirm(bk?'确定还原到备份 '+bk+'?未保存的改动会丢失':'确定还原到打开选取器时的状态?'))return;
  const j=await post2('/restore',{name:bk||''});
  if(j.ok)location.reload();
  else msg.textContent='失败:'+j.err;
};
function post2(path,body){
  return fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)}).then(r=>r.json());
}
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
function setT(v){t=Math.min(DUR,Math.max(0,v));$('t').value=fmt(t);$('slider').value=t;loadFrame();syncAct()}
$('t').onchange=()=>{const v=parseT($('t').value);if(!isNaN(v))setT(v)};
$('slider').oninput=()=>{t=+$('slider').value;$('t').value=fmt(t);loadFrame();syncAct()};
let playing=false,pt=0;  // 播放状态:按真实时间推进,逐帧异步取图
function tick(){
  if(!playing)return;
  const now=performance.now();
  t=Math.min(DUR,t+(now-pt)/1000);pt=now;
  $('t').value=fmt(t);$('slider').value=t;
  loadFrame();syncAct();
  if(t>=DUR){playing=false;msg.textContent='播放结束'}else setTimeout(tick,200); // ~5fps,时间按真实时钟推进不丢速
}
function togglePlay(){
  playing=!playing;
  if(playing){pt=performance.now();if(t>=DUR)t=0;msg.textContent='播放中(q 停止)';tick()}
  else msg.textContent='已暂停';
}
function newMaskAt(){
  const last=masks[masks.length-1];
  const w=last?last.w:Math.round(W/4),h=last?last.h:Math.round(H/4);
  const x=last?last.x:Math.round((W-w)/2),y=last?last.y:Math.round((H-h)/2);
  const eff=document.querySelector('input[name=eff]:checked').value;
  const me={start:+t.toFixed(2),end:+(t+5).toFixed(2),
    x:Math.max(0,x),y:Math.max(0,y),effect:eff};
  me.w=Math.min(w,W-me.x);me.h=Math.min(h,H-me.y);
  me.vol=eff==='mute'?0:1;
  if(eff==='cut'){me.effect='cut';masks.push(me);syncList();draw();markDirty();
    msg.textContent='已新增裁掉片段,应用/导出时整段剔除';return}
  if(eff==='blur')me.effect='blur:'+$('strength').value;
  if(eff==='color')me.effect='color:'+$('color').value.slice(1);
  if(eff==='image'&&curImg)me.effect=curImg;
  if(eff==='image'&&!curImg){msg.textContent='请先选择图片文件';return}
  masks.push(me);syncList();draw();markDirty();
  msg.textContent='已新增 mask(沿用上次区域)';
}
function cutAdd(){  // 新增「裁掉」条目:当前帧~+5秒,用 Z/X 调起止
  masks.push({start:+t.toFixed(2),end:+(t+5).toFixed(2),x:0,y:0,w:2,h:2,effect:'cut',vol:1});
  syncList();draw();markDirty();
  msg.textContent='已新增裁掉片段(应用/导出时整段剔除),Z/X 调起止';
}
$('cutexport').onclick=async()=>{
  // 优先取当前时间所在的「裁掉」条目;否则取最后一个「裁掉」条目;再否则取当前生效条目
  const m=masks.find(m=>m.effect==='cut'&&active(m))
        ||[...masks].reverse().find(m=>m.effect==='cut')
        ||masks.find(m=>active(m));
  if(!m)return msg.textContent='没有可导出的片段(先新增「裁掉」片段)';
  if(!await save())return;
  if(applying)return msg.textContent='已有任务进行中...';
  applying=true;msg.textContent='导出片段中...';
  try{await post2('/cutclip',{start:m.start,end:m.end});}
  finally{applying=false;}
};
document.onkeydown=e=>{
  if(e.key==='q'||e.key==='Q'){togglePlay();e.preventDefault();return}
  if(e.key==='c'||e.key==='C'){newMaskAt();e.preventDefault();return}
  if(e.key==='v'||e.key==='V'){cutAdd();e.preventDefault();return}
  if(e.key==='z'||e.key==='Z'||e.key==='x'||e.key==='X'){
    const k=e.key.toLowerCase()==='z'?'start':'end';
    const m=masks.find(m=>active(m));
    if(m){m[k]=+t.toFixed(2);syncList();draw();markDirty();
      msg.textContent=`已设为 mask ${masks.indexOf(m)+1} 的${k==='start'?'开始':'结束'}时间`}
    else msg.textContent='当前时间没有生效的 mask,无法设置';
    e.preventDefault();return}
  // 方向键始终控制预览(即使焦点在输入框/滑条上),并阻止默认行为避免同时移动控件
  // WASD 与方向键等价:a/d ±1s,w/s ±10s(Shift/Alt 前缀同样生效)
  const step=e.shiftKey?0.1:(e.altKey?5:1);
  const big=e.shiftKey?1:(e.altKey?30:10);
  const left=e.key==='ArrowLeft'||e.key==='a'||e.key==='A';
  const right=e.key==='ArrowRight'||e.key==='d'||e.key==='D';
  const up=e.key==='ArrowUp'||e.key==='PageUp'||e.key==='w'||e.key==='W';
  const down=e.key==='ArrowDown'||e.key==='PageDown'||e.key==='s'||e.key==='S';
  if(left){setT(t-step);e.preventDefault()}
  else if(right){setT(t+step);e.preventDefault()}
  else if(up){setT(t-big);e.preventDefault()}
  else if(down){setT(t+big);e.preventDefault()}
  else if(e.key==='Home'){setT(0);e.preventDefault()}
  else if(e.key==='End'){setT(DUR);e.preventDefault()}
};
$('strength').onchange=draw;$('color').oninput=draw;
$('quit').onclick=()=>{if(confirm('退出打码选取器?'))fetch('/quit',{method:'POST'})};
syncList();loadFrame();
</script>"""


class _Picker:
    def __init__(self, video: Path, masks_path: Path):
        self.video = video
        self.masks_path = masks_path
        # 打开时的初始快照:还原改动(不选备份)时用
        self._initial = masks_path.read_bytes() if masks_path.is_file() else b""
        self.vw, self.vh = video_size(video)
        self.duration = video_duration(video)
        self.apply_msg = "空闲"
        self._img_seq = 0
        self.idle = utils.IdleQuit()

        from http.server import BaseHTTPRequestHandler

        picker = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # 静默访问日志
                pass

            def do_GET(self):
                picker.idle.touch()
                if self.path == "/":
                    utils.http_page(self, _render_page(picker))
                elif self.path == "/video":
                    utils.http_video(self, video)
                elif self.path == "/backups":  # GET:历史备份列表(前端 loadBks 用 GET)
                    from .cli import _backup_names

                    names = _backup_names(picker.masks_path.parent, "masks.json")
                    utils.http_json(self, {"ok": True, "names": names})
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
                    utils.http_bytes(self, data, "image/jpeg")
                elif self.path.startswith("/img?"):
                    import urllib.parse

                    name = Path(
                        urllib.parse.parse_qs(
                            urllib.parse.urlparse(self.path).query
                        ).get("name", [""])[0]
                    ).name
                    fp = _upload_dir() / name
                    if not name or not fp.exists() or not fp.is_file():
                        self.send_error(404)
                        return
                    body = fp.read_bytes()
                    ctype = "image/png" if fp.suffix.lower() == ".png" else "image/jpeg"
                    utils.http_bytes(self, body, ctype)
                elif self.path == "/apply-status":
                    utils.http_json(self, {"msg": picker.apply_msg})
                elif self.path == "/exists":
                    out = picker.video.with_name(picker.video.stem + ".masked.mp4")
                    utils.http_json(self, {"exists": out.exists()})
                else:
                    self.send_error(404)

            def do_POST(self):
                import json

                picker.idle.touch()
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n)
                if self.path == "/quit":
                    utils.http_json(self, {"ok": True})
                    picker.idle.on_quit()
                elif self.path == "/save":
                    data = json.loads(body)
                    items = [
                        MaskEntry(
                            float(m["start"]),
                            float(m["end"]),
                            int(m["x"]),
                            int(m["y"]),
                            int(m["w"]),
                            int(m["h"]),
                            str(m["effect"]),
                            float(m.get("vol", 1.0)),
                        )
                        for m in data["items"]
                    ]
                    if not data.get("auto"):  # 自动保存只落盘,不建备份
                        from .cli import _backup

                        _backup(
                            picker.masks_path.parent,
                            picker.masks_path,
                            name=str(data.get("name", "") or ""),
                        )
                    save_masks(items, picker.masks_path)
                    utils.http_json(self, {"ok": True})
                elif self.path == "/backups":
                    from .cli import _backup_names

                    names = _backup_names(picker.masks_path.parent, "masks.json")
                    utils.http_json(self, {"ok": True, "names": names})
                elif self.path == "/restore":
                    try:
                        name = ""
                        try:
                            name = str(json.loads(body).get("name", "") or "").strip()
                        except Exception:  # noqa: BLE001
                            pass
                        if name:
                            from .cli import _backup, _backup_src

                            src = _backup_src(
                                picker.masks_path.parent, name, "masks.json"
                            )
                            if not src.is_file():
                                raise FileNotFoundError(f"找不到备份:{src}")
                            from .cli import _backup

                            _backup(  # 还原前备份当前状态
                                picker.masks_path.parent, picker.masks_path
                            )
                            picker.masks_path.write_bytes(src.read_bytes())
                        else:  # 不选备份:还原到打开选取器时的状态
                            if picker._initial:
                                picker.masks_path.write_bytes(picker._initial)
                            elif picker.masks_path.exists():
                                picker.masks_path.unlink()
                        utils.http_json(self, {"ok": True})
                    except Exception as e:  # noqa: BLE001
                        utils.http_json(self, {"ok": False, "err": str(e)})
                elif self.path == "/upload":
                    ctype = self.headers.get("Content-Type", "")
                    if "boundary=" not in ctype:
                        self.send_error(400)
                        return
                    # 手动解析 multipart(只取第一个文件部分)
                    boundary = ctype.partition("boundary=")[2].encode()
                    name = picker._save_upload(body, boundary)
                    utils.http_json(self, {"name": name})
                elif self.path == "/apply":
                    threading.Thread(target=picker._apply, daemon=True).start()
                    utils.http_json(self, {"ok": True})
                elif self.path == "/cutclip":
                    b2 = json.loads(body or b"{}")
                    span = (float(b2["start"]), float(b2["end"]))
                    threading.Thread(
                        target=picker._cutclip, args=(span,), daemon=True
                    ).start()
                    utils.http_json(self, {"ok": True})
                else:
                    self.send_error(404)

        self.srv = utils.pick_server(H, range(8765, 8775))
        self.idle.srv = self.srv

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

    def _cutclip(self, span: tuple[float, float]):
        """导出片段:当前生效时间段(start~end)打码后单独导出为短片段。"""
        try:
            self.apply_msg = "导出片段中..."
            import re as _re

            out = self.video.with_name(
                _re.sub(r"(\.[^.]+)$", r".cut\1", self.video.name)
            )
            apply_masks(
                self.video,
                self.masks_path,
                out,
                span=span,
                progress=lambda s: setattr(self, "apply_msg", f"导出片段中 {s}"),
            )
            self.apply_msg = f"片段已导出:{out}({span[0]:g}~{span[1]:g}s)"
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
                "vol": m.vol,
            }
            for m in entries
        ],
        ensure_ascii=False,
    )


def _render_page(p: "_Picker") -> str:
    return utils.render_page(_PAGE, {
        "@@TITLE@@": p.video.name,
        "@@DURF@@": fmt_t(p.duration),
        "@@DUR@@": f"{p.duration:.3f}",
        "@@W@@": str(p.vw),
        "@@H@@": str(p.vh),
        "@@MASKS@@": _entries_json(p.entries()),
        "@@AUTOSAVE@@": utils.AUTOSAVE_JS,
    })


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
    """masks.json 默认放在视频工作目录的隐藏子目录 .jpsub/ 下。"""
    from .cli import _work_of
    from . import handoff

    return handoff.masks_path(_work_of(video))


def picker(video: Path, masks_path: Path | None = None) -> None:
    """启动浏览器框选取景器;masks.json 默认在视频工作目录的 .jpsub/ 下。"""
    if not video.exists():
        raise SystemExit(f"错误:视频不存在:{video}")
    masks_path = masks_path or default_masks_path(video)
    masks_path.parent.mkdir(parents=True, exist_ok=True)
    p = _Picker(video, masks_path)
    url = utils.open_browser(p.srv)
    print(
        f"打码选取器:{url}(浏览器未自动打开时手动访问;关闭页面即退出;应用打码完成后自动退出)"
    )
    try:
        p.srv.serve_forever()
    except KeyboardInterrupt:
        pass
