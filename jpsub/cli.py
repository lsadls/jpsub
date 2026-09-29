"""命令行入口:抽帧 -> OCR -> 合并 -> (导出/导入) -> ASS。程序零网络。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
from pathlib import Path

from PIL import Image

from . import ai, ass, frames, handoff, ocr, segment, settings, trigger, utils
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
        def _crop_preset(v: str) -> str:
            """--crop 快捷预设:命中 settings.CROP_PRESETS 的键时替换为预设值。"""
            presets = settings.CROP_PRESETS or {}
            return presets.get(str(v).strip(), v)

        a.add_argument("--fps", type=float, default=settings.FPS)
        a.add_argument(
            "--crop",
            type=_crop_preset,
            default=settings.CROP,
            help="字幕区裁剪:单数字=底部占比(如 0.25),或 上:下:左:右 四边距(如 0.75:0.03:0.03:0.03);"
            "也可用 settings.CROP_PRESETS 里的预设键(如 --crop 1)",
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
            "--ocr-boost",
            action="store_true",
            help="OCR 文字凸显预处理(特殊画面:火焰等亮暖背景浅白字整行漏读时用)",
        )
        a.add_argument(
            "--selected-only",
            action="store_true",
            help="只抽帧并筛选,不 OCR;筛选帧保存到 -o/--work 工作目录",
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
            "--download-only",
            action="store_true",
            help="(仅 download)只下载视频,不抽帧不 OCR",
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
            help="名词对照表文件(默认读 ~/.jpsub/glossary.txt、工作目录 glossary.txt 与 settings.GLOSSARY_FILE)",
        )
        a.add_argument(
            "--batch-size",
            type=int,
            default=settings.BATCH_SIZE,
            help="每次请求翻译的句数",
        )
        a.add_argument(
            "--src-lang",
            default=None,
            help=f"源语言(默认 settings.SOURCE_LANG={settings.SOURCE_LANG!r},用百度简写)",
        )
        a.add_argument(
            "--to-lang",
            default=None,
            help=f"目的语言(默认 settings.TARGET_LANG={settings.TARGET_LANG!r})",
        )
        a.add_argument(
            "--prompt",
            type=Path,
            default=None,
            help="翻译提示词文件(默认 settings.PROMPT_FILE,内置默认);支持 {src}/{tgt}/{punct} 占位",
        )

    def add_style(a):
        a.add_argument("--font", default=settings.FONT)
        a.add_argument("--font-size", type=int, default=settings.FONT_SIZE)

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
        "--bcc", action="store_true",
        help="输出必剪 .bcc(JSON)而不是 .ass(不做自动折行)",
    )
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
        "--long",
        "--hard",
        action="store_true",
        dest="long",
        help="把每条原文按「。」拆成单句逐句翻译后拼回,单句有问题不连累整条",
    )
    t.add_argument(
        "--cache", type=Path, default=None, help="默认 <工作目录>/cache.json"
    )
    add_api(t)

    x = sub.add_parser("text", help="翻译任意文本文件(逐行发给 AI,保留空行)")
    x.add_argument("file", type=Path, help="要翻译的文本文件(UTF-8)")
    x.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="输出文件(默认 <原文件名>.zh.txt)",
    )
    x.add_argument(
        "--comment",
        help="内容背景描述,引导 AI 翻译时参考",
    )
    x.add_argument(
        "--long",
        "--hard",
        action="store_true",
        dest="long",
        help="把每行按「。」拆成单句逐句翻译后拼回,单句有问题不连累整行",
    )
    add_api(x)

    m = sub.add_parser("mask", help="打码选取器:鼠标框选区域,生成 masks.json")
    m.add_argument("video", type=Path)
    m.add_argument(
        "--masks",
        type=Path,
        default=None,
        help="打码清单(默认 <视频工作目录>/masks.json)",
    )
    m.add_argument(
        "--auto",
        action="store_true",
        help="自动去人:用肤色+像素分布检测真人肤色区域,结果写到 <工作目录>/.jpsub/masks.auto.json(不进入选取器、不改手工 masks.json)",
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

    v = sub.add_parser(
        "voice",
        help="朗读视频时间轴:按音量变化(静音检测)切出语音段,产出 segments.json(文本留空)",
    )
    v.add_argument("video", type=Path)
    v.add_argument("-o", "--work", type=Path, help="工作目录(默认 <视频>.jpsub)")
    v.add_argument(
        "--noise",
        default=None,
        help="静音判定音量阈值(如 -30dB);默认自适应(Otsu 分析音量分布)",
    )
    v.add_argument(
        "--min-silence",
        type=float,
        default=0.6,
        help="静音至少持续多少秒才算段落间隔(默认 0.6)",
    )
    v.add_argument(
        "--pad", type=float, default=0.2, help="每段两侧扩展的缓冲秒数(默认 0.2)"
    )
    v.add_argument(
        "--hyst",
        type=float,
        default=10.0,
        help="滞回带宽 dB:语音退到(阈值-hyst)以下才算静音开始,防软朗读被切碎(默认 10)",
    )
    v.add_argument("--start", type=parse_time, default=None, help="只处理该时刻之后")
    v.add_argument("--end", type=parse_time, default=None, help="只处理该时刻之前")
    v.add_argument(
        "--force", action="store_true", help="已有 segments.json 时强制重新生成"
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

    up = sub.add_parser("upload", help="把工作目录成品投稿到 B 站(biliup)")
    up.add_argument("work", type=Path, help="工作目录(含视频/info.txt/cover.jpg)")
    up.add_argument("--title", help="稿件标题(默认 info.txt 原标题的 AI 译文)")
    up.add_argument("--desc", help="稿件简介(默认 info.txt 组装)")
    up.add_argument("--tags", help="逗号分隔标签(默认 settings.UPLOAD_TAGS)")
    up.add_argument("--tid", type=int, help="分区 tid(默认 settings.UPLOAD_TID)")
    up.add_argument(
        "--delay", type=int, help="定时发布:从现在起延迟秒数(默认 settings.UPLOAD_DELAY)"
    )
    up.add_argument("--login", action="store_true", help="扫码登录(biliup-rs),保存 cookies")

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
        "-v",
        "--video-quality",
        default=None,
        help="视频画质:lowest/best/360p/480p/720p(默认取 settings.NICO_VIDEO_QUALITY)",
    )
    d.add_argument(
        "-a",
        "--audio-quality",
        default=None,
        choices=("lowest", "best"),
        help="音质:lowest/best(默认取 settings.NICO_AUDIO_QUALITY)",
    )
    d.add_argument(
        "--comment",
        help="视频描述(如 剧场类型/背景设定),引导 AI 翻译时参考",
    )
    d.add_argument(
        "--long",
        "--hard",
        action="store_true",
        dest="long",
        help="把每条原文按「。」拆成单句逐句翻译后拼回,单句有问题不连累整条",
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
    return utils.output_root()


def _default_work(video: Path) -> Path:
    """默认工作目录:output/ 下以视频名新建。"""
    return utils.default_work(video)


def _work_of(video: Path) -> Path:
    """视频所属工作目录:视频已在工作目录内则为其所在目录,否则默认目录。"""
    return utils.work_of(video)


def _video_arg(video: Path) -> Path:
    """mask/maskapply 的视频参数:允许传工作目录或裸条目名,自动定位其中的视频
    (ffprobe 传目录会直接报错)。"""
    if video.is_file():
        return video
    for cand in (video, _output_root() / video.name):
        if cand.is_dir():
            v = utils.find_video(cand)
            if v:
                return v
    return video  # 原样返回,让后续报错信息可读


def _ass_path(work: Path) -> Path:
    """ASS 路径:工作目录内,以条目名命名(去掉尾部视频 id 如 _sm29889006)。"""
    stem = re.sub(r"_[a-z]{2}\d+$", "", handoff.item_stem(work))
    return work / (stem + ".ass")


def _find_ass(work: Path) -> Path:
    """找 ASS:工作目录内。"""
    return _ass_path(work)


def _find_video(work: Path) -> Path | None:
    """找视频:工作目录内(新布局)。"""
    return utils.find_video(work)


def _product_out(video: Path, suffix: str) -> Path:
    """打码/烧录产物路径:工作目录内,以视频名命名。"""
    return _work_of(video) / (video.stem + suffix)


def _read_crop(work: Path) -> str | None:
    """读工作目录里保存的字幕区参数(crop.json {"crop": "上:下:左:右"}),无则 None。"""
    f = handoff.crop_path(work)
    if not f.is_file():
        return None
    try:
        v = json.loads(f.read_text(encoding="utf-8")).get("crop")
        return str(v) if v else None
    except Exception:  # noqa: BLE001
        return None


def apply_work_crop(ns) -> None:
    """抽帧前生效字幕区:--crop 未显式指定(仍等于默认)时,优先级为
    工作目录 crop.json > tag 匹配 settings.TAG_CROP(标签含关键词即命中)。"""
    work = getattr(ns, "work", None) or _work_of(Path(ns.video))
    if ns.crop != str(settings.CROP):
        return  # 命令行显式指定的 crop 优先
    wc = _read_crop(work)
    if wc:
        ns.crop = wc
        return
    tags = _load_tags(work)
    if not tags:
        return
    presets = settings.CROP_PRESETS or {}
    for key, val in (settings.TAG_CROP or {}).items():
        if key in tags:
            ns.crop = presets.get(val, val)
            return


def _make_engine(args):
    """单进程 OCR;--ocr-workers 1 时走此路径。"""
    from .ocr import make_engine

    return make_engine()


def _ocr_one(engine, path: Path) -> str:
    return engine.run(path)


def _ocr_many(engine, paths: list[Path], quiet: bool = False, on_item=None) -> list[str]:
    from . import ocr as _ocr_mod
    from .progress import Reporter

    rep = Reporter(quiet=quiet)
    n = len(paths)
    if hasattr(engine, "run_many"):  # 支持多帧合并识别(按次计费省额度)
        out = engine.run_many(
            paths,
            on_item=lambda i, t: (
                rep.line(f"OCR {min(i + 1, n)}/{n}"),
                on_item(i, t) if on_item else None,
            ),
        )
        rep.close()
        if _ocr_mod.REQUESTS:
            print(f"OCR 消耗 {_ocr_mod.REQUESTS} 次请求(百度月额度 3500 次/月)")
        return out
    out: list[str] = []
    for i, p in enumerate(paths):
        out.append(_ocr_one(engine, p))
        if on_item:
            on_item(i, out[-1])
        rep.line(f"OCR {i + 1}/{n}")
    rep.close()
    if _ocr_mod.REQUESTS:
        print(f"OCR 消耗 {_ocr_mod.REQUESTS} 次请求(百度月额度 3500 次/月)")
    return out


def _ocr_dedup(engine, items: list[tuple[bytes, Path]], on_text=None) -> list[str]:
    """按文字掩膜去重:同一画面复用已识别文本,只为新画面跑推理。

    `items` 是 [(掩膜字节, 帧路径), ...],返回与 items 等长的文本列表。
    on_text(帧路径, 文本) 每识别完一帧回调一次,供上层增量落盘缓存。

    粗掩膜可能把不同画面压成同一字节(如深底居中暗字的说明卡,暗字被
    亮度门槛剔除后只剩相同的人物轮廓),故对掩膜碰撞组再用未二值化的
    局部对比指纹(text_residual_fp)复核,避免把 A 帧文本错配给 B 帧。
    """
    coarse_n: dict[bytes, int] = {}
    for k, _ in items:
        coarse_n[k] = coarse_n.get(k, 0) + 1
    fp_cache: dict[Path, bytes] = {}

    def final_key(k: bytes, p: Path) -> bytes:
        if coarse_n[k] == 1:
            return k
        if p not in fp_cache:
            fp_cache[p] = frames.text_residual_fp(Image.open(p))
        return k + b"\x00" + fp_cache[p]

    keys = [final_key(k, p) for k, p in items]
    uniq: list[bytes] = []
    path_of: dict[bytes, Path] = {}
    for k, (_, p) in zip(keys, items):
        if k not in path_of:
            path_of[k] = p
            uniq.append(k)
    texts = _ocr_many(
        engine,
        [path_of[k] for k in uniq],
        on_item=lambda i, t: on_text(path_of[uniq[i]], t) if on_text else None,
    )
    saved = len(items) - len(uniq)
    if saved:
        print(f"重复画面去重:识别 {len(uniq)}/{len(items)} 帧,省 {saved} 次 OCR")
    mapping = dict(zip(uniq, texts))
    return [mapping[k] for k in keys]


def _select_keyframes(args, extract_dir: Path, quiet: bool = False):
    """抽帧 -> 文字掩膜 -> 停顿触发筛选关键帧。

    返回 (paths, spans, masks, cores, frame_dur, offset),供单视频与批量模式复用。
    """
    apply_work_crop(args)  # 工作目录里保存的字幕区参数优先(--crop 未显式指定时)
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


_NO_TEXT = "\x00"  # 缓存哨兵:确认识别过且无字;空串/缺失 = 未完成,续跑时重 OCR


def _cache_val(text: str) -> str:
    """写入缓存:空文本换成哨兵,与「未识别」区分开。"""
    return text if text else _NO_TEXT


def _cache_text(val: str) -> str:
    """读出缓存:哨兵还原为空文本。"""
    return "" if val == _NO_TEXT else val


def _ocr_cache_meta() -> dict:
    """OCR 缓存元数据:引擎/语言变化时旧缓存整体作废(如把 CHN_ENG 改成 JAP)。"""
    return {
        "v": 2,  # v2:空文本存 _NO_TEXT 哨兵;v1 的空串是真实无字,加载时迁移
        "provider": settings.OCR_PROVIDER,
        "language": settings.ocr_language(),
        "upscale": bool(settings.OCR_UPSCALE),
        "model": settings.OCR_MODEL,
    }


def _load_ocr_cache(path: Path | None, force: bool) -> dict:
    """读工作目录 OCR 缓存;--force 忽略;元数据(引擎/语言)不匹配则全部作废。"""
    if not path or force or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    meta = data.get("__meta__") if isinstance(data, dict) else None
    if not isinstance(meta, dict) or {
        k: v for k, v in meta.items() if k != "v"
    } != {k: v for k, v in _ocr_cache_meta().items() if k != "v"}:
        return {}  # 引擎/语言变了,旧识别结果不可信
    data.pop("__meta__", None)
    if meta.get("v") != 2:
        # v1 旧缓存:空串 = 真实识别过但无字,迁移为哨兵
        data = {k: _cache_val(v) for k, v in data.items()}
    else:
        # v2:空串 = 未完成占位(中断),当作不存在,续跑重 OCR
        data = {k: v for k, v in data.items() if v != ""}
    return data


def _save_ocr_cache(path: Path | None, cache: dict) -> None:
    if not path or not cache:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({**cache, "__meta__": _ocr_cache_meta()}, ensure_ascii=False),
        encoding="utf-8",
    )


def _ocr_segments(
    args, extract_dir: Path, engine, work: Path | None = None
) -> tuple[list[segment.Segment], list[Path]]:
    """抽帧 -> 筛选 -> OCR。返回 (字幕段列表, 真正送入 OCR 的帧路径)。"""
    paths, spans, masks, cores, frame_dur, offset = _select_keyframes(args, extract_dir)
    key_idx = [k for _, _, k in spans]
    # 无字帧靠 predict 内部跳过 rec,空文本在 build_segments 里被丢弃,无需额外检测
    # OCR 缓存:已识别过的帧序号/文本存工作目录,重跑时直接复用(--force 时忽略,全部重识别)
    ocr_cache_path = handoff.ocr_cache_path(work) if work else None
    ocr_cache: dict[str, str] = _load_ocr_cache(ocr_cache_path, getattr(args, "force", False))
    todo = [
        (masks[k].tobytes(), paths[k])
        for k in key_idx
        if paths[k].name not in ocr_cache
    ]
    if len(todo) < len(key_idx):
        print(f"OCR 缓存:复用 {len(key_idx) - len(todo)} 帧,新识别 {len(todo)} 帧")
    # OCR 前先落盘占位(空串=未完成,中断重跑会重识别),缓存随时反映当前进度
    for _, p in todo:
        ocr_cache.setdefault(p.name, "")
    _save_ocr_cache(ocr_cache_path, ocr_cache)

    def _flush(p: Path, text: str) -> None:
        """每识别完一帧就落盘缓存,中断重跑不用从头再识别。"""
        ocr_cache[p.name] = _cache_val(text)
        _save_ocr_cache(ocr_cache_path, ocr_cache)

    fresh = _ocr_dedup(engine, todo, on_text=_flush) if todo else []
    for (mask, p), text in zip(todo, fresh):
        ocr_cache[p.name] = _cache_val(text)
    _save_ocr_cache(ocr_cache_path, ocr_cache)
    texts = [_cache_text(ocr_cache.get(paths[k].name, "")) for k in key_idx]
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


def _backup(work: Path, *files: Path, name: str = "") -> None:
    """覆盖前备份到 work/backup/(复用 utils.backup)。"""
    utils.backup(work, *files, name=name)


def _backup_names(work: Path, fname: str) -> list[str]:
    """列出 backup/ 下含 fname 的备份(复用 utils.backup_names)。"""
    return utils.backup_names(work, fname)


def _backup_src(work: Path, name: str, fname: str) -> Path:
    """按备份名取 fname 的可读路径(复用 utils.backup_src)。"""
    return utils.backup_src(work, name, fname)


def _save_work(
    args, segs: list[segment.Segment], work: Path, *, fresh: bool = False
) -> Path:
    """把 OCR 段落写进工作目录(单视频与批量模式共用):

    - segments.json 覆盖前自动备份到 backup/<时间戳>/;
    - fresh=True(刚跑完 OCR)或没有原始备份时,同步写 segments.orig.json。

    原文/译文统一以 segments.json 为准。
    """
    cache = TranslationCache(args.cache or handoff.cache_path(work))
    comment_path = handoff.comment_path(work)  # download --comment 写入的视频描述
    comment = (
        comment_path.read_text(encoding="utf-8").strip()
        if comment_path.exists()
        else None
    )
    seg_file = handoff.seg_path(work)
    orig_file = work / handoff.ORIG_NAME
    if seg_file.exists():
        # 已有识别结果:同键同原文的段保留旧 tr 快照(重跑免重翻,含 --force)
        # 键用纯秒数表示(0.01s 精度),旧军方时间键体系已废弃
        old = {f"{s.start:.2f}-{s.end:.2f}": s for s in handoff.read_segments(seg_file)}
        for s in segs:
            o = old.get(f"{s.start:.2f}-{s.end:.2f}")
            if o and o.text == s.text and not handoff.is_untranslated(o.tr or ""):
                s.tr = o.tr
    _backup(work, seg_file, orig_file)  # 覆盖前备份
    handoff.write_segments(
        segs, seg_file, comment=comment,
        long=handoff.read_meta(seg_file).get("long") if seg_file.exists() else None,
    )
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

    bdir = handoff.hidden_dir(work) / "_batch"
    meta = json.loads((bdir / "meta.json").read_text(encoding="utf-8"))
    fps = meta["fps"]
    offset = meta["offset"]
    spans = [tuple(s) for s in meta["spans"]]
    frame_dur = 1.0 / fps
    frames = [bdir / f for f in meta["frames"]]
    hashes = meta["hashes"]

    ocr_cache_path = handoff.ocr_cache_path(work)
    ocr_cache: dict[str, str] = _load_ocr_cache(ocr_cache_path, getattr(args, "force", False))
    todo = [(h, p) for h, p in zip(hashes, frames) if p.name not in ocr_cache]
    if len(todo) < len(frames):
        _say(f"OCR 缓存:复用 {len(frames) - len(todo)} 帧,新识别 {len(todo)} 帧")
    # OCR 前先落盘占位(空串=未完成,中断重跑会重识别),缓存随时反映当前进度
    for _, p in todo:
        ocr_cache.setdefault(p.name, "")
    _save_ocr_cache(ocr_cache_path, ocr_cache)
    # 按掩膜哈希去重:同一画面只识别一次
    uniq: dict[str, Path] = {}
    for h, p in todo:
        uniq.setdefault(h, p)
    # 逐帧识别,批量模式经 progress 上报实时进度;每帧落盘缓存防中断重跑
    uniq_paths = list(uniq.values())
    fresh: list[str] = []
    for i, p in enumerate(uniq_paths):
        fresh.append(_ocr_one(engine, p))
        ocr_cache[p.name] = _cache_val(fresh[-1])
        _save_ocr_cache(ocr_cache_path, ocr_cache)
        if progress:
            progress(i + 1, len(uniq_paths))
    saved = len(todo) - len(uniq)
    if saved:
        _say(f"重复画面去重:识别 {len(uniq)}/{len(todo)} 帧,省 {saved} 次 OCR")
    text_of = dict(zip(uniq.keys(), fresh))
    for h, p in todo:
        ocr_cache[p.name] = _cache_val(text_of[h])
    _save_ocr_cache(ocr_cache_path, ocr_cache)
    texts = [_cache_text(ocr_cache.get(p.name, "")) for p in frames]

    timed = []
    for (s, e, _k), text in zip(spans, texts):
        for i in range(s, e + 1):
            timed.append((offset + i * frame_dur, text))
    segs = segment.build_segments(timed, frame_dur=frame_dur, threshold=args.similarity)
    _say(f"合并为 {len(segs)} 条字幕")
    work = _save_work(args, segs, work, fresh=True)
    shutil.rmtree(bdir, ignore_errors=True)
    return work


def _extract(args) -> Path:
    work = args.work or _work_of(args.video)  # 视频已在工作目录内则沿用,否则默认目录
    work.mkdir(parents=True, exist_ok=True)
    apply_work_crop(args)  # 工作目录里保存的字幕区参数优先(--crop 未显式指定时)
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
            for stale in list(keep.glob("frame_*.jpg")) + list(keep.glob("frame_*.webp")):
                stale.unlink()
            for fp in saved_frames:
                shutil.copy2(fp, keep / fp.name)
            print(f"保留 {len(saved_frames)} 帧 -> {keep}")
        else:
            seg_file = handoff.seg_path(work)
            if seg_file.exists() and not getattr(args, "force", False):
                # 已有识别结果:跳过抽帧/OCR,直接沿用(--force 可强制重跑)
                print(f"已有 {seg_file},跳过 OCR(--force 可强制重跑)")
                segs = handoff.read_segments(seg_file)
                return _save_work(args, segs, work, fresh=False)
            engine = _make_engine(args)
            segs, _ = _ocr_segments(args, extract_dir, engine, work=work)
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)

    if preview:
        print(f"仅抽帧完成 -> {keep}")
        return work

    return _save_work(args, segs, work, fresh=True)


def _load_meta(work: Path) -> dict:
    """读翻译元信息(comment):优先 segments.json。"""
    return handoff.read_meta(handoff.seg_path(work))


def _render(args) -> Path:
    work = args.work
    seg_file = handoff.seg_path(work)
    cache = TranslationCache(args.cache or handoff.cache_path(work))
    segs = handoff.read_segments(seg_file)
    out = args.output or _ass_path(work)
    if getattr(args, "bcc", False):
        out = out.with_suffix(".bcc")
    trs = handoff.resolve(segs, cache)
    if not any(trs):
        # 完全没有译文:用日文原文生成字幕
        if not getattr(args, "batch", False):
            print("没有译文,改用日文原文生成字幕")
        trs = [s.text for s in segs]
    if getattr(args, "bcc", False):
        ass.write_bcc(segs, trs, out)
    else:
        ass.write_ass(
            segs,
            trs,
            out,
            font=args.font,
            font_size=args.font_size,
        )
    if not getattr(args, "batch", False):
        print(f"完成:{out}")
    return out


def _load_glossary(work: Path, args) -> dict[str, str]:
    """加载名词对照表:全局 ~/.jpsub/glossary.txt + 工作目录 glossary.txt + settings 指定文件,
    --glossary 指定的文件优先级最高(后加载覆盖同名词条)。"""
    paths = [Path.home() / ".jpsub" / "glossary.txt", work / "glossary.txt"]
    cfg_file = settings.GLOSSARY_FILE
    if cfg_file:
        paths.append(Path(cfg_file).expanduser())
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
            k, _, v = ln.partition("\t")  # Tab 分隔;无 Tab 时回退首个空白
            if not _:
                k, _, v = ln.partition(" ")
            k, v = k.strip(), v.strip()
            if k and v:
                gloss[k] = v
    return gloss


def _load_tags(work: Path) -> str:
    """读工作目录 info.txt 的「标签:」行,返回逗号分隔串(无则空串)。"""
    return utils.load_tags(work)


def _long_translate(
    items: list[tuple[str, str]],
    cfg: dict,
    *,
    comment: str | None = None,
    glossary: dict[str, str] | None = None,
    quiet: bool = False,
    cache: TranslationCache | None = None,
    on_item=None,
    src_lang: str | None = None,
    to_lang: str | None = None,
    prompt_file: str | None = None,
    tags: str | None = None,
    fail_log: str | None = None,
) -> dict[str, str]:
    """长文模式(--long):每条原文按「。」拆成单句,逐句单独翻译后按原序拼回。

    单句失败只丢那一句(以[[原文]]占位),不会毁掉整条;拆出的重复句子只翻一次。
    cache 传入时单句译文进缓存(跳过已缓存句);on_item(key, 合并译文) 在每条
    原文的全部句子翻完后回调,供调用方实时写回。
    """
    split_of: list[list[str]] = []
    sents: list[str] = []
    seen: set[str] = set()
    for _k, t in items:
        # 拆句:见 handoff.split_sents(引号感知,闭引号后必拆)
        parts = handoff.split_sents(t)
        split_of.append(parts)
        for p in parts:
            if p not in seen:
                seen.add(p)
                sents.append(p)
    # 单句先查缓存,命中直接用,只把没缓存的发给 AI
    got: dict[str, str] = {}
    todo: list[tuple[str, str]] = []
    for s in sents:
        v = cache.get(s) if cache else None
        if v and not handoff.is_untranslated(v):
            got[s] = v
        else:
            todo.append((s, s))
    if not quiet:
        n_cached = len(sents) - len(todo)
        msg = f"长文模式:{len(items)} 条原文拆成 {len(sents)} 个单句(去重后),并发翻译"
        if n_cached:
            msg += f"(缓存命中 {n_cached} 句)"
        print(msg)

    def _on_batch(d: dict) -> None:
        for k, v in d.items():
            got[k] = v
            if cache is not None and v and not handoff.is_bad_tr(v):
                cache.put(k, v)  # 单句译文也进缓存,下次长文模式直用

    if todo:
        ai.translate_texts_parallel(
            todo,
            cfg,
            concurrency=ai.calc_concurrency(todo),
            comment=comment,
            glossary=glossary,
            quiet=quiet,
            on_batch=_on_batch,
            refuse_fix=False,  # 拒译句不补问省 token,标记后由续翻重试
            fail_log=fail_log,
            src_lang=src_lang,
            to_lang=to_lang,
            prompt_file=prompt_file,
            tags=tags,
        )
    out: dict[str, str] = {}
    for (k, _t), parts in zip(items, split_of):
        buf = []
        for p in parts:
            tr = got.get(p, "")
            buf.append(tr if tr and not handoff.is_bad_tr(tr) else f"[[{p}]]")
        out[k] = "".join(buf)
        if on_item:
            on_item(k, out[k])
    return out


def _expand_long_segments(
    segs: list[Segment], cache: TranslationCache | None
) -> tuple[list[Segment], int]:
    """长文模式收尾:把拆句翻译的段展开成单句段(每句一个 Segment)。

    时间按句长比例分配;每句译文取单句缓存,缺失句以[[原句]]占位。
    某段若拆出的句子缺缓存、而整条译文又是有效合并译文(旧普通模式产物,
    无法对应到句),保持整条不拆。返回(新段列表, 展开的段数)。"""
    out: list[Segment] = []
    n = 0
    for s in segs:
        parts = handoff.split_sents(s.text) if s.text else []
        if len(parts) <= 1:
            out.append(s)
            continue
        trs = [cache.get(p) if cache else None for p in parts]
        missing = [
            p for p, tr in zip(parts, trs) if not tr or handoff.is_bad_tr(tr)
        ]
        merged_bad = bool(s.tr) and handoff.is_bad_tr(s.tr)
        if missing and not merged_bad:
            out.append(s)  # 对应不到句,保持整条
            continue
        total = sum(len(p) for p in parts)
        t0, t1, cur = s.start, s.end, s.start
        for p, tr in zip(parts, trs):
            end = round(cur + (t1 - t0) * len(p) / total, 3)
            out.append(
                segment.Segment(
                    cur, end, p,
                    tr=tr if tr and not handoff.is_bad_tr(tr) else f"[[{p}]]",
                )
            )
            cur = end
        n += 1
    return out, n


def _translate(args) -> Path:
    """翻译工作目录里的 segments.json:待翻句子发给 AI,译文写回段的 tr 并落盘。"""
    quiet = getattr(args, "batch", False)
    work = args.work
    seg_file = handoff.seg_path(work)
    if not seg_file.exists():
        raise SystemExit(f"错误:找不到 {seg_file},先运行 extract")
    cache = TranslationCache(args.cache or handoff.cache_path(work))
    segs = handoff.read_segments(seg_file)

    def _expand_and_write() -> None:
        """长文模式收尾:把拆句翻译的段展开成单句段并落盘(已展开则为空操作)。"""
        new, n = _expand_long_segments(segs, cache)
        if not n:
            return
        segs[:] = new
        cache.save()
        handoff.write_segments(
            segs, seg_file,
            comment=_load_meta(work).get("comment"), long=True,
        )
        if not quiet:
            print(f"长文拆句:{n} 条已展开为单句段")

    force = getattr(args, "force", False)
    if force:
        _backup(work, seg_file)  # 重翻会覆盖全部译文,先备份
        texts = list(dict.fromkeys(s.text for s in segs if s.text))
    else:
        # 段上缺译文但缓存有的(如手工删了 tr):直接回填,不当作待翻重花钱
        backfilled = 0
        for s in segs:
            if s.text and not (s.tr and not handoff.is_bad_tr(s.tr)):
                v = cache.get(s.text)
                if v and not handoff.is_untranslated(v):
                    s.tr = v
                    backfilled += 1
        if backfilled and not quiet:
            print(f"从缓存回填 {backfilled} 条缺失译文")
        texts = handoff.pending_texts(segs, cache)
    if not texts:
        if not quiet:
            print("没有待翻译的句子(全部已译,--force 可强制重翻)")
        if getattr(args, "long", False):
            handoff.write_meta(seg_file, long=True)  # 长文标记写进元信息,edit 自动展开
            _expand_and_write()  # 旧版 long 产物仍是整条,这里补展开
        return seg_file
    items = [(t, t) for t in texts]  # 键即原文,译文按原文回填各段
    _backup(work, seg_file)  # 翻译结果会写回 segments,先备份
    cfg = ai.resolve_config(args)
    comment = getattr(args, "comment", None)  # 命令行指定优先
    if comment is None:
        comment = _load_meta(work).get("comment")
    tags = _load_tags(work)  # 原视频标签:随 system 一次性发给 AI 供参考题材风格
    if tags and not quiet:
        print(f"视频标签:{tags}")
    if not getattr(args, "long", False):
        for key in settings.TAG_LONG or ():
            if key in tags:
                args.long = True  # 强制长文模式;想关可清空 settings.TAG_LONG
                if not quiet:
                    print(f"标签命中 {key},自动启用长文模式")
                break
    glossary = _load_glossary(work, args)
    if glossary and not quiet:
        print(f"名词对照表:{len(glossary)} 条")
    src_lang = getattr(args, "src_lang", None)
    to_lang = getattr(args, "to_lang", None)
    prompt_file = str(args.prompt) if getattr(args, "prompt", None) else None
    if (src_lang or to_lang) and not quiet:
        print(f"语言:{settings.lang_name(src_lang) if src_lang else settings.source_lang_name()} → {to_lang or settings.TARGET_LANG}")

    acc: dict[str, str] = {}  # 每批累积的部分结果:中断时保留已完成部分
    long_flag = bool(getattr(args, "long", False))  # 写入 segments.json 供 edit 自动开长文

    def _persist() -> None:
        """把累积译文写回 segments(含 [[未译]] 占位)与缓存并落盘。"""
        n = 0
        for s in segs:
            tr = acc.get(s.text)
            if tr is None:
                continue
            s.tr = tr  # 含未译占位:调整器里橙色显示,可重翻
            if not handoff.is_untranslated(tr):
                cache.put(s.text, tr)
                n += 1
        cache.save()  # 长文模式的单句缓存也在此落盘(中断续翻的关键)
        if acc:
            handoff.write_segments(
                segs, seg_file,
                comment=_load_meta(work).get("comment"), long=long_flag,
            )
        if n and not quiet:
            print(f"译文已写回 {seg_file}(新翻译 {n} 句)")

    def _run(bs: int, todo: list[tuple[str, str]]):
        # 普通翻译也走并发对话:实测比单对话更省 token(历史不滚雪球)且更快
        return ai.translate_texts_parallel(
            todo,
            cfg,
            concurrency=ai.calc_concurrency(todo),
            comment=comment,
            glossary=glossary or None,
            progress=getattr(args, "tr_progress", None),
            quiet=quiet,
            on_batch=acc.update,
            fail_log=str(work / "translate-fails.txt"),
            src_lang=src_lang,
            to_lang=to_lang,
            prompt_file=prompt_file,
            tags=tags or None,
        )

    if getattr(args, "long", False):
        # 长文模式:整条按「。」拆句逐句翻译,单句问题不连累整条;
        # 每翻完一条就把合并译文写回 segments 并落盘,中断不丢已完成部分
        def _on_item(k: str, merged: str) -> None:
            # 含 [[原文]] 占位(该条有失败句)也写回 segments,替换掉旧的脏译文;
            # 只是整条不进缓存,下次续翻只重翻失败的单句
            for s in segs:
                if s.text == k:
                    s.tr = merged
            if "[[" not in merged:
                cache.put(k, merged)
            cache.save()
            handoff.write_segments(
                segs, seg_file,
                comment=_load_meta(work).get("comment"), long=True,
            )

        try:
            _long_translate(
                items, cfg,
                comment=comment, glossary=glossary or None,
                quiet=quiet, cache=cache, on_item=_on_item,
                src_lang=src_lang, to_lang=to_lang,
                prompt_file=prompt_file, tags=tags or None,
                fail_log=str(work / "translate-fails.txt"),
            )
        except RuntimeError as e:
            if "额度不足" in str(e):
                _expand_and_write()  # 已翻译的单句都在缓存里,先展开落盘
                _persist()  # 已翻译部分先落盘,充值后可续翻
                raise SystemExit(f"\n提醒:{e}(已翻译部分已写回 segments,可充值后续翻)") from None
            raise
        _expand_and_write()  # long 产物直接保存拆句版本
        _persist()
        return seg_file

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


def _translate_text(args) -> Path:
    """翻译任意文本文件:逐行发给 AI,译文按行写回输出文件(空行原样保留)。"""
    src: Path = args.file
    if not src.exists():
        raise SystemExit(f"错误:找不到文件 {src}")
    out: Path = args.output or src.with_name(src.stem + ".zh.txt")
    lines = src.read_text(encoding="utf-8").splitlines()
    texts = [ln for ln in lines if ln.strip()]
    if not texts:
        raise SystemExit(f"错误:{src} 没有可翻译的内容")
    cfg = ai.resolve_config(args)
    glossary = _load_glossary(Path.cwd(), args)
    if glossary:
        print(f"名词对照表:{len(glossary)} 条")
    items = [(t, t) for t in texts]

    def _run(bs: int, todo: list[tuple[str, str]]):
        return ai.translate_texts_parallel(
            todo,
            cfg,
            concurrency=ai.calc_concurrency(todo),
            comment=args.comment,
            glossary=glossary or None,
            fail_log=str(src.parent / "translate-fails.txt"),
            src_lang=getattr(args, "src_lang", None),
            to_lang=getattr(args, "to_lang", None),
            prompt_file=str(args.prompt) if getattr(args, "prompt", None) else None,
        )

    if getattr(args, "long", False):
        # 长文模式:整行按「。」拆句逐句翻译,单句问题不连累整行(失败句回退原文)
        got = _long_translate(
                items, cfg, comment=args.comment, glossary=glossary or None,
                src_lang=getattr(args, "src_lang", None),
                to_lang=getattr(args, "to_lang", None),
                prompt_file=str(args.prompt) if getattr(args, "prompt", None) else None,
            )
    else:
        got = None

    try:
        if got is None:
            got = _run(args.batch_size, items)
    except ai.CensoredError as e:
        print(f"\n内容审查拦截:{e}\n改用 batch_size=1 逐句重跑...")
        got.update(_run(1, items))
    out_lines = []
    for ln in lines:
        if not ln.strip():
            out_lines.append(ln)
            continue
        tr = got.get(ln, "")
        out_lines.append(tr if tr and not handoff.is_untranslated(tr) else ln)
    out.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    n = sum(1 for ln in out_lines if ln and ln not in set(texts))
    print(f"完成:输出 {out}({n}/{len(texts)} 句已译)")
    return out


def _voice(args) -> Path:
    """朗读视频:静音检测切语音段,产出 segments.json(文本留空,edit 里填原文)。"""
    from .voice import detect_speech_segments

    work = args.work or _work_of(args.video)  # 视频已在工作目录内则沿用,否则默认目录
    work.mkdir(parents=True, exist_ok=True)
    seg_file = handoff.seg_path(work)
    if seg_file.exists() and not args.force:
        print(f"已有 {seg_file},跳过(--force 可强制重新生成)")
        return work
    print(f"静音检测:{args.video}(noise={args.noise}, min_silence={args.min_silence}s)")
    segs = detect_speech_segments(
        args.video,
        noise=args.noise,
        min_silence=args.min_silence,
        pad=args.pad,
        hyst=args.hyst,
        start=args.start,
        end=args.end,
    )
    if not segs:
        raise SystemExit("错误:没有检测到语音段,检查 --noise/--min-silence/--start/--end")
    print(f"切出 {len(segs)} 个语音段")
    _backup(work, seg_file)
    handoff.write_segments(segs, seg_file)
    print(f"工作目录:{work}(文本留空,请在 edit 里填入原文)")
    from .edit import editor

    try:
        editor(work)
    except KeyboardInterrupt:
        print("已跳过,之后可用 jpsub edit 继续填写原文")
    return work


def _has_pending(work: Path) -> bool:
    """segments.json 是否有未翻译的句子。"""
    p = handoff.seg_path(work)
    if not p.exists():
        return False
    segs = handoff.read_segments(p)
    cache = TranslationCache(handoff.cache_path(work))
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
    from .edit import editor

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
        "-threads",
        str(settings.ffmpeg_threads()),
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
    if args.video_quality:  # -v/-a 命令行优先于 settings.py
        dl.settings.NICO_VIDEO_QUALITY = args.video_quality
    if args.audio_quality:
        dl.settings.NICO_AUDIO_QUALITY = args.audio_quality
    args.output = args.output or _output_root()
    video = download(args.url, args.output, comment=args.comment)
    args.video = video
    work = _work_of(video)  # 新布局视频已在工作目录内
    # 已有 ASS 则跳过流水线直接烧录
    ass = _find_ass(work)
    if getattr(args, "burn", False) and ass.exists():
        return _burn(video, ass)
    if getattr(args, "download_only", False):  # 仅下载:拿到视频即停
        print(f"仅下载模式,不抽帧不 OCR:{video}")
        return video
    # 完整流水线:extract -> translate -> render(ASS 存工作目录内,以视频名命名)
    if not hasattr(args, "work"):  # download 子命令没有 --work 参数
        args.work = None  # 让 _extract 用默认 <视频>.jpsub
    work = _extract(args)
    args.output = _ass_path(work)
    return _maybe_translate_render(args, work)


def _status(args) -> None:
    work = args.work
    seg_file = handoff.seg_path(work)
    if not seg_file.exists():
        raise SystemExit(f"错误:找不到 {seg_file}")
    segs = handoff.read_segments(seg_file)
    cache = TranslationCache(args.cache or handoff.cache_path(work))
    total = len({s.text for s in segs if s.text})
    missing = len(handoff.pending_texts(segs, cache))
    print(
        f"段数:{len(segs)}  唯一句:{total}  已有译文:{total - missing}  待翻译:{missing}"
    )


def run(argv: list[str] | argparse.Namespace | None = None) -> Path | None:
    """程序入口。argv 既可以是命令行参数列表,也可以是已解析的 Namespace。"""
    import sys

    if argv is None:
        argv = sys.argv[1:]
    # 裸 `jpsub`(无参数):打开浏览器主页
    if isinstance(argv, list) and not argv:
        from .home import home_page

        return home_page()
    # 允许省略子命令:`jpsub [选项] <url或视频id>` 直接视为下载
    _SUBS = {
        "extract", "render", "run", "status", "translate", "text",
        "mask", "maskapply", "voice", "edit", "home", "download", "upload",
    }
    if isinstance(argv, list) and argv and argv[0] not in _SUBS:
        from pathlib import Path as _Path

        from .download import extract_video_id, is_youtube

        if not argv[0].startswith("-") and _Path(argv[0]).exists():
            # 本地文件(如 sm123.mp4)优先视为 run 的输入,避免被误当视频 id 走下载
            argv = ["run", *argv]
        else:
            # URL/sm 号可能在选项之后(如 jpsub --crop 1 <url>),扫描全列表
            if any(extract_video_id(a) or is_youtube(a) for a in argv):
                argv = ["download", *argv]
    args = argv if isinstance(argv, argparse.Namespace) else parse_args(argv)
    if getattr(args, "ocr_boost", False):
        settings.OCR_BOOST = True  # 特殊画面 OCR 文字凸显(默认关)
    if getattr(args, "script", None):
        from .batch import run_script

        run_script(args)
        return None
    if args.command == "download":
        return _download(args)
    if args.command == "extract":
        work = _extract(args)
        return _maybe_translate_render(args, work)
    if args.command == "render":
        return _render(args)
    if args.command == "translate":
        return _translate(args)
    if args.command == "text":
        return _translate_text(args)
    if args.command == "status":
        _status(args)
        return None
    if args.command == "mask":
        from .mask import auto_masks_path, detect_masks, picker, save_masks

        video = _video_arg(args.video)
        if args.auto:  # 自动去人:只写独立文件,手工 masks.json 保持不动
            entries = detect_masks(video)
            out = args.masks or auto_masks_path(video)
            save_masks(entries, out)
            print(
                f"自动去人:检出 {len(entries)} 条覆盖 -> {out}"
                "(人工复核后用 maskapply 应用,如:jpsub maskapply <视频> "
                f"{out.name})"
            )
            return None
        picker(video, args.masks)
        return None
    if args.command == "maskapply":
        from .mask import apply_masks, default_masks_path

        args.video = _video_arg(args.video)
        masks = args.masks or default_masks_path(args.video)
        out = args.output or _product_out(args.video, ".masked.mp4")
        apply_masks(args.video, masks, out)
        if args.burn:  # 链式:打码完成后接着烧字幕
            ass = _find_ass(_work_of(args.video))
            if ass.exists():
                _burn(out, ass)
            else:
                print(f"提示:未找到字幕 {ass},跳过烧录,只输出打码视频")
        return None
    if args.command == "voice":
        return _voice(args)
    if args.command == "edit":
        from .edit import editor

        return editor(args.target)
    if args.command == "home":
        from .home import home_page

        return home_page(args.root)
    if args.command == "upload":
        from . import upload as _up

        work = args.work
        if not work.exists():  # 裸条目名(批量脚本 upload <名>)按 output/ 下的条目找
            cand = _output_root() / work.name
            if cand.is_dir():
                work = cand
        if not work.is_dir():
            raise SystemExit(f"错误:找不到工作目录 {work}")
        if args.login:
            _up.login()
            return None
        return _up.upload(
            work,
            title=args.title, desc=args.desc, tags=args.tags,
            tid=args.tid, delay=args.delay,
        )
    # run = extract + (默认)translate + render
    work = args.work or _work_of(args.video)  # 视频已在工作目录内则沿用,否则默认目录
    # --burn 且已有 ASS:跳过流水线直接烧录(--force 时强制重跑)
    if getattr(args, "burn", False) and not getattr(args, "force", False):
        ass_path = getattr(args, "output", None) or _find_ass(work)
        if ass_path.exists():
            print(f"已有字幕 {ass_path},跳过流水线直接烧录")
            return _burn(args.video, ass_path)
    work = _extract(args)
    return _maybe_translate_render(args, work)


def main(argv: list[str] | None = None) -> None:
    settings.apply_proxy()  # settings.json 里的 proxy 用于 pip/模型下载
    run(argv)


if __name__ == "__main__":
    main()
