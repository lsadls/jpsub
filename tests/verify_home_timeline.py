"""验收检查:主页生命周期(关闭/多开/刷新)与字幕时间轴(帧编号/切分/单调)。

用法: ``.venv/bin/python tests/verify_home_timeline.py``;全部通过时退出码 0。
覆盖 brief 的 A1-A4、A6-A8 与 spec 场景 A9-A13、A15-A17。
A5/A14(「退出程序」按钮)会终止进程,单独由 Verifier 验证,不含在本脚本内。
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from jpsub import cli, home as home_mod, segment  # noqa: E402
from jpsub.ocr import BaiduOcrEngine as E  # noqa: E402


def check_marker_font() -> str:
    """A6/A15:候选字体全不可用时仍为 j>0 的帧绘制编号(不抛异常)。"""
    saved = E._MARKER_FONTS
    E._MARKER_FONTS = ("/nonexistent/font-a.ttf", "/nonexistent/font-b.ttf")
    try:
        assert E._marker_font(34) is not None, "字体回退返回 None"
        eng = E.__new__(E)
        h, sep = 60, E._SEP
        imgs = [Image.new("RGB", (200, h), (30, 30, 30)) for _ in range(3)]
        canvas = eng._stitch(imgs, 200, h)
    finally:
        E._MARKER_FONTS = saved

    def white(j: int) -> int:
        top = j * (h + sep)
        raw = canvas.crop((0, top, 200, top + sep)).tobytes()
        return sum(
            1
            for k in range(0, len(raw), 3)
            if raw[k] > 150 and raw[k + 1] > 150 and raw[k + 2] > 150
        )

    assert white(1) > 0 and white(2) > 0, "第 1/2 帧分隔带无编号像素"
    assert white(0) == 0, "第 0 帧分隔带不应有编号"
    return "第 1、2 帧绘制出编号像素,第 0 帧无编号"


def check_marker_split() -> str:
    """A7/A16:无位置版接口无编号返回时不被采用,换下一个接口。"""
    saved_eps, saved_ex = E._BATCH_ENDPOINTS, E._exhausted
    E._exhausted = set()
    E._BATCH_ENDPOINTS = (("accurate_basic", False), ("general_basic", False))
    try:
        eng = E.__new__(E)
        eng._save_state = lambda *a, **k: None
        calls: list[str] = []
        canvas = Image.new("RGB", (200, 240), (0, 0, 0))

        def no_marker(endpoint, b64):
            calls.append(endpoint)
            return {"words_result": [{"words": "メロス"}, {"words": "走れ"}]}

        eng._call = no_marker
        try:
            eng._run_stitched(canvas, 4, 60)
        except RuntimeError:
            pass
        else:
            raise AssertionError("无编号的切分结果被采用")
        assert calls == ["accurate_basic", "general_basic"], calls

        calls.clear()

        def second_ok(endpoint, b64):
            calls.append(endpoint)
            if endpoint == "accurate_basic":
                return {"words_result": [{"words": "メロス"}]}  # 无编号:应被拒绝
            return {"words_result": [{"words": "1"}, {"words": "走れ"}]}

        eng._call = second_ok
        out = eng._run_stitched(canvas, 4, 60)
        assert calls == ["accurate_basic", "general_basic"], calls
        assert out[0] == "" and out[1] == "走れ", out
    finally:
        E._BATCH_ENDPOINTS, E._exhausted = saved_eps, saved_ex
    return "无编号返回被拒绝并降级到下一接口,编号返回正确切分"


def check_timeline() -> str:
    """A8/A17:修复后的各帧文本构建段,起点严格递增、首段为 0、不被压缩。"""
    frame_dur = 0.5
    n = 40
    timed = [(round(i * frame_dur, 3), f"第{i}句。次{i}。") for i in range(n)]
    segs = segment.build_segments(timed, frame_dur=frame_dur)
    assert segs and segs[0].start == 0, segs[:1]

    class Cache:
        def get(self, _p):
            return "译文"

    expanded, _ = cli._expand_long_segments(segs, Cache())
    starts = [round(s.start, 3) for s in expanded]
    assert starts[0] == 0, starts[:3]
    assert all(b > a for a, b in zip(starts, starts[1:])), "段起点未严格递增"
    assert expanded[-1].end >= (n - 2) * frame_dur, (
        f"时间轴被压缩:末段 end={expanded[-1].end}"
    )
    return f"{len(expanded)} 段起点严格递增,首段 0,末段 end={expanded[-1].end}"


def check_home_lifecycle() -> str:
    """A1-A4/A9-A13:关一个页面/子进程退出/刷新不退出,最后页面关闭才退出。"""
    tmp = tempfile.mkdtemp(prefix="jpsub-home-check-")
    h = home_mod._Home(Path(tmp))
    addr = f"http://127.0.0.1:{h.srv.server_address[1]}"
    threading.Thread(target=h.srv.serve_forever, daemon=True).start()

    def get(path: str):
        with urllib.request.urlopen(addr + path, timeout=5) as r:
            return r.status, r.read()

    def post(path: str, body: bytes = b"") -> None:
        req = urllib.request.Request(addr + path, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()

    def alive() -> bool:
        try:
            return get("/ping")[0] == 200
        except Exception:  # noqa: BLE001
            return False

    assert alive(), "服务未启动"
    get("/page-open?page=p1")
    get("/ping?page=p1&t=1")
    get("/page-open?page=p2")
    get("/ping?page=p2&t=1")

    # A3/A11:两个主页页面,关一个不退出
    post("/page-close?page=p1")
    time.sleep(2.6)
    assert alive(), "关闭两个页面之一后主页退出了"
    assert get("/ping?page=p2&t=2")[0] == 200, "剩余页面请求异常"

    # A1/A2/A9/A10:launch 型子进程退出不联动主页
    idx = h.spawn("测试编辑页", [sys.executable, "-c", "pass"], kind="launch")
    h.spawn("测试打码页", [sys.executable, "-c", "import sys; sys.exit(3)"], kind="launch")
    time.sleep(1.2)
    assert alive(), "launch 子进程退出后主页退出了"
    assert h.jobs[idx]["st"] == "done", h.jobs[idx]

    # A4/A13:最后一个页面注销后宽限期内重新注册(刷新)→ 不退出
    post("/page-close?page=p2")
    get("/page-open?page=p2b")
    time.sleep(2.6)
    assert alive(), "刷新(宽限期内重新注册)导致主页退出"

    # A12:最后一个页面注销且无新注册 → 退出(静默等待,避免请求取消宽限)
    post("/page-close?page=p2b")
    time.sleep(6.0)
    try:
        get("/ping")
        stopped = False
    except Exception:  # noqa: BLE001
        stopped = True
    assert stopped, "最后一个主页页面关闭后程序未退出"
    return json.dumps({"jobs": len(h.jobs)})


CHECKS = (
    ("marker-font-fallback", check_marker_font),
    ("marker-split-fallback", check_marker_split),
    ("monotonic-timeline", check_timeline),
    ("home-lifecycle", check_home_lifecycle),
)


def main() -> int:
    failed = 0
    for name, fn in CHECKS:
        try:
            note = fn()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {e}")
        else:
            print(f"OK {name}: {note}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
