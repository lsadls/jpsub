"""segments.json 中心数据层:段落读写、原始备份、旧 in/out 迁移。程序不发任何网络请求。

GUI 化后不再使用 translate-in/out.txt 简易格式,原文与译文统一存在 segments.json
(Segment.text / Segment.tr);OCR 完成后另存一份原始备份 segments.orig.json,
供译文调整器一键还原改动。
"""
from __future__ import annotations

import json
from pathlib import Path

from .cache import TranslationCache
from .segment import Segment

# 未译占位标记:AI 未返回译文(内容审查拦截、单批失败等)时,tr 记为该值,
# 不进缓存、不参与渲染,可在译文调整器里一眼看出并重翻。
UNTRANSLATED_MARK = "[[未译]]"

# OCR 产物原始备份(仅 text,无 tr):译文调整器「还原改动」的数据源
ORIG_NAME = "segments.orig.json"


def is_untranslated(text: str) -> bool:
    """判断一段译文是否为未译占位标记(或为空)。"""
    return not text or text.strip() == UNTRANSLATED_MARK


def fmt_secs(v: float) -> str:
    """秒 -> 军方时间 'MMSS'(0.1s 精度,分秒各补足两位,如 58s -> '0058');
    有小时则前面再加 HH,如 3680s -> '010120'。"""
    v = round(v, 1)
    h, rem = divmod(v, 3600)
    m, s = divmod(rem, 60)
    sstr = f"{int(s):02d}" if s == int(s) else f"{s:04.1f}"
    if h:
        return f"{int(h):02d}{int(m):02d}{sstr}"
    return f"{int(m):02d}{sstr}"


def make_key(start: float, end: float) -> str:
    """段落时间轴键:'1120-1145'(军方时间,便于人工编辑)。"""
    return f"{fmt_secs(start)}-{fmt_secs(end)}"


def _parse_secs(t: str) -> float | None:
    """解析单个时间:'1120'(MMSS)、'010120'(HHMMSS)、'58'(纯秒,兼容)、
    兼容带冒号的 '11:20' / '1:01:20'。"""
    t = t.strip()
    if ":" in t:
        parts = t.split(":")
        if len(parts) > 3:
            return None
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        while len(nums) < 3:
            nums.insert(0, 0.0)
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    ip, _, frac = t.partition(".")
    if not ip.isdigit() or (frac and not frac.isdigit()):
        return None
    if len(ip) <= 2:  # 纯秒数
        return float(t)
    if len(ip) > 6:
        return None
    s = ip[-2:] + (f".{frac}" if frac else "")
    rest = ip[:-2]
    if len(rest) <= 2:
        h, m = 0, int(rest)
    else:
        h, m = int(rest[:-2]), int(rest[-2:])
    return h * 3600 + m * 60 + float(s)


def parse_key(key: str) -> tuple[float, float] | None:
    """解析时间轴键为 (起秒, 止秒),兼容军方时间与带冒号写法。"""
    a, sep, b = key.partition("-")
    if not sep:
        return None
    x, y = _parse_secs(a), _parse_secs(b)
    return (x, y) if x is not None and y is not None else None


def norm_key(key: str) -> str:
    """任意格式的键统一为军方时间形式(旧秒数键自动迁移);无法解析则原样返回。"""
    r = parse_key(key)
    return make_key(*r) if r else key


def write_segments(
    segments: list[Segment],
    path: Path,
    *,
    comment: str | None = None,
) -> None:
    """segments 与翻译元信息(comment)合写进同一个 JSON。

    结构:{"segments": [...], "comment": ...},comment 是视频描述。
    每段带 tr(译文)快照,重跑时免重翻。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "segments": [
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                **({"tr": s.tr} if s.tr else {}),
            }
            for s in segments
        ],
        "comment": comment,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def read_segments(path: Path) -> list[Segment]:
    data = json.loads(path.read_text(encoding="utf-8"))
    # 旧格式:顶层就是段落列表;新格式:包在 "segments" 键下
    items = data if isinstance(data, list) else data.get("segments", [])
    return [Segment(d["start"], d["end"], d["text"], tr=d.get("tr")) for d in items]


def read_meta(path: Path) -> dict:
    """读 segments.json 里的翻译元信息(comment)。旧格式/文件缺失返回空。"""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {}
    return {"comment": data.get("comment")}


def pending_texts(segments: list[Segment], cache: TranslationCache) -> list[str]:
    """全局去重、且还没有译文的原文(段上无 tr 且缓存未命中),保持首次出现顺序。"""
    seen: list[str] = []
    for s in segments:
        if not s.text:
            continue
        if s.tr and not is_untranslated(s.tr):
            continue  # 段上已有有效译文
        if s.text not in seen and cache.get(s.text) is None:
            seen.append(s.text)
    return seen


def resolve(segments: list[Segment], cache: TranslationCache) -> dict[str, str]:
    """段文本 -> 译文。优先段上快照的 tr(用户在调整器里编辑后最新),
    其次翻译缓存;缺译文的不返回(渲染时该段不显示,不用原文填补)。"""
    out: dict[str, str] = {}
    for s in segments:
        if not s.text:
            continue
        for cand in (s.tr, cache.get(s.text)):
            if cand and not is_untranslated(cand):
                out[s.text] = cand
                break
    return out


def import_out_to_segments(work: Path, cache: TranslationCache) -> int:
    """旧目录一次性迁移:把 translate-out.txt 的译文导入 segments 的 tr 与缓存。

    只给「当前无有效译文」的段补 tr,不会覆盖用户在调整器里的新改动;
    迁移后 out 文件保留但不再使用。返回导入条数。
    """
    out_path = work / "translate-out.txt"
    seg_path = work / "segments.json"
    if not out_path.exists() or not seg_path.exists():
        return 0
    segs = read_segments(seg_path)
    by_key: dict[str, Segment] = {}
    for s in segs:
        by_key.setdefault(norm_key(make_key(s.start, s.end)), s)
    n = 0
    for ln in out_path.read_text(encoding="utf-8").splitlines():
        if "\t" not in ln:
            continue
        k, _, t = ln.partition("\t")
        k, t = norm_key(k.strip()), t.strip()
        if is_untranslated(t):
            continue
        s = by_key.get(k)
        if s is None:
            continue
        if is_untranslated(s.tr or ""):  # 只补缺,不覆盖
            s.tr = t
            n += 1
        if s.text and cache.get(s.text) is None:
            cache.put(s.text, t)
    if n:
        write_segments(segs, seg_path, comment=read_meta(seg_path).get("comment"))
        cache.save()
    return n


def save_orig(segments: list[Segment], work: Path) -> Path:
    """OCR 完成后把原始段落(仅原文,无译文)备份到 segments.orig.json。"""
    path = work / ORIG_NAME
    write_segments(
        [Segment(s.start, s.end, s.text) for s in segments], path, comment=None
    )
    return path


def restore_from_orig(work: Path) -> int:
    """从原始备份还原 segments.json 的改动(文本/译文/增删全部回到 OCR 原始状态)。

    返回还原的段数;没有备份文件时抛 FileNotFoundError。
    """
    orig = work / ORIG_NAME
    if not orig.exists():
        raise FileNotFoundError(orig)
    segs = read_segments(orig)
    cur = work / "segments.json"
    comment = read_meta(cur).get("comment") if cur.exists() else None
    write_segments(segs, cur, comment=comment)
    return len(segs)
