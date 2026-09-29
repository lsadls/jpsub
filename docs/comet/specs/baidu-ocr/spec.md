# 百度 OCR：高精度默认 + 移除 webimage

## Overview

`jpsub/ocr.py` 的 `BaiduOcrEngine` 通过多个百度 OCR 免费额度接口轮换识别。本能力规定：接口优先级默认高精度（accurate）优先，且不再包含网络图片识别（webimage）接口。

## 接口清单与额度

可用接口（同一 AK/SK，按自然月计费，用尽记录于 `~/.jpsub/baidu_ocr_state.json`，月初重置）：

| 接口              | 类型           | 月额度 |
| ----------------- | -------------- | ------ |
| accurate_basic    | 高精度版       | 1000   |
| accurate_general  | 高精度含位置版 | 500    |
| general_basic     | 标准版         | 1000   |
| general           | 标准含位置版   | 1000   |

合计 3500 次/月。webimage 接口已移除，不属于本能力。

## Requirement: per-frame-endpoint-order 逐帧识别接口顺序

`_ENDPOINTS`（`_run_b64` 使用）按以下顺序尝试，单个接口额度用尽（error 17/19）自动切换下一个，QPS 超限（18）短暂等待重试：

1. `accurate_basic`
2. `accurate_general`
3. `general_basic`
4. `general`

### Scenario: 逐帧高精度优先

- WHEN 逐帧识别发起百度 OCR 请求且所有接口均有剩余额度
- THEN 首个尝试的接口为 `accurate_basic`
- AND 兜底顺序依次为 `accurate_general`、`general_basic`、`general`
- AND `webimage` 不在任何尝试路径中

## Requirement: batch-endpoint-order 批量拼接识别接口顺序

`_BATCH_ENDPOINTS`（多帧合并识别使用）按以下顺序尝试，位置版（`has_pos=True`）按行 y 坐标切分，无位置版按分隔带编号行切分：

1. `accurate_general`（位置版）
2. `accurate_basic`（无位置版）
3. `general`（位置版）
4. `general_basic`（无位置版）

### Scenario: 批量高精度优先

- WHEN 批量拼接识别发起百度 OCR 请求且所有接口均有剩余额度
- THEN 首个尝试的接口为 `accurate_general`
- AND 兜底顺序依次为 `accurate_basic`、`general`、`general_basic`
- AND `webimage` 不在任何尝试路径中

## Requirement: no-webimage-residue 无 webimage 残留

`jpsub/ocr.py` 全文（代码、注释、docstring）不包含 `webimage`；类 docstring 的接口与额度说明只列上表 4 个接口，合计 3500 次/月。

### Scenario: 代码与说明一致

- WHEN 在 `jpsub/ocr.py` 中搜索 `webimage`
- THEN 匹配数为 0
- AND docstring 中的额度说明与上表一致

## Requirement: docs-consistency 文档同步

项目文档（AGENTS.md/jpsub.md、doc/ 相关页、changelog.md）中百度 OCR 的接口与额度描述与实现一致：不出现"4500 次/月"，不提及 webimage。

### Scenario: 文档与实现一致

- WHEN 在项目文档中搜索百度 OCR 额度描述
- THEN 无"4500 次/月"与 webimage 表述
- AND 额度总数描述为 3500 次/月

## 不变量

- 高精度接口在两条链中都排在对应通用接口之前。
- 通用接口保留作兜底，高精度额度用尽后识别仍可用。
- `_call` 传参、language_type、额度状态文件机制、QPS 重试逻辑不变。
