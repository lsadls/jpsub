"""OCR 触发策略:只为"关键帧"跑 OCR,把次数从每帧降到每段约 1 次。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from .frames import mask_diff


def frame_diffs(paths: list[Path], diff_fn) -> list[float]:
    """相邻帧差异序列;首帧记为 +inf(视为一段的开始)。"""
    out = [float("inf")]
    for a, b in zip(paths, paths[1:]):
        out.append(diff_fn(a, b))
    return out


def mask_diffs(masks: list[Image.Image]) -> list[float]:
    """相邻掩膜差异序列;首帧 +inf。

    等价于 `frame_diffs(paths, frames.frame_diff)`,但掩膜每帧只算一次
    (`frame_diff` 会把中间帧的掩膜重复算两遍)。
    """
    out = [float("inf")]
    for a, b in zip(masks, masks[1:]):
        out.append(mask_diff(a, b))
    return out


def _removed_stats(prev: Image.Image, cur: Image.Image) -> tuple[float, float]:
    """(相对比例, 绝对均值):cur 相对 prev 消失的笔画量。

    相对比例 = 消失像素 / prev 笔画总量(0-1);prev 掩膜几乎为空时相对比例
    记为 0,不触发换段判定。
    """
    base = ImageStat.Stat(prev).mean[0]
    if base < 0.5:
        return 0.0, 0.0
    removed = ImageStat.Stat(ImageChops.subtract(prev, cur)).mean[0]
    return removed / base, removed


def select_keyframes(
    diffs: list[float],
    *,
    change_threshold: float,
    settle_frames: int = 2,
    max_run: int = 10,
    merge_short_pauses: bool = False,
    append_limit: float | None = None,
    masks: list[Image.Image] | None = None,
) -> list[tuple[int, int, int]]:
    """返回 [(start_idx, end_idx, key_idx), ...]。

    - start_idx: 段开始变化的帧;
    - end_idx:   段结束帧(下一段 start 之前);
    - key_idx:   用来 OCR 的帧——取静止区的**末帧**,即下一次变化(继续打字 /
                 清屏)之前的最后一帧,此时该段文字最完整。

    短于 `settle_frames` 的停顿不算段落结束,并入同一段,避免把打字中途的
    小停顿切成多段、且丢掉段末的完整帧。

    `merge_short_pauses=True` 时更进一步:短停顿后继续打字也不收尾,整段只
    保留最后一次静止的末帧(用于"每句话只留一帧"的筛选预览)。但候选帧之后
    的变化若差分 >= `append_limit`(默认 change_threshold*4),说明旧文字被
    大面积替换——是**新段落**而非续写,仍在候选帧收尾,否则夹在两个长段落
    之间的小段落会被吞掉。
    """
    n = len(diffs)
    changed = [d >= change_threshold for d in diffs]
    spans: list[tuple[int, int, int]] = []
    start: int | None = None      # 当前段的起始帧
    run = 0                       # 当前连续变化帧数(max_run 兜底用)
    cand: int | None = None       # 段内最近静止区末帧(候选关键帧)
    i = 0
    while i < n:
        if changed[i]:
            if start is not None and masks is not None:
                # 连续变化中的换段检测:前一帧的笔画大量消失(相对>=50% 且
                # 绝对量可观,排除抽帧抖动)说明旧文字被替换——小段落一闪
                # 而过、没有静止区也会在此断开,否则会被当成连续打字吞掉。
                rel, abs_removed = _removed_stats(masks[i - 1], masks[i])
                if rel >= 0.5 and abs_removed >= 1.5:
                    key = cand if cand is not None else i - 1
                    spans.append((start, key, key))
                    start, cand, run = i, None, 0
                    i += 1
                    continue
            if cand is not None:
                if merge_short_pauses and masks is None:
                    # 无掩膜时的退化判定:差分小于阈值视为续写,不收尾
                    limit = append_limit if append_limit is not None else change_threshold * 4
                    if diffs[i] < limit:
                        cand, run = None, 0
                        i += 1
                        continue
                # 候选之后又出现变化:说明候选帧是该段"最后一次完整显示",
                # 在此收尾;后续变化(继续打字/清屏)归下一段。
                spans.append((start, cand, cand))
                start, cand, run = i, None, 0
            elif start is None:
                start = i
            run += 1
            if run >= max_run:    # 兜底:长时间持续变化,先取当前帧防漏
                spans.append((start, i, i))
                start, cand, run = None, None, 0
            i += 1
            continue
        # 静止帧:量出这段静止区的长度
        j = i
        while j < n and not changed[j]:
            j += 1
        if start is not None:
            if j - i >= settle_frames:
                # 静止足够长:段落在此结束,关键帧取静止区末帧(最完整)
                spans.append((start, j - 1, j - 1))
                start, cand, run = None, None, 0
            else:
                cand = j - 1      # 停顿过短:暂记候选,等待后续变化定夺
        i = j
    if start is not None:         # 视频结尾:取候选或末帧兜底
        spans.append((start, n - 1, cand if cand is not None else n - 1))
    # 段边界:end 延续到下一段 start 之前
    for s in range(len(spans)):
        nxt = spans[s + 1][0] if s + 1 < len(spans) else n
        spans[s] = (spans[s][0], nxt - 1, spans[s][2])
    if merge_short_pauses and masks is not None:
        # 事后合并:前段关键帧的文字若仍(近乎)完整出现在后段关键帧中
        # (消失笔画 < 20%),说明前段只是后段的"打字过程",并入后段;
        # 否则(旧字消失=换段)一律保留。
        merged: list[tuple[int, int, int]] = []
        for s in spans:
            prev_key = masks[merged[-1][2]] if merged else None
            if (
                prev_key is not None
                and ImageStat.Stat(prev_key).mean[0] >= 0.5  # 空掩膜(纯色帧)不参与合并
                and _removed_stats(prev_key, masks[s[2]])[0] < 0.2
            ):
                merged[-1] = (merged[-1][0], s[1], s[2])
            else:
                merged.append(s)
        spans = merged
    return spans
