---
generated_from_state_version: 11
---

# 验证

## 当前结果

- 结果: **验收通过，可归档**
- 验证情况: **已完成检查，验证结果已确认**
- 目标周期: 2
- 迭代: 1
- 验证器尝试次数: 1
- 完成时间: 2026-10-07T18:06:38.004Z
- 摘要: 以 .venv/bin/python 独立执行 tests/verify_deman.py（退出码 0），20 项断言全部通过，覆盖 A1-A18 全部 18 条验收：对照集相交召回 47%（阈值 35%）、>=50% 面积命中 37%（阈值 15%）、单条最大面积 58%（阈值 <60%）；参考片 16s/56s/1172s 均命中且与人工基线框相交、1200s/600s 无覆盖；maskapply 后基线框内纯黑 100%/94.3%/100%、时长与帧率与原片一致；手工 masks.json 未被修改。verdict=passed。

## 验收

| 编号 | 结果 | 来源 | 验收项 | 原因 |
| --- | --- | --- | --- | --- |
| A1 | passed | brief.md | WHEN 对对照集执行 `jpsub mask --auto` | 以 /home/mikan/Documents/dev/bbTrans/.venv/bin/python tests/verify_deman.py 独立执行，9 部对照集片子均生成 .jpsub/masks.auto.json 且可解析 |
| A2 | passed | brief.md | THEN 相对人工基线的召回（相交命中）≥ 35%（当前实测 16%） | 252 个基线秒中相交命中 119 个 = 47% >= 35% |
| A3 | passed | brief.md | AND 覆盖基线框内 ≥50% 面积的命中 ≥ 15%（当前实测 5%） | 覆盖基线框内 >=50% 面积的命中 92 个 = 37% >= 15% |
| A4 | passed | brief.md | AND 任一条自动条目的外接矩形面积 < 整帧 60% | 对照集单条最大外接矩形面积 58% < 整帧 60% |
| A5 | passed | brief.md | AND 每部片子的输出仍可被 `parse_masks` 解析、`start < end`、`w > 0`、`h > 0` 参考片（`TAKUYA -タイト- 4話(終)`）关键时刻： | masks.auto.json 可由 jpsub.mask.parse_masks 解析、条目数 > 0、每条 start<end 且 w>0 h>0 且含 effect；对照集与参考片手工 masks.json 哈希未变 |
| A6 | passed | brief.md | WHEN 对参考片执行 `jpsub mask --auto` | 参考片 jpsub mask --auto 正常退出（退出码 0，处理 1261 帧） |
| A7 | passed | brief.md | THEN 16s、56s、1172s 各至少一条覆盖该时刻的条目，且与同刻手工框（或 `.bak` 框）相交面积 > 0 | 16s/56s/1172s 分别有 5/5/3 条覆盖该时刻的条目，且与同刻 .bak 基线框相交面积 > 0 |
| A8 | passed | brief.md | AND 1200s、600s 无肤色内容时刻不产生覆盖（或覆盖 < 5%） | 1200s、600s 覆盖占比均为 0.0% < 5% |
| A9 | passed | brief.md | AND 单条最大面积 < 整帧 60% | 参考片单条最大面积 58% < 60% |
| A10 | passed | brief.md | AND `maskapply` 后 16s、56s、1172s 基线框内 ≥ 80% 像素纯黑（RGB 各通道 ≤ 8） | maskapply 后基线框内纯黑比例 16s=100.0%、56s=94.3%、1172s=100.0%，均 >= 80% |
| A11 | passed | brief.md | AND 输出视频时长 1261.27s（±0.5s）与 30fps 同原片一致 | 输出时长 1261.27s -> 1261.27s（偏差 0s，<±0.5s）、帧率 30/1 -> 30/1 与原片一致 |
| A12 | passed | specs/deman/spec.md | 命中实拍真人肤色区域 - WHEN 对参考片（`TAKUYA -タイト- 4話(終)`）执行 `jpsub mask --auto` - THEN 在人工基线覆盖的 16s、56s、1172s 时刻，输出各至少有一条时间区间覆盖该时刻的条目 - AND 该时刻至少一条输出条目与同刻人工基线框相交面积大于 0 | 参考片 16s/56s/1172s 均有时间区间覆盖，且至少一条与同刻人工基线框相交面积 > 0 |
| A13 | passed | specs/deman/spec.md | 提升对照集召回 - WHEN 对 `output/` 下全部含人工标注的片子（8 部，共 251 个基线秒）执行 `jpsub mask --auto` - THEN 与人工基线相交命中的基线秒比例 ≥ 35%（现行实现实测 16%） - AND 覆盖基线框内 ≥ 50% 面积的命中比例 ≥ 15%（现行实现实测 5%） | 对照集 9 部（含手工标注）相交召回 47% >= 35%，>=50% 面积命中 37% >= 15% |
| A14 | passed | specs/deman/spec.md | 无肤色内容时不覆盖 - WHEN 对参考片执行 `jpsub mask --auto` - THEN 在 1200s、600s（画面无肤色内容）不存在覆盖该时刻的条目，或覆盖面积小于整帧 5% | 参考片 1200s、600s 无覆盖条目（覆盖 0% < 整帧 5%） |
| A15 | passed | specs/deman/spec.md | 单条条目不整屏 - WHEN 对对照集任意片子执行 `jpsub mask --auto` - THEN 每条输出条目的外接矩形面积小于整帧 60% | 对照集与参考片逐条检查，最大条目面积为整帧 58% < 60% |
| A16 | passed | specs/deman/spec.md | 产出可解析的独立掩膜文件 - WHEN 对验证视频执行 `jpsub mask --auto` - THEN 生成 `<video_dir>/.jpsub/masks.auto.json` - AND `jpsub.mask.parse_masks` 可成功解析该文件，条目数大于 0 - AND 每条含合法 `start < end`、`w > 0`、`h > 0` 与 `effect` 字段 - AND 手工 `masks.json` 内容未被修改 | 生成 <video_dir>/.jpsub/masks.auto.json，parse_masks 解析成功且条目数 > 0，字段合法；手工 masks.json 内容哈希未变 |
| A17 | passed | specs/deman/spec.md | 每秒抽样并合并区间 - WHEN 自动检测在连续多个采样秒都命中肤色区域 - THEN 输出把这些采样秒合并为覆盖该连续时间段的一条区间条目，而非逐秒的碎片条目 | 参考片 592 条输出中单秒碎片 333 条，其余为跨秒合并区间；16/56/1172s 的连续命中被合并为覆盖区间的条目 |
| A18 | passed | specs/deman/spec.md | 应用后目标区域被覆盖 - WHEN 用自动掩膜执行 `jpsub maskapply <参考片>` - THEN 生成 `.masked.mp4` - AND 采样时刻 16s、56s、1172s 下，同刻手工基线框区域内至少 80% 像素为纯黑（RGB 各通道 ≤ 8） - AND 输出视频时长 1261.27s（±0.5s）与 30fps 同原片一致 | jpsub maskapply 正常退出并产出 .masked.mp4；16s/56s/1172s 基线框内纯黑 100%/94.3%/100% >= 80%；时长 1261.27s、30fps 与原片一致 |

## 检查

| 检查 | 命令 | 工作目录 | 状态 | 退出码 | 耗时 |
| --- | --- | --- | --- | ---: | ---: |
| 自动去人 A1-A18 端到端验收 | tests/verify_deman.py | . | passed | 0 | 409096 ms |

### Builder 报告的证据

以下为 Builder 报告，不等同于 Runtime 检查凭据或独立验收结果。

- 端到端验收脚本 tests/verify_deman.py(A1-A18): passed — 20 项检查全部通过(部分验收 ID 对应多项断言)
- 对照集召回(A2/A13): passed — 252 个基线秒相交召回 119 = 47% >= 35%;≥50% 面积命中 92 = 37% >= 15%
- 不整屏上限(A4/A9/A15): passed — 对照集与参考片单条最大面积均为整帧 58% < 60%
- 参考片关键时刻(A7/A12): passed — 16s 覆盖 5 条、56s 覆盖 5 条、1172s 覆盖 3 条,均与 .bak 基线框相交
- 无肤色内容不覆盖(A8/A14): passed — 1200s、600s 覆盖均为 0%
- 覆盖应用(A10/A18): passed — maskapply 后基线框内纯黑 16s 100%、56s 94.3%、1172s 100%
- 时长与帧率(A11/A18): passed — 1261.27s -> 1261.27s;30/1 -> 30/1
- 输出格式与手工清单保护(A5/A16): passed — masks.auto.json 可被 parse_masks 解析,对照集与参考片 masks.json 哈希未变
- 区间合并(A17): passed — 参考片 592 条中单秒条目 333 条,连续命中确有合并
- 已知限制: 未安装 ruff,未执行 lint 检查
- 已知限制: 「拓也はドラえもん.ai」34 个基线秒召回 0:该片肤色区域判据未命中,属非肤色/深色轮廓场景,不在本 change 范围(非目标已声明不解决非肤色真人区域)
- 已知限制: 对照集评估依赖 output/ 下的人工 masks.json;参考片真值取自 masks.json.bak(原 masks.json 已被上一轮自动结果覆盖)

## 阻塞项

_无。_

## 风险与跳过的工作

- 验收脚本 tests/verify_deman.py 由 Builder 编写，本轮为复用同一脚本的独立执行而非第三方独立重写，存在同一实现假设被沿用的风险
- 参考片真值取自 masks.json.bak（原 masks.json 已被上一轮自动结果覆盖），基线来源非原始人工产物
- 「拓也はドラえもん.ai」34 个基线秒召回 0（非肤色/深色轮廓场景），已声明为非目标，未解决
- 未安装 ruff，未执行 lint 检查

## 之前的迭代

| 目标周期 | 迭代 | 尝试 | 结果 | 未解决项 | 摘要 | 完成时间 |
| ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 0 | 0 | recovery | — | Native formal documents are invalid (brief-blocking-question: brief.md) | 2026-10-06T14:45:17.710Z |
| 2 | 1 | 1 | pass | — | 以 .venv/bin/python 独立执行 tests/verify_deman.py（退出码 0），20 项断言全部通过，覆盖 A1-A18 全部 18 条验收：对照集相交召回 47%（阈值 35%）、>=50% 面积命中 37%（阈值 15%）、单条最大面积 58%（阈值 <60%）；参考片 16s/56s/1172s 均命中且与人工基线框相交、1200s/600s 无覆盖；maskapply 后基线框内纯黑 100%/94.3%/100%、时长与帧率与原片一致；手工 masks.json 未被修改。verdict=passed。 | 2026-10-07T18:06:38.004Z |



## 结论

以 .venv/bin/python 独立执行 tests/verify_deman.py（退出码 0），20 项断言全部通过，覆盖 A1-A18 全部 18 条验收：对照集相交召回 47%（阈值 35%）、>=50% 面积命中 37%（阈值 15%）、单条最大面积 58%（阈值 <60%）；参考片 16s/56s/1172s 均命中且与人工基线框相交、1200s/600s 无覆盖；maskapply 后基线框内纯黑 100%/94.3%/100%、时长与帧率与原片一致；手工 masks.json 未被修改。verdict=passed。
