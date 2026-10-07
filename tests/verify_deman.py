"""验收脚本:自动去人 `jpsub mask --auto`(change: improve-auto-deman)。

用法: ``.venv/bin/python tests/verify_deman.py [参考片路径]``
覆盖 brief A1-A11 与 spec 场景 A12-A18,逐项打印结果;全部通过时退出码 0。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _output_dir() -> Path:
    """对照集在承载 output/ 的仓库根;worktree 内无 output 时回退到主仓库。"""
    if (ROOT / "output").is_dir():
        return ROOT / "output"
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return Path(common).parent / "output"


OUTPUT_DIR = _output_dir()
REF_VIDEO = OUTPUT_DIR / "TAKUYA -タイト- 4話(終)_sm38156190" / "TAKUYA -タイト- 4話(終).mp4"
W, H = 640, 360
POS_TIMES = (16, 56, 1172)  # 人工基线覆盖、含实拍真人
NEG_TIMES = (1200, 600)  # 无肤色内容
RECALL_MIN = 0.35  # A2/A13
HALF_MIN = 0.15  # A3/A13
ENTRY_MAX = 0.60  # A4/A9/A15

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


def frame_rgb(path: Path, t: float):
    import numpy as np

    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(out, dtype=np.uint8)[: W * H * 3].reshape(H, W, 3)


def probe(path: Path, entries: str) -> str:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", entries, "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return r.stdout.strip()


def overlap(a, b) -> int:
    return max(0, min(a.x + a.w, b[0] + b[2]) - max(a.x, b[0])) * max(
        0, min(a.y + a.h, b[1] + b[3]) - max(a.y, b[1])
    )


def covering(entries, t):
    return [e for e in entries if e.start <= t < e.end]


def truth_path(video: Path, masks_path: Path) -> Path:
    bak = masks_path.with_suffix(".json.bak")
    return bak if bak.exists() else masks_path


def reference_cases():
    """对照集: output/ 下含人工标注的片子(排除 old.mp4/.cut 等副本)。"""
    from jpsub.mask import default_masks_path, parse_masks, video_duration

    cases = []
    for mf in sorted(OUTPUT_DIR.glob("*/.jpsub/masks.json")):
        d = mf.parent.parent
        vids = [
            p for p in d.glob("*.mp4")
            if not p.name.endswith(".masked.mp4")
            and not p.name.startswith("old")
            and ".cut" not in p.name
        ]
        if not vids:
            continue
        video = max(vids, key=lambda p: (video_duration(p), p.stat().st_size))
        src = truth_path(video, default_masks_path(video))
        cases.append((d.name, video, parse_masks(src), src.name))
    return cases


def main() -> int:
    from jpsub.mask import (
        auto_masks_path,
        default_masks_path,
        parse_masks,
        video_duration,
    )

    ref = Path(sys.argv[1]) if len(sys.argv) > 1 else REF_VIDEO
    if not ref.exists():
        print(f"错误:参考片不存在:{ref}")
        return 2

    # A16:先跑对照集,验证自动结果落盘、手工清单未被改动
    cases = reference_cases()
    manual_before = {
        str(default_masks_path(v)): hashlib.sha256(
            default_masks_path(v).read_bytes()
        ).hexdigest()
        for _, v, _, _ in cases
    }
    ref_manual = default_masks_path(ref)
    ref_manual_before = hashlib.sha256(ref_manual.read_bytes()).hexdigest()

    tot_sec = tot_hit = tot_half = 0
    max_entry = 0.0
    parse_ok = True
    trace = []
    for name, video, manual, srcname in cases:
        proc = run_cli("mask", "--auto", str(video))
        if proc.returncode != 0:
            check("A1", f"{name[:20]} mask --auto 退出码 0", False, proc.stderr[-200:])
        auto_path = auto_masks_path(video)
        if not auto_path.exists():
            parse_ok = False
            trace.append(f"{name[:16]}: 无 masks.auto.json")
            continue
        try:
            auto = parse_masks(auto_path)
        except Exception as exc:  # noqa: BLE001
            parse_ok = False
            trace.append(f"{name[:16]}: 解析失败 {exc}")
            continue
        legal = bool(auto) and all(
            e.start < e.end and e.w > 0 and e.h > 0 and e.effect for e in auto
        )
        parse_ok = parse_ok and legal
        fa = W * H
        max_entry = max(max_entry, *(e.w * e.h / fa for e in auto))
        by_t: dict[int, list] = {}
        for m in manual:
            by_t.setdefault(int(m.start), []).append(m)
        c_hit = c_half = 0
        for t, boxes in by_t.items():
            cov = covering(auto, t)
            tot_sec += 1
            if any(overlap(e, (b.x, b.y, b.w, b.h)) > 0 for e in cov for b in boxes):
                tot_hit += 1
                c_hit += 1
            best = max(
                (min(1.0, sum(overlap(e, (b.x, b.y, b.w, b.h)) for e in cov) / (b.w * b.h))
                 for b in boxes if b.w * b.h > 0),
                default=0.0,
            )
            if best >= 0.5:
                tot_half += 1
                c_half += 1
        trace.append(
            f"{name[:20]:22s}[{srcname:14s}] auto={len(auto):4d} 基线秒={len(by_t):3d} "
            f"命中={c_hit:3d}({c_hit / max(1, len(by_t)):.0%}) >=50%={c_half:3d}"
        )
    for line in trace:
        print("  " + line)

    recall = tot_hit / tot_sec if tot_sec else 0.0
    half = tot_half / tot_sec if tot_sec else 0.0
    check("A1", "对照集 mask --auto 全部产出可解析 auto 文件", parse_ok, f"共 {len(cases)} 部")
    check("A2/A13", f"对照集相交召回 {recall:.0%} >= {RECALL_MIN:.0%}",
          recall >= RECALL_MIN, f"{tot_hit}/{tot_sec}")
    check("A3/A13", f"≥50% 面积命中 {half:.0%} >= {HALF_MIN:.0%}",
          half >= HALF_MIN, f"{tot_half}/{tot_sec}")
    check("A4/A15", f"单条最大面积 {max_entry:.0%} < {ENTRY_MAX:.0%}",
          max_entry < ENTRY_MAX)
    unchanged = all(
        hashlib.sha256(default_masks_path(v).read_bytes()).hexdigest() == manual_before[str(default_masks_path(v))]
        for _, v, _, _ in cases
    )
    check("A5/A16", "对照集手工 masks.json 未被修改", unchanged)

    # 参考片:重跑自动检测
    proc = run_cli("mask", "--auto", str(ref))
    check("A6", "参考片 mask --auto 正常退出", proc.returncode == 0, proc.stderr[-200:])
    auto_path = auto_masks_path(ref)
    auto = parse_masks(auto_path) if auto_path.exists() else []
    ref_manual_ok = (
        hashlib.sha256(ref_manual.read_bytes()).hexdigest() == ref_manual_before
    )
    check("A16", "参考片手工 masks.json 未被修改", ref_manual_ok)
    manual = parse_masks(truth_path(ref, ref_manual))
    fa = W * H

    for t in POS_TIMES:
        cov = covering(auto, t)
        boxes = [m for m in manual if m.start <= t < m.end]
        hit = any(overlap(e, (b.x, b.y, b.w, b.h)) > 0 for e in cov for b in boxes)
        check("A7/A12", f"{t}s 有覆盖且与基线框相交",
              bool(cov) and hit, f"覆盖={len(cov)} 基线框={len(boxes)}")

    for t in NEG_TIMES:
        area = sum(e.w * e.h for e in covering(auto, t))
        check("A8/A14", f"{t}s 覆盖 < 整帧 5%",
              area < 0.05 * fa, f"占比={area / fa:.1%}")

    ref_max = max((e.w * e.h / fa for e in auto), default=0.0)
    check("A9", f"参考片单条最大面积 {ref_max:.0%} < {ENTRY_MAX:.0%}",
          ref_max < ENTRY_MAX)

    ones = [e for e in auto if abs((e.end - e.start) - 1.0) < 1e-6]
    check("A17", "连续命中已合并为区间条目",
          bool(auto) and len(ones) < len(auto),
          f"总条目={len(auto)} 单秒条目={len(ones)}")

    # A10/A11/A18:maskapply 后基线框内纯黑,时长/帧率一致
    masked = Path(str(ref) + ".auto.masked.mp4")
    proc = run_cli("maskapply", str(ref), str(auto_path), "-o", str(masked))
    check("A18", "jpsub maskapply 正常退出且产出 .masked.mp4",
          proc.returncode == 0 and masked.exists(), proc.stderr[-200:])
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
            check("A10/A18", f"{t}s 基线框内 ≥80% 为纯黑",
                  ratio >= 0.8, f"纯黑={ratio:.1%}")
        dur_src, dur_out = video_duration(ref), video_duration(masked)
        fps_src, fps_out = probe(ref, "stream=r_frame_rate"), probe(masked, "stream=r_frame_rate")
        check("A11/A18", "输出时长与原片一致(±0.5s)",
              abs(dur_out - dur_src) <= 0.5, f"{dur_src:.2f}s -> {dur_out:.2f}s")
        check("A11/A18", "输出帧率与原片一致", fps_src == fps_out, f"{fps_src} -> {fps_out}")
        masked.unlink(missing_ok=True)

    failed = [r for r in results if not r[2]]
    for ids, name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {ids} {name}" + (f" — {detail}" if detail else ""))
    print(f"\n合计 {len(results)} 项,通过 {len(results) - len(failed)},失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
