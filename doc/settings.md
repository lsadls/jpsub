# 配置详解（settings.py）

在程序目录新建 `settings.py`（打包版内置默认值，这个文件可覆盖它们），填入你的 AI 服务信息。程序目录下另有一份 `settings example.py` 是同样的带注释模板，可直接复制改名为 `settings.py` 使用。只有你想修改的项才需要写，其余保持默认。

> 推荐 [千问 AI 平台](https://platform.qianwenai.com)：注册的免费额度够翻译几百个视频。

```python
COOKIES_FROM_BROWSER = ""  # 登录/地区限制时填浏览器名,如 "chrome"(见命令行参考9)
# https://platform.qianwenai.com
# 注册的免费额度够几百个视频了
API_BASE = ""  # 例如 "https://maas.qianwenaiapi.com/compatible-mode/v1"
API_KEY = ""   # 例如 "sk-xxx"
MODEL = ""     # 例如 "qwen3.7-flash"
# 翻译速度很大程度上受各家模型影响,deepseek每秒token在200-300,且会听话的按提示词不多想,其他家的模型明显慢一些,有些还会自作聪明的试图理清人物关系和剧情导致更慢
# qwen容易对输入应激(特别是AI拓也), 使用mimo, 基本不会应激

PROXY = ""  # 例如 "http://127.0.0.1:1080",留空不走代理

# niconico 下载质量
# VIDEO:"lowest"=最低画质,"best"=最高画质,"360p"/"480p"/"720p"=指定分辨率
NICO_VIDEO_QUALITY = "360p"
# AUDIO:"lowest"=最低码率,"best"=最高码率, 一般只有64k/192k 两种
NICO_AUDIO_QUALITY = "lowest"

# 在线 OCR 二选一,详见上方注释
OCR_PROVIDER = "baidu"
BAIDU_OCR_API_KEY = ""       # 百度智能云 AK
BAIDU_OCR_SECRET_KEY = ""    # 百度智能云 SK
# openai:OpenAI 兼容视觉 API(OCR_MODEL 必填,如 "qwen-vl-plus";KEY/BASE 留空回退上面的翻译配置)
OCR_API_BASE = ""
OCR_API_KEY = ""
OCR_MODEL = ""

# ---------- 语言 ----------
# 源语言用百度 OCR 简写,OCR 与翻译提示词共用一份配置
# 常用:JAP=日语 ENG=英语 CHN_ENG=中英 KOR=韩语 FRE=法语 GER=德语 RUS=俄语 SPA=西班牙语
SOURCE_LANG = "JAP"
TARGET_LANG = "简体中文"     # 目的语言(写自然语言名);含「中」才做中文全角标点规范化
OCR_LANGUAGE = ""            # 百度识别语言,留空自动用 SOURCE_LANG(需不同时才填,如 CHN_ENG)

# ---------- 字幕生成参数 ----------

# 抽帧:每秒抽几帧;CROP:截取视频底部高度的比例(字幕区域)
FPS = 2.0
CROP = "0.78:0.02:0.01:0.01"
# --crop 快捷预设:--crop <键> 即用对应值
# CROP_PRESETS = {"1": "0.03:0.03:0.02:0.02"}
# 标签自动化:info 标签含键时生效(TAG_CROP 值可为 CROP_PRESETS 的键或 crop 值)
# TAG_CROP = {"拓也": "1"}   # 该标签的视频自动用对应裁剪
# TAG_LONG = {"拓也"}        # 该标签的视频自动启用长文拆句翻译(--long)

# 关键帧筛选:掩膜差异阈值(越小越灵敏,漏段少但误判多);静止多少帧算停顿(越小越容易收尾);连续变化超过多少帧强制识别
DIFF_THRESHOLD = 2.0
SETTLE_FRAMES = 1
MAX_RUN = 10

# 段落合并:文本相似度阈值
SIMILARITY = 0.85

# 翻译:每次请求翻译的句数(实际每批还会按字符总量自动收缩)
BATCH_SIZE = 10
BATCH_CHAR_TARGET = 1500     # 每批翻译的字符总量上限,长句自动减少批内句数
# 翻译并发数不用配:按待译总字数自动计算(3792字→16、5230字→24,钳 8~32)

# 省 token / 降拒译
REASONING_EFFORT = "none"    # 关闭推理模型思考,建议 "none" 或 "low";留空=不发送该参数(部分端点不认会报错)
HISTORY_KEEP = 3             # 翻译对话只保留最近几轮历史(0=全保留)
PROMPT_FILE = ""             # 翻译提示词文件,留空用内置默认;支持 {src}/{tgt}/{punct} 占位
GLOSSARY_FILE = "~/.jpsub/glossary.txt"  # 默认名词对照表路径(工作目录 glossary.txt 与 --glossary 优先级更高,加载顺序见 cli.md 第 13 节)

# ASS 字幕样式(位置交给播放器默认处理)
FONT = "Noto Sans CJK SC"  # 字体
FONT_SIZE = 20  # 字号
MAX_CHARS = 20  # 每行最大字数(超出折行)
OUTLINE_COLOR = (255, 165, 0)  # 描边颜色(RGB,当前为橙色)
OUTLINE_WIDTH = 1  # 描边宽度
SHADOW = 0  # 阴影宽度

# 译文调整器
EDIT_PLAYBACK_RATE = 1.0  # 视频默认播放速度,如 0.5/1.25/2.0
```
