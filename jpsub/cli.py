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
    sub = p.add_subparsers(dest="command", required=True)

    def add_pipeline(a):
        a.add_argument("--fps", type=float, default=settings.FPS)
        a.add_argument(
            "--crop",
            type=float,
            default=settings.CROP,
            help="截取视频底部高度的比例",
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
            "--no-index", action="store_true", help="translate-in.txt 不写编号"
        )
        a.add_argument(
            "--extract-only",
            action="store_true",
            help="只抽帧不 OCR,用于检查抽帧质量",
        )
        a.add_argument(
            "--notrans",
            action="store_true",
            help="只到产出 translate-in.txt 为止,不自动翻译不渲染",
        )

    def add_api(a):
        a.add_argument(
            "--api-base", help="OpenAI 兼容端点,默认 $JPSUB_API_BASE/$OPENAI_BASE_URL"
        )
        a.add_argument("--api-key", help="默认 $JPSUB_API_KEY/$OPENAI_API_KEY")
        a.add_argument("--model", help="模型名,默认 $JPSUB_MODEL")
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

    e = sub.add_parser("extract", help="抽帧/OCR/合并,产出待翻译文件")
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
    r.add_argument("--cache", type=Path, default=None, help="默认 <工作目录>/cache.json")
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
    s.add_argument("--cache", type=Path, default=None, help="默认 <工作目录>/cache.json")

    t = sub.add_parser("translate", help="调用 AI API 翻译 translate-in.txt(可续翻)")
    t.add_argument("work", type=Path, help="工作目录(含 translate-in.txt)")
    t.add_argument(
        "--comment",
        help="视频描述(如 剧场类型/背景设定),引导 AI 翻译时参考;默认读工作目录 comment.txt",
    )
    add_api(t)

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
    return p.parse_args(argv)


def _output_root() -> Path:
    """统一产物根目录:项目根(包上一级)下的 output/,与运行时 cwd 无关。"""
    return Path(__file__).resolve().parent.parent / "output"


def _default_work(video: Path) -> Path:
    return _output_root() / (video.stem + ".jpsub")


def _make_engine(args):
    """单进程 OCR;--ocr-workers 1 时走此路径。"""
    from .ocr import PaddleOcrEngine

    return PaddleOcrEngine()


def _ocr_one(engine, path: Path) -> str:
    return engine.run(path)


def _ocr_many(engine, paths: list[Path]) -> list[str]:
    if hasattr(engine, "run_many"):
        return engine.run_many(paths)
    return [_ocr_one(engine, p) for p in paths]


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


def _ocr_segments(
    args, extract_dir: Path, engine
) -> tuple[list[segment.Segment], list[Path]]:
    """抽帧 -> 筛选 -> OCR。返回 (字幕段列表, 真正送入 OCR 的帧路径)。"""
    paths = frames.extract_frames(
        args.video,
        extract_dir,
        fps=args.fps,
        crop_ratio=args.crop,
        start=args.start,
        end=args.end,
    )
    if not paths:
        raise SystemExit("错误:没有抽到任何帧,检查视频与 --fps/--crop/--start/--end")
    frame_dur = 1.0 / args.fps
    offset = args.start or 0.0  # 时间轴对齐到原始视频
    print(f"抽到 {len(paths)} 帧 (crop={args.crop})")

    # 停顿触发选关键帧,短停顿合并,只识别每段静止末帧
    masks = frames.strip_static(frames.text_masks(paths))
    diffs = trigger.mask_diffs(masks)
    spans = trigger.select_keyframes(
        diffs,
        change_threshold=args.diff_threshold,
        settle_frames=args.settle_frames + 1,
        max_run=args.max_run,
        merge_short_pauses=True,
        masks=masks,
    )
    key_idx = [k for _, _, k in spans]
    # 无字帧靠 predict 内部跳过 rec,空文本在 build_segments 里被丢弃,无需额外检测
    texts = _ocr_dedup(engine, [(masks[k].tobytes(), paths[k]) for k in key_idx])
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
                crop_ratio=args.crop,
                start=args.start,
                end=args.end,
            )
            print(f"抽到 {len(all_frames)} 帧 (仅抽帧不 OCR)")
            if args.selected_only:
                # 统一用停顿触发筛选:只保留每段打字完成后的静止末帧
                masks = frames.strip_static(frames.text_masks(all_frames))
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
                )
                # 无字校验:先掩膜化(白字黑底,已剔除静态水印)再检测,
                # 低亮度水印/背景纹理不会误触发检测框
                if settings.OCR_NOTEXT_FILTER:
                    flt = ocr.NoTextFilter()
                    saved_frames = []
                    for sp in spans:
                        masked = masks[sp[2]].resize(
                            (512, 128), Image.Resampling.NEAREST
                        )
                        if flt.has_text_image(masked):
                            saved_frames.append(all_frames[sp[2]])
                    dropped = len(spans) - len(saved_frames)
                    if dropped:
                        print(f"无字校验:跳过 {dropped} 个无字幕帧")
                else:
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
            if engine is None:
                engine = _make_engine(args)
            segs, _ = _ocr_segments(args, extract_dir, engine)
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)

    if preview:
        print(f"仅抽帧完成 -> {keep}")
        return work

    cache = TranslationCache(args.cache or work / "cache.json")
    pending = handoff.export_pending(
        segs, cache, work / "translate-in.txt", with_index=not args.no_index
    )
    comment_path = work / "comment.txt"  # download --comment 写入的视频描述
    handoff.write_segments(
        segs,
        work / "segments.json",
        comment=comment_path.read_text(encoding="utf-8").strip()
        if comment_path.exists()
        else None,
    )
    print(f"工作目录:{work}")
    print(f"待翻译 {len(pending)} 句 -> {work / 'translate-in.txt'}")
    if pending:
        print("默认将自动调用 AI 翻译并渲染;加 --notrans 可停在此步")
    return work


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
    segs = handoff.read_segments(work / "segments.json")
    # pending 快照从 translate-in.txt 反推(它就是导出时的原文顺序);
    # 实在没有(旧目录且被清理)才回退 pending.json
    in_path = work / "translate-in.txt"
    pending = (
        handoff.pending_from_in(in_path)
        if in_path.exists()
        else _load_meta(work).get("pending", [])
    )
    cache = TranslationCache(args.cache or work / "cache.json")

    out_file = work / "translate-out.txt"
    if out_file.exists():
        count, deleted = handoff.import_translations(out_file, cache, pending)
        print(f"导入译文 {count} 条")
        if deleted:
            print(f"检测到 {len(deleted)} 行被删除,对应字幕将移除")
            deleted_set = set(deleted)
            segs = [s for s in segs if s.text not in deleted_set]
    out = args.output or work.with_suffix(".ass")
    ass.write_ass(
        segs,
        handoff.resolve(segs, cache),
        out,
        font=args.font,
        font_size=args.font_size,
        max_chars=args.max_chars,
    )
    print(f"完成:{out}")
    return out


def _translate(args) -> Path:
    in_path = args.work / "translate-in.txt"
    if not in_path.exists():
        raise SystemExit(f"错误:找不到 {in_path}")
    cfg = ai.resolve_config(args)
    out_path = args.work / "translate-out.txt"
    comment = getattr(args, "comment", None)  # 命令行指定优先
    if comment is None:
        comment = _load_meta(args.work).get("comment")
    n = ai.translate_file(
        in_path, out_path, cfg, batch_size=args.batch_size, comment=comment
    )
    print(f"完成:{out_path}(新翻译 {n} 句)")
    return out_path


def _has_pending(work: Path) -> bool:
    """translate-in.txt 是否有未翻译的句子。"""
    p = work / "translate-in.txt"
    return p.exists() and any(
        ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
    )


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
    args.output = getattr(args, "output", None) or work.with_suffix(".ass")
    out = _render(args)
    if getattr(args, "burn", False):
        return _burn(args.video, out)
    return out


def _burn(video: Path, ass_path: Path) -> Path:
    """用 ffmpeg 把 ASS 字幕烧录进视频,输出 <视频名>.burned.mp4。"""
    import subprocess

    out = _output_root() / (video.stem + ".burned.mp4")
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
    # 已有 ASS 则跳过流水线直接烧录
    if getattr(args, "burn", False) and video.with_suffix(".ass").exists():
        return _burn(video, video.with_suffix(".ass"))
    # 完整流水线:extract -> translate -> render(ASS 与视频同目录同名)
    if not hasattr(args, "work"):  # download 子命令没有 --work 参数
        args.work = None  # 让 _extract 用默认 <视频>.jpsub
    work = _extract(args)
    args.output = video.with_suffix(".ass")
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
    # 允许省略子命令:`jpsub <url或视频id>` 直接视为下载
    if isinstance(argv, list) and argv and not argv[0].startswith("-"):
        from .download import extract_video_id

        if extract_video_id(argv[0]):
            argv = ["download", *argv]
    args = argv if isinstance(argv, argparse.Namespace) else parse_args(argv)
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
    # run = extract + (默认)translate + render
    work = args.work or _default_work(args.video)
    # --burn 且已有 ASS:跳过流水线直接烧录
    if getattr(args, "burn", False):
        ass_path = getattr(args, "output", None) or work.with_suffix(".ass")
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
