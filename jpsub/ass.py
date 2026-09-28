"""生成外挂 ASS 字幕。"""
from __future__ import annotations

from pathlib import Path

import pysubs2

from . import settings
from .segment import Segment

import re as _re

# 渲染前把超长省略号截短:连续 6 个点(3 个「...」)及以上只留 2 个;全角「…」同理
_DOT_RUN = _re.compile(r"\.{6,}|…{3,}")


def write_ass(
    segments: list[Segment],
    translations: list[str],
    out_path: Path,
    *,
    font: str = "Noto Sans CJK SC",
    font_size: int = 54,
) -> None:
    """把字幕段与译文写成 ASS;缺译文的段不显示(不用日文原文填补)。
    translations 与 segments 按下标一一对应(空串=跳过),不用文本做键,
    避免重复原文段共用/覆盖译文。位置交给播放器默认处理。

    一条字幕整体显示为一行(长文模式的句子切分已保证粒度足够细)。
    """
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

    for seg, text in zip(segments, translations):
        if not text:
            continue
        text = _DOT_RUN.sub(lambda m: "......" if m.group(0)[0] == "." else "……", text)
        text = text.replace("\r", "").replace("\n", "\\N")  # 译文换行 → ASS 硬换行
        subs.append(
            pysubs2.SSAEvent(
                start=int(seg.start * 1000),
                end=int(seg.end * 1000),
                text=text,
            )
        )
    subs.save(str(out_path), encoding="utf-8")
