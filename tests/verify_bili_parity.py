"""对拍:jpsub/bili.py(裁剪实现) 与上游 biliup 在 HTTP 层的请求序列一致性。

做法:把 requests.Session 与 aiohttp.ClientSession 换成录制桩,用同一组预设响应
驱动两侧的 BiliBili,逐项比较发出的请求(method / url / params / data / json)。

基线来源:tests/fixtures/bili_webup_1.2.9.py(上游 biliup 1.2.9 原文件,
sha256 = 437aa496...,与 jpsub/bili.py 顶部记录的 vendored 基线一致)。
基线不可用或不匹配时跳过。基线更新方式见 docs/bili.md。

运行:python -m unittest tests.verify_bili_parity
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import types
import unittest

BASELINE_SHA = "437aa49650c9205dd54dae9a161642bfe5e84f931a77c2550f8a0874feefd15d"
REPO = pathlib.Path(__file__).resolve().parent.parent
UPSTREAM_CANDIDATES = [
    # 仓库内固化基线(上游 biliup 1.2.9 的 bili_webup.py,sha256 见下)。
    REPO / "tests/fixtures/bili_webup_1.2.9.py",
    # 若本机装了 biliup,也接受其安装副本。
    *sorted(REPO.glob(".venv/lib/python3*/site-packages/biliup/plugins/bili_webup.py")),
]

COOKIES = {
    "cookie_info": {"cookies": [{"name": "SESSDATA", "value": "s"}, {"name": "bili_jct", "value": "JCT"}]},
    "token_info": {"access_token": "at", "refresh_token": "rt"},
}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.content = json.dumps(payload).encode()
        self.headers = {"Etag": "etag-stub"}

    def json(self):
        return self._payload


class RecorderSession:
    """包装真实 requests.Session 以复用 cookie jar / headers,只拦截请求。"""

    def __init__(self, routes, log, factory):
        self._inner = factory()  # factory 是未被 patch 的真实 requests.Session
        self._routes = routes
        self._log = log

    def __getattr__(self, name):
        return getattr(self._inner, name)

    @property
    def cookies(self):
        return self._inner.cookies

    @property
    def headers(self):
        return self._inner.headers

    def mount(self, *a, **k):
        return self._inner.mount(*a, **k)

    def close(self):
        return self._inner.close()

    def request(self, method, url, **kw):
        payload = None
        for frag, resp in self._routes:
            if frag in url:
                payload = resp
                break
        self._log.append({
            "method": method.upper(),
            "url": url,
            "params": kw.get("params"),
            "data": kw.get("data"),
            "json": kw.get("json"),
        })
        return FakeResponse(payload if payload is not None else {"code": -1})

    def get(self, url, **kw):
        return self.request("get", url, **kw)

    def post(self, url, **kw):
        return self.request("post", url, **kw)


class FakeAiohttpSession:
    def __init__(self, log):
        self._log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def put(self, url, params=None, data=None, headers=None, **kw):
        self._log.append({"method": "PUT", "url": url, "params": params, "data_len": len(data or b"")})
        outer = self

        class _Ctx:
            async def __aenter__(self_inner):
                outer._log.append({"method": "PUT-RESP", "url": url})
                return FakeResponse({}, status=200)

            async def __aexit__(self_inner, *a):
                return False

        return _Ctx()


def load_upstream():
    for p in UPSTREAM_CANDIDATES:
        if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == BASELINE_SHA:
            sys.modules.setdefault("rsa", types.ModuleType("rsa"))  # 仅密码登录用,桩掉顶层 import
            spec = importlib.util.spec_from_file_location("_up_bili_webup", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


UPSTREAM = load_upstream()

PREUPLOAD = {
    "chunk_size": 4,
    "auth": "AUTH",
    "endpoint": "//upos-cs-upcdnbda2.bilivideo.com",
    "biz_id": "BIZ",
    "upos_uri": "upos://bucket/video.mp4",
}
ROUTES = [
    ("web-interface/nav", {"code": 0, "data": {"mid": 1}}),
    ("?uploads&output=json", {"upload_id": "UID"}),
    ("preupload", PREUPLOAD),
    # upos 合并分片(POST 到上传地址,参数含 name/uploadId),需返回 OK=1,否则触发 15s 重试
    ("/video.mp4", {"OK": 1}),
    ("cover/up", {"code": 0, "data": {"url": "http://i0.hdslb.com/cover.jpg"}}),
    ("vu/web/add", {"code": 0, "data": {"bvid": "BV1", "aid": 2}}),
]


def run_flow(mod, video_bytes: bytes):
    """跑 login → upload_file → cover_up → submit_web,返回请求日志。"""
    log: list[dict] = []
    aio_log: list[dict] = []

    bili_mod = mod
    real_session = bili_mod.requests.Session
    bili_mod.requests.Session = lambda: RecorderSession(ROUTES, log, real_session)
    real_aio = bili_mod.aiohttp.ClientSession
    bili_mod.aiohttp.ClientSession = lambda: FakeAiohttpSession(aio_log)

    from PIL import Image

    try:
        data = bili_mod.Data()
        data.title = "标题"
        data.desc = "简介"
        data.set_tag(["a", "b"])
        with bili_mod.BiliBili(data) as bili:
            bili.login_by_cookies(COOKIES)
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                f.write(video_bytes)
                path = f.name
            part = bili.upload_file(path, "bda2", 1)
            part["title"] = "标题"
            data.append(part)
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as c:
                cover_path = c.name
            Image.new("RGB", (32, 20), (10, 20, 30)).save(cover_path, format="JPEG")
            data.cover = bili.cover_up(cover_path).replace("http:", "")
            ret = bili.submit("web")
            assert ret["code"] == 0
    finally:
        bili_mod.requests.Session = real_session
        bili_mod.aiohttp.ClientSession = real_aio
    return log, aio_log


def normalize(entry: dict) -> dict:
    """归一化:折叠大 bytes、屏蔽测试临时文件名的随机部分。"""
    import copy
    import re

    def walk(o):
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        if isinstance(o, bytes):
            return f"<bytes:{len(o)}>"
        if isinstance(o, str):
            return re.sub(r"\S*tmp\S*\.\w+", "<FILE>", o)
        return o

    return walk(copy.deepcopy(entry))


@unittest.skipIf(UPSTREAM is None, "未找到与基线 sha 一致的上游 bili_webup.py")
class ParityTest(unittest.TestCase):
    @staticmethod
    def _local():
        from jpsub import bili

        return bili

    def test_request_sequence_matches(self):
        payload = b"0123456789"  # 4 字节 chunk → 3 片
        up_log, up_aio = run_flow(UPSTREAM, payload)
        lo_log, lo_aio = run_flow(self._local(), payload)

        self.assertEqual(len(up_log), len(lo_log), f"请求数不同:\n上={up_log}\n下={lo_log}")
        for i, (u, l) in enumerate(zip(up_log, lo_log)):
            self.assertEqual(normalize(u), normalize(l), f"第 {i} 个请求不一致")

        # 分片上传的 URL/参数(元数据)一致
        up_parts = [e for e in up_aio if e["method"] == "PUT"]
        lo_parts = [e for e in lo_aio if e["method"] == "PUT"]
        self.assertEqual(len(up_parts), 3)
        self.assertEqual(len(lo_parts), 3, "分片数不同")
        for u, l in zip(up_parts, lo_parts):
            self.assertEqual(u, l, "分片请求不一致")

    def test_submit_payload_matches(self):
        payload = b"0123456789"
        up_log, _ = run_flow(UPSTREAM, payload)
        lo_log, _ = run_flow(self._local(), payload)
        up_add = next(e for e in up_log if "vu/web/add" in e["url"])
        lo_add = next(e for e in lo_log if "vu/web/add" in e["url"])
        self.assertEqual(up_add["url"], lo_add["url"], "提交 URL(含 csrf)不一致")
        self.assertEqual(up_add["json"], lo_add["json"], "提交表单不一致")

    def test_chunk_boundaries(self):
        """chunk_size=4:0 字节 / 整除 / 末尾残片三种边界的分片数一致。"""
        for payload in (b"", b"1234", b"12345678", b"1234567"):
            up_log, up_aio = run_flow(UPSTREAM, payload)
            lo_log, lo_aio = run_flow(self._local(), payload)
            up_puts = [e for e in up_aio if e["method"] == "PUT"]
            lo_puts = [e for e in lo_aio if e["method"] == "PUT"]
            self.assertEqual(
                [(e["params"]["chunk"], e["data_len"]) for e in up_puts],
                [(e["params"]["chunk"], e["data_len"]) for e in lo_puts],
                f"payload={payload!r} 分片不一致",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
