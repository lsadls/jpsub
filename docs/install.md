# 安装准备（源码运行）

打包版无需以下步骤。使用源码运行需要：

- **Python 3.14+**（`pyproject.toml` 要求 `>=3.14`）；
- 先开启代理，再下载外部工具。

## 1. 开启全局代理（重要）

- 下载 niconico 视频、调用百度 OCR / AI 翻译，都需要访问国外网站。
- 请先开启你的代理软件（如 Clash、v2rayN 等）并开启**全局模式（Global）**，或在 `.env` 里配 `PROXY`。

## 2. 下载两个外部工具

请到官网手动下载（不要用命令行下载），全部放进程序目录下的 **`bin` 文件夹**：

| 工具   | 下载地址                                                                         | 说明                                                    |
| ------ | -------------------------------------------------------------------------------- | ------------------------------------------------------- |
| ffmpeg | https://www.gyan.dev/ffmpeg/builds/ （选 "ffmpeg-release-essentials.7z" 压缩包） | 解压后取 `bin` 文件夹里的 `ffmpeg.exe` 和 `ffprobe.exe` |
| yt-dlp | https://github.com/yt-dlp/yt-dlp/releases （下载 `yt-dlp.exe`）                  | 用于下载 niconico 视频                                  |

可选：装上 [aria2c](https://github.com/aria2/aria2/releases)（放 `bin` 或加入 PATH），下载视频会自动改用它多连接，带宽吃得更满。

放好后程序目录应长这样：

```
.venv
jpsub
.env        ← 你的 AI 配置(可选,没有就用程序内置默认值)
ffmpeg.exe
ffprobe.exe
yt-dlp.exe
.env.example  ← 默认配置模板,不用动
```

也可以不用放进 `bin`，改为安装到系统并加入 PATH，二选一即可。

## 3. 安装可选依赖（按需）

核心库（Pillow、pysubs2、requests、aiohttp）随程序安装；下面按实际用途选装，不装也能跑其余功能：

```bash
pip install "jpsub[auto]"   # 自动去人（肤色检测）:引入 numpy;tqdm 只加进度条
```

- **不装 `auto`**：`jpsub mask --auto` 会提示安装；其余打码（手工选取器、`maskapply`、敏感词打码）、下载 / OCR / 翻译 / 字幕全部可用；
- B 站投稿（`jpsub upload`）**已内置**，无需额外安装：上传接口是自 [biliup](https://github.com/biliup/biliup) 裁剪的 vendored 实现（`jpsub/bili.py`），只保留 cookies 登录 + upos 上传，去掉了 streamlink / yt-dlp / rsa 等重传递依赖。详见 [bili.md](bili.md)；
- 相似度合并用标准库 `difflib`，无需额外依赖。
