"""subtitle 布局时间段:文本归一化、相似度、连续帧合并。

本模块还提供两种布局共用的 normalize/similar/Segment。
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass
class Segment:
    start: float
    end: float
    text: str


def normalize(text: str) -> str:
    """NFKC 归一化(半角转全角)并去掉所有空白(含换行)。"""
    text = unicodedata.normalize("NFKC", text)
    return "".join(text.split())


def similar(a: str, b: str, threshold: float = 0.85) -> bool:
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= threshold


def _typing_partial(short: str, long: str) -> bool:
    """short 是否为 long 打字中途的残缺帧(允许少量 OCR 误差)。

    - 逐字前缀直接判定;
    - 极短文本(<=2 字)首字相同即视为残缺(如「あつ」vs「あっ本当だゾ」);
    - 其余比较 short 与 long 的等长前缀,相似度 >=0.7 即判定
      (容忍 OCR 误识别,如「暑いヅ。確」vs「暑い。確実に…」)。
    """
    if not short or len(short) >= len(long):
        return False
    if long.startswith(short):
        return True
    if len(short) <= 2:
        return short[0] == long[0]
    return SequenceMatcher(None, short, long[: len(short)]).ratio() >= 0.7


def merge_partial_duplicates(
    segments: list[Segment], *, frame_dur: float
) -> list[Segment]:
    """时间相邻的段里,残缺文本并入完整文本(保留长者,时间取并集)。"""
    out: list[Segment] = []
    for seg in segments:
        if out and seg.start <= out[-1].end + frame_dur * 1.5:
            cur = out[-1]
            if _typing_partial(cur.text, seg.text):
                cur.end, cur.text = seg.end, seg.text
                continue
            if _typing_partial(seg.text, cur.text):
                cur.end = max(cur.end, seg.end)
                continue
        out.append(seg)
    return out


def build_segments(
    timed_texts: list[tuple[float, str]],
    *,
    frame_dur: float,
    threshold: float = 0.85,
) -> list[Segment]:
    """把 (帧时间, OCR 文本) 序列合并成字幕段(subtitle 布局)。

    - 空文本(字幕消失/OCR 失败)不延伸 end;
    - 相邻文本相似且时间连续则合并,end 延到该帧 + frame_dur;
    - 合并时保留更长文本,抗 OCR 半途截断。
    """
    segments: list[Segment] = []
    for t, raw in timed_texts:
        text = normalize(raw)
        if not text:
            continue
        if segments:
            cur = segments[-1]
            gap_ok = t <= cur.end + frame_dur * 1.5
            # 相似,或新文本是当前段的逐字扩展(打字中途被识别过一次)
            if gap_ok and (similar(cur.text, text, threshold) or text.startswith(cur.text)):
                cur.end = t + frame_dur
                if len(text) > len(cur.text):
                    cur.text = text
                continue
        segments.append(Segment(start=t, end=t + frame_dur, text=text))
    return merge_partial_duplicates(segments, frame_dur=frame_dur)
