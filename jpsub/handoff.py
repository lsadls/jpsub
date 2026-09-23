"""翻译交接:导出待翻译清单、导入译文、段落 JSON 读写。程序不发任何网络请求。"""
from __future__ import annotations

import json
from pathlib import Path

from .cache import TranslationCache
from .segment import Segment


def write_segments(segments: list[Segment], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [{"start": s.start, "end": s.end, "text": s.text} for s in segments]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def read_segments(path: Path) -> list[Segment]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Segment(d["start"], d["end"], d["text"]) for d in data]


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
) -> list[str]:
    """把待翻译的唯一原文写成纯文本,返回本次导出的原文顺序(即编号 1..N)。

    with_index=True 时每行 '编号<TAB>原文';否则每行仅原文。
    全部命中缓存时写出空文件并返回 []。
    """
    pending = pending_texts(segments, cache)
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    if with_index:
        body = "".join(f"{i}\t{t}\n" for i, t in enumerate(pending, 1))
    else:
        body = "".join(f"{t}\n" for t in pending)
    out_txt.write_text(body, encoding="utf-8")
    return pending


def import_translations(
    txt_path: Path,
    cache: TranslationCache,
    pending: list[str],
) -> tuple[int, list[str]]:
    """读回译文写入缓存并落盘,返回 (成功条数, 被删除的原文列表)。

    优先按 '编号<TAB>译文' 解析(编号 -> pending[编号-1]);若每行都没有 TAB,
    则按行序与 pending 对齐。译文为空的行跳过。
    translate-out.txt 里被删掉的行视为"删除该条字幕",其原文会出现在返回的
    删除列表中,由调用方把对应字幕段从时间轴上移除。
    """
    lines = [ln for ln in txt_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    has_index = bool(lines) and all("\t" in ln for ln in lines)
    count = 0
    imported: set[int] = set()  # 已出现的 pending 位置(0-based)
    for pos, line in enumerate(lines):
        if has_index:
            idx_s, _, dst = line.partition("\t")
            try:
                src = pending[int(idx_s) - 1]
                imported.add(int(idx_s) - 1)
            except (ValueError, IndexError):
                continue
        else:
            if pos >= len(pending):
                continue
            src, dst = pending[pos], line
            imported.add(pos)
        dst = dst.strip()
        if dst:
            cache.put(src, dst)
            count += 1
    cache.save()
    deleted = [t for i, t in enumerate(pending) if i not in imported]
    return count, deleted


def resolve(segments: list[Segment], cache: TranslationCache) -> dict[str, str]:
    """段文本 -> 译文;缺译文的段回退为原文(不中断管道)。"""
    return {s.text: (cache.get(s.text) or s.text) for s in segments if s.text}
