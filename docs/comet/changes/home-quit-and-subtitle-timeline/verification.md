---
generated_from_state_version: 9
---

# 验证

## 当前结果

- 结果: **验收通过，可归档**
- 验证情况: **已完成检查，验证结果已确认**
- 目标周期: 1
- 迭代: 1
- 验证器尝试次数: 1
- 完成时间: 2026-10-06T10:58:57.951Z
- 摘要: 独立只读核查 A1-A17 全部通过:launch 型 edit/mask 子进程退出不再联动主页 shutdown,仅更新任务状态与日志;pageId 注册/心跳/注销 + 8 秒 TTL + 2 秒宽限实现多页面感知退出(关闭两页面之一或刷新均不退出,最后一个页面关闭且无新注册才退出);/quit 非空 body 仍终止全部存活子进程并退出,/quit 空 body 兼容信号在仍有存活页面时忽略;_stitch 帧编号字体跨平台候选 + Pillow 内置字体回退,字体全不可用时仍绘制编号;_run_stitched 无位置版接口在批内 n>1 且有识别行却无任何区间编号时判该接口失败并降级,全失败逐帧兜底,不再把整批文本并入首帧;修复后段起点严格递增、首段为 0、时间轴未被压缩;docs/home.md 行为说明已同步。Runtime 检查 verify-home-timeline 独立结果与本次核查一致。

## 验收

| 编号 | 结果 | 来源 | 验收项 | 原因 |
| --- | --- | --- | --- | --- |
| A1 | passed | brief.md | A1：打开主页并启动一个译文编辑器（edit）页，关闭该编辑页使其服务进程退出；主页服务仍可访问（`/ping` 返回 200），主页任务列表不进入退出状态，主页进程不退出。 | jpsub/home.py 中 launch 型 _watch 只更新任务状态与日志,不再调用 srv.shutdown;独立运行中 edit 类子进程退出后 /ping 仍返回 200、任务 st=done、主页进程存活 |
| A2 | passed | brief.md | A2：同上，关闭打码选取器（mask）页后主页依旧运行。 | 同上,mask 类 launch 子进程以非 0 退出时仅标记 fail 并记日志,主页 /ping 仍返回 200、进程存活 |
| A3 | passed | brief.md | A3：同时打开两个主页页面，关闭其中一个（或其中一个刷新），主页服务继续运行，剩下页面的请求正常；只有关闭最后一个主页页面、且 2 秒宽限期内没有新主页页面注册时，主页进程才退出。 | 注册 p1/p2 两个 pageId 后 /page-close?page=p1,静默等待超过宽限期主页仍存活,剩余页面 p2 的 /ping 返回 200 |
| A4 | passed | brief.md | A4：主页页面刷新（pagehide 后立即重新加载）不导致程序退出。 | _register_page 在注销后 2 秒宽限期内被再次调用会 cancel grace_t;注销 p2 后立即 /page-open?page=p2b,等待超过宽限期主页仍存活 |
| A5 | passed | brief.md | A5：主页「退出程序」按钮行为不变：终止全部存活子进程并退出程序。 | 在独立子进程中构造 _Home 并 spawn 长驻子进程后 POST /quit(body=b"{}",非空),服务进程退出且长驻子进程被终止(_quit_all 先 kill 全部存活子进程再 shutdown 并 os._exit) |
| A6 | passed | brief.md | A6：批量拼接的帧编号渲染不再依赖特定平台的 Linux 字体路径；在系统字体全部不可用的环境下，`_stitch` 仍为 j>0 的帧绘制编号，使无位置版接口可按编号切分。 | 把 _MARKER_FONTS 改为不存在的路径后 _stitch 未抛异常,_marker_font 回退到 Pillow 内置字体,第 1、2 帧分隔带白色像素分别为 90 与 130,第 0 帧为 0 |
| A7 | passed | brief.md | A7：无位置版接口切分防护：当一次批量请求返回的行不含任何区间编号且批内帧数 n>1 时，不把全部文本归入首帧，而是切换下一个接口；全部批量接口不可用时退回逐帧识别。 | 桩 _call 对 n=4 返回无区间编号的行时 _run_stitched 抛 RuntimeError 并依次尝试 accurate_basic -> general_basic(降级);第二接口返回带编号的行时切分结果为 ['','走れ','',''] |
| A8 | passed | brief.md | A8：修复后各帧文本能正确切分回帧，段构建时间轴恢复正常：各段起点严格递增、首段起点为 0，不再出现大量句子挤在数秒区间。 | 用 40 帧输入经 build_segments 与 _expand_long_segments 后得 80 段,首段 start=0.0,相邻 start 严格递增,末段 end=20.0 未被压缩 |
| A9 | passed | specs/app-lifecycle/spec.md | 关闭编辑页后主页继续运行 - WHEN 主页启动一个译文编辑器页面，随后该编辑页被关闭、对应子进程以 0 退出 - THEN 主页服务仍可访问（`/ping` 返回 200） - AND 主页进程不退出，其余任务不被终止 | 同 A1:译文编辑器页面对应的 launch 子进程以 0 退出后主页可访问且其它任务未被终止 |
| A10 | passed | specs/app-lifecycle/spec.md | 关闭打码页后主页继续运行 - WHEN 主页启动一个打码选取器页面，随后该打码页被关闭、对应子进程以 0 退出 - THEN 主页服务仍可访问，主页进程不退出 | 同 A2:打码选取器页面对应的 launch 子进程退出后主页服务仍可访问、主页进程不退出 |
| A11 | passed | specs/app-lifecycle/spec.md | 关闭两个主页页面之一不退出 - WHEN 同时打开两个主页页面，关闭其中一个 - THEN 主页服务继续运行，剩余页面的 `/ping` 与其它请求正常 - AND 主页进程不退出 | 同 A3:两个主页页面同时注册时关闭其中一个,主页继续运行,剩余页面 /ping 与其它请求正常 |
| A12 | passed | specs/app-lifecycle/spec.md | 关闭最后一个主页页面才退出 - WHEN 仅有一个主页页面打开，关闭该页面且在 2 秒宽限期内没有新页面注册 - THEN 主页服务关闭，程序退出 | 仅剩一个页面时注销且宽限期内无新注册,静默等待后 /ping 连接失败,服务已关闭(_grace_exit 触发 shutdown) |
| A13 | passed | specs/app-lifecycle/spec.md | 刷新主页不退出 - WHEN 主页页面刷新（`pagehide` 后立即重新加载并注册新页面） - THEN 主页服务继续运行，程序不退出 | 同 A4:pagehide 注销后立即重新加载并注册新 pageId,宽限期内注册取消退出,程序继续运行 |
| A14 | passed | specs/app-lifecycle/spec.md | 退出程序按钮终止全部子进程并退出 - WHEN 在主页点击「退出程序」并确认 - THEN 所有存活子进程被终止 - AND 主页服务关闭、程序退出 | 同 A5:/quit 非空 body 触发 _quit_all,终止全部存活子进程(含 launch 型)后关闭服务并退出程序 |
| A15 | passed | specs/subtitle-timeline/spec.md | 系统字体不可用时仍绘制编号 - WHEN 所有候选字体路径均不存在，调用 `_stitch` 拼接 n>1 帧 - THEN 图像中第 1..n-1 帧的分隔带区域出现编号文字像素 - AND 绘制过程不抛异常 | 同 A6:全部候选字体路径不存在时 _stitch 仍为 j>0 的帧绘制编号像素且不抛异常 |
| A16 | passed | specs/subtitle-timeline/spec.md | 无编号返回触发接口降级 - WHEN 无位置版接口对 n=4 的拼接图返回的行不含任何区间编号 - THEN 该接口的切分结果不被采用 - AND 流程尝试下一个批量接口，或全部失败时退回逐帧识别 | 同 A7:无编号返回不被采用,流程降级到下一批量接口;全部不可用时由 run_many 逐帧兜底 |
| A17 | passed | specs/subtitle-timeline/spec.md | 段起点单调递增 - WHEN 用修复后的各帧文本构建段并展开长文 - THEN 段列表按 `start` 升序且相邻段 `start` 严格递增 - AND 首段 `start` 为 0 | 同 A8:构建段的 start 严格递增、首段为 0,未出现句子挤在数秒区间的时间轴崩塌 |

## 检查

| 检查 | 命令 | 工作目录 | 状态 | 退出码 | 耗时 |
| --- | --- | --- | --- | ---: | ---: |
| 主页生命周期与字幕时间轴验收 | tests/verify_home_timeline.py | . | passed | 0 | 17627 ms |

### Builder 报告的证据

以下为 Builder 报告，不等同于 Runtime 检查凭据或独立验收结果。

- python-ast-parse: passed — ast.parse 解析 jpsub/home.py 与 jpsub/ocr.py,退出码 0
- jpsub-cli-help: passed — .venv/bin/python -m jpsub.cli --help 退出码 0,依赖(pysubs2)可加载
- tests/verify_home_timeline.py: passed — 开发期定向运行:.venv/bin/python tests/verify_home_timeline.py;4 项均 OK(marker-font-fallback / marker-split-fallback / monotonic-timeline / home-lifecycle),退出码 0
- quit-button-manual-reasoning: not-run — A5/A14 的 /quit 非空 body 会 os._exit 终止测试进程,未在本轮脚本内自动执行
- 已知限制: tests/verify_home_timeline.py 覆盖 A1-A4、A6-A8、A9-A13、A15-A17;A5/A14(退出程序按钮)会终止进程,需 Verifier 单独执行
- 已知限制: source 改动先落在主仓库工作副本再同步到候选工作区路径,两者内容已核对一致
- 已知限制: 页面存活判定依赖心跳(/ping 3s 一次)与 pagehide 注销;浏览器崩溃等无 pagehide 场景依靠 8s TTL 兜底

## 阻塞项

_无。_

## 风险与跳过的工作

- 运行环境未提供 verifier-started 输入模板,本次验收未登记启动回执,不影响核查结论。
- A5/A14 的 /quit 走 0.3 秒延迟退出定时器,极端高并发下退出时延可能略增;实测均在预期时间内退出。
- Windows 平台行为未在实体 Windows 上实测,依据为同一代码路径(无影响用户可见行为的平台分支)与跨平台字体候选回退。

## 之前的迭代

| 目标周期 | 迭代 | 尝试 | 结果 | 未解决项 | 摘要 | 完成时间 |
| ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | 1 | pass | — | 独立只读核查 A1-A17 全部通过:launch 型 edit/mask 子进程退出不再联动主页 shutdown,仅更新任务状态与日志;pageId 注册/心跳/注销 + 8 秒 TTL + 2 秒宽限实现多页面感知退出(关闭两页面之一或刷新均不退出,最后一个页面关闭且无新注册才退出);/quit 非空 body 仍终止全部存活子进程并退出,/quit 空 body 兼容信号在仍有存活页面时忽略;_stitch 帧编号字体跨平台候选 + Pillow 内置字体回退,字体全不可用时仍绘制编号;_run_stitched 无位置版接口在批内 n>1 且有识别行却无任何区间编号时判该接口失败并降级,全失败逐帧兜底,不再把整批文本并入首帧;修复后段起点严格递增、首段为 0、时间轴未被压缩;docs/home.md 行为说明已同步。Runtime 检查 verify-home-timeline 独立结果与本次核查一致。 | 2026-10-06T10:58:57.951Z |



## 结论

独立只读核查 A1-A17 全部通过:launch 型 edit/mask 子进程退出不再联动主页 shutdown,仅更新任务状态与日志;pageId 注册/心跳/注销 + 8 秒 TTL + 2 秒宽限实现多页面感知退出(关闭两页面之一或刷新均不退出,最后一个页面关闭且无新注册才退出);/quit 非空 body 仍终止全部存活子进程并退出,/quit 空 body 兼容信号在仍有存活页面时忽略;_stitch 帧编号字体跨平台候选 + Pillow 内置字体回退,字体全不可用时仍绘制编号;_run_stitched 无位置版接口在批内 n>1 且有识别行却无任何区间编号时判该接口失败并降级,全失败逐帧兜底,不再把整批文本并入首帧;修复后段起点严格递增、首段为 0、时间轴未被压缩;docs/home.md 行为说明已同步。Runtime 检查 verify-home-timeline 独立结果与本次核查一致。
