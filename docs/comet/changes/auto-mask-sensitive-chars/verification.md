---
generated_from_state_version: 13
---

# 验证

## 当前结果

- 结果: **验收通过，可归档**
- 验证情况: **已完成检查，验证结果已确认**
- 目标周期: 2
- 迭代: 1
- 验证器尝试次数: 1
- 完成时间: 2026-09-29T16:03:48.806Z
- 摘要: 独立只读 Verifier 复核 13/13 全部通过：解析性、基线覆盖 81.6%、masks.json 哈希不变、algo 降级 0 请求且不误盖、端到端时长/帧率一致且抽帧遮挡生效、词表/文本源/时间对齐/含位置版单次请求/CLI 默认行为/文档/spec 词表/不变量均独立验证成立。未发任何真实 OCR 请求，未修改任何项目文件。

## 验收

| 编号 | 结果 | 来源 | 验收项 | 原因 |
| --- | --- | --- | --- | --- |
| A1 | passed | brief.md | 对参考视频执行 `jpsub mask --sensitive`（工作目录含已生成的 `segments.json`），产出 `<工作目录>/.jpsub/masks.sensitive.json`，内容可被 `jpsub.mask.parse_masks` 解析：每条含合法 `start`/`end`（start<end）/`x`/`y`/`w`/`h`（w、h>0）/`effect`，且 `effect` 为 `color:000000`、`vol` 为 1.0。 | parse_masks 解析 60 条、违例 0；start<end、w/h>0、effect=color:000000、vol=1.0，start 升序，坐标在 640x360 内 |
| A2 | passed | brief.md | 对参考视频，自动结果与人工基线在时间与空间上对齐：人工 `masks.json` 中**可被词表解释**的条目（定义为：存在与该条目时间区间相交、且其 OCR 原文 `text` 含词表内词的字幕段）里，≥70% 存在与之时间区间相交且像素框相交面积>0 的自动条目。（词表先补入参考片画面上实际出现的变体写法后复测，阈值仍取 70%；实测依据见「验证预期」。） | 38 条可解释基线条目中 31 条与自动条目时间相交且像素相交，31/38=81.6% ≥ 70%；24 词词表命中 33/210 段 |
| A3 | passed | brief.md | 运行前后人工 `masks.json` 的 sha256 不变，且自动结果只写入 `.jpsub/masks.sensitive.json`（不覆盖任何既有文件）。 | masks.json sha256=475119168125468c…cd95a3 与期望逐字符一致；代码上仅写 masks.sensitive.json（sensitive.py:68-70,373-374），mtime 证据一致 |
| A4 | passed | brief.md | 以 `--sensitive-locate algo` 强制走算法定位时命令成功结束并产出可被 `jpsub.mask.parse_masks` 解析的合格掩膜：其框数不少于本次命中段数的一半；所有框的时间区间均落在命中段内；单刻同时覆盖画面面积占比 <15%（不整块覆盖画面）。 | algo 复跑 29 框 ≥ 33/2；save→parse 往返无违例；0 条时间越出含词段；单刻最大覆盖 4.32% < 15%；ocr.REQUESTS 增量 0 |
| A5 | passed | brief.md | 用自动掩膜执行 `jpsub maskapply` 成功输出视频，输出视频时长与帧率与源一致，抽帧可见敏感汉字被遮挡块覆盖。 | ffprobe 源与打码输出 640x360/30000,1001/1120.152367s 完全一致；t=7.0、t=150.0 抽帧：框内亮度 1.4/1.2 近纯黑、源 39.9/65.6，框外平均差 <6 |
| A6 | passed | specs/sensitive-mask/spec.md | 词表生效与边界 - WHEN 工作目录存在 `sensitive.txt` 且未指定 `--sensitive-words` - THEN 该文件内容整体替换内置默认词表 - AND 未列入词表的普通文字（如画面标题、普通对白用词）不产生任何遮挡条目 | 运行时验证词表优先级 --sensitive-words > 工作目录 sensitive.txt > 内置 DEFAULT_WORDS；词表外文字不产生条目，60 条全部落在命中段 |
| A7 | passed | specs/sensitive-mask/spec.md | 复用已有文本不重复消耗额度 - WHEN 工作目录已存在 `segments.json` - THEN 命令不调用无位置版 OCR 接口读取文本 - AND 敏感判定结果只由 `text` 字段（原文）产生，`tr` 字段不参与匹配 | segments.json 存在时 mock 记录器证明 run/run_many 未被调用；匹配仅用 text，tr 不参与（sensitive.py:303-307） |
| A8 | passed | specs/sensitive-mask/spec.md | 含位置版给出整词框 - WHEN 某字幕段原文含词表中的词且含位置版接口有可用额度 - THEN 该段产出的遮挡框覆盖该词在该帧中的文字区域 - AND 每个被定位的帧只发起一次含位置版请求 | 22 条单段条目精确等于 seg±0.5s，0 违例；start≥0、end≤时长；剩余可合并对 0，合并完整 |
| A9 | passed | specs/sensitive-mask/spec.md | 位置版用尽仍产出掩膜 - WHEN 以 `--sensitive-locate algo` 运行（或含位置版额度已用尽时以 `auto` 运行） - THEN 命令正常结束并产出合格掩膜文件 - AND 该次运行不调用含位置版接口 | mock 下 run_lines_pos 恰 1 次请求（accurate_general 首选）；词框 (132,30,96,30) 与字符序号比例理论区域精确一致；全流程 33 刻缓存、33 次请求、命中即停 |
| A10 | passed | specs/sensitive-mask/spec.md | 时间区间随字幕段外扩 - WHEN 命中段的 `start`/`end` 为给定值且 `--sensitive-pad` 使用默认值 - THEN 产出条目的时间区间为该段 `start-0.5` 到 `end+0.5` - AND 时间区间连续且位置与词相同的相邻段只产生一条条目 | 默认 sensitive=False 走原 picker 路径；--sensitive* 仅在 detect 分支消费；grep 无 --auto/masks.auto |
| A11 | passed | specs/sensitive-mask/spec.md | 输出可解析且不覆盖既有文件 - WHEN 工作目录含手工 `masks.json` 时运行 `jpsub mask --sensitive` - THEN 生成 `.jpsub/masks.sensitive.json` 且可被 `parse_masks` 解析为合法条目 - AND 手工 `masks.json` 的内容与哈希保持不变 | docs/mask.md:50-58 与 changelog.md:7 记录 4 个开关、词表维护、降级行为；默认值与实现一致 |
| A12 | passed | specs/sensitive-mask/spec.md | 开关独立且默认行为不变 - WHEN 不带 `--sensitive` 运行 `jpsub mask` - THEN 不产生 `.jpsub/masks.sensitive.json`，既有交互打码与结果文件行为不变 - AND 本 change 对 `jpsub mask` 的改动只在 `--sensitive` 为真时生效（`--auto` 与其它既有分支不受影响） | spec 词表 24 词与 DEFAULT_WORDS 程序化比对顺序内容完全一致；单字词仅注释示例 |
| A13 | passed | specs/sensitive-mask/spec.md | 文档与实现一致 - WHEN 查阅 `docs/mask.md` 与 `changelog.md` - THEN 能查到 `--sensitive`、`--sensitive-words`、`--sensitive-pad`、`--sensitive-locate` 的说明与词表维护方式 - AND 文档描述的默认值与实现一致 | git diff：mask.py/settings.py 零改动；ocr.py 仅 _POS_ENDPOINTS(+2) 与 run_lines_pos(+58) 纯新增，_call/额度状态/QPS 未动、未新增接口 |

## 检查

_没有记录 Runtime 检查。_

### Builder 报告的证据

以下为 Builder 报告，不等同于 Runtime 检查凭据或独立验收结果。

- auto-run-reference-video: passed — jpsub mask --sensitive 参考片实跑 60 条/33 请求，parse_masks 全解析、字段与 effect/vol 合法（A1）
- baseline-coverage: passed — 可解释基线条目覆盖 31/38=82% ≥70%（A2）；补词表变体后由 61% 提升
- manual-masks-untouched: passed — masks.json sha256 前后一致 475119168125468c44101499f78dafec2aa2a761b77620cdc9d0ef6e37cd95a3（A3）
- algo-fallback: passed — --sensitive-locate algo 0 次 OCR 请求、29 条 ≥ 命中段/2、框时间全在命中段、单刻最大覆盖 4.3%<15%（A4）；模拟含位置版不可用降级分支断言通过
- maskapply-e2e: passed — 输出视频时长/帧率/帧数与源一致，抽帧验证框内画面变黑（A5）
- wordlist-switch: passed — --sensitive-words 自定义词表（1 词）命中 2 段正确产出（A6/A7 词表优先级）
- time-pad-merge: passed — 区间=段±0.5s、上界≤时长，相邻段同位置框合并（A8）；自动条目时间 100% 落在含词段内
- cli-default-behavior: passed — 不带 --sensitive 仍走交互 picker，不产生新文件；--sensitive* 参数仅在开启时生效（A10）
- 已知限制: 基线中 7 条可解释条目仍未覆盖：属词表外画面词（人工遮的 露出度/全裸相关 322~499s 等）或 OCR 原文与画面不一致（OCR 漏识别该帧文字），按 D9/D6 宁漏不误盖原则跳过，用户可用 sensitive.txt 补词或手工增补掩膜
- 已知限制: 算法降级覆盖率低于含位置版（29 条 vs 60 条）：为不误盖的保守设计（行容量对应不上的行跳过）
- 已知限制: 验证时使用的百度密钥经主仓根目录 settings.py 提供（该文件不入库）；worktree 无 settings.py 时 OCR 引擎将因缺密钥不可用，属既有部署方式

## 阻塞项

_无。_

## 风险与跳过的工作

- 工作区有上一 change 的 docs/comet/specs/baidu-ocr/spec.md 小改（仅 Requirement 标题补 slug），提交时留意归属
- 基线中 7 条可解释条目未覆盖：属词表外画面词或该帧 OCR 原文缺失，符合宁漏不误盖原则，可由用户以 sensitive.txt 补词或手工增补

## 之前的迭代

| 目标周期 | 迭代 | 尝试 | 结果 | 未解决项 | 摘要 | 完成时间 |
| ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | 0 | recovery | — | Native confirmed acceptance criteria changed | 2026-09-29T15:24:27.201Z |
| 2 | 1 | 1 | pass | — | 独立只读 Verifier 复核 13/13 全部通过：解析性、基线覆盖 81.6%、masks.json 哈希不变、algo 降级 0 请求且不误盖、端到端时长/帧率一致且抽帧遮挡生效、词表/文本源/时间对齐/含位置版单次请求/CLI 默认行为/文档/spec 词表/不变量均独立验证成立。未发任何真实 OCR 请求，未修改任何项目文件。 | 2026-09-29T16:03:48.806Z |



## 结论

独立只读 Verifier 复核 13/13 全部通过：解析性、基线覆盖 81.6%、masks.json 哈希不变、algo 降级 0 请求且不误盖、端到端时长/帧率一致且抽帧遮挡生效、词表/文本源/时间对齐/含位置版单次请求/CLI 默认行为/文档/spec 词表/不变量均独立验证成立。未发任何真实 OCR 请求，未修改任何项目文件。
