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
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import cli, handoff, progress, utils

VID_EXTS = utils.VID_EXTS


# 子进程输出清洗:去掉 ANSI 转义序列(彩色/光标控制)与其他控制字符
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|[\x00-\x08\x0b-\x1f]"
)


def _sanitize(line: str) -> str:
    return _ANSI_RE.sub("", line)


def _kill_tree(proc: subprocess.Popen) -> None:
    """终止进程及其全部子孙(下载任务会派生 yt-dlp/ffmpeg,只杀父进程会残留)。"""
    import os
    import signal

    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    proc.wait(timeout=10)


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
    "upload": "投稿",
    "proofread": "校对",
    "import": "导入字幕",
    "submerge": "合并字幕",
    "layout": "排版字幕",
    "convert": "转换字幕",
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
.sec{margin-top:10px;display:flex;flex-wrap:wrap;align-items:center;gap:4px 6px}
.st{color:#9ab;font-size:12px;font-weight:600;white-space:nowrap}
.sec>.btns{flex-basis:100%}
.sec.danger{border:1px solid #733;border-radius:6px;padding:6px 8px;background:rgba(120,40,40,.12)}
.sec.danger .st{color:#f88}
.outbox{margin-top:6px;border:1px solid #444;border-radius:4px;background:#181818;padding:6px;max-height:45vh;overflow-y:auto}
.outbox pre{margin:0;font:12px monospace;color:#9c9;white-space:pre-wrap}
.job{padding:2px 4px;cursor:pointer}
.job.sel{background:#3a3a26}
.tagb{padding:4px 10px}
input[type=checkbox]{accent-color:#6c6;width:15px;height:15px;margin:0;cursor:pointer}
label.chk{display:flex;align-items:center;gap:4px;cursor:pointer}
</style>
<div style="display:flex;justify-content:space-between;align-items:center;gap:10px">
<h2 style=margin:0>jpsub 主页</h2>
<button onclick=quitApp()>退出程序</button>
</div>
<div id=grid>
<div class=pane>
<h3>① 操作</h3>
<div class=sec><b class=st>输入</b>
<span class=lbl>sm号或URL</span>
<input id=url type=text placeholder="如 sm43168834" style="flex:1;min-width:120px">
<span class=lbl>本地视频</span>
<input id=vfile type=file accept=".mp4,.mkv,.webm,video/mp4,video/x-matroska,video/webm" style=display:none>
<button onclick=$('vfile').click()>选视频…</button>
<span id=locline style="color:#888;flex:1;min-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">未选择</span>
<div class=btns>
<button onclick=cmd('download',[])>下载并翻译</button>
<button onclick=cmd('download',['--download-only'])>仅下载</button>
<button onclick=cmd('download',['--notrans'])>下载不翻译</button>
<button onclick=cmd('download',['--burn'])>下载+烧录</button>
<button onclick=cmd('run',[])>翻译(run)</button>
<button onclick=cmd('run',['--burn'])>翻译+烧录</button>
<button onclick=cmd('run',['--notrans'])>OCR</button>
<button onclick=cmd('run',['--force'])>重新OCR翻译</button>
<button onclick=cmd('extract',['--force','--notrans'])>重新OCR</button>
</div></div>
<div class=sec><b class=st>编辑与翻译</b>
<div class=btns>
<button onclick=cmd('edit',[])>编辑</button>
<button onclick=cmd('mask',[])>打码</button>
<button onclick=openCrop()>选择原视频字幕区域</button>
<button onclick=cmd('translate',[])>续翻</button>
<button onclick=cmd('translate',['--force'])>重翻</button>
<button onclick=cmd('proofread',[])>AI 校对</button>
<button onclick=showUsage()>翻译用量</button>
<button onclick=openApi()>AI 接口/预设</button>
<button onclick=openGlossary()>术语表</button>
</div></div>
<div class=sec><b class=st>字幕文件</b>
<span class=lbl>字幕文件</span>
<input id=subfile type=text style="flex:1;min-width:140px" placeholder="本地字幕路径,如 D:\\sub.srt">
<select id=subop>
<option value=import>导入工作目录</option>
<option value=submerge>合并碎片</option>
<option value=layout>排版(空格换行→\\N)</option>
<option value=convert>格式转换</option>
</select>
<button onclick=runSub()>执行</button>
<span id=submsg style="color:#888"></span>
</div>
<div class=sec><b class=st>产出与分享</b>
<div class=btns>
<button onclick=cmd('render',[])>生成字幕</button>
<button onclick=cmd('burn',[])>烧录</button>
<button onclick=openUpload()>投稿</button>
<button onclick=copyInfo()>复制info</button>
<button onclick=copyTag()>复制tag</button>
</div></div>
<div class="sec danger"><b class=st>删除(不可恢复)</b>
<div class=btns>
<button onclick=delWork()>删除工作目录</button>
<button onclick=delVid()>删除视频</button>
</div></div>
<div class=row>
<label class=chk title="勾选后所有按钮触发的任务自动追加 --debug,把全流程日志写入 logs/debug.log">
<input type=checkbox id=dbg>Debug 日志(记录全流程到 logs/debug.log)</label>
</div>
<div class=row>
<span class=lbl>自定义命令</span>
<input id=ccmd type=text style="flex:1" placeholder="完整命令,如 download sm123 -v 720p -a best --download-only">
<button onclick=runCmd()>运行</button>
</div>
</div>
<div class=pane>
<h3>② output/ 视频与工作目录（点击行选中） <span id=selname style="font-weight:normal;color:#fc6;font-size:12px"></span> <button onclick=openDir()>打开文件夹</button></h3>
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
<b style=color:#aaa>① 输入</b><br>
<b>下载并翻译</b> — 完整流水线:下载→抽帧→OCR→翻译→生成ASS<br>
<b>仅下载</b> — --download-only,只下载视频到 output/,不抽帧不 OCR<br>
<b>下载不翻译</b> — --notrans,停在译文待编辑<br>
<b>下载+烧录</b> — --burn,字幕直接烧进视频(新工作目录按默认字幕样式,可在「编辑」里改样式后重烧)<br>
<b>翻译(run)</b> — 本地视频完整流水线<br>
<b>翻译+烧录</b> — run --burn,已有ASS直接烧<br>
<b>OCR</b> — --notrans,抽帧+OCR产出 segments.json,不翻译<br>
<b>重新OCR翻译</b> — run --force,无视已有结果重跑全流程<br>
<b>重新OCR</b> — extract --force,只重跑抽帧OCR<br>
<b style=color:#aaa>① 编辑与翻译(对选中项)</b><br>
<b>编辑</b> — 浏览器译文调整器(可一键还原OCR原始结果,并设置烧录字幕的颜色/描边/位置)<br>
<b>打码</b> — 打码选取器(生成masks.json)<br>
<b>选择字幕区</b> — 页内框选字幕区域,生成 --crop 参数存到工作目录,抽帧时自动优先使用<br>
<b>续翻</b> — translate,只翻没翻过的句子<br>
<b>重翻</b> — translate --force,忽略译文和缓存全部重翻<br>
<b style=color:#aaa>① 产出与分享(对选中项)</b><br>
<b>生成字幕</b> — render,用现有译文生成ASS<br>
<b>烧录</b> — 把ASS烧进视频;有masks.json时自动先打码再烧字幕<br>
<b>投稿</b> — 弹窗确认标题(AI 译)/简介/标签后投稿到 B 站,需先 biliup-rs 登录(见 settings.BILIUP_COOKIE)<br>
<b>复制info</b> — 把选中条目工作目录的 info.txt 复制到剪贴板<br>
<b>复制tag</b> — 弹窗列出 info.txt 标签行里的所有 tag,点击单个 tag 复制<br>
<b style=color:#aaa>① 删除(不可恢复,对选中项)</b><br>
<b>删除工作目录</b> — 删除选中条目的 <名称>.jpsub(字幕/译文全删,视频保留)<br>
<b>删除视频</b> — 删除选中条目的视频文件(工作目录保留)<br>
<b>AI 接口/预设</b> — 弹窗管理多组 API 供应商配置(名称/端点/密钥/模型),「★当前」那组用于翻译与校对;也可用 <code>jpsub preset</code> 命令管理<br>
<b>术语表</b> — 弹窗查看/编辑名词对照表(全局 <code>~/.jpsub/glossary.txt</code> 或选中条目的工作目录 <code>glossary.txt</code>),可一键识别候选角色名追加待填<br>
<b style=color:#aaa>其他</b><br>
<b>② 打开文件夹</b> — 用系统文件管理器打开 output 目录<br>
<b>③ 运行脚本</b> — 每行一条任务(等价 jpsub -s)批量执行,输出显示在下方<br>
<b>① 自定义命令</b> — 输入完整 jpsub 命令(子命令+参数,如 download sm123 -v 720p),点运行即在后台执行,输出显示在任务里<br>
<b>④ 终止选中</b> — 结束选中的任务;自定义参数框的内容会追加到所有命令后<br>
<b>Debug 日志</b> — 勾选后所有按钮发起的任务自动加 --debug,抽帧/筛选/OCR/合并全流程写到 logs/debug.log(排查时间轴异常用;批量脚本不受影响)<br>
<b>退出程序</b> — 右上角按钮,终止程序(含正在运行的任务)<br>
force 操作与覆盖旧文件前都会自动备份到工作目录 backup/时间戳/ 文件夹
</div>
<div id=apibox style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:103;overflow:auto" onclick="if(event.target===this)this.style.display='none'">
<div style="margin:60px auto;width:min(600px,94vw);background:#252525;border:1px solid #555;border-radius:8px;padding:14px">
<h3 style="margin:0 0 10px">AI 接口预设 <button onclick=$('apibox').style.display='none'>关闭</button></h3>
<div class=hint style="color:#888;margin-bottom:8px">保存多组供应商配置(名称 + 端点 + 密钥 + 模型),翻译/校对时使用「当前选中」的一组;优先级:命令行/环境变量 &gt; 预设 &gt; .env</div>
<div class=row style="align-items:center;gap:6px;flex-wrap:wrap">
<span class=lbl>选择</span>
<select id=apiSel onchange=apiUse() style="background:#333;color:#ddd;border:1px solid #555;border-radius:4px;padding:4px;min-width:160px"></select>
<span id=apiCur style="color:#fc6;font-size:12px"></span>
</div>
<label style="display:block;margin-top:8px">名称</label>
<input id=apiName type=text style="width:100%;box-sizing:border-box" placeholder="如 中转A / 官方B">
<label style="display:block;margin-top:6px">API 端点 (api_base)</label>
<input id=apiBase type=text style="width:100%;box-sizing:border-box" placeholder="https://.../v1">
<label style="display:block;margin-top:6px">API Key</label>
<input id=apiKey type=text style="width:100%;box-sizing:border-box" placeholder="sk-...  (留空则保留原值)">
<label style="display:block;margin-top:6px">模型</label>
<input id=apiModel type=text style="width:100%;box-sizing:border-box" placeholder="如 deepseek-chat">
<div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">
<button onclick=apiSave()>保存并设为当前</button>
<button onclick=apiDel()>删除</button>
<span id=apimsg style=color:#fc6></span>
</div>
</div>
</div>
<div id=glbox style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:104;overflow:auto" onclick="if(event.target===this)this.style.display='none'">
<div style="margin:50px auto;width:min(720px,94vw);background:#252525;border:1px solid #555;border-radius:8px;padding:14px">
<h3 style="margin:0 0 10px">术语表 <button onclick=$('glbox').style.display='none'>关闭</button></h3>
<div class=row style="align-items:center;gap:6px;flex-wrap:wrap">
<span class=lbl>范围</span>
<select id=glscope onchange=glossLoad() style="background:#333;color:#ddd;border:1px solid #555;border-radius:4px;padding:4px">
<option value=global>全局 ~/.jpsub/glossary.txt</option>
<option value=work>当前工作目录(② 选中)glossary.txt</option>
</select>
<button onclick=glossCand()>识别角色名</button>
<span id=glmsg style=color:#fc6></span>
</div>
<div style="color:#888;margin:6px 0">每行「原文 Tab 译文」(空格分隔也可以),# 开头为注释;改动后需重翻对应句子才生效</div>
<textarea id=gltext rows=16 spellcheck=false style="width:100%;box-sizing:border-box;background:#181818;color:#ddd;border:1px solid #555;border-radius:4px;font:13px monospace;padding:6px;resize:vertical"></textarea>
<div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
<button onclick=glossSave()>保存</button>
<button onclick=glossLoad()>重新载入</button>
<span id=glpath style="color:#888;font-size:12px"></span>
</div>
</div>
</div>
<div id=cropbox style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:100;overflow:auto">
<div style="margin:24px auto;width:max-content;max-width:94vw;background:#252525;border:1px solid #555;border-radius:8px;padding:12px">
<h3 style="margin:0 0 8px">字幕区选择 - <span id=cropname></span> <button onclick=closeCrop()>关闭</button></h3>
<div id=cropwrap style="position:relative;display:inline-block">
<canvas id=cropcv style="display:block;background:#000;width:min(58vw,68vh)"></canvas>
<div id=cropov style="position:absolute;left:0;top:0;width:100%;height:100%;cursor:crosshair"></div>
</div>
<div style="margin-top:6px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
时间 <input id=cropt type=text value=0:00.0 style=width:70px>
<input type=range id=cropslider min=0 max=100 step=0.1 value=0 style=width:220px>
</div>
<div style="margin-top:6px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
crop参数 <input id=cropval type=text readonly style="width:260px;background:#181818;color:#8cf">
<button id=cropsave onclick=saveCrop()>保存</button>
<button onclick=clearCrop()>清除</button>
<span id=cropmsg style=color:#fc6></span>
</div>
<div class=hint style="color:#888;margin-top:4px">在画面上左键拖框选出字幕区域,左键拖动选框内部移动位置,右键拖动选框边缘/角调整大小,保存后抽帧(--crop 未显式指定时)优先使用;清除则恢复默认。拖动时间滑杆换画面预览。</div>
</div>
</div>
<div id=tagbox style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:101;overflow:auto" onclick="if(event.target===this)this.style.display='none'">
<div style="margin:60px auto;width:max-content;max-width:80vw;background:#252525;border:1px solid #555;border-radius:8px;padding:14px">
<h3 style="margin:0 0 10px">标签 - <span id=tagname></span> <button onclick=$('tagbox').style.display='none'>关闭</button></h3>
<div id=taglist style="display:flex;flex-wrap:wrap;gap:6px;max-width:70vw"></div>
<div class=hint style="color:#888;margin-top:8px">点击任意标签复制到剪贴板;点弹层外部或「关闭」收起</div>
</div>
</div>
<div id=upbox style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:102;overflow:auto" onclick="if(event.target===this)this.style.display='none'">
<div style="margin:60px auto;width:min(680px,92vw);background:#252525;border:1px solid #555;border-radius:8px;padding:14px">
<h3 style="margin:0 0 10px">投稿 - <span id=upname></span></h3>
<label>标题前缀(保存后长期有效)</label>
<div style="display:flex;gap:6px">
<input id=upprefix type=text style="flex:1">
<button onclick=savePrefix()>保存前缀</button>
</div>
<label style="display:block;margin-top:8px">标题(上限80字,留空用原标题)</label>
<input id=uptitle type=text style="width:100%;box-sizing:border-box">
<label style="display:block;margin-top:8px">简介</label>
<textarea id=updesc rows=6 style="width:100%;box-sizing:border-box;background:#333;color:#ddd;border:1px solid #555;border-radius:4px;font:inherit;padding:4px;resize:vertical"></textarea>
<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:8px">
<label>标签 <input id=uptags type=text style=width:300px placeholder="逗号分隔"></label>
<label>分区 <select id=uptid style="background:#333;color:#ddd;border:1px solid #555;border-radius:4px;padding:4px">
<option value=22>鬼畜调教</option>
<option value=26>音MAD</option>
<option value=126>人力VOCALOID</option>
<option value=216>鬼畜剧场</option>
<option value=127>教程演示</option>
<option value=24>MAD·AMV(动画)</option>
<option value=27>综合(动画)</option>
<option value=21>日常(生活)</option>
</select></label>
<label>定时(秒) <input id=updelay type=number style=width:80px title="从现在起延迟多少秒发布,0=立即"></label>
</div>
<div style="margin-top:10px">
<button onclick=doUpload()>确认投稿</button>
<button onclick=$('upbox').style.display='none'>取消</button>
<span id=upmsg style=color:#fc6></span>
</div>
</div>
</div>
<script>
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
const $=id=>document.getElementById(id);
async function post(path,body){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  return r.json();
}
let selName=null;
function setSel(n){selName=n;
  const el=$('selname');if(el)el.textContent=n||'未选择';
  document.querySelectorAll('#list tr').forEach(tr=>
    tr.classList.toggle('sel',tr.dataset.name===n));}
async function delWork(){
  if(!selName)return alert('请先在 ② 点击选中一个条目');
  if(!confirm('确定删除 '+selName+' 的工作目录?\\n字幕、译文等将全部删除,视频保留'))return;
  const j=await post('/del',{name:selName});
  if(!j.ok)alert('失败:'+j.err);else{setSel(null);refresh()}
}
async function delVid(){
  if(!selName)return alert('请先在 ② 点击选中一个条目');
  if(!confirm('确定删除 '+selName+' 的视频文件?\\n字幕/译文等工作目录内其他文件保留'))return;
  const j=await post('/delvid',{name:selName});
  if(!j.ok)alert('失败:'+j.err);else{alert('已删除 '+j.n+' 个视频');refresh()}
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
async function runCmd(){
  const t=$('ccmd').value.trim();
  if(!t)return alert('请输入命令,如 download sm123 -v 720p');
  const j=await post('/runcmd',{text:t});
  if(!j.ok)alert('失败:'+j.err);else{$('ccmd').value='';refresh();refreshJobs()}
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
async function runSub(){
  const f=$('subfile').value.trim();
  if(!f)return alert('请先填写本地字幕文件路径');
  const j=await post('/subtool',{op:$('subop').value,file:f,name:selName,extra:$('extra').value.trim()});
  $('submsg').textContent=j.ok?'已提交':'失败:'+j.err;
  if(!j.ok)alert('失败:'+j.err);else refreshJobs();
}
async function showUsage(){
  const j=await(await fetch('/usage')).json();
  alert(j.text||'暂无用量记录');
}
let apiCache=[];
async function openApi(){
  $('apibox').style.display='block';
  await apiLoad();
}
async function apiLoad(){
  const j=await(await fetch('/presets')).json();
  if(!j.ok)return alert('失败:'+j.err);
  apiCache=j.presets||[];
  const sel=$('apiSel');
  sel.innerHTML=apiCache.map(p=>`<option value="${esc(p.name)}">${esc(p.name)}${p.name===j.current?' ★当前':''}</option>`).join('')
    ||'<option value="">(暂无预设)</option>';
  $('apiCur').textContent=j.current?('当前:'+j.current):'当前:(未选中)';
  const cur=apiCache.find(p=>p.name===j.current);
  if(cur)fillApi(cur);
}
function fillApi(p){
  $('apiName').value=p.name;$('apiBase').value=p.api_base||'';
  $('apiModel').value=p.model||'';$('apiKey').value='';
  $('apiKey').placeholder=p.has_key?'sk-...  (留空则保留原值)':'sk-...';
}
async function apiUse(){
  const name=$('apiSel').value;if(!name)return;
  const p=apiCache.find(x=>x.name===name);if(p)fillApi(p);
  const j=await post('/presets',{action:'use',name});
  if(!j.ok)alert('失败:'+j.err);else apiLoad();
}
async function apiSave(){
  const j=await post('/presets',{action:'save',name:$('apiName').value.trim(),
    api_base:$('apiBase').value.trim(),api_key:$('apiKey').value.trim(),
    model:$('apiModel').value.trim()});
  $('apimsg').textContent=j.ok?'已保存':'失败:'+j.err;
  if(!j.ok)alert('失败:'+j.err);else apiLoad();
}
async function apiDel(){
  const name=$('apiSel').value;if(!name)return;
  if(!confirm('确定删除预设 '+name+' ?'))return;
  const j=await post('/presets',{action:'del',name});
  if(!j.ok)alert('失败:'+j.err);else apiLoad();
}
async function openGlossary(){
  $('glbox').style.display='block';
  await glossLoad();
}
function glossScope(){return {scope:$('glscope').value,name:selName||''}}
async function glossLoad(){
  const s=glossScope();
  const j=await(await fetch('/glossary?scope='+encodeURIComponent(s.scope)+'&name='+encodeURIComponent(s.name))).json();
  if(!j.ok){$('glmsg').textContent='失败:'+j.err;$('glpath').textContent='';return}
  $('glpath').textContent=j.path;
  $('gltext').value=j.text||'';
  $('glmsg').textContent=j.count+' 条';
}
async function glossSave(){
  const s=glossScope();
  const j=await post('/glossary',{scope:s.scope,name:s.name,text:$('gltext').value});
  $('glmsg').textContent=j.ok?('已保存,'+j.count+' 条'):('失败:'+j.err);
}
async function glossCand(){
  if(!selName)return alert('请先在 ② 选中一个工作目录条目');
  const j=await(await fetch('/glossary-candidates?name='+encodeURIComponent(selName))).json();
  if(!j.ok)return alert('失败:'+j.err);
  if(!j.candidates.length)return alert('未识别出候选角色名');
  const have=$('gltext').value;
  const keys=new Set(have.split('\n').map(l=>l.split(/[\t ]/)[0].trim()));
  const add=j.candidates.filter(c=>!keys.has(c));
  if(!add.length)return $('glmsg').textContent='候选角色名已全部在表中';
  $('gltext').value=(have&&!have.endsWith('\n')?have+'\n':have)+add.map(c=>c+'\t').join('\n')+'\n';
  $('glmsg').textContent='已追加 '+add.length+' 条候选,填写译名后点保存';
}
async function cmd(sub,flags){
  const j=await post('/cmd',{sub,flags,name:selName,
    url:$('url').value.trim(),path:localPath,
    extra:$('extra').value.trim(),debug:$('dbg').checked});
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
let lastList='';
async function refresh(){
  const j=await(await fetch('/list')).json();
  const s=JSON.stringify(j.items);
  if(s===lastList)return;  // 无变化不重建表格,避免重建瞬间吞掉点击
  lastList=s;
  const tb=$('list');
  tb.innerHTML='<tr><th style=width:44%>名称</th><th style=width:26%>选择字幕区</th><th>状态</th></tr>';
  j.items.forEach(it=>{
    const tr=document.createElement('tr');
    tr.dataset.name=it.name;
    if(it.name===selName)tr.classList.add('sel');
    let badge='';
    if(!it.work)badge='<span class="badge warn">未抽取</span>';
    else if(it.ass)badge='<span class="badge ok">已生成字幕</span>';
    if(it.unt>0)badge+=` <span class="badge warn">${it.unt} 条未译</span>`;
    tr.innerHTML=`<td>${esc(it.name)}${badge}</td>`+
      `<td>${it.crop?esc(it.crop):'默认'}</td>`+
      `<td>${it.video?'有视频':'无视频'}${it.work?' / 有工作目录':''}</td>`;
    tr.onclick=()=>setSel(it.name);
    tb.appendChild(tr);
  });
}
async function copyInfo(){
  if(!selName)return alert('请先在 ② 点击选中一个条目');
  const j=await(await fetch('/info?name='+encodeURIComponent(selName))).json();
  if(!j.ok)return alert('失败:'+j.err);
  try{await navigator.clipboard.writeText(j.text);alert('info 已复制到剪贴板')}
  catch(e){prompt('浏览器拒绝访问剪贴板,请手动复制:',j.text)}
}
async function copyTag(){
  if(!selName)return alert('请先在 ② 点击选中一个条目');
  const j=await(await fetch('/tags?name='+encodeURIComponent(selName))).json();
  if(!j.ok)return alert('失败:'+j.err);
  if(!j.tags.length)return alert('info 里没有标签行(旧下载或模板去掉了 {tags})');
  $('tagname').textContent=selName;
  $('taglist').innerHTML=j.tags.map(t=>
    `<button class=tagb onclick=cpTag(this) data-t="${esc(t)}">${esc(t)}</button>`).join('');
  $('tagbox').style.display='block';
}
async function cpTag(b){
  try{await navigator.clipboard.writeText(b.dataset.t);
    b.textContent='✓ 已复制';setTimeout(()=>b.textContent=b.dataset.t,800)}
  catch(e){prompt('浏览器拒绝访问剪贴板,请手动复制:',b.dataset.t)}
}
async function openUpload(){  // 投稿确认弹窗:预填原标题/简介/标签,标题由用户填写
  if(!selName)return alert('请先在 ② 点击选中一个条目');
  $('upname').textContent=selName;$('upmsg').textContent='加载元信息…';
  $('upbox').style.display='block';
  const j=await(await fetch('/upload-meta?name='+encodeURIComponent(selName))).json();
  if(!j.ok){alert('失败:'+j.err);$('upbox').style.display='none';return}
  $('upprefix').value=j.prefix||'';
  const t=$('uptitle');t.value=j.title;t.placeholder=j.orig_title||'';
  $('updesc').value=j.desc;
  $('uptags').value=j.tags;
  $('uptid').value=String(j.tid);
  if(!$('uptid').value)$('uptid').value='22';
  $('updelay').value=j.delay;
  $('upmsg').textContent='';
}
async function savePrefix(){
  const j=await post('/upload-prefix',{prefix:$('upprefix').value});
  if(!j.ok)alert('失败:'+j.err);else $('upmsg').textContent='前缀已保存';
}
async function doUpload(){
  if(!$('uptitle').value.trim()&&$('upprefix').value.trim())
    return alert('标题为空时不能只用前缀,请填写标题');
  $('upmsg').textContent='提交中…';
  const j=await post('/upload',{name:selName,title:$('uptitle').value,
    desc:$('updesc').value,tags:$('uptags').value,
    tid:+$('uptid').value||0,delay:+$('updelay').value||0});
  if(!j.ok)alert('失败:'+j.err);
  else{$('upbox').style.display='none';refreshJobs()}
}
async function quitApp(){
  if(!confirm('确定退出 jpsub 程序?正在运行的任务将被终止'))return;
  quitting=true;  // 停掉心跳,让 /quit 的退出流程不受干扰
  try{await post('/quit',{})}catch(e){}
  window.close();  // 页面随程序一同关闭(浏览器允许时);失败则显示遮罩提示
  const d=document.createElement('div');
  d.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.85);color:#eee;display:flex;align-items:center;justify-content:center;font-size:22px;z-index:99999';
  d.textContent='程序已退出,请关闭此页面';
  document.body.appendChild(d);
}
// ---- 心跳:程序退出(无论何种方式)后自动关闭页面/显示遮罩 ----
let quitting=false;  // 主动退出中:停掉心跳,避免打断 /quit 的退出流程
setInterval(async()=>{
  if(quitting)return;
  try{
    const r=await fetch('/ping?t='+Date.now());
    if(!r.ok)throw 0;
  }catch(e){
    if(document.getElementById('deadov'))return;
    window.close();  // 浏览器允许时直接关页(脚本打开的窗口可以)
    const d=document.createElement('div');d.id='deadov';
    d.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.85);color:#eee;display:flex;align-items:center;justify-content:center;font-size:22px;z-index:99999';
    d.textContent='程序已退出,请关闭此页面';
    document.body.appendChild(d);
  }
},3000);
// 关闭/刷新页面不影响程序(仅网页「退出程序」按钮才终止)
// ---- 字幕区选择器(页内弹层,框选生成 --crop 参数) ----
let cname=null,cw=0,ch=0,cdur=0,cropT=0,csel=null,cdrag=null,chov=null,cfetch=false,cpending=null,clast=null;
const fmtT=s=>`${Math.floor(s/60)}:${(s%60).toFixed(1).padStart(4,'0')}`;
const parseT=s=>{s=String(s).trim();if(/^\\d+(\\.\\d+)?$/.test(s))return+s;
  const p=s.split(':');if(p.length>3)return NaN;
  return p.reduce((a,v)=>a*60+(+v||0),0)};
async function openCrop(){
  if(!selName)return alert('请先在 ② 点击选中一个条目');
  const j=await(await fetch('/crop-meta?name='+encodeURIComponent(selName))).json();
  if(!j.ok)return alert('失败:'+j.err);
  cname=selName;cw=j.vw;ch=j.vh;cdur=j.dur;csel=null;cdrag=null;chov=null;
  $('cropname').textContent=selName;
  $('cropval').value=j.crop||'';
  $('cropmsg').textContent='';
  $('cropt').value=fmtT(0);
  const sl=$('cropslider');sl.max=cdur;sl.value=0;
  $('cropbox').style.display='block';
  const cv=$('cropcv');cv.width=cw;cv.height=ch;
  cropT=0;loadCropFrame();
}
function closeCrop(){$('cropbox').style.display='none';refresh()}
function loadCropFrame(){  // 单飞+尾随:快速拖动滑杆时只补最新一帧
  if(cfetch){cpending=cropT;return}
  cfetch=true;
  const im=new Image();
  im.onload=()=>{clast=im;drawCrop(im);cfetch=false;
    if(cpending!==null){cpending=null;loadCropFrame()}};
  im.onerror=()=>{cfetch=false};
  im.src='/crop-frame?name='+encodeURIComponent(cname)+'&t='+cropT.toFixed(3)+'&r='+Math.random();
}
function calcCrop(r){  // 原视频像素矩形 -> --crop 的 上:下:左:右 边距比例
  const top=r[1]/ch,bot=1-(r[1]+r[3])/ch,left=r[0]/cw,right=1-(r[0]+r[2])/cw;
  return [top,bot,left,right].map(v=>Math.max(0,Math.min(1,v)).toFixed(4)).join(':');
}
function drawCrop(im){  // 不传图时用缓存帧重绘,避免拖动/松手后画面变黑
  const cv=$('cropcv'),ctx=cv.getContext('2d');
  ctx.clearRect(0,0,cw,ch);
  im=im||clast;
  if(im)ctx.drawImage(im,0,0,cw,ch);
  for(const r of [chov,csel]){
    if(!r)continue;
    ctx.strokeStyle=r===csel?'#0f0':'#f44';
    ctx.setLineDash(r===csel?[]:[5,3]);ctx.lineWidth=2;
    ctx.strokeRect(r[0],r[1],r[2],r[3]);ctx.setLineDash([]);
  }
}
const cropsc=()=>{const r=$('cropcv').getBoundingClientRect();return r.width/cw};
$('cropov').oncontextmenu=e=>e.preventDefault();
function normSel(x0,y0,x1,y1){  // 任意两点 -> 规范化选框(≥5px 才有效)
  const x=Math.max(0,Math.round(Math.min(x0,x1))),y=Math.max(0,Math.round(Math.min(y0,y1)));
  const w=Math.min(Math.round(Math.abs(x1-x0)),cw-x),h=Math.min(Math.round(Math.abs(y1-y0)),ch-y);
  return (w<5||h<5)?null:[x,y,w,h];
}
function nearSel(px,py){  // 距选框边缘 8px 内返回可拖的边/角 {l,t,r,b}
  if(!csel)return null;
  const T=8/cropsc(),[x,y,w,h]=csel;
  const l=Math.abs(px-x)<=T,r=Math.abs(px-(x+w))<=T,t=Math.abs(py-y)<=T,b=Math.abs(py-(y+h))<=T;
  const inx=px>=x-T&&px<=x+w+T,iny=py>=y-T&&py<=y+h+T;
  if(!((l||r)&&iny)&&!((t||b)&&inx))return null;
  return {l,t,r,b};
}
$('cropov').onpointerdown=e=>{
  const r=$('cropcv').getBoundingClientRect(),s=cropsc();
  const px=(e.clientX-r.left)/s,py=(e.clientY-r.top)/s;
  $('cropov').setPointerCapture(e.pointerId);
  if(e.button===2){  // 右键:靠近已有选框边缘/角则调整大小,否则忽略
    const g=nearSel(px,py);
    if(g&&csel)cdrag={mode:'rsz',g,orig:csel.slice(),ox:px,oy:py};
    return;
  }
  if(e.button!==0)return;
  const g=nearSel(px,py);  // 边缘带优先调整大小,框内则移动,框外重新框选
  if(g&&csel){cdrag={mode:'rsz',g,orig:csel.slice(),ox:px,oy:py};return}
  if(csel&&px>csel[0]&&px<csel[0]+csel[2]&&py>csel[1]&&py<csel[1]+csel[3]){
    cdrag={mode:'mv',orig:csel.slice(),ox:px,oy:py};return}
  cdrag={mode:'new',x0:px,y0:py};chov=[px,py,px,py];
};
$('cropov').onpointermove=e=>{
  const r=$('cropcv').getBoundingClientRect(),s=cropsc();
  const px=(e.clientX-r.left)/s,py=(e.clientY-r.top)/s;
  if(!cdrag){$('cropov').style.cursor=nearSel(px,py)?'nwse-resize':'crosshair';return}
  if(cdrag.mode==='new'){
    chov=[cdrag.x0,cdrag.y0,px-cdrag.x0,py-cdrag.y0];  // strokeRect 接受负宽高,实时预览跟随鼠标
    drawCrop();return;
  }
  if(cdrag.mode==='mv'){  // 左键移动:整体平移,钳制在画面内
    const [x,y,w,h]=cdrag.orig;
    chov=[Math.max(0,Math.min(x+px-cdrag.ox,cw-w)),Math.max(0,Math.min(y+py-cdrag.oy,ch-h)),w,h];
    drawCrop();return;
  }
  const [x,y,w,h]=cdrag.orig,g=cdrag.g;  // 右键调整:拖到的位置替换对应边
  const x0=g.l?px:x,x1=g.r?px:x+w,y0=g.t?py:y,y1=g.b?py:y+h;
  chov=[x0,y0,x1-x0,y1-y0];
  drawCrop();
};
$('cropov').onpointerup=e=>{
  if(!cdrag)return;
  const r=$('cropcv').getBoundingClientRect(),s=cropsc();
  const px=(e.clientX-r.left)/s,py=(e.clientY-r.top)/s;
  let sel=null;
  if(cdrag.mode==='new')sel=normSel(cdrag.x0,cdrag.y0,px,py);
  else if(cdrag.mode==='mv')sel=[Math.round(chov[0]),Math.round(chov[1]),cdrag.orig[2],cdrag.orig[3]];
  else{const [x,y,w,h]=cdrag.orig,g=cdrag.g;
    sel=normSel(g.l?px:x,g.t?py:y,g.r?px:x+w,g.b?py:y+h)||csel}  // 拖太小则保持原框
  cdrag=null;chov=null;
  if(sel){csel=sel;$('cropval').value=calcCrop(csel);$('cropmsg').textContent=''}
  drawCrop();
};
function setCropT(v){
  cropT=Math.min(cdur,Math.max(0,v));
  $('cropt').value=fmtT(cropT);$('cropslider').value=cropT;
  loadCropFrame();
}
$('cropt').onchange=()=>{const v=parseT($('cropt').value);if(!isNaN(v))setCropT(v)};
$('cropslider').oninput=()=>setCropT(+$('cropslider').value);
async function saveCrop(){
  if(!$('cropval').value)return alert('请先在画面上拖框选择字幕区');
  const j=await post('/crop-save',{name:cname,crop:$('cropval').value});
  if(!j.ok)alert('失败:'+j.err);
  else{$('cropmsg').textContent='已保存,抽帧时将优先使用';refresh()}
}
async function clearCrop(){
  const j=await post('/crop-save',{name:cname,crop:null});
  if(!j.ok)alert('失败:'+j.err);
  else{$('cropval').value='';$('cropmsg').textContent='已清除,恢复默认字幕区';refresh()}
}
refresh();refreshJobs();
setInterval(refresh,3000);setInterval(refreshJobs,1000);
</script>"""


def _work_of_name(root: Path, name: str) -> Path:
    """按列表名找工作目录:<name> 目录。"""
    return utils.work_of_name(root, name)


def _glossary_count(text: str) -> int:
    """术语表有效条目数(排除空行与 # 注释)。"""
    return sum(1 for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#"))


def _glossary_path(root: Path, scope: str, name: str) -> tuple[Path, str]:
    """术语表文件路径:global 为 ~/.jpsub/glossary.txt,work 为选中工作目录下的 glossary.txt。"""
    if scope == "work":
        if not name:
            return Path(), "请先在 ② 选中一个工作目录条目"
        return _work_of_name(root, name) / "glossary.txt", ""
    return Path.home() / ".jpsub" / "glossary.txt", ""


def _scan_output(root: Path) -> list[dict]:
    """扫描 output/,以工作目录为主条目。

    新布局:任意名目录,视频/ASS/info/cover 在目录内,内部文件在 .jpsub/ 下;
    根目录散视频作为本地视频条目列出。
    """
    stems: dict[str, dict] = {}
    if not root.is_dir():
        return []
    for p in sorted(root.iterdir()):
        if p.name.startswith(("_", ".")):
            continue
        if p.is_dir():
            if (
                (p / handoff.HIDDEN_DIR).is_dir()
                or (p / "segments.json").is_file()
                or any(
                    (p / f).is_file()
                    for f in ("info.txt", "cover.jpg")
                )
                or any(
                    q.suffix.lower() in VID_EXTS
                    for q in p.iterdir()
                    if q.is_file()
                )
            ):
                stems.setdefault(p.name, {"name": p.name})
        elif p.is_file() and p.suffix.lower() in VID_EXTS:
            d = stems.setdefault(p.stem, {"name": p.stem})  # 散视频条目
            d["legacy_video"] = True
    items = []
    for d in stems.values():
        name = d["name"]
        work = _work_of_name(root, name)
        video = _named_video(root, name)
        d["video"] = video is not None
        d.setdefault("work", work.is_dir())
        d["crop"] = cli._read_crop(work) if work.is_dir() else None
        d["ass"] = (work / (name + ".ass")).exists() or (
            root / (name + ".ass")
        ).exists()
        unt = 0
        if work.is_dir():
            seg_file = handoff.seg_path(work)
            if seg_file.exists():
                try:
                    segs = handoff.read_segments(seg_file)
                    from .cache import TranslationCache

                    cache = TranslationCache(handoff.cache_path(work))
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


def _scan_output_cached(home: "_Home") -> list[dict]:
    """带缓存的扫描:用轻量 mtime 签名判断目录树是否变化,未变直接复用上次结果,
    避免每 3 秒轮询都重读全部工作目录的 segments/cache 导致 /list 卡顿。"""
    root = home.root
    sig = []
    if root.is_dir():
        for p in sorted(root.iterdir()):
            if p.name.startswith(("_", ".")):
                continue
            try:
                sig.append((p.name, p.stat().st_mtime))
            except OSError:  # noqa: BLE001
                continue
            if p.is_dir():
                for f in (
                    "segments.json",
                    "cache.json",
                    "crop.json",
                    handoff.HIDDEN_DIR + "/segments.json",
                    handoff.HIDDEN_DIR + "/cache.json",
                    handoff.HIDDEN_DIR + "/crop.json",
                ):
                    fp = p / f
                    if fp.exists():
                        try:
                            sig.append((f, fp.stat().st_mtime))
                        except OSError:  # noqa: BLE001
                            pass
    sig = tuple(sig)
    if home._scan_cache is not None and home._scan_sig == sig:
        return home._scan_cache
    items = _scan_output(root)
    home._scan_sig, home._scan_cache = sig, items
    return items


def _named_video(root: Path, name: str) -> Path | None:
    """按条目名找视频,复用 utils.named_video。"""
    return utils.named_video(root, name)


class _Home:
    def __init__(self, root: Path):
        import contextlib
        import queue

        from http.server import BaseHTTPRequestHandler

        self.root = root
        self.jobs: list[dict] = []  # {proc|inline, desc, st, line}
        self._scan_sig = None  # output 扫描结果缓存:目录树未变时 /list 直接复用
        self._scan_cache: list[dict] | None = None
        home = self

        # ---- 操作/任务日志:全部追加到项目根 logs/home.log(与 cwd 无关) ----
        _log_lock = threading.Lock()
        _log_path = Path(__file__).resolve().parent.parent / "logs" / "home.log"

        def _log(msg: str) -> None:
            with _log_lock:
                try:
                    _log_path.parent.mkdir(parents=True, exist_ok=True)
                    with _log_path.open("a", encoding="utf-8") as f:
                        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
                except OSError:
                    pass

        _log(f"主页启动:{root}")
        self._log = _log  # 供退出等外部位置调用

        def _on_line(j: dict, line: str) -> None:
            j["line"] = line
            _log(f"[{j.get('label') or '任务'}] {line}")
            if j.get("show"):
                lines = j.setdefault("lines", [])
                if line.startswith("[第 ") and lines and lines[-1].startswith("[第 "):
                    lines[-1] = line  # 翻译进度原地刷新,不刷屏
                else:
                    lines.append(line)
                    del lines[:-200]

        # ---- 进程内任务队列:任务串行执行 ----
        def _worker():
            while True:
                j, args = home._q.get()
                if j.get("cancel"):
                    j["st"] = "fail"
                    j["line"] = "已终止"
                    home._q.task_done()
                    continue
                home._cur = j
                _log(f"内联任务开始:{j.get('label')} args={args}")
                try:
                    with (
                        contextlib.redirect_stdout(
                            progress.LineCapture(
                                lambda ln, j=j: _on_line(j, ln),
                                echo=sys.stdout,
                                guard=lambda: not j.get("cancel"),
                            )
                        ),
                        contextlib.redirect_stderr(
                            progress.LineCapture(
                                lambda ln, j=j: _on_line(j, ln),
                                guard=lambda: not j.get("cancel"),
                            )
                        ),  # tqdm 走 stderr
                    ):
                        cli.run(args)
                    j["st"] = "done"
                    j["line"] = "已完成"
                    _log(f"内联任务完成:{j.get('label')}")
                except SystemExit as e:  # noqa: BLE001
                    j["st"] = "fail"
                    j["line"] = str(e) or "失败"
                    _log(f"内联任务失败:{j.get('label')} {j['line']}")
                except Exception as e:  # noqa: BLE001
                    j["st"] = "fail"
                    j["line"] = f"{e}"
                    _log(f"内联任务异常:{j.get('label')} {j['line']}")
                finally:
                    home._cur = None
                    home._q.task_done()

        self._q = queue.Queue()
        self._cur = None
        threading.Thread(target=_worker, daemon=True).start()

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
                        _log(f"[{j.get('label') or j.get('desc') or '子进程'}] {line}")
                        if j.get("show"):
                            lines = j.setdefault("lines", [])
                            lines.append(line)
                            del lines[:-200]

        # ---- 关闭主页页面不影响程序:仅网页「退出程序」按钮才终止 ----

        def spawn(
            desc: str,
            cmd: list[str],
            label: str = "",
            kind: str = "long",
            show: bool = False,
        ) -> int:
            """kind: long=长任务等退出; launch=启动型(编辑/打码,常驻),立即标完成。"""
            import shlex

            _log(f"启动子进程[{kind}]{f' {label}' if label else ''}:{shlex.join(cmd)}")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=(
                    sys.platform != "win32"
                ),  # 独立进程组,终止时连子孙一起杀
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                if sys.platform == "win32"
                else 0,
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
            if kind == "launch":
                # 编辑/打码等窗口进程:只是主页启动的独立窗口,其退出不联动主页退出
                def _watch(p=proc):
                    code = p.wait()
                    j["st"] = "done" if code == 0 else "fail"
                    if code != 0 and not str(j.get("line", "")).strip():
                        j["line"] = f"异常退出(code={code})"
                    _log(
                        f"窗口进程退出(code={code}),主页保持运行:{desc}"
                    )

                threading.Thread(target=_watch, daemon=True).start()
            return len(home.jobs) - 1

        self.spawn = spawn

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/":
                    utils.http_page(self, _PAGE)
                elif self.path.startswith("/ping"):  # 心跳:页面据此检测程序是否已退出
                    utils.http_json(self, {})
                elif self.path == "/list":
                    utils.http_json(self, {"items": _scan_output_cached(home)})
                elif self.path.startswith("/upload-video"):
                    # 原生文件对话框选中的视频上传到临时目录(同打码选图片机制)
                    import tempfile
                    import urllib.parse

                    q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    name = Path(q.get("name", ["video.mp4"])[0].replace("\\", "/")).name
                    if Path(name).suffix.lower() not in VID_EXTS:
                        utils.http_json(self, {"ok": False, "err": f"不是视频文件:{name}"})
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
                        utils.http_json(self, {"ok": True, "path": str(dst)})
                    except Exception as e:  # noqa: BLE001
                        utils.http_json(self, {"ok": False, "err": str(e)})
                elif self.path.startswith("/crop-meta?"):
                    # 字幕区选择器元数据:视频尺寸/时长 + 已保存的 crop 参数
                    import urllib.parse

                    name = str(
                        urllib.parse.parse_qs(
                            urllib.parse.urlparse(self.path).query
                        ).get("name", [""])[0]
                    )
                    work = _work_of_name(home.root, name)
                    video = _named_video(home.root, name)
                    if not name or video is None:
                        utils.http_json(self, {"ok": False, "err": "该条目没有视频文件"})
                        return
                    from .mask import video_duration, video_size

                    try:
                        vw, vh = video_size(video)
                        dur = video_duration(video)
                    except Exception as e:  # noqa: BLE001
                        utils.http_json(self, {"ok": False, "err": f"读取视频信息失败:{e}"})
                        return
                    utils.http_json(self, 
                        {
                            "ok": True,
                            "vw": vw,
                            "vh": vh,
                            "dur": dur,
                            "crop": cli._read_crop(work) if work.is_dir() else None,
                        }
                    )
                elif self.path.startswith("/crop-frame?"):
                    # 字幕区选择器取帧:ffmpeg 抽指定时刻整帧 JPEG
                    import urllib.parse

                    q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    name = q.get("name", [""])[0]
                    video = _named_video(home.root, name)
                    if video is None:
                        self.send_error(404)
                        return
                    from .mask import frame_jpeg

                    try:
                        data = frame_jpeg(video, float(q.get("t", ["0"])[0]))
                    except Exception:  # noqa: BLE001
                        self.send_error(500)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif self.path.startswith("/info?"):
                    # 复制info:读工作目录 info.txt 返回给前端复制到剪贴板(不含标签行)
                    import urllib.parse

                    name = str(
                        urllib.parse.parse_qs(
                            urllib.parse.urlparse(self.path).query
                        ).get("name", [""])[0]
                    )
                    fp = _work_of_name(home.root, name) / "info.txt"
                    if not name or not fp.is_file():
                        utils.http_json(self, {"ok": False, "err": "工作目录没有 info.txt"})
                        return
                    text = "\n".join(
                        ln
                        for ln in fp.read_text(encoding="utf-8").splitlines()
                        if not ln.startswith("标签:")
                    )
                    utils.http_json(self, {"ok": True, "text": text})
                elif self.path.startswith("/tags?"):
                    # 复制tag:解析 info.txt 的「标签:」行,返回 tag 列表
                    import urllib.parse

                    name = str(
                        urllib.parse.parse_qs(
                            urllib.parse.urlparse(self.path).query
                        ).get("name", [""])[0]
                    )
                    fp = _work_of_name(home.root, name) / "info.txt"
                    tags: list[str] = []
                    if name and fp.is_file():
                        for ln in fp.read_text(encoding="utf-8").splitlines():
                            if ln.startswith("标签:"):
                                tags = [
                                    t.strip()
                                    for t in ln.partition("标签:")[2].split(",")
                                    if t.strip()
                                ]
                    utils.http_json(self, {"ok": True, "tags": tags})
                elif self.path.startswith("/upload-meta?"):
                    # 投稿确认表单预填:组装稿件元信息(标题走一次 AI 翻译)
                    import urllib.parse

                    name = str(
                        urllib.parse.parse_qs(
                            urllib.parse.urlparse(self.path).query
                        ).get("name", [""])[0]
                    )
                    work = _work_of_name(home.root, name)
                    if not name or not work.is_dir():
                        utils.http_json(
                            self, {"ok": False, "err": "工作目录不存在,先跑完流水线"}
                        )
                        return
                    if (work / "upload.json").is_file():
                        utils.http_json(
                            self, {"ok": False, "err": "该条目已投稿过(upload.json 存在)"}
                        )
                        return
                    from .upload import build_meta, load_prefix

                    try:
                        meta = build_meta(work)
                    except Exception as e:  # noqa: BLE001
                        utils.http_json(self, {"ok": False, "err": str(e)})
                        return
                    utils.http_json(self, {"ok": True, "prefix": load_prefix(), **meta})
                elif self.path == "/presets":
                    # API 配置预设(~/.jpsub/api-presets.json):网页里选择/保存供应商
                    from . import aiassist as _a

                    d = _a.load_presets()
                    items = [
                        {
                            "name": n,
                            "api_base": c.get("api_base", ""),
                            "model": c.get("model", ""),
                            "has_key": bool(c.get("api_key")),
                        }
                        for n, c in d["presets"].items()
                    ]
                    utils.http_json(
                        self, {"ok": True, "current": d.get("current", ""), "presets": items}
                    )
                elif self.path == "/usage":
                    # 翻译用量统计(持久化在 ~/.jpsub/usage.json)
                    from . import aiassist

                    utils.http_json(
                        self,
                        {"ok": True, "text": aiassist.usage_summary()},
                    )
                elif self.path.startswith("/glossary-candidates"):
                    # 从选中工作目录的原文自动识别候选角色名
                    import urllib.parse

                    q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    name = q.get("name", [""])[0]
                    seg_file = handoff.seg_path(_work_of_name(home.root, name))
                    if not seg_file.is_file():
                        utils.http_json(self, {"ok": False, "err": f"找不到 {seg_file.name}"})
                        return
                    from . import aiassist as _a

                    segs = handoff.read_segments(seg_file)
                    cands = _a.auto_name_candidates([x.text for x in segs if x.text])
                    utils.http_json(self, {"ok": True, "candidates": cands})
                elif self.path.startswith("/glossary"):
                    # 读取术语表(原文<Tab>译文 文本)
                    import urllib.parse

                    q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    fp, err = _glossary_path(
                        home.root, q.get("scope", ["global"])[0], q.get("name", [""])[0]
                    )
                    if err:
                        utils.http_json(self, {"ok": False, "err": err})
                        return
                    text = fp.read_text(encoding="utf-8") if fp.is_file() else ""
                    utils.http_json(
                        self,
                        {
                            "ok": True,
                            "path": str(fp),
                            "text": text,
                            "count": _glossary_count(text),
                        },
                    )
                elif self.path == "/jobs":
                    for j in home.jobs:
                        if (
                            j["proc"] is not None
                            and j["st"] == "run"
                            and j["proc"].poll() is not None
                        ):
                            j["st"] = "done" if j["proc"].returncode == 0 else "fail"
                            _log(
                                f"子进程结束:{j.get('label') or j.get('desc')}"
                                f" -> {j['st']}(退出码 {j['proc'].returncode})"
                            )
                    utils.http_json(self, 
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
                _log(f"操作 {self.path}:{body if body else ''}")
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
                        utils.http_json(self, {"ok": True})
                    elif self.path == "/del":
                        name = str(body.get("name", "")).strip()
                        work = _work_of_name(home.root, name)
                        if not name or not work.is_dir():
                            utils.http_json(self, {"ok": False, "err": "工作目录不存在"})
                            return
                        import shutil

                        shutil.rmtree(work)
                        # 连带清理:根目录散落的同名视频文件,什么都不留
                        for ext in VID_EXTS:
                            v = home.root / (name + ext)
                            if v.is_file():
                                v.unlink()
                        utils.http_json(self, {"ok": True})
                    elif self.path == "/delvid":
                        # 删除选定工作目录里的视频文件,保留字幕等其余文件
                        name = str(body.get("name", "")).strip()
                        work = _work_of_name(home.root, name)
                        n = 0
                        for ext in VID_EXTS:
                            v = work / (name + ext)
                            if v.is_file():
                                v.unlink()
                                n += 1
                        if n == 0:
                            utils.http_json(self, {"ok": False, "err": "没找到视频文件"})
                            return
                        utils.http_json(self, {"ok": True, "n": n})
                    elif self.path == "/kill":
                        idx = int(body.get("idx", -1))
                        if not 0 <= idx < len(home.jobs):
                            utils.http_json(self, {"ok": False, "err": "请先在 ④ 选中一个任务"})
                            return
                        j = home.jobs[idx]
                        if j["proc"] is None:  # 内联任务:排队/运行中打取消标记
                            j["cancel"] = True
                            utils.http_json(self, {"ok": True, "note": "将在下一个 OCR 阶段终止"})
                            return
                        if j["proc"].poll() is not None:
                            utils.http_json(self, {"ok": False, "err": "该任务已结束"})
                            return
                        _kill_tree(j["proc"])  # 连 yt-dlp 等子孙进程一起终止
                        utils.http_json(self, {"ok": True})
                    elif self.path == "/crop-save":
                        # 保存/清除选中条目的字幕区参数(工作目录 crop.json)
                        name = str(body.get("name", "")).strip()
                        work = _work_of_name(home.root, name)
                        if not name or not work.is_dir():
                            utils.http_json(self, {"ok": False, "err": "工作目录不存在,请先抽取"})
                            return
                        crop = body.get("crop")
                        cf = handoff.crop_path(work)
                        if crop:
                            cf.parent.mkdir(parents=True, exist_ok=True)
                            cf.write_text(
                                json.dumps({"crop": str(crop)}, ensure_ascii=False),
                                encoding="utf-8",
                            )
                        elif cf.exists():
                            cf.unlink()
                        utils.http_json(self, {"ok": True})
                    elif self.path == "/upload-prefix":
                        # 保存标题前缀(settings.UPLOAD_PREFIX 运行时配置)
                        from .upload import save_prefix

                        save_prefix(str(body.get("prefix", "")))
                        utils.http_json(self, {"ok": True})
                    elif self.path == "/upload":
                        # 投稿:表单确认后的标题/简介/标签转成 upload 子命令后台执行
                        name = str(body.get("name", "")).strip()
                        work = _work_of_name(home.root, name)
                        if not name or not work.is_dir():
                            utils.http_json(self, {"ok": False, "err": "工作目录不存在"})
                            return
                        args = ["upload", str(work)]
                        if body.get("title"):
                            args += ["--title", str(body["title"])]
                        if "desc" in body:
                            args += ["--desc", str(body.get("desc") or "")]
                        if "tags" in body:
                            args += ["--tags", str(body.get("tags") or "")]
                        if body.get("tid"):
                            args += ["--tid", str(int(body["tid"]))]
                        if body.get("delay"):
                            args += ["--delay", str(int(body["delay"]))]
                        home.spawn(f"{name} 投稿", [*exe, *args], label=f"{name} 投稿")
                        utils.http_json(self, {"ok": True})
                    elif self.path.startswith("/page-close"):
                        # 页面关闭信号:忽略,关闭页面不影响程序
                        utils.http_json(self, {"ok": True})
                    elif self.path == "/quit":
                        utils.http_json(self, {"ok": True})
                        if n == 0:
                            # 页面卸载 beacon:忽略,不因页面关闭而退出
                            _log("收到页面关闭信号,忽略(关闭页面不影响程序)")
                            return
                        # 网页「退出程序」按钮:延迟退出避免连接中断报错;先终止所有
                        # 存活子进程(编辑/打码是独立子进程,不杀会残留),os._exit 连内联任务一起终止
                        home.quitting = True

                        def _quit_all():
                            for j in home.jobs:
                                if j["proc"] is not None and j["proc"].poll() is None:
                                    _kill_tree(j["proc"])
                            home.srv.shutdown()
                            home.srv.server_close()
                            os._exit(0)

                        threading.Timer(0.3, _quit_all).start()
                    elif self.path == "/script":
                        text = str(body.get("text", ""))
                        if not text.strip():
                            utils.http_json(self, {"ok": False, "err": "脚本为空"})
                            return
                        path = home.root / ".home-batch.txt"
                        path.write_text(text, encoding="utf-8")
                        home.spawn(
                            "批量脚本",
                            [*exe, "-s", str(path)],
                            label="批量脚本",
                            show=True,
                        )
                        utils.http_json(self, {"ok": True})
                    elif self.path == "/subtool":
                        # 字幕文件工具:导入/合并/排版/转换(不吃选中条目的工作目录,
                        # 除 import 需要 --work)
                        from pathlib import Path as _P

                        op = str(body.get("op", "")).strip()
                        f = _P(str(body.get("file", "")).strip())
                        if not op or not str(f):
                            utils.http_json(self, {"ok": False, "err": "缺少 op 或文件路径"})
                            return
                        if not f.is_file():
                            utils.http_json(self, {"ok": False, "err": f"文件不存在:{f}"})
                            return
                        if op == "import":
                            work = _work_of_name(home.root, str(body.get("name", "")).strip())
                            if not work.is_dir():
                                utils.http_json(
                                    self, {"ok": False, "err": "请先在 ② 选中一个工作目录条目"}
                                )
                                return
                            argv = ["import", str(f), "--work", str(work), *extra()]
                        elif op in ("submerge", "layout", "convert"):
                            argv = [op, str(f), *extra()]
                        else:
                            utils.http_json(self, {"ok": False, "err": f"未知字幕操作:{op}"})
                            return
                        home.spawn(
                            f"{f.name} {_OP.get(op, op)}",
                            [*exe, *argv],
                            label=f"{f.name} {_OP.get(op, op)}",
                            show=True,
                        )
                        utils.http_json(self, {"ok": True})
                    elif self.path == "/presets":
                        # API 配置预设:action=save/use/del
                        from . import aiassist as _a

                        act = str(body.get("action", "")).strip()
                        name = str(body.get("name", "")).strip()
                        if act == "save":
                            if not name:
                                utils.http_json(self, {"ok": False, "err": "请填写预设名"})
                                return
                            old = _a.load_presets()["presets"].get(name) or {}
                            cfg = {
                                "api_base": str(body.get("api_base", "")).strip()
                                or old.get("api_base", ""),
                                "api_key": str(body.get("api_key", "")).strip()
                                or old.get("api_key", ""),
                                "model": str(body.get("model", "")).strip()
                                or old.get("model", ""),
                            }
                            if not (cfg["api_base"] and cfg["model"]):
                                utils.http_json(
                                    self, {"ok": False, "err": "api_base 与 model 不能为空"}
                                )
                                return
                            _a.set_preset(name, cfg)
                            utils.http_json(self, {"ok": True, "current": name})
                        elif act == "use":
                            if not _a.get_preset(name):
                                utils.http_json(self, {"ok": False, "err": f"不存在预设:{name}"})
                                return
                            d = _a.load_presets()
                            d["current"] = name
                            _a.save_presets(d)
                            utils.http_json(self, {"ok": True, "current": name})
                        elif act == "del":
                            if not _a.delete_preset(name):
                                utils.http_json(self, {"ok": False, "err": f"不存在预设:{name}"})
                                return
                            utils.http_json(self, {"ok": True})
                        else:
                            utils.http_json(self, {"ok": False, "err": f"未知操作:{act}"})
                    elif self.path == "/glossary":
                        # 保存术语表:整段文本原子写盘
                        fp, err = _glossary_path(
                            home.root,
                            str(body.get("scope", "global")).strip(),
                            str(body.get("name", "")).strip(),
                        )
                        if err:
                            utils.http_json(self, {"ok": False, "err": err})
                            return
                        text = str(body.get("text", ""))
                        try:
                            fp.parent.mkdir(parents=True, exist_ok=True)
                            tmp = fp.with_name(fp.name + ".tmp")
                            tmp.write_text(text, encoding="utf-8")
                            tmp.replace(fp)
                        except OSError as e:
                            utils.http_json(self, {"ok": False, "err": str(e)})
                            return
                        utils.http_json(self, {"ok": True, "count": _glossary_count(text)})
                    elif self.path == "/runcmd":
                        # 一次性自定义命令:整条命令 = jpsub 子命令 + 参数
                        import shlex as _shlex

                        text = str(body.get("text", "")).strip()
                        try:
                            toks = _shlex.split(text)
                        except ValueError as e:
                            utils.http_json(self, {"ok": False, "err": f"命令解析失败:{e}"})
                            return
                        if not toks:
                            utils.http_json(self, {"ok": False, "err": "命令为空"})
                            return
                        home.spawn(
                            " ".join(toks[:2]),
                            [*exe, *toks],
                            label=" ".join(toks[:2]),
                            show=True,  # 输出保留到任务输出区
                        )
                        utils.http_json(self, {"ok": True})
                    elif self.path == "/cmd":
                        # 通用子命令入口:{sub, flags[], url, path, name}
                        sub = str(body.get("sub", "")).strip()
                        flags = [str(f) for f in body.get("flags", [])]
                        if body.get("debug"):  # 主页 Debug 勾选:本次任务追加 --debug
                            flags = [*flags, "--debug"]
                        url = str(body.get("url", "")).strip()
                        name = str(body.get("name", "")).strip()
                        video = _named_video(home.root, name) or home.root / name
                        work = _work_of_name(home.root, name)

                        def need(cond: bool, err: str) -> bool:
                            if not cond:
                                utils.http_json(self, {"ok": False, "err": err})
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
                                    p = _named_video(home.root, name)
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
                        elif sub in ("render", "translate", "edit", "proofread"):
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
                            if sub == "burn" and handoff.masks_path(work).is_file():
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
                            utils.http_json(self, {"ok": False, "err": f"未知子命令:{sub}"})
                            return
                        utils.http_json(self, {"ok": True})
                    else:
                        self.send_error(404)
                except Exception as e:  # noqa: BLE001
                    utils.http_json(self, {"ok": False, "err": str(e)})

        self.srv = utils.pick_server(H, range(8790, 8800))


def home_page(root: Path | None = None) -> None:
    """启动浏览器主页。root = 产物根目录(默认 output/)。"""
    root = root or cli._output_root()
    root.mkdir(parents=True, exist_ok=True)
    h = _Home(root)
    url = utils.open_browser(h.srv)
    print(f"jpsub 主页:{url}(浏览器未自动打开时手动访问;Ctrl+C 退出)")
    try:
        h.srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        h._log("主页退出")
        for j in h.jobs:  # home 退出时把还挂着的 edit/mask 等启动型子进程一并带走
            proc = j.get("proc")
            if proc and proc.poll() is None:
                try:
                    _kill_tree(proc)
                except Exception:  # noqa: BLE001
                    pass
