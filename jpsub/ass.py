"""生成外挂 ASS 字幕。"""
from __future__ import annotations

from pathlib import Path

import pysubs2

from . import settings
from .segment import Segment

_PUNCT = "、。，,.!?！?;；:："

# 配对符号的开口侧:不能挂在行尾(行尾「、行头」分离非常难看),折行时应断在它前面。
# ASCII 的 " ' 分不出开闭,按闭符号处理(跟随前句),避免行首出现闭引号
_OPEN_SYMS = set("「『（(［[【〖〈《｛{“‘")

import re as _re

# 渲染前把超长省略号截短:连续 6 个点(3 个「...」)及以上只留 2 个;全角「…」同理
_DOT_RUN = _re.compile(r"\.{6,}|…{3,}")

MAX_LINES = 2  # 每条字幕最多行数,超出按时间切分成多条


def _is_sym(ch: str) -> bool:
    """标点/符号:不计入折行字数,折行时直接挂在行尾。"""
    import unicodedata

    return unicodedata.category(ch)[0] in ("P", "S")


def _real_len(text: str) -> int:
    """计入折行字数的长度(只数文字,不算标点符号)。"""
    return sum(1 for ch in text if not _is_sym(ch))


_SENT_END = set("。．!?！？…")  # 句子结束标点:折行在句子之间进行


def _merge_closings(parts: list[str]) -> list[str]:
    """把后一段开头的闭符号(」 ）等)合并回前一段收尾。"""
    for i in range(len(parts) - 1):
        nxt = parts[i + 1]
        j = 0
        while j < len(nxt) and _is_sym(nxt[j]) and nxt[j] not in _OPEN_SYMS:
            j += 1
        if j:
            parts[i] += nxt[:j]
            parts[i + 1] = nxt[j:]
    return [p for p in parts if p]


def _split_outside_parens(text: str, breaks: str) -> list[str]:
    """在 breaks 中的标点后切分,但跳过未闭合括号内部(括号内容不被拆开)。
    连续同类标点只切在最后一个之后。"""
    parts: list[str] = []
    buf = ""
    depth = 0
    for i, ch in enumerate(text):
        buf += ch
        if ch in _OPEN_SYMS:
            depth += 1
        elif depth > 0 and _is_sym(ch) and ch not in _OPEN_SYMS:
            depth = max(0, depth - 1)
        elif depth == 0 and ch in breaks:
            if i + 1 >= len(text) or text[i + 1] not in breaks:
                parts.append(buf)
                buf = ""
    if buf:
        parts.append(buf)
    return [p for p in parts if p]


def _sentences(text: str) -> list[str]:
    """把文本拆成完整句子:在句子结束标点后切,闭引号等收尾符号跟随前句,
    开口配对符号(「『( 等)归下一句开头,括号内不切。"""
    return _merge_closings(_split_outside_parens(text, "。．!?！?…"))


def _split_before_quotes(text: str) -> list[str]:
    """在最外层引语开口符号(「『)前切开:叙述+引语时引语另起单元,
    折行可断在引语前(如「出国后，他们在交谈」|「果然是……」)。"""
    parts: list[str] = []
    buf = ""
    depth = 0
    for ch in text:
        if depth == 0 and ch in "「『" and buf:
            parts.append(buf)
            buf = ch
        else:
            buf += ch
            if ch in _OPEN_SYMS:
                depth += 1
            elif depth > 0 and _is_sym(ch) and ch not in _OPEN_SYMS:
                depth = max(0, depth - 1)
    if buf:
        parts.append(buf)
    return [p for p in parts if p]


def _units(text: str, max_chars: int) -> list[str]:
    """折行单元:整句优先;超长句先按句中停顿(，、；：)与引语开口切短,
    仍超长的子句才按字数硬切。每个单元都不超过 max_chars。"""
    out: list[str] = []
    for s in _sentences(text):
        for c in _merge_closings(_split_outside_parens(s, "，、；：,;:")):
            for u in _split_before_quotes(c):
                if _real_len(u) <= max_chars:
                    out.append(u)
                else:
                    out.extend(_hard_split(u, max_chars))
    return out


def _hard_split(text: str, max_chars: int) -> list[str]:
    """无任何标点的超长子句按字数硬切:贪心填满 max_chars 个字,
    紧跟符号挂行尾(开口配对符号断在前面)。"""
    out: list[str] = []
    rest = text
    while _real_len(rest) > max_chars:
        cnt = idx = 0
        for i, ch in enumerate(rest):
            if not _is_sym(ch):
                cnt += 1
                if cnt == max_chars:
                    idx = i + 1
                    break
        while idx < len(rest) and _is_sym(rest[idx]) and rest[idx] not in _OPEN_SYMS:
            idx += 1
        # 断点落在未闭合的括号内:改为断在最外层未闭合开口符号前
        # (该括号内容自身超长时只能硬切,保留原断点)
        stack: list[int] = []
        for i, ch in enumerate(rest[:idx]):
            if ch in _OPEN_SYMS:
                stack.append(i)
            elif _is_sym(ch) and ch not in _OPEN_SYMS and stack:
                stack.pop()
        if stack and stack[0] > 0:
            idx = stack[0]
        out.append(rest[:idx])
        rest = rest[idx:]
    if rest:
        out.append(rest)
    return out


def wrap_lines(text: str, *, max_chars: int = 24) -> list[str]:
    """按 max_chars 折行:只数文字,标点符号不算字数,紧跟的符号直接挂行尾。
    整句优先:先拆句,逐句往行里装,装不下就换行;超长句按句中停顿再切,
    实在不行才按字数硬切,句子永远尽量不在中间断开。"""
    if max_chars <= 0 or _real_len(text) <= max_chars:
        return [text]
    lines: list[str] = []
    cur = ""
    cur_r = 0
    for u in _units(text, max_chars):
        if cur and cur_r + _real_len(u) > max_chars:  # 装不下:换行
            lines.append(cur)
            cur = ""
        cur += u
        cur_r = _real_len(cur)
    if cur:
        lines.append(cur)
    return lines


def wrap_cjk(text: str, *, max_chars: int = 24) -> str:
    """按 max_chars 折行(优先在标点后断),用 ASS 的 '\\N' 连接。"""
    return _join(wrap_lines(text, max_chars=max_chars))


def _disp_w(s: str) -> int:
    """估算显示宽度:全角(F/W)算 2,半角算 1。"""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(c) in ("F", "W") else 1 for c in s)


def _join(lines: list[str]) -> str:
    """把折行结果连成 ASS 文本;多行时短行尾部补全角空格补齐到等宽,
    使事件整体居中时各行左端对齐(第二行视觉上左对齐而非居中)。"""
    if len(lines) > 1:
        w = max(_disp_w(ln) for ln in lines)
        lines = [ln + "　" * ((w - _disp_w(ln) + 1) // 2) for ln in lines]
    return "\\N".join(lines)


def _split_events(
    text: str, start: float, end: float, *, max_chars: int
) -> list[tuple[float, float, str]]:
    """超长文本按每次最多 MAX_LINES 行切分,时长按字数比例分配。"""
    lines = wrap_lines(text, max_chars=max_chars)
    if len(lines) <= MAX_LINES:
        return [(start, end, _join(lines))]
    chunks = [_join(lines[i : i + MAX_LINES]) for i in range(0, len(lines), MAX_LINES)]
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
        text = _DOT_RUN.sub(lambda m: "......" if m.group(0)[0] == "." else "……", text)
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
