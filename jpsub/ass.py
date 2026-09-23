"""生成外挂 ASS 字幕。"""
from __future__ import annotations

from pathlib import Path

import pysubs2

from . import settings
from .segment import Segment

_PUNCT = "、。，,.!?！?;；:："

MAX_LINES = 2  # 每条字幕最多行数,超出按时间切分成多条


def wrap_lines(text: str, *, max_chars: int = 24) -> list[str]:
    """按 max_chars 折行(优先在标点后断),返回行列表。"""
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]
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
    return lines


def wrap_cjk(text: str, *, max_chars: int = 24) -> str:
    """按 max_chars 折行(优先在标点后断),用 ASS 的 '\\N' 连接。"""
    return "\\N".join(wrap_lines(text, max_chars=max_chars))


def _split_events(
    text: str, start: float, end: float, *, max_chars: int
) -> list[tuple[float, float, str]]:
    """超长文本按每次最多 MAX_LINES 行切分,时长按字数比例分配。"""
    lines = wrap_lines(text, max_chars=max_chars)
    if len(lines) <= MAX_LINES:
        return [(start, end, "\\N".join(lines))]
    chunks = ["\\N".join(lines[i : i + MAX_LINES]) for i in range(0, len(lines), MAX_LINES)]
    total = sum(len(c.replace("\\N", "")) for c in chunks)
    dur = end - start
    events: list[tuple[float, float, str]] = []
    cur = start
    for i, chunk in enumerate(chunks):
        w = len(chunk.replace("\\N", "")) / total
        nxt = end if i == len(chunks) - 1 else cur + dur * w
        events.append((cur, nxt, chunk))
        cur = nxt
    return events


def write_ass(
    segments: list[Segment],
    translations: dict[str, str],
    out_path: Path,
    *,
    font: str = "Noto Sans CJK SC",
    font_size: int = 54,
    max_chars: int = 24,
) -> None:
    """把字幕段与译文写成 ASS;缺译文的段不显示(不用日文原文填补)。位置交给播放器默认处理。

    超过 MAX_LINES 行的段会按字数比例切分成多条,每条最多 MAX_LINES 行。
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

    for seg in segments:
        text = translations.get(seg.text)
        if not text:
            continue
        for start, end, chunk in _split_events(
            text, seg.start, seg.end, max_chars=max_chars
        ):
            subs.append(
                pysubs2.SSAEvent(
                    start=int(start * 1000),
                    end=int(end * 1000),
                    text=chunk,
                )
            )
    subs.save(str(out_path), encoding="utf-8")
