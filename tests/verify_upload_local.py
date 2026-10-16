"""smoke:投稿路径使用本地 jpsub.bili,不再依赖 biliup Python 包。

阻断 biliup 模块后导入 jpsub.upload —— 若仍引用 biliup 则本测试失败。
同时断言裁剪保留项存在、裁剪项已移除(与 tests/verify_bili_vendor.py 的
AST 对拍互补:这里验运行时导入面,那里验源码等价性)。

运行:python -m unittest tests.verify_upload_local
"""

from __future__ import annotations

import sys
import unittest

_SAVED = {k: sys.modules.get(k) for k in ("biliup", "biliup.plugins", "biliup.plugins.bili_webup")}
sys.modules["biliup"] = None  # type: ignore[assignment]

from jpsub import bili, upload  # noqa: E402


class UploadLocalTest(unittest.TestCase):
    def test_uses_local_bili(self):
        self.assertIs(upload.BiliBili, bili.BiliBili, "upload 未使用本地 BiliBili")
        self.assertIs(upload.Data, bili.Data, "upload 未使用本地 Data")
        self.assertFalse(hasattr(upload, "_require_biliup"), "_require_biliup 未移除")

    def test_retained_methods(self):
        for name in ("upload_file", "upos", "_upload", "cover_up", "submit", "login_by_cookies"):
            self.assertTrue(hasattr(bili.BiliBili, name), f"BiliBili 缺少保留方法 {name}")
        for name in ("set_tag", "append", "delay_time"):
            self.assertTrue(hasattr(bili.Data, name), f"Data 缺少保留方法 {name}")

    def test_trimmed_methods_removed(self):
        for name in ("login_by_password", "submit_client", "cos", "kodo", "list_seasons"):
            self.assertFalse(hasattr(bili.BiliBili, name), f"裁剪项 {name} 仍存在")


if __name__ == "__main__":
    unittest.main(verbosity=2)
