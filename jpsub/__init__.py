"""jpsub:日语视频 -> 中文外挂 ASS 字幕(本地 OCR,程序零网络)。

HuggingFace 模型缓存约定:
- 默认把模型缓存固定到项目内 `.hf-cache/`(随仓库走,git 忽略);
- 模型已就位时自动开启离线模式(HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE),
  加载模型不再发任何网络请求(否则每次启动都会 HEAD huggingface.co 检查更新,
  网络不通时卡在重试);
- 需要联网下载/更新模型时显式覆盖:`HF_HUB_OFFLINE=0 jpsub ...`(可配 http_proxy)。

这些环境变量必须在 huggingface_hub/transformers 首次 import 之前设置,
因此放在包的 __init__ 顶部执行。
"""
from __future__ import annotations

import os
from pathlib import Path


def _setup_hf_cache() -> None:
    default_home = Path(__file__).resolve().parent.parent / ".hf-cache"
    home = Path(os.environ.setdefault("HF_HOME", str(default_home)))
    model = home / "hub" / "models--kha-white--manga-ocr-base"
    if model.exists():
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


_setup_hf_cache()
