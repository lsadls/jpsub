# Project Memory

<!-- MACHINE_BLOCK_START -->
<!-- MEMORY_BANK_TEMPLATE:v7.1 -->

## Project Snapshot
- **结论**: jpsub — 把 niconico 日语视频（或本地视频）自动 OCR 识别日语字幕 → AI 翻译成中文 → 生成外挂 ASS 字幕 / 烧录进视频；面向普通用户的免安装 exe，全程浏览器点按钮。
- **边界**: 本文件是**薄索引**。项目文档已很完整（README + `docs/` + `changelog.md` + `docs/comet/specs/`），本文件**不复制**它们的内容，只保留「仍绑定当下实现的决策」与路由指针；细节一律去源文档读。
- **指针**: 入口 `README.md`；分主题 `docs/`；历史 `changelog.md`；规格 `docs/comet/specs/`。

## Current Focus
> 更新于: 2026-10-08

- **当前焦点**: Comet Native change `panel-space-efficiency`（home/edit/mask 三页面板分组收敛为行内标签式、mask 打码列表放大），worktree 分支 `comet/panel-space-efficiency`，phase=build / stage=repairing。
- **下一步**:
  - [ ] 完成 panel-space-efficiency 的 build → verify → archive
- **阻塞项**: 无

## Decision Highlights (Still Binding)

> 只留「仍影响当前实现」的决策约束；为什么/历史去 `changelog.md`。

| 决策 | 对实现的直接约束 |
|------|-----------------|
| 配置改 `.env`（废弃 `settings.py`） | 读程序目录 `.env`，按行 `KEY = VALUE`，值用 Python 字面量；模板 `.env.example`；`.gitignore` 忽略 `/.env` |
| 百度 OCR 4 端点，高精度优先 | 顺序 accurate_basic→accurate_general→general_basic→general；合计 3500 次/月；含位置版端点为 `accurate`（不是 accurate_general） |
| 时间轴为军方时间 | 键用 `MMSS`/`HHMMSS`（0.1s 精度），兼容带冒号与旧纯秒数键 |
| 自动去人肤色判据取并集 | RGB 规则 ∪ YCbCr 规则；外接矩形/合并上限 0.58，仍 < 整帧 60% 保留「不整屏」底线 |
| 敏感字打码独立文件 | 写 `.jpsub/masks.sensitive.json`，不触碰手工 `masks.json` |
| Comet native 工作流 | `.comet/config.yaml` default_workflow=native，artifact_root=`docs`，规格在 `docs/comet/specs/` |

## Routing Rules（意图驱动）

按「你想做什么」直接读**源文档**（不再有 details/ 中间层）。

| 意图 | 目标 |
|------|------|
| 项目是什么 / 怎么安装 / 功能总览 | `README.md` |
| 主页 UI（操作/列表/脚本/任务） | `docs/home.md` |
| 译文调整器 | `docs/edit.md` |
| 打码（选取器/应用） | `docs/mask.md` |
| `.env` 配置项详解 | `docs/settings.md` |
| 命令行参考 | `docs/cli.md` |
| 批量模式（-s） | `docs/batch.md` |
| 性能/时长调优 | `docs/perf.md` |
| B 站投稿 | `docs/upload.md` |
| 常见问题 / 更新 / 安装 | `docs/faq.md`、`docs/update.md`、`docs/install.md` |
| 某功能的历史变更与原因 | `changelog.md` |
| 当前生效的能力规格（capability） | `docs/comet/specs/<name>/spec.md` |
| 进行中的 change | `docs/comet/changes/<name>/brief.md` |
| 仓库工作区/Comet 恢复规则 | `AGENTS.md` |

## Drill-Down Protocol

1. **先按上表直达源文档**；本文件只给结论与指针，不复制细节
2. 默认 direct read 1-3 个文件；不确定文件名先 `glob("docs/**/*.md")`
3. 需要跨多文档汇总/冲突检测 → 升级 `memory-reader`（本仓文档 > 3 个时的常见情况）
4. **反幻觉**：未读到/未写明的信息 = 未知，不要补全
5. **回答必须给引用指针**（至少 1-2 个文件路径）

## Write Safety Rules

- `memory-bank/` 仅允许 `.md` 文件
- 写入前必须 Proposal → 用户确认
- **禁止写入敏感信息**：`.env` 含真实百度 AK/SK 与 AI API_KEY，其值绝不进 memory-bank
- 本仓已有文档为主源，**不新增重复内容**；只有「决策约束 + 路由」两类信息进本文件

## Top Quick Answers

> 最多 8 条；可验证；过期即删。

1. Q: 配置写哪？ A: 程序目录 `.env`（复制 `.env.example`）→ `docs/settings.md`
2. Q: 怎么跑？ A: `jpsub`（无参）/ `jpsub home` 开主页，子命令见 `docs/cli.md`
3. Q: 怎么测试？ A: `pytest`；端到端 `tests/verify_*.py` → 见 `pyproject.toml` / `tests/`
4. Q: 打码/去人的规格？ A: `docs/comet/specs/deman/spec.md`、`docs/comet/specs/sensitive-mask/spec.md`
5. Q: 时间轴键格式？ A: 军方时间 `MMSS`/`HHMMSS` → `docs/cli.md` / `changelog.md` (9)→
6. Q: 最近改了什么？ A: `changelog.md` 顶部

<!-- MACHINE_BLOCK_END -->

<!-- USER_BLOCK_START -->
## 用户笔记

<!-- USER_BLOCK_END -->
