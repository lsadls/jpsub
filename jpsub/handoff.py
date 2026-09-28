"""segments.json 中心数据层:段落读写、原始备份。程序不发任何网络请求。

原文与译文统一存在 segments.json (Segment.text / Segment.tr);OCR 完成后
另存一份原始备份 segments.orig.json,供译文调整器一键还原改动。
"""

from __future__ import annotations

import json
from pathlib import Path

from .cache import TranslationCache
from .segment import Segment

# 未译占位标记:AI 未返回译文(内容审查拦截、单批失败等)时,tr 记为该值,
# 不进缓存、不参与渲染,可在译文调整器里一眼看出并重翻。
UNTRANSLATED_MARK = "[[未译]]"

# 长文拆句:引号感知扫描,替代旧正则。规则:
# - 句末符号(。!?!)只在引号外拆,且后随句末符号/闭引号时不拆
# - 闭引号(」』)后,只有后续不是续接助词(「という印象」这类引号作句中成分)时才拆
# - 连续"!!!!"、引号内的"!"等不再产生碎句(旧正则会把「来たそ!!!!」拆出 4 个"!"废句)
_SENT_END = "。!?!"
_SENT_OPEN = "「『"
_SENT_CLOSE = "」』"
_SENT_CONT = set(
    "とだでをはがにのもっなかやゆりし"
)  # 」后续接词首字:という/みたい/など/って/と思った…


def split_sents(t: str) -> list[str]:
    # OCR 漏闭引号:开引号之后整段再无闭引号的,视为无效,不计深度
    # (否则一个未闭合的『会把整段都当成引号内,句末符号全不拆)
    closes = [i for i, c in enumerate(t) if c in _SENT_CLOSE]
    broken_opens = {
        i for i, c in enumerate(t) if c in _SENT_OPEN and not any(j > i for j in closes)
    }
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for i, ch in enumerate(t):
        buf.append(ch)
        if ch in _SENT_OPEN and i not in broken_opens:
            depth += 1
        elif ch in _SENT_CLOSE:
            depth -= 1
        if ch in _SENT_END:
            nxt = t[i + 1] if i + 1 < len(t) else ""
            if depth <= 0:
                do = bool(nxt) and nxt not in _SENT_END + _SENT_CLOSE
            else:
                # 引号内的句末符号:若后续先出现新开引号(新说话人)再出现闭引号,
                # 说明当前引号已被 OCR 漏闭,就地拆分(引号内短叹句如"楽しみだな」"不拆)
                rest = t[i + 1 :]
                io = min(
                    (j for j in (rest.find(c) for c in _SENT_OPEN) if j >= 0),
                    default=-1,
                )
                ic = min(
                    (j for j in (rest.find(c) for c in _SENT_CLOSE) if j >= 0),
                    default=-1,
                )
                do = io >= 0 and (ic < 0 or io < ic)
            if do:
                parts.append("".join(buf))
                buf = []
                depth = 0  # 引号已断,后续深度重置
        elif ch in _SENT_CLOSE and depth <= 0:
            nxt = t[i + 1] if i + 1 < len(t) else ""
            if nxt not in _SENT_CONT:
                parts.append("".join(buf))
                buf = []
    if buf:
        parts.append("".join(buf))
    return [p for p in parts if p.strip()]


# OCR 产物原始备份(仅 text,无 tr):译文调整器「还原改动」的数据源
ORIG_NAME = "segments.orig.json"

# 工作目录布局:内部工作文件(segments/cache/comment/masks/ocr 缓存/backup 等)
# 统一收进工作目录下的隐藏子目录,用户在目录里只看到视频、info.txt、cover.jpg、字幕。
HIDDEN_DIR = ".jpsub"


def hidden_dir(work: Path) -> Path:
    """工作目录里收内部文件的隐藏子目录。"""
    return work / HIDDEN_DIR


def seg_path(work: Path) -> Path:
    """segments.json 路径。"""
    return hidden_dir(work) / "segments.json"


def orig_path(work: Path) -> Path:
    return hidden_dir(work) / ORIG_NAME


def cache_path(work: Path) -> Path:
    return hidden_dir(work) / "cache.json"


def comment_path(work: Path) -> Path:
    return hidden_dir(work) / "comment.txt"


def masks_path(work: Path) -> Path:
    return hidden_dir(work) / "masks.json"


def ocr_cache_path(work: Path) -> Path:
    return hidden_dir(work) / "ocr-cache.json"


def crop_path(work: Path) -> Path:
    return hidden_dir(work) / "crop.json"


def backup_dir(work: Path) -> Path:
    """防误操作备份目录(始终在隐藏子目录里)。"""
    return work / HIDDEN_DIR / "backup"


def item_stem(work: Path) -> str:
    """工作目录的展示名。"""
    return work.name


def is_untranslated(text: str) -> bool:
    """判断一段译文是否为未译占位标记(或为空)。"""
    return not text or text.strip() == UNTRANSLATED_MARK


# 模型把拒绝语当译文返回(内容审查提示混进译文,中英文变体)时的特征,视为无效译文
import re as _re

_JUNK_PAT = _re.compile(
    r"the request was rejected|high risk|content[_ ]?filter|safety system"
    r"|无法翻译|无法协助|不能翻译|不能协助|拒绝翻译"
    r"|提供(其他|不含|别的)(文本|内容)"
    r"|can('?)t (assist|help|translate)|unable to (assist|help|translate)",
    _re.I,
)


def is_junk_tr(text: str) -> bool:
    """判断译文里是否混入了模型拒绝语(如 'The request was rejected...')。"""
    return bool(text) and bool(_JUNK_PAT.search(text))


def is_bad_tr(text: str) -> bool:
    """无效译文 = 未译占位(或空)、混入拒绝语、或含 [[原句]] 占位(--hard
    失败句拼回后的形式);一律视为待翻/待修。"""
    return bool(
        is_untranslated(text) or is_junk_tr(text) or _re.search(r"\[\[.+\]\]", text)
    )


_QUOT_PUNCT = _re.compile(r"[\s，。、！？：；…—·,.!?;:~()（）\[\]{}「」『』\"']")


def _normalize_quotes(text: str) -> str:
    """把译文里的引号统一换成中文直角引号:双引号("、“、”)→「」,单引号('、‘、’)→『』。

    弯引号自带方向直接映射;直引号按上下文判断开/闭(贴着词尾、后面是
    空白/标点/串尾 → 闭;贴着词头 → 开),判断不了才回退开闭交替。
    纯交替法在 AI 漏写一个引号(奇数个)时会让后续全部串位。"""

    def _is_word(ch: str) -> bool:
        return bool(ch) and not ch.isspace() and not _QUOT_PUNCT.match(ch)

    out: list[str] = []
    dq_open, sq_open = True, True  # 直引号回退用的交替状态
    for i, ch in enumerate(text):
        if ch == "“":
            out.append("「")
        elif ch == "”":
            out.append("」")
        elif ch == "‘":
            out.append("『")
        elif ch == "’":
            out.append("』")
        elif ch in ('"', "'"):
            prev = text[i - 1] if i else ""
            nxt = text[i + 1] if i + 1 < len(text) else ""
            prev_word, nxt_word = _is_word(prev), _is_word(nxt)
            if prev_word and not nxt_word:
                opn = False  # 词尾之后 → 闭引号
            elif nxt_word and not prev_word:
                opn = True  # 词头之前 → 开引号
            else:
                opn = dq_open if ch == '"' else sq_open  # 判断不了,交替回退
            if ch == '"':
                out.append("「" if opn else "」")
                dq_open = not opn
            else:
                out.append("『" if opn else "』")
                sq_open = not opn
        else:
            out.append(ch)
    return "".join(out)


def write_segments(
    segments: list[Segment],
    path: Path,
    *,
    comment: str | None = None,
    long: bool | None = None,
) -> None:
    """segments 与翻译元信息(comment)合写进同一个 JSON。

    结构:{"segments": [...], "comment": ..., "long": ...},comment 是视频描述,
    long 记录上次翻译是否为长文模式(None=不写该键,保持原文件不变)。
    每段带 tr(译文)快照,重跑时免重翻。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "segments": [
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                **({"tr": _normalize_quotes(s.tr)} if s.tr else {}),
            }
            for s in segments
        ],
        "comment": comment,
    }
    if long is not None:
        data["long"] = long
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def read_segments(path: Path) -> list[Segment]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Segment(d["start"], d["end"], d["text"], tr=d.get("tr"))
        for d in data["segments"]
    ]


def read_meta(path: Path) -> dict:
    """读 segments.json 里的翻译元信息(comment)。文件缺失返回空。"""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"comment": data.get("comment"), "long": data.get("long")}


def is_symbol_only(text: str) -> bool:
    """原文是否只由符号/标点构成(无任何文字、数字字符),如「!!」「……」——不必送翻。"""
    import unicodedata

    return bool(text) and not any(
        unicodedata.category(ch)[0] in ("L", "N") for ch in text if not ch.isspace()
    )


def write_meta(path: Path, *, long: bool | None = None) -> None:
    """只更新 segments.json 的元信息键(如 long),不动段落内容;文件不存在则忽略。"""
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    if long is not None:
        data["long"] = long
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def pending_texts(segments: list[Segment], cache: TranslationCache) -> list[str]:
    """全局去重、且还没有译文的原文(段上无 tr 且缓存未命中),保持首次出现顺序。
    纯符号原文(如「!!」)不送翻,直接按原样记为译文。"""
    seen: list[str] = []
    for s in segments:
        if not s.text:
            continue
        if s.tr and not is_bad_tr(s.tr):
            continue  # 段上已有有效译文
        if is_symbol_only(s.text):
            s.tr = s.text  # 符号串原样通过,不算未译
            continue
        if s.text not in seen and cache.get(s.text) is None:
            seen.append(s.text)
    return seen


def resolve(segments: list[Segment], cache: TranslationCache) -> list[str]:
    """按段序返回每段译文(空串=无译文,渲染时跳过)。优先段上快照的 tr
    (用户在调整器里编辑后最新),其次翻译缓存;只跳过空/[[未译]]占位,
    混入拒绝语的译文照常渲染(是否重翻是翻译侧的事,渲染侧不负责丢弃)。
    必须与段一一对应,不能用原文文本做字典键:重复原文段会被后段覆盖,
    渲染出张冠李戴的字幕。"""
    out: list[str] = []
    for s in segments:
        cand = ""
        for c in (s.tr, cache.get(s.text)):
            if c and not is_untranslated(c):
                cand = c
                break
        out.append(cand)
    return out


def save_orig(segments: list[Segment], work: Path) -> Path:
    """OCR 完成后把原始段落(仅原文,无译文)备份到 segments.orig.json。"""
    path = orig_path(work)
    write_segments(
        [Segment(s.start, s.end, s.text) for s in segments], path, comment=None
    )
    return path


def restore_from_orig(work: Path) -> int:
    """从原始备份还原 segments.json 的改动(文本/译文/增删全部回到 OCR 原始状态)。

    返回还原的段数;没有备份文件时抛 FileNotFoundError。
    """
    orig = orig_path(work)
    if not orig.exists():
        raise FileNotFoundError(orig)
    segs = read_segments(orig)
    cur = seg_path(work)
    comment = read_meta(cur).get("comment") if cur.exists() else None
    write_segments(segs, cur, comment=comment)
    return len(segs)
