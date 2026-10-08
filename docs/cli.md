# 命令行参考

主页能完成日常全部操作；以下命令行方式供进阶使用，所有参数也都能写在批量脚本行里。在程序所在文件夹的地址栏输入 `cmd` 回车打开命令行后运行。

## 1. 最常用：一条命令下载视频 + 生成中文字幕

```
.venv\Scripts\jpsub.exe https://www.nicovideo.jp/watch/sm42567589
```

直接写 sm 号也可以：`.venv\Scripts\jpsub.exe sm43168834`。翻译完成后会自动打开**译文调整器**（见 [edit.md](edit.md)），确认/修正译文后点 **生成字幕** 输出 `.ass`（不想调整可 Ctrl+C 跳过，之后用 `jpsub edit` 或 `jpsub render` 继续）。完成后同目录会得到视频 `sm42567589.mp4` 和字幕 `sm42567589.ass`，用 PotPlayer / MPC-HC 等播放器打开视频即可看到中文字幕。

## 2. 把字幕烧录进视频

```
.venv\Scripts\jpsub.exe sm43168834 --burn
```

烧录模式仍自动完成渲染，不打开译文调整器。已生成过字幕时会直接烧录，不再重新处理。

## 3. 本地视频生成字幕

```
.venv\Scripts\jpsub.exe run "output\lesson01.mp4" --comment "恐怖剧场"
```

`--comment` 简单描述视频内容，可提高翻译质量。也可要求AI比如不更改专有名词的翻译

## 4. 只想生成日文字幕和时间轴文件（不翻译）

```
.venv\Scripts\jpsub.exe run "output\lesson01.mp4" --notrans
```

运行后在工作目录(如output\sm114514.jpsub)生成 `segments.json`（日文原文和时间轴，另有原始备份 `segments.orig.json`），不会调用 AI 翻译。

之后执行 `render` 会生成**日文字幕**（没有任何译文时自动用日文原文填充）：

```
.venv\Scripts\jpsub.exe render output/sm114514.jpsub
```

## 5. 调整字幕样式

```
.venv\Scripts\jpsub.exe run sm43168834 --font "微软雅黑" --font-size 32 --max-chars 20
```

## 6. 手动调整翻译

推荐先在浏览器里可视化改译文（见 [edit.md](edit.md)，支持一键还原 OCR 原始结果），不用手编文件。

字幕数据统一存在工作目录(如 output\sm114514.jpsub)下的 `segments.json`：每段含 `start`/`end`(秒)、`text`(日文原文)、`tr`(中文译文，缺省/`[[未译]]` 表示未翻)。可直接手工编辑：

```json
{
  "segments": [
    {
      "start": 58.0,
      "end": 107.5,
      "text": "こより、コード:E3を発動する。",
      "tr": "小依理、发动代码:E3。目标是忍界全人类!!"
    },
    { "start": 107.5, "end": 112.0, "text": "世界に苦しみを......!" }
  ],
  "comment": null
}
```

编辑要点：

- **改译文**：直接改对应段的 `tr`；
- **删字幕**：删掉整个该段对象（或把 `tr` 改成无效内容）该句就不显示；
- **加字幕**：加一个对象，`start`/`end` 写秒数，没有原文时 `text` 直接写中文、`tr` 也写同样内容；
- **调时间轴**：改该段的 `start`/`end`（单位秒）。

之后重新生成字幕文件：

```
.venv\Scripts\jpsub.exe render output/sm114514.jpsub
```

> 加 `--bcc` 输出必剪 `.bcc`（JSON）而不是 `.ass`（不做 ASS 式自动折行，译文最多保留一个换行），字段对齐 B 站 CC 导出格式。生成的 `.ass` 文件名会用条目名（去掉尾部视频 id）。

> 如果 `segments.json` 不小心改乱了：点调整器里的 **还原改动** 按钮（或删除它后重跑 `extract`）可从原始备份 `segments.orig.json` 恢复到 OCR 刚识别完的状态。

> **防误操作备份**：OCR 覆盖 segments.json、翻译/调整器写回译文之前,都会自动把旧文件备份到工作目录的 `backup/时间戳/` 文件夹,改坏了可以从那里找回。

> **默认不重复劳动**：已有 segments.json 时跳过 OCR 直接沿用,已译的句子续翻时自动跳过,都读缓存。想推倒重来加 `--force`：`run --force` 重新抽帧 OCR+翻译、`extract --force` 只重跑 OCR、`translate --force` 忽略已有译文和缓存全部重翻（主页对应「重新OCR翻译」「重新OCR」「重翻」按钮,force 操作同样先备份）。

> 也可以直接修正 AI 译文后重 render,改动会自动进缓存。

> **长文模式 `--long`**：`translate` / `text` 加此参数后,每条原文按「。」拆成单句逐句翻译再按原序拼回——单句被审查/翻坏只回退那一句,不会毁掉整条。请求次数明显变多,适合对付经常翻坏长段的素材。

> **翻译任意文本文件**：`jpsub text 文件.txt` 逐行发给 AI 翻译,输出 `文件.zh.txt`（`-o` 可指定输出）。支持 `--comment` 背景引导、`--glossary` 名词对照表、`--long` 拆句模式。

## 7. 原字幕位置

运行时使用`--crop`参数指定原字幕位置，支持两种写法:

- 单数字: 取底部该占比高度, 如 `--crop 0.22`(从底部22%高度),大部分BB剧场的对话框(不加人名)都这么大.
- `上:下:左:右` 四边距: 从各边裁掉对应比例后保留中间区域, 如 `--crop 0.78:0.1:0.1:0.1`.

**`--crop` 选的范围越小越好**：只框住文字区域即可。范围越小，文字之外变化的像素（背景、人物动作）越少，换段判定（哪帧算一句话的结尾）与 OCR 识别准确率都越高。只有字幕不在底部的特殊素材才需要 `--crop 1` 全帧（此时程序会自动把小图放大 2 倍送识别以改善小字精度）。

AI拓也使用 `--crop 1` 获取全部文字,并使用 `--long` 拆句翻译降低长段翻坏的概率

也可以在 `.env` 里配置 `CROP_PRESETS` 快捷预设,之后 `--crop 键` 直接使用对应值:

```python
CROP_PRESETS = {"1": "0.03:0.03:0.02:0.02"}  # --crop 1 -> 0.03:0.03:0.02:0.02
```

再配合 `TAG_CROP` / `TAG_LONG` 可按 info 标签全自动（见 [settings.md](settings.md)），或直接在主页 ② 选条目点「选择原视频字幕区域」框选保存。

## 8. 跳过视频段

使用`--start` `--end`参数来制定仅截取在此之间的视频作为字幕来源.
例如11分45秒后为借物表, 不作为字幕源

```
.venv\Scripts\jpsub.exe run sm43168834 --end 11:45
```

## 9. 使用浏览器 cookies 下载（应对登录/地区限制）

下载时把浏览器的登录状态传给 yt-dlp，用 `--cookies-from-browser` 参数指定浏览器名：

```
.venv\Scripts\jpsub.exe sm43168834 --cookies-from-browser chrome
.venv\Scripts\jpsub.exe sm43168834 --cookies-from-browser edge
.venv\Scripts\jpsub.exe sm43168834 --cookies-from-browser firefox
```

也可以在 `.env` 里写 `COOKIES_FROM_BROWSER = "chrome"` 永久生效（命令行参数优先）。

> 注意：使用前请确保对应浏览器已**完全退出**（Firefox 可不用退出），否则 cookie 数据库可能被占用。

**yt-dlp 找不到浏览器配置（报 `could not find ... cookies database`）时**，完整语法为
`--cookies-from-browser 浏览器名[+KEYRING][:PROFILE][::CONTAINER]`：

- 支持的浏览器：`brave, chrome, chromium, edge, firefox, opera, safari, vivaldi, whale`
- `+KEYRING`：Linux 下 Chromium 系浏览器解密 cookie 用的密钥环，可选 `basictext, gnomekeyring, kwallet, kwallet5, kwallet6`
- `:PROFILE`：指定 profile 的**名称或路径**。找不到数据库通常是因为浏览器装在非默认位置，直接把 profile 文件夹的完整路径写在这里即可，例如：
  ```
  .venv\Scripts\jpsub.exe sm43168834 --cookies-from-browser "chrome:C:\Users\你的用户名\AppData\Local\Google\Chrome\User Data\Default"
  ```
- `::CONTAINER`：仅 Firefox，指定容器名（`none` 表示不使用容器），默认使用最近访问 profile 的所有容器。

## 10. 朗读视频：无字幕只有语音的时间轴

```
.venv\Scripts\jpsub.exe voice "output\radio.mp4"
```

按音量变化（静音检测）自动切出语音段落，产出 `segments.json`（原文留空）。在译文调整器里手填原文，再「生成字幕」或「续翻」交给 AI 翻译。支持 `--start/--end` 跳过片头片尾。朗读音量起伏导致一句话被切碎时，可调 `--hyst`（滞回 dB，默认 10）：语音需跌到（阈值-10dB）以下才算静音开始。

## 11. 翻译任意文本文件

```
.venv\Scripts\jpsub.exe text 文章.txt --comment "博客" --long
```

逐行发给 AI 翻译，输出 `文章.zh.txt`（`-o` 可指定输出）。支持 `--comment` 背景引导、`--glossary` 名词对照表、`--long` 拆句模式（每句单独翻译，单句翻坏不连累整条）。

## 12. 长文拆句翻译（`--long`）

`translate` / `text` 加 `--long` 后，每条原文按「。」拆成单句逐句翻译再按原序拼回——单句被审查或翻坏只回退那一句，不会毁掉整条；单句译文也进缓存。请求次数明显变多，适合对付经常翻坏长段的素材（如 AI 拓也的大长篇）。

翻译失败（含拒译）的单句以 `[[原句]]` 占位，续翻时自动只重翻这些句子；失败原句同时追加写到工作目录的 `translate-fails.txt`，方便提取关键词补进术语表。

## 13. 名词对照表（glossary）

格式 `原文<TAB>译文`（空格分隔也可以），`#` 是注释：

```
霊夢	灵梦
 BB劇場	BB剧场
```

加载顺序（后加载覆盖同名词条）：全局 `~/.jpsub/glossary.txt` → 工作目录 `glossary.txt` → `.env` 的 `GLOSSARY_FILE` → `--glossary` 指定文件。

自动识别候选角色名（单独成行、长度 ≤12、无标点/空格、出现 ≥2 次），确认后加入对照表：

```
.venv\Scripts\jpsub.exe glossary output/sm114514.jpsub            # 只打印候选
.venv\Scripts\jpsub.exe glossary output/sm114514.jpsub --append glossary.txt   # 追加「词=空」待填
```

主页「术语表」按钮可直接查看/编辑（全局 `~/.jpsub/glossary.txt` 与当前工作目录 `glossary.txt`），并一键从原文追加候选。

词条**不随提示词发送**，而是在发送前把原文里的词条直接替换成译文（长词优先，⟦⟧标记提醒 AI 原样保留），专有名词必准，也大幅降低含敏感词素材被 API 拒译的概率。改动对照表后需重翻对应句子才生效（旧译文不自动改）。

## 14. 多语言翻译

默认日语→简体中文。改源/目的语言两种方式：

```python
# .env
SOURCE_LANG = "JAP"       # 源语言(百度 OCR 简写,OCR 与翻译提示词共用)
TARGET_LANG = "简体中文"   # 目的语言
```

或命令行临时指定（优先级更高）：

```
.venv\Scripts\jpsub.exe sm43168834 --src-lang ENG --to-lang English
```

`OCR_LANGUAGE` 留空时自动跟随 `SOURCE_LANG`。注意：切换语言后需 `--force` 重翻，已入库的旧译文不受影响；中文全角标点规范化只在目的语含「中」时生效。

## 15. 自定义翻译提示词（`--prompt`）

```
.venv\Scripts\jpsub.exe translate output/sm114514.jpsub --prompt myprompt.txt
```

留空用内置默认（`prompts/bb.txt`）。文件里可用占位符：`{src}` 源语言名、`{tgt}` 目的语言名、`{punct}` 标点规则（目的语为中文时生成）。也可写 `.env` 的 `PROMPT_FILE` 永久生效。

## 16. B 站投稿（upload）

```
.venv\Scripts\jpsub.exe upload output/sm114514.jpsub --delay 3600
```

用 biliup 把工作目录成品（打码视频 > 原视频）上传并提交稿件：标题默认 info.txt 原标题（可 `--title`/`--desc` 覆盖，支持标题前缀）、简介由 info.txt 组装、标签取 info 标签行（兜底 `UPLOAD_TAGS`）、封面 `cover.jpg` 自动上传、转载 URL 自动填、`--delay` 定时发布。一次性登录用 `jpsub upload <目录> --login`（代跑 biliup-rs 扫码）或 `biliup login` 后把 cookies.json 放 `~/.jpsub/`。BV 号写回工作目录 `upload.json`，重复投稿先查此文件拦截。直接传**裸条目名**也可以，自动按 output/ 下的条目查找。详见 [upload.md](upload.md)。

> `mask` / `maskapply` 的视频参数同样支持直接传工作目录或裸条目名，自动定位其中的视频。

## 17. 调试日志（`--debug`）

所有子命令都支持 `--debug`，把**本次运行**的全流程事件按行 JSON 追加写到项目根 `logs/debug.log`：

```
.venv\Scripts\jpsub.exe run "output\sm114431.jpsub\...mp4" --force --debug
```

记录内容（含时间戳）：

- `run start`：子命令、argv、工作目录、平台；
- `extract params` / `extract frames`：生效的 `--crop/--fps/--start/--end` 与阈值、抽到的帧数、`frame_dur`、`offset`；
- `trigger spans`：关键帧筛选出的每段起止帧与关键帧文件名；
- `ocr texts`：每个关键帧的 OCR 文本（帧归属核对）；
- `ocr stitch` / `run_pos` / `run_nopos`：多帧拼接批次的尺寸、编号字体、逐识别行的 `top`/`divmod` 归属与结果、无位置版接口的编号命中数；
- `segment merged`：合并后每条字幕的起止时间与文本；
- `render done`：输出路径、段数、字体与字号；
- `ffmpeg_error` / `ffmpeg_oserror` / `fatal`：ffmpeg 失败（返回码 + 完整命令 + stderr 尾部）或未处理异常（类型 + traceback）。

同时把多帧拼接图存到 `logs/debug/ocr_batch_<首帧名>.png`，可直接查看分隔带里的帧编号是否绘制正确。

不传 `--debug` 时零开销、不落盘。主页 ① 的 **Debug 复选框** 勾选后，按钮发起的任务自动带上 `--debug`（详见 [home.md](home.md)）。

## 18. 字幕文件导入与整理（import / submerge / layout / convert）

把已有的字幕文件（SRT/ASS/TXT）接进 jpsub 流水线，或做格式整理（移植自「烤肉汉化排版」的合并/排版能力）。编码默认自动识别（UTF-8/16、GB18030、Shift-JIS、EUC-KR、Big5，可用 `--enc` 指定）。

```
# 导入到工作目录 → .jpsub/segments.json，之后可直接 render / edit / translate
.venv\Scripts\jpsub.exe import D:\sub.srt --work output/sm114514.jpsub

# 合并碎片/重复条目后另存（不建工作目录）
.venv\Scripts\jpsub.exe submerge D:\sub.srt -o D:\sub.merged.ass --out-format ass

# 排版：把空格/换行转 \N（时间轴不变）
.venv\Scripts\jpsub.exe layout D:\sub.ass

# 格式互转，不改时间与文本
.venv\Scripts\jpsub.exe convert D:\sub.txt --out-format srt
```

`import` / `submerge` 共用合并参数：`--merge-cap`（间隔上限秒，默认 3）、`--same-style`（只合并同样式，ASS 用）、`--smart`（智能去重，默认开）、`--layout`（顺带排版）。合并判据与烤肉一致：包含/前缀相似/模糊子序列去重，单行短名字行并入后立即封组防串句。主页 ①「字幕文件」区填路径选操作即可执行。

## 19. AI 校对（proofread）

对工作目录已有译文再送一次 AI，按编号行协议逐条校对（保持原意、人物语气与字幕长度），失败批次保留原译文不动：

```
.venv\Scripts\jpsub.exe proofread output/sm114514.jpsub --dry-run     # 只看条数
.venv\Scripts\jpsub.exe proofread output/sm114514.jpsub --instruct "统一称呼为「前辈」"
```

主页「AI 校对」按钮等价于此。逐条校对完不自动生成 ASS，仍需 `render`。

## 20. API 预设（preset）与用量（usage）

```
.venv\Scripts\jpsub.exe preset list
.venv\Scripts\jpsub.exe preset add 中转A --api-base https://xx/v1 --api-key sk-xx --model deepseek-chat
.venv\Scripts\jpsub.exe preset use 中转A
.venv\Scripts\jpsub.exe preset del 中转A

.venv\Scripts\jpsub.exe usage          # token 用量与费用估算
.venv\Scripts\jpsub.exe usage --json
.venv\Scripts\jpsub.exe usage --clear
```

预设存 `~/.jpsub/api-presets.json`，用量存 `~/.jpsub/usage.json`（跨进程累计）。优先级：命令行/环境变量 > 预设 > `.env`。单价在 `.env` 的 `PRICE_HIT`/`PRICE_IN`/`PRICE_OUT`（元/百万 token）按所用端点实际价格修改。接口报错会转成可读提示（额度不足/被 Cloudflare 拦截/网关后端故障等），不再直接抛整页 HTML。
