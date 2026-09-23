"""翻译交接:导出待翻译清单、导入译文、段落 JSON 读写。程序不发任何网络请求。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .cache import TranslationCache
from .segment import Segment


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
    """读 segments.json 里的翻译元信息(comment)。旧格式返回空。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {}
    return {"comment": data.get("comment")}


def pending_texts(segments: list[Segment], cache: TranslationCache) -> list[str]:
    """全局去重、且缓存里还没有译文的原文,保持首次出现顺序。"""
    seen: list[str] = []
    for s in segments:
        if s.text and s.text not in seen and cache.get(s.text) is None:
            seen.append(s.text)
    return seen


def export_pending(
    segments: list[Segment],
    cache: TranslationCache,
    out_txt: Path,
    *,
    with_index: bool = True,
) -> list[tuple[str, str]]:
    """把全部唯一原文写成纯文本(全量导出,命中缓存的也包含)。

    每行 `起-止<TAB>原文`,时间轴取该句首次出现段的起止秒(如 `12.5-14`),
    用户可随意编辑/换行/增删行。返回 [(时间轴键, 原文), ...]。
    """
    seen: dict[str, str] = {}
    for s in segments:
        if s.text and s.text not in seen:
            seen[s.text] = f"{s.start:g}-{s.end:g}"
    items = [(k, t) for t, k in seen.items()]  # [(时间轴键, 原文)]
    items.sort(key=lambda kt: _key_start(kt[0]))  # 按时间轴排序,便于人工阅读
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    if with_index:
        body = "".join(f"{k}\t{t}\n" for k, t in items)
    else:
        body = "".join(f"{t}\n" for _, t in items)
    out_txt.write_text(body, encoding="utf-8")
    return items


def _key_start(key: str) -> float:
    """时间轴键 '573.5-584' 的起始秒,用于排序。"""
    try:
        return float(key.partition("-")[0])
    except ValueError:
        return float("inf")


def realign_translations(
    in_txt: Path,
    old_in_text: str | None,
    old_out_text: str | None,
    old_segs: list[Segment],
    cache: TranslationCache,
) -> str:
    """导出全量 translate-in.txt 后重排 translate-out.txt,使时间轴对齐。

    每个时间轴键的译文来源优先级(**用户 in/out 优先**):
    1. 旧 translate-out.txt 中同键的行——仅当旧 in 同键原文一致
       (时间轴未漂移,用户/AI 写的内容原样保留);
    2. 翻译缓存(按原文文本为键,可靠);
    3. 旧 segments.json 里该原文的 tr 快照。
    都没有的键不写行,交给 AI 翻。返回新的 translate-out.txt 内容。
    """
    src_map = pending_map_from_in(in_txt)
    old_src = pending_map_from_str(old_in_text) if old_in_text else {}
    old_tr_by_text = {s.text: s.tr for s in old_segs if s.text and s.tr}
    # 旧 out 里出现过的键(含译文置空行):新 out 只保留这些键,
    # 用户从 out 删掉的时间轴不再从缓存/快照回填,视为删除该字幕
    old_out_keys: set[str] = set()
    if old_out_text:
        for ln in old_out_text.splitlines():
            if ln.strip():
                old_out_keys.add(ln.partition("\t")[0].strip())
    lines: list[str] = []
    for key, text in src_map.items():
        if old_out_text and key not in old_out_keys:
            continue
        tr = None
        if old_in_text and old_src.get(key) == text and old_out_text:
            for ln in old_out_text.splitlines():  # 同键同原文:用户优先
                if ln.startswith(key + "\t") and ln.partition("\t")[2].strip():
                    tr = ln.partition("\t")[2].strip()
                    break
        if not tr:
            tr = cache.get(text)
        if not tr:
            tr = old_tr_by_text.get(text)
        if tr:
            lines.append(f"{key}\t{tr}")
    lines.sort(key=lambda ln: _key_start(ln.partition("\t")[0]))  # 按时间轴排序
    return "".join(f"{ln}\n" for ln in lines)


def sync_segments(
    segs: list[Segment],
    in_map: dict[str, str],
    out_text: str,
    *,
    allow_delete: set[str] | None = None,
) -> list[Segment]:
    """以 out 的时间轴为准同步 segments:

    - `allow_delete` 里的键若不在 out 中 = 用户删除了该字幕,从 segments 移除;
      (传 None/空集则不删段——out 缺行可能只是未翻译,不能误删)
    - out 里有而 in 里没有的时间轴 = 用户新增的字幕,解析行首 `起-止`
      新增段落(译文即该行内容,原样显示)。
    返回按起始时间排序的新段落列表。
    """
    out_keys: set[str] = set()
    tr_by_key: dict[str, str] = {}
    for ln in out_text.splitlines():
        if not ln.strip():
            continue
        sp = split_line(ln)
        if not sp:
            continue
        k, t = sp
        out_keys.add(k)
        if t.strip():
            tr_by_key[k] = t.strip()
    deleted_keys = (set(in_map) - out_keys) & (allow_delete or set())
    segs = [s for s in segs if f"{s.start:g}-{s.end:g}" not in deleted_keys]
    if allow_delete:
        # out 是最终裁决:凡 out 里没有的时间轴,一律视为用户删除
        segs = [s for s in segs if f"{s.start:g}-{s.end:g}" in out_keys]
    for k, t in tr_by_key.items():
        if k in in_map or "-" not in k:
            continue
        try:
            st, en = (float(x) for x in k.split("-", 1))
        except ValueError:
            continue
        segs.append(Segment(st, en, t, tr=t))  # 新增段:文本即译文
    # 同一时间轴只保留一条(优先带译文的),避免历史重复段重复渲染
    dedup: dict[str, Segment] = {}
    for s in segs:
        k = f"{s.start:g}-{s.end:g}"
        if k not in dedup or (not dedup[k].tr and s.tr):
            dedup[k] = s
    segs = sorted(dedup.values(), key=lambda s: s.start)
    return segs


_KEY_RE = re.compile(r"^(\d+(?:\.\d+)?-\d+(?:\.\d+)?)[ \t]+(.*)$")


def split_line(ln: str) -> tuple[str, str] | None:
    """解析「键<TAB>文本」行;键后用空格分隔(如 '745-747 住手')也能识别。"""
    if "\t" in ln:
        k, _, t = ln.partition("\t")
        return k.strip(), t
    m = _KEY_RE.match(ln)
    return (m.group(1), m.group(2)) if m else None


def pending_map_from_str(text: str) -> dict[str, str]:
    """从「键<TAB>原文」文本反推 键->原文 映射(键为时间轴或行序号)。"""
    out: dict[str, str] = {}
    for pos, ln in enumerate(text.splitlines(), 1):
        if not ln.strip():
            continue
        sp = split_line(ln)
        if sp:
            out[sp[0]] = sp[1]
        else:
            out[str(pos)] = ln
    return out


def pending_map_from_in(in_txt: Path) -> dict[str, str]:
    """从 translate-in.txt 反推 编号->原文 映射(渲染导入译文时用)。

    '编号<TAB>原文' 行按编号取;无 TAB 的行按行序编号 1..N。
    编号允许不连续(用户手动删行后重跑),导入时按编号对应才不会错位。
    """
    return pending_map_from_str(in_txt.read_text(encoding="utf-8"))


def import_translations(
    txt_path: Path,
    cache: TranslationCache,
    src_map: dict[str, str],
) -> tuple[int, list[str]]:
    """读回译文写入缓存并落盘,返回 (成功条数, 被删除的原文列表)。

    src_map 是 translate-in.txt 的 键->原文 映射(键为时间轴)。
    译文行按 '键<TAB>译文' 解析;若每行都没有 TAB,则按行序对齐。
    **键<TAB>空(置空译文)= 删除该条字幕**,其原文出现在返回的删除列表中,
    由调用方把对应字幕段从时间轴上移除;键不在 out 里 = 未翻译,保留原文。
    """
    lines = [ln for ln in txt_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    order = list(src_map)
    count = 0
    deleted_keys: set[str] = set()
    for pos, line in enumerate(lines):
        sp = split_line(line)
        if sp:
            idx, dst = sp
            if idx not in src_map:
                continue
        else:
            if pos >= len(order):
                continue
            idx, dst = order[pos], line
        dst = dst.strip()
        if not dst:
            deleted_keys.add(idx)  # 置空译文 = 删除该条字幕
        elif "\t" in dst or "\n" in dst:
            continue  # 译文里混入时间轴/多行 = 损坏行,不进缓存
        else:
            cache.put(src_map[idx], dst)
            count += 1
    cache.save()
    deleted = [t for key, t in src_map.items() if key in deleted_keys]
    return count, deleted


def resolve(segments: list[Segment], cache: TranslationCache) -> dict[str, str]:
    """段文本 -> 译文。优先段上快照的 tr(用户编辑过 in/out 后最新),
    其次翻译缓存;缺译文的不返回(渲染时该段不显示,不用原文填补)。"""
    out: dict[str, str] = {}
    for s in segments:
        if s.text and (s.tr or cache.get(s.text)):
            out[s.text] = s.tr or cache.get(s.text)
    return out
