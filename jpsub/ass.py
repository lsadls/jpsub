"""生成外挂 ASS 字幕。"""
from __future__ import annotations

from pathlib import Path

import pysubs2

from . import settings
from .segment import Segment

_PUNCT = "、。，,.!?！?;；:："


def wrap_cjk(text: str, *, max_chars: int = 24) -> str:
    """按 max_chars 折行(优先在标点后断),用 ASS 的 '\\N' 连接。"""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    lines: list[str] = []
    rest = text
    while len(rest) > max_chars:
        window = rest[:max_chars]
        cut = max((window.rfind(p) for p in _PUNCT), default=-1)
        cut = cut + 1 if cut >= max_chars // 2 else max_chars
        lines.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        lines.append(rest)
    return "\\N".join(lines)


def write_ass(
    segments: list[Segment],
    translations: dict[str, str],
    out_path: Path,
    *,
    font: str = "Noto Sans CJK SC",
    font_size: int = 54,
    max_chars: int = 24,
) -> None:
    """把字幕段与译文写成 ASS;缺译文的段保留日文原文。位置交给播放器默认处理。"""
    subs = pysubs2.SSAFile()
    style = pysubs2.SSAStyle()
    style.fontname = font
    style.fontsize = font_size
    style.primarycolor = pysubs2.Color(255, 255, 255, 0)  # 白字
    r, g, b = settings.OUTLINE_COLOR
    style.outlinecolor = pysubs2.Color(r, g, b, 0)        # 描边(settings.OUTLINE_COLOR)
    style.outline = settings.OUTLINE_WIDTH
    style.shadow = settings.SHADOW
    subs.styles["Default"] = style

    for seg in segments:
        text = translations.get(seg.text) or seg.text
        subs.append(
            pysubs2.SSAEvent(
                start=int(seg.start * 1000),
                end=int(seg.end * 1000),
                text=wrap_cjk(text, max_chars=max_chars),
            )
        )
    subs.save(str(out_path), encoding="utf-8")
