# jpsub — niconico 日语视频 → 中文字幕工具

从 niconico 下载日语视频（或使用本地视频），自动识别日语字幕、翻译成中文，生成外挂 ASS 字幕，也可以直接把字幕烧录进视频。面向普通用户的免安装 exe 程序。

## 一、准备工作

### 1. 开启全局代理（重要）

- 下载 niconico 视频、首次运行自动下载 OCR 识别模型，都需要访问国外网站。
- 请先开启你的代理软件（如 Clash、v2rayN 等）并开启**全局模式（Global）**。

### 2. 下载两个外部工具

请到官网手动下载（不要用命令行下载），全部放进程序目录下的 **`bin` 文件夹**：

| 工具   | 下载地址                                                                         | 说明                                                    |
| ------ | -------------------------------------------------------------------------------- | ------------------------------------------------------- |
| ffmpeg | https://www.gyan.dev/ffmpeg/builds/ （选 "ffmpeg-release-essentials.7z" 压缩包） | 解压后取 `bin` 文件夹里的 `ffmpeg.exe` 和 `ffprobe.exe` |
| yt-dlp | https://github.com/yt-dlp/yt-dlp/releases （下载 `yt-dlp.exe`）                  | 用于下载 niconico 视频                                  |

放好后程序目录应长这样：

```
.venv
jpsub
settings.py        ← 你的 AI 配置(可选,没有就用 bin 里的默认值)
ffmpeg.exe
ffprobe.exe
yt-dlp.exe
settings.py   ← 默认配置,不用动
```

也可以不用放进 `bin`，改为安装到系统并加入 PATH，二选一即可。

## 二、配置 AI 翻译

在程序目录新建 `settings.py`（打包版内置默认值，这个文件可覆盖它们），填入你的 AI 服务信息：

```python
API_BASE = "https://api.xxx.com/v1"   # OpenAI 兼容接口地址
API_KEY  = "sk-xxxx"                  # 你的密钥
MODEL    = "模型名"                    # 使用的模型
```

只有你想修改的项才需要写，其余保持默认。代理端口、字幕样式等参数也可在此文件里覆盖。

> 推荐 [千问 AI 平台](https://platform.qianwenai.com)：注册的免费额度够翻译几百个视频。

## 三、使用方法

在程序所在文件夹的地址栏输入 `cmd` 回车打开命令行，然后运行：

### 1. 最常用：一条命令下载视频 + 生成中文字幕

```
.venv\Scripts\jpsub.exe https://www.nicovideo.jp/watch/sm42567589
```

直接写 sm 号也可以：`.venv\Scripts\jpsub.exe sm43168834`。完成后同目录会得到视频 `sm42567589.mp4` 和字幕 `sm42567589.ass`，用 PotPlayer / MPC-HC 等播放器打开视频即可看到中文字幕。

### 2. 把字幕烧录进视频

```
.venv\Scripts\jpsub.exe sm43168834 --burn
```

已生成过字幕时会直接烧录，不再重新处理。

### 3. 本地视频生成字幕

```
.venv\Scripts\jpsub.exe run "output\lesson01.mp4" --comment "恐怖剧场"
```

`--comment` 简单描述视频内容，可提高翻译质量。也可要求AI比如不更改专有名词的翻译

### 4. 只想生成日文字幕和时间轴文件（不翻译）

```
.venv\Scripts\jpsub.exe run "output\lesson01.mp4" --notrans
```

运行后在工作目录(如output\sm114514.jpsub)生成 `translate-in.txt` `segments.json`（日文原文和时间轴），不会调用 AI 翻译。

### 5. 调整字幕样式

```
.venv\Scripts\jpsub.exe run sm43168834 --font "微软雅黑" --font-size 32 --max-chars 20
```

### 6. 手动调整翻译

AI翻译完后去视频对应的目录下(如output\sm114514.jpsub)打开translate-out.txt文件,可以删除不需要的行或者更改翻译,时间轴通过segments.json文件调整, 之后重新生成字幕文件

```
.venv\Scripts\jpsub.exe run render output/sm114514.jpsub
```

### 7. 原字幕位置

运行时使用`--crop`参数指定原字幕位置，默认是0.25(从底部25%高度),大部分BB剧场的对话框(不加人名)都这么大.
AI拓也使用 `--crop 1` 获取全部文字,并使用 `--batch-size 3` 降低ai翻译的压力

### 8. 跳过视频段

使用`--start` `--end`参数来制定仅截取在此之间的视频作为字幕来源.
例如11分45秒后为借物表, 不作为字幕源

```
.venv\Scripts\jpsub.exe run sm43168834 --end 11:45
```

## 四、常见问题

- **首次运行很慢 / 卡在下载模型**：首次使用会自动下载 OCR 模型，请确认代理已开启全局模式，耐心等待。
- **下载视频失败**：检查代理是否为全局模式、`bin` 文件夹里 `yt-dlp.exe` 是**最新版**且有 `ffmpeg.exe` 和 `ffprobe.exe`（合并音视频必需）。
- **翻译中断了**：直接重跑translate命令即可，已翻译的内容有缓存，不会重复消耗。

  ```
  .venv\Scripts\jpsub.exe run translate output/sm114514.jpsub
  ```

- **字幕没显示**：确认 `.ass` 字幕文件与视频同名并放在同一文件夹，使用支持外挂字幕的播放器。
- **字幕位置/字体想改**：样式可在 `settings.py` 的 ASS 字幕样式区修改，或用 `--font/--font-size` 等参数指定并重新生成。
  ```
  .venv\Scripts\jpsub.exe render output/sm114514.jpsub --font "微软雅黑" --font-size 32 --max-chars 20
  ```
