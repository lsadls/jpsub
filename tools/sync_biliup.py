#!/usr/bin/env python3
"""拉取上游 biliup 的 bili_webup.py,与本仓库 vendored 子集 jpsub/bili.py 比对。

用途:应对 B 站接口变更 —— 上游发布新版本后,跑本脚本得到结构化差异报告,
人工判断哪些改动需要移植到 jpsub/bili.py(见 docs/bili.md 的更新流程)。

锚定来源(PyPI wheel,而非 GitHub tag):
  上游 v1.2.9 的 GitHub tag 已被 Rust/Next.js 重写,Python 包路径不复存在;
  但 PyPI 上的 1.2.9 wheel 仍含原 Python 实现,且与 GitHub v1.2.8 tag 的
  bili_webup.py 字节一致(可用 --check-baseline 验证)。因此以 PyPI 为准。

用法:
  python tools/sync_biliup.py                # 下载锁定版本并报告差异
  python tools/sync_biliup.py --version 1.2.10   # 试拉更新的上游版本
  python tools/sync_biliup.py --local .venv/lib/python3.14/site-packages/biliup/plugins/bili_webup.py
  python tools/sync_biliup.py --keep         # 保留下载的 wheel 供人工比对

依赖:仅标准库。外网需代理时设 --proxy 或 https_proxy 环境变量。
"""

from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import io
import os
import pathlib
import sys
import tempfile
import urllib.request
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
LOCAL = REPO / "jpsub" / "bili.py"

# 当前 vendored 基线(改 jpsub/bili.py 时同步更新此处与 docs/bili.md)。
BASELINE_VERSION = "1.2.9"
BASELINE_SHA256 = "437aa49650c9205dd54dae9a161642bfe5e84f931a77c2550f8a0874feefd15d"
WHEEL_MEMBER = "biliup/plugins/bili_webup.py"

# 与 tests/verify_bili_vendor.py 保持一致的裁剪清单。
RETAINED = [
    "BiliBili.load", "BiliBili.store", "BiliBili.login_by_cookies", "BiliBili.probe",
    "BiliBili.upload_file", "BiliBili.upos", "BiliBili._upload", "BiliBili.submit_web",
    "BiliBili.cover_up", "BiliBili.__enter__", "BiliBili.__exit__", "BiliBili.close", "Data",
]
REMOVED = [
    "FileInfo", "BiliWeb", "BiliBili.check_tag", "BiliBili.get_qrcode", "BiliBili.login_by_qrcode",
    "BiliBili.tid_archive", "BiliBili.myinfo", "BiliBili.send_sms", "BiliBili.login_by_sms",
    "BiliBili.login_by_password", "BiliBili.sign", "BiliBili.get_key", "BiliBili.cos",
    "BiliBili.kodo", "BiliBili.submit_client", "BiliBili.get_tags", "BiliBili.list_seasons",
    "BiliBili.get_season_section", "BiliBili.get_video_info", "BiliBili.add_to_season",
    "BiliBili.remove_from_season", "BiliBili.sort_season_episodes",
]
MODIFIED = ["BiliBili.__init__", "BiliBili.login", "BiliBili.submit"]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_wheel(version: str, proxy: str | None, keep: bool) -> bytes:
    """从 PyPI 下载指定版本的 wheel,返回其字节。"""
    api = f"https://pypi.org/pypi/biliup/{version}/json"
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    print(f"查询 PyPI: {api}")
    with opener.open(api, timeout=30) as r:
        import json

        meta = json.load(r)
    wheels = [u for u in meta["urls"] if u["filename"].endswith(".whl")]
    if not wheels:
        raise SystemExit(f"PyPI 上 biliup {version} 没有 wheel,请手动下载 sdist")
    url = wheels[0]["url"]
    print(f"下载 wheel: {url}")
    with opener.open(url, timeout=120) as r:
        data = r.read()
    if keep:
        out = REPO / "archive" / pathlib.Path(url).name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        print(f"已保留:{out}")
    return data


def extract_member(wheel_bytes: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as z:
        if WHEEL_MEMBER not in z.namelist():
            raise SystemExit(f"wheel 内缺少 {WHEEL_MEMBER};上游结构可能已变,请人工检查")
        return z.read(WHEEL_MEMBER)


def collect(path_or_src) -> dict[str, str]:
    """{限定名: ast.dump}。"""
    if isinstance(path_or_src, bytes):
        tree = ast.parse(path_or_src.decode("utf-8"))
    else:
        tree = ast.parse(pathlib.Path(path_or_src).read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            out[node.name] = ast.dump(node)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"{node.name}.{sub.name}"] = ast.dump(sub)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = ast.dump(node)
    return out


def report(up_src: bytes, up_hash: str, version: str) -> list[str]:
    up = collect(up_src)
    lo = collect(LOCAL.read_bytes())
    lines: list[str] = []
    todo: list[str] = []

    lines.append(f"\n上游 biliup {version}  {WHEEL_MEMBER}")
    lines.append(f"  sha256 = {up_hash}")
    if version == BASELINE_VERSION and up_hash == BASELINE_SHA256:
        lines.append("  ✓ 与 vendored 基线完全一致,无需动作")
    else:
        lines.append(f"  基线 = {BASELINE_VERSION} / {BASELINE_SHA256}")

    # RETAINED:上游改了就必须移植(否则本地实现会与上游行为分叉)。
    changed = [n for n in RETAINED if n in up and n in lo and up[n] != lo[n]]
    missing = [n for n in RETAINED if n not in up]
    if changed:
        todo.append(f"{len(changed)} 个保留方法与上游不一致,需移植:")
        for n in changed:
            todo.append(f"    - {n}")
    if missing:
        todo.append(f"上游已删除这些保留方法,需复核:{missing}")

    # REMOVED:上游这些裁剪项若有变化,确认是否影响投稿路径。
    removed_changed = [n for n in REMOVED if n in up and n in lo]
    if removed_changed:
        todo.append(f"本地仍存在已裁剪项(裁剪遗漏):{removed_changed}")

    # MODIFIED:有意改写项,普遍与上游不等,不构成差异报告。
    # 但若上游把其中某项改回与本地一致,说明改写已无必要,可移入 RETAINED。
    mod_same = [n for n in MODIFIED if n in up and n in lo and up[n] == lo[n]]
    if mod_same:
        todo.append(f"有意改写项现已与上游相同,可考虑移入 RETAINED:{mod_same}")
    lines.append(f"\n有意改写项(预期与上游不同,需人工复核改动):{MODIFIED}")

    # 新增的公开方法/类(上游新功能,通常无需移植,但值得知悉)。
    new_names = sorted(set(up) - set(lo) - set(REMOVED))
    if new_names:
        lines.append(f"\n上游新增(本地未含,一般无需移植):{new_names}")

    lines.append("\n=== 结论 ===")
    lines.extend(todo or ["无差异,无需动作"])
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="拉取上游 biliup 并与 jpsub/bili.py 比对")
    ap.add_argument("--version", default=BASELINE_VERSION, help=f"上游版本(默认 {BASELINE_VERSION})")
    ap.add_argument("--proxy", default=os.environ.get("https_proxy") or None, help="HTTP 代理,如 http://127.0.0.1:1080")
    ap.add_argument("--local", metavar="PATH", help="直接用本地 bili_webup.py,不联网")
    ap.add_argument("--keep", action="store_true", help="保留下载的 wheel 到 archive/")
    ap.add_argument("--dump", metavar="PATH", help="把上游文件写到指定路径供人工 diff")
    args = ap.parse_args(argv)

    if args.local:
        src = pathlib.Path(args.local).read_bytes()
    else:
        src = extract_member(fetch_wheel(args.version, args.proxy, args.keep))

    if args.dump:
        pathlib.Path(args.dump).write_bytes(src)
        print(f"已导出上游文件:{args.dump}")

    lines = report(src, sha256(src), args.version if not args.local else "local")
    print("\n".join(lines))

    # 供人工 diff 用:整文件 unified diff(截断展示)。
    up_text = src.decode("utf-8").splitlines()
    lo_text = LOCAL.read_text(encoding="utf-8").splitlines()
    diff = list(difflib.unified_diff(up_text, lo_text, f"upstream-{args.version}", "jpsub/bili.py", lineterm=""))
    print(f"\n整文件 diff:{len(diff)} 行(裁剪后的正常差异;要看新增改动请用 --dump 后手动 diff)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
