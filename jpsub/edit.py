"""译文调整系统:浏览器编辑 segments.json(改译文/删字幕/新增字幕),保存后可直接生成 ASS。

用法:`jpsub edit <工作目录或视频>`,模仿打码选取器的交互:
- 左侧为可播放视频(/video 走 Range 流式),下方联动精细时间轴(色块=每条字幕起止,可横向拖动扫动/点块跳转),空格播放/暂停;
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

from . import handoff, utils
from .mask import frame_jpeg, video_duration
from . import settings

_PAGE = """<!doctype html><html lang=zh><meta charset=utf-8>
<title>译文调整</title><style>
body{font:14px sans-serif;margin:12px;background:#1e1e1e;color:#ddd}
#main{display:flex;gap:16px;align-items:flex-start}
#left{flex:0 0 auto;width:min(46vw,80vh)}
canvas{display:block;background:#000;width:min(46vw,80vh);height:auto}
video{display:block;background:#000;width:min(46vw,80vh)}
#vbox{position:relative;width:min(46vw,80vh)}
#ov{position:absolute;left:4%;right:4%;bottom:4%;text-align:center;color:#fff;font-size:clamp(14px,2.2vw,24px);font-weight:600;line-height:1.35;text-shadow:0 0 3px #000,2px 2px 2px #000,-1px -1px 1px #000;pointer-events:none;white-space:pre-wrap}
#tlwrap{width:min(46vw,80vh);overflow-x:auto;overflow-y:hidden;margin-top:6px}
#tl{position:relative;height:28px;background:#222;border:1px solid #444;cursor:ew-resize;touch-action:none;user-select:none;width:100%}
#tlbar{display:flex;gap:6px;align-items:center}
#tlbar input[type=range]{width:90px}
#tl .blk{position:absolute;top:2px;bottom:2px;background:#567;border-radius:3px;min-width:2px;overflow:hidden;color:transparent}
#tl .blk:hover{background:#78a}
#tl .blk.act{background:#8a8}
#tl .blk.unt{background:#a62}
#ph{position:absolute;top:0;bottom:0;width:2px;background:#fc6;pointer-events:none}
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
#vhead{position:sticky;top:0;display:grid;grid-template-columns:200px 1fr 1fr;gap:6px;background:#1e1e1e;z-index:1;border-bottom:1px solid #444;padding:3px 6px}
#vbody{position:relative;overflow-anchor:none}
.vrow{position:absolute;left:0;right:0;display:grid;grid-template-columns:200px 1fr 1fr;gap:6px;padding:3px 6px;border-bottom:1px solid #333;align-items:start}
.vrow.act{color:#8f8;background:rgba(80,140,80,.12)}
.vrow.unt .tr{color:#f80}
#msg{position:fixed;top:4px;left:50%;transform:translateX(-50%);background:rgba(30,30,30,.94);border:1px solid #555;border-radius:6px;padding:4px 14px;z-index:999;color:#fc6;max-width:70vw}
#quit{position:fixed;top:4px;right:10px;z-index:999}
#right{flex:1;min-width:0;max-height:calc(100vh - 90px);overflow-y:auto}
.trow{display:flex;gap:3px;align-items:center;flex-wrap:wrap}
input.tin{width:60px}
</style>
<h3>译文调整 - @@TITLE@@</h3>
<button id=quit title=关闭编辑器>退出</button>
<div id=main>
<div id=left>
<div id=vbox><video id=vid src="/video" controls style="width:100%" @@NOVID@@></video><div id=ov></div></div>
<div id=tlbar><div id=tlwrap><div id=tl title="时间轴:点击色块跳到该句,拖动横向扫动定位"><div id=ph></div></div></div></div>
<div style=margin-top:6px><button id=add>＋在当前时间新增字幕</button>
<span class=grp style=margin-left:8px><button id=shiftl title=所有字幕整体前移输入的秒数(时间不能为负)>整体前移</button>
<input id=shifts value=1 style=width:60px title=秒数,支持小数>秒
<button id=shiftr title=所有字幕整体后移输入的秒数>整体后移</button></span>
<span class=grp style=margin-left:8px><input id=finds placeholder=查找 style=width:110px>
<input id=reps placeholder=替换为 style=width:110px>
<label style=cursor:pointer title=同时替换原文列><input type=checkbox id=fincl>含原文</label>
<button id=fr title=把所有译文(及勾选时的原文)中的查找内容替换为替换内容>全部替换</button></span></div>
<div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:6px;align-items:flex-start">
<span class=grp>
<button id=continue-tr title="翻译标为[[未译]]的句子,已译的跳过">续翻</button>
<button id=force-tr title="重新翻译全部句子(含已译)">重翻</button>
<label style=cursor:pointer><input type=checkbox id=longtr>长文</label>
</span>
<span class=grp><button id=save>保存</button><input id=bkname placeholder=备份名(可空) style="width:130px" title="留空用当前时间作为备份名"> <label title="生成字幕的格式">格式:<input type=radio name=fmt value=bcc checked>bcc</label><label><input type=radio name=fmt value=ass>ass</label> <button id=render>生成字幕</button></span>
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
<input id=bksearch placeholder=搜索备份 style="width:100%;margin:2px 0">
<select id=bksel size=8 style="width:100%" title="历史备份:每次保存前自动生成;还原时选中其一即还原到该备份,不选则还原到 OCR 原始"></select>
</div>
<div id=msg></div>
<span style=color:#888>译文留空 = 删除该字幕;保存时未列出/已删的行不写回</span>
</div>
<div id=right>
<div id=vhead><span>时间</span><span>原文</span><span>译文</span></div>
<div id=vbody><div id=vrows></div></div>
</div>
</div>
<p>点时间旁的 ▶ 跳到该句开头;时间栏「←起」「止→」把起止设为当前预览时间,键盘 ←/→ 或 A/D ±1秒(Shift ±0.1秒,Alt ±5秒),W/S 或 PageUp/PageDown ±10秒;Q 或空格播放/暂停,C 新增字幕,Z/X 设当前字幕的开始/结束;「✕」删除该行字幕;「↺」把该句标为未译并清掉缓存旧译文。
「续翻」只翻译未译句子,「重翻」全部重翻,勾「长文」则按「。」拆成单句逐句翻(单句翻坏不连累整条),且每句展开为独立字幕条目(时间轴按句长比例估算,可编辑,保存后永久拆分,单句译文写入缓存);预览画面上实时叠加显示当前时间段字幕;翻完可点「生成字幕」;「转换」把所选 .bcc/.ass 互转,「烧录」把字幕烧进视频;「还原改动」从备份列表选中一份恢复。</p>
<script>
const DUR=@@DUR@@,HASVID=@@HASVID@@;
if(HASVID)document.getElementById('vid').playbackRate=@@RATE@@;
const msg=document.getElementById('msg');
let rows=@@ROWS@@, t=0, lastIdx=-2;
const $=id=>document.getElementById(id);
const vid=$('vid'), tl=$('tl'), ph=$('ph');
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
let longm=false;
// 长文展开:多句条目按句长比例估算时间轴,拆成独立字幕行;收起时合并回来
function expandLong(){
  const out=[];
  rows.forEach((r,pi)=>{
    if(r.sents&&r.sents.length>1){
      const tot=r.sents.reduce((a,s)=>a+s.s.length,0)||1,dur=r.end-r.start;
      let acc=r.start;
      r.sents.forEach(s=>{
        const d=dur*(s.s.length/tot);
        out.push({start:+acc.toFixed(2),end:+(acc+d).toFixed(2),src:s.s,
          text:s.t&&s.t.trim()&&s.t.trim()!=='[[未译]]'?s.t:'[[未译]]',long:true,_pid:pi});
        acc+=d;
      });
    }else out.push(r);
  });
  return out;
}
function collapseLong(pre){
  const out=[];
  rows.forEach(r=>{
    if(!r.long){out.push(r);return}
    const p=out[out.length-1];
    if(p&&p.long&&p._pid===r._pid){p.src+=r.src;p.text+=(r.text.trim()==='[[未译]]'?'':r.text);p.end=r.end}
    else out.push({start:r.start,end:r.end,src:r.src,
      text:r.text.trim()==='[[未译]]'?'[[未译]]':r.text,long:true,sents:[],bad:r.bad,_pid:r._pid});
  });
  if(pre)out.forEach(r=>{if(r.long&&r._pid!=null&&pre[r._pid])r.sents=pre[r._pid].sents||[]});
  return out;
}
let preLong=null;  // 展开前的行快照,收起时还原 sents,保证反复勾选不坏
// 上次翻译是长文模式时自动勾选展开;无 sents 时 expandLong 原样返回
if(@@LONG@@){$('longtr').checked=true;longm=true;preLong=rows;rows=expandLong(rows)}
$('longtr').onchange=()=>{
  longm=$('longtr').checked;
  if(longm){preLong=rows;rows=expandLong(rows)}
  else{rows=collapseLong(preLong);preHard=null}
  lastIdx=-2;syncList();updateOv()};
function updateOv(){
  if(!HASVID)return;
  const i=cur();let s='';
  if(i>=0){const tx=rows[i].text.trim();if(tx&&tx!=='[[未译]]')s=rows[i].text}
  $('ov').textContent=s;
}
const fmt=s=>`${Math.floor(s/60)}:${(s%60).toFixed(1).padStart(4,'0')}`;
const parseT=s=>{s=String(s).trim();if(/^\\d+(\\.\\d+)?$/.test(s))return+s;
  const p=s.split(':');if(p.length>3||p.some(v=>v===''))return NaN;
  return p.reduce((a,v)=>a*60+(+v||0),0)};
function loadFrame(){
  if(!HASVID)return;
  vid.currentTime=Math.min(t,Math.max(0,DUR-0.05));
}
vid.addEventListener('timeupdate',()=>{
  t=vid.currentTime;
  ph.style.left=(t/DUR*100)+'%';
  const ai=cur();
  if(ai!==lastIdx){lastIdx=ai;updateAct();refreshTl();scrollList();}
  followTl();
  updateOv();
});
function cur(){
  // 连续字幕边界重合(上一条 end == 下一条 start)时取 start 更晚的那条,
  // 否则跳到某条开头会高亮到上一条
  let idx=-1;
  rows.forEach((r,i)=>{if(r.start<=t&&t<=r.end)idx=i});
  return idx;
}
// ===== 虚拟滚动列表:只渲染可视区附近的行,几千条字幕也只维护几十个 DOM 节点 =====
// 行绝对定位(按 top 摆放),容器总高固定,滚动中替换行不改变布局高度,避免滚动锚定乱跳
const vrowsEl=$('vrows'),vbody=$('vbody'),rightBox=$('right');
const EST=64;               // 未测量行的估高
let rowH=[],off=null;       // 每行实测高度 / 前缀和偏移
let lastSt=0,lastEn=0,rafPend=0;
function resetHeights(){rowH=new Array(rows.length).fill(0);off=null}
function buildOff(){off=[0];for(let i=0;i<rows.length;i++)off[i+1]=off[i]+(rowH[i]||EST)}
function findIdx(y){let lo=0,hi=rows.length;while(lo<hi){const m=(lo+hi)>>1;if(off[m]<=y)lo=m+1;else hi=m}return Math.max(0,lo-1)}
function setVSize(){vbody.style.height=(off[rows.length]||0)+'px'}
const rowHtml=(r,i)=>{
  const unt=r.text.trim()==='[[未译]]'||r.bad;
  return `<div class="vrow${i===lastIdx?' act':''}${unt?' unt':''}" data-i=${i} style="top:${off[i]}px">`+
    `<div class=trow>`+
    `<button class=jump title=跳到该句开头>▶</button>`+
    `<input class=tin value='${fmt(r.start)}' data-k=start>`+
    `<button class=st data-k=start title=设为当前时间>←起</button>`+
    `<span>~</span>`+
    `<input class=tin value='${fmt(r.end)}' data-k=end>`+
    `<button class=st data-k=end title=设为当前时间>止→</button>`+
    `<button class=del title=删除该字幕>✕</button>`+
    `<button class=unt title=标为未译,配合主页「续翻」重翻该句>↺</button>`+
    `</div>`+
    `<textarea class=src>${esc(r.src)}</textarea>`+
    `<textarea class=tr>${esc(r.text)}</textarea></div>`};
function renderWindow(){
  rafPend=0;
  if(!off)buildOff();
  setVSize();
  if(!rows.length){vrowsEl.innerHTML='';return}
  const sc=rightBox.scrollTop,vh=rightBox.clientHeight||600,BUF=8;
  let st=Math.max(0,findIdx(Math.max(0,sc-200))-BUF);
  let en=st;const lim=sc+vh+BUF*EST;
  while(en<rows.length&&off[en]<lim)en++;
  en=Math.min(rows.length,en+BUF);
  lastSt=st;lastEn=en;
  vrowsEl.innerHTML=rows.slice(st,en).map((r,k)=>rowHtml(r,st+k)).join('');
  // 渲染后批量测量行高,写回缓存(仅可视区 ~30 行,无重排风暴)
  let changed=false;
  [...vrowsEl.children].forEach((el,k)=>{
    const i=st+k;
    el.querySelectorAll('textarea').forEach(ta=>{ta.style.height='auto';ta.style.height=ta.scrollHeight+'px'});
    const h=el.getBoundingClientRect().height;
    if(Math.abs((rowH[i]||0)-h)>1){rowH[i]=Math.round(h);changed=true}
  });
  if(changed){
    buildOff();setVSize();
    // 高度缓存更新后,把已渲染行的 top 对齐到新偏移
    [...vrowsEl.children].forEach((el,k)=>{el.style.top=off[st+k]+'px'});
    // 总高变化时防止 scrollTop 越界被钳到极端
    const maxSc=Math.max(0,off[rows.length]-rightBox.clientHeight);
    if(rightBox.scrollTop>maxSc)rightBox.scrollTop=maxSc;
  }
}
function refreshTl(){
  if(!HASVID)return;
  const ai=lastIdx;
  tl.querySelectorAll('.blk').forEach(b=>b.remove());
  rows.forEach((r,i)=>{
    const b=document.createElement('div');b.className='blk';b.dataset.i=i;
    if(i===ai)b.className+=' act';
    if(r.text.trim()==='[[未译]]'||r.bad)b.className+=' unt';
    b.style.left=(r.start/DUR*100)+'%';
    b.style.width=Math.max(0.15,(r.end-r.start)/DUR*100)+'%';
    b.title=fmt(r.start)+'~'+fmt(r.end)+' '+(r.text||r.src||'');
    tl.appendChild(b);
  });
  ph.style.left=(t/DUR*100)+'%';
}
function syncList(){ // 结构性刷新(增删/展开收起/整体操作):锚定首行重建
  const first=off?findIdx(rightBox.scrollTop):0;
  refreshTl();resetHeights();buildOff();
  rightBox.scrollTop=off[first]||0;
  renderWindow();
}
function renderedRow(i){return [...vrowsEl.children].find(e=>+e.dataset.i===i)}
function updateRow(i){ // 单行就地刷新(改时间/标未译),不动整个列表
  const r=rows[i],el=renderedRow(i);
  if(el){
    const tins=el.querySelectorAll('input.tin');
    tins[0].value=fmt(r.start);tins[1].value=fmt(r.end);
    el.classList.toggle('unt',r.text.trim()==='[[未译]]'||r.bad);
    el.querySelectorAll('textarea').forEach(ta=>{ta.style.height='auto';ta.style.height=ta.scrollHeight+'px'});
    const h=el.getBoundingClientRect().height;
    if(Math.abs((rowH[i]||0)-h)>1){rowH[i]=Math.round(h);buildOff();setVSize()}
  }
  refreshTl();
}
function updateAct(){
  const old=vrowsEl.querySelector('.vrow.act');if(old)old.classList.remove('act');
  const el=renderedRow(lastIdx);if(el)el.classList.add('act');
}
function scrollToRow(i){
  if(!off)buildOff();
  rightBox.scrollTop=(off[i]||0)-40;
  renderWindow();
}
rightBox.addEventListener('scroll',()=>{if(!rafPend)rafPend=requestAnimationFrame(renderWindow)});
// 事件委托:所有行内按钮/输入只挂一份监听,索引从行元素读取,删除后天然正确
vrowsEl.addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;
  const i=+b.closest('.vrow').dataset.i;
  if(b.classList.contains('jump'))setT(rows[i].start);
  else if(b.classList.contains('st')){rows[i][b.dataset.k]=+t.toFixed(2);updateRow(i);markDirty()}
  else if(b.classList.contains('del')){rows.splice(i,1);rowH.splice(i,1);syncList();markDirty()}
  else if(b.classList.contains('unt'))(async()=>{
    const j=await post2('/unt',{i:i,rows:rows});
    if(j.ok){rows[i].text='[[未译]]';updateRow(i);markDirty();msg.textContent='已标为未译,去主页点「续翻」重翻该句'}
    else msg.textContent='失败:'+j.err;
  })();
});
vrowsEl.addEventListener('change',e=>{
  const el=e.target,i=+el.closest('.vrow').dataset.i;
  if(el.classList.contains('tin')){
    const v=parseT(el.value);if(!isNaN(v)){rows[i][el.dataset.k]=v;updateRow(i)}
    markDirty()}
  else if(el.classList.contains('src')){rows[i].src=el.value;updateRow(i);markDirty()}
  else if(el.classList.contains('tr')){rows[i].text=el.value;updateRow(i);markDirty()}
});
vrowsEl.addEventListener('input',e=>{
  if(!e.target.classList.contains('src')&&!e.target.classList.contains('tr'))return;
  const el=e.target;el.style.height='auto';el.style.height=el.scrollHeight+'px';
});
$('add').onclick=()=>{
  const row={start:+t.toFixed(2),end:+(t+2).toFixed(2),src:'',text:''};
  let idx=rows.findIndex(r=>r.start>t);
  if(idx<0)idx=rows.length;
  rows.splice(idx,0,row);
  syncList();scrollToRow(idx);markDirty();msg.textContent='已新增,记得填写时间与译文'};
// 整体平移:所有字幕起止同加/同减指定秒数,前移到负数的钳为 0
function shiftAll(d){
  const v=parseFloat($('shifts').value);
  if(isNaN(v)||v<=0)return msg.textContent='请输入大于 0 的秒数';
  const dd=d*v;
  rows.forEach(r=>{r.start=Math.max(0,+(r.start+dd).toFixed(2));r.end=Math.max(0,+(r.end+dd).toFixed(2))});
  syncList();markDirty();msg.textContent=`已整体${d>0?'后':'前'}移 ${v} 秒,记得保存`;
}
$('shiftl').onclick=()=>shiftAll(-1);
$('shiftr').onclick=()=>shiftAll(1);
// 全局查找替换:纯文本替换,默认只替换译文列,勾「含原文」时同时替换原文列;替换后标脏走保存链路
$('fr').onclick=()=>{
  const q=$('finds').value;
  if(!q)return msg.textContent='请输入要查找的内容';
  const rep=$('reps').value, inc=$('fincl').checked;
  let n=0;
  rows.forEach(r=>{
    if(r.text.includes(q)){r.text=r.text.split(q).join(rep);n++}
    if(inc&&r.src.includes(q)){r.src=r.src.split(q).join(rep);n++}
  });
  syncList();markDirty();
  msg.textContent=n?`已替换 ${n} 处,记得保存`:'没有找到匹配内容';
};
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
async function save(auto){
  const r=await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:auto?'':$('bkname').value.trim(),rows,auto})});
  const j=await r.json();
  if(!auto){msg.textContent=j.ok?`已保存 ${j.n} 条`:'保存失败:'+j.err;
    if(j.ok){$('bkname').value='';loadBks();}}
  return j.ok;
}
$('save').onclick=()=>save(false);
@@AUTOSAVE@@
mkAutosave(a=>save(a));
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
  const j=await post2('/translate',{long:$('longtr').checked,force:force});
  if(!j.ok)return msg.textContent='失败:'+j.err;
  msg.textContent='翻译启动中...';
  pollTr();
}
$('continue-tr').onclick=()=>startTr(false);
$('force-tr').onclick=()=>startTr(true);
$('render').onclick=async()=>{
  if(!await save())return;
  msg.textContent='生成字幕中...';
  const fmt=document.querySelector('input[name=fmt]:checked').value;
  const j=await post2('/render',{fmt});
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
  let idx=cur();
  if(idx<0)idx=rows.findIndex(r=>r.start>=t);
  if(idx<0)return;
  if(!off)buildOff();
  rightBox.scrollTop=(off[idx]||0)-40; // 滚动事件触发 renderWindow
  updateAct();
}
function setT(v){t=Math.min(DUR,Math.max(0,v));if(HASVID&&!vid.paused)vid.pause();lastIdx=cur();loadFrame();refreshTl();scrollList();followTl();}
// 时间轴:固定 30 倍宽,可横向滚动,定位/播放时自动滚动让播放头保持可见
const tlwrap=$('tlwrap');
const tlZoom=30;
tl.style.width=(tlZoom*100)+'%';
function followTl(){  // 播放头保持在可视区中间
  if(!tlwrap)return;
  const x=(t/DUR)*tl.clientWidth;
  tlwrap.scrollLeft=Math.max(0,x-tlwrap.clientWidth/2);
}
// 时间轴拖动:横向拖动扫动定位;点色块跳到该句开头
let tldrag=false;
function tlScrub(e){const r=tl.getBoundingClientRect();setT((e.clientX-r.left)/r.width*DUR)}
tl.addEventListener('pointerdown',e=>{
  const b=e.target.closest('.blk');
  if(b){setT(rows[+b.dataset.i].start);return}
  tldrag=true;tl.setPointerCapture(e.pointerId);tlScrub(e);
});
tl.addEventListener('pointermove',e=>{if(tldrag)tlScrub(e)});
tl.addEventListener('pointerup',()=>tldrag=false);
document.onkeydown=e=>{
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA')return;
  if(e.key===' '||e.key==='q'||e.key==='Q'){vid.paused?vid.play():vid.pause();e.preventDefault();return}
  if(e.key==='c'||e.key==='C'){$('add').onclick();e.preventDefault();return}
  if(e.key==='z'||e.key==='Z'||e.key==='x'||e.key==='X'){
    let i=cur();
    if(i<0)i=rows.findIndex(r=>r.start>=t);
    if(i<0)i=rows.length-1;
    if(i<0)return;
    rows[i][e.key.toLowerCase()==='z'?'start':'end']=+t.toFixed(2);
    updateRow(i);markDirty();msg.textContent=`已设为第 ${i+1} 条字幕的${e.key.toLowerCase()==='z'?'开始':'结束'}时间`;
    e.preventDefault();return}
  // WASD 与方向键等价:a/d ±1s,w/s ±10s(Shift/Alt 前缀同样生效)
  const step=e.shiftKey?0.1:(e.altKey?5:1);
  const big=e.shiftKey?1:(e.altKey?30:10);
  const left=e.key==='ArrowLeft'||e.key==='a'||e.key==='A';
  const right=e.key==='ArrowRight'||e.key==='d'||e.key==='D';
  if(left){setT(t-step);e.preventDefault()}
  else if(right){setT(t+step);e.preventDefault()}
  else if(e.key==='w'||e.key==='W'){setT(t-big);e.preventDefault()}
  else if(e.key==='s'||e.key==='S'){setT(t+big);e.preventDefault()}
};
syncList();loadFrame();updateOv();
if(!HASVID){$('tlbar').style.display='none';}
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
$('quit').onclick=()=>{if(confirm('退出译文编辑器?'))fetch('/quit',{method:'POST'})};
addEventListener('pagehide',()=>navigator.sendBeacon('/quit'));
</script>"""


def _read_rows(work: Path) -> list[dict]:
    """读 segments.json 为行列表,按时间排序;未译段显示占位标记。
    另附 sents(长文切句+缓存单句译文),供页面勾「长文」时展开显示。"""
    segs = handoff.read_segments(handoff.seg_path(work))
    cache = None
    cp = handoff.cache_path(work)
    if cp.exists():
        from .cache import TranslationCache

        cache = TranslationCache(cp)
    rows = []
    for s in segs:
        src = s.text or ""
        parts = handoff.split_sents(src)
        sents = (
            [{"s": p, "t": cache.get(p) or ""} for p in parts]
            if cache is not None and len(parts) > 1
            else []
        )
        rows.append(
            {
                "start": s.start,
                "end": s.end,
                "src": src,
                "text": s.tr or "" if s.tr and not handoff.is_untranslated(s.tr) else handoff.UNTRANSLATED_MARK,
                "bad": bool(s.tr and handoff.is_junk_tr(s.tr)),  # 混入拒绝语:显示原文但标橙,续翻会重翻
                "sents": sents,
            }
        )
    rows.sort(key=lambda r: (r["start"], r["end"]))
    return rows


def _save_rows(
    work: Path, rows: list[dict], name: str = "", *, backup: bool = True
) -> int:
    """行列表写回 segments.json:译文置空/删除行 = 删除该字幕;
    译文为占位标记 = 保持未译。新增行(src 为空)文本即译文。
    backup=False 用于自动保存:只落盘,不建备份。"""
    from . import cli
    from .segment import Segment

    cache = None
    segs: list[Segment] = []
    for r in rows:
        try:
            start, end = float(r["start"]), float(r["end"])
        except (TypeError, ValueError):
            continue
        src = str(r.get("src", "")).strip()
        t = str(r.get("text", "")).strip()  # 保留换行,渲染 ASS 时转 \N
        if t == handoff.UNTRANSLATED_MARK:
            segs.append(Segment(start, end, src, tr=None))  # 保持未译
            continue
        if not t:
            continue  # 空行不写 = 删除
        segs.append(Segment(start, end, src or t, tr=t))
        if r.get("long") and src:  # 长文拆分句:译文进缓存,长文续翻直接复用
            from .cache import TranslationCache

            cache = cache or TranslationCache(handoff.cache_path(work))
            cache.put(src, t)
    segs.sort(key=lambda s: s.start)
    if cache is not None:
        cache.save()
    sp = handoff.seg_path(work)
    if backup:
        cli._backup(work, sp, name=name)  # 覆盖前备份(自动保存不建)
    handoff.write_segments(
        segs,
        sp,
        comment=handoff.read_meta(sp).get("comment"),
        long=handoff.read_meta(sp).get("long"),
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
    """新布局:视频在工作目录内。"""
    from . import utils

    return utils.find_video(work)


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
        from http.server import BaseHTTPRequestHandler

        self.work = work
        self.video = _find_video(work)
        self.duration = video_duration(self.video) if self.video else 0.0
        self.render_msg = ""
        self.tr_busy = False  # 翻译任务进行中标记
        self.tr_log: list[str] = []  # 翻译任务输出(进度)

        self.idle = utils.IdleQuit()
        editor = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                editor.idle.touch()
                if self.path == "/ping":
                    utils.http_json(self, {})
                elif self.path == "/trstatus":
                    utils.http_json(self, {"busy": editor.tr_busy, "log": editor.tr_log[-8:]})
                elif self.path == "/backups":
                    from .cli import _backup_names

                    names = _backup_names(editor.work, "segments.json")
                    utils.http_json(self, {"ok": True, "names": names})
                elif self.path == "/":
                    utils.http_page(self, _render_page(editor))
                elif self.path == "/video":
                    if not editor.video:
                        self.send_error(404)
                        return
                    utils.http_video(self, editor.video)
                elif self.path.startswith("/frame?"):
                    import urllib.parse

                    q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    t = float(q.get("t", ["0"])[0])
                    try:
                        data = frame_jpeg(editor.video, t)
                    except Exception:
                        self.send_error(500)
                        return
                    utils.http_bytes(self, data, "image/jpeg")
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
                editor.idle.touch()
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n)
                if self.path == "/quit":
                    utils.http_json(self, {"ok": True})
                    editor.idle.on_quit()
                elif self.path == "/save":
                    try:
                        data = json.loads(body)
                        rows = data["rows"]
                        cnt = _save_rows(
                            editor.work, rows, name=str(data.get("name", "") or ""),
                            backup=not data.get("auto"),
                        )
                        editor.render_msg = ""
                        utils.http_json(self, {"ok": True, "n": cnt})
                    except Exception as e:  # noqa: BLE001
                        utils.http_json(self, {"ok": False, "err": str(e)})
                elif self.path == "/unt":
                    # 标为未译:删除缓存里的旧译文,之后主页「续翻」按钮只会重翻这句;
                    # 段落内容由客户端标脏后自动保存落盘
                    try:
                        b = json.loads(body)
                        rows = b["rows"]
                        i = int(b["i"])
                        from .cache import TranslationCache

                        cache = TranslationCache(handoff.cache_path(editor.work))
                        cache.remove(str(rows[i].get("src", "")))
                        cache.save()
                        utils.http_json(self, {"ok": True})
                    except Exception as e:  # noqa: BLE001
                        utils.http_json(self, {"ok": False, "err": str(e)})
                elif self.path == "/translate":
                    # 后台线程跑翻译(续翻/重翻),进度经 /trstatus 轮询
                    if editor.tr_busy:
                        utils.http_json(self, {"ok": False, "err": "已有翻译任务在进行"})
                    else:
                        b = json.loads(body) if body else {}
                        argv = ["translate", str(editor.work)]
                        if b.get("long"):
                            argv.append("--long")
                        if b.get("force"):
                            argv.append("--force")
                        threading.Thread(
                            target=_tr_worker, args=(editor, argv), daemon=True
                        ).start()
                        utils.http_json(self, {"ok": True})
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
                        utils.http_json(self, {"ok": True, "out": str(out)})
                    except Exception as e:  # noqa: BLE001
                        utils.http_json(self, {"ok": False, "err": str(e)})
                elif self.path == "/render":
                    try:
                        b = json.loads(body) if body else {}
                        out = editor.run_render(bcc=b.get("fmt", "bcc") == "bcc")
                        utils.http_json(self, {"ok": True, "out": str(out)})
                    except Exception as e:  # noqa: BLE001
                        utils.http_json(self, {"ok": False, "err": str(e)})
                elif self.path == "/restore":
                    try:
                        name = ""
                        try:
                            name = str(json.loads(body).get("name", "") or "").strip()
                        except Exception:  # noqa: BLE001
                            pass
                        if name:
                            from . import cli
                            from .segment import Segment as _Seg

                            src = cli._backup_src(editor.work, name, "segments.json")

                            segs = handoff.read_segments(src)
                            cur = handoff.seg_path(editor.work)
                            cli._backup(editor.work, cur)  # 还原前备份当前状态
                            handoff.write_segments(
                                [
                                    _Seg(s.start, s.end, s.text, s.tr)
                                    for s in segs
                                ],
                                cur,
                                comment=handoff.read_meta(cur).get("comment"),
                                long=handoff.read_meta(handoff.seg_path(editor.work)).get("long"),
                            )
                            n = len(segs)
                        else:
                            n = handoff.restore_from_orig(editor.work)
                        utils.http_json(self, {"ok": True, "n": n})
                    except FileNotFoundError as e:
                        utils.http_json(self, {"ok": False, "err": str(e)})
                    except Exception as e:  # noqa: BLE001
                        utils.http_json(self, {"ok": False, "err": str(e)})
                elif self.path == "/burn":
                    try:
                        f = ""
                        try:
                            f = str(json.loads(body).get("file", "") or "").strip()
                        except Exception:  # noqa: BLE001
                            pass
                        ass_file = self._resolve_file(f) if f else None
                        out = editor.run_burn(ass_file)
                        utils.http_json(self, {"ok": True, "out": str(out)})
                    except Exception as e:  # noqa: BLE001
                        utils.http_json(self, {"ok": False, "err": str(e)})
                else:
                    self.send_error(404)
        self.srv = utils.pick_server(H, range(8775, 8785))
        self.idle.srv = self.srv

    def rows(self) -> list[dict]:
        return _read_rows(self.work)

    def run_render(self, bcc: bool = False) -> Path:
        from . import cli

        argv = ["render", str(self.work)] + (["--bcc"] if bcc else [])
        args = cli.parse_args(argv)
        return cli._render(args)

    def run_burn(self, ass_file: Path | None = None) -> Path:
        from . import cli

        if not self.video:
            raise SystemExit("错误:找不到视频文件,无法烧录")
        if ass_file is None:
            ass = self.run_render()  # 烧录始终用 ass(ffmpeg 需要)
        else:
            # 文件选择器指定的字幕:.bcc 先转 .ass,再直接烧录
            ass = _bcc2ass(ass_file) if ass_file.suffix.lower() == ".bcc" else ass_file
        video = self.video
        masks = handoff.masks_path(self.work)
        if masks.is_file():  # 与 home「烧录」一致:先打码再烧字幕
            from .mask import apply_masks

            out = cli._product_out(video, ".masked.mp4")
            print(f"先应用打码:{masks} -> {out}")
            apply_masks(video, masks, out)
            video = out
        return cli._burn(video, ass)


def _render_page(p: _Editor) -> str:
    return utils.render_page(_PAGE, {
        "@@TITLE@@": p.work.name,
        "@@STEM@@": handoff.item_stem(p.work),
        "@@DUR@@": f"{p.duration:.3f}" if p.video else "1",
        "@@NOVID@@": "" if p.video else "hidden",
        "@@HASVID@@": "true" if p.video else "false",
        "@@BURN@@": ('<button id=burn>烧录进视频</button>' if p.video else ""),
        "@@ROWS@@": json.dumps(
            [{k: r[k] for k in ("start", "end", "src", "text", "sents")} for r in p.rows()],
            ensure_ascii=False,
        ),
        "@@LONG@@": "true" if handoff.read_meta(handoff.seg_path(p.work)).get("long") else "false",
        "@@AUTOSAVE@@": utils.AUTOSAVE_JS,
        "@@RATE@@": str(max(0.1, float(getattr(settings, "EDIT_PLAYBACK_RATE", 1.0) or 1.0))),
    })


def editor(target: Path) -> None:
    """启动浏览器译文调整器。target = 工作目录或视频文件。"""
    target = Path(target)
    if target.is_file():
        from .cli import _work_of

        target = _work_of(target)
    if not target.is_dir():
        raise SystemExit(f"错误:工作目录不存在:{target}")
    if not handoff.seg_path(target).exists():
        raise SystemExit(f"错误:{target} 里没有 segments.json,先运行 extract")
    e = _Editor(target)
    url = utils.open_browser(e.srv)
    print(f"译文调整器:{url}(浏览器未自动打开时手动访问;关闭页面即退出)")
    try:
        e.srv.serve_forever()
    except KeyboardInterrupt:
        pass
