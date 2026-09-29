# Outcome

百度 OCR（`jpsub/ocr.py:BaiduOcrEngine`）默认优先使用高精度接口识别，并彻底移除网络图片识别（webimage）接口，逐帧与批量拼接两条兜底链均不再调用它。

# Scope

- `jpsub/ocr.py`：
  - 逐帧兜底 `_ENDPOINTS` 改为高精度优先：`accurate_basic` → `accurate_general` → `general_basic` → `general`（保留通用接口作兜底，避免高精度额度用尽后完全不可用）。
  - 批量拼接 `_BATCH_ENDPOINTS` 改为高精度优先：`accurate_general` → `accurate_basic` → `general` → `general_basic`。
  - 删除两处元组中的 `webimage` 及其注释。
  - 更新类 docstring 中的接口/额度说明（总额度由 4500 次/月变为 3500 次/月）。
- 文档同步：项目内提到"4500 次/月"或 webimage 的说明（如 AGENTS.md/jpsub.md、doc/ 下相关文档、changelog.md）随之更新。
- 不改变调用方接口、缓存逻辑、额度状态文件（`~/.jpsub/baidu_ocr_state.json`）机制与 QPS 重试逻辑。

## Source coverage

无外部需求来源文件；需求来自用户口头描述。

# Non-goals

- 不修改 OpenAI 视觉 OCR 引擎（`ApiOcrEngine`）。
- 不新增用户可配置的接口优先级开关。
- 不修改 OCR 筛选、抽帧、合并、缓存等其他流水线环节。
- 不改变额度用尽自动切换、月初重置的既有机制。

# Acceptance examples

- A1: 逐帧识别（`BaiduOcrEngine._run_b64`）按 `accurate_basic` → `accurate_general` → `general_basic` → `general` 的顺序尝试接口，`webimage` 不在列表中。
- A2: 批量拼接识别（`BaiduOcrEngine` 的批量兜底链）按 `accurate_general` → `accurate_basic` → `general` → `general_basic` 的顺序尝试接口，`webimage` 不在列表中。
- A3: `jpsub/ocr.py` 全文不再出现 `webimage`（含代码、注释与 docstring），接口额度说明与剩余 4 个接口一致（合计 3500 次/月）。
- A4: 项目文档中关于百度 OCR 接口/额度的描述与实现一致（不再宣传 4500 次/月，不再提及 webimage）。

# Constraints and invariants

- 高精度接口（accurate_basic/accurate_general）必须排在对应通用接口之前，即"默认高精度"对逐帧和批量两条链都成立。
- 保留通用接口兜底：高精度额度（合计 1500 次/月）用尽后仍可继续识别。
- 各接口月额度事实（同一 AK/SK）：accurate_basic 1000、accurate_general 500、general_basic 1000、general 1000。
- language_type 传参行为不变（webimage 移除后不再有忽略 JAP 的问题）。

# Decisions

- D1：高精度优先但仍保留通用接口兜底，而非只用高精度接口——避免 1500 次/月高精度额度用尽后当月完全无法识别（Agent 决定，基于现有兜底架构意图）。
- D2：批量链顺序为 `accurate_general` → `accurate_basic`（位置版优先），与原批量链"位置版优先"的既有取向一致。
- D3：文档同步纳入本 change 范围，保证描述与实现一致。

# Open questions

（无）

# Verification expectations

- 静态检查：grep `jpsub/ocr.py` 断言 `webimage` 零匹配；核对 `_ENDPOINTS` 与 `_BATCH_ENDPOINTS` 顺序符合 A1/A2。
- `python -c "import jpsub.ocr"` 可正常导入，无语法错误。
- 文档 grep 不再出现"4500 次/月"与 webimage 相关描述。
