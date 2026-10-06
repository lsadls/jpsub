"""画面敏感文字自动打码:独立词表 + 复用已有 OCR 原文 + 定位到像素框。

流程(打码总在 OCR 之后,不额外做全量逐帧 OCR):
1. 读 <工作目录>/.jpsub/segments.json 的画面原文(text),按词表做子串匹配,得到命中段;
2. 对每个命中段取代表帧(段中点)定位敏感词像素框:
   - 含位置版 OCR(accurate/general):按行框与行内字符序号比例切出整词框;
   - 降级(含位置版额度/可用性用尽,或 --sensitive-locate algo):算法在帧内检测文字行,
     把该段原文按行宽容量对应到检测到的行,再按字符序号比例切框;
3. 命中段的打码区间 = 段的 start/end 各外扩 pad 秒(--sensitive-pad,默认 0.5);
   相邻段中位置重合且同一批词的框合并为一条;
4. 结果写 <工作目录>/.jpsub/masks.sensitive.json,由既有 `jpsub maskapply` 应用。

手工 masks.json 与其它文件不受影响。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from . import handoff
from .mask import (
    MaskEntry,
    frame_jpeg,
    save_masks,
    video_duration,
    video_size,
)

# 内置默认敏感词表(多字词;单字通用词如「死」「堕」「雌」误盖风险高,默认不启用)
DEFAULT_WORDS: tuple[str, ...] = (
    "性的虐待",
    "虐待",
    "調教",
    "肛門",
    "肛問",
    "ケツの穴",
    "ケツマンコ",
    "ホモ",
    "雌堕ち",
    "雄堕ち",
    "奴隷",
    "エロ",
    "雄子宮",
    "雄膣",
    "淫獣",
    "デカマラ",
    "性処理",
    "性欲",
    "露出",
    "股間",
    "媚薬",
    "全裸",
    "排泄",
    "自殺",
)

EFFECT = "color:000000"  # 遮挡效果口径与手工基线一致
VOL = 1.0
MERGE_GAP = 1.0  # 相邻段同位置同词的框间隔不超过该秒数时合并
LINE_ROW_FRAC = 0.02  # 一行至少占整帧宽度该比例的文字像素才算文字行
LINE_MIN_H = 8  # 文字行最小高度(px)
LINE_MAX_FRAC = 0.09  # 文字行最大高度(占帧高):更高的多为画面/图案,不是字幕行
FPS = 1.0  # 兜底逐帧 OCR 的抽帧率(仅 segments.json 缺失时使用)


def masks_path(work: Path) -> Path:
    """自动敏感打码结果文件路径(与手工 masks.json 区分)。"""
    return handoff.hidden_dir(work) / "masks.sensitive.json"


def words_file(work: Path) -> Path:
    """默认词表文件:工作目录下的 sensitive.txt(存在即替换内置默认词表)。"""
    return work / "sensitive.txt"


def load_words(work: Path, path: Path | None = None) -> list[str]:
    """读敏感词表:--sensitive-words > 工作目录 sensitive.txt > 程序目录
    sensitive.txt > 内置默认词表。文件每行一词,`#` 起始为注释,空行忽略。"""
    cands: list[Path] = []
    if path:
        cands.append(Path(path))
    cands += [words_file(work), Path(__file__).resolve().parent.parent / "sensitive.txt"]
    for cand in cands:
        if not cand.is_file():
            continue
        words = [
            line.strip()
            for line in cand.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if words:
            return words
    return list(DEFAULT_WORDS)


def find_words(text: str, words: list[str]) -> list[tuple[int, str]]:
    """返回 text 中命中的 [(起始字符序号, 词)],按位置排序。

    精确子串匹配;同一位置只保留最长的词,被更长命中覆盖的不再单列。"""
    if not text:
        return []
    best: dict[int, str] = {}
    for w in words:
        i = text.find(w)
        while i >= 0:
            cur = best.get(i)
            if cur is None or len(w) > len(cur):
                best[i] = w
            i = text.find(w, i + 1)
    spans = sorted(best.items(), key=lambda kv: (kv[0], -len(kv[1])))
    kept: list[tuple[int, str]] = []
    for start, word in spans:
        if any(s <= start < s + len(w) for s, w in kept):
            continue  # 已被更早开始的命中覆盖
        kept.append((start, word))
    return kept


def _split_box(box: tuple[int, int, int, int], text: str, start: int, length: int):
    """在行框内按字符序号比例切出 [start, start+length) 的框。"""
    x, y, w, h = box
    n = max(1, len(text))
    x0 = x + round(w * start / n)
    x1 = x + round(w * (start + length) / n)
    return (x0, y, max(3, x1 - x0), max(3, h))


def _word_boxes(pairs: list[tuple[str, tuple[int, int, int, int]]], words: list[str]):
    """[(行文本, 行框)] → 命中词的整词框列表。"""
    out: list[tuple[int, int, int, int]] = []
    for text, box in pairs:
        for start, word in find_words(text, words):
            out.append(_split_box(box, text, start, len(word)))
    return out


def _line_boxes(image) -> list[tuple[int, int, int, int]]:
    """算法检测帧内文字行:复用 frames 的文字掩膜(局部对比+亮度门槛)做水平投影分组,
    返回按 y 排序的 (x,y,w,h)。只留高度像字幕行的带,过高的多为图案/色块。"""
    try:
        import numpy as np
    except ImportError:
        return []
    from . import frames as frames_mod

    m = np.asarray(frames_mod._binary_mask(image), dtype=np.uint8)
    if m.size == 0:
        return []
    h, w = m.shape
    rows = (m > 0).sum(axis=1)
    thr = max(3, int(w * LINE_ROW_FRAC))
    bands: list[tuple[int, int]] = []
    y = 0
    while y < h:
        if rows[y] < thr:
            y += 1
            continue
        y0 = y
        gap = 0
        while y < h and (rows[y] >= thr or gap < 2):
            gap = 0 if rows[y] >= thr else gap + 1
            y += 1
        y1 = y - gap
        if LINE_MIN_H <= y1 - y0:
            bands.append((y0, y1))
    out: list[tuple[int, int, int, int]] = []
    max_h = max(LINE_MIN_H, round(LINE_MAX_FRAC * h))
    for y0, y1 in bands:
        if y1 - y0 > max_h:
            continue  # 过高的带多为图案/色块,不是字幕行
        cols = (m[y0:y1] > 0).sum(axis=0)
        xs = np.nonzero(cols > 0)[0]
        if len(xs) < 4:
            continue
        x0, x1 = int(xs[0]), int(xs[-1]) + 1
        if x1 - x0 < LINE_MIN_H:
            continue
        out.append((x0, y0, x1 - x0, y1 - y0))
    return out


def _algo_pairs(boxes: list[tuple[int, int, int, int]], text: str):
    """算法降级:把该段 OCR 原文的每一行,按「能装下这一行」的行框就近对应。

    行框容量按「行宽 ÷ (0.9×行高)」估算(日文方块字宽约为行高的 0.9 倍);每个
    行框只配一行,取容量刚好装得下该行(且不超过 1.6 倍)的最小行框;没有合适的
    行框就跳过该行——宁可漏框,不误盖。"""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()] or [text or ""]
    used: set[int] = set()
    pairs: list[tuple[str, tuple[int, int, int, int]]] = []
    for line in lines:
        n = len("".join(line.split()))
        if not n:
            continue
        best, best_cap = -1, 0
        for i, box in enumerate(boxes):
            if i in used:
                continue
            cap = round(box[2] / (0.9 * max(1, box[3])))
            if cap < n or cap > 1.6 * n:
                continue  # 装不下或多装太多:不是这一行的行框
            if best < 0 or cap < best_cap:
                best, best_cap = i, cap
        if best < 0:
            continue
        used.add(best)
        pairs.append((line, boxes[best]))
    return pairs


def _iou(a: MaskEntry, b: MaskEntry) -> float:
    x0, y0 = max(a.x, b.x), max(a.y, b.y)
    x1 = min(a.x + a.w, b.x + b.w)
    y1 = min(a.y + a.h, b.y + b.h)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union else 0.0


def _merge(entries: list[MaskEntry]) -> list[MaskEntry]:
    """相邻段中位置重合(交并比 >= 0.5)且时间间隔不超过 MERGE_GAP 的框合并为一条。"""
    out: list[MaskEntry] = []
    for m in sorted(entries, key=lambda e: e.start):
        for i, o in enumerate(out):
            gap = max(m.start - o.end, o.start - m.end)
            if gap <= MERGE_GAP and _iou(o, m) >= 0.5:
                out[i] = MaskEntry(
                    min(o.start, m.start),
                    max(o.end, m.end),
                    o.x,
                    o.y,
                    o.w,
                    o.h,
                    o.effect,
                    o.vol,
                )
                break
        else:
            out.append(m)
    return sorted(out, key=lambda e: e.start)


def _segments_from_frames(video: Path, work: Path, engine, quiet: bool) -> list:
    """segments.json 缺失时的兜底:抽全屏帧逐帧 OCR 取画面原文(消耗 OCR 额度)。"""
    from . import frames as frames_mod
    from .segment import Segment

    if not quiet:
        print("[sensitive] 未找到 segments.json,回退到逐帧 OCR 取文本(消耗 OCR 额度)")
    tmp = handoff.hidden_dir(work) / "sensitive_frames"
    try:
        paths = frames_mod.extract_frames(video, tmp, fps=FPS, crop="1", quiet=quiet)
        texts = engine.run_many(paths, lines=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return [Segment(i / FPS, (i + 1) / FPS, (t or "").strip(), "") for i, t in enumerate(texts)]


def _sample_times(seg) -> list[float]:
    """段内采样时刻:优先段末(静止段的关键帧在该处,OCR 原文与画面最一致),
    再取段中点与段首。逐个采样、一旦定位到词即停,命中时只消耗一次含位置版请求。"""
    out: list[float] = []
    for t in (seg.end - 0.2, (seg.start + seg.end) / 2, seg.start + 0.2):
        t = max(0.0, t)
        if not any(abs(t - o) < 0.2 for o in out):
            out.append(t)
    return out


def detect(
    video: Path,
    work: Path | None = None,
    *,
    words_file_path: Path | None = None,
    pad: float = 0.5,
    locate: str = "auto",
    quiet: bool = False,
) -> Path:
    """自动检测画面敏感汉字并写出掩膜文件,返回结果路径。

    locate: auto=含位置版优先、额度/可用性用尽降级算法;ocr=只用含位置版;
    algo=只用算法定位(不调用含位置版接口)。
    """
    from . import ocr
    from .cli import _work_of

    video = Path(video)
    if not video.is_file():
        raise SystemExit(f"错误:视频不存在:{video}")
    work = work or _work_of(video)
    engine = None
    seg_file = handoff.seg_path(work)
    if seg_file.is_file():
        segments = handoff.read_segments(seg_file)  # 复用已有原文,不再消耗 OCR 额度
    else:
        engine = ocr.make_engine()
        segments = _segments_from_frames(video, work, engine, quiet)
    words = load_words(work, words_file_path)
    hits = [
        (s, find_words(s.text or "", words))
        for s in segments
        if s.text and find_words(s.text or "", words)
    ]
    if not hits:
        raise SystemExit(f"错误:没有段命中敏感词表({len(words)} 个词),未生成掩膜。")

    if locate == "algo":
        use_pos = False
    else:
        engine = engine or ocr.make_engine()
        use_pos = isinstance(engine, ocr.BaiduOcrEngine)
    if locate == "ocr" and not use_pos:
        raise SystemExit("错误:--sensitive-locate ocr 需要百度 OCR 引擎(含位置版接口)。")

    vw, vh = video_size(video)
    duration = video_duration(video)
    entries: list[MaskEntry] = []
    if not quiet:
        print(f"敏感词表 {len(words)} 个词,命中 {len(hits)}/{len(segments)} 段")
    with tempfile.TemporaryDirectory(prefix="jpsub_sensitive_") as tmpdir:
        frame = Path(tmpdir) / "frame.jpg"
        ranked: dict[float, list] = {}  # 采样时刻 -> 含位置版行结果(同刻只识别一次)
        for n, (seg, _seg_hits) in enumerate(hits, 1):
            boxes: list[tuple[int, int, int, int]] = []
            times = _sample_times(seg)
            if use_pos:
                for t in times:
                    if t not in ranked:
                        frame.write_bytes(frame_jpeg(video, t))
                        ranked[t] = engine.run_lines_pos(frame)  # None 表示含位置版不可用
                    rows = ranked[t]
                    if rows is None:
                        if locate == "ocr":
                            raise SystemExit(
                                "错误:含位置版接口不可用(额度用尽),"
                                "无法满足 --sensitive-locate ocr。"
                            )
                        use_pos = False
                        if not quiet:
                            print("[sensitive] 含位置版不可用,降级为算法定位")
                        break
                    boxes = _word_boxes(rows, words)  # 以画面实际识别文本为准
                    if boxes:
                        break  # 已定位到词,不再多花额度
            if not use_pos:
                from PIL import Image

                for t in times:  # 算法定位不占额度,多采几帧提高覆盖
                    frame.write_bytes(frame_jpeg(video, t))
                    with Image.open(frame) as im:
                        pairs = _algo_pairs(_line_boxes(im), seg.text or "")
                    boxes += _word_boxes(pairs, words)
            for x, y, w, h in boxes:
                x = max(0, min(x, vw - 3))
                y = max(0, min(y, vh - 3))
                w = max(3, min(w, vw - x))
                h = max(3, min(h, vh - y))
                start = max(0.0, seg.start - pad)
                end = min(duration, seg.end + pad)
                if end <= start:
                    continue
                entries.append(MaskEntry(start, end, x, y, w, h, EFFECT, VOL))
            if not quiet:
                print(f"[sensitive] {n}/{len(hits)} 段 {seg.start:g}~{seg.end:g}s 框 {len(boxes)}")

    merged = _merge(entries)
    if not merged:
        raise SystemExit("错误:未能定位到任何敏感词框,未生成掩膜。")
    out = masks_path(work)
    save_masks(merged, out)
    print(
        f"完成:敏感汉字打码 {len(merged)} 条 -> {out}"
        + (f"(本次 OCR 请求 {ocr.REQUESTS} 次)" if ocr.REQUESTS else "")
    )
    print(f"应用:{'jpsub maskapply'} {video} {out}")
    return out
