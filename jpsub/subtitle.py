"""字幕文件工程:解析/编码识别/合并去重/排版转换/导出/导入。

把现成字幕文件(SRT/ASS/TXT)读成条目,按时间间隔上限合并碎片与打字帧、
智能去重,再排版转换或导出为 SRT/ASS/TXT;导入时写入工作目录
segments.json(原文进 text),接既有 render/edit/翻译/烧录链路。

程序零网络。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# 支持的编码候选:自动探测时按 \uFFFD 最少者择优
ENCODINGS = ("utf-8", "gb18030", "shift_jis", "euc-kr", "big5", "utf-16le", "utf-16be")
ENC_LABELS = {
    "utf-8": "UTF-8",
    "gb18030": "GBK",
    "big5": "Big5",
    "shift_jis": "Shift-JIS",
    "euc-kr": "EUC-KR",
    "utf-16le": "UTF-16LE",
    "utf-16be": "UTF-16BE",
}

VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".avi")

# 默认 ASS [Events] Format 列
_ASS_DEFAULT_FORMAT = (
    "Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
)

_SMALL_KANA = "ぁぃぅぇぉっゃゅょゎ"
_BIG_KANA = "あいうえおつやゆよわ"


@dataclass
class Entry:
    """一条字幕条目(已解析)。"""

    start: float
    end: float
    text: str  # 原文,内部换行用 "\n"
    style: str = ""
    raw_time: str = ""  # TXT 原样的时间前缀
    sep: str = ":"  # TXT 时间与文本的分隔符
    fields: list[str] = field(default_factory=list)  # ASS Dialogue 各列
    si: int = -1  # ASS Start 列下标
    ei: int = -1  # ASS End 列下标
    ti: int = -1  # ASS Text 列下标
    raw: str = ""  # ASS 原始行


@dataclass
class Parsed:
    """解析结果:格式 + 条目 + ASS 非对白行(保留原样)。"""

    fmt: str
    entries: list[Entry]
    lines: list[str] = field(default_factory=list)  # ASS 全行(含非对白)
    header: list[str] = field(default_factory=list)  # SRT/TXT 无用


# ---------- 编码 ----------


def decode_bytes(buf: bytes, enc: str = "auto") -> tuple[str, str]:
    """把字节解码为文本,返回 (text, 编码标签)。

    enc='auto':先看 BOM,无 BOM 时在候选编码里选解码后 U+FFFD 最少的。
    """
    if enc == "utf-16le":
        return buf.decode("utf-16le", "replace"), "UTF-16LE"
    if enc == "utf-16be":
        return buf.decode("utf-16be", "replace"), "UTF-16BE"
    if enc != "auto":
        label = ENC_LABELS.get(enc, enc)
        return buf.decode(enc, "replace"), label
    if buf[:3] == b"\xef\xbb\xbf":
        return buf[3:].decode("utf-8", "replace"), "UTF-8(BOM)"
    if buf[:2] == b"\xff\xfe":
        return buf[2:].decode("utf-16le", "replace"), "UTF-16LE"
    if buf[:2] == b"\xfe\xff":
        return buf[2:].decode("utf-16be", "replace"), "UTF-16BE"
    # gb18030/big5 对任意字节几乎都能解码,单看 U+FFFD 会把 Shift-JIS 判成中文乱码,
    # 故用字符统计打分:假名/谚文加分(解对的特征),半角片假名减分(日文被错解的征兆),
    # U+FFFD 重罚;同分按候选顺序取先者。
    best, best_name, best_score = "", "UTF-8", None
    for cand in ("utf-8", "gb18030", "shift_jis", "euc-kr", "big5"):
        t = buf.decode(cand, "replace")
        sc = _score_text(t)
        if best_score is None or sc > best_score:
            best, best_name, best_score = t, cand, sc
    return best, ENC_LABELS.get(best_name, best_name)


def _score_text(t: str) -> int:
    """解码质量打分:用于在多种编码都能解码时挑最合理的一种。"""
    s = 0
    for c in t:
        o = ord(c)
        if c == "\ufffd":
            s -= 100
        elif 0x3040 <= o <= 0x30FF:
            s += 2  # 全角平/片假名
        elif 0xFF61 <= o <= 0xFF9F:
            s -= 2  # 半角片假名:通常是日文被错解
        elif 0x4E00 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7A3:
            s += 1  # 汉字 / 谚文
    return s


def _guess_enc(text: str) -> str:
    """凭文本特征猜编码标签(仅用于展示)。"""
    if any("\u3040" <= c <= "\u30ff" for c in text):
        return "shift_jis"  # 含假名:日文
    if any("\uac00" <= c <= "\ud7a3" for c in text):
        return "euc-kr"
    return "utf-8"


def read_text(path: Path, enc: str = "auto") -> tuple[str, str]:
    """读文件并按需解码,返回 (text, 编码标签)。"""
    return decode_bytes(path.read_bytes(), enc)


# ---------- 解析 ----------


def detect_format(text: str) -> str:
    """判定格式:[Events]+Dialogue: → ass;含 '-->' → srt;否则 txt。"""
    if "[Events]" in text and "Dialogue:" in text:
        return "ass"
    if "-->" in text:
        return "srt"
    return "txt"


def parse_time_value(s: str) -> float:
    """裸数字=秒;`1:05.5`=分:秒;`00:01:05,500`=时:分:秒。"""
    s = s.strip().replace(",", ".")
    if not s:
        return 0.0
    if ":" in s:
        parts = s.split(":")
        try:
            vals = [float(p) for p in parts]
        except ValueError:
            return 0.0
        total = 0.0
        for v in vals:
            total = total * 60 + v
        return total
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_srt_clock(s: str) -> float:
    return parse_time_value(s.replace(",", "."))


def fmt_srt_clock(t: float) -> str:
    t = max(t, 0.0)
    ms = round(t * 1000)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def parse_ass_clock(s: str) -> float:
    """ASS 时间 H:MM:SS.cc(百分秒)。"""
    s = s.strip()
    if not s:
        return 0.0
    hms, dot, frac = s.partition(".")
    parts = hms.split(":")
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        return 0.0
    total = 0.0
    for v in vals:
        total = total * 60 + v
    if dot:
        try:
            total += float("0." + frac)
        except ValueError:
            pass
    return total


def fmt_ass_clock(t: float) -> str:
    t = max(t, 0.0)
    cs = round(t * 100)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    sec, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{sec:02d}.{cs:02d}"


_SRT_TIME = re.compile(
    r"^\s*(\d{1,3}:\d{1,2}:\d{1,2}[,.]\d{1,3})\s*-->\s*(\d{1,3}:\d{1,2}:\d{1,2}[,.]\d{1,3})"
)


def parse_srt(text: str) -> Parsed:
    entries: list[Entry] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    for blk in blocks:
        lines = [ln for ln in blk.split("\n")]
        if not lines:
            continue
        idx = 0
        if lines and re.match(r"^\s*\d+\s*$", lines[0]):
            idx = 1  # 跳过序号行
        if idx >= len(lines):
            continue
        m = _SRT_TIME.match(lines[idx])
        if not m:
            continue
        start = parse_srt_clock(m.group(1))
        end = parse_srt_clock(m.group(2))
        body = "\n".join(lines[idx + 1 :]).strip()
        entries.append(Entry(start=start, end=end, text=body))
    return Parsed("srt", entries)


def parse_ass(text: str) -> Parsed:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    entries: list[Entry] = []
    fmt_cols: list[str] = []
    in_events = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            in_events = s.lower() == "[events]"
            continue
        if in_events and s.lower().startswith("format:"):
            fmt_cols = [c.strip() for c in s.split(":", 1)[1].split(",")]
            continue
        if in_events and s.startswith("Dialogue:"):
            body = line.split(":", 1)[1].lstrip()
            cols = fmt_cols or [c.strip() for c in _ASS_DEFAULT_FORMAT.split(",")]
            n = len(cols)
            parts = body.split(",", n - 1)  # Text 列含逗号时并入最后一段
            if len(parts) < n:
                parts += [""] * (n - len(parts))
            fields = parts[:n]
            si = cols.index("Start") if "Start" in cols else 1
            ei = cols.index("End") if "End" in cols else 2
            ti = cols.index("Text") if "Text" in cols else n - 1
            style = fields[cols.index("Style")] if "Style" in cols else ""
            entries.append(
                Entry(
                    start=parse_ass_clock(fields[si]),
                    end=parse_ass_clock(fields[ei]),
                    text=fields[ti],
                    style=style,
                    fields=fields,
                    si=si,
                    ei=ei,
                    ti=ti,
                    raw=line,
                )
            )
    return Parsed("ass", entries, lines=lines)


_TXT_TIME = re.compile(r"^\s*(\d{1,3}:\d{1,2}(?::\d{1,2})?(?:[.,]\d+)?)\s*([:：\s])(.*)$")
_TXT_BARE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s+(.*)$")


def parse_txt(text: str) -> Parsed:
    """TXT:`时间:文本` / `0.12 文本` / `1:05 文本`;无时间的行视为上句续行。"""
    entries: list[Entry] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw.strip():
            continue
        m = _TXT_TIME.match(raw)
        sep = ":"
        if m:
            t = parse_time_value(m.group(1))
            sep = m.group(2)
            body = m.group(3).strip()
        else:
            m = _TXT_BARE.match(raw)
            if m:
                t = parse_time_value(m.group(1))
                sep = " "
                body = m.group(2).strip()
            else:
                if entries:
                    entries[-1].text = (entries[-1].text + "\n" + raw.strip()).strip()
                continue
        entries.append(Entry(start=t, end=t, text=body, raw_time=m.group(1), sep=sep))
    return Parsed("txt", entries)


def parse(text: str, fmt: str | None = None) -> Parsed:
    fmt = fmt or detect_format(text)
    if fmt == "ass":
        return parse_ass(text)
    if fmt == "srt":
        return parse_srt(text)
    return parse_txt(text)


def parse_file(path: Path, enc: str = "auto", fmt: str | None = None) -> Parsed:
    text, _label = read_text(path, enc)
    return parse(text, fmt)


# ---------- 合并 ----------


def gap_seconds(a: Entry, b: Entry) -> float:
    if a.end and b.start:
        return b.start - a.end
    return b.start - a.start


def normalize_text(s: str) -> str:
    """NFKC + 省略号收拢 + 小假名转大写(便于跨帧比对)。"""
    t = unicodedata.normalize("NFKC", str(s))
    t = re.sub(r"\.{2,}", "...", t)
    return "".join(_BIG_KANA[_SMALL_KANA.index(c)] if c in _SMALL_KANA else c for c in t)


def raw_entry_text(e: Entry) -> str:
    return e.text.strip()


def is_name_frame(e: Entry) -> bool:
    """单行短文本(如 観測員 / 警備兵Y-10 / ???)视为名字行。"""
    t = raw_entry_text(e)
    return t != "" and "\n" not in t and len(t) <= 12


def prefix_sim(short: str, long: str) -> bool:
    """短串是否近似等于长串开头(允许少量字符差异)。"""
    sn, ln = normalize_text(short), normalize_text(long)
    n = len(sn)
    if n < 4 or len(ln) < n:
        return False
    tol = max(1, int(n * 0.2))
    mism = run = max_run = 0
    for i in range(n):
        if sn[i] == ln[i]:
            run += 1
            max_run = max(max_run, run)
        else:
            mism += 1
            run = 0
    return mism <= tol and max_run >= 4


def lcs_subseq_len(a: str, b: str) -> int:
    n, m = len(a), len(b)
    if not n or not m or n * m > 5_000_000:
        return 0
    best = 0
    prev = [0] * (m + 1)
    cur = [0] * (m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
            else:
                cur[j] = max(prev[j], cur[j - 1])
            best = max(best, cur[j])
        prev, cur = cur, prev
        for k in range(m + 1):
            cur[k] = 0
    return best


def fuzzy_subseq(short: str, long: str) -> bool:
    """短句是否近似为长句的子序列(允许约 15% 字对不上)。"""
    sn, ln = normalize_text(short), normalize_text(long)
    if len(sn) < 3 or len(ln) < len(sn):
        return False
    tol = int(len(sn) * 0.15)
    return lcs_subseq_len(sn, ln) >= len(sn) - tol


def text_mergeable(a: Entry, b: Entry, cap: float = 3.0) -> bool:
    """两条在文字与时间上是否应合并。"""
    if gap_seconds(a, b) > cap:
        return False
    at, bt = raw_entry_text(a), raw_entry_text(b)
    if not at or not bt:
        return False
    if bt.startswith(at) or at.startswith(bt):
        return True
    if bt.endswith(at):
        return True
    if is_name_frame(b) and at.endswith(bt):
        return True
    short, long = (at, bt) if len(at) <= len(bt) else (bt, at)
    if prefix_sim(short, long):
        return True
    return len(at) <= len(bt) and fuzzy_subseq(short, long)


def build_groups(
    entries: list[Entry],
    cap: float = 3.0,
    *,
    require_same_style: bool = False,
) -> list[list[Entry]]:
    """顺序扫描分组合并:相邻可并则并入;并入名字行后立即封组防串句。"""
    if not entries:
        return []
    groups: list[list[Entry]] = []
    cur = [entries[0]]
    sealed = False
    for b in entries[1:]:
        a = cur[-1]
        style_ok = (not require_same_style) or a.style == b.style
        if not sealed and style_ok and text_mergeable(a, b, cap):
            cur.append(b)
            sealed = is_name_frame(b)
        else:
            groups.append(cur)
            cur = [b]
            sealed = False
    groups.append(cur)
    return groups


def smart_merge(a: str, b: str, join_with: str = "\\N") -> str:
    """智能拼接:包含/前缀相似/模糊子序列时保留较完整句,否则 join 连接。"""
    ta, tb = a.strip(), b.strip()
    if ta and tb.startswith(ta):
        return b
    if tb and ta.startswith(tb):
        return a
    if ta and tb.endswith(ta):
        return b
    if ta and ta.endswith(tb) and "\n" not in tb and len(tb) <= 12:
        return a
    if ta and tb:
        short, long = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        if prefix_sim(short, long) or fuzzy_subseq(short, long):
            return long
    return a + join_with + b


def merge_text(group: list[Entry], join_with: str = "\\N", smart: bool = True) -> str:
    acc = group[0].text
    for e in group[1:]:
        acc = smart_merge(acc, e.text, join_with) if smart else acc + join_with + e.text
    return acc


def flatten(
    groups: list[list[Entry]],
    split: set[int] | None = None,
    join_with: str = "\\N",
    smart: bool = True,
) -> list[Entry]:
    """把分组展开成条目:单条组或已拆组原样,多条目组合并首尾时间与文本。"""
    split = split or set()
    out: list[Entry] = []
    for gi, g in enumerate(groups):
        if len(g) == 1 or gi in split:
            out.extend(g)
        else:
            out.append(
                Entry(
                    start=g[0].start,
                    end=g[-1].end,
                    text=merge_text(g, join_with, smart),
                    style=g[0].style,
                    raw_time=g[0].raw_time,
                    sep=g[0].sep,
                )
            )
    return out


# ---------- 排版转换 ----------


def layout_convert(text: str, *, space: bool = True, newline: bool = True) -> str:
    """把空格/换行替换为 \\N;{…} 覆盖标签内不动;去首尾 \\N。"""
    if space and newline:
        re_ = re.compile(r"\{[^}]*\}|[ \t\r\n]+")
    elif space:
        re_ = re.compile(r"\{[^}]*\}|[ \t]+")
    else:
        re_ = re.compile(r"\{[^}]*\}|\r?\n+")
    out = re_.sub(lambda m: m.group(0) if m.group(0)[0] == "{" else "\\N", str(text))
    return re.sub(r"^(?:\\N)+|(?:\\N)+$", "", out)


# ---------- 导出 ----------


def to_srt(entries: list[Entry]) -> str:
    blocks = []
    for i, e in enumerate(entries):
        blocks.append(str(i + 1))
        blocks.append(f"{fmt_srt_clock(e.start)} --> {fmt_srt_clock(e.end)}")
        blocks.append(e.text)
        blocks.append("")
    return "\ufeff" + "\n".join(blocks)


def to_txt(entries: list[Entry]) -> str:
    lines = []
    for e in entries:
        rt = e.raw_time or f"{e.start:.2f}"
        lines.append(f"{rt}{e.sep}{e.text}")
    return "\n\n".join(lines) + "\n"


# SRT/TXT 转 ASS 时的最小骨架(Script Info + 样式 + Events 表头)
_ASS_TEMPLATE = """[Script Info]
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_skeleton() -> list[str]:
    return _ASS_TEMPLATE.split("\n")


def to_ass(parsed: Parsed, entries: list[Entry]) -> str:
    """按条目顺序重建 ASS:非对白行原样保留,Dialogue 用条目的时间与文本。"""
    lines = list(parsed.lines)
    # 源不是 ASS(如 SRT/TXT 转 ASS)时没有骨架,补默认模板再逐条追加 Dialogue
    if not any(line.lstrip().startswith("Dialogue:") for line in lines):
        skeleton = _ass_skeleton()
        while skeleton and skeleton[-1] == "":
            skeleton.pop()
        base = skeleton or ["[Events]"]
        for e in entries:
            text = e.text.replace("\n", "\\N")
            style = e.style or "Default"
            base.append(
                "Dialogue: 0,"
                + fmt_ass_clock(e.start)
                + ","
                + fmt_ass_clock(e.end)
                + f",{style},,0,0,0,,{text}"
            )
        return "\n".join(base) + "\n"
    diag = 0
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("Dialogue:"):
            continue
        if diag >= len(entries):
            break
        e = entries[diag]
        diag += 1
        fields = list(e.fields)
        if fields and e.si >= 0:
            fields[e.si] = fmt_ass_clock(e.start)
            fields[e.ei] = fmt_ass_clock(e.end)
            fields[e.ti] = e.text
            lines[i] = "Dialogue: " + ",".join(fields)
    return "\n".join(lines)


def to_layout_text(parsed: Parsed, *, space: bool = True, newline: bool = True) -> str:
    """排版模式:不动时间轴与结构,仅把每条文本的空格/换行转 \\N。"""
    def conv(e: Entry) -> str:
        return layout_convert(e.text, space=space, newline=newline)
    items = [
        Entry(e.start, e.end, conv(e), e.style, e.raw_time, e.sep) for e in parsed.entries
    ]
    if parsed.fmt == "ass":
        return to_ass(parsed, items)
    return to_srt(items) if parsed.fmt == "srt" else to_txt(items)


# ---------- 导入工作目录 ----------


def import_to_work(
    src: Path,
    work: Path,
    *,
    enc: str = "auto",
    fmt: str | None = None,
    cap: float | None = None,
    merge: bool = True,
    require_same_style: bool = False,
    smart: bool = True,
    layout: bool = False,
    layout_space: bool = True,
    layout_newline: bool = True,
) -> tuple[Path, int]:
    """解析字幕文件并写入工作目录 segments.json(原文进 text)。

    返回 (segments.json 路径, 写入条目数)。merge=False 时保留原始条目。
    """
    from . import handoff
    from .segment import Segment as _Seg

    parsed = parse_file(src, enc, fmt)
    entries = parsed.entries
    if merge and cap is not None:
        groups = build_groups(entries, cap, require_same_style=require_same_style)
        entries = flatten(groups, join_with="\n", smart=smart)
    if layout:
        for e in entries:
            e.text = layout_convert(e.text, space=layout_space, newline=layout_newline)
    segs = [_Seg(e.start, e.end, e.text) for e in entries if e.text.strip()]
    seg_file = handoff.seg_path(work)
    work.mkdir(parents=True, exist_ok=True)
    handoff.write_segments(segs, seg_file, comment=None)
    handoff.save_orig(segs, work)
    return seg_file, len(segs)
