"""批量脚本模式:-s 指定脚本文件,每行一条 jpsub 调用。

流水线三级调度,保证全程不闲置:
  阶段A 下载+抽帧筛选 -> 进程池并行;预览模式(--extract-only/--selected-only)
        在此抽帧存图即结束,不进 B/C
  阶段B OCR -> 按下载完成顺序排队,单实例依次识别
  阶段C 翻译+渲染 -> 线程池并发;translate/render/status 无需 A/B,直接在此并发
每个视频独立显示所处阶段与进度,单个失败不影响其余。
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, wait
from multiprocessing import Manager
from queue import Empty
from pathlib import Path

_NO_STAGE_A = ("translate", "render", "status")  # 只收 work 的子命令,无下载/抽帧


def _parse_line(line: str):
    """把脚本一行解析成 args;裸 URL/视频 id 自动视为 download。"""
    from .cli import parse_args
    from .download import extract_video_id

    tokens = shlex.split(line)
    for i, t in enumerate(tokens):  # 行内 # 注释:连同其后内容一并丢弃
        if t.startswith("#"):
            tokens = tokens[:i]
            break
    if tokens and not tokens[0].startswith("-") and extract_video_id(tokens[0]):
        tokens = ["download", *tokens]
    return parse_args(tokens)


def _save_preview_frames(ns, work: Path) -> int:
    """预览模式(阶段A):抽帧存到工作目录,全部帧或仅关键帧;返回帧数。"""
    from . import frames as fr
    from .cli import _select_keyframes

    tmp = Path(tempfile.mkdtemp(prefix="jpsub_"))
    try:
        if getattr(ns, "selected_only", False):
            paths, spans, _m, _c, _fd, _off = _select_keyframes(ns, tmp, quiet=True)
            saved = [paths[sp[2]] for sp in spans]
        else:
            saved = fr.extract_frames(
                ns.video, tmp, fps=ns.fps, crop=ns.crop, start=ns.start, end=ns.end
            )
        for stale in work.glob("frame_*.jpg"):
            stale.unlink()
        for p in saved:
            shutil.copy2(p, work / p.name)
        return len(saved)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _prepare(line: str, q=None):
    """阶段A(子进程):下载视频 + 抽帧筛选,关键帧与元数据落工作目录 _batch/。

    translate/render/status 无阶段A,直接透传给阶段C;
    预览模式只抽帧存图,不进 B/C;--burn 且已有 ASS 时直接烧录。
    q 为 multiprocessing 队列,回传 (line, 消息) 给主进程刷状态板。
    """
    try:
        ns = _parse_line(line)
        ns.batch = True  # 阶段B/C 静默散行输出,由主进程状态板统一展示
        cmd = ns.command
        if cmd in _NO_STAGE_A:
            return line, ns, None, None, 0, None

        from . import download as dl
        from .cli import _default_work, _output_root, _select_keyframes

        if cmd == "download":
            if ns.cookies_from_browser:
                dl.settings.COOKIES_FROM_BROWSER = ns.cookies_from_browser
            video = dl.download(
                ns.url,
                ns.output or _output_root(),
                comment=ns.comment,
                quiet=True,
                progress=(lambda msg: q.put((line, msg))) if q else None,
            )
        else:  # run / extract
            video = ns.video

        ns.video = video
        work = Path(getattr(ns, "work", None) or _default_work(video))

        # --download-only:只下载,不抽帧不 OCR(与 cli._download 行为一致)
        if cmd == "download" and getattr(ns, "download_only", False):
            return line, ns, str(video), None, 0, None

        # --burn 且已有 ASS:跳过整条流水线直接烧录(与 cli.run 一致),在进程池并行
        if getattr(ns, "burn", False):
            from .cli import _find_ass, _work_of

            work = Path(getattr(ns, "work", None) or _work_of(video))
            ass_path = getattr(ns, "output", None) or _find_ass(work)
            if not Path(ass_path).exists() and cmd == "download":
                ass_path = video.with_suffix(".ass")  # 旧布局回退
            if Path(ass_path).exists():
                from .cli import _burn

                _burn(video, Path(ass_path))
                return line, ns, str(video), None, 0, None

        work.mkdir(parents=True, exist_ok=True)

        if getattr(ns, "extract_only", False) or getattr(ns, "selected_only", False):
            return line, ns, str(video), str(work), _save_preview_frames(ns, work), None

        bdir = work / "_batch"
        bdir.mkdir(exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="jpsub_"))
        try:
            paths, spans, masks, _cores, _frame_dur, offset = _select_keyframes(
                ns, tmp, quiet=True
            )
            frames, hashes = [], []
            for _s, _e, k in spans:
                p = paths[k]
                shutil.copy2(p, bdir / p.name)
                frames.append(p.name)
                hashes.append(hashlib.sha1(masks[k].tobytes()).hexdigest())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        (bdir / "meta.json").write_text(
            json.dumps(
                {
                    "fps": ns.fps,
                    "offset": offset,
                    "spans": spans,
                    "frames": frames,
                    "hashes": hashes,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return line, ns, str(video), str(work), len(frames), None
    except BaseException:
        return line, None, None, None, 0, traceback.format_exc()


class _Board:
    """每任务固定一行、原地刷新:阶段 + 总数,失败等事件打到板下方。"""

    def __init__(self, lines: list[str]):
        self.lock = threading.Lock()
        self.rows: list[str] = []
        self.state: dict[str, dict] = {}
        self.order: list[str] = list(lines)
        for ln in lines:
            self.state[ln] = {"stage": self._initial_stage(ln), "info": ""}
            self.rows.append(self._fmt(ln))
        self._drawn = False
        self._last: dict[str, str] = {}  # 非终端模式:上次打印的阶段

    @staticmethod
    def _initial_stage(ln: str) -> str:
        """无需下载/抽帧的子命令,初始阶段直接显示命令名。"""
        first = ln.split()[0] if ln.split() else ""
        return first if first in _NO_STAGE_A else "下载/筛选"

    @staticmethod
    def _short(ln: str) -> str:
        """任务标签:优先取行内 sm 号/本地文件名,取不到用首个 token。"""
        m = re.search(r"\b(?:sm|so|nm)(\d{5,})", ln)
        if m:
            return "sm" + m.group(1)
        toks = ln.split()
        for t in toks[1:]:
            if re.fullmatch(r"\d{5,}", t):  # 裸视频 id
                return "sm" + t
        for t in toks[1:]:  # 跳过子命令,找含路径分隔符或扩展名的本地文件
            p = Path(t)
            if "/" in t or p.suffix:
                return p.name if len(p.name) <= 40 else p.name[:37] + "..."
        s = toks[0] if toks else ln
        return s if len(s) <= 40 else s[:37] + "..."

    def _fmt(self, line: str) -> str:
        st = self.state[line]
        row = f"[{self._short(line)}] {st['stage']}"
        if st["info"]:
            row += f" | {st['info']}"
        return row

    def update(self, line: str, stage: str | None = None, info: str | None = None):
        with self.lock:
            st = self.state[line]
            if stage is not None and stage != st["stage"]:
                st["stage"] = stage
                st["info"] = ""  # 换阶段时清掉上一阶段的进度信息
            if info is not None:
                st["info"] = info
            self.rows[self.order.index(line)] = self._fmt(line)
            self._draw()

    def _draw(self):
        """终端下整块原地重绘;非终端只在阶段变化时逐行打印(避免百分比刷屏)。"""
        if not sys.stdout.isatty():
            for line, row in zip(self.order, self.rows):
                stage = self.state[line]["stage"]
                if self._last.get(line) != stage:
                    self._last[line] = stage
                    print(row, flush=True)
            return
        block = "\n".join(self.rows)
        if not self._drawn:
            print(block, flush=True)
            self._drawn = True
        else:
            sys.stdout.write(f"\033[{len(self.rows)}A\033[J{block}\n")
            sys.stdout.flush()

    def event(self, msg: str):
        """板下方的追加日志(事件/错误),不影响各行内容。"""
        with self.lock:
            if sys.stdout.isatty() and self._drawn:
                # 先抹掉整块,打印事件后再把板画回来,保持光标在板尾
                sys.stdout.write(f"\033[{len(self.rows)}A\033[J")
                print(msg, flush=True)
                print("\n".join(self.rows), flush=True)
            else:
                print(msg, flush=True)


def run_script(args) -> None:
    """批量调度主循环(主进程):OCR 单实例排队,翻译线程并发。"""
    script: Path = args.script
    lines = [
        ln.strip()
        for ln in script.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if not lines:
        raise SystemExit(f"错误:脚本文件为空 {script}")
    n = len(lines)
    dl_workers = args.dl_workers or min(n, max(2, os.cpu_count() or 2))
    tr_workers = args.tr_workers or min(4, n)
    board = _Board(lines)
    board.event(
        f"批量模式:{n} 个任务(下载/筛选 {dl_workers} 进程,翻译 {tr_workers} 线程)"
    )

    dl_pool = ProcessPoolExecutor(max_workers=dl_workers)
    tr_pool = ThreadPoolExecutor(max_workers=tr_workers)
    q = Manager().Queue()  # 阶段A子进程 -> 主进程 的进度消息 (line, msg)
    dl_futs = {dl_pool.submit(_prepare, ln, q): ln for ln in lines}
    tr_futs: dict = {}
    queue: collections.deque = collections.deque()  # (line, ns, work) 待 OCR
    engine = None
    ok = True
    failed = 0
    done_cnt = 0
    try:
        while dl_futs or queue or tr_futs:
            # 收割阶段A进度消息(下载百分比等)
            while True:
                try:
                    line, msg = q.get_nowait()
                    if line in board.state:
                        board.update(line, None, msg)
                except Empty:
                    break
            # 收割翻译/渲染结果
            for f in [f for f in tr_futs if f.done()]:
                line = tr_futs.pop(f)
                exc = f.exception()
                if exc:
                    ok = False
                    failed += 1
                    board.update(line, "失败", "见下方错误")
                    board.event(
                        f"[失败] {line}\n{''.join(traceback.format_exception(exc))}"
                    )
                else:
                    done_cnt += 1
                    out = f.result()
                    board.update(line, "完成", Path(out).name if out else "")
            # 阶段B:按下载完成顺序依次 OCR(单实例),完成即进翻译队列
            if queue:
                line, ns, work = queue.popleft()
                try:
                    from .cli import _make_engine, ocr_batch_stage

                    if engine is None:
                        board.event("加载 OCR 模型...")
                        engine = _make_engine(ns)
                    meta = json.loads((Path(work) / "_batch" / "meta.json").read_text())
                    board.update(line, "OCR", f"0/{len(meta['frames'])} 帧")
                    ocr_batch_stage(
                        ns,
                        engine,
                        Path(work),
                        quiet=True,
                        progress=lambda d, t, _l=line: board.update(
                            _l, None, f"{d}/{t} 帧"
                        ),
                    )
                    board.update(line, "翻译/渲染")
                    ns.work = Path(work)
                    tr_futs[tr_pool.submit(_stage_c, ns, work)] = line
                except Exception:
                    ok = False
                    failed += 1
                    board.update(line, "失败", "见下方错误")
                    board.event(f"[OCR失败] {line}\n{traceback.format_exc()}")
                continue
            # 收割下载/筛选结果
            if dl_futs:
                done, _ = wait(set(dl_futs), timeout=0.2)
                for f in done:
                    line, ns, _video, work, keyframes, err = f.result()
                    dl_futs.pop(f)
                    if err:
                        ok = False
                        failed += 1
                        board.update(line, "失败", "见下方错误")
                        board.event(f"[下载/筛选失败] {line}\n{err}")
                    elif work and (Path(work) / "_batch" / "meta.json").exists():
                        board.update(line, "排队OCR", f"关键帧 {keyframes}")
                        queue.append((line, ns, work))
                    elif ns.command in _NO_STAGE_A:  # 无 A/B,直接进阶段C
                        board.update(line, ns.command)
                        tr_futs[tr_pool.submit(_stage_c, ns, work)] = line
                    else:  # 预览存帧 / 直接烧录:已在阶段A完成
                        done_cnt += 1
                        board.update(
                            line, "完成", f"{keyframes} 帧" if keyframes else ""
                        )
            else:
                time.sleep(0.2)  # 只剩翻译线程在跑,避免空转
    finally:
        dl_pool.shutdown(wait=False, cancel_futures=True)
        tr_pool.shutdown(wait=True)
    board.event(f"\n批量完成:{n - failed}/{n} 成功")
    if not ok:
        raise SystemExit(1)


def _stage_c(ns, work):
    """阶段C(线程):按子命令分派。

    translate/render/status 各自直接执行;run/extract/download 走翻译+渲染(+烧录)。
    """
    from .cli import _maybe_translate_render, _render, _status, _translate

    if work:
        ns.work = Path(work)
    if ns.command == "translate":
        return _translate(ns)
    if ns.command == "render":
        return _render(ns)
    if ns.command == "status":
        _status(ns)
        return None
    return _maybe_translate_render(ns, Path(work))
