"""全局设置:默认值内置,可被程序目录 settings.py 覆盖。

- API_BASE/API_KEY/MODEL:AI 翻译用的 OpenAI 兼容端点配置
- PROXY:访问国外资源用的代理;留空走系统代理
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def binary(name: str) -> str:
    """外部工具路径:Windows 依次找程序目录下 <name>.exe、bin/<name>.exe,都没有则用系统 PATH。"""
    if sys.platform == "win32":
        root = _program_root()
        for p in (root / f"{name}.exe", root / "bin" / f"{name}.exe"):
            if p.is_file():
                return str(p)
    return name


def _program_root() -> Path:
    """程序根目录:打包后为 exe 所在目录,否则为项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


# ---------- 内置默认值 ----------

API_BASE = ""
API_KEY = ""
MODEL = ""
PROXY = ""
COOKIES_FROM_BROWSER = ""  # 传给 yt-dlp 的 --cookies-from-browser,如 "firefox"

NICO_VIDEO_QUALITY = "360p"
NICO_AUDIO_QUALITY = "lowest"

# 在线 OCR:baidu(默认,AK/SK 见下)或 openai(OpenAI 兼容视觉 API)
OCR_PROVIDER = "baidu"
BAIDU_OCR_API_KEY = ""  # 百度智能云 AK
BAIDU_OCR_SECRET_KEY = ""  # 百度智能云 SK
OCR_LANGUAGE = "JAP"  # 百度 OCR 语言,JAP=日文
OCR_UPSCALE = True  # 小图(<1600px)2x 放大后再送识别;False=关闭放大用原图
OCR_BOOST = (
    False  # 特殊画面(火焰等亮暖背景浅白字)文字凸显预处理;默认关,用 --ocr-boost 开启
)
OCR_API_BASE = ""  # openai 引擎用;留空回退 API_BASE
OCR_API_KEY = ""  # openai 引擎用;留空回退 API_KEY
OCR_MODEL = ""  # openai 视觉模型名,必填(openai 引擎时)

FPS = 2.0
CROP = "0.78:0.02:0.01:0.01"
# --crop 快捷预设:--crop <键> 时实际使用对应值,如 {"1": "0.03:0.03:0.02:0.02"}
CROP_PRESETS: dict[str, str] = {}
# 标签快捷裁剪:info 标签含键时采用对应值(可为 CROP_PRESETS 的键或 crop 值)
TAG_CROP: dict[str, str] = {}
# 标签自动长文:info 标签含任一关键词时翻译自动启用长文模式(--long);空 set=关闭
TAG_LONG: set[str] = set()
DIFF_THRESHOLD = 2.0
SETTLE_FRAMES = 1
MAX_RUN = 10
SIMILARITY = 0.85
BATCH_SIZE = 30
BATCH_CHAR_TARGET = 1500  # 动态批大小:每批翻译的字符总量上限,长句自动减少批内句数
# 翻译并发数已改为按原文总字数动态计算(ai.calc_concurrency):3792字→16,5230字→24,钳制 8~32
CENSOR_RESET = False  # 内容审查触发时清空对话历史重建上下文:True=payload 级审查的 API 用(组内命中 2 次自动清史);False=只查单句的 API,保留历史
REASONING_EFFORT = "none"  # 关闭推理模型思考省输出 token:"none"=关闭;留空=不发送该参数(部分端点不认会报错)
HISTORY_KEEP = 3  # 翻译对话历史保留最近几轮(不含 system);0=全保留。长对话每批重发全部历史,砍掉旧轮省 prompt token
# 源语言:直接用百度 OCR 语言简写,OCR 与翻译提示词共用,无需两处配置
# 常用:JAP=日语 ENG=英语 CHN_ENG=中英 KOR=韩语 FRE=法语 GER=德语 RUS=俄语 SPA=西班牙语
SOURCE_LANG = "JAP"
TARGET_LANG = "简体中文"  # 目的语言(翻译输出用,写自然语言名即可)
OCR_LANGUAGE = (
    ""  # 百度 OCR 识别语言;留空自动用 SOURCE_LANG,仅当需与源语言不同时才填(如 CHN_ENG)
)
PROMPT_FILE = ""  # 翻译提示词文件路径,留空用内置默认;支持 {src}/{tgt}/{punct} 占位;--prompt 参数优先
GLOSSARY_FILE = (
    "glossary.txt"  # 默认名词对照表路径(工作目录 glossary.txt 与 --glossary 优先级更高)
)

# 下载时写入工作目录 info.txt 的模板;可用占位符
# {url} {title} {uploader} {date} {description} {tags},未知占位符原样保留
INFO_TEMPLATE = """{url}
原标题:{title}
原作者:{uploader}
投稿时间:{date}
原简介:{description}
标签:{tags}"""

FONT = "微软雅黑"
FONT_SIZE = 20
MAX_CHARS = 20  # 字幕每行显示宽度上限(全角1/半角0.5),超宽自动折行
OUTLINE_COLOR = (255, 165, 0)
OUTLINE_WIDTH = 1
SHADOW = 0

EDIT_PLAYBACK_RATE = 1.0  # edit 页视频默认播放速度,如 0.5/1.25/2.0

# ---------- 投稿(B 站) ----------
BILIUP_COOKIE = "~/.jpsub/cookies.json"  # biliup-rs login 导出的 cookies.json 路径
UPLOAD_TID = 22  # 投稿分区 tid(鬼畜主区119的二级分区:22鬼畜调教 26音MAD 126人力VOCALOID 216鬼畜剧场)
UPLOAD_PREFIX = ""  # 投稿标题前缀(如 "[搬运]"),网页「保存前缀」会更新此值(运行时)
UPLOAD_TAGS = ""  # 兜底标签(info.txt 无标签行时用),逗号分隔
UPLOAD_DELAY = 0  # 定时发布:从现在起延迟的秒数;0=立即发布


def apply_proxy() -> None:
    """若配置了 PROXY,则设置下载用的环境变量(幂等,不覆盖已有值)。"""
    if not PROXY:
        return
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.setdefault(var, PROXY)


# ---------- 语言简写 ↔ 中文名 ----------

# 百度 OCR 语言简写 -> 中文名(翻译提示词用)
_BAIDU_LANG_NAMES = {
    "CHN_ENG": "中文",
    "CHN": "中文",
    "ENG": "英语",
    "JAP": "日语",
    "KOR": "韩语",
    "FRE": "法语",
    "SPA": "西班牙语",
    "TH": "泰语",
    "GER": "德语",
    "ITA": "意大利语",
    "PT": "葡萄牙语",
    "RUS": "俄语",
}


def lang_name(v: str) -> str:
    """语言简写转中文名(如 JAP -> 日语);不是已知简写则原样返回(兼容直接写中文名)。"""
    return _BAIDU_LANG_NAMES.get(v.upper(), v) if v else v


def source_lang_name() -> str:
    """SOURCE_LANG 对应的中文名,给翻译提示词/OpenAI 视觉 OCR 提示用。"""
    return lang_name(SOURCE_LANG)


def ocr_language() -> str:
    """百度 OCR 识别语言:OCR_LANGUAGE 留空时自动用 SOURCE_LANG。"""
    return OCR_LANGUAGE or SOURCE_LANG


# ---------- CPU 自适应:并行参数按机器核数自动调优,不写死 ----------


def cpu_workers(n: int) -> int:
    """CPU 密集并行度:普通模式吃满全部逻辑核。"""
    return max(1, min(n, os.cpu_count() or 2))


def ffmpeg_threads() -> int:
    """单个 ffmpeg 实例的 -threads:吃满全部逻辑核。"""
    return os.cpu_count() or 1


def _load_user_settings() -> None:
    """加载程序目录 settings.py(用户覆盖),用其中的大写变量覆盖上面的默认值。"""
    p = _program_root() / "settings.py"
    if not p.is_file():
        return
    ns: dict = {}
    exec(compile(p.read_text(encoding="utf-8"), str(p), "exec"), ns)
    for k, v in ns.items():
        if k.isupper():
            globals()[k] = v


_load_user_settings()
