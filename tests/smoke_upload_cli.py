"""smoke:投稿 CLI 入口端到端(无 cookie 场景)。

构造最小工作目录(info.txt + 假视频),跑 `jpsub upload <dir>`,断言:
  - 进程不崩溃(无 Traceback / ImportError / AttributeError);
  - 给出可读的「未登录」提示;
  - 退出码非 0。
覆盖 upload.py 的 read_info/build_meta/_find_upload_video/cookie 检查整条路径。

运行:.venv/bin/python tests/smoke_upload_cli.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="jpsub-up-smoke-") as td:
        work = Path(td) / "条目.jpsub"
        work.mkdir()
        (work / "info.txt").write_text(
            "https://www.nicovideo.jp/watch/sm9\n"
            "原标题:测试标题\n"
            "原作者:某人\n"
            "投稿时间:2026-01-01\n"
            "原简介:简介第一行\n"
            "标签:测试,搬运\n",
            encoding="utf-8",
        )
        (work / "video.mp4").write_bytes(b"\x00" * 2048)
        # 用隔离 HOME,确保没有现存 cookies
        home = Path(td) / "home"
        home.mkdir()

        env = dict(os.environ)
        env["HOME"] = str(home)
        env["PYTHONPATH"] = str(REPO)

        p = subprocess.run(
            [sys.executable, "-m", "jpsub.cli", "upload", str(work)],
            capture_output=True, text=True, env=env, timeout=120,
        )
        out = p.stdout + p.stderr
        print("--- 输出 ---")
        print(out.strip()[-500:])
        print("--- 退出码:", p.returncode, "---")

        fails = []
        for bad in ("Traceback", "ImportError", "ModuleNotFoundError", "AttributeError"):
            if bad in out:
                fails.append(f"输出出现 {bad}")
        if "未登录" not in out:
            fails.append("未给出「未登录」提示")
        if p.returncode == 0:
            fails.append("未登录仍返回成功退出码")

        if fails:
            for f in fails:
                print("FAIL", f)
            return 1
        print("PASS:投稿 CLI 入口可用,无 cookie 时给出可读提示且不崩溃")
        return 0


if __name__ == "__main__":
    sys.exit(main())
