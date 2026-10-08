# 字幕文件导入与工程（subtitle-import）

## Overview

`jpsub` 现有的字幕来源只有 OCR 流水线（抽帧 → OCR → 合并 → `segments.json`）。本能力新增「导入现成字幕文件」入口：把用户已有的 SRT/ASS/TXT 字幕文件读入工作目录 `segments.json`，沿途完成编码识别、碎片合并与智能去重，之后交由既有 `render`/`edit`/`translate`/烧录链路处理。翻译与校对能力见 `ai-assist`。

参考实现为「烤肉汉化排版」的 `字幕合并工具.html`（浏览器 JS）；本次在 jpsub 侧以 Python 重写，不直接复制该 JS。

## Requirement: 字幕文件解析与编码识别

导入支持 SRT、ASS/SSA、TXT 三类格式，按内容特征自动判定格式（含 `[Events]` 与 `Dialogue:` 判为 ASS；含 `-->` 判为 SRT；否则 TXT）。SRT 解析标准时间块；ASS 解析 `[Events]` 段的 `Format:` 列名与 `Dialogue:` 行（`Text` 列含逗号时整列归并）；TXT 解析 `时间:文本`、`秒数 文本`、`分:秒 文本` 三种时间行，无时间行并入上一条为续行。

文件编码自动识别：优先按 BOM 判定 UTF-8/UTF-16；无 BOM 时在 UTF-8、GB18030、Shift-JIS、EUC-KR、Big5 候选中选取解码后替换字符最少者。

### Scenario: 无 BOM 的日文 Shift-JIS SRT 正确导入

- WHEN 导入一个无 BOM、Shift-JIS 编码的 `.srt` 文件
- THEN 解析出的原文全部为正确日文，无替换字符（`U+FFFD`）
- AND 条目数与该 SRT 的时间块数一致

## Requirement: 碎片合并与智能去重

导入时按时间间隔上限（默认 3 秒，可调）把相邻条目归组：仅当文本可合并时才并入。文本可合并的判据包含——后句以前句开头、前句以后句开头、后句以前句结尾、近似前缀（容错少量字符差异）、近似子序列（容错约 15% 字符级错字/空格）。`requireSameStyle` 开启时仅合并样式相同的条目（ASS 来源）。「名字行」（无换行的短文本，长度 ≤ 12）并入后立即封组，禁止继续向后串句。

### Scenario: 打字式逐帧 SRT 合并为整句

- WHEN 导入一个同一句台词被拆成多条的 `.srt`（后条以前条为前缀、间隔均 ≤ 3 秒）
- THEN 这些条目被合并为一条，`start` 取首条、`end` 取末条、`text` 为最完整文本

### Scenario: 间隔超过上限不合并

- WHEN 两条相邻字幕文本相同，但时间间隔超过上限
- THEN 两条保持独立，不被合并

### Scenario: 名字行封组

- WHEN 中间存在一条单行短名字（长度 ≤ 12），其后紧跟与名字无关的新台词
- THEN 该名字并入前组后封组，新台词不被并入前组

## Requirement: 排版转换（空格/换行 → \N）

提供排版转换：把文本中的空格、制表符、换行替换为 ASS 换行符 `\N`，`{…}` 覆盖标签内部不动，去掉结果首尾多余 `\N`。时间轴不变。

### Scenario: 空格与换行转 \N

- WHEN 对一条文本为 `第一行 第二行\n第三行` 的条目执行排版转换
- THEN 结果为 `第一行\N第二行\N第三行`
- AND `{\pos(1,2)}` 之类的标签内容不被替换

## Requirement: 导出 SRT/ASS/TXT

导入并处理后的结果可导出为 SRT（带 BOM、标准时间块）、TXT（保留原时间行格式）、ASS（复用输入 ASS 结构；无输入结构时按生成格式输出）。已合并的组若被标记拆开，导出时按原条目逐条输出。

### Scenario: 合并后导出 SRT

- WHEN 把一份被合并去重的字幕导出为 SRT
- THEN 输出为带 BOM 的标准 SRT，时间格式 `HH:MM:SS,mmm`，条目数与合并后组数一致

## Requirement: 与既有流水线对接

导入产生的 `segments.json` 与 OCR 产物同构（`segments` 数组，每段 `start`/`end`/`text`，可选 `tr`），写入工作目录 `.jpsub/segments.json`。导入后 `jpsub render`、`jpsub edit`、`jpsub translate` 可正常处理，无需改动这些命令。导入不覆盖既有 OCR 结果前的用户确认；导入命令对已有 `segments.json` 的行为（覆盖/备份）见实现约束。

### Scenario: 导入后可直接渲染

- WHEN 把一份 SRT 导入为工作目录 `segments.json`，随后执行 `jpsub render <工作目录>`
- THEN 成功产出 `.ass`，字幕时间与文本与导入内容一致

## 不变量

- 既有 OCR 流水线（`extract`/`run`）行为与输出不变。
- `segments.json` 文件格式不变。
- 不引入新的第三方运行时依赖。
