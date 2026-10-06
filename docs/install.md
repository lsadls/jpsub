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
