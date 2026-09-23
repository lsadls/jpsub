"""screen 布局:全屏打字机文本的段落合并。

与 subtitle 布局不同:这里的文本逐字增长,靠"扩展关系"判断同段,
靠"非扩展"(清屏或换屏)判断段落边界。
"""
from __future__ import annotations

from difflib import SequenceMatcher

from .segment import Segment, normalize


def is_extension(prev: str, cur: str, *, min_ratio: float = 0.6) -> bool:
    """cur 是否是在 prev 基础上继续打字(prev 是 cur 的前缀,容忍 OCR 抖动)。"""
    p, c = normalize(prev), normalize(cur)
    if not p or not c:
        return False
    if p == c:
        return True
    if len(c) < len(p) * 0.9:          # 明显变短 -> 不是同段的继续
        return False
    if c.startswith(p):                # 严格前缀
        return True
    # 容错:比较前缀部分,允许 1~2 字 OCR 抖动
    return SequenceMatcher(None, p, c[: len(p)]).ratio() >= min_ratio


def build_screen_segments(
    timed_texts: list[tuple[float, str]],
    *,
    frame_dur: float,
    min_ratio: float = 0.6,
    clear_frames: int = 2,
) -> list[Segment]:
    """把逐帧 OCR 文本合并成全屏段落。

    - 新文本是当前段的"扩展" -> 同段,保留更长版本;
    - 否则 -> 结束当前段(end=该帧时间),开启新段;
    - 连续 clear_frames 帧为空 -> 视为清屏,结束当前段。
    """
    segments: list[Segment] = []
    cur_start: float | None = None
    cur_text = ""
    last_t = 0.0
    blanks = 0

    for t, raw in timed_texts:
        text = normalize(raw)
        if not text:
            blanks += 1
            if cur_start is not None and blanks >= clear_frames:
                segments.append(Segment(cur_start, last_t + frame_dur, cur_text))
                cur_start, cur_text = None, ""
            continue
        blanks = 0
        if cur_start is None:
            cur_start, cur_text = t, text
        elif is_extension(cur_text, text, min_ratio=min_ratio):
            if len(text) >= len(cur_text):
                cur_text = text
        else:
            segments.append(Segment(cur_start, t, cur_text))
            cur_start, cur_text = t, text
        last_t = t

    if cur_start is not None:
        segments.append(Segment(cur_start, last_t + frame_dur, cur_text))
    return segments
