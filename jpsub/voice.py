"""朗读视频的时间轴生成:按音量变化切出语音段落。

逐帧取 RMS 音量,聚合成小窗口;自适应阈值(默认 Otsu)区分语音与底噪/BGM,
静音持续 >= min_silence 才算段落间隔,段两侧留 pad 缓冲。产出与 OCR 流水线
一致的 segments.json(text 留空,由用户在 edit 里填原文)。程序零网络。
"""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

from .segment import Segment
from .settings import binary

_WINDOW = 0.25  # 音量统计窗口秒数


def _rms_stream(video: Path) -> list[tuple[float, float]]:
    """逐帧输出 (时间, RMS dB)。用 astats+ametadata 打到 stdout 再解析。

    不用 -ss/-t 做裁剪:-ss 在 -i 前会重置 pts(有无 -t 行为还不同),
    时间轴不可靠;全片解码很快,区间过滤交给调用方在 Python 里做。
    """
    cmd = [
        binary("ffmpeg"), "-hide_banner", "-nostats",
        "-i", str(video),
        "-af",
        "astats=metadata=1:reset=1,"
        "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    pts: float | None = None
    out: list[tuple[float, float]] = []
    for ln in proc.stdout.splitlines():
        m = re.match(r"frame:\S+ pts:\S+ pts_time:(\d+(?:\.\d+)?)", ln)
        if m:
            pts = float(m.group(1))
            continue
        m = re.match(r"lavfi\.astats\.Overall\.RMS_level=(-?\d+(?:\.\d+)?|-inf)", ln)
        if m and pts is not None:
            v = m.group(1)
            out.append((pts, float(v) if v != "-inf" else -90.0))
            pts = None
    if not out:
        raise SystemExit("错误:没能从音频取到音量数据(astats 无输出),检查视频音轨")
    return out


def _auto_threshold(db_vals: list[float]) -> float:
    """自适应阈值:窗口音量按 dB 做 Otsu 二类分割(语音 vs 底噪/BGM),
    钳制到 [p05+2, p95-2];响度跨度大时再以 p95-10dB 保底,
    确保阈值高于 BGM 电平,语句间的 BGM 停顿也能切开。"""
    xs = sorted(db_vals)
    n = len(xs)

    def pct(p: float) -> float:
        return xs[min(n - 1, int(n * p))]

    p50, p95 = pct(0.50), pct(0.95)
    lo, hi = pct(0.05) + 2.0, p95 - 2.0
    if lo >= hi:  # 音量分布过窄:全程等响或全程静音,无法按音量分段
        raise SystemExit("错误:音量分布过窄,无法区分语音与静音;调 --noise 手动指定阈值")
    # Otsu:64 桶直方图上最大化类间方差
    bins = 64
    g0, g1 = min(xs), max(xs)
    width = (g1 - g0) / bins or 1.0
    hist = [0] * bins
    for v in xs:
        hist[min(bins - 1, int((v - g0) / width))] += 1
    total, wsum = float(n), sum(v * (g0 + (i + 0.5) * width) for i, v in enumerate(hist))
    wb, sb, wbs = 0.0, 0.0, 0.0
    best, best_t = -1.0, (lo + hi) / 2
    for i, c in enumerate(hist):
        wb += c
        if wb == 0:
            continue
        wf = total - wb
        if wf == 0:
            break
        sb += c * (g0 + (i + 0.5) * width)
        mb, mf = sb / wb, (wsum - sb) / wf
        between = wb * wf * (mb - mf) ** 2
        if between > best:
            best, best_t = between, g0 + (i + 1) * width
    th = min(max(best_t, lo), hi)
    if p95 - p50 > 4.0:  # 响度跨度大(有 BGM 垫底):阈值至少压到语音峰值下 10dB
        th = max(th, p95 - 10.0)
    return th


def detect_speech_segments(
    video: Path,
    *,
    noise: str | None = None,
    min_silence: float = 0.6,
    pad: float = 0.2,
    hyst: float = 10.0,
    start: float | None = None,
    end: float | None = None,
) -> list[Segment]:
    """返回语音段 [(start, end, "")]。

    noise: None=自适应阈值;否则固定阈值(如 '-30dB')。
    min_silence: 音量低于阈值持续这么久才算段落间隔;pad: 段两侧缓冲秒数。
    hyst: 滞回带宽 dB——进入语音用高阈值 th,退到 th-hyst 以下才算静音开始。
        朗读音量起伏(软音节)常低于全局阈值十几个 dB,单阈值会把一句话切碎;
        滞回让语音一旦确立就不因短暂变轻而丢失,只对持续深跌的真静音切分。
    start/end: 只处理该区间。
    """
    frames = _rms_stream(video)
    if start is not None:  # 区间过滤:留 0.5s 余量给窗口聚合
        frames = [(t, db) for t, db in frames if t >= start - 0.5]
    if end is not None:
        frames = [(t, db) for t, db in frames if t <= end]
    t0 = start if start is not None else frames[0][0]  # 音轨可能晚于视频开头(如封面段)
    if end is None:
        end = frames[-1][0] + _WINDOW  # 最后一帧时间即音频末尾
    hi = end
    # 聚合到固定窗口:窗口内取线性功率平均,更接近听感
    by_win: dict[int, list[float]] = {}
    for t, db in frames:
        by_win.setdefault(int(t / _WINDOW), []).append(10 ** (db / 10))
    win_db = [
        (i * _WINDOW, 10 * math.log10(sum(v) / len(v)))
        for i, v in sorted(by_win.items())
    ]
    vals = [db for _, db in win_db]
    th = float(noise.lower().replace("db", "")) if noise else _auto_threshold(vals)
    th_lo = th - hyst
    print(f"音量阈值:{th:.1f} dB(滞回下限 {th_lo:.1f},{len(win_db)} 窗口 × {_WINDOW}s)")
    # 滞回状态机:db>=th 进入语音;语音期间跌到 th_lo 以下才开始计静音,
    # 静音持续 >= min_silence 才切分;[th_lo, th) 之间维持原状态
    sil_start: float | None = None
    cuts: list[tuple[float, float]] = []  # 静音区间 (起, 止)
    for t, db in win_db:
        if db >= th:
            if sil_start is not None:
                if t - sil_start >= min_silence:
                    cuts.append((sil_start, t))
                sil_start = None
        elif db < th_lo and sil_start is None:
            sil_start = t
    if sil_start is not None:  # 结尾静音
        cuts.append((sil_start, hi))
    # 语音 = [t0, hi] 减去静音区间
    segs: list[Segment] = []
    cur = t0
    for s, e in cuts:
        if s > cur:
            segs.append(Segment(start=cur, end=min(s, hi), text=""))
        cur = max(cur, e)
        if cur >= hi:
            break
    if cur < hi:
        segs.append(Segment(start=cur, end=hi, text=""))
    # pad 缓冲 + 相邻合并 + 过窄丢弃
    out: list[Segment] = []
    for seg in segs:
        s, e = seg.start - pad, seg.end + pad
        if e - s < 0.3:
            continue
        if out and s <= out[-1].end:
            out[-1].end = e
        else:
            out.append(Segment(start=max(s, t0), end=min(e, hi), text=""))
    return out

