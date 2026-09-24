"""命令行入口:抽帧 -> OCR -> 合并 -> (导出/导入) -> ASS。程序零网络。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
from pathlib import Path

from PIL import Image

from . import ai, ass, frames, handoff, ocr, segment, settings, trigger
from .cache import TranslationCache


def parse_time(s: str) -> float:
    """把时刻转成秒。支持 90 / 90.5 / 11:41 / 1:02:03 / 11分41秒 / 1时2分3秒。"""
    text = str(s).strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", text):  # 裸数字 = 秒
        return float(text)
    m = re.fullmatch(r"(?:(\d+)时)?(?:(\d+)分)?(?:(\d+(?:\.\d+)?)秒?)?", text)
    if ":" in text:  # 冒号分隔:分:秒 或 时:分:秒
        parts = text.split(":")
        if not all(re.fullmatch(r"\d+(?:\.\d+)?", p) for p in parts) or len(parts) > 3:
            raise argparse.ArgumentTypeError(f"无法识别的时刻:{s!r}")
        nums = [float(p) for p in parts]
        if len(nums) == 2:
            return nums[0] * 60 + nums[1]
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if not m or not any(m.groups()):
        raise argparse.ArgumentTypeError(f"无法识别的时刻:{s!r}")
    h = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    sec = float(m.group(3) or 0)
    if h and not mi and not text.endswith("秒"):
        mi, sec = int(sec), 0.0  # 「1时41」按 1时41分 理解
    return h * 3600 + mi * 60 + sec


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="jpsub",
        description="日语视频(底部短句 / 全屏打字机长文) -> 中文外挂 ASS 字幕",
    )
    p.add_argument(
        "-s",
        "--script",
        type=Path,
        help="批量脚本:每行一条 jpsub 调用;下载/筛选并行,OCR 排队,翻译并发",
    )
    p.add_argument(
        "--dl-workers", type=int, default=0, help="批量模式下载/筛选进程数(默认自动)"
    )
    p.add_argument(
        "--tr-workers", type=int, default=0, help="批量模式翻译并发线程数(默认自动)"
    )
    sub = p.add_subparsers(dest="command")

    def add_pipeline(a):
        a.add_argument("--fps", type=float, default=settings.FPS)
        a.add_argument(
            "--crop",
            type=str,
            default=settings.CROP,
            help="字幕区裁剪:单数字=底部占比(如 0.25),或 上:下:左:右 四边距(如 0.75:0.03:0.03:0.03)",
        )
        a.add_argument("--diff-threshold", type=float, default=settings.DIFF_THRESHOLD)
        a.add_argument(
            "--start",
            type=parse_time,
            default=None,
            help="只处理该时刻之后的画面,如 90 / 11:41 / 11分41秒",
        )
        a.add_argument(
            "--end",
            type=parse_time,
            default=None,
            help="只处理该时刻之前的画面,如 300 / 5:00 / 5分(避开片尾滚动的素材名单)",
        )
        a.add_argument("--similarity", type=float, default=settings.SIMILARITY)
        a.add_argument(
            "--settle-frames",
            type=int,
            default=settings.SETTLE_FRAMES,
            help="停顿触发:静止多少帧后识别一次",
        )
        a.add_argument(
            "--max-run",
            type=int,
            default=settings.MAX_RUN,
            help="停顿触发:连续变化超过这么多帧未停则强制识别",
        )
        a.add_argument(
            "--cache",
            type=Path,
            default=None,
            help="翻译缓存路径(默认 <工作目录>/cache.json,按视频隔离)",
        )
        a.add_argument(
            "--selected-only",
            action="store_true",
            help="只抽帧并筛选,不 OCR;筛选帧保存到 -o 工作目录",
        )
        a.add_argument(
            "--extract-only",
            action="store_true",
            help="只抽帧不 OCR,用于检查抽帧质量",
        )
        a.add_argument(
            "--notrans",
            action="store_true",
            help="只到 OCR 产出 segments.json 为止,不自动翻译不渲染",
        )
        a.add_argument(
            "--force",
            action="store_true",
            help="强制重新抽帧 OCR(默认已有 segments.json 时跳过,读缓存)",
        )

    def add_api(a):
        a.add_argument(
            "--api-base", help="OpenAI 兼容端点,默认 $JPSUB_API_BASE/$OPENAI_BASE_URL"
        )
        a.add_argument("--api-key", help="默认 $JPSUB_API_KEY/$OPENAI_API_KEY")
        a.add_argument("--model", help="模型名,默认 $JPSUB_MODEL")
        a.add_argument(
            "--glossary",
            type=Path,
            default=None,
            help="名词对照表文件(默认读 ~/.jpsub/glossary.txt 与工作目录 glossary.txt)",
        )
        a.add_argument(
            "--batch-size",
            type=int,
            default=settings.BATCH_SIZE,
            help="每次请求翻译的句数",
        )

    def add_style(a):
        a.add_argument("--font", default=settings.FONT)
        a.add_argument("--font-size", type=int, default=settings.FONT_SIZE)
        a.add_argument("--max-chars", type=int, default=settings.MAX_CHARS)

    e = sub.add_parser("extract", help="抽帧/OCR/合并,产出 segments.json(+原始备份)")
    e.add_argument("video", type=Path)
    e.add_argument("-o", "--work", type=Path, help="工作目录(默认 <视频>.jpsub)")
    e.add_argument(
        "--comment",
        help="视频描述(如 剧场类型/背景设定),引导 AI 翻译时参考;默认读工作目录 comment.txt",
    )
    add_pipeline(e)
    add_api(e)
    add_style(e)

    r = sub.add_parser("render", help="用译文生成 ASS")
    r.add_argument("work", type=Path)
    r.add_argument("-o", "--output", type=Path, help="输出 .ass(默认 <work>.ass)")
    r.add_argument(
        "--cache", type=Path, default=None, help="默认 <工作目录>/cache.json"
    )
    add_style(r)

    u = sub.add_parser("run", help="extract,若有译文则顺带 render")
    u.add_argument("video", type=Path)
    u.add_argument("-o", "--output", type=Path)
    u.add_argument("--work", type=Path)
    u.add_argument("--burn", action="store_true", help="把 ASS 烧录进视频(默认不烧录)")
    u.add_argument(
        "--comment",
        help="视频描述(如 剧场类型/背景设定),引导 AI 翻译时参考;默认读工作目录 comment.txt",
    )
    add_pipeline(u)
    add_api(u)
    add_style(u)

    s = sub.add_parser("status", help="查看工作目录进度")
    s.add_argument("work", type=Path)
    s.add_argument(
        "--cache", type=Path, default=None, help="默认 <工作目录>/cache.json"
    )

    t = sub.add_parser("translate", help="调用 AI API 翻译 segments.json(可续翻)")
    t.add_argument("work", type=Path, help="工作目录(含 segments.json)")
    t.add_argument(
        "--comment",
        help="视频描述(如 剧场类型/背景设定),引导 AI 翻译时参考;默认读工作目录 comment.txt",
    )
    t.add_argument(
        "--force",
        action="store_true",
        help="重新翻译全部句子(默认已译的跳过,读缓存)",
    )
    t.add_argument(
        "--cache", type=Path, default=None, help="默认 <工作目录>/cache.json"
    )
    add_api(t)

    m = sub.add_parser("mask", help="打码选取器:鼠标框选区域,生成 masks.json")
    m.add_argument("video", type=Path)
    m.add_argument(
        "--masks",
        type=Path,
        default=None,
        help="打码清单(默认 <视频工作目录>/masks.json)",
    )

    ma = sub.add_parser("maskapply", help="把 masks.json 的打码应用到视频")
    ma.add_argument("video", type=Path)
    ma.add_argument(
        "masks",
        type=Path,
        nargs="?",
        default=None,
        help="默认 <视频工作目录>/masks.json",
    )
    ma.add_argument(
        "-o", "--output", type=Path, default=None, help="默认 <视频>.masked.mp4"
    )
    ma.add_argument(
        "--burn",
        action="store_true",
        help="打码后接着把 <视频>.ass 烧进结果(一步得到打码+字幕的成品)",
    )

    ed = sub.add_parser(
        "edit", help="浏览器调整译文:编辑/删除/新增字幕,支持一键还原 OCR 原始结果"
    )
    ed.add_argument(
        "target", type=Path, help="工作目录(如 output/sm123.jpsub)或视频文件"
    )

    hm = sub.add_parser(
        "home", help="浏览器主页:选择视频/输入 sm 号,按钮发起下载/编辑/打码/烧录"
    )
    hm.add_argument(
        "--root", type=Path, default=None, help="产物根目录(默认 <项目根>/output)"
    )

    d = sub.add_parser(
        "download",
        help="下载 niconico 视频(≥360p 中最小)并跑完整流水线:抽帧>OCR>翻译>ASS",
    )
    d.add_argument("url", help="niconico 视频 URL 或 id(如 sm43168834)")
    d.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="保存目录(默认 <项目根>/output)",
    )
    d.add_argument(
        "--cookies-from-browser",
        default=settings.COOKIES_FROM_BROWSER,
        help="把浏览器 cookies 传给 yt-dlp(如 chrome/firefox/edge),应对登录或地区限制",
    )
    d.add_argument("--burn", action="store_true", help="把 ASS 烧录进视频(默认不烧录)")
    d.add_argument(
        "--comment",
        help="视频描述(如 剧场类型/背景设定),引导 AI 翻译时参考",
    )
    add_api(d)
    add_pipeline(d)
    add_style(d)
    args = p.parse_args(argv)
    if not args.command and not args.script:
        p.error("需要子命令、视频 URL 或 -s 脚本文件")
    return args


def _output_root() -> Path:
    """统一产物根目录:项目根(包上一级)下的 output/,与运行时 cwd 无关。"""
    return Path(__file__).resolve().parent.parent / "output"


def _default_work(video: Path) -> Path:
    return _output_root() / (video.stem + ".jpsub")


def _work_of(video: Path) -> Path:
    """视频所属工作目录:视频已在工作目录内(新布局)则为其所在目录,否则默认目录。"""
    return video.parent if video.parent.name.endswith(".jpsub") else _default_work(video)


def _ass_path(work: Path) -> Path:
    """ASS 路径:工作目录内,以视频名命名(新布局)。"""
    return work / (work.stem + ".ass")


def _find_ass(work: Path) -> Path:
    """找 ASS:新布局(工作目录内)优先,回退旧布局(工作目录旁)。"""
    p = _ass_path(work)
    if p.exists():
        return p
    return work.with_suffix(".ass")


def _find_video(work: Path) -> Path | None:
    """找视频:工作目录内(新布局)优先,回退旧布局(工作目录旁)。"""
    for base in (work, work.parent):
        for ext in (".mp4", ".mkv", ".webm"):
            v = base / (work.stem + ext)
            if v.exists():
                return v
    return None


def _product_out(video: Path, suffix: str) -> Path:
    """打码/烧录产物路径:工作目录内,以视频名命名。"""
    return _work_of(video) / (video.stem + suffix)


def _make_engine(args):
    """单进程 OCR;--ocr-workers 1 时走此路径。"""
    from .ocr import PaddleOcrEngine

    return PaddleOcrEngine()


def _ocr_one(engine, path: Path) -> str:
    return engine.run(path)


def _ocr_many(engine, paths: list[Path], quiet: bool = False) -> list[str]:
    out: list[str] = []
    n = len(paths)
    for i, p in enumerate(paths):
        out.append(_ocr_one(engine, p))
        if not quiet:
            print(f"\rOCR {i + 1}/{n}", end="", flush=True)
    if not quiet:
        print()
    return out


def _ocr_dedup(engine, items: list[tuple[bytes, Path]]) -> list[str]:
    """按文字掩膜去重:同一画面复用已识别文本,只为新画面跑推理。

    `items` 是 [(掩膜字节, 帧路径), ...],返回与 items 等长的文本列表。
    """
    keys = [k for k, _ in items]
    uniq: list[bytes] = []
    path_of: dict[bytes, Path] = {}
    for k, p in items:
        if k not in path_of:
            path_of[k] = p
            uniq.append(k)
    texts = _ocr_many(engine, [path_of[k] for k in uniq])
    saved = len(items) - len(uniq)
    if saved:
        print(f"重复画面去重:识别 {len(uniq)}/{len(items)} 帧,省 {saved} 次 OCR")
    mapping = dict(zip(uniq, texts))
    return [mapping[k] for k in keys]


def _select_keyframes(args, extract_dir: Path, quiet: bool = False):
    """抽帧 -> 文字掩膜 -> 停顿触发筛选关键帧。

    返回 (paths, spans, masks, cores, frame_dur, offset),供单视频与批量模式复用。
    """
    paths = frames.extract_frames(
        args.video,
        extract_dir,
        fps=args.fps,
        crop=args.crop,
        start=args.start,
        end=args.end,
        quiet=quiet,
    )
    if not paths:
        raise SystemExit("错误:没有抽到任何帧,检查视频与 --fps/--crop/--start/--end")
    frame_dur = 1.0 / args.fps
    offset = args.start or 0.0  # 时间轴对齐到原始视频
    if not quiet:
        print(f"抽到 {len(paths)} 帧 (crop={args.crop})")

    # 停顿触发选关键帧,短停顿合并,只识别每段静止末帧
    # cores:高门槛文字核心掩膜,换段判定不受亮背景噪声稀释
    masks, cores = frames.text_core_masks(paths, quiet=quiet)
    masks, cores = frames.strip_static(masks), frames.strip_static(cores)
    diffs = trigger.mask_diffs(masks)
    spans = trigger.select_keyframes(
        diffs,
        change_threshold=args.diff_threshold,
        settle_frames=args.settle_frames + 1,
        max_run=args.max_run,
        merge_short_pauses=True,
        masks=masks,
        cores=cores,
    )
    return paths, spans, masks, cores, frame_dur, offset


def _ocr_segments(
    args, extract_dir: Path, engine, work: Path | None = None
) -> tuple[list[segment.Segment], list[Path]]:
    """抽帧 -> 筛选 -> OCR。返回 (字幕段列表, 真正送入 OCR 的帧路径)。"""
    paths, spans, masks, cores, frame_dur, offset = _select_keyframes(args, extract_dir)
    key_idx = [k for _, _, k in spans]
    # 无字帧靠 predict 内部跳过 rec,空文本在 build_segments 里被丢弃,无需额外检测
    # OCR 缓存:已识别过的帧序号/文本存工作目录,重跑时直接复用
    ocr_cache_path = work / "ocr-cache.json" if work else None
    ocr_cache: dict[str, str] = {}
    if ocr_cache_path and ocr_cache_path.exists():
        try:
            ocr_cache = json.loads(ocr_cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            ocr_cache = {}
    todo = [
        (masks[k].tobytes(), paths[k])
        for k in key_idx
        if paths[k].name not in ocr_cache
    ]
    if len(todo) < len(key_idx):
        print(f"OCR 缓存:复用 {len(key_idx) - len(todo)} 帧,新识别 {len(todo)} 帧")
    fresh = _ocr_dedup(engine, todo) if todo else []
    for (mask, p), text in zip(todo, fresh):
        ocr_cache[p.name] = text
    if ocr_cache_path and todo:
        ocr_cache_path.write_text(
            json.dumps(ocr_cache, ensure_ascii=False), encoding="utf-8"
        )
    texts = [ocr_cache.get(paths[k].name, "") for k in key_idx]
    saved = 100 * (1 - len(key_idx) / max(1, len(paths)))
    print(f"停顿触发:识别 {len(key_idx)}/{len(paths)} 帧,省 {saved:.0f}% OCR")
    timed = []
    for (s, e, _k), text in zip(spans, texts):
        for i in range(s, e + 1):
            timed.append((offset + i * frame_dur, text))
    segs = segment.build_segments(timed, frame_dur=frame_dur, threshold=args.similarity)
    ocr_paths = [paths[k] for k in key_idx]

    print(f"合并为 {len(segs)} 条字幕")
    return segs, ocr_paths


def _backup(work: Path, *files: Path) -> None:
    """覆盖前把存在的文件备份到 work/backup/<时间戳>/,防止误操作。"""
    import shutil
    import time

    ts = time.strftime("%Y%m%d-%H%M%S")
    dest = work / "backup" / ts
    for f in files:
        if f.is_file():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest / f.name)


def _save_work(
    args, segs: list[segment.Segment], work: Path, *, fresh: bool = False
) -> Path:
    """把 OCR 段落写进工作目录(单视频与批量模式共用):

    - 旧 translate-out.txt 的译文一次性迁移进 segments(只补缺,不覆盖用户改动);
    - segments.json 覆盖前自动备份到 backup/<时间戳>/;
    - fresh=True(刚跑完 OCR)或没有原始备份时,同步写 segments.orig.json。

    GUI 化后不再产出 translate-in/out.txt,原文/译文统一以 segments.json 为准。
    """
    cache = TranslationCache(args.cache or work / "cache.json")
    handoff.import_out_to_segments(work, cache)  # 旧目录一次性迁移
    comment_path = work / "comment.txt"  # download --comment 写入的视频描述
    comment = (
        comment_path.read_text(encoding="utf-8").strip()
        if comment_path.exists()
        else None
    )
    seg_file = work / "segments.json"
    orig_file = work / handoff.ORIG_NAME
    if seg_file.exists() and not fresh:
        # 已有识别结果:同键同原文的段保留旧 tr 快照(重跑免重翻)
        old = {
            handoff.norm_key(handoff.make_key(s.start, s.end)): s
            for s in handoff.read_segments(seg_file)
        }
        for s in segs:
            o = old.get(handoff.norm_key(handoff.make_key(s.start, s.end)))
            if o and o.text == s.text and not handoff.is_untranslated(o.tr or ""):
                s.tr = o.tr
    _backup(work, seg_file, orig_file)  # 覆盖前备份
    handoff.write_segments(segs, seg_file, comment=comment)
    if fresh or not orig_file.exists():
        handoff.save_orig(segs, work)  # OCR 原始备份(调整器「还原改动」用)
    if not getattr(args, "batch", False):
        print(f"工作目录:{work}")
        todo = handoff.pending_texts(segs, cache)
        print(f"待翻译 {len(todo)} 句(共 {len(segs)} 段)")
        if todo:
            print("默认将自动调用 AI 翻译;加 --notrans 可停在此步")
    return work


def ocr_batch_stage(args, engine, work: Path, progress=None, quiet=False) -> Path:
    """批量模式阶段B:读取 _batch 元数据,OCR 关键帧,产出 segments.json。

    quiet=True 时不打印中间统计(缓存/去重/合并),由调用方统一展示。
    """

    def _say(msg: str):
        if not quiet:
            print(msg)

    bdir = work / "_batch"
    meta = json.loads((bdir / "meta.json").read_text(encoding="utf-8"))
    fps = meta["fps"]
    offset = meta["offset"]
    spans = [tuple(s) for s in meta["spans"]]
    frame_dur = 1.0 / fps
    frames = [bdir / f for f in meta["frames"]]
    hashes = meta["hashes"]

    ocr_cache_path = work / "ocr-cache.json"
    ocr_cache: dict[str, str] = {}
    if ocr_cache_path.exists():
        try:
            ocr_cache = json.loads(ocr_cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            ocr_cache = {}
    todo = [(h, p) for h, p in zip(hashes, frames) if p.name not in ocr_cache]
    if len(todo) < len(frames):
        _say(f"OCR 缓存:复用 {len(frames) - len(todo)} 帧,新识别 {len(todo)} 帧")
    # 按掩膜哈希去重:同一画面只识别一次
    uniq: dict[str, Path] = {}
    for h, p in todo:
        uniq.setdefault(h, p)
    # 逐帧识别,批量模式经 progress 上报实时进度
    uniq_paths = list(uniq.values())
    fresh: list[str] = []
    for i, p in enumerate(uniq_paths):
        fresh.append(_ocr_one(engine, p))
        if progress:
            progress(i + 1, len(uniq_paths))
    saved = len(todo) - len(uniq)
    if saved:
        _say(f"重复画面去重:识别 {len(uniq)}/{len(todo)} 帧,省 {saved} 次 OCR")
    text_of = dict(zip(uniq.keys(), fresh))
    for h, p in todo:
        ocr_cache[p.name] = text_of[h]
    if ocr_cache_path and todo:
        ocr_cache_path.write_text(
            json.dumps(ocr_cache, ensure_ascii=False), encoding="utf-8"
        )
    texts = [ocr_cache.get(p.name, "") for p in frames]

    timed = []
    for (s, e, _k), text in zip(spans, texts):
        for i in range(s, e + 1):
            timed.append((offset + i * frame_dur, text))
    segs = segment.build_segments(timed, frame_dur=frame_dur, threshold=args.similarity)
    _say(f"合并为 {len(segs)} 条字幕")
    work = _save_work(args, segs, work, fresh=True)
    shutil.rmtree(bdir, ignore_errors=True)
    return work


def _extract(args, engine=None) -> Path:
    work = args.work or _default_work(args.video)
    work.mkdir(parents=True, exist_ok=True)
    # --extract-only 或 --selected-only 任一出现:只抽帧不 OCR,并保留截图
    preview = args.extract_only or args.selected_only
    keep = work  # 预览模式下帧保存到工作目录
    extract_dir = Path(tempfile.mkdtemp(prefix="jpsub_"))
    try:
        if preview:
            all_frames = frames.extract_frames(
                args.video,
                extract_dir,
                fps=args.fps,
                crop=args.crop,
                start=args.start,
                end=args.end,
            )
            print(f"抽到 {len(all_frames)} 帧 (仅抽帧不 OCR)")
            if args.selected_only:
                # 统一用停顿触发筛选:只保留每段打字完成后的静止末帧
                masks, cores = frames.text_core_masks(all_frames)
                masks, cores = frames.strip_static(masks), frames.strip_static(cores)
                diffs = trigger.mask_diffs(masks)
                # settle_frames+1:停顿必须"超过"阈值才收尾,恰好等于阈值的
                # 短停顿也会走合并逻辑,避免打字中途同长度停顿切出多余关键帧
                spans = trigger.select_keyframes(
                    diffs,
                    change_threshold=args.diff_threshold,
                    settle_frames=args.settle_frames + 1,
                    max_run=args.max_run,
                    merge_short_pauses=True,
                    masks=masks,
                    cores=cores,
                )
                saved_frames = [all_frames[sp[2]] for sp in spans]
                print(f"筛选出 {len(saved_frames)}/{len(all_frames)} 帧")
            else:
                saved_frames = all_frames
            keep.mkdir(parents=True, exist_ok=True)
            for stale in keep.glob("frame_*.jpg"):
                stale.unlink()
            for fp in saved_frames:
                shutil.copy2(fp, keep / fp.name)
            print(f"保留 {len(saved_frames)} 帧 -> {keep}")
        else:
            seg_file = work / "segments.json"
            if seg_file.exists() and not getattr(args, "force", False):
                # 已有识别结果:跳过抽帧/OCR,直接沿用(--force 可强制重跑)
                print(f"已有 {seg_file},跳过 OCR(--force 可强制重跑)")
                segs = handoff.read_segments(seg_file)
                return _save_work(args, segs, work, fresh=False)
            if engine is None:
                engine = _make_engine(args)
            segs, _ = _ocr_segments(args, extract_dir, engine, work=work)
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)

    if preview:
        print(f"仅抽帧完成 -> {keep}")
        return work

    return _save_work(args, segs, work, fresh=True)


def _load_meta(work: Path) -> dict:
    """读翻译元信息(comment):优先 segments.json,旧目录回退 pending.json。"""
    meta = handoff.read_meta(work / "segments.json")
    if meta.get("comment") is None:
        legacy = work / "pending.json"
        if legacy.exists():
            return json.loads(legacy.read_text(encoding="utf-8"))
    return meta


def _render(args) -> Path:
    work = args.work
    seg_file = work / "segments.json"
    cache = TranslationCache(args.cache or work / "cache.json")
    handoff.import_out_to_segments(work, cache)  # 旧目录一次性迁移(有 out 才生效)
    segs = handoff.read_segments(seg_file)
    out = args.output or _ass_path(work)
    tr_map = handoff.resolve(segs, cache)
    if not tr_map:
        # 完全没有译文:用日文原文生成字幕
        if not getattr(args, "batch", False):
            print("没有译文,改用日文原文生成字幕")
        tr_map = {s.text: s.text for s in segs if s.text}
    ass.write_ass(
        segs,
        tr_map,
        out,
        font=args.font,
        font_size=args.font_size,
        max_chars=args.max_chars,
    )
    if not getattr(args, "batch", False):
        print(f"完成:{out}")
    return out


def _load_glossary(work: Path, args) -> dict[str, str]:
    """加载名词对照表:全局 ~/.jpsub/glossary.txt + 工作目录 glossary.txt,
    --glossary 指定的文件优先级最高(后加载覆盖同名词条)。"""
    paths = [Path.home() / ".jpsub" / "glossary.txt", work / "glossary.txt"]
    custom = getattr(args, "glossary", None)
    if custom:
        paths.append(custom)
    gloss: dict[str, str] = {}
    for p in paths:
        if not p.exists():
            continue
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            k, _, v = ln.partition("\t")
            k, v = k.strip(), v.strip()
            if k and v:
                gloss[k] = v
    return gloss


def _translate(args) -> Path:
    """翻译工作目录里的 segments.json:待翻句子发给 AI,译文写回段的 tr 并落盘。"""
    quiet = getattr(args, "batch", False)
    work = args.work
    seg_file = work / "segments.json"
    if not seg_file.exists():
        raise SystemExit(f"错误:找不到 {seg_file},先运行 extract")
    cache = TranslationCache(args.cache or work / "cache.json")
    handoff.import_out_to_segments(work, cache)  # 旧目录一次性迁移
    segs = handoff.read_segments(seg_file)
    force = getattr(args, "force", False)
    if force:
        _backup(work, seg_file)  # 重翻会覆盖全部译文,先备份
        texts = list(dict.fromkeys(s.text for s in segs if s.text))
    else:
        texts = handoff.pending_texts(segs, cache)
    if not texts:
        if not quiet:
            print("没有待翻译的句子(全部已译,--force 可强制重翻)")
        return seg_file
    items = [(t, t) for t in texts]  # 键即原文,译文按原文回填各段
    _backup(work, seg_file)  # 翻译结果会写回 segments,先备份
    cfg = ai.resolve_config(args)
    comment = getattr(args, "comment", None)  # 命令行指定优先
    if comment is None:
        comment = _load_meta(work).get("comment")
    glossary = _load_glossary(work, args)
    if glossary and not quiet:
        print(f"名词对照表:{len(glossary)} 条")

    acc: dict[str, str] = {}  # 每批累积的部分结果:中断时保留已完成部分

    def _persist() -> None:
        """把累积译文写回 segments(含 [[未译]] 占位)与缓存并落盘。"""
        if not acc:
            return
        n = 0
        for s in segs:
            tr = acc.get(s.text)
            if tr is None:
                continue
            s.tr = tr  # 含未译占位:调整器里橙色显示,可重翻
            if not handoff.is_untranslated(tr):
                cache.put(s.text, tr)
                n += 1
        cache.save()
        handoff.write_segments(
            segs, seg_file, comment=_load_meta(work).get("comment")
        )
        if not quiet:
            print(f"译文已写回 {seg_file}(新翻译 {n} 句)")

    def _run(bs: int, todo: list[tuple[str, str]]):
        return ai.translate_texts(
            todo,
            cfg,
            batch_size=bs,
            comment=comment,
            glossary=glossary or None,
            progress=getattr(args, "tr_progress", None),
            quiet=quiet,
            on_batch=acc.update,
        )

    try:
        _run(args.batch_size, items)
    except ai.CensoredError as e:
        # 单句敏感会连累整批:停止本轮,改用逐句重跑(已译部分自动跳过)
        if not quiet:
            print(f"\n内容审查拦截:{e}\n改用 batch_size=1 逐句重跑...")
        _persist()
        acc.clear()
        # 逐句重跑全部待翻句(已译部分已随 _persist 落盘,未落盘的重发)
        _run(1, items)
    except RuntimeError as e:
        if "额度不足" in str(e):
            _persist()  # 已翻译部分先落盘,充值后可续翻
            raise SystemExit(f"\n提醒:{e}(已翻译部分已写回 segments,可充值后续翻)") from None
        raise
    _persist()
    return seg_file


def _has_pending(work: Path) -> bool:
    """segments.json 是否有未翻译的句子。"""
    p = work / "segments.json"
    if not p.exists():
        return False
    segs = handoff.read_segments(p)
    cache = TranslationCache(work / "cache.json")
    return bool(handoff.pending_texts(segs, cache))


def _maybe_translate_render(args, work: Path) -> Path:
    """默认流程:有新句则 AI 翻译,再渲染 ASS;--notrans 或预览模式停在抽帧/清单。"""
    if (
        getattr(args, "notrans", False)
        or getattr(args, "extract_only", False)
        or getattr(args, "selected_only", False)
    ):
        return work
    args.work = work
    if _has_pending(work):
        _translate(args)
    if getattr(args, "batch", False):
        # 批量模式无人值守:照旧直接渲染
        args.output = getattr(args, "output", None) or _ass_path(work)
        return _render(args)
    if getattr(args, "burn", False):
        out = _render(args)
        return _burn(args.video, out)
    # 单视频:不自动渲染,打开浏览器译文调整器,由用户确认后点「生成字幕」
    from .adjust import editor

    print("翻译完成,正在打开译文调整器……确认无误后点「生成字幕」输出 ASS(Ctrl+C 跳过)")
    try:
        editor(work)
    except KeyboardInterrupt:
        print("已跳过,之后可用 jpsub edit 或 jpsub render 继续")
    return work


def _burn(video: Path, ass_path: Path) -> Path:
    """用 ffmpeg 把 ASS 字幕烧录进视频,输出工作目录内 <视频名>.burned.mp4。"""
    import subprocess

    out = _product_out(video, ".burned.mp4")
    # filter 级双重转义(filtergraph 层 + 选项层):反斜杠、引号、分隔符都要转
    escaped = str(ass_path)
    for _ in range(2):
        for ch, rep in (
            ("\\", "\\\\"),
            ("'", "\\'"),
            (":", "\\:"),
            (",", "\\,"),
            (";", "\\;"),
            ("[", "\\["),
            ("]", "\\]"),
        ):
            escaped = escaped.replace(ch, rep)
    cmd = [
        settings.binary("ffmpeg"),
        "-y",
        "-i",
        str(video),
        "-vf",
        f"subtitles={escaped}",
        "-c:a",
        "copy",
        str(out),
    ]
    print(f"烧录字幕:{video} -> {out}")
    subprocess.run(cmd, check=True)
    print(f"完成:{out}")
    return out


def _download(args) -> Path:
    from . import download as dl
    from .download import download

    if args.cookies_from_browser:  # 命令行优先于 settings.py
        dl.settings.COOKIES_FROM_BROWSER = args.cookies_from_browser
    args.output = args.output or _output_root()
    video = download(args.url, args.output, comment=args.comment)
    args.video = video
    work = _work_of(video)  # 新布局视频已在工作目录内
    # 已有 ASS 则跳过流水线直接烧录(新旧布局都认)
    ass = _find_ass(work)
    if getattr(args, "burn", False) and ass.exists():
        return _burn(video, ass)
    # 完整流水线:extract -> translate -> render(ASS 存工作目录内,以视频名命名)
    if not hasattr(args, "work"):  # download 子命令没有 --work 参数
        args.work = None  # 让 _extract 用默认 <视频>.jpsub
    work = _extract(args)
    args.output = _ass_path(work)
    return _maybe_translate_render(args, work)


def _status(args) -> None:
    work = args.work
    if not (work / "segments.json").exists():
        raise SystemExit(f"错误:找不到 {work / 'segments.json'}")
    segs = handoff.read_segments(work / "segments.json")
    cache = TranslationCache(args.cache or work / "cache.json")
    total = len({s.text for s in segs if s.text})
    missing = len(handoff.pending_texts(segs, cache))
    print(
        f"段数:{len(segs)}  唯一句:{total}  已有译文:{total - missing}  待翻译:{missing}"
    )


def run(
    argv: list[str] | argparse.Namespace | None = None, *, engine=None
) -> Path | None:
    """程序入口;engine 可注入用于测试。argv 既可以是命令行参数列表,也可以是已解析的 Namespace。"""
    import sys

    if argv is None:
        argv = sys.argv[1:]
    # 裸 `jpsub`(无参数):打开浏览器主页
    if isinstance(argv, list) and not argv:
        from .home import home_page

        return home_page()
    # 允许省略子命令:`jpsub <url或视频id>` 直接视为下载
    if isinstance(argv, list) and argv and not argv[0].startswith("-"):
        from pathlib import Path as _Path

        from .download import extract_video_id

        if _Path(argv[0]).exists():
            # 本地文件(如 sm123.mp4)优先视为 run 的输入,避免被误当视频 id 走下载
            argv = ["run", *argv]
        elif extract_video_id(argv[0]):
            argv = ["download", *argv]
    args = argv if isinstance(argv, argparse.Namespace) else parse_args(argv)
    if getattr(args, "script", None):
        from .batch import run_script

        run_script(args)
        return None
    if args.command == "download":
        return _download(args)
    if args.command == "extract":
        work = _extract(args, engine=engine)
        return _maybe_translate_render(args, work)
    if args.command == "render":
        return _render(args)
    if args.command == "translate":
        return _translate(args)
    if args.command == "status":
        _status(args)
        return None
    if args.command == "mask":
        from .mask import picker

        picker(args.video, args.masks)
        return None
    if args.command == "maskapply":
        from .mask import apply_masks, default_masks_path

        masks = args.masks or default_masks_path(args.video)
        if not masks.exists() and masks.suffix == ".json":
            old = masks.with_suffix(".txt")
            if old.exists():
                masks = old  # 兼容旧版 masks.txt
        out = args.output or _product_out(args.video, ".masked.mp4")
        apply_masks(args.video, masks, out)
        if args.burn:  # 链式:打码完成后接着烧字幕
            ass = _find_ass(_work_of(args.video))
            if ass.exists():
                _burn(out, ass)
            else:
                print(f"提示:未找到字幕 {ass},跳过烧录,只输出打码视频")
        return None
    if args.command == "edit":
        from .adjust import editor

        return editor(args.target)
    if args.command == "home":
        from .home import home_page

        return home_page(args.root)
    # run = extract + (默认)translate + render
    work = args.work or _default_work(args.video)
    # --burn 且已有 ASS:跳过流水线直接烧录(--force 时强制重跑)
    if getattr(args, "burn", False) and not getattr(args, "force", False):
        ass_path = getattr(args, "output", None) or _find_ass(work)
        if ass_path.exists():
            print(f"已有字幕 {ass_path},跳过流水线直接烧录")
            return _burn(args.video, ass_path)
    work = _extract(args, engine=engine)
    return _maybe_translate_render(args, work)


def main(argv: list[str] | None = None) -> None:
    settings.apply_proxy()  # settings.json 里的 proxy 用于 pip/模型下载
    run(argv)


if __name__ == "__main__":
    main()
