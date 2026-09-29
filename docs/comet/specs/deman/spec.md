# 自动去人（deman）：肤色 + 像素分布检测

## Overview

`jpsub` 新增自动去人能力 `deman`：对输入视频按固定粒度抽样，用肤色判据 + 像素分布判据定位肤色区域，输出与既有 `masks.json` 同构的矩形覆盖条目，交给既有 `jpsub maskapply` 应用。自动结果只写独立文件，不修改手工掩膜。

## 入口

`jpsub mask --auto [video]` 非交互执行自动检测；不带 `--auto` 时 `jpsub mask` 仍是原手工选取器，行为与输出不变。

## Requirement: 检测判据

判据只使用肤色 + 像素分布类特征：RGB 肤色规则与 YCbCr 肤色规则联合得到肤色掩膜，再叠加连通区域面积/形状与局部纹理或边缘密度判据。不引入模型权重、外部服务或新增第三方依赖。检测目标是画面中所有满足判据的肤色区域，不预设 ROI，也不区分实拍或渲染来源。

### Scenario: 命中实拍真人肤色区域

- WHEN 对验证视频执行 `jpsub mask --auto`
- THEN 在手工基线覆盖的 16s、56s、1172s 时刻，输出各至少有一条时间区间覆盖该时刻的条目
- AND 该时刻至少一条输出条目与同刻手工基线框相交面积大于 0

### Scenario: 无肤色内容时不覆盖

- WHEN 对验证视频执行 `jpsub mask --auto`
- THEN 在 1200s、1220s（画面无肤色内容）不存在覆盖该时刻的条目，或覆盖面积小于整帧 5%

### Scenario: 肤色样背景不误覆盖

- WHEN 对验证视频执行 `jpsub mask --auto`
- THEN 在 40s、600s（米色/泥土等肤色样场景背景）输出覆盖面积小于整帧 15%
- AND 画面上半部分（y < 180）无覆盖条目

## Requirement: 输出格式与落盘

输出写入被处理视频同目录的 `.jpsub/masks.auto.json`，与 `masks.json` 同构（`start`/`end`/`x`/`y`/`w`/`h`/`effect`/`vol`），可被 `jpsub.mask.parse_masks` 解析。不修改、不删除、不合并手工 `masks.json`。

### Scenario: 产出可解析的独立掩膜文件

- WHEN 对验证视频执行 `jpsub mask --auto`
- THEN 生成 `<video_dir>/.jpsub/masks.auto.json`
- AND `jpsub.mask.parse_masks` 可成功解析该文件，条目数大于 0
- AND 每条含合法 `start < end`、`w > 0`、`h > 0` 与 `effect` 字段
- AND 手工 `masks.json` 内容未被修改

## Requirement: 采样粒度

按每秒 1 帧抽样检测；连续命中的采样时刻合并为秒级连续区间条目。

### Scenario: 每秒抽样并合并区间

- WHEN 自动检测在连续多个采样秒都命中肤色区域
- THEN 输出把这些采样秒合并为覆盖该连续时间段的一条区间条目，而非逐秒的碎片条目

## Requirement: 覆盖应用

自动条目可由既有 `jpsub maskapply` 直接应用，默认效果为 `color:000000`。

### Scenario: 应用后目标区域被覆盖

- WHEN 用自动掩膜执行 `jpsub maskapply <验证视频>`
- THEN 生成 `.masked.mp4`
- AND 采样时刻 16s、56s、1172s 下，同刻手工基线框区域内至少 80% 像素为纯黑（RGB 各通道 ≤ 8）
- AND 输出视频时长 1261.27s（±0.5s）与 30fps 同原片一致

## 不变量

- 既有无参 `jpsub mask` 选取器与 `jpsub maskapply` 的行为与输出保持不变。
- `masks.json` 字段格式与 `MaskEntry`/`parse_masks`/`save_masks`/`build_filter`/`apply_masks` 实现不变。
- 不新增第三方依赖，不访问外部服务。
