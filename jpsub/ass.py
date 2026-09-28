"""生成外挂 ASS 字幕。"""
from __future__ import annotations

from pathlib import Path

import pysubs2

from . import settings
from .segment import Segment

import re as _re
import unicodedata as _ud

# 渲染前把超长省略号截短:连续 6 个点(3 个「...」)及以上只留 2 个;全角「…」同理
_DOT_RUN = _re.compile(r"\.{6,}|…{3,}")

# 折行用:括号对与断行优先级(句末标点 > 逗号顿号 > 空格)
_OPEN_BR = "「『（《〈【〔"
_CLOSE_BR = "」』）》〉】〕"
_END_PUNCT = "。！？…!?"
_MID_PUNCT = "，、：；,;:"
# 禁止出现在行首的字符(避头尾)
_NO_START = _CLOSE_BR + _END_PUNCT + _MID_PUNCT


def _cw(ch: str) -> float:
    """单个字符的显示宽度:全角 1,半角 0.5。"""
    return 1.0 if _ud.east_asian_width(ch) in "WF" else 0.5


def _wlen(s: str) -> float:
    return sum(_cw(c) for c in s)


def _wrap_line(s: str, max_chars: float) -> list[str]:
    """单行按宽度切分:贪心扫描,超宽时回退到最近的优先断点。断行优先级:
    句末标点/闭括号 > 逗号顿号 > 空格;括号内断点降一级(仅无括号外断点
    时才用)。断点不允许落在禁行首字符之前(避头尾);无断点才硬切,硬切
    位置若遇行首标点则悬挂到上一行,行尾开括号则收回下一行。"""
    parts: list[str] = []
    start = 0
    while True:
        if _wlen(s[start:]) <= max_chars:
            parts.append(s[start:])
            break
        cand_pos, cand_pri = -1, -1
        cut, depth, width = -1, 0, 0.0
        i = start
        while i < len(s):
            c = s[i]
            if c in _OPEN_BR:
                depth += 1
            elif c in _CLOSE_BR:
                depth = max(0, depth - 1)
            width += _cw(c)
            if width > max_chars:
                cut = i
                break
            nxt = s[i + 1] if i + 1 < len(s) else ""
            if c not in _OPEN_BR and nxt and nxt not in _OPEN_BR and nxt not in _NO_START:
                pri = -1
                if c in _END_PUNCT or c in _CLOSE_BR:
                    pri = 4 if depth == 0 else 3
                elif c in _MID_PUNCT:
                    pri = 3 if depth == 0 else 2
                elif c == " ":
                    pri = 1
                if pri > 0 and pri >= cand_pri:
                    cand_pos, cand_pri = i, pri
            i += 1
        if cand_pos >= start:
            br = cand_pos + 1
        else:
            br = cut
            while br < len(s) and s[br] in _NO_START:  # 行首标点悬挂到上一行
                br += 1
            while br > start + 1 and s[br - 1] in _OPEN_BR:  # 行尾开括号下放
                br -= 1
        parts.append(s[start:br])
        start = br
    return parts


def _wrap(text: str, max_chars: float) -> str:
    """译文折行:每行限宽 settings.MAX_CHARS,两行时短的一行尾部补全角
    空格使上下行视觉均衡。"""
    out: list[str] = []
    for para in text.split("\n"):
        parts = _wrap_line(para, max_chars) if para else [""]
        if len(parts) == 2:
            diff = _wlen(parts[0]) - _wlen(parts[1])
            if diff > 0:
                parts[1] += "　" * int(diff / 2 + 0.5)
        out.extend(parts)
    return "\\N".join(out)


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
        text = _wrap(text.replace("\r", ""), settings.MAX_CHARS)  # 超宽折行 → \N
        subs.append(
            pysubs2.SSAEvent(
                start=int(seg.start * 1000),
                end=int(seg.end * 1000),
                text=text,
            )
        )
    subs.save(str(out_path), encoding="utf-8")


def write_bcc(
    segments: list[Segment],
    translations: list[str],
    out_path: Path,
) -> None:
    """把字幕段与译文写成必剪 .bcc(JSON)。不做 ASS 式自动折行,译文里
    最多保留一个换行(第一个 \\N 或换行符),其余丢弃。"""
    import json

    body = []
    for seg, text in zip(segments, translations):
        if not text:
            continue
        text = _DOT_RUN.sub(lambda m: "......" if m.group(0)[0] == "." else "……", text)
        text = text.replace("\r", "")
        parts = [p for part in text.split("\\N") for p in part.split("\n")]
        content = parts[0] + ("\n" + "".join(parts[1:]) if len(parts) > 1 else "")
        content = content.strip()
        if not content:
            continue
        body.append({
            "from": round(seg.start, 3),
            "to": round(seg.end, 3),
            "location": 2,
            "content": content,
        })
    # 字段对齐 B 站 CC 导出格式
    out_path.write_text(json.dumps({
        "font_size": 0.4,
        "font_color": "#FFFFFF",
        "background_alpha": 0.5,
        "background_color": "#9C27B0",
        "stroke": "none",
        "body": body,
    }, ensure_ascii=False), encoding="utf-8")
