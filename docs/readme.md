# jpsub

niconico 日语视频 → 中文外挂 ASS 字幕（在线 OCR + AI 翻译 + 烧录/打码/批量）。

## 技术栈

- Python >= 3.14，setuptools 打包，入口 `jpsub = jpsub.cli:main`
- 标准库 `http.server`：主页 / 译文调整器 / 打码选取器
- numpy + Pillow：抽帧掩膜、关键帧判定、打码
- pysubs2：ASS / 必剪 BCC 字幕读写
- rapidfuzz：段落相似度合并；tqdm：进度
- biliup：B 站投稿；外部工具 ffmpeg / yt-dlp（放 `bin/`）

## 结构

- jpsub/ 核心代码
- docs/ 文档（见 [index.md](index.md)）
- prompts/ 翻译提示词
- tests/ 端到端验收脚本
- output/ 产物与工作目录

## 入口

- 启动：`jpsub`（无参数即打开主页）
- 测试：`python tests/verify_port_tools.py`（tests/ 下为端到端验收脚本）
