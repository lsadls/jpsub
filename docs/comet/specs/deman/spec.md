# 自动去人（deman）：肤色 + 像素分布检测

## Overview

`jpsub` 提供自动去人能力 `deman`：对输入视频按固定粒度抽样，用肤色判据 + 像素分布判据定位肤色区域，输出与既有 `masks.json` 同构的矩形覆盖条目，交给既有 `jpsub maskapply` 应用。自动结果只写独立文件，不修改手工掩膜。

本次优化扩大肤色判据的召回范围：现行 RGB 规则与 YCbCr 规则取交集的判据对深色/阴影/暖色调皮肤漏检严重（参考片人工基线框内 `rgb_rule` 命中率中位数仅 27.8%）。优化后两条规则改为并集，并相应放宽像素分布筛选，在保持「不整屏误覆盖」底线的前提下提升召回。

## 入口

`jpsub mask --auto [video]` 非交互执行自动检测；不带 `--auto` 时 `jpsub mask` 仍是原手工选取器，行为与输出不变。

## Requirement: 肤色判据

肤色掩膜由 RGB 肤色规则与 YCbCr 肤色规则**取并集**得到：任一规则判为肤色即视为肤色像素。两条规则的阈值相对现行版本下调，以纳入深色/阴影/暖色调皮肤；仍要求像素偏暖（R 通道不低于 G、B）以排除中性灰/冷色区域。不引入模型权重、外部服务或新增第三方依赖。检测目标是画面中所有满足判据的肤色区域，不预设 ROI，也不区分实拍或渲染来源。

### Scenario: 命中实拍真人肤色区域

- WHEN 对参考片（`TAKUYA -タイト- 4話(終)`）执行 `jpsub mask --auto`
- THEN 在人工基线覆盖的 16s、56s、1172s 时刻，输出各至少有一条时间区间覆盖该时刻的条目
- AND 该时刻至少一条输出条目与同刻人工基线框相交面积大于 0

### Scenario: 提升对照集召回

- WHEN 对 `output/` 下全部含人工标注的片子（8 部，共 251 个基线秒）执行 `jpsub mask --auto`
- THEN 与人工基线相交命中的基线秒比例 ≥ 35%（现行实现实测 16%）
- AND 覆盖基线框内 ≥ 50% 面积的命中比例 ≥ 15%（现行实现实测 5%）

### Scenario: 无肤色内容时不覆盖

- WHEN 对参考片执行 `jpsub mask --auto`
- THEN 在 1200s、600s（画面无肤色内容）不存在覆盖该时刻的条目，或覆盖面积小于整帧 5%

## Requirement: 区域筛选

在肤色掩膜之上按像素分布筛选连通区域：块级连通、面积下限、外接矩形上限、填充率、灰度梯度上下限、亮度上限、亮度标准差上限、HSV 饱和度上限。筛选阈值随肤色判据放宽而相应放宽，但保留「不整屏误覆盖」底线：单条条目外接矩形面积必须小于整帧 60%。

### Scenario: 单条条目不整屏

- WHEN 对对照集任意片子执行 `jpsub mask --auto`
- THEN 每条输出条目的外接矩形面积小于整帧 60%

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

- WHEN 用自动掩膜执行 `jpsub maskapply <参考片>`
- THEN 生成 `.masked.mp4`
- AND 采样时刻 16s、56s、1172s 下，同刻手工基线框区域内至少 80% 像素为纯黑（RGB 各通道 ≤ 8）
- AND 输出视频时长 1261.27s（±0.5s）与 30fps 同原片一致

## 不变量

- 既有无参 `jpsub mask` 选取器与 `jpsub maskapply` 的行为与输出保持不变。
- `masks.json` 字段格式与 `MaskEntry`/`parse_masks`/`save_masks`/`build_filter`/`apply_masks` 实现不变。
- 不新增第三方依赖，不访问外部服务。
