# B 站投稿实现(jpsub/bili.py)

投稿功能不是直接依赖 `biliup` 包,而是把它上传所需的接口**裁剪后 vendor 进本仓库**(`jpsub/bili.py`),以便:

- 去掉 biliup 的重传递依赖(streamlink / yt-dlp / trio / lxml / pycountry / rsa 等约 20 个包,cookie 登录路径完全用不到);
- 投稿成为主依赖即可用,无需 `pip install "jpsub[bili]"`。

代价是**上游 B 站接口变更时需人工移植**。本文即为此维护流程。

## 来源锚定

| 项 | 值 |
| --- | --- |
| 仓库 | https://github.com/biliup/biliup |
| 上游文件 | `biliup/plugins/bili_webup.py` |
| 基线版本 | **1.2.9**(PyPI 发布物) |
| 基线 sha256 | `437aa49650c9205dd54dae9a161642bfe5e84f931a77c2550f8a0874feefd15d` |
| 作者 / 许可证 | ForgQi / MIT |

> **锚定陷阱(重要)**:上游 GitHub 的 **v1.2.9 tag 已被 Rust + Next.js 重写**,`biliup/plugins/bili_webup.py` 路径在该 tag 下不存在;而 PyPI 上 `biliup 1.2.9` 的 wheel 仍是 Python 实现,且其内容与 GitHub **v1.2.8** tag 的文件字节一致(已核对 sha256)。因此**同步一律以 PyPI wheel 为准**,不要用 GitHub tag 拉取。`tools/sync_biliup.py` 已按此实现。

## 裁剪清单

保留(投稿路径实际使用,与上游 AST 逐节点一致,除下表「有意改写」外):

- `Data`:稿件元信息容器
- `BiliBili.upload_file` / `upos` / `_upload`:upos 分片并发上传
- `BiliBili.login` / `load` / `store` / `login_by_cookies`:cookies 登录
- `BiliBili.submit` / `submit_web`:提交稿件
- `BiliBili.cover_up`:封面上传
- `BiliBili.probe` / `__enter__` / `__exit__` / `close`

移除(本仓库不使用,移除后消除对应依赖):

| 已移除 | 原因 |
| --- | --- |
| `login_by_password` / `sign` / `get_key` | 密码登录;唯一依赖 `rsa`,jpsub 走 biliup-rs 扫码 |
| `get_qrcode` / `login_by_qrcode` / `send_sms` / `login_by_sms` | 其它登录方式 |
| `cos` / `kodo` | 其它上传线路;上游 1.2.9 实际只走 upos(cos/kodo 分支已注释) |
| 合集(season)管理 6 个方法 | 投稿后不做合集操作 |
| `BiliWeb` / `FileInfo` / `myinfo` / `get_tags` / `check_tag` / `tid_archive` | biliup 命令行封装与辅助接口 |

有意改写(与上游不同,评审时重点看):

- `BiliBili.__init__`:去掉 `account`,日志命名空间 `biliup` → `jpsub`
- `BiliBili.login`:上游失败时回退密码登录;jpsub 只保留 cookies 单一路径,失败直接抛出
- `BiliBili.submit`:去掉 `submit_client`(客户端 API)与 `myinfo` 权重分支,只保留网页端提交

## 更新流程(上游发布新版本后)

```bash
# 1. 拉取上游新版本(默认走 PyPI;外网经代理)
python tools/sync_biliup.py --version 1.2.10 --proxy http://127.0.0.1:1080 --dump /tmp/bili_webup_up.py

# 2. 读报告:哪些「保留方法」在上游变了 —— 这些是必须移植的
#    报告还会指出裁剪遗漏、上游新增接口

# 3. 人工比对改动,移植到 jpsub/bili.py
diff -u /tmp/bili_webup_up.py jpsub/bili.py | less

# 4. 移植后:更新 jpsub/bili.py 顶部注释、本文档基线版本与 sha256、
#    tools/sync_biliup.py 的 BASELINE_VERSION / BASELINE_SHA256

# 5. 验证
python tools/sync_biliup.py --version 1.2.10   # 应显示 ✓ 与基线一致
python -m unittest tests.verify_bili_vendor tests.verify_upload_local
```

> 无需随上游小改动亦步亦趋:只有**保留方法**的改动才必须移植(否则本地实现会与上游行为分叉);新增接口一般与投稿路径无关。

## 自动校验

- `tests/verify_bili_vendor.py`:AST 逐节点比对 `jpsub/bili.py` 与上游 —— 保留项零漂移、裁剪项确已移除、改写项确有差异。上游文件优先取仓库内基线 `tests/fixtures/bili_webup_1.2.9.py`,其次本机安装的 biliup;都没有则跳过。
- `tests/verify_bili_parity.py`:HTTP 层对拍 —— 桩掉 `requests.Session`/`aiohttp.ClientSession`,用同一组预设响应驱动上游与本地实现,逐项比对登录/预上传/分片/合并/封面/提交的 method、URL、参数、表单,并覆盖分片边界(空文件/整除/末尾残片)。
- `tests/verify_upload_local.py`:阻断 `biliup` 模块后导入 `jpsub.upload`,确保运行时不再触碰该包(其余为登录用的外部二进制 `biliup-rs`,与本包无关)。
- `tests/smoke_upload_cli.py`:`jpsub upload <目录>` 端到端 smoke(无 cookie 场景)。

改动 `jpsub/bili.py` 后以上各项必须全绿。

### 基线 fixture

`tests/fixtures/bili_webup_1.2.9.py` 是上游 `biliup 1.2.9` 的原始 `bili_webup.py`(sha256 `437aa496...`,即 vendored 基线本身),供上述对拍测试离线使用。**不要修改或格式化此文件**——它是比对基准。更新上游版本时,按上文流程用 `tools/sync_biliup.py --dump` 覆盖它,并同步三处版本/sha 常量。
