# 敏感文字自动打码（sensitive-mask）

## Overview

`jpsub mask --sensitive` 依据独立敏感词清单，自动把画面内烧录/绘制的日文文本中的敏感词用遮挡块盖掉，产出与手工打码同构的条目到独立掩膜文件，由既有 `jpsub maskapply` 应用。定位优先使用百度 OCR 含位置版接口；含位置版额度或可用性用尽后降级为算法定位。本能力不改变既有手工打码与自动去人（`--auto`）的行为。

## 内置默认词表

程序内置以下默认敏感词（精确子串匹配，词表可含 OCR 常见错字变体以免漏判）：

- 正式条目：`性的虐待`、`虐待`、`調教`、`肛門`、`肛問`、`ケツの穴`、`ケツマンコ`、`ホモ`、`雌堕ち`、`雄堕ち`、`奴隷`、`エロ`、`雄子宮`、`雄膣`、`淫獣`、`デカマラ`、`性処理`、`性欲`、`露出`、`股間`、`媚薬`、`全裸`、`排泄`、`自殺`
- 仅作注释示例、默认不启用（单字通用词，误盖风险高）：`死`、`堕`、`雌`

词表包含参考片画面/OCR 原文中出现的变体写法（`雄堕ち`/`雄膣` 等与 `雌堕ち`/`雄子宮` 并列；`淫獣`、`ケツマンコ`、`デカマラ`、`性処理`、`性欲`、`露出`、`股間`、`媚薬` 为人工基线实际遮挡的画面词）。

## Requirement: word-list 敏感词清单与匹配

敏感判定只依据敏感词清单，不对全部汉字打码，也不复用 glossary。词表按以下优先级取一个文件作为**替换**内容：`--sensitive-words <path>` > 工作目录 `sensitive.txt` > 程序目录 `sensitive.txt` > 内置默认词表。词表文件为 UTF-8 文本，每行一个词，`#` 起始为注释，空行忽略。匹配规则为对画面 OCR 原文的**精确子串匹配**，命中即为一个需要打码的词实例；词表内容不含某文字时该文字永不被框选。

### Scenario: 词表生效与边界

- WHEN 工作目录存在 `sensitive.txt` 且未指定 `--sensitive-words`
- THEN 该文件内容整体替换内置默认词表
- AND 未列入词表的普通文字（如画面标题、普通对白用词）不产生任何遮挡条目

## Requirement: text-source 复用已有 OCR 原文

打码流程总在 OCR 之后执行，并复用工作目录已有的 OCR 文本，不额外做全量逐帧 OCR：优先读取 `<工作目录>/.jpsub/segments.json` 中各段的 `text`（画面 OCR 原文）作为匹配输入；该文件缺失时，才对工作目录中的帧执行既有无位置版 OCR 取得文本。匹配对象只有画面原文，不匹配本程序烧录的中文字幕译文（`tr`）。

### Scenario: 复用已有文本不重复消耗额度

- WHEN 工作目录已存在 `segments.json`
- THEN 命令不调用无位置版 OCR 接口读取文本
- AND 敏感判定结果只由 `text` 字段（原文）产生，`tr` 字段不参与匹配

## Requirement: position-ocr 含位置版定位

敏感词的像素框优先由百度 OCR 含位置版接口（`accurate`、`general`）给出：对存在命中的时间区间取代表帧（区间中点），该帧只发起一次含位置版请求，取得按行的文字与行框；在命中词所在行的行框内，按该行文本的字符序号与词长占行宽的比例切出**命中词整体一个框**（粒度见 D6），并把框裁剪到画面范围内。含位置版请求只在有命中的区间发起，同一时刻的帧只识别一次。

### Scenario: 含位置版给出整词框

- WHEN 某字幕段原文含词表中的词且含位置版接口有可用额度
- THEN 该段产出的遮挡框覆盖该词在该帧中的文字区域
- AND 每个被定位的帧只发起一次含位置版请求

## Requirement: algo-fallback 算法降级定位

`--sensitive-locate` 取 `auto`（默认）、`ocr`、`algo`：`auto` 在含位置版接口额度/可用性用尽（如额度耗尽或接口报错）时自动降级；`algo` 强制使用算法定位；`ocr` 强制使用含位置版。算法定位不调用含位置版接口，在帧内复用主流程的文字掩膜（局部对比 + 亮度门槛）做水平投影分组、检测文字行并按 y 排序，再按「行宽 ÷ 行高」估算的行容量把该段 OCR 原文的各行对应到容量刚好匹配的行框（容量差异过大则跳过该行），在行框内按字符序号比例切出命中词框。对应不上或检测不到文字行时跳过（宁可漏框，不误盖）。位置版不可用不得导致命令失败。

### Scenario: 位置版用尽仍产出掩膜

- WHEN 以 `--sensitive-locate algo` 运行（或含位置版额度已用尽时以 `auto` 运行）
- THEN 命令正常结束并产出合格掩膜文件
- AND 该次运行不调用含位置版接口

## Requirement: time-align 时间区间与字幕段对齐

遮挡条目的时间区间以 `segments.json` 的字幕段为单位：命中段的区间 = 该段 `start - pad` 到 `end + pad`，`pad` 由 `--sensitive-pad` 指定，默认 0.5 秒；区间下界不小于 0，上界不超过视频时长。相邻段中位置高度重合且命中同一敏感词的框合并为一条，避免产生大量碎条目。

### Scenario: 时间区间随字幕段外扩

- WHEN 命中段的 `start`/`end` 为给定值且 `--sensitive-pad` 使用默认值
- THEN 产出条目的时间区间为该段 `start-0.5` 到 `end+0.5`
- AND 时间区间连续且位置与词相同的相邻段只产生一条条目

## Requirement: output-file 输出独立掩膜文件

结果写入 `<工作目录>/.jpsub/masks.sensitive.json`，结构为与手工 `masks.json` 相同的条目数组（`start`/`end`/`x`/`y`/`w`/`h`/`effect`/`vol`），条目按 `start` 升序排列，`effect` 为 `color:000000`、`vol` 为 1.0，可被 `jpsub.mask.parse_masks` 解析并由 `jpsub maskapply` 应用，也可人工增删改。命令不修改、不覆盖、不删除手工 `masks.json` 及其它既有工作目录文件。

### Scenario: 输出可解析且不覆盖既有文件

- WHEN 工作目录含手工 `masks.json` 时运行 `jpsub mask --sensitive`
- THEN 生成 `.jpsub/masks.sensitive.json` 且可被 `parse_masks` 解析为合法条目
- AND 手工 `masks.json` 的内容与哈希保持不变

## Requirement: cli-switch CLI 开关与共存

入口为既有 `jpsub mask` 子命令上的非交互开关：`--sensitive` 触发自动敏感打码，`--sensitive-words <path>`、`--sensitive-pad <sec>`、`--sensitive-locate auto|ocr|algo` 为可选参数。未给出 `--sensitive` 时命令行为与既有版本一致（仍进入交互选取器，不产生任何新文件）。与另一 change 的自动去人开关 `--auto` 在设计上各自独立：本 change 只新增 `--sensitive*` 参数，且仅在 `--sensitive` 为真时生效，不改动 `--auto` 相关代码路径；两类结果写入不同文件，互不覆盖。（`--auto` 属另一 change，本 change 的分支内不含该参数。）

### Scenario: 开关独立且默认行为不变

- WHEN 不带 `--sensitive` 运行 `jpsub mask`
- THEN 不产生 `.jpsub/masks.sensitive.json`，既有交互打码与结果文件行为不变
- AND 本 change 对 `jpsub mask` 的改动只在 `--sensitive` 为真时生效（`--auto` 与其它既有分支不受影响）

## Requirement: docs-sync 文档同步

`docs/mask.md` 记录新开关、词表维护方式（`sensitive.txt` / `--sensitive-words`）与降级行为；`changelog.md` 记录本次新增能力。

### Scenario: 文档与实现一致

- WHEN 查阅 `docs/mask.md` 与 `changelog.md`
- THEN 能查到 `--sensitive`、`--sensitive-words`、`--sensitive-pad`、`--sensitive-locate` 的说明与词表维护方式
- AND 文档描述的默认值与实现一致

## 不变量

- 手工 `masks.json`、`MaskEntry`/`parse_masks`/`save_masks` 契约与 `jpsub maskapply` 行为不变。
- 百度 OCR 侧只新增「含位置版定位」调用路径：不改动 `_call` 既有传参、语言设置、额度状态文件机制与 QPS 重试逻辑，不新增接口。
- 未指定新开关时，`jpsub mask` 与 `jpsub run` 的既有行为完全不变。
- 遮挡只覆盖命中敏感词的小框，不整块覆盖画面；无法可靠定位时漏框而不是误盖。
