"""port-karou-tools 验收脚本:逐条核对 brief/spec 的验收项(A1-A28)。

无网络、无 jpsubwin 依赖。AI 相关项用 mock 的 _chat 呈递,不接触真实接口。
运行:.venv/bin/python tests/verify_port_tools.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HOME", tempfile.mkdtemp(prefix="jpsub-verify-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jpsub import ai, aiassist, cli, handoff, subtitle
from jpsub.cache import TranslationCache
from jpsub.segment import Segment

PASS: list[str] = []
FAIL: list[str] = []


def check(aid: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(aid)
    mark = "ok  " if cond else "FAIL"
    print(f"[{mark}] {aid} {detail}")


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="karou-"))


# ---------------------------------------------------------------- A1/A22
def test_shift_jis_no_bom() -> None:
    body = (
        "1\n00:00:01,000 --> 00:00:03,000\nこんにちは世界\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\n今日はいい天気ですね\n\n"
    )
    raw = body.encode("shift_jis")  # 无 BOM
    text, label = subtitle.decode_bytes(raw, "auto")
    check("A1", "\ufffd" not in text and "こんにちは世界" in text, f"编码={label}")
    parsed = subtitle.parse(text)
    check("A22", len(parsed.entries) == 2 and parsed.entries[0].text == "こんにちは世界",
          f"条目={len(parsed.entries)}")


# ---------------------------------------------------------------- A2/A23
def test_prefix_fragments_merge() -> None:
    # 逐帧打字:后条以前条为前缀。用长句(非单行短帧)才不会被名字行规则提前封组,
    # 与烤肉参考实现 buildGroups 在同样输入下的分组一致。
    srt = (
        "1\n00:00:01,000 --> 00:00:02,000\nこれはとても長い台詞の前半部分です\n\n"
        "2\n00:00:02,500 --> 00:00:03,500\nこれはとても長い台詞の前半部分です続き\n\n"
        "3\n00:00:03,800 --> 00:00:05,000\n"
        "これはとても長い台詞の前半部分です続きもっと\n\n"
    )
    parsed = subtitle.parse(srt)
    groups = subtitle.build_groups(parsed.entries, 3.0)
    check("A2", len(groups) == 1 and len(groups[0]) == 3, f"组数={len(groups)}")
    flat = subtitle.flatten(groups, join_with="\\N", smart=True)
    e = flat[0]
    check("A23", e.start == 1.0 and e.end == 5.0 and "もっと" in e.text,
          f"{e.start}-{e.end} {e.text!r}")


# ---------------------------------------------------------------- A3/A24
def test_gap_over_cap_keeps_separate() -> None:
    srt = (
        "1\n00:00:01,000 --> 00:00:02,000\n同じ台詞\n\n"
        "2\n00:00:10,000 --> 00:00:11,000\n同じ台詞\n\n"
    )
    parsed = subtitle.parse(srt)
    groups = subtitle.build_groups(parsed.entries, 3.0)
    check("A3", len(groups) == 2, f"组数={len(groups)}")
    check("A24", len(groups) == 2, "间隔 8s > 3s,保持独立")


# ---------------------------------------------------------------- A4/A25
def test_name_frame_seals_group() -> None:
    # 名前帧(单行短)并入前组后封组:前条以该名字结尾 -> 并入并封组 -> 后续无关台词独立
    srt = (
        "1\n00:00:01,000 --> 00:00:02,000\n拓也が来た\n\n"
        "2\n00:00:02,200 --> 00:00:03,000\n拓也\n\n"
        "3\n00:00:03,200 --> 00:00:05,000\n拓也が走る\n\n"
    )
    parsed = subtitle.parse(srt)
    groups = subtitle.build_groups(parsed.entries, 3.0)
    texts = [[e.text for e in g] for g in groups]
    check("A4", len(groups) == 2 and texts[0] == ["拓也が来た", "拓也"], f"{texts}")
    check("A25", groups[1][0].text == "拓也が走る", "名字行封组,后续台词独立")


# ---------------------------------------------------------------- A5/A26
def test_layout_convert() -> None:
    src = "{\\pos(1,2)}第一行 第二行\n第三行"
    out = subtitle.layout_convert(src)
    ok = out == "{\\pos(1,2)}第一行\\N第二行\\N第三行"
    check("A5", ok, repr(out))
    check("A26", ok, "标签内容未被替换")


# ---------------------------------------------------------------- A6/A28 导入后可 render
def test_import_then_render() -> None:
    work = tmpdir() / "demo"
    work.mkdir()
    srt = tmpdir() / "a.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nおはよう\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nこんばんは\n\n",
        encoding="utf-8",
    )
    seg_file, n = subtitle.import_to_work(srt, work, enc="auto", fmt=None, cap=None,
                                          merge=False, layout=False)
    check("A6", seg_file.is_file() and n == 2, f"{seg_file.name} n={n}")
    args = cli.parse_args(["render", str(work)])
    out = cli._render(args)
    check("A28", Path(out).is_file() and Path(out).suffix == ".ass", str(out))
    body = Path(out).read_text(encoding="utf-8")
    check("A28", "おはよう" in body and "こんばんは" in body, "文本在 ASS 中")


# ---------------------------------------------------------------- A7/A27
def test_export_srt_after_merge() -> None:
    srt = (
        "1\n00:00:01,000 --> 00:00:02,000\n今日は\n\n"
        "2\n00:00:02,200 --> 00:00:03,000\n今日はいい\n\n"
    )
    parsed = subtitle.parse(srt)
    groups = subtitle.build_groups(parsed.entries, 3.0)
    flat = subtitle.flatten(groups, join_with="\\N")
    out = subtitle.to_srt(flat)
    check("A7", out.startswith("\ufeff"), "带 BOM")
    check("A27", re.search(r"\d{2}:\d{2}:\d{2},\d{3} --> ", out) is not None
          and out.count("-->") == len(flat), f"块数={out.count('-->')}")


# ---------------------------------------------------------------- A8/B1 术语表
def test_glossary_unified() -> None:
    sub = ai._glossary_sub("拓也が来た", {"拓也": "拓也"})
    check("A8", "⟦拓也⟧" in sub, repr(sub))


# ---------------------------------------------------------------- A9/B2 双协议
def test_dual_protocol_parse() -> None:
    a = ai._parse_reply_lines('```json\n["甲","乙"]\n```', 2)
    b = ai._parse_reply_lines("甲%%乙", 2)
    c = ai._parse_reply_lines("1 甲\n2 乙", 2)
    check("A9", a == ["甲", "乙"] and b == ["甲", "乙"] and c == ["甲", "乙"], f"{a} {b} {c}")


# ---------------------------------------------------------------- A10/B3 回显
def test_echo_detection() -> None:
    srcs = ["こんにちは", "世界です", "テスト"]
    ratio = ai.echo_ratio(srcs, srcs)
    check("A10", ratio == 1.0, f"echo_ratio={ratio}")
    check("A10", ai.echo_ratio(srcs, ["你好", "世界", "测试"]) == 0.0, "真实译文不误判")


def test_echo_blocks_write() -> None:
    import jpsub.settings as st
    st.BATCH_SIZE, st.BATCH_CHAR_TARGET = 2, 99999

    def echo(msgs, cfg, **_kw):
        lines = [re.sub(r"^\d+\s*", "", ln) for ln in msgs[-1]["content"].splitlines()]
        return "\n".join(f"{i + 1} {t}" for i, t in enumerate(lines))

    saved = ai._chat
    ai._chat = echo
    try:
        srcs = ["こんにちは", "世界です", "テスト", "おはよう"]
        res = ai.translate_texts_parallel(
            [(s, s) for s in srcs], {"api_base": "x", "api_key": "k", "model": "m"},
            concurrency=1, quiet=True, group_batch=2)
    finally:
        ai._chat = saved
    check("A10", all(v == handoff.UNTRANSLATED_MARK for v in res.values()),
          "回显批记未译,不写原文当译文")


    # 缩小批处理:仅多行请求回显、单行请求正常翻译时,应逐句重试取到真译文
    def echo_multiline(msgs, cfg, **_kw):
        lines = [re.sub(r"^\d+\s*", "", ln) for ln in msgs[-1]["content"].splitlines()]
        if len(lines) > 1:
            return "\n".join(f"{i + 1} {t}" for i, t in enumerate(lines))
        return "1 译_" + lines[0]

    ai._chat = echo_multiline
    try:
        res2 = ai.translate_texts_parallel(
            [(s, s) for s in srcs], {"api_base": "x", "api_key": "k", "model": "m"},
            concurrency=1, quiet=True, group_batch=2)
    finally:
        ai._chat = saved
    check("A10", all(v == "译_" + k.split("\n")[0] for k, v in res2.items()),
          "回显批缩小批处理后取到逐句真译文")


# ---------------------------------------------------------------- A11/B4 续翻
def test_resume_skips_done() -> None:
    import jpsub.settings as st
    st.BATCH_SIZE, st.BATCH_CHAR_TARGET = 5, 99999
    srcs = [f"句{i}" for i in range(10)]
    calls: list[tuple[str, ...]] = []

    def flaky(msgs, cfg, **_kw):
        lines = [re.sub(r"^\d+\s*", "", ln) for ln in msgs[-1]["content"].splitlines()]
        calls.append(tuple(lines))
        if any(t in ("句3", "句4") for t in lines):
            raise RuntimeError("HTTP 500:boom")
        return "\n".join(f"{i + 1} 译_{t}" for i, t in enumerate(lines))

    saved = ai._chat
    ai._chat = flaky
    try:
        r1 = ai.translate_texts_parallel([(s, s) for s in srcs],
                                         {"api_base": "x", "api_key": "k", "model": "m"},
                                         concurrency=1, quiet=True, group_batch=2)
    finally:
        ai._chat = saved
    failed = [k for k, v in r1.items() if v == handoff.UNTRANSLATED_MARK]
    check("A11", failed == ["句3", "句4"], f"失败={failed}")

    cache = TranslationCache(tmpdir() / "c.json")
    for k, v in r1.items():
        if v != handoff.UNTRANSLATED_MARK:
            cache.put(k, v)
    segs = [Segment(0, 1, s) for s in srcs]
    for s in segs:
        s.tr = cache.get(s.text)
    todo = handoff.pending_texts(segs, cache)
    check("A11", todo == ["句3", "句4"], f"续翻待办={todo}")

    calls.clear()
    saved = ai._chat
    ai._chat = flaky
    try:
        ai.translate_texts_parallel([(s, s) for s in todo],
                                    {"api_base": "x", "api_key": "k", "model": "m"},
                                    concurrency=1, quiet=True, group_batch=2)
    finally:
        ai._chat = saved
    sent = [t for c in calls for t in c]
    check("A17", not any(t in ("句0", "句1", "句2") for t in sent), f"重发={sent}")


# ---------------------------------------------------------------- A12/A18 校对
def test_proofread_keeps_timeline() -> None:
    rows = [("原文1", "旧译1"), ("原文2", "旧译2"), ("原文3", "旧译3")]

    def partial(msgs, cfg, **_kw):
        # 只回第 1 条,其余缺失 -> 未返回的保留原译文
        return "1|新译1"

    saved = ai._chat
    ai._chat = partial
    try:
        out = ai.proofread(rows, {"api_base": "x", "api_key": "k", "model": "m"},
                           batch_size=10, quiet=True)
    finally:
        ai._chat = saved
    check("A12", out[0] == "新译1" and out[1] == "旧译2" and out[2] == "旧译3", f"{out}")

    # 时间轴不变:校对只动 text,不动 start/end
    work = tmpdir() / "pf"
    work.mkdir()
    srt = tmpdir() / "b.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\n原文\n\n", encoding="utf-8")
    seg_file, _ = subtitle.import_to_work(srt, work, enc="auto", fmt=None, cap=None, merge=False)
    segs = handoff.read_segments(seg_file)
    for s in segs:  # 校对需要已有译文
        s.tr = "旧译_" + s.text
    handoff.write_segments(segs, seg_file)
    before = [(s.start, s.end) for s in segs]

    def full(msgs, cfg, **_kw):
        n = len(msgs[-1]["content"].splitlines())
        return "\n".join(f"{i + 1}|改{i + 1}" for i in range(n))

    saved = ai._chat
    os.environ["JPSUB_API_BASE"] = "http://mock"
    os.environ["JPSUB_API_KEY"] = "mock-key"
    os.environ["JPSUB_MODEL"] = "mock-model"
    ai._chat = full
    try:
        cli._proofread(cli.parse_args(["proofread", "--work", str(work)]))
    finally:
        ai._chat = saved
        for k in ("JPSUB_API_BASE", "JPSUB_API_KEY", "JPSUB_MODEL"):
            os.environ.pop(k, None)
    segs2 = handoff.read_segments(seg_file)
    check("A18", before == [(s.start, s.end) for s in segs2], "时间轴不变")


# ---------------------------------------------------------------- A13/A19 用量持久化
def test_usage_persisted() -> None:
    aiassist.clear_usage()
    # cached_tokens 位于 usage.prompt_tokens_details.cached_tokens(OpenAI 结构)
    aiassist.record_usage({"prompt_tokens": 1000, "completion_tokens": 500,
                           "prompt_tokens_details": {"cached_tokens": 200}})
    # 模拟「重启」:重新从磁盘读
    data = aiassist.load_usage()
    s = aiassist.usage_summary(data)
    check("A13", data["prompt_tokens"] == 1000 and data["completion_tokens"] == 500,
          f"{data}")
    check("A19", "1000" in s and "500" in s, s.replace("\n", " / "))
    # 费用:200*0.02 + (1000-200)*1.0 + 500*2.0 = 1804 /1e6
    expect_cost = (200 * aiassist.PRICE_HIT + (1000 - 200) * aiassist.PRICE_IN
                   + 500 * aiassist.PRICE_OUT) / 1e6
    check("A13", abs(data["cost"] - expect_cost) < 1e-12, f"cost={data['cost']}")
    aiassist.clear_usage()


# ---------------------------------------------------------------- A14/A20/A21 HTML 错误页
def test_html_error_readable() -> None:
    page = ("<html><head><title>502 Bad Gateway</title></head>"
            "<body><h1>502 Bad Gateway</h1></body></html>")
    msg = aiassist.readable_http_error(502, page, "Bad Gateway")
    check("A20", "<html>" not in msg and "网关" in msg, msg)

    cf = ("<!DOCTYPE html><html><head><title>Attention Required! | Cloudflare</title>"
          "</head><body>cf-ray: abc123 Just a moment...</body></html>")
    msg2 = aiassist.readable_http_error(403, cf, "Forbidden")
    check("A21", "Cloudflare" in msg2 or "拦截" in msg2, msg2)

    # 集成:_chat 的 HTTPError 分支应把 HTML 页转成可读提示(拦截 urlopen)
    import io
    import urllib.error
    import urllib.request

    class _Opener:
        def open(self, req, timeout=None):
            raise urllib.error.HTTPError(
                "http://x", 502, "Bad Gateway", {},
                io.BytesIO(page.encode()))

    saved = urllib.request.build_opener
    urllib.request.build_opener = lambda *a, **k: _Opener()
    try:
        ai._chat([{"role": "system", "content": "s"},
                  {"role": "user", "content": "u"}],
                 {"api_base": "http://x", "api_key": "k", "model": "m"},
                 retries=1, quiet=True)
        raised = None
    except Exception as e:  # noqa: BLE001
        raised = str(e)
    finally:
        urllib.request.build_opener = saved
    check("A14", raised is not None and "<html>" not in raised and "网关" in raised,
          str(raised))


# ---------------------------------------------------------------- A15 预设切换
def test_preset_switch() -> None:
    aiassist.save_presets({"presets": {}, "current": None})
    aiassist.set_preset("中转A", {"api_base": "http://a", "api_key": "ka", "model": "ma"})
    aiassist.set_preset("官方B", {"api_base": "http://b", "api_key": "kb", "model": "mb"},
                        select=True)
    cfg = aiassist.get_preset()
    check("A15", cfg.get("model") == "mb", f"当前={cfg}")


# ---------------------------------------------------------------- A16 自动角色名
def test_auto_name_candidates() -> None:
    texts = ["拓也", "拓也", "拓也", "これは普通の長い台詞です",
             "香織", "香織", "香織", "そうだね"]
    cands = aiassist.auto_name_candidates(texts, top=30)
    check("A16", "拓也" in cands and "香織" in cands, f"{cands}")
    check("A16", not any(len(c) > 12 for c in cands), "无长句候选")


def main() -> int:
    test_shift_jis_no_bom()
    test_prefix_fragments_merge()
    test_gap_over_cap_keeps_separate()
    test_name_frame_seals_group()
    test_layout_convert()
    test_import_then_render()
    test_export_srt_after_merge()
    test_glossary_unified()
    test_dual_protocol_parse()
    test_echo_detection()
    test_echo_blocks_write()
    test_resume_skips_done()
    test_proofread_keeps_timeline()
    test_usage_persisted()
    test_html_error_readable()
    test_preset_switch()
    test_auto_name_candidates()
    print(f"\n通过 {len(PASS)} 项,失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项:" + ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
