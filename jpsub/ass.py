"""生成外挂 ASS 字幕。"""
from __future__ import annotations

import json
from pathlib import Path

import pysubs2

from . import handoff, settings
from .segment import Segment

import re as _re
import unicodedata as _ud

# 字幕样式:文字颜色/描边颜色/描边宽度/位置(底部、顶部居中)
STYLE_COLOR = "#FFFFFF"          # 默认白字
STYLE_OUTLINE_WIDTH_MAX = 4      # 描边宽度上限(0 = 无描边)
STYLE_POSITIONS = ("bottom", "top")
STYLE_POSITION_ALIGN = {"bottom": 2, "top": 8}  # ASS Alignment:2 底部居中,8 顶部居中

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


def _rgb_hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(rgb)


def _hex_rgb(value) -> tuple[int, int, int] | None:
    """解析 #RRGGBB;非法返回 None。"""
    if not isinstance(value, str):
        return None
    s = value.strip().lstrip("#")
    if _re.fullmatch(r"[0-9a-fA-F]{6}", s) is None:
        return None
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def default_style() -> dict:
    """默认字幕样式:白字,描边与阴影沿用 settings。"""
    return {
        "color": STYLE_COLOR,
        "outline_color": _rgb_hex(settings.OUTLINE_COLOR),
        "outline_width": int(settings.OUTLINE_WIDTH),
        "position": "bottom",
    }


def normalize_style(data) -> dict:
    """规整样式:非法颜色/位置回退默认,描边宽度钳制到 0-4。"""
    style = default_style()
    if not isinstance(data, dict):
        return style
    for key in ("color", "outline_color"):
        rgb = _hex_rgb(data.get(key))
        if rgb is not None:
            style[key] = _rgb_hex(rgb)
    width = data.get("outline_width")
    if isinstance(width, (int, float)) and not isinstance(width, bool):
        style["outline_width"] = max(0, min(STYLE_OUTLINE_WIDTH_MAX, int(width)))
    if data.get("position") in STYLE_POSITIONS:
        style["position"] = data["position"]
    return style


def read_style(work: Path) -> dict:
    """读工作目录样式文件;缺失或损坏时回退默认。"""
    path = handoff.style_path(work)
    if not path.is_file():
        return default_style()
    try:
        return normalize_style(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return default_style()


def save_style(work: Path, data) -> dict:
    """把样式写入工作目录(规整后落盘),返回实际生效的样式。"""
    style = normalize_style(data)
    path = handoff.style_path(work)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(style, ensure_ascii=False, indent=1), encoding="utf-8")
    return style


def _apply_style(style: pysubs2.SSAStyle, conf: dict) -> None:
    r, g, b = _hex_rgb(conf["color"])
    style.primarycolor = pysubs2.Color(r, g, b, 0)
    r, g, b = _hex_rgb(conf["outline_color"])
    style.outlinecolor = pysubs2.Color(r, g, b, 0)
    style.outline = conf["outline_width"]
    style.alignment = STYLE_POSITION_ALIGN[conf["position"]]
    style.shadow = settings.SHADOW


def write_ass(
    segments: list[Segment],
    translations: list[str],
    out_path: Path,
    *,
    font: str = "Noto Sans CJK SC",
    font_size: int = 54,
    style_conf: dict | None = None,
) -> None:
    """把字幕段与译文写成 ASS;缺译文的段不显示(不用日文原文填补)。
    translations 与 segments 按下标一一对应(空串=跳过),不用文本做键,
    避免重复原文段共用/覆盖译文。style_conf 为空时用默认样式(白字、
    settings 描边、底部居中)。

    一条字幕整体显示为一行(长文模式的句子切分已保证粒度足够细)。
    """
    subs = pysubs2.SSAFile()
    style = pysubs2.SSAStyle()
    style.fontname = font
    style.fontsize = font_size
    _apply_style(style, normalize_style(style_conf) if style_conf else default_style())
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
    *,
    style_conf: dict | None = None,
) -> None:
    """把字幕段与译文写成必剪 .bcc(JSON)。不做 ASS 式自动折行,译文里
    最多保留一个换行(第一个 \\N 或换行符),其余丢弃。bcc 无描边/位置
    字段,只同步文字颜色到 font_color。"""
    style = normalize_style(style_conf) if style_conf else default_style()
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
        "font_color": style["color"],
        "background_alpha": 0.5,
        "background_color": "#9C27B0",
        "stroke": "none",
        "body": body,
    }, ensure_ascii=False), encoding="utf-8")
