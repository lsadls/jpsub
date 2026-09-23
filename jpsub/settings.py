"""全局设置:默认值来自程序目录 bin/settings.py,可被程序目录 settings.py 覆盖。

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


# ---------- 内置默认值(与 bin/settings.py 保持一致) ----------

API_BASE = ""
API_KEY = ""
MODEL = ""
PROXY = ""
COOKIES_FROM_BROWSER = ""  # 传给 yt-dlp 的 --cookies-from-browser,如 "firefox"

NICO_VIDEO_QUALITY = "360p"
NICO_AUDIO_QUALITY = "lowest"

OCR_DET_MODEL = "PP-OCRv5_mobile_det"
OCR_REC_MODEL = "PP-OCRv5_mobile_rec"

FPS = 2.0
CROP = "0.78:0.02:0.01:0.01"
DIFF_THRESHOLD = 2.0
SETTLE_FRAMES = 1
MAX_RUN = 10
SIMILARITY = 0.85
BATCH_SIZE = 30

FONT = "微软雅黑"
FONT_SIZE = 20
MAX_CHARS = 20
OUTLINE_COLOR = (255, 165, 0)
OUTLINE_WIDTH = 1
SHADOW = 0


def apply_proxy() -> None:
    """若配置了 PROXY,则设置下载用的环境变量(幂等,不覆盖已有值)。"""
    if not PROXY:
        return
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.setdefault(var, PROXY)


def _load_user_settings() -> None:
    """按顺序加载 bin/settings.py(默认)和程序目录 settings.py(用户覆盖),
    用其中的大写变量覆盖上面的默认值。"""
    for p in (_program_root() / "bin" / "settings.py", _program_root() / "settings.py"):
        if not p.is_file():
            continue
        ns: dict = {}
        exec(compile(p.read_text(encoding="utf-8"), str(p), "exec"), ns)
        for k, v in ns.items():
            if k.isupper():
                globals()[k] = v


_load_user_settings()
