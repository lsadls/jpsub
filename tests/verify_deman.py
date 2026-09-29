"""验收脚本:自动去人 `jpsub mask --auto`(change: remove-weird-man)。

用法: ``.venv/bin/python tests/verify_deman.py [视频路径]``
覆盖 brief 的 A1-A5 与 spec 场景 A6-A11,逐项打印结果;全部通过时退出码 0。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_VIDEO = (
    ROOT
    / "output"
    / "TAKUYA -タイト- 4話(終)_sm38156190"
    / "TAKUYA -タイト- 4話(終).mp4"
)
W, H = 640, 360
POS_TIMES = (16, 56, 1172)  # 手工基线覆盖、含实拍真人
NEG_TIMES = (1200, 1220)  # 无肤色内容
SKIN_BG_TIMES = (40, 600)  # 米色/泥土等肤色样背景

results: list[tuple[str, str, bool, str]] = []


def check(ids: str, name: str, ok: bool, detail: str = "") -> None:
    results.append((ids, name, bool(ok), detail))


def run_cli(*args: str) -> subprocess.CompletedProcess:
    code = "import sys; from jpsub.cli import main; main(sys.argv[1:])"
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def frame_rgb(path: Path, t: float) -> object:
    import numpy as np

    out = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(t),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(out, dtype=np.uint8)[: W * H * 3].reshape(H, W, 3)


def probe(path: Path, entries: str) -> str:
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            entries,
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def overlap(a, b) -> int:
    return max(0, min(a.x + a.w, b[0] + b[2]) - max(a.x, b[0])) * max(
        0, min(a.y + a.h, b[1] + b[3]) - max(a.y, b[1])
    )


def covering(entries, t):
    return [e for e in entries if e.start <= t < e.end]


def main() -> int:
    from jpsub.mask import (
        auto_masks_path,
        default_masks_path,
        parse_masks,
        video_duration,
    )

    video = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_VIDEO
    if not video.exists():
        print(f"错误:视频不存在:{video}")
        return 2
    manual_path = default_masks_path(video)
    auto_path = auto_masks_path(video)
    manual_before = hashlib.sha256(manual_path.read_bytes()).hexdigest()

    proc = run_cli("mask", "--auto", str(video))
    check(
        "A1",
        "jpsub mask --auto 正常退出",
        proc.returncode == 0,
        (proc.stderr or proc.stdout).strip()[-300:],
    )

    # A1 / A9:产出可解析的独立文件,且手工清单未被改动
    exists = auto_path.exists()
    check("A1", f"生成独立掩膜文件 {auto_path.name}", exists, str(auto_path))
    entries = parse_masks(auto_path) if exists else []
    legal = bool(entries) and all(
        e.start < e.end and e.w > 0 and e.h > 0 and e.effect for e in entries
    )
    check("A1", "条目数 > 0 且字段合法", legal, f"条目数={len(entries)}")
    unchanged = hashlib.sha256(manual_path.read_bytes()).hexdigest() == manual_before
    check("A9", "手工 masks.json 未被改动", unchanged, str(manual_path))

    manual = parse_masks(manual_path)

    # A2 / A6:命中实拍真人
    for t in POS_TIMES:
        cov = covering(entries, t)
        boxes = [m for m in manual if m.start <= t < m.end]
        hit = any(overlap(e, (b.x, b.y, b.w, b.h)) > 0 for e in cov for b in boxes)
        check(
            "A2/A6",
            f"{t}s 有覆盖且与手工基线框相交",
            bool(cov) and hit,
            f"覆盖条目={len(cov)} 基线框={len(boxes)}",
        )

    # A3 / A7:无肤色内容时不覆盖
    for t in NEG_TIMES:
        area = sum(e.w * e.h for e in covering(entries, t))
        check(
            "A3/A7",
            f"{t}s 无覆盖或 < 整帧 5%",
            area < 0.05 * W * H,
            f"覆盖面积占比={area / (W * H):.3%}",
        )

    # A4 / A8:肤色样背景不误覆盖
    for t in SKIN_BG_TIMES:
        cov = covering(entries, t)
        area = sum(e.w * e.h for e in cov)
        top = min((e.y for e in cov), default=H)
        check(
            "A4/A8",
            f"{t}s 覆盖 < 整帧 15% 且上半屏(y<180)无覆盖",
            area < 0.15 * W * H and top >= 180,
            f"覆盖面积占比={area / (W * H):.3%} 最高框 y={top}",
        )

    # A10:连续命中的采样秒合并为区间(而非逐秒碎片)
    ones = [e for e in entries if abs((e.end - e.start) - 1.0) < 1e-6]
    check(
        "A10",
        "连续命中已合并为区间条目",
        len(ones) < len(entries),
        f"总条目={len(entries)} 单秒条目={len(ones)}",
    )

    # A5 / A11:maskapply 后在基线框内为纯黑,且输出时长/帧率一致
    masked = Path(str(video) + ".auto.masked.mp4")
    proc = run_cli("maskapply", str(video), str(auto_path), "-o", str(masked))
    check(
        "A5",
        "jpsub maskapply 正常退出",
        proc.returncode == 0 and masked.exists(),
        (proc.stderr or proc.stdout).strip()[-300:],
    )
    if masked.exists():
        for t in POS_TIMES:
            img = frame_rgb(masked, t)
            boxes = [m for m in manual if m.start <= t < m.end]
            total = black = 0
            for b in boxes:
                sub = img[b.y : b.y + b.h, b.x : b.x + b.w]
                total += sub.shape[0] * sub.shape[1]
                black += int((sub.max(axis=2) <= 8).sum())
            ratio = black / total if total else 0.0
            check(
                "A5/A11",
                f"{t}s 基线框内 ≥80% 为纯黑",
                ratio >= 0.8,
                f"纯黑占比={ratio:.1%}({len(boxes)} 个基线框)",
            )
        dur_src, dur_out = video_duration(video), video_duration(masked)
        fps_src = probe(video, "stream=r_frame_rate")
        fps_out = probe(masked, "stream=r_frame_rate")
        check(
            "A5/A11",
            "输出时长与原片一致(±0.5s)",
            abs(dur_out - dur_src) <= 0.5,
            f"{dur_src:.2f}s -> {dur_out:.2f}s",
        )
        check(
            "A5/A11",
            "输出帧率与原片一致",
            fps_src == fps_out,
            f"{fps_src} -> {fps_out}",
        )
        masked.unlink(missing_ok=True)

    failed = [r for r in results if not r[2]]
    for ids, name, ok, detail in results:
        print(
            f"[{'PASS' if ok else 'FAIL'}] {ids} {name}"
            + (f" — {detail}" if detail else "")
        )
    print(
        f"\n合计 {len(results)} 项,通过 {len(results) - len(failed)},失败 {len(failed)}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
