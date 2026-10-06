# 常见问题

- **OCR 报错 / 识别为空**：检查 `.env` 里百度 OCR 的 AK/SK 是否填好；百度接口免费额度共 3500 次/月（默认高精度接口优先，已用次数记在 `~/.jpsub/baidu_ocr_state.json`，月初重置，额度用尽自动切换备用接口），用完可等月初或改用 `OCR_PROVIDER = "openai"` 走视觉模型。
- **下载视频失败**：检查代理是否为全局模式、`bin` 文件夹里 `yt-dlp.exe` 是**最新版**且有 `ffmpeg.exe` 和 `ffprobe.exe`（合并音视频必需）。
- **翻译中断了**：主页选中条目点「续翻」即可（命令行 `jpsub translate output/sm114514.jpsub`），已翻译的内容有缓存，不会重复消耗。
- **总有句子翻坏/被拒译**：失败原句会追加写到工作目录 `translate-fails.txt`，把里面的关键词加进名词对照表（见 [cli.md](cli.md) 第 13 节）后再「续翻」，词条会预替换进原文，成功率大幅提高。
- **字幕没显示**：确认 `.ass` 字幕文件与视频同名并放在同一文件夹，使用支持外挂字幕的播放器。
- **字幕位置/字体想改**：样式可在 `.env` 的 ASS 字幕样式区修改，或用 `--font/--font-size` 等参数指定并重新生成（见 [cli.md](cli.md) 第 5 节）。
- **字幕时间轴错乱（大量句子挤在开头、后面长时间空）**：多帧拼接批量识别时靠分隔带里的帧编号切分各帧文本，旧版本在 Windows 上因找不到 Linux 字体路径而画不出编号，整批文本被并进首帧。现已改为跨平台字体候选并回退内置字体，识别不到编号时自动降级到下一接口；如仍遇到请更新到最新版后重新生成字幕。
- **抽帧报 `Unable to choose an output format` / 找不到 ffmpeg**：说明用到的 ffmpeg 是残缺版本（缺 image2 muxer），常见于编辑器/工具链注入到 PATH 的精简版。程序在非 Windows 平台会优先使用系统标准目录（`/usr/local/bin`、`/usr/bin`、`/bin`）里的 ffmpeg 以避开污染的 PATH；Windows 版会优先用程序目录/bin 下自带的 `ffmpeg.exe`。确认这些位置的 ffmpeg 完整即可。
- **想看某次运行到底发生了什么**：给命令加 `--debug`（主页 ① 勾选「Debug 日志」），全流程事件写到 `logs/debug.log`，OCR 拼接图存 `logs/debug/`，详见 [cli.md](cli.md) 第 17 节。
