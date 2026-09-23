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
import shlex
import shutil
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, wait
from pathlib import Path

_NO_STAGE_A = ("translate", "render", "status")  # 只收 work 的子命令,无下载/抽帧


def _parse_line(line: str):
    """把脚本一行解析成 args;裸 URL/视频 id 自动视为 download。"""
    from .cli import parse_args
    from .download import extract_video_id

    tokens = shlex.split(line)
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
            paths, spans, _m, _c, _fd, _off = _select_keyframes(ns, tmp)
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


def _prepare(line: str):
    """阶段A(子进程):下载视频 + 抽帧筛选,关键帧与元数据落工作目录 _batch/。

    translate/render/status 无阶段A,直接透传给阶段C;
    预览模式只抽帧存图,不进 B/C;--burn 且已有 ASS 时直接烧录。
    """
    try:
        ns = _parse_line(line)
        cmd = ns.command
        if cmd in _NO_STAGE_A:
            return line, ns, None, None, 0, None

        from . import download as dl
        from .cli import _default_work, _output_root, _select_keyframes

        if cmd == "download":
            if ns.cookies_from_browser:
                dl.settings.COOKIES_FROM_BROWSER = ns.cookies_from_browser
            video = dl.download(ns.url, ns.output or _output_root(), comment=ns.comment)
        else:  # run / extract
            video = ns.video

        ns.video = video
        work = Path(getattr(ns, "work", None) or _default_work(video))

        # --burn 且已有 ASS:跳过整条流水线直接烧录(与 cli.run 一致),在进程池并行
        if getattr(ns, "burn", False):
            ass_path = (
                video.with_suffix(".ass")
                if cmd == "download"
                else (getattr(ns, "output", None) or work.with_suffix(".ass"))
            )
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
            paths, spans, masks, _cores, _frame_dur, offset = _select_keyframes(ns, tmp)
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
    """每个视频一行的状态板:阶段 + 进度,失败原因单独打印且不影响其他。"""

    def __init__(self, lines: list[str]):
        self.lock = threading.Lock()
        self.rows: list[str] = []
        self.state: dict[str, dict] = {}
        self.order: list[str] = []
        for i, ln in enumerate(lines, 1):
            self.order.append(ln)
            self.state[ln] = {"stage": self._initial_stage(ln), "info": ""}
            self.rows.append(
                f"[{i}/{len(lines)}] {self._short(ln)} | {self.state[ln]['stage']}"
            )

    @staticmethod
    def _initial_stage(ln: str) -> str:
        """无需下载/抽帧的子命令,初始阶段直接显示命令名。"""
        first = ln.split()[0] if ln.split() else ""
        return first if first in _NO_STAGE_A else "下载/筛选"

    @staticmethod
    def _short(ln: str) -> str:
        s = ln.split()[0] if ln.split() else ln
        return s if len(s) <= 40 else s[:37] + "..."

    def update(self, line: str, stage: str | None = None, info: str | None = None):
        with self.lock:
            st = self.state[line]
            if stage is not None:
                st["stage"] = stage
            if info is not None:
                st["info"] = info
            i = self.order.index(line) + 1
            self.rows[i - 1] = (
                f"[{i}/{len(self.order)}] {self._short(line)} | {st['stage']}"
            )
            if st["info"]:
                self.rows[i - 1] += f" | {st['info']}"
            self._draw()

    def _draw(self):
        if not sys.stdout.isatty():
            return
        block = "\n".join(self.rows)
        if not hasattr(self, "_drawn"):
            print(block, flush=True)
            self._drawn = True
        else:
            sys.stdout.write(f"\033[{len(self.rows)}A\033[J{block}\n")
            sys.stdout.flush()

    def event(self, msg: str):
        """状态板下方的追加日志行(事件/错误)。"""
        with self.lock:
            if sys.stdout.isatty() and hasattr(self, "_drawn"):
                sys.stdout.write("\n")
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
    dl_futs = {dl_pool.submit(_prepare, ln): ln for ln in lines}
    tr_futs: dict = {}
    queue: collections.deque = collections.deque()  # (line, ns, work) 待 OCR
    engine = None
    ok = True
    failed = 0
    done_cnt = 0
    try:
        while dl_futs or queue or tr_futs:
            # 收割翻译/渲染结果
            for f in [f for f in tr_futs if f.done()]:
                line = tr_futs.pop(f)
                exc = f.exception()
                if exc:
                    ok = False
                    failed += 1
                    board.update(line, "失败", "见下方错误")
                    board.event(f"[失败] {line}\n{exc}")
                else:
                    done_cnt += 1
                    board.update(line, "完成")
            # 阶段B:按下载完成顺序依次 OCR(单实例),完成即进翻译队列
            if queue:
                line, ns, work = queue.popleft()
                try:
                    from .cli import _make_engine, ocr_batch_stage

                    if engine is None:
                        board.event("加载 OCR 模型...")
                        engine = _make_engine(ns)
                    board.update(line, "OCR")
                    ocr_batch_stage(
                        ns,
                        engine,
                        Path(work),
                        progress=lambda d, t, _l=line: board.update(
                            _l, None, f"{d}/{t} 画面"
                        ),
                    )
                    board.update(line, "翻译/渲染")
                    ns.work = Path(work)
                    ns.tr_progress = lambda d, t, _l=line: board.update(
                        _l, None, f"{d}/{t} 句"
                    )
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
                        board.update(line, "完成", f"{keyframes} 帧" if keyframes else "")
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
        _translate(ns)
    elif ns.command == "render":
        _render(ns)
    elif ns.command == "status":
        _status(ns)
    else:
        _maybe_translate_render(ns, Path(work))
