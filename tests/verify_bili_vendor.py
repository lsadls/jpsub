"""校验 jpsub/bili.py(vendored 子集)与上游 biliup 的一致性。

三类断言:
  1. RETAINED   —— 保留的方法体与上游 AST 完全一致(裁剪未引入行为漂移);
  2. REMOVED    —— 确认已删方法在上游存在、本地不存在(裁剪生效);
  3. MODIFIED   —— 有意改写的方法(仅去密码登录/客户端提交分支),记录并断言两侧都存在。

需本机已安装 biliup(对拍基线);未安装时跳过,不视为失败。
用法:python tests/verify_bili_vendor.py [上游 bili_webup.py 路径]
"""

from __future__ import annotations

import ast
import pathlib
import sys

LOCAL = pathlib.Path(__file__).resolve().parent.parent / "jpsub" / "bili.py"
REPO = pathlib.Path(__file__).resolve().parent.parent
# 仓库内固化的上游基线(供离线校验;勿修改)。更新方式见 docs/bili.md。
FIXTURE = REPO / "tests" / "fixtures" / "bili_webup_1.2.9.py"

RETAINED = [
    "BiliBili.load",
    "BiliBili.store",
    "BiliBili.login_by_cookies",
    "BiliBili.probe",
    "BiliBili.upload_file",
    "BiliBili.upos",
    "BiliBili._upload",
    "BiliBili.submit_web",
    "BiliBili.cover_up",
    "BiliBili.__enter__",
    "BiliBili.__exit__",
    "BiliBili.close",
    "Data",  # 整个 dataclass(字段 + 方法)
]

REMOVED = [
    "FileInfo",
    "BiliWeb",
    "BiliBili.check_tag",
    "BiliBili.get_qrcode",
    "BiliBili.login_by_qrcode",
    "BiliBili.tid_archive",
    "BiliBili.myinfo",
    "BiliBili.send_sms",
    "BiliBili.login_by_sms",
    "BiliBili.login_by_password",
    "BiliBili.sign",
    "BiliBili.get_key",
    "BiliBili.cos",
    "BiliBili.kodo",
    "BiliBili.submit_client",
    "BiliBili.get_tags",
    "BiliBili.list_seasons",
    "BiliBili.get_season_section",
    "BiliBili.get_video_info",
    "BiliBili.add_to_season",
    "BiliBili.remove_from_season",
    "BiliBili.sort_season_episodes",
]

MODIFIED = ["BiliBili.__init__", "BiliBili.login", "BiliBili.submit"]


def collect(path: pathlib.Path) -> dict[str, str]:
    """返回 {限定名: ast.dump},含类方法(Class.method)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}

    def dump(node: ast.AST) -> str:
        return ast.dump(node)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            out[node.name] = dump(node)  # 整类(不含方法时也登记)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"{node.name}.{sub.name}"] = dump(sub)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = dump(node)
    return out


def find_upstream(argv: list[str]) -> pathlib.Path | None:
    """定位上游 bili_webup.py:显式参数 > 仓库内基线 fixture > 本机安装的 biliup。"""
    if len(argv) > 1:
        p = pathlib.Path(argv[1])
        return p if p.is_file() else None
    if FIXTURE.is_file():
        return FIXTURE
    try:
        from biliup.plugins import bili_webup
    except ImportError:
        return None
    return pathlib.Path(bili_webup.__file__)


def run(upstream: pathlib.Path) -> list[str]:
    up = collect(upstream)
    lo = collect(LOCAL)
    fails: list[str] = []
    ok = 0

    for name in RETAINED:
        if name not in up:
            fails.append(f"[RETAINED] 上游缺少 {name}(上游结构已变,需重新裁剪)")
        elif name not in lo:
            fails.append(f"[RETAINED] 本地缺少 {name}")
        elif up[name] != lo[name]:
            fails.append(f"[RETAINED] {name} 与上游不一致 —— 裁剪引入了行为漂移")
        else:
            ok += 1

    for name in REMOVED:
        if name not in up:
            fails.append(f"[REMOVED] 上游已无 {name}(上游重构,请复核裁剪清单)")
        elif name in lo:
            fails.append(f"[REMOVED] 本地仍存在 {name} —— 裁剪遗漏")
        else:
            ok += 1

    for name in MODIFIED:
        if name not in up or name not in lo:
            fails.append(f"[MODIFIED] {name} 缺失(上游或本地)")
        elif up[name] == lo[name]:
            fails.append(f"[MODIFIED] {name} 与上游相同 —— 若已无需改造,请移入 RETAINED")
        else:
            ok += 1

    print(f"检查项 {len(RETAINED) + len(REMOVED) + len(MODIFIED)} 项,通过 {ok} 项")
    return fails


def test_vendor_matches_upstream() -> None:
    upstream = find_upstream([])
    if upstream is None:
        print("SKIP:未找到 biliup(对拍基线),跳过")
        return
    fails = run(upstream)
    assert not fails, "\n".join(fails)


if __name__ == "__main__":
    upstream = find_upstream(sys.argv)
    if upstream is None:
        print("SKIP:未找到上游 bili_webup.py;可传入路径作为参数")
        raise SystemExit(0)
    fails = run(upstream)
    for f in fails:
        print("FAIL", f)
    if fails:
        raise SystemExit(1)
    print("PASS:jpsub/bili.py 与上游 biliup 一致(保留项零漂移,裁剪项已移除)")
