"""AI 辅助小项:API 配置预设、术语表自动识别、翻译用量持久化、接口异常可读化。

供 ai.py 与 cli.py 复用。程序不做任何网络请求。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

# ---------- 路径 ----------


def _config_dir() -> Path:
    """用户配置目录 ~/.jpsub(与 glossary.txt 同处)。"""
    return Path.home() / ".jpsub"


def usage_path() -> Path:
    return _config_dir() / "usage.json"


def preset_path() -> Path:
    return _config_dir() / "api-presets.json"


def _atomic_write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ---------- 用量统计(持久化) ----------

# 价格估算(人民币/百万 token):命中缓存 / 未命中输入 / 输出
PRICE_HIT = 0.02
PRICE_IN = 1.0
PRICE_OUT = 2.0


def _empty_usage() -> dict:
    return {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "cost": 0.0,
    }


def load_usage() -> dict:
    p = usage_path()
    data = _empty_usage()
    if p.is_file():
        try:
            data.update(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return data


def record_usage(u: dict) -> None:
    """把一次请求的 usage 累加进本地记录并落盘(程序重启后仍可查询)。"""
    if not u:
        return
    data = load_usage()
    pt = int(u.get("prompt_tokens") or 0)
    ct = int(u.get("completion_tokens") or 0)
    hit = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    data["calls"] = int(data.get("calls", 0)) + 1
    data["prompt_tokens"] = int(data.get("prompt_tokens", 0)) + pt
    data["completion_tokens"] = int(data.get("completion_tokens", 0)) + ct
    data["cached_tokens"] = int(data.get("cached_tokens", 0)) + hit
    data["cost"] = round(
        float(data.get("cost", 0.0))
        + (hit * PRICE_HIT + (pt - hit) * PRICE_IN + ct * PRICE_OUT) / 1e6,
        6,
    )
    _atomic_write(usage_path(), data)


def clear_usage() -> None:
    _atomic_write(usage_path(), _empty_usage())


def usage_summary(data: dict | None = None) -> str:
    d = data or load_usage()
    pt = int(d.get("prompt_tokens", 0))
    hit = int(d.get("cached_tokens", 0))
    return (
        f"调用 {d.get('calls', 0)} 次  输入 {pt}(命中 {hit} / 未命中 {pt - hit})  "
        f"输出 {d.get('completion_tokens', 0)}  ≈¥{float(d.get('cost', 0.0)):.4f}"
    )


# ---------- API 配置预设 ----------


def load_presets() -> dict:
    p = preset_path()
    if not p.is_file():
        return {"presets": {}, "current": ""}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"presets": {}, "current": ""}
    d.setdefault("presets", {})
    d.setdefault("current", "")
    return d


def save_presets(d: dict) -> None:
    _atomic_write(preset_path(), d)


def set_preset(name: str, cfg: dict, *, select: bool = True) -> None:
    d = load_presets()
    d["presets"][name] = {
        k: cfg.get(k, "") for k in ("api_base", "api_key", "model")
    }
    if select:
        d["current"] = name
    save_presets(d)


def delete_preset(name: str) -> bool:
    d = load_presets()
    if name not in d["presets"]:
        return False
    del d["presets"][name]
    if d.get("current") == name:
        d["current"] = ""
    save_presets(d)
    return True


def get_preset(name: str | None = None) -> dict | None:
    """取预设;name 为空时取当前选中项。"""
    d = load_presets()
    key = name or d.get("current") or ""
    if not key:
        return None
    return d["presets"].get(key)


# ---------- 术语表自动识别角色名 ----------

_DECOR = "[-一ー—\\s]"


def _norm_name(s: str) -> str:
    return s.strip().strip(_DECOR).strip()


_NAME_BAD = re.compile(r"[\s/：:、,，。「」『』\"'（）()\[\]{}!?!?…—\-]")


def auto_name_candidates(texts: list[str], *, top: int = 30) -> list[str]:
    """从原文自动识别候选角色名。

    判据:单独成行、长度 ≤12、无空格/标点/斜杠/冒号/引号、不以句末标点结尾、
    同一词出现 ≥2 次。按出现频次取前 top 条。
    """
    counts: dict[str, int] = {}
    for raw in texts:
        t = _norm_name(str(raw))
        if not t or len(t) > 12:
            continue
        if _NAME_BAD.search(t):
            continue
        if t[-1] in "。!?!?…":
            continue
        counts[t] = counts.get(t, 0) + 1
    picked = [(k, v) for k, v in counts.items() if v >= 2]
    picked.sort(key=lambda kv: (-kv[1], kv[0]))
    return [k for k, _ in picked[:top]]


# ---------- 接口异常可读化 ----------

_CF_MARKERS = (
    "cloudflare",
    "cf-ray",
    "attention required",
    "just a moment",
    "checking your browser",
    "<title>access denied",
)


def readable_http_error(code: int, body: str, reason: str = "") -> str:
    """把接口的非 JSON 错误响应(尤其整页 HTML)转成说明真实原因的可读提示。

    依据真实状态码与响应内容判定,不给出与实际原因不符的笼统兜底。
    """
    snippet = (body or "").strip()
    low = snippet[:2000].lower()
    is_html = snippet[:200].lstrip().lower().startswith(("<html", "<!doctype", "<head"))
    if any(m in low for m in _CF_MARKERS):
        return (
            f"HTTP {code}:被 Cloudflare 拦截(端点要求浏览器验证),"
            "可能需要更换端点或改用官方 API"
        )
    if code in (502, 503, 504):
        return f"HTTP {code}:网关错误,中转站后端故障或不可用,稍后重试或更换端点"
    if code == 401:
        return "HTTP 401:认证失败,检查 API key 是否正确"
    if code == 403:
        return "HTTP 403:拒绝访问,key 无权调用该模型或端点被限制"
    if code == 404:
        return "HTTP 404:端点路径不存在,检查 api_base 是否遗漏 /v1"
    if code == 429:
        return "HTTP 429:请求过于频繁,降低并发或稍后重试"
    if is_html:
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", snippet)).strip()[:200]
        return f"HTTP {code}:接口返回网页而非 JSON(可能被拦截):{text}"
    short = re.sub(r"\s+", " ", snippet)[:300]
    return f"HTTP {code}:{short or reason}"
