# jpsub

niconico 日语视频 → 中文外挂 ASS 字幕（在线 OCR + AI 翻译 + 烧录/打码/批量）。

## 技术栈

- Python >= 3.14，setuptools 打包，入口 `jpsub = jpsub.cli:main`
- 标准库 `http.server`：主页 / 译文调整器 / 打码选取器
- Pillow：抽帧掩膜、关键帧判定、打码；pysubs2：ASS / 必剪 BCC 字幕读写
- `requests` + `aiohttp`：B 站投稿 `jpsub/bili.py`（自 [biliup](https://github.com/biliup/biliup) 裁剪，见 [bili.md](bili.md)）
- 相似度合并用标准库 `difflib`（无第三方依赖）
- 可选 `jpsub[auto]`（numpy、tqdm）；外部工具 ffmpeg / yt-dlp（放 `bin/`）

## 结构

- jpsub/ 核心代码
- docs/ 文档（见 [index.md](index.md)）
- tools/ 上游同步脚本
- prompts/ 翻译提示词
- tests/ 端到端验收脚本
- output/ 产物与工作目录

## 入口

- 启动：`jpsub`（无参数即打开主页）
- 测试：`python tests/verify_port_tools.py`（tests/ 下为端到端验收脚本）
